"""
Stage 2: search the internal database. Mirrors search_external.py's logic
exactly (same normalization, same tolerance-based doped matching, same
polymorph disambiguation) via matching.py, so a compound can't be "found" by
one stage's rules and "not found" by the other's.
"""

from __future__ import annotations
import logging
from typing import List, Optional, Tuple

from .models import SearchMatch, DopingSpec
from .matching import normalize_formula_for_search, doped_formulas_match, disambiguate_polymorphs
from .user_interaction import AskUserFn

log = logging.getLogger("cif_pipeline.search_internal")


def _query_internal_db(formula: str) -> List[SearchMatch]:
    """TODO: query your actual backing store (Postgres/Mongo/etc).
    Return one SearchMatch per stored structure matching this formula key -
    including multiple rows if you already store several polymorphs. Set
    `source_url` to a link back into your own UI's record view, e.g.
    f"/compounds/{record_id}", so the pipeline's provenance display works the
    same way for internal records as it does for external ones.
    """
    log.info(f"[internal] querying '{formula}' (stub)")
    return []


def search_internal_database(
    raw_formula: str,
    is_doped: bool,
    doping_spec: Optional[DopingSpec],
    requested_space_group: Optional[str],
    ask_user: AskUserFn,
) -> Tuple[Optional[SearchMatch], List[SearchMatch]]:
    """Returns (chosen_match_or_None, all_candidate_matches_found) - same shape
    as search_external.search_external_databases, for the same reason.
    """

    search_key = normalize_formula_for_search(raw_formula, is_doped)
    log.info(f"Searching internal DB with normalized key='{search_key}' (doped={is_doped})")

    matches = _query_internal_db(search_key)

    if is_doped and doping_spec is not None:
        matches = [
            m for m in matches
            if m.formula and doped_formulas_match(m.formula, doping_spec)
        ]

    if not matches:
        return None, []

    return disambiguate_polymorphs(matches, requested_space_group, ask_user)
