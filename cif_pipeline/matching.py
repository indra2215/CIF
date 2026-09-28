"""
Normalization and matching logic shared by both the external-database search
(Stage 1) and the internal-database search (Stage 2), so the two stages apply
IDENTICAL rules and can't silently disagree about what counts as "the same
compound".

Key decision this module encodes:
-------------------------------------------------------------------------
UNDOPED compounds -> normalize to a reduced/canonical formula before
searching. "Fe2O3", "Fe4O6" and "FeO1.5" should all resolve to the same
canonical search key, because the formula unit multiplicity carries no
chemical meaning here.

DOPED compounds -> do NOT reduce/normalize the formula. The fractional site
occupancies (e.g. La0.7Sr0.3MnO3) are themselves the chemically meaningful
information - collapsing/reducing them risks merging the dopant fraction
into the host in a way that changes what compound is actually being asked
for. Instead, doped-formula comparison uses a tolerance-based match on
(host species set + dopant species + dopant fraction), not string/formula
reduction.
-------------------------------------------------------------------------
"""

from __future__ import annotations
import re
from typing import List, Optional, Tuple
from pymatgen.core import Composition, Structure
from pymatgen.analysis.structure_matcher import StructureMatcher

from .models import SearchMatch, DopingSpec, NormalizationTrace, FormulaMultiplicityInfo
from .user_interaction import AskUserFn
from .classify import _normalize_formula_input

# How many polymorph candidates to show in a single UI picker screen before
# paginating. We NEVER drop candidates from the underlying data - this only
# controls what's rendered in one screen; the full count and full list are
# always available to the caller via the second return value of
# disambiguate_polymorphs().
MAX_POLYMORPHS_SHOWN_AT_ONCE = 10


def _normalize_space_group(sg: Optional[str]) -> Optional[str]:
    """Normalize a space-group specification for comparison.

    Handles:
    - International Tables number (e.g. "62") → Hermann-Mauguin symbol ("Pnma")
    - Whitespace variants ("P n m a" → "Pnma")
    - Case normalization where conventional (e.g. "pnma" → "Pnma")

    Returns None if the input is None or cannot be recognized.
    Returns the compact whitespace-stripped string as fallback for comparison
    when pymatgen can't parse it — this still handles the "P n m a" == "Pnma"
    case correctly since both become "Pnma" after stripping whitespace.
    """
    if sg is None:
        return None
    sg_str = str(sg).strip()
    if not sg_str:
        return None

    from pymatgen.symmetry.groups import SpaceGroup

    # Remove internal whitespace for comparison
    sg_compact = sg_str.replace(" ", "")

    # If it's a pure integer, look up by number
    if sg_compact.isdigit():
        try:
            return SpaceGroup.from_int_number(int(sg_compact)).symbol
        except Exception:
            return sg_str  # return as-is if lookup fails

    # Try multiple casing/format strategies to canonicalize the symbol.
    # pymatgen's SpaceGroup is case-sensitive and uses specific notation
    # (e.g. "P2_1/c" not "P21/c"), so we try several transformations.
    repaired_screw = re.sub(r'([A-Za-z])(\d)(\d)(?=/|$)', r'\1\2_\3', sg_compact)
    attempts = [
        sg_compact,                                          # original, whitespace-stripped
        sg_str,                                              # original with spaces (e.g. "F d -3 m" works)
        sg_compact[0].upper() + sg_compact[1:],              # first char uppercase: "pnma" -> "Pnma"
        sg_compact[0].upper() + sg_compact[1:].lower(),      # title-ish: "PNMA" -> "Pnma"
        sg_compact.title(),                                  # Python .title(): "P N M A" -> "P N M A" -> skip whitespace
        repaired_screw,                                      # screw-axis repair: "P21/c" -> "P2_1/c"
        repaired_screw[0].upper() + repaired_screw[1:],      # screw-axis with leading upper
        sg_str.strip(),                                      # original with leading/trailing stripped
    ]
    for attempt in attempts:
        if not attempt:
            continue
        try:
            return SpaceGroup(attempt).symbol
        except Exception:
            continue

    # Final fallback: return the compact (whitespace-stripped) form for comparison.
    # This at least makes "P n m a" compare equal to "Pnma" even if pymatgen
    # can't parse the symbol.
    return sg_compact


def normalize_formula_for_search(formula: str, is_doped: bool) -> str:
    if is_doped:
        # Preserve as-is (whitespace-stripped) - do not reduce.
        return formula.strip()
    try:
        return Composition(formula).reduced_formula
    except Exception:
        return formula.strip()


def normalize_formula_with_trace(
    formula: str,
    is_doped: bool,
    classification_reasoning: str = "",
) -> Tuple[str, NormalizationTrace]:
    """Same normalization as normalize_formula_for_search, but also returns a
    NormalizationTrace explaining exactly what happened - this is what the UI
    should render so normalization is never an invisible step.

    If the formula cannot be parsed at all, the trace's search_key_used will
    be the raw (stripped) input and multiplicity.reason will explain the failure.
    """
    formula = _normalize_formula_input(formula.strip() if formula else "")

    if is_doped:
        search_key = formula
        trace = NormalizationTrace(
            raw_input=formula,
            is_doped=True,
            classification_reasoning=classification_reasoning,
            search_key_used=search_key,
            multiplicity=None,  # formula-unit multiplicity is not collapsed for doped compounds
            doped_match_rule=(
                "Doped formula is NOT reduced. Matching against existing records uses the "
                "site-occupancy ratio (dopant amount / (dopant amount + host-site amount)), "
                "computed directly from raw atom counts so it works whether the record is "
                "stored in decimal form (e.g. La0.7Sr0.3MnO3) or scaled integer form "
                "(e.g. La7Sr3Mn10O30). Tolerance: +/-3% on that ratio."
            ),
        )
        return search_key, trace

    # Undoped path: reduce, and explicitly record the multiplicity relationship
    # between what was typed and the canonical form used for searching.
    try:
        comp = Composition(formula)
        reduced_comp, factor = comp.get_reduced_composition_and_factor()
        reduced_formula = reduced_comp.reduced_formula
        was_reduced = abs(factor - 1.0) > 1e-6
        multiplicity = FormulaMultiplicityInfo(
            input_formula=formula,
            reduced_formula=reduced_formula,
            multiplier=factor,
            was_reduced=was_reduced,
            reason=(
                f"'{formula}' is {factor:g}x the reduced formula '{reduced_formula}' - "
                f"formula-unit multiplicity carries no chemical meaning for an undoped "
                f"compound, so the reduced form is used as the search key."
                if was_reduced else
                f"'{formula}' is already in reduced form; no change made."
            ),
        )
        search_key = reduced_formula
    except Exception as parse_err:
        search_key = formula
        multiplicity = FormulaMultiplicityInfo(
            input_formula=formula, reduced_formula=formula, multiplier=1.0,
            was_reduced=False,
            reason=(
                f"Could not parse formula '{formula}' to determine reduced form "
                f"({type(parse_err).__name__}: {parse_err}). "
                f"Using the raw input as-is for the search key."
            ),
        )

    trace = NormalizationTrace(
        raw_input=formula,
        is_doped=False,
        classification_reasoning=classification_reasoning,
        search_key_used=search_key,
        multiplicity=multiplicity,
        doped_match_rule=None,
    )
    return search_key, trace


def doped_formulas_match(formula_a: str, doping_spec: DopingSpec, tol: float = 0.03) -> bool:
    """Tolerance-based match for doped compositions: same host species set,
    same dopant species, dopant fraction within `tol`.

    Deliberately does NOT require either amount to be a non-integer coefficient.
    A database may store the same doped compound in a scaled/reduced integer
    form (e.g. "La7Sr3Mn10O30" instead of "La0.7Sr0.3MnO3") - the site-occupancy
    *ratio* between host and dopant is scale-invariant, so we compute it directly
    from the raw amounts rather than pre-filtering to "fractional-looking" ones.
    """
    try:
        comp_a = Composition(formula_a)
    except Exception:
        return False

    amt_dopant = comp_a.get(doping_spec.dopant_species, 0)
    amt_host = comp_a.get(doping_spec.host_site_species, 0)
    if amt_dopant <= 0 or amt_host <= 0:
        return False
    site_total = amt_dopant + amt_host
    if abs((amt_dopant / site_total) - doping_spec.dopant_fraction) > tol:
        return False

    # Host lattice element check: ensure the compound has the expected element set
    try:
        expected_els = {str(el) for el in Composition(doping_spec.host_formula).elements}
    except Exception:
        return True
    expected_els.add(str(doping_spec.dopant_species))
    return {str(el) for el in comp_a.elements} == expected_els


def structures_match(struct_a: Structure, struct_b: Structure,
                      ltol: float = 0.2, stol: float = 0.3, angle_tol: float = 5.0) -> bool:
    matcher = StructureMatcher(ltol=ltol, stol=stol, angle_tol=angle_tol, primitive_cell=True, scale=True)
    return matcher.fit(struct_a, struct_b)


def disambiguate_polymorphs(
    matches: List[SearchMatch],
    requested_space_group: Optional[str],
    ask_user: AskUserFn,
) -> Tuple[Optional[SearchMatch], List[SearchMatch]]:
    """Multiple hits for the same formula (polymorphs) require disambiguation -
    we never just silently take matches[0], and we never silently DROP any of
    them either.

    Returns (chosen_match_or_None, all_candidate_matches). The second element
    is always the FULL list regardless of what was chosen or shown, so the
    caller (orchestrator) can attach it to PipelineResult.candidate_matches
    for the UI to render a complete picker (with pagination if there are more
    than MAX_POLYMORPHS_SHOWN_AT_ONCE) - "ambiguous" never means "only some of
    the options were considered", it means "all of them were found, and a
    choice is needed among all of them".

    Space group comparison uses normalized symbols/numbers (QA §3.1, §3.2):
    - "62" and "Pnma" match correctly
    - "P n m a" and "Pnma" match correctly
    - case differences are normalized via pymatgen SpaceGroup
    """
    if not matches:
        return None, []

    # Normalize the requested space group for comparison
    requested_sg_norm = _normalize_space_group(requested_space_group)

    # Requested space group is checked FIRST, regardless of how many matches came
    # back. Checking "len(matches) == 1" before this would silently return a
    # single mismatched polymorph as if it satisfied the request.
    if requested_sg_norm:
        # Compare against normalized match space groups
        filtered = [
            m for m in matches
            if _normalize_space_group(m.space_group) == requested_sg_norm
        ]
        if filtered:
            return filtered[0], matches
        # None of the hits have the requested space group - don't silently
        # substitute a different polymorph. Ask, rather than guess.
        shown = matches[:MAX_POLYMORPHS_SHOWN_AT_ONCE]
        options = [
            f"{m.source} / {m.record_id} (space group: {m.space_group or 'unknown'}, "
            f"NOT the requested '{requested_space_group}')"
            for m in shown
        ]
        options.append("None of these - generate a new structure for the requested space group")
        more_note = (
            f" ({len(matches) - len(shown)} more not shown - refine the search or request pagination)"
            if len(matches) > len(shown) else ""
        )
        choice = ask_user(
            f"None of the {len(matches)} existing entries for this formula match the "
            f"requested space group '{requested_space_group}'.{more_note} Use one of these anyway, "
            f"or generate fresh?",
            options,
        )
        from .user_interaction import pick_option_index
        idx = pick_option_index(choice, options)
        if idx is None or idx == len(options) - 1 or idx >= len(shown):
            return None, matches
        return shown[idx], matches

    if len(matches) == 1:
        return matches[0], matches

    distinct_sgs = {_normalize_space_group(m.space_group) for m in matches if m.space_group}
    if len(distinct_sgs) <= 1:
        # Same formula, same (or unknown) space group across all hits -> treat as one match.
        return matches[0], matches

    # Stability-order sorting: sort candidates so the most stable (lowest energy_above_hull,
    # then lowest formation_energy_per_atom) is first.
    def _stability_key(m: SearchMatch):
        ehull = (m.extra or {}).get("energy_above_hull")
        eform = (m.extra or {}).get("formation_energy_per_atom")
        k_ehull = float(ehull) if ehull is not None else 1.0
        k_eform = float(eform) if eform is not None else 0.0
        return (k_ehull, k_eform)

    sorted_matches = sorted(matches, key=_stability_key)
    shown = sorted_matches[:MAX_POLYMORPHS_SHOWN_AT_ONCE]
    options = [f"{m.source} / {m.record_id} (space group: {m.space_group or 'unknown'})" for m in shown]
    options.append("None of these - generate a new structure instead")
    more_note = (
        f" Showing the first {len(shown)} of {len(sorted_matches)} - more are available."
        if len(sorted_matches) > len(shown) else ""
    )
    choice = ask_user(
        f"Found {len(sorted_matches)} structurally distinct entries for this formula "
        f"(different polymorphs).{more_note} Which one matches what you need?",
        options,
    )
    from .user_interaction import pick_option_index
    idx = pick_option_index(choice, options)
    if idx is None:
        # Non-interactive mode (web API or batch) where user didn't specify choice upfront:
        # Default to the most stable ground-state polymorph (sorted_matches[0]),
        # while preserving ALL candidates in sorted_matches for the UI/caller to inspect and select.
        return sorted_matches[0], sorted_matches
    if idx == len(options) - 1 or idx >= len(shown):
        return None, sorted_matches
    return shown[idx], sorted_matches
