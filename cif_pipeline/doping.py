"""
Stage 4b: resolve the parent (undoped) structure and apply the doping
transformation. Every piece of information needed (host formula, which site
the dopant replaces, dopant species, dopant fraction) comes from the
DopingSpec built in classify.py - never inferred from a bare formula string.
If the parent structure has multiple symmetry-distinct sites matching the
requested host_site_species, the user is asked which one is intended.
"""

from __future__ import annotations
import logging
from typing import Optional, Tuple

from pymatgen.core import Structure
from pymatgen.symmetry.analyzer import SpacegroupAnalyzer
from pymatgen.transformations.standard_transformations import SubstitutionTransformation

from .models import DopingSpec, PipelineResult, DopedStructureValidation
from .search_external import search_external_databases
from .search_internal import search_internal_database
from .generate import generate_with_feedback_loop
from .filter_rules import validate_doped_structure, classify_compound_type
from .user_interaction import AskUserFn

log = logging.getLogger("cif_pipeline.doping")


def resolve_parent_structure(
    spec: DopingSpec,
    mp_api_key: str,
    ask_user: AskUserFn,
    max_iterations: int = 5,
    samples_per_iteration: int = 25,
    mace_model_path: Optional[str] = None,
) -> Optional[Structure]:
    """Find (or generate) the undoped parent structure specified in `spec`."""

    match, _all_ext = search_external_databases(
        spec.host_formula, is_doped=False, doping_spec=None,
        mp_api_key=mp_api_key, requested_space_group=None, ask_user=ask_user,
    )
    if match:
        log.info(f"Parent '{spec.host_formula}' found in {match.source} ({match.record_id})")
        return match.structure

    match, _all_int = search_internal_database(
        spec.host_formula, is_doped=False, doping_spec=None,
        requested_space_group=None, ask_user=ask_user,
    )
    if match:
        log.info(f"Parent '{spec.host_formula}' found internally ({match.record_id})")
        return match.structure

    log.info(f"Parent '{spec.host_formula}' not found anywhere - generating it first")
    result = generate_with_feedback_loop(
        spec.host_formula, max_iterations=max_iterations,
        samples_per_iteration=samples_per_iteration, mace_model_path=mace_model_path,
        ask_user=ask_user,
    )
    if result.cif_string is None:
        return None
    return Structure.from_str(result.cif_string, fmt="cif")


def _find_candidate_sites(structure: Structure, host_site_species: str) -> list:
    """Return symmetry-distinct site indices occupied by host_site_species."""
    sga = SpacegroupAnalyzer(structure)
    sym_struct = sga.get_symmetrized_structure()
    candidate_indices = []
    for group in sym_struct.equivalent_indices:
        idx = group[0]
        site = structure[idx]
        if host_site_species in [str(sp) for sp in site.species.elements]:
            candidate_indices.append(idx)
    return candidate_indices


def apply_doping(
    parent_structure: Structure,
    spec: DopingSpec,
    ask_user: AskUserFn,
) -> Structure:
    """Apply the substitution using pymatgen for the primary dopant and any co-dopants.
    Resolves which symmetry-distinct site to target for each dopant.
    Handles both multi-dopant co-substitution on the SAME site (e.g. {Fe: 0.90, Ti: 0.05, Cr: 0.05})
    and co-doping across DIFFERENT sites (e.g. Ti site and O site).
    """
    from collections import defaultdict
    from pymatgen.core.periodic_table import Element

    all_specs = [spec] + (spec.co_dopants if hasattr(spec, "co_dopants") and spec.co_dopants else [])
    doped_structure = parent_structure.copy()

    # Step 1: Validate element symbols and resolve site index for each dopant
    for s in all_specs:
        # QA §6.3: validate dopant_species against pymatgen's element list before
        # we try to build a site occupancy dict - an invalid symbol would produce
        # an unparseable/corrupted structure rather than a clean error.
        try:
            Element(s.dopant_species)
        except ValueError:
            raise ValueError(
                f"'{s.dopant_species}' is not a valid element symbol. "
                f"Check the dopant spelling (e.g. 'Ti', 'Sr', 'Cr')."
            )

        # QA §6.2: validate host_site_species as well
        try:
            Element(s.host_site_species)
        except ValueError:
            raise ValueError(
                f"'{s.host_site_species}' is not a valid element symbol for the host site. "
                f"Check the host site species spelling."
            )

        if s.site_index is None:
            candidates = _find_candidate_sites(parent_structure, s.host_site_species)
            if len(candidates) == 0:
                raise ValueError(
                    f"No sites with species '{s.host_site_species}' found in the parent "
                    f"structure (formula: {parent_structure.composition.reduced_formula}). "
                    f"Cannot apply doping for '{s.dopant_species}'. "
                    f"Check that host_site_species matches an element actually present in the host."
                )
            elif len(candidates) == 1:
                s.site_index = candidates[0]
            else:
                options = [
                    f"Site {i}: {parent_structure[i].species_string} at {parent_structure[i].frac_coords.round(3)}"
                    for i in candidates
                ]
                choice = ask_user(
                    f"The parent structure has {len(candidates)} symmetry-distinct "
                    f"'{s.host_site_species}' sites. Which one should "
                    f"'{s.dopant_species}' substitute onto?",
                    options,
                )
                if choice is None:
                    s.site_index = candidates[0]
                    log.warning(f"No site choice given for {s.dopant_species} - defaulting to site {candidates[0]}")
                else:
                    s.site_index = candidates[options.index(choice)]

    # Step 2: Group dopants by site index to compute composite fractional occupancies
    sites_map = defaultdict(list)
    for s in all_specs:
        sites_map[s.site_index].append(s)

    for site_idx, specs_on_site in sites_map.items():
        host_sp = specs_on_site[0].host_site_species
        total_dopant_fraction = sum(s.dopant_fraction for s in specs_on_site)
        remaining_host = max(0.0, 1.0 - total_dopant_fraction)

        occupancy = {}
        if remaining_host > 1e-6:
            occupancy[host_sp] = round(remaining_host, 6)
        for s in specs_on_site:
            if s.dopant_fraction > 1e-6:
                occupancy[s.dopant_species] = round(occupancy.get(s.dopant_species, 0.0) + s.dopant_fraction, 6)

        if not occupancy:
            occupancy = {host_sp: 1.0}

        doped_structure[site_idx] = occupancy
        log.info(f"[Doping] Substituted site {site_idx} ({host_sp}) -> {occupancy}")

    return doped_structure


def apply_doping_with_validation(
    parent_structure: Structure,
    spec: DopingSpec,
    ask_user: AskUserFn,
) -> Tuple[Structure, DopedStructureValidation]:
    """Wraps apply_doping() with the SEPARATE doped-structure validation pass
    (filter_rules.validate_doped_structure). Validates both the primary dopant
    and any co-dopant substitutions.
    """
    doped_structure = apply_doping(parent_structure, spec, ask_user)
    compound_type = classify_compound_type(doped_structure.composition)

    # Validate primary dopant
    validation = validate_doped_structure(
        doped_structure,
        host_site_species=spec.host_site_species,
        dopant_species=spec.dopant_species,
        dopant_fraction=spec.dopant_fraction,
        compound_type=compound_type,
    )

    # Also validate co-dopants if present
    if hasattr(spec, "co_dopants") and spec.co_dopants:
        for co_s in spec.co_dopants:
            co_val = validate_doped_structure(
                doped_structure,
                host_site_species=co_s.host_site_species,
                dopant_species=co_s.dopant_species,
                dopant_fraction=co_s.dopant_fraction,
                compound_type=compound_type,
            )
            if not co_val.is_valid:
                validation.is_valid = False
                validation.notes += f" | Co-dopant {co_s.dopant_species} issue: {co_val.notes}"

    if not validation.is_valid:
        log.warning(f"Doped structure failed validation: {validation.notes}")
    return doped_structure, validation
