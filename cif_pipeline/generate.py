"""
Stage 4a (also used as the parent-resolution generator in stage 4b): CrystaLLM
candidate generation, driven as a feedback loop rather than a single shot.

Each iteration:
  1. Generate N candidate CIFs with CrystaLLM (local model via crystallm package)
  2. Filter them with compound-type-aware pymatgen rules (filter_rules.py)
  3. Relax survivors with MACE
  4. If at least one relaxation converges -> done, return the best (lowest energy)
  5. If not -> inspect *why* nothing survived (failure breakdown from filtering,
     or non-convergence from relaxation) and adjust generation parameters
     before the next iteration:
       - mostly parse failures            -> something structurally wrong with
                                              the prompt/tokens; try again as-is
                                              but flag for manual review after
                                              max_iterations
       - mostly bond_length failures      -> increase temperature/diversity so
                                              the model explores different
                                              geometries instead of repeating
                                              the same bad one
       - mostly symmetry failures         -> try constraining to a target space
                                              group if CrystaLLM's interface
                                              supports a symmetry-conditioned
                                              prompt
       - mostly duplicate failures        -> increase temperature (candidates
                                              are all collapsing to one basin)
       - relax non-convergence            -> increase MACE max_steps, or relax
                                              fmax tolerance slightly
   Stops after `max_iterations` and reports full diagnostics either way.

CrystaLLM Integration Notes:
  - Uses the locally cloned CrystaLLM package (crystallm_repo/) installed via
    `pip install -e crystallm_repo`.
  - Checkpoint must be a directory containing a `ckpt.pt` file trained with
    the CrystaLLM nanoGPT-style training procedure.
  - Set CRYSTALLM_CHECKPOINT_PATH in .env to point to that directory.
  - Prompt format: "data_{formula}\n" where formula is sorted by
    electronegativity (using pymatgen's Composition.formula).
  - With a target space group: "data_{formula}\n{atomic_props_block}\n
    _symmetry_space_group_name_H-M {sg}\n"
"""

from __future__ import annotations
import logging
import os
import re
from contextlib import nullcontext
from typing import List, Optional

from .models import GenerationDiagnostics, PipelineResult
from .filter_rules import filter_candidates, classify_compound_type
from .relax import batch_relax
from .user_interaction import AskUserFn, default_cli_ask

log = logging.getLogger("cif_pipeline.generate")

# ---------------------------------------------------------------------------
# CrystaLLM model cache — loaded once on first call, reused across iterations.
# ---------------------------------------------------------------------------
_CRYSTALLM_MODEL = None
_CRYSTALLM_TOKENIZER = None
_CRYSTALLM_DEVICE = "cpu"


def _ensure_crystallm_loaded(checkpoint_path: str, device: str = "cpu"):
    """Lazily load the CrystaLLM model and tokenizer from the given checkpoint
    directory (must contain ckpt.pt).

    Uses the real crystallm package (CIFTokenizer, GPT, GPTConfig) installed
    from the locally cloned CrystaLLM repository.
    """
    global _CRYSTALLM_MODEL, _CRYSTALLM_TOKENIZER, _CRYSTALLM_DEVICE

    if _CRYSTALLM_MODEL is not None:
        return

    if not checkpoint_path:
        raise RuntimeError(
            "No CrystaLLM checkpoint path configured. "
            "Set CRYSTALLM_CHECKPOINT_PATH in your .env file to the directory "
            "containing the ckpt.pt file."
        )

    # Resolve the directory containing ckpt.pt
    if os.path.isfile(checkpoint_path):
        checkpoint_dir = os.path.dirname(checkpoint_path)
        ckpt_pt = checkpoint_path
    else:
        checkpoint_dir = checkpoint_path
        ckpt_pt = os.path.join(checkpoint_dir, "ckpt.pt")

    if not os.path.exists(ckpt_pt):
        raise FileNotFoundError(
            f"CrystaLLM checkpoint file not found: {ckpt_pt}\n"
            f"Download a pre-trained checkpoint from the CrystaLLM repository "
            f"(https://github.com/lantunes/CrystaLLM) and extract it to "
            f"'{checkpoint_dir}'."
        )

    try:
        import torch
        from crystallm import CIFTokenizer, GPT, GPTConfig

        log.info(f"[CrystaLLM] Loading checkpoint from '{ckpt_pt}' on device={device}...")

        checkpoint = torch.load(ckpt_pt, map_location=device, mmap=True, weights_only=False)
        gptconf = GPTConfig(**checkpoint["model_args"])

        with torch.device("meta"):
            meta_model = GPT(gptconf)

        state_dict = checkpoint["model"]
        # Strip compile wrapper prefix if present (added by torch.compile)
        unwanted_prefix = "_orig_mod."
        for k in list(state_dict.keys()):
            if k.startswith(unwanted_prefix):
                state_dict[k[len(unwanted_prefix):]] = state_dict.pop(k)

        model = meta_model.to_empty(device=device)
        model.load_state_dict(state_dict, assign=True)
        model.eval()

        _CRYSTALLM_MODEL = model
        _CRYSTALLM_TOKENIZER = CIFTokenizer()
        _CRYSTALLM_DEVICE = device
        log.info(
            f"[CrystaLLM] Model loaded successfully "
            f"(layers={gptconf.n_layer}, heads={gptconf.n_head}, "
            f"embd={gptconf.n_embd}, vocab={gptconf.vocab_size})"
        )

    except ImportError as e:
        raise RuntimeError(
            f"CrystaLLM package not importable: {e}\n"
            f"Run: pip install -e crystallm_repo   (from the project root)"
        ) from e
    except Exception as e:
        _CRYSTALLM_MODEL = None
        _CRYSTALLM_TOKENIZER = None
        raise RuntimeError(f"Failed to load CrystaLLM: {e}") from e


def _build_crystallm_prompt(formula: str, target_space_group: Optional[str] = None) -> str:
    """Build the CIF prompt string that CrystaLLM will complete.

    Follows the exact same format used in the official CrystaLLM
    make_prompt_file.py:
      - Without space group:  "data_{comp_str}\n"
      - With space group:     "data_{comp_str}\n{atomic_props_block}\n
                               _symmetry_space_group_name_H-M {sg}\n"

    The formula is sorted by electronegativity via pymatgen Composition.formula
    (e.g., "Fe2O3" → "Fe2O3"; "TiO2" → "Ti1O2"), which is what the model
    saw during training.
    """
    try:
        from pymatgen.core import Composition
        comp = Composition(formula)
        # comp.formula sorts by electronegativity and looks like "Fe2 O3"
        comp_str = comp.formula.replace(" ", "")
    except Exception:
        # Fallback: use the formula as-is
        comp_str = formula.replace(" ", "")

    if target_space_group:
        try:
            from crystallm import get_atomic_props_block_for_formula
            block = get_atomic_props_block_for_formula(comp_str)
            cif_str = f"data_{comp_str}\n{block}\n_symmetry_space_group_name_H-M {target_space_group}\n"
            # Strip leading/trailing spaces per CrystaLLM convention
            cif_str = re.sub(r"^[ \t]+|[ \t]+$", "", cif_str, flags=re.MULTILINE)
            return cif_str
        except Exception as e:
            log.warning(
                f"[CrystaLLM] Could not build space-group-conditioned prompt "
                f"(get_atomic_props_block_for_formula failed: {e}). "
                f"Falling back to plain formula prompt."
            )
            return f"data_{comp_str}\n"
    else:
        return f"data_{comp_str}\n"


def _call_crystallm(formula: str, n_samples: int, temperature: float,
                    target_space_group: Optional[str] = None,
                    top_k: int = 10,
                    max_new_tokens: int = 3000) -> List[str]:
    """Generate candidate CIF blocks using the local CrystaLLM model.

    Returns a list of raw CIF strings. Returns an empty list and logs a
    warning if no checkpoint is configured or the model fails to load.
    """
    from .config import get_crystallm_checkpoint, get_mace_device

    checkpoint_path = get_crystallm_checkpoint()
    if not checkpoint_path:
        log.warning(
            f"[CrystaLLM] No checkpoint configured — cannot generate candidates "
            f"for '{formula}'. Set CRYSTALLM_CHECKPOINT_PATH in .env to the "
            f"directory containing ckpt.pt."
        )
        return []

    # Use the same device as MACE (or override via env)
    device = get_mace_device() or "cpu"

    try:
        _ensure_crystallm_loaded(checkpoint_path, device)
    except Exception as e:
        log.error(f"[CrystaLLM] Model loading failed: {e}")
        return []

    try:
        import torch

        prompt = _build_crystallm_prompt(formula, target_space_group)
        log.info(
            f"[CrystaLLM] Generating {n_samples} candidates for '{formula}' "
            f"(temperature={temperature:.3f}, top_k={top_k}, "
            f"max_new_tokens={max_new_tokens}, "
            f"target_sg={target_space_group})"
        )
        log.debug(f"[CrystaLLM] Prompt: {repr(prompt)}")

        tokenizer = _CRYSTALLM_TOKENIZER
        model = _CRYSTALLM_MODEL

        # Tokenize the prompt
        start_tokens = tokenizer.tokenize_cif(prompt)
        start_ids = tokenizer.encode(start_tokens)
        x = torch.tensor(start_ids, dtype=torch.long, device=_CRYSTALLM_DEVICE)[None, ...]

        # Context manager for autocast on CUDA
        device_type = "cuda" if "cuda" in _CRYSTALLM_DEVICE else "cpu"
        ctx = nullcontext() if device_type == "cpu" else torch.amp.autocast(
            device_type=device_type, dtype=torch.bfloat16
        )

        generated_cifs = []
        with torch.no_grad():
            with ctx:
                for i in range(n_samples):
                    try:
                        y = model.generate(
                            x,
                            max_new_tokens=max_new_tokens,
                            temperature=temperature,
                            top_k=top_k,
                        )
                        output = tokenizer.decode(y[0].tolist())
                        # The full output starts from the prompt — extract CIF block
                        if "data_" in output:
                            cif_block = output[output.index("data_"):]
                            generated_cifs.append(cif_block)
                        log.debug(f"[CrystaLLM] Sample {i+1}/{n_samples}: generated {len(output)} chars")
                    except Exception as gen_err:
                        log.debug(f"[CrystaLLM] Sample {i+1} generation error: {gen_err}")
                        continue

        log.info(f"[CrystaLLM] Generated {len(generated_cifs)} raw CIF block(s) for '{formula}'")
        return generated_cifs

    except Exception as e:
        log.error(f"[CrystaLLM] Generation pipeline failed: {e}")
        return []


# ---------------------------------------------------------------------------
# Feedback-loop parameter adjustment
# ---------------------------------------------------------------------------

def _decide_next_adjustment(failures: dict, relax_nonconverged: int, current_temperature: float):
    """Look at the failure breakdown from the last iteration and decide how to
    adjust generation parameters for the next one. Returns (new_temperature, notes).
    """
    counts = {k: len(v) for k, v in failures.items()}
    dominant = max(counts, key=counts.get) if any(counts.values()) else None

    if dominant == "bond_length" or dominant == "duplicate":
        new_temp = min(current_temperature + 0.15, 1.5)
        return new_temp, f"raising temperature to {new_temp:.2f} (dominant failure: {dominant})"
    if dominant == "symmetry":
        return current_temperature, "dominant failure: symmetry - consider space-group-conditioned prompting"
    if dominant == "charge_balance":
        return current_temperature, "dominant failure: charge balance - candidates chemically implausible, keeping temperature"
    if relax_nonconverged > 0 and dominant is None:
        return current_temperature, "candidates passed filtering but relaxation did not converge - consider raising MACE max_steps"
    return current_temperature, "no dominant failure mode identified"


# ---------------------------------------------------------------------------
# Main generation entry point
# ---------------------------------------------------------------------------

def generate_with_feedback_loop(
    formula: str,
    max_iterations: int = 5,
    samples_per_iteration: int = 25,
    mace_model_path: Optional[str] = None,
    target_space_group: Optional[str] = None,
    ask_user: AskUserFn = default_cli_ask,
) -> PipelineResult:
    """NOTE on space groups: CrystaLLM decides the space group of each raw
    candidate (implicitly, via whatever geometry it samples) - MACE never
    does. MACE only relaxes atoms/cell to a local energy minimum. The actual,
    final space group of each candidate is determined HERE, after relaxation
    (relax.determine_final_space_group), never trusted from pre-relaxation
    labeling, since relaxation can shift atoms enough to change the effective
    symmetry.

    If `target_space_group` is given, only post-relaxation candidates that
    actually match it are accepted - candidates in other space groups don't
    silently substitute for it, they just don't count this iteration.

    If no target space group is given and converged candidates end up split
    across multiple distinct final space groups, this follows the same
    "surface everything, never silently narrow" policy used for database
    polymorph disambiguation: all distinct space groups found are presented
    (via `ask_user`) rather than silently returning whichever has the lowest
    energy across different symmetries.
    """

    compound_type = classify_compound_type(formula)
    log.info(f"Classified '{formula}' as compound_type='{compound_type}'")

    temperature = 0.7
    diagnostics_history: List[GenerationDiagnostics] = []

    for iteration in range(1, max_iterations + 1):
        raw_cifs = _call_crystallm(
            formula, samples_per_iteration, temperature, target_space_group
        )

        valid_structures, failures = filter_candidates(raw_cifs, compound_type)

        diag = GenerationDiagnostics(
            iteration=iteration,
            n_generated=len(raw_cifs),
            n_parse_failed=len(failures["parse"]),
            n_bond_length_failed=len(failures["bond_length"]),
            n_symmetry_failed=len(failures["symmetry"]),
            n_charge_balance_failed=len(failures["charge_balance"]),
            n_duplicates_removed=len(failures["duplicate"]),
            n_survived=len(valid_structures),
            failure_examples={k: v[:3] for k, v in failures.items() if v},
        )

        if not valid_structures:
            new_temp, notes = _decide_next_adjustment(failures, 0, temperature)
            diag.notes = f"no candidates survived filtering. {notes}"
            diagnostics_history.append(diag)
            log.info(f"[iter {iteration}] {diag.notes}")
            temperature = new_temp
            continue

        relax_results = batch_relax(valid_structures, mace_model_path)
        converged = [r for r in relax_results if r.converged]
        diag.n_relax_nonconverged = len(relax_results) - len(converged)

        if not converged:
            new_temp, notes = _decide_next_adjustment(failures, diag.n_relax_nonconverged, temperature)
            diag.notes = f"candidates passed filtering but none relaxed to convergence. {notes}"
            diagnostics_history.append(diag)
            log.info(f"[iter {iteration}] {diag.notes}")
            temperature = new_temp
            continue

        # Space group is determined from the RELAXED geometry - this is a
        # property of the atoms after MACE has moved them, not a choice MACE
        # made. `final_space_group` is already populated by relax_with_mace()
        # via relax.determine_final_space_group().
        distinct_groups = {r.final_space_group for r in converged if r.final_space_group}

        if target_space_group:
            matching = [r for r in converged if r.final_space_group == target_space_group]
            if not matching:
                found = sorted(g for g in distinct_groups if g)
                new_temp, notes = _decide_next_adjustment(failures, diag.n_relax_nonconverged, temperature)
                diag.notes = (
                    f"{len(converged)} candidate(s) converged but none relaxed into the requested "
                    f"space group '{target_space_group}' (found instead: {found or 'none determinable'}). {notes}"
                )
                diagnostics_history.append(diag)
                log.info(f"[iter {iteration}] {diag.notes}")
                temperature = new_temp
                continue
            converged = matching
            distinct_groups = {target_space_group}

        best = min(converged, key=lambda r: r.energy)

        # No target given, and candidates converged into genuinely different
        # space groups -> don't silently pick lowest-energy-across-symmetries.
        if not target_space_group and len(distinct_groups) > 1:
            by_group: dict = {}
            for r in converged:
                by_group.setdefault(r.final_space_group or "unknown", []).append(r)
            ranked_groups = sorted(by_group.items(), key=lambda kv: min(x.energy for x in kv[1]))

            options = [
                f"Space group {g}: {len(rs)} candidate(s), best energy {min(x.energy for x in rs):.4f} eV"
                for g, rs in ranked_groups
            ]
            options.append(
                f"Just use the lowest-energy candidate overall "
                f"(space group {best.final_space_group}, {best.energy:.4f} eV)"
            )
            choice = ask_user(
                f"Generation converged on {len(distinct_groups)} different space groups for "
                f"'{formula}' (no target space group was specified, so CrystaLLM was free to "
                f"sample any of them). Which should be used?",
                options,
            )
            if choice and choice != options[-1]:
                chosen_group, chosen_results = ranked_groups[options.index(choice)]
                best = min(chosen_results, key=lambda r: r.energy)

        diag.notes = (
            f"succeeded with {len(converged)} converged candidate(s) across "
            f"{len(distinct_groups)} distinct final space group(s); selected "
            f"space group {best.final_space_group}, energy {best.energy:.4f} eV"
        )
        diagnostics_history.append(diag)
        log.info(f"[iter {iteration}] {diag.notes}")

        alt_groups = sorted(g for g in distinct_groups if g and g != best.final_space_group)
        return PipelineResult(
            cif_string=best.structure.to(fmt="cif"),
            source="generated_undoped",
            notes=(
                f"converged after {iteration} iteration(s); final space group "
                f"{best.final_space_group} (determined post-relaxation, not chosen by MACE)"
                + (f"; other space groups also found among converged candidates: {alt_groups}" if alt_groups else "")
            ),
            diagnostics_history=diagnostics_history,
        )

    return PipelineResult(
        cif_string=None,
        source="failed",
        notes=f"no converged candidate found after {max_iterations} iterations - see diagnostics_history",
        diagnostics_history=diagnostics_history,
    )
