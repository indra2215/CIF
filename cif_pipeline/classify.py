"""
Doped vs. undoped classification.

Multiple levels are checked in order of reliability. As soon as one level gives
a confident answer, we stop. If nothing is confident, we ask the user rather
than guessing - a wrong doped/undoped call sends the whole pipeline down the
wrong branch (parent resolution + site substitution vs. straight generation),
so this is exactly the kind of ambiguity worth one clarifying question for.
"""

from __future__ import annotations
import re
import unicodedata
from typing import Optional, Tuple, List

from pymatgen.core import Composition
from pymatgen.core.periodic_table import Element
from .models import CompoundQuery, DopingSpec
from .user_interaction import AskUserFn

FRACTIONAL_COEFF_RE = re.compile(r"[A-Z][a-z]?\d*\.\d+")

# NOTE: element-symbol groups are intentionally case-SENSITIVE (no re.IGNORECASE
# on the whole pattern) - only the keyword parts ("doped"/"doping"/"with") use the
# scoped inline flag (?i:...). Mixing global IGNORECASE with [A-Z] would let any
# lowercase letter masquerade as an element symbol, and - critically - would let
# "Undoped" match as dopant "Un" (U + n immediately followed by "doped"), silently
# flipping "this is NOT doped" into "this is doped with element Un". Every
# extracted token is additionally validated against pymatgen's real element list
# below, which is what actually closes that hole (Element("Un") raises).
_WITH_RE = re.compile(
    r"(?i:doped|doping)\b.{0,20}?\bwith\s+(?:(?P<pct1>\d+(?:\.\d+)?)\s*%\s*)?(?P<dopant>[A-Z][a-z]?)\b"
)
_BEFORE_RE = re.compile(
    r"(?:(?P<pct2>\d+(?:\.\d+)?)\s*%\s*)?\b(?P<dopant>[A-Z][a-z]?)[\s-]*(?i:doped)\b"
)
_DOPING_OF_RE = re.compile(
    r"(?:(?P<pct3>\d+(?:\.\d+)?)\s*%\s*)?\b(?P<dopant>[A-Z][a-z]?)\s+(?i:doping)\s+of\b"
)
_ANY_DOPING_WORD_RE = re.compile(r"(?i:doped|doping)")

# Regex for algebraic doping notation: La1-xSrxMnO3 + optional ", x=0.3"
_ALGEBRAIC_DOPING_RE = re.compile(
    r"([A-Z][a-z]?)1\s*[-−]\s*x\s*([A-Z][a-z]?)x",
    re.IGNORECASE,
)
_X_VALUE_RE = re.compile(r"x\s*=\s*(\d+(?:\.\d+)?)")

# Common materials-science abbreviations -> expanded formula
_ABBREVIATION_MAP = {
    "LSMO": "La0.7Sr0.3MnO3",
    "LCMO": "La0.7Ca0.3MnO3",
    "YBCO": "YBa2Cu3O7",
    "BSCCO": "Bi2Sr2CaCu2O8",
    "PCMO": "Pr0.7Ca0.3MnO3",
    "YSZ": "ZrO2",  # yttria-stabilized zirconia (simplified)
    "BTO": "BaTiO3",
    "PZT": "PbZr0.52Ti0.48O3",
    "LSC": "La0.6Sr0.4CoO3",
    "LSF": "La0.6Sr0.4FeO3",
    "BSTO": "Ba0.5Sr0.5TiO3",
    "BCFZ": "BaCo0.4Fe0.4Zr0.2O3",
}

# Build a case-insensitive element-symbol lookup once at import time.
# Sorted longest-first so greedy 2-char matching takes priority over 1-char.
_ELEM_LOWER_MAP: dict = {}


def _build_elem_map() -> None:
    """Populate _ELEM_LOWER_MAP lazily (avoids import cost at module level)."""
    if _ELEM_LOWER_MAP:
        return
    from pymatgen.core.periodic_table import Element as _PMGEl
    for el in _PMGEl:
        _ELEM_LOWER_MAP[el.symbol.lower()] = el.symbol


def _segment_letters(s: str) -> list:
    results: list = []
    def backtrack(idx: int, current: list) -> None:
        if idx == len(s):
            results.append(list(current))
            return
        if idx + 2 <= len(s):
            two = s[idx:idx + 2]
            if two in _ELEM_LOWER_MAP:
                backtrack(idx + 2, current + [_ELEM_LOWER_MAP[two]])
        if idx + 1 <= len(s):
            one = s[idx:idx + 1]
            if one in _ELEM_LOWER_MAP:
                backtrack(idx + 1, current + [_ELEM_LOWER_MAP[one]])
    backtrack(0, [])
    return results


def _score_formula_candidate(cand: str) -> float:
    score = 0.0
    try:
        from pymatgen.core import Composition as _Comp
        comp = _Comp(cand)
        # Check charge balance / oxidation states
        try:
            oxi = comp.oxi_state_guesses(max_sites=-1)
            if oxi:
                score += 50.0
        except Exception:
            pass
        # Penalize obscure radioactive synthetic elements
        radioactives = {'Ac', 'Th', 'Pa', 'Np', 'Pu', 'Am', 'Cm', 'Bk', 'Cf', 'Es', 'Fm', 'Md', 'No', 'Lr'}
        if any(el.symbol in radioactives for el in comp.elements):
            score -= 30.0
    except Exception:
        score -= 100.0
    return score


def _try_normalize_case(formula: str) -> str:
    """Attempt to fix capitalisation in a formula where the user typed all-
    lowercase (or mixed-bad-case) characters.

    Uses backtracking segmentation into valid periodic-table elements followed
    by chemical charge-balance scoring to pick the best representation.

    Examples:
        _try_normalize_case('limno4')         -> 'LiMnO4'
        _try_normalize_case('tio2')           -> 'TiO2'
        _try_normalize_case('feo')            -> 'FeO'
        _try_normalize_case('nacl')           -> 'NaCl'
        _try_normalize_case('caco3')          -> 'CaCO3'
        _try_normalize_case('la0.7sr0.3mno3') -> 'La0.7Sr0.3MnO3'
    """
    _build_elem_map()
    # Find all (letters, number) pairs
    tokens = re.findall(r'([a-zA-Z]+)([\d\.\-\+]*)', formula)
    if not tokens:
        return formula

    candidates = ['']
    for letters, num in tokens:
        segs = _segment_letters(letters.lower())
        if not segs:
            return formula  # Can't segment -> return original
        new_candidates = []
        for c in candidates:
            for seg in segs:
                formed = ''.join(seg[:-1]) + seg[-1] + num
                new_candidates.append(c + formed)
        candidates = new_candidates

    # Deduplicate and score candidates with pymatgen
    scored: list = []
    seen = set()
    for cand in candidates:
        if cand in seen:
            continue
        seen.add(cand)
        scored.append((_score_formula_candidate(cand), cand))

    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1] if scored else formula

# Trailing state-of-matter annotations: (s), (l), (g), (aq)
_STATE_ANNOTATION_RE = re.compile(r"\s*\([slgaq]+\)\s*$", re.IGNORECASE)

# Hydrate separator characters (middle dot, bullet, x)
_HYDRATE_DOT_RE = re.compile(r"[·•·\u00B7\u2022\u00b7]\s*(\d*)\s*H2O", re.IGNORECASE)

# Unicode subscript digit map
_UNICODE_SUBSCRIPT_DIGITS = str.maketrans(
    "₀₁₂₃₄₅₆₇₈₉",
    "0123456789",
)


def _normalize_formula_input(formula: str) -> str:
    """Pre-normalize a raw formula string before any pymatgen or regex processing.

    Handles (in order):
    1. Known abbreviation expansion (LSMO, YBCO, …)
    2. NFKC Unicode normalization
    3. Unicode subscript digit conversion (₀₁₂₃₄₅₆₇₈₉ → ASCII)
    4. Trailing state annotations: (s), (l), (g), (aq)
    5. Hydrate dot separators (·5H2O stripped)
    6. Case normalization: if pymatgen can't parse the string (e.g. 'limno4'),
       try the greedy element-tokenizer to fix capitalisation.

    Returns the normalised formula string.  Never raises.
    """
    formula = formula.strip()
    if not formula:
        return formula

    # 1. Abbreviation check (before any transformation so the key is clean)
    upper = formula.upper()
    if upper in _ABBREVIATION_MAP:
        return _ABBREVIATION_MAP[upper]

    # 2. NFKC normalization
    formula = unicodedata.normalize("NFKC", formula)

    # 3. Unicode subscript digits
    formula = formula.translate(_UNICODE_SUBSCRIPT_DIGITS)

    # 4. Strip trailing state annotations: LaMnO3(s) → LaMnO3
    formula = _STATE_ANNOTATION_RE.sub("", formula)

    # 5. Hydrate dot: CuSO4·5H2O → CuSO4
    formula = _HYDRATE_DOT_RE.sub("", formula).strip()

    # 6. Case normalisation — only attempted if pymatgen can't parse the string
    #    as-is (avoids mutating formulas that already parse correctly, e.g. CO2).
    try:
        from pymatgen.core import Composition as _Comp
        _Comp(formula)
        # Parsed fine — return as-is
        return formula
    except Exception:
        pass

    # pymatgen rejected it — try fixing capitalisation
    try:
        candidate = _try_normalize_case(formula)
        from pymatgen.core import Composition as _Comp
        _Comp(candidate)      # validate the corrected version
        return candidate
    except Exception:
        pass

    # Nothing worked — return the best we have (state-stripped / Unicode-cleaned)
    return formula


def _is_valid_element_symbol(token: str) -> bool:
    try:
        Element(token)
        return True
    except Exception:
        return False


def _has_fractional_site_occupancy(formula: str) -> bool:
    """Non-integer coefficients (e.g. La0.7Sr0.3MnO3) are the classic signature
    of a doped/substituted site. NOTE: this alone is not 100% reliable -
    some legitimately undoped compounds are non-stoichiometric by nature
    (e.g. Fe0.95O, vacancy-ordered defect structures) - so this heuristic
    only produces a *candidate* classification, still subject to the
    ambiguity check below.
    """
    try:
        comp = Composition(formula)
    except Exception:
        return bool(FRACTIONAL_COEFF_RE.search(formula))
    return any(abs(amt - round(amt)) > 1e-6 for amt in comp.values())


def _parse_doping_phrase(text: str) -> Tuple[Optional[str], Optional[float]]:
    """Look for explicit natural-language doping phrasing and pull out the dopant
    element (and fraction, if a percentage is stated in the same phrase).
    Returns (dopant_symbol_or_None, fraction_or_None). "Undoped ..." correctly
    returns (None, None) because "Un" fails element validation and no other
    pattern matches.

    Note: full element names (e.g. "doped with strontium") are NOT matched by the
    symbol regex. This is intentional - we fall through to ask_user rather than
    silently misidentify a partial name match as a dopant symbol.
    """
    if not _ANY_DOPING_WORD_RE.search(text):
        return None, None

    # Checked in order of specificity: "doped with X" is the least ambiguous,
    # then "X-doped", then "X doping of".
    for pattern in (_WITH_RE, _BEFORE_RE, _DOPING_OF_RE):
        m = pattern.search(text)
        if not m:
            continue
        token = m.group("dopant")
        if not _is_valid_element_symbol(token):
            continue  # e.g. "Un" from "Undoped" - not a real element, reject
        gd = m.groupdict()
        pct = gd.get("pct1") or gd.get("pct2") or gd.get("pct3")
        fraction = float(pct) / 100.0 if pct else None
        return token, fraction

    return None, None


def _parse_algebraic_doping(text: str) -> Optional[Tuple[str, str, float]]:
    """Parse the A(1-x)B(x) algebraic doping notation common in papers.

    Handles:
    - "La1-xSrxMnO3, x=0.3" → ("La", "Sr", 0.3)
    - "La(1-x)Sr(x)MnO3 with x=0.25" → ("La", "Sr", 0.25)
    - Pattern without x= value → returns (host_el, dopant_el, None)

    Returns (host_element, dopant_element, x_value_or_None).
    """
    # Match A1-xBx pattern
    m = _ALGEBRAIC_DOPING_RE.search(text)
    if not m:
        return None

    host_el = m.group(1)
    dopant_el = m.group(2)

    # Validate both are real elements
    if not (_is_valid_element_symbol(host_el) and _is_valid_element_symbol(dopant_el)):
        return None

    # Try to find x= value
    x_match = _X_VALUE_RE.search(text)
    x_value = float(x_match.group(1)) if x_match else None

    return host_el, dopant_el, x_value


def _looks_like_classic_doping_pattern(formula: str) -> bool:
    """Distinguish 'two elements sharing one site with fractions summing to 1'
    (classic doping, e.g. La0.7Sr0.3) from generic non-stoichiometry
    (e.g. Fe0.95O, a single-element deficiency). This is still a heuristic,
    not a guarantee - it exists purely to decide whether we're confident
    enough to skip asking the user.
    """
    try:
        comp = Composition(formula)
    except Exception:
        return False
    fractional = {el: amt for el, amt in comp.items() if abs(amt - round(amt)) > 1e-6}
    if len(fractional) < 2:
        return False
    # crude check: do two fractional amounts sum to ~an integer (i.e. co-occupy one site)?
    amounts = list(fractional.values())
    for i in range(len(amounts)):
        for j in range(i + 1, len(amounts)):
            if abs((amounts[i] + amounts[j]) - round(amounts[i] + amounts[j])) < 0.05:
                return True
    return False


def _count_fractional_species_pairs(formula: str) -> int:
    """Count how many pairs of elements appear to share a site (co-doping check).
    Returns the count of co-occupancy pairs found.
    """
    try:
        comp = Composition(formula)
    except Exception:
        return 0
    fractional = {el: amt for el, amt in comp.items() if abs(amt - round(amt)) > 1e-6}
    if len(fractional) < 2:
        return 0
    amounts = list(fractional.values())
    pairs = 0
    for i in range(len(amounts)):
        for j in range(i + 1, len(amounts)):
            if abs((amounts[i] + amounts[j]) - round(amounts[i] + amounts[j])) < 0.05:
                pairs += 1
    return pairs


def _infer_doping_from_stoichiometry(formula: str) -> Optional[Tuple[str, str, str, float]]:
    """Try to infer (host_formula, dopant_species, host_site_species, dopant_fraction)
    from a classic doped formula such as Fe1.9Ti0.1O3 or La0.7Sr0.3MnO3.
    """
    try:
        comp = Composition(formula)
    except Exception:
        return None
    fractional = {el: amt for el, amt in comp.items() if abs(amt - round(amt)) > 1e-6}
    if len(fractional) < 2:
        return None
    items = list(fractional.items())
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            el_a, amt_a = items[i]
            el_b, amt_b = items[j]
            site_sum = amt_a + amt_b
            if abs(site_sum - round(site_sum)) < 0.05:
                # The smaller amount is the dopant
                if amt_a < amt_b:
                    dopant = str(el_a)
                    host_site = str(el_b)
                    fraction = round(amt_a / site_sum, 4)
                else:
                    dopant = str(el_b)
                    host_site = str(el_a)
                    fraction = round(amt_b / site_sum, 4)

                # Reconstruct host formula by replacing the combined site with host_site
                host_dict = {}
                for el, amt in comp.items():
                    el_str = str(el)
                    if el_str == dopant:
                        continue
                    elif el_str == host_site:
                        host_dict[el_str] = round(site_sum)
                    else:
                        host_dict[el_str] = amt
                host_comp = Composition(host_dict)
                host_formula = host_comp.reduced_formula
                return host_formula, dopant, host_site, fraction
    return None


def classify_compound(
    query: CompoundQuery,
    ask_user: AskUserFn,
) -> Tuple[bool, Optional[DopingSpec]]:
    """Returns (is_doped, doping_spec_or_None).
    doping_spec is only populated when is_doped=True AND we have (or can get)
    enough information to actually do the substitution later. If the user
    can't/won't supply the missing pieces, doping_spec stays partially filled
    and the orchestrator must ask again at the doping stage.
    """

    # --- Pre-normalize the raw input before any classification ---
    raw_normalized = _normalize_formula_input(query.raw_input)
    # Store back the normalized form so downstream stages use a clean string
    if raw_normalized != query.raw_input:
        query.raw_input = raw_normalized

    # --- Level 0: algebraic doping notation (e.g. La1-xSrxMnO3, x=0.3) ---
    algebraic = _parse_algebraic_doping(query.raw_input)
    algebraic_x_missing = False
    if algebraic:
        host_el, dopant_el, x_value = algebraic
        if x_value is None:
            # Found the pattern but no x value - ask for it
            raw_x = ask_user(
                f"The formula '{query.raw_input}' uses algebraic notation. "
                f"What is the value of x?",
                None,
            )
            try:
                x_value = float(raw_x)
            except (TypeError, ValueError):
                x_value = None
                algebraic_x_missing = True

        if x_value is not None and not algebraic_x_missing:
            # Populate the query's dopant fields from the algebraic notation
            if not query.dopant_element:
                query.dopant_element = dopant_el
            if not query.host_site_species:
                query.host_site_species = host_el
            if query.dopant_fraction is None:
                query.dopant_fraction = x_value

            # Build the host_formula if not already provided.
            # Since the algebraic formula looks like "A1-xBxCDE...", we need to extract
            # the remaining elements from the original formula string. We can do that by
            # removing the algebraic part and parsing what's left, then combining:
            # For simplicity, if we can't infer the full formula, we at least set a
            # placeholder host that _infer_doping_from_stoichiometry can complete later.
            # Best effort: try to extract remaining formula suffix after the "Bx" match
            if not query.host_formula:
                # Try to extract non-algebraic tail of the formula
                # e.g. "La1-xSrxMnO3, x=0.3" -> extract "MnO3" tail
                try:
                    import re as _re
                    tail_m = _re.search(
                        r"[A-Z][a-z]?x\s*([A-Z].*?)(?:,|$|\s+x\s*=|\s+with)", query.raw_input
                    )
                    if tail_m:
                        tail = tail_m.group(1).strip()
                        # Attempt to parse host = host_el(1.0) + tail elements
                        host_formula_str = f"{host_el}{tail}"
                        from pymatgen.core import Composition as _Comp
                        host_comp = _Comp(host_formula_str)
                        query.host_formula = host_comp.reduced_formula
                except Exception:
                    pass  # will fall through to ask_user for host_formula

    # --- Level 1: explicit UI hint (most reliable - trust it outright) ---
    if query.is_doped_hint is not None:
        is_doped = query.is_doped_hint
    else:
        # --- Level 2: explicit natural-language phrasing ---
        phrase_dopant, phrase_fraction = _parse_doping_phrase(query.raw_input)
        if phrase_dopant:
            is_doped = True
            if not query.dopant_element:
                query.dopant_element = phrase_dopant
            if phrase_fraction is not None and query.dopant_fraction is None:
                query.dopant_fraction = phrase_fraction
        elif _ANY_DOPING_WORD_RE.search(query.raw_input):
            # Doping vocabulary present ("doped"/"doping") but no valid element symbol
            # could be extracted (e.g. "doped with strontium" uses a full element name,
            # "heavily doped LaMnO3" / "n-type doped Si" name no dopant at all).
            # Per QA §4.1-4.3: classify as doped and let the ask_user flow below
            # collect the missing dopant info rather than silently returning is_doped=False.
            is_doped = True
            # dopant_element stays None — the ask_user block below will request it
        elif algebraic and query.dopant_fraction is not None:
            # Algebraic notation resolved successfully
            is_doped = True
        else:
            # --- Level 3: formula-shape heuristic ---
            has_fraction = _has_fractional_site_occupancy(query.raw_input)
            if not has_fraction:
                is_doped = False
            elif _looks_like_classic_doping_pattern(query.raw_input):
                is_doped = True
            else:
                # --- Level 4: genuinely ambiguous -> ask the user ---
                answer = ask_user(
                    f"'{query.raw_input}' has non-integer stoichiometry, but it's not clear "
                    f"whether this is a doped/substituted compound or a naturally "
                    f"non-stoichiometric (defect) undoped compound. Which is it?",
                    ["Doped (substitutional)", "Non-stoichiometric / undoped", "Not sure - treat as undoped"],
                )
                is_doped = (answer == "Doped (substitutional)")

    if not is_doped:
        return False, None

    # --- Guard: check whether input looks like it has >1 co-doping pair
    #    (more dopants than DopingSpec handles via a single primary+co_dopant) ---
    # This is QA item 4.4: we support primary dopant + co_dopants list, but
    # if the stoichiometry suggests > 1 fractional pair AND there are no co_dopant
    # specs given in the query, warn rather than silently picking the wrong one.
    n_pairs = _count_fractional_species_pairs(query.raw_input)
    if n_pairs > 1 and not (hasattr(query, "co_dopants") and query.co_dopants):
        # More than one pair of co-occupying species detected in the formula
        # but no co-dopant spec provided. Warn the user.
        answer = ask_user(
            f"'{query.raw_input}' appears to contain {n_pairs} pairs of co-occupying "
            f"species, suggesting multiple dopants on different sites. "
            f"Only one primary dopant is inferred automatically. "
            f"Would you like to proceed with just the primary (largest) dopant substitution, "
            f"or cancel to provide explicit co-dopant details?",
            ["Proceed with primary dopant only", "Cancel - I will provide co-dopant details"],
        )
        if answer and "Cancel" in answer:
            return True, None  # Signal doped but incomplete spec

    # Try automatic stoichiometry inference first if any pieces are missing
    if not (query.host_formula and query.dopant_element and query.host_site_species and query.dopant_fraction is not None):
        inferred = _infer_doping_from_stoichiometry(query.raw_input)
        if inferred:
            inf_host, inf_dopant, inf_site, inf_frac = inferred
            if not query.host_formula:
                query.host_formula = inf_host
            if not query.dopant_element:
                query.dopant_element = inf_dopant
            if not query.host_site_species:
                query.host_site_species = inf_site
            if query.dopant_fraction is None:
                query.dopant_fraction = inf_frac

    # --- Build DopingSpec, asking for whatever pieces are still missing ---
    host_formula = query.host_formula
    if not host_formula:
        host_formula = ask_user(
            f"To generate '{query.raw_input}' as a doped structure, what is the "
            f"undoped parent/host compound's formula? (e.g. 'LaMnO3')",
            None,
        )

    dopant_element = query.dopant_element
    if not dopant_element:
        dopant_element = ask_user("What element is the dopant?", None)

    host_site_species = query.host_site_species
    if not host_site_species:
        host_site_species = ask_user(
            f"Which species in {host_formula} does {dopant_element} substitute onto? "
            f"(e.g. 'La')",
            None,
        )

    dopant_fraction = query.dopant_fraction
    if dopant_fraction is None:
        raw = ask_user(
            f"What fraction/percentage of {host_site_species} sites does {dopant_element} occupy? "
            f"(e.g. 0.3 for 30%)",
            None,
        )
        try:
            dopant_fraction = float(raw)
        except (TypeError, ValueError):
            dopant_fraction = None  # orchestrator must handle this being unresolved

    # --- Boundary value guards (QA §5.1, §5.2) ---
    if dopant_fraction is not None:
        if dopant_fraction <= 0.0:
            # dopant_fraction=0.0 means no doping — treat as undoped
            return False, None
        if dopant_fraction >= 1.0:
            # Full substitution: not really "doping". Build the spec but flag it.
            import logging
            logging.getLogger("cif_pipeline.classify").warning(
                f"dopant_fraction={dopant_fraction} means complete substitution "
                f"({dopant_element} replaces 100% of {host_site_species} sites). "
                f"The resulting structure is effectively a new undoped compound."
            )

    spec = None
    if host_formula and dopant_element and host_site_species and dopant_fraction is not None:
        co_spec_list = []
        if hasattr(query, "co_dopants") and query.co_dopants:
            for item in query.co_dopants:
                co_elem = item.get("dopant_element") or item.get("dopant_species")
                co_site = item.get("host_site_species") or item.get("site")
                co_frac = item.get("dopant_fraction") if item.get("dopant_fraction") is not None else item.get("fraction")
                if co_elem and co_site and co_frac is not None:
                    try:
                        co_spec_list.append(DopingSpec(
                            host_formula=host_formula,
                            host_site_species=str(co_site).strip(),
                            dopant_species=str(co_elem).strip(),
                            dopant_fraction=float(co_frac),
                        ))
                    except Exception:
                        pass

        spec = DopingSpec(
            host_formula=host_formula,
            host_site_species=host_site_species,
            dopant_species=dopant_element,
            dopant_fraction=dopant_fraction,
            co_dopants=co_spec_list,
        )

    return True, spec
