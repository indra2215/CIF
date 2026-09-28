"""
Stage 0 (runs BEFORE classification): compound recognition.

Everything downstream (classify.py's formula-shape heuristic, matching.py's
normalization, search, generation) needs an actual, parseable chemical
formula to work with a pymatgen Composition. Raw user input very often is
NOT that - it's a full sentence, an abbreviation, or a formula written with
formatting pymatgen won't accept as-is. This module is the single front door
that takes whatever the user typed and turns it into a clean formula (or
fails explicitly and asks for a correction), before any other stage touches
Composition() directly.

Things this handles:
  - Full natural-language input containing a formula plus other words
    (e.g. "LaMnO3 doped with 30% Sr") - the formula is EXTRACTED, not
    assumed to be the whole string.
  - Common formatting noise: Unicode subscript/superscript digits, hydrate
    separators (. vs * vs · vs •), trailing state annotations (s)/(aq)/(l)/(g),
    stray whitespace.
  - The "1-x / x=" algebraic doping notation that real papers actually use
    (e.g. "La1-xSrxMnO3, x=0.3") - resolved into an equivalent decimal
    formula before handing off to the rest of the pipeline.
  - Common materials-science abbreviations (LSMO, YBCO, BSCCO, PZT, NMC, etc.)
  - Intelligent case-correction for lowercase inputs (e.g. "limno4" -> "LiMnO4",
    "tio2" -> "TiO2") using periodic table segmentation.
  - Interactive user correction fallback if all automatic methods fail.
"""

from __future__ import annotations
import re
import unicodedata
import logging
from typing import List, Optional

from pymatgen.core import Composition
from .models import RecognitionResult
from .user_interaction import AskUserFn

log = logging.getLogger("cif_pipeline.recognition")

# --- Unicode digit normalization -------------------------------------------
_SUBSCRIPT_DIGITS = "₀₁₂₃₄₅₆₇₈₉"
_SUPERSCRIPT_DIGITS = "⁰¹²³⁴⁵⁶⁷⁸⁹"
_ASCII_DIGITS = "0123456789"
_UNICODE_DIGIT_TRANSLATION = str.maketrans(
    _SUBSCRIPT_DIGITS + _SUPERSCRIPT_DIGITS,
    _ASCII_DIGITS + _ASCII_DIGITS,
)

# --- Trailing state annotations --------------------------------------------
_STATE_ANNOTATION_RE = re.compile(r"\s*\((?i:s|aq|l|g)\)\s*$")

# --- Hydrate separators -----------------------------------------------------
_HYDRATE_SEPARATOR_RE = re.compile(r"[·•*·\u00B7\u2022\u00b7]")

# --- Formula-substring extraction from a longer sentence -------------------
_FORMULA_TOKEN_RUN_RE = re.compile(r"(?:[A-Z][a-z]?\d*(?:\.\d+)?){2,}")

# --- "1-x / x=" algebraic doping notation -----------------------------------
_X_NOTATION_PATTERN_RE = re.compile(
    r"(?P<host>[A-Z][a-z]?)\(?1\s*[-−]\s*x\)?(?P<dopant>[A-Z][a-z]?)x",
    re.IGNORECASE,
)
_X_VALUE_RE = re.compile(r"\bx\s*=\s*(?P<value>\d*\.?\d+)\b", re.IGNORECASE)

# --- Common abbreviations ---------------------------------------------------
COMMON_ABBREVIATIONS = {
    "LSMO": "La0.7Sr0.3MnO3",
    "LCMO": "La0.7Ca0.3MnO3",
    "YBCO": "YBa2Cu3O7",
    "BSCCO": "Bi2Sr2CaCu2O8",
    "PCMO": "Pr0.7Ca0.3MnO3",
    "YSZ": "ZrO2",
    "BTO": "BaTiO3",
    "PZT": "PbZr0.52Ti0.48O3",
    "LSC": "La0.6Sr0.4CoO3",
    "LSF": "La0.6Sr0.4FeO3",
    "BSTO": "Ba0.5Sr0.5TiO3",
    "BCFZ": "BaCo0.4Fe0.4Zr0.2O3",
    "NMC": "LiNi0.33Mn0.33Co0.33O2",
    "LNO": "LaNiO3",
    "STO": "SrTiO3",
    "LAO": "LaAlO3",
}


def clean_formula_text(text: str) -> str:
    """Safe formatting normalization applied to any formula string.
    Does not attempt extraction or abbreviation resolution - just formatting cleanup.
    """
    if not text:
        return ""
    text = text.strip()
    text = unicodedata.normalize("NFKC", text)
    text = text.translate(_UNICODE_DIGIT_TRANSLATION)
    text = _STATE_ANNOTATION_RE.sub("", text)
    text = _HYDRATE_SEPARATOR_RE.sub(".", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _try_parse(formula: str) -> bool:
    """Return True if pymatgen can parse this string as a non-empty chemical composition."""
    if not formula:
        return False
    try:
        comp = Composition(formula)
        return len(comp) > 0
    except Exception:
        return False


def extract_formula_candidates(text: str) -> List[str]:
    """Pull out formula-shaped substrings from a sentence, longest first.
    e.g. 'LaMnO3 doped with 30% Sr' -> ['LaMnO3']
    """
    matches = [m.group(0) for m in _FORMULA_TOKEN_RUN_RE.finditer(text)]
    return sorted(set(matches), key=len, reverse=True)


def resolve_x_notation(text: str) -> Optional[str]:
    """Resolve 'A1-xBx..., x=value' into a decimal formula.
    e.g. 'La1-xSrxMnO3, x=0.3' -> 'La0.7Sr0.3MnO3'
    """
    pattern_match = _X_NOTATION_PATTERN_RE.search(text)
    value_match = _X_VALUE_RE.search(text)
    if not pattern_match or not value_match:
        return None

    try:
        x = float(value_match.group("value"))
    except ValueError:
        return None
    if not (0.0 < x < 1.0):
        return None

    host = pattern_match.group("host")
    dopant = pattern_match.group("dopant")
    host_frac = round(1.0 - x, 6)

    replaced = _X_NOTATION_PATTERN_RE.sub(
        f"{host}{host_frac:g}{dopant}{x:g}", text, count=1
    )
    replaced = _X_VALUE_RE.sub("", replaced)
    replaced = re.sub(r",\s*$", "", replaced).strip().rstrip(",").strip()
    replaced = re.sub(r"\s+", "", replaced)
    return replaced if _try_parse(replaced) else None


def lookup_abbreviation(text: str) -> Optional[str]:
    """Check known abbreviations case-insensitively."""
    key = text.strip().upper()
    return COMMON_ABBREVIATIONS.get(key)


def _try_case_normalization(text: str) -> Optional[str]:
    """Attempt case reconstruction for lowercase inputs (e.g. limno4 -> LiMnO4)."""
    try:
        from .classify import _try_normalize_case
        cand = _try_normalize_case(text)
        if cand != text and _try_parse(cand):
            return cand
    except Exception:
        pass
    return None


def recognize_compound_formula(
    raw_input: str,
    ask_user: Optional[AskUserFn] = None,
) -> RecognitionResult:
    """Main entry point for Stage 0 (Compound Recognition).
    Tries in order of specificity:
      1. Direct parse
      2. Cleaned parse (Unicode digits, hydrates, state annotations)
      3. Resolution of '1-x / x=' algebraic notation
      4. Extraction of formula substring from conversational sentences
      5. Abbreviation lookup (LSMO, YBCO, etc.)
      6. Intelligent case-normalization (e.g. limno4 -> LiMnO4)
      7. Ask user for correction if interactive callback is provided
    """
    raw_input = (raw_input or "").strip()
    warnings: List[str] = []

    if not raw_input:
        return RecognitionResult(
            raw_input=raw_input,
            cleaned_input="",
            resolved_formula=None,
            method="failed",
            success=False,
            warnings=["empty input provided"],
        )

    # 1. Abbreviation lookup (checked FIRST so abbreviations like LSMO, YBCO aren't misparsed as dummy elements)
    abbrev_resolved = lookup_abbreviation(raw_input)
    if abbrev_resolved and _try_parse(abbrev_resolved):
        warnings.append(f"resolved abbreviation '{raw_input}' to '{abbrev_resolved}'")
        return RecognitionResult(
            raw_input=raw_input,
            cleaned_input=raw_input,
            resolved_formula=abbrev_resolved,
            method="abbreviation",
            success=True,
            warnings=warnings,
        )

    # 2. Direct parse
    if _try_parse(raw_input):
        return RecognitionResult(
            raw_input=raw_input,
            cleaned_input=raw_input,
            resolved_formula=raw_input,
            method="direct",
            success=True,
            warnings=warnings,
        )
    warnings.append(f"direct parse of '{raw_input}' failed")

    # 3. Cleaned parse
    cleaned = clean_formula_text(raw_input)
    # Check abbreviation again on cleaned input
    abbrev_cleaned = lookup_abbreviation(cleaned)
    if abbrev_cleaned and _try_parse(abbrev_cleaned):
        warnings.append(f"resolved cleaned abbreviation '{cleaned}' to '{abbrev_cleaned}'")
        return RecognitionResult(
            raw_input=raw_input,
            cleaned_input=cleaned,
            resolved_formula=abbrev_cleaned,
            method="abbreviation",
            success=True,
            warnings=warnings,
        )

    if cleaned != raw_input and _try_parse(cleaned):
        return RecognitionResult(
            raw_input=raw_input,
            cleaned_input=cleaned,
            resolved_formula=cleaned,
            method="cleaned",
            success=True,
            warnings=warnings,
        )
    if cleaned != raw_input:
        warnings.append(f"cleaned parse of '{cleaned}' failed")

    # 4. "1-x / x=" algebraic notation
    x_resolved = resolve_x_notation(cleaned)
    if x_resolved:
        warnings.append(f"resolved algebraic doping notation to '{x_resolved}'")
        return RecognitionResult(
            raw_input=raw_input,
            cleaned_input=cleaned,
            resolved_formula=x_resolved,
            method="x_notation",
            success=True,
            warnings=warnings,
        )

    # 4. Extract formula substring from conversational sentence
    candidates = extract_formula_candidates(cleaned)
    for candidate in candidates:
        if _try_parse(candidate):
            warnings.append(f"extracted formula substring '{candidate}' from input sentence")
            return RecognitionResult(
                raw_input=raw_input,
                cleaned_input=cleaned,
                resolved_formula=candidate,
                method="extracted",
                success=True,
                warnings=warnings,
            )
    if candidates:
        warnings.append(f"extracted candidate(s) {candidates} did not parse")

    # 6. Intelligent case-normalization
    case_cand = _try_case_normalization(cleaned)
    if case_cand:
        warnings.append(f"case-normalized '{cleaned}' to periodic table formula '{case_cand}'")
        return RecognitionResult(
            raw_input=raw_input,
            cleaned_input=cleaned,
            resolved_formula=case_cand,
            method="case_normalized",
            success=True,
            warnings=warnings,
        )

    # 7. Ask user if interactive callback is available
    if ask_user:
        best_guess = candidates[0] if candidates else cleaned
        suggestion_note = f" (best guess: '{best_guess}')" if best_guess else ""
        correction = ask_user(
            f"Could not recognize '{raw_input}' as a chemical formula{suggestion_note}. "
            f"Please provide the correct chemical formula.",
            None,
        )
        if correction and _try_parse(correction):
            warnings.append(f"user corrected input to '{correction}'")
            return RecognitionResult(
                raw_input=raw_input,
                cleaned_input=cleaned,
                resolved_formula=correction,
                method="user_corrected",
                success=True,
                warnings=warnings,
            )

    warnings.append("recognition exhausted all methods without resolving a valid formula")
    return RecognitionResult(
        raw_input=raw_input,
        cleaned_input=cleaned,
        resolved_formula=None,
        method="failed",
        success=False,
        warnings=warnings,
    )
