"""
Shared data structures for the compound -> CIF pipeline.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
from pymatgen.core import Structure


@dataclass
class RecognitionResult:
    """Stage 0 output - whether/how the raw input was successfully turned
    into a clean, parseable formula before anything else in the pipeline
    touches it. See recognition.py for the full strategy.
    """
    raw_input: str
    cleaned_input: str
    resolved_formula: Optional[str]                  # the formula the REST of the pipeline should use, or None if recognition failed
    method: str                                        # "direct" | "cleaned" | "extracted" | "x_notation" | "abbreviation" | "case_normalized" | "user_corrected" | "failed"
    success: bool
    warnings: list = field(default_factory=list)       # every attempt made, in order - full transparency trail


@dataclass
class CompoundQuery:
    """What comes in from the web interface / user."""
    raw_input: str                              # e.g. "La0.7Sr0.3MnO3" or "MnO3 doped with 30% Sr on La site"
    is_doped_hint: Optional[bool] = None         # explicit flag from a UI toggle, if the form has one
    host_formula: Optional[str] = None           # e.g. "LaMnO3" - only if user already knows/gives it
    dopant_element: Optional[str] = None         # e.g. "Sr"
    dopant_fraction: Optional[float] = None      # e.g. 0.3
    host_site_species: Optional[str] = None      # which species the dopant replaces, e.g. "La"
    space_group: Optional[str] = None            # e.g. "Pnma" or "62" - if the user knows/specifies it
    co_dopants: list[dict] = field(default_factory=list)  # list of additional co-dopant specs: [{"dopant_element": "...", "host_site_species": "...", "dopant_fraction": ...}]


@dataclass
class DopingSpec:
    """Fully resolved doping description - required before we can dope a parent structure.
    Every field here must eventually be filled (either given up front or via ask_user),
    never guessed from a bare formula string.
    """
    host_formula: str
    host_site_species: str
    dopant_species: str
    dopant_fraction: float
    site_index: Optional[int] = None             # which symmetry-distinct site, once parent structure is known
    co_dopants: list[DopingSpec] = field(default_factory=list)  # additional co-dopants (co-valent / multi-site)
    space_group: Optional[str] = None


@dataclass
class SearchMatch:
    source: str                                   # "MP" | "OQMD" | "JARVIS" | "COD" | "internal"
    record_id: str
    structure: Structure
    space_group: Optional[str] = None
    formula: Optional[str] = None
    match_confidence: float = 1.0                 # 1.0 = exact formula match, lower for fuzzy/tolerance matches
    source_url: Optional[str] = None              # direct link to the record - for UI provenance display
    is_theoretical: Optional[bool] = None          # COD flags some entries as theoretical/predicted, not measured
    extra: dict = field(default_factory=dict)      # source-specific metadata (formation energy, citation, etc.)


@dataclass
class FormulaMultiplicityInfo:
    """Explains the relationship between what the user typed and the formula
    actually used for searching/generation, so the UI can show it plainly
    instead of silently substituting a different-looking formula.
    """
    input_formula: str
    reduced_formula: str
    multiplier: float                              # input_formula = reduced_formula * multiplier (Z-like factor)
    was_reduced: bool                               # True if we changed the formula before searching
    reason: str                                     # human-readable explanation for the UI


@dataclass
class NormalizationTrace:
    """Full transparency record of how the raw input became the actual search
    key(s) used against the databases - meant to be rendered directly in the UI
    so normalization is never a silent/invisible step.
    """
    raw_input: str
    is_doped: bool
    classification_reasoning: str                   # which classification level fired, and why
    search_key_used: str                             # the actual formula string sent to each database
    multiplicity: Optional[FormulaMultiplicityInfo] = None   # only set for the undoped path
    doped_match_rule: Optional[str] = None            # only set for the doped path - explains the tolerance rule used


@dataclass
class DopedStructureValidation:
    """Result of the SEPARATE validation pass applied to a doped structure
    after substitution, as distinct from filter_rules.filter_candidates()
    (which only ever runs on raw, freshly-generated undoped candidates).
    """
    is_valid: bool
    occupancy_sum_ok: bool
    occupancy_sum: float
    bond_length_ok: bool
    bond_length_issues: list = field(default_factory=list)
    notes: str = ""


@dataclass
class GenerationDiagnostics:
    """Per-iteration bookkeeping so the retry loop knows *why* candidates failed
    and can adjust generation parameters instead of blindly resampling."""
    iteration: int
    n_generated: int = 0
    n_parse_failed: int = 0
    n_bond_length_failed: int = 0
    n_symmetry_failed: int = 0
    n_charge_balance_failed: int = 0
    n_duplicates_removed: int = 0
    n_relax_nonconverged: int = 0
    n_survived: int = 0
    failure_examples: dict = field(default_factory=dict)  # {"bond_length": ["Mn-O 1.3A too short", ...], ...}
    notes: str = ""


@dataclass
class PipelineResult:
    cif_string: Optional[str]
    source: str
    # "existing_MP" | "existing_OQMD" | "existing_JARVIS" | "existing_COD" | "existing_internal"
    # | "generated_undoped" | "generated_doped" | "ambiguous_polymorphs" | "failed"
    notes: str = ""
    diagnostics_history: list = field(default_factory=list)      # list[GenerationDiagnostics]
    normalization_trace: Optional[NormalizationTrace] = None      # UI-facing: what happened to the formula
    matched_record: Optional[SearchMatch] = None                  # UI-facing: exactly which record we used, if existing
    candidate_matches: list = field(default_factory=list)         # list[SearchMatch] - populated when polymorphs
                                                                    # are ambiguous and awaiting a UI picker choice
    doped_validation: Optional[DopedStructureValidation] = None   # UI-facing: result of the doped-structure check
    recognition_result: Optional[RecognitionResult] = None        # UI-facing: Stage 0 input recognition & normalization trace
    is_reliable: bool = True                                       # False when we returned a structure flagged as unrelaxed/untrustworthy
