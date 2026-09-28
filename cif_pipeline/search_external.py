"""
Stage 1: search external open-source databases.

Applies the SAME normalization/matching rules as Stage 2 (internal DB), via
matching.py, so "does this compound already exist" means the same thing in
both places. Returns ALL matches (not just the first hit) so polymorphs can
be disambiguated rather than silently collapsed.

Each individual database query is wrapped in a try/except so that one
failing source (network error, API outage, bad data) never crashes the
pipeline — it just logs the error and returns no results from that source,
while the other sources continue normally.
"""

from __future__ import annotations
import csv
import io
import logging
from typing import List, Optional, Tuple

import requests
from pymatgen.core import Structure as PmgStructure, Composition

from .models import SearchMatch, DopingSpec
from .matching import normalize_formula_for_search, doped_formulas_match, disambiguate_polymorphs
from .user_interaction import AskUserFn

log = logging.getLogger("cif_pipeline.search_external")

# ---------------------------------------------------------------------------
# JARVIS dataset cache — loaded once on first use, then reused across calls.
# The raw dataset is ~1.5 GB on first download; jarvis-tools caches it locally
# after that, but iterating the full list per query is still O(N), so we build
# a formula→entries index on first load.
# ---------------------------------------------------------------------------
_JARVIS_INDEX: Optional[dict] = None  # formula -> list[dict]


def _get_jarvis_index() -> dict:
    """Lazily load and index the JARVIS DFT-3D dataset.

    First-time use downloads ~1.5 GB from figshare. If the download fails
    (network timeout, corrupted file), we set the index to empty and log a
    clear message — the pipeline continues without JARVIS results rather
    than blocking.
    """
    global _JARVIS_INDEX
    if _JARVIS_INDEX is not None:
        return _JARVIS_INDEX

    try:
        from jarvis.db.figshare import data as jarvis_data
        log.info("[JARVIS] Loading DFT-3D dataset (first time downloads ~1.5 GB from figshare)...")
        dft_3d = jarvis_data("dft_3d")
        if not dft_3d:
            log.warning("[JARVIS] Dataset loaded but empty — check your jarvis-tools installation")
            _JARVIS_INDEX = {}
            return _JARVIS_INDEX
        _JARVIS_INDEX = {}
        for entry in dft_3d:
            formula = entry.get("formula", "")
            _JARVIS_INDEX.setdefault(formula, []).append(entry)
        log.info(f"[JARVIS] Indexed {len(dft_3d)} entries across {len(_JARVIS_INDEX)} unique formulas")
    except Exception as e:
        log.warning(
            f"[JARVIS] Could not load DFT-3D dataset: {e}. "
            f"This usually means the ~1.5 GB download from figshare failed "
            f"(network timeout or corrupted file). JARVIS searches will be "
            f"skipped. To retry, restart the pipeline — the download will be "
            f"attempted again automatically."
        )
        _JARVIS_INDEX = {}

    return _JARVIS_INDEX


# ---------------------------------------------------------------------------
# Individual database query implementations
# ---------------------------------------------------------------------------

def _query_materials_project(formula: str, api_key: str) -> List[SearchMatch]:
    """Query the Materials Project via mp-api.
    Requires a valid API key from https://materialsproject.org.
    Supports either chemical formula or direct mp-id (e.g. mp-1234).
    """
    if not api_key:
        log.info("[MP] skipping — no API key provided")
        return []

    try:
        from mp_api.client import MPRester

        matches = []
        stripped = formula.strip()
        with MPRester(api_key) as mpr:
            if stripped.lower().startswith("mp-") or stripped.lower().startswith("mvc-"):
                docs = mpr.summary.search(material_ids=[stripped])
            else:
                docs = mpr.summary.search(formula=stripped)

            for doc in docs:
                sg = None
                if hasattr(doc, "symmetry") and doc.symmetry:
                    sg = doc.symmetry.symbol if hasattr(doc.symmetry, "symbol") else str(doc.symmetry)

                formula_pretty = doc.formula_pretty if hasattr(doc, "formula_pretty") else formula

                extra = {}
                if hasattr(doc, "formation_energy_per_atom") and doc.formation_energy_per_atom is not None:
                    extra["formation_energy_per_atom"] = doc.formation_energy_per_atom
                if hasattr(doc, "energy_above_hull") and doc.energy_above_hull is not None:
                    extra["energy_above_hull"] = doc.energy_above_hull

                matches.append(SearchMatch(
                    source="MP",
                    record_id=str(doc.material_id),
                    structure=doc.structure,
                    space_group=sg,
                    formula=formula_pretty,
                    source_url=f"https://materialsproject.org/materials/{doc.material_id}",
                    extra=extra,
                ))
            log.info(f"[MP] found {len(matches)} result(s) for '{formula}'")
        return matches

    except Exception as e:
        log.error(f"[MP] query failed for '{formula}': {e}")
        return []


def _query_oqmd(formula: str) -> List[SearchMatch]:
    """Query OQMD via its public REST API.
    No authentication needed.
    Endpoint: http://oqmd.org/oqmdapi/formationenergy
    """
    try:
        # OQMD API uses element_set for filtering by composition.
        # Parse the formula to get element set for the query.
        try:
            comp = Composition(formula)
            elements = sorted(str(el) for el in comp.elements)
            element_filter = f"element_set=({'-'.join(elements)})"
        except Exception:
            element_filter = f"composition={formula}"

        resp = requests.get(
            "http://oqmd.org/oqmdapi/formationenergy",
            params={"filter": element_filter, "fields": "name,entry_id,spacegroup,unit_cell,sites,formationenergy", "limit": "50"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        entries = data.get("data", [])
        if not entries:
            log.info(f"[OQMD] no results for '{formula}'")
            return []

        matches = []
        for entry in entries:
            entry_id = entry.get("entry_id", "")
            sg = entry.get("spacegroup", None)

            # OQMD provides unit_cell and sites; try to reconstruct a Structure
            unit_cell = entry.get("unit_cell")
            sites = entry.get("sites")
            if unit_cell and sites:
                try:
                    # Parse OQMD site format: each site is "Element @ x,y,z"
                    species_list = []
                    coords_list = []
                    for site_str in sites:
                        parts = site_str.split("@")
                        sp = parts[0].strip()
                        coords = [float(c.strip()) for c in parts[1].strip().split(",")]
                        species_list.append(sp)
                        coords_list.append(coords)
                    struct = PmgStructure(
                        lattice=unit_cell,
                        species=species_list,
                        coords=coords_list,
                        coords_are_cartesian=True,
                    )
                except Exception as parse_err:
                    log.debug(f"[OQMD] could not parse structure for entry {entry_id}: {parse_err}")
                    continue
            else:
                continue  # can't build a structure without cell/sites

            extra = {}
            fe = entry.get("formationenergy")
            if fe is not None:
                extra["formation_energy"] = fe

            matches.append(SearchMatch(
                source="OQMD",
                record_id=str(entry_id),
                structure=struct,
                space_group=str(sg) if sg else None,
                formula=entry.get("name", formula),
                source_url=f"http://oqmd.org/materials/entry/{entry_id}",
                extra=extra,
            ))

        log.info(f"[OQMD] found {len(matches)} result(s) for '{formula}'")
        return matches

    except Exception as e:
        log.error(f"[OQMD] query failed for '{formula}': {e}")
        return []


def _query_jarvis(formula: str) -> List[SearchMatch]:
    """Query the JARVIS DFT-3D dataset.
    No authentication needed — data is public, downloaded via jarvis-tools.
    Uses a cached index so the full dataset isn't re-iterated per call.
    """
    try:
        from jarvis.core.atoms import Atoms as JarvisAtoms

        index = _get_jarvis_index()
        entries = index.get(formula, [])
        if not entries:
            log.info(f"[JARVIS] no results for '{formula}'")
            return []

        matches = []
        for d in entries:
            try:
                atoms = JarvisAtoms.from_dict(d["atoms"])
                struct = atoms.pymatgen_converter()
                matches.append(SearchMatch(
                    source="JARVIS",
                    record_id=d.get("jid", ""),
                    structure=struct,
                    space_group=d.get("spg_symbol"),
                    formula=d.get("formula", formula),
                    source_url=f"https://www.ctcms.nist.gov/~knc6/static/JARVIS-DFT/{d.get('jid', '')}.xml",
                    extra={k: d[k] for k in ("optb88vdw_total_energy", "ehull", "mbj_bandgap") if k in d and d[k] is not None},
                ))
            except Exception as entry_err:
                log.debug(f"[JARVIS] could not convert entry {d.get('jid')}: {entry_err}")
                continue

        log.info(f"[JARVIS] found {len(matches)} result(s) for '{formula}'")
        return matches

    except Exception as e:
        log.error(f"[JARVIS] query failed for '{formula}': {e}")
        return []


def _query_cod(formula: str) -> List[SearchMatch]:
    """Crystallography Open Database (COD) — free, open, public-domain.

    Supports:
    1. Direct COD ID lookup (e.g. "1546384", "COD 1546384", "COD:1546384") via id= param.
    2. Precise chemical formula search using Hill system notation (formula= param)
       which COD indexes (e.g. 'C12 H8 B F3 K N3 O2' for KBH8C12N3O2F3).
    3. Text search fallback (text= param).
    4. Mandatory formula verification against each fetched CIF.

    Only fetches up to MAX_COD_CANDIDATES CIFs per query to avoid hammering
    COD's server or blocking the pipeline on a high-hit-count text match.
    """
    MAX_COD_CANDIDATES = 20  # cap CIF fetches per query for rate-limiting sanity

    try:
        stripped = formula.strip()
        direct_cod_id = None
        if stripped.isdigit():
            direct_cod_id = stripped
        elif stripped.upper().startswith("COD"):
            parts = stripped.replace(":", " ").replace("-", " ").replace("_", " ").split()
            if len(parts) >= 2 and parts[1].isdigit():
                direct_cod_id = parts[1]

        rows = []
        target_comp = None
        target_reduced = stripped

        if direct_cod_id:
            log.info(f"[COD] Direct ID search for '{direct_cod_id}'")
            resp = requests.get(
                "https://www.crystallography.net/cod/result",
                params={"id": direct_cod_id, "format": "csv"},
                timeout=20,
            )
            resp.raise_for_status()
            clean_lines = [line for line in resp.text.splitlines() if not line.strip().startswith("#")]
            if clean_lines:
                rows = list(csv.DictReader(clean_lines))
        else:
            try:
                target_comp = Composition(formula).reduced_composition
                target_reduced = target_comp.reduced_formula
            except Exception:
                target_comp = None
                target_reduced = formula

            seen_files = set()

            # Query 1: Formula search using Hill notation (COD stores formula_sum in Hill system)
            if target_comp is not None:
                hill_str = target_comp.hill_formula
                try:
                    resp_hill = requests.get(
                        "https://www.crystallography.net/cod/result",
                        params={"formula": hill_str, "format": "csv"},
                        timeout=20,
                    )
                    if resp_hill.status_code == 200:
                        clean_lines = [line for line in resp_hill.text.splitlines() if not line.strip().startswith("#")]
                        if clean_lines:
                            for r in csv.DictReader(clean_lines):
                                fid = r.get("file")
                                if fid and fid not in seen_files:
                                    seen_files.add(fid)
                                    rows.append(r)
                except Exception as e_hill:
                    log.debug(f"[COD] Hill formula query failed: {e_hill}")

            # Query 2: Text search fallback
            try:
                resp_text = requests.get(
                    "https://www.crystallography.net/cod/result",
                    params={"text": formula, "format": "csv"},
                    timeout=20,
                )
                if resp_text.status_code == 200:
                    clean_lines = [line for line in resp_text.text.splitlines() if not line.strip().startswith("#")]
                    if clean_lines:
                        for r in csv.DictReader(clean_lines):
                            fid = r.get("file")
                            if fid and fid not in seen_files:
                                seen_files.add(fid)
                                rows.append(r)
            except Exception as e_text:
                log.debug(f"[COD] text search query failed: {e_text}")

        if not rows:
            log.info(f"[COD] no results for '{formula}'")
            return []

        # Filter candidate rows: prioritize entries whose formula matches target
        candidate_rows = []
        for row in rows:
            if direct_cod_id:
                candidate_rows.append(row)
                continue
            row_formula = (row.get("formula") or row.get("calcformula") or "").strip("- ")
            if row_formula and target_comp is not None:
                try:
                    c = Composition(row_formula).reduced_composition
                    if c != target_comp:
                        continue  # Skip obvious false positives from text search
                except Exception:
                    pass  # Keep if parsing failed so CIF can be checked
            candidate_rows.append(row)

        log.info(
            f"[COD] search returned {len(rows)} raw candidate(s), "
            f"{len(candidate_rows)} filtered candidate(s) for '{formula}', "
            f"verifying up to {MAX_COD_CANDIDATES}..."
        )

        # Step 2: fetch CIFs and verify formula match
        matches = []
        for row in candidate_rows[:MAX_COD_CANDIDATES]:
            cod_id = row.get("file")
            if not cod_id:
                continue

            try:
                cif_resp = requests.get(
                    f"https://www.crystallography.net/cod/{cod_id}.cif",
                    timeout=15,
                )
                if cif_resp.status_code != 200:
                    continue

                struct = PmgStructure.from_str(cif_resp.text, fmt="cif")

                # MANDATORY verification if formula was queried
                if not direct_cod_id and target_comp is not None:
                    cif_reduced = struct.composition.reduced_composition
                    if cif_reduced != target_comp:
                        log.debug(
                            f"[COD] {cod_id} candidate formula mismatch: "
                            f"{cif_reduced.reduced_formula} != {target_reduced} — rejected"
                        )
                        continue

                # Determine theoretical flag from the row metadata
                is_theoretical = None
                flags = row.get("flags", "")
                if "has theoretical" in flags.lower() or "theoretical" in str(row.get("method", "")).lower():
                    is_theoretical = True
                elif flags:
                    is_theoretical = False

                matches.append(SearchMatch(
                    source="COD",
                    record_id=str(cod_id),
                    structure=struct,
                    space_group=row.get("sg"),
                    formula=row.get("formula", formula),
                    source_url=f"https://www.crystallography.net/cod/{cod_id}.html",
                    is_theoretical=is_theoretical,
                ))
            except Exception as cif_err:
                log.debug(f"[COD] could not process entry {cod_id}: {cif_err}")
                continue

        log.info(f"[COD] {len(matches)} verified match(es) for '{formula}' (out of {min(len(candidate_rows), MAX_COD_CANDIDATES)} candidates checked)")
        return matches

    except Exception as e:
        log.error(f"[COD] query failed for '{formula}': {e}")
        return []


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def search_external_databases(
    raw_formula: str,
    is_doped: bool,
    doping_spec: Optional[DopingSpec],
    mp_api_key: str,
    requested_space_group: Optional[str],
    ask_user: AskUserFn,
) -> Tuple[Optional[SearchMatch], List[SearchMatch]]:
    """Returns (chosen_match_or_None, all_candidate_matches_found).
    The second element is always the complete list of everything found across
    all four databases, for the UI's benefit - see matching.disambiguate_polymorphs.
    """

    search_key = normalize_formula_for_search(raw_formula, is_doped)
    log.info(f"Searching external DBs with normalized key='{search_key}' (doped={is_doped})")

    all_matches: List[SearchMatch] = []
    all_matches += _query_materials_project(search_key, mp_api_key)
    all_matches += _query_oqmd(search_key)
    all_matches += _query_jarvis(search_key)
    all_matches += _query_cod(search_key)

    if is_doped and doping_spec is not None:
        # For doped compounds, filter hits by tolerance-based composition match
        # rather than trusting exact formula string equality.
        all_matches = [
            m for m in all_matches
            if m.formula and doped_formulas_match(m.formula, doping_spec)
        ]

    if not all_matches:
        return None, []

    return disambiguate_polymorphs(all_matches, requested_space_group, ask_user)
