"""
Full pipeline orchestration:

  1. Classify doped vs undoped (multi-level, asks user if ambiguous)
  2. Search external databases (formula normalized per doped/undoped rule,
     polymorphs disambiguated)
  3. Search internal database (identical rules to step 2)
  4a. If undoped and not found -> CrystaLLM generate/filter/relax feedback loop
  4b. If doped and not found   -> resolve parent structure, apply doping
                                    transformation, relax
  5. Return final CIF + full diagnostics trail
"""

from __future__ import annotations
import logging
from typing import Optional

from .models import CompoundQuery, PipelineResult
from .recognition import recognize_compound_formula
from .classify import classify_compound
from .matching import normalize_formula_with_trace
from .search_external import search_external_databases
from .search_internal import search_internal_database
from .generate import generate_with_feedback_loop
from .doping import resolve_parent_structure, apply_doping_with_validation
from .relax import relax_with_mace
from .user_interaction import AskUserFn, default_cli_ask
from .config import get_mp_api_key, get_mace_model, get_mace_device

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("cif_pipeline.orchestrator")


def run_pipeline(
    query: CompoundQuery,
    mp_api_key: Optional[str] = None,
    ask_user: AskUserFn = default_cli_ask,
    max_iterations: int = 5,
    samples_per_iteration: int = 25,
    mace_model_path: Optional[str] = None,
) -> PipelineResult:

    # --- Input guard: reject empty / whitespace-only / None inputs ---
    # (QA §1.6) — this must happen before classification so downstream stages
    # never receive an unparseable formula string.
    raw = getattr(query, "raw_input", None) or ""
    if not raw.strip():
        return PipelineResult(
            cif_string=None,
            source="failed",
            notes=(
                "Empty or blank formula provided. "
                "Please enter a valid chemical formula (e.g. 'Fe2O3', 'LaMnO3', 'TiO2')."
            ),
        )

    # --- Stage 0: Compound Recognition ---
    # Normalize noise, extract formula from conversational text, resolve algebraic notation,
    # or expand abbreviations before classification or database searches touch it.
    recognition = recognize_compound_formula(raw, ask_user)
    if not recognition.success:
        return PipelineResult(
            cif_string=None,
            source="failed",
            notes=(
                f"Could not recognize '{raw}' as a chemical formula: "
                f"{'; '.join(recognition.warnings)}"
            ),
            recognition_result=recognition,
        )
    recognized_formula = recognition.resolved_formula
    log.info(f"[Stage 0 Recognition] '{raw}' -> '{recognized_formula}' (method={recognition.method})")

    # Auto-load from .env if not explicitly provided
    if mp_api_key is None:
        mp_api_key = get_mp_api_key()
    if mace_model_path is None:
        mace_model_path = get_mace_model()

    # --- Stage 3 done first conceptually, since it decides how normalization/
    #     search in stages 1-2 should even behave (doped formulas are matched
    #     differently from undoped ones) ---
    is_doped, doping_spec = classify_compound(query, ask_user, recognized_formula=recognized_formula)
    classification_reasoning = (
        f"is_doped_hint={query.is_doped_hint}" if query.is_doped_hint is not None else
        "resolved via phrasing/formula-shape heuristics (see classify.py)"
    )
    log.info(f"Classification: doped={is_doped}, spec={doping_spec}")

    # Build the normalization trace up front - this is what the UI should
    # display so the user can see exactly what search key was used and why,
    # rather than normalization being an invisible step.
    search_key, normalization_trace = normalize_formula_with_trace(
        recognized_formula, is_doped, classification_reasoning,
    )

    # --- Stage 1: external databases ---
    ext_match, ext_candidates = search_external_databases(
        recognized_formula, is_doped, doping_spec, mp_api_key,
        query.space_group, ask_user,
    )
    if ext_match:
        return PipelineResult(
            cif_string=ext_match.structure.to(fmt="cif"),
            source=f"existing_{ext_match.source}",
            notes=f"matched record {ext_match.record_id} ({ext_match.source_url or 'no URL available'})",
            normalization_trace=normalization_trace,
            matched_record=ext_match,
            candidate_matches=ext_candidates,
            recognition_result=recognition,
            relaxation_skipped=False,
            relaxation_status="EXPERIMENTAL (database entry)",
            final_space_group=ext_match.space_group,
            diagnostics_history=[],
            is_reliable=True,
        )
    if len(ext_candidates) > 1:
        # Ambiguous polymorphs were found but no choice was resolved (e.g. the
        # user declined to pick, or ask_user is a non-blocking web callback
        # that hasn't returned yet). Surface everything found so the UI can
        # render its own picker rather than the pipeline silently giving up.
        return PipelineResult(
            cif_string=None,
            source="ambiguous_polymorphs",
            notes=f"found {len(ext_candidates)} external candidates but none was selected",
            normalization_trace=normalization_trace,
            candidate_matches=ext_candidates,
            recognition_result=recognition,
        )

    # --- Stage 2: internal database ---
    internal_match, internal_candidates = search_internal_database(
        recognized_formula, is_doped, doping_spec, query.space_group, ask_user,
    )
    if internal_match:
        return PipelineResult(
            cif_string=internal_match.structure.to(fmt="cif"),
            source="existing_internal",
            notes=f"matched internal record {internal_match.record_id}",
            normalization_trace=normalization_trace,
            matched_record=internal_match,
            candidate_matches=internal_candidates,
            recognition_result=recognition,
        )
    if len(internal_candidates) > 1:
        return PipelineResult(
            cif_string=None,
            source="ambiguous_polymorphs",
            notes=f"found {len(internal_candidates)} internal candidates but none was selected",
            normalization_trace=normalization_trace,
            candidate_matches=internal_candidates,
            recognition_result=recognition,
        )

    log.info("Confirmed: not found in any external or internal database. Proceeding to generation.")

    # --- Stage 4a: undoped generation ---
    if not is_doped:
        result = generate_with_feedback_loop(
            search_key,  # use the normalized/reduced formula, not the raw input
            max_iterations=max_iterations,
            samples_per_iteration=samples_per_iteration,
            mace_model_path=mace_model_path,
            target_space_group=query.space_group,
            ask_user=ask_user,
        )
        result.normalization_trace = normalization_trace
        result.recognition_result = recognition
        return result

    # --- Stage 4b: doped generation ---
    if doping_spec is None:
        return PipelineResult(
            cif_string=None,
            source="failed",
            notes="compound classified as doped but DopingSpec could not be fully resolved "
                  "(missing host formula / site / dopant / fraction).",
            normalization_trace=normalization_trace,
            recognition_result=recognition,
        )

    parent_structure = resolve_parent_structure(
        doping_spec, mp_api_key, ask_user,
        max_iterations=max_iterations,
        samples_per_iteration=samples_per_iteration,
        mace_model_path=mace_model_path,
    )
    if parent_structure is None:
        return PipelineResult(
            cif_string=None,
            source="failed",
            notes=f"could not find or generate parent structure '{doping_spec.host_formula}'",
            normalization_trace=normalization_trace,
            recognition_result=recognition,
        )

    # QA §6.1 (regression guard): wrap apply_doping_with_validation in try/except
    # so that invalid host_site_species values or other ValueError from doping.py
    # are always converted to a clean PipelineResult(source="failed") instead of
    # raising an unhandled exception to the web framework.
    try:
        doped_structure, doped_validation = apply_doping_with_validation(parent_structure, doping_spec, ask_user)
    except ValueError as doping_err:
        log.error(f"Doping application failed: {doping_err}")
        return PipelineResult(
            cif_string=None,
            source="failed",
            notes=f"Doping could not be applied: {doping_err}",
            normalization_trace=normalization_trace,
            recognition_result=recognition,
        )
    except Exception as doping_err:
        log.error(f"Unexpected error in doping stage: {doping_err}")
        return PipelineResult(
            cif_string=None,
            source="failed",
            notes=f"Unexpected error applying doping: {type(doping_err).__name__}: {doping_err}",
            normalization_trace=normalization_trace,
            recognition_result=recognition,
        )

    if not doped_validation.is_valid:
        return PipelineResult(
            cif_string=doped_structure.to(fmt="cif"),
            source="generated_doped",
            notes=f"WARNING: doped-structure validation failed ({doped_validation.notes}). "
                  f"Returning the structure anyway for inspection - do not treat as final.",
            normalization_trace=normalization_trace,
            doped_validation=doped_validation,
            recognition_result=recognition,
            is_reliable=False,
            doping_spec=doping_spec,
        )

    relax_result = relax_with_mace(doped_structure, mace_model_path)

    if not relax_result.converged:
        return PipelineResult(
            cif_string=doped_structure.to(fmt="cif"),
            source="generated_doped",
            notes=f"WARNING: relaxation did not converge (max force {relax_result.max_force:.3f} eV/A). "
            f"Returning unrelaxed/partially-relaxed structure - inspect before use.",
            normalization_trace=normalization_trace,
            doped_validation=doped_validation,
            recognition_result=recognition,
            is_reliable=False,
            doping_spec=doping_spec,
        )

    is_skipped = getattr(relax_result, "relaxation_skipped", False)
    if is_skipped:
        notes_str = "doped structure validated; MACE relaxation was skipped (disordered occupancy preserved)"
        rel_status = "UNKNOWN (disordered solid solution preserved)"
        is_rel = False
    else:
        notes_str = f"doped structure validated and relaxed successfully (energy={relax_result.energy:.4f} eV)"
        rel_status = "CONVERGED" if relax_result.converged else "ACTIVE / UNCONVERGED"
        is_rel = relax_result.converged

    return PipelineResult(
        cif_string=relax_result.structure.to(fmt="cif"),
        source="generated_doped",
        notes=notes_str,
        normalization_trace=normalization_trace,
        doped_validation=doped_validation,
        recognition_result=recognition,
        is_reliable=is_rel,
        relaxation_skipped=is_skipped,
        relaxation_status=rel_status,
        final_space_group=getattr(relax_result, "final_space_group", None) or "P1",
        energy=getattr(relax_result, "energy", None) if not is_skipped else None,
        max_force=getattr(relax_result, "max_force", None) if not is_skipped else None,
        doping_spec=doping_spec,
    )
