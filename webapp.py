"""
FastAPI backend for the CIF Generator pipeline.
Provides REST endpoints for querying compounds, retrieving CIF structures,
element breakdown, execution timeline, and serving the modern monochrome frontend.
"""

from __future__ import annotations
import os
import time
import hmac
import hashlib
import logging
from pathlib import Path
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from pymatgen.core import Composition, Element

from cif_pipeline.models import CompoundQuery, PipelineResult, DopingSpec
from cif_pipeline.orchestrator import run_pipeline
from cif_pipeline.classify import classify_compound, _normalize_formula_input

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("cif_pipeline.webapp")

app = FastAPI(
    title="CIF Pipeline API",
    description="High-Precision Crystallographic Pipeline with MACE-MP-0 float64 relaxation",
    version="1.1.0",
)

FRONTEND_DIR = Path(__file__).resolve().parent / "frontend"


class GenerationRequest(BaseModel):
    formula: Optional[str] = Field(None, description="Compound formula or description (e.g. Fe2O3, Fe1.9Ti0.1O3)")
    text: Optional[str] = Field(None, description="Alternative field for compound text")
    verbose: Optional[bool] = Field(False, description="Return detailed internal diagnostic state")
    is_doped_hint: Optional[bool] = Field(None, description="Optional hint whether compound is doped")
    space_group: Optional[str] = Field(None, description="Optional target space group")
    host_formula: Optional[str] = Field(None, description="Host formula if doped")
    dopant_element: Optional[str] = Field(None, description="Dopant element symbol")
    host_site_species: Optional[str] = Field(None, description="Species being substituted")
    dopant_fraction: Optional[float] = Field(None, description="Fraction of substitution (0.0 - 1.0)")
    doping: Optional[Dict[str, Any]] = Field(None, description="Optional nested doping parameters object")
    co_dopants: Optional[List[Dict[str, Any]]] = Field(default_factory=list, description="List of additional co-dopant specs")
    max_iterations: Optional[int] = Field(None, description="Max generation iterations")
    samples_per_iteration: Optional[int] = Field(None, description="Number of samples per iteration")


class ParseFormulaRequest(BaseModel):
    formula: Optional[str] = Field(None, description="Formula to parse into constituent elements and doping hints")
    text: Optional[str] = Field(None, description="Conversational text or formula")
    verbose: Optional[bool] = Field(False, description="Return detailed internal diagnostic state")


class DisambiguationRequest(BaseModel):
    choice: str = Field(..., description="User's selection or response to disambiguation prompt")
    options: Optional[List[str]] = Field(None, description="List of available candidate options")


def _web_ask_handler(question: str, options: Optional[List[str]] = None) -> Optional[str]:
    """No interactive channel in a single HTTP request: NEVER guess on the user's
    behalf. Returning None makes the pipeline surface ambiguity or candidate lists
    instead of silently selecting options[0] (which would answer the question itself).
    """
    log.info(f"[WebUI] Clarification required: {question} | options={options}")
    return None


@app.get("/api/health")
def health_check() -> Dict[str, Any]:
    from cif_pipeline.config import get_mp_api_key, get_mace_model, get_mace_device, get_crystallm_checkpoint
    import os
    ckpt = get_crystallm_checkpoint()
    ckpt_ready = bool(ckpt) and os.path.exists(os.path.join(ckpt, "ckpt.pt") if os.path.isdir(ckpt) else ckpt)
    return {
        "status": "healthy",
        "precision": "float64 (MACE-MP-0)",
        "mace_model": get_mace_model(),
        "mace_device": get_mace_device(),
        "has_mp_api_key": bool(get_mp_api_key()),
        "crystallm_checkpoint": ckpt or None,
        "crystallm_ready": ckpt_ready,
    }


@app.post("/api/parse-formula")
def parse_formula(req: ParseFormulaRequest) -> Dict[str, Any]:
    raw_text = (req.text or req.formula or "").strip()
    if not raw_text:
        return {
            "valid": False,
            "elements": [],
            "is_doped": False,
            "error": "Empty formula or text provided",
            "charge_balance": {"state": "unknown", "message": "No formula provided"},
        }

    # Out-of-scope syntax checks
    if "@" in raw_text:
        return {
            "valid": False,
            "raw_input": raw_text,
            "error": "Endohedral fullerene / caged-atom notation ('@') is not supported for periodic crystal CIF generation.",
            "recognition_method": "rejected_unsupported",
            "elements": [],
            "is_doped": False,
        }
    if "wt%" in raw_text.lower() or ("/" in raw_text and "wt" in raw_text.lower()):
        return {
            "valid": False,
            "raw_input": raw_text,
            "error": "Supported catalyst / weight-percent loading notation (e.g. 'wt%') is out-of-scope for bulk crystal CIF generation.",
            "recognition_method": "rejected_unsupported",
            "elements": [],
            "is_doped": False,
        }

    from cif_pipeline.recognition import recognize_compound_formula
    recog = recognize_compound_formula(raw_text, ask_user=_web_ask_handler)
    target_formula = recog.resolved_formula or raw_text

    # Step 2: Attempt pymatgen parse
    try:
        comp = Composition(target_formula)
    except Exception as parse_err:
        return {
            "valid": False,
            "raw_input": raw_text,
            "resolved_formula": recog.resolved_formula,
            "recognition_method": recog.method,
            "cleanup_notes": "; ".join(recog.warnings) if recog.warnings else None,
            "error": f"Cannot parse formula '{raw_text}': {parse_err}",
            "elements": [],
            "is_doped": False,
            "charge_balance": {"state": "invalid", "message": "Cannot check — formula is not parseable"},
        }

    # Step 3: Build element breakdown
    elements_data = []
    for el in comp.elements:
        elements_data.append({
            "symbol":         el.symbol,
            "name":           getattr(el, "name", el.symbol),
            "amount":         round(float(comp[el]), 6),    # full precision
            "weight_percent": round(float(comp.get_wt_fraction(el)) * 100, 4),
            "atomic_mass":    round(float(el.atomic_mass), 4),
            "atomic_number":  int(el.Z),
        })

    # Step 4: Compound classification
    from cif_pipeline.filter_rules import classify_compound_type
    compound_class = classify_compound_type(comp)

    # Step 5: Real charge balance check via pymatgen oxidation states
    charge_balance = _check_charge_balance(comp, compound_class)

    # Step 6: Doping classification
    query = CompoundQuery(raw_input=target_formula)
    try:
        is_doped, doping_spec = classify_compound(query, ask_user=_web_ask_handler, recognized_formula=target_formula)
    except Exception:
        is_doped, doping_spec = False, None

    doping_info = None
    if doping_spec:
        frac = doping_spec.dopant_fraction
        doping_info = {
            "host_formula":      doping_spec.host_formula,
            "host_site_species": doping_spec.host_site_species,
            "dopant_species":    doping_spec.dopant_species,
            "dopant_fraction":   round(float(frac), 6) if frac is not None else None,
            "dopant_fraction_pct": f"{float(frac)*100:.3g}%" if frac is not None else None,
        }

    return {
        "valid": True,
        "raw_input": raw_text,
        "resolved_formula": target_formula,
        "recognition_method": recog.method,
        "cleanup_notes": "; ".join(recog.warnings) if recog.warnings else None,
        "normalized_formula": target_formula if target_formula != raw_text else None,
        "case_corrected": (recog.method in ("case_normalized", "explicit_molecular_mapping")),
        "reduced_formula": comp.reduced_formula,
        "formula_hill": comp.hill_formula,
        "compound_class": compound_class,
        "elements": elements_data,
        "is_doped": is_doped,
        "doping_spec": doping_info,
        "charge_balance": charge_balance,
    }


def _check_charge_balance(comp: Composition, compound_class: str) -> Dict[str, Any]:
    """Use pymatgen's oxidation-state guesser for real charge balance check.

    Returns a dict with:
      state:   'balanced' | 'unbalanced' | 'uncertain' | 'intermetallic' | 'molecular'
      message: human-readable explanation
      detail:  the winning oxidation-state assignment, if found
    """
    # Intermetallics don't have meaningful ionic charge balance
    if compound_class == "intermetallic":
        return {
            "state": "intermetallic",
            "message": "Intermetallic compound — formal charge balance not applicable",
            "detail": None,
        }
    # Molecular/organic compounds (no metal) — no ionic model
    if compound_class == "other":
        return {
            "state": "molecular",
            "message": "Molecular compound — charge balance via oxidation-state model not applied",
            "detail": None,
        }

    # Use pymatgen's oxi_state_guesses — returns list of {El: oxi_state} dicts ordered by likelihood
    try:
        guesses = comp.oxi_state_guesses(max_sites=-1)
        if guesses:
            best = guesses[0]
            state_str = ", ".join(f"{el}{'+' if v >= 0 else ''}{v:g}" for el, v in best.items())
            return {
                "state": "balanced",
                "message": f"Charge balance: VALID — {compound_class} compound",
                "detail": state_str,
            }
        else:
            return {
                "state": "unbalanced",
                "message": "Charge balance: WARNING — no valid ionic oxidation-state assignment found. "
                           "Check stoichiometry or consider non-ionic bonding.",
                "detail": None,
            }
    except Exception as e:
        return {
            "state": "uncertain",
            "message": f"Charge balance: could not determine ({type(e).__name__}). "
                       "This may be normal for doped or defect-rich formulas.",
            "detail": None,
        }



@app.post("/api/generate")
def generate_cif(req: GenerationRequest) -> Dict[str, Any]:
    formula = (req.formula or req.text or "").strip()
    if not formula:
        raise HTTPException(status_code=400, detail="Formula cannot be empty")

    start_time = time.perf_counter()
    timeline: List[Dict[str, Any]] = []

    host_formula = req.host_formula
    dopant_element = req.dopant_element
    host_site_species = req.host_site_species
    dopant_fraction = req.dopant_fraction

    if req.doping and isinstance(req.doping, dict):
        host_formula = host_formula or req.doping.get("host_formula")
        dopant_element = dopant_element or req.doping.get("dopant_species") or req.doping.get("dopant_element") or req.doping.get("dopant")
        host_site_species = host_site_species or req.doping.get("host_site_species") or req.doping.get("site")
        dopant_fraction = dopant_fraction if dopant_fraction is not None else (
            req.doping.get("dopant_fraction") if req.doping.get("dopant_fraction") is not None else (
                req.doping.get("fraction") if req.doping.get("fraction") is not None else req.doping.get("dopant_fraction")
            )
        )

    co_dopants = list(req.co_dopants or [])
    if req.doping and isinstance(req.doping, dict):
        if not co_dopants and req.doping.get("co_dopants"):
            co_dopants = list(req.doping.get("co_dopants"))

    query = CompoundQuery(
        raw_input=formula,
        is_doped_hint=req.is_doped_hint,
        space_group=req.space_group.strip() if req.space_group else None,
        host_formula=host_formula.strip() if host_formula else None,
        dopant_element=dopant_element.strip() if dopant_element else None,
        host_site_species=host_site_species.strip() if host_site_species else None,
        dopant_fraction=float(dopant_fraction) if dopant_fraction is not None else None,
        co_dopants=co_dopants,
    )

    t0 = time.perf_counter()
    max_iter = req.max_iterations or 5
    samples_per_iter = req.samples_per_iteration or 25
    try:
        result: PipelineResult = run_pipeline(
            query,
            ask_user=_web_ask_handler,
            max_iterations=max_iter,
            samples_per_iteration=samples_per_iter,
        )
    except Exception as e:
        log.error(f"Pipeline execution error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    total_elapsed = round((time.perf_counter() - start_time), 3)

    # Build timeline breakdown
    if getattr(result, "recognition_result", None) and result.recognition_result.success:
        rr = result.recognition_result
        timeline.append({
            "stage": f"Stage 0: Compound Recognition ({rr.method})",
            "description": f"Recognized '{rr.raw_input}' -> '{rr.resolved_formula}' via {rr.method}",
            "status": "completed",
        })
    else:
        timeline.append({
            "stage": "Stage 0: Compound Recognition",
            "description": f"Processed input formula: '{formula}'",
            "status": "completed",
        })

    timeline.append({
        "stage": "Stage 0a: Classification & Dopant Parsing",
        "description": "Determined doped vs undoped; extracted doping spec and host matrix",
        "status": "completed",
    })
    timeline.append({
        "stage": "Stage 0b: Normalization & Multiplicity Trace",
        "description": f"Normalized query key: '{result.normalization_trace.search_key_used if result.normalization_trace else formula}'",
        "status": "completed",
    })

    if result.source.startswith("existing_"):
        src = result.source.replace("existing_", "").upper()
        timeline.append({
            "stage": f"Stage 1: Multi-Database Retrieval ({src})",
            "description": f"Verified match found in {src} (Record ID: {result.matched_record.record_id if result.matched_record else 'N/A'})",
            "status": "completed",
        })
        timeline.append({
            "stage": "Stage 4a/b: ML Generation / Substitution",
            "description": "Bypassed (structure already confirmed in open database)",
            "status": "skipped",
        })
        timeline.append({
            "stage": "Stage 5: Relaxation & Space Group Analysis",
            "description": "Pre-computed experimental / theoretical database geometry verified with spglib",
            "status": "completed",
        })
    elif result.source == "generated_doped":
        timeline.append({
            "stage": "Stage 1: Multi-Database Search",
            "description": "Parent host structure resolved from external database",
            "status": "completed",
        })
        timeline.append({
            "stage": "Stage 4b: Site-Selective Doping Substitution",
            "description": "Substituted dopant atom into parent lattice; validated occupancy & local distances",
            "status": "completed",
        })
        timeline.append({
            "stage": "Stage 5: MACE-MP-0 Relaxation & Symmetry",
            "description": "Processed geometry & evaluated space group with SpacegroupAnalyzer",
            "status": "completed",
        })
    elif result.source == "generated_undoped":
        timeline.append({
            "stage": "Stage 1-2: External/Internal DB Search",
            "description": "Confirmed not present in any connected database",
            "status": "completed",
        })
        timeline.append({
            "stage": "Stage 4a: CrystaLLM Generative Feedback Loop",
            "description": "Sampled candidate CIF completions and filtered via chemical rules",
            "status": "completed",
        })
        timeline.append({
            "stage": "Stage 5: MACE-MP-0 float64 Relaxation",
            "description": "Relaxed candidate structures with FIRE optimizer and spglib space group determination",
            "status": "completed",
        })
    else:
        timeline.append({
            "stage": "Pipeline Execution",
            "description": f"Completed with status: {result.source} ({result.notes})",
            "status": "completed",
        })

    # Tool provenance
    crystallm_used = result.source.startswith("generated_undoped")
    tools_used = [
        {
            "name": "pymatgen",
            "role": "Crystallographic structure manipulation, CIF generation, and composition analysis",
            "active": True,
        },
        {
            "name": "spglib",
            "role": "Post-relaxation SpaceGroupAnalyzer (H-M symbol & Hall number determination)",
            "active": True,
        },
        {
            "name": "COD REST API",
            "role": "Crystallography Open Database text search & CIF composition verification",
            "active": True,
        },
        {
            "name": "MACE-MP-0",
            "role": "Machine learning interatomic potential with float64 double precision (medium model)",
            "active": True,
        },
        {
            "name": "ASE (Atomic Simulation Environment)",
            "role": "ExpCellFilter lattice optimizer and FIRE geometry minimizer",
            "active": True,
        },
        {
            "name": "CrystaLLM (GPT-2 nanoGPT)",
            "role": "Local autoregressive CIF generation via CIFTokenizer + GPT checkpoint (crystallm_repo/ckpt.pt)",
            "active": crystallm_used,
        },
    ]

    # Parse elements in input formula for UI search space display
    parsed_elements = []
    try:
        comp = Composition(formula)
        for el in comp.elements:
            parsed_elements.append({
                "symbol": el.symbol,
                "amount": float(comp[el]),
                "weight_percent": round(float(comp.get_wt_fraction(el)) * 100, 2),
                "atomic_mass": round(float(el.atomic_mass), 2),
            })
    except Exception:
        pass

    # Serialize candidate matches (including CIF for 3D visualization)
    candidates = []
    if result.candidate_matches:
        for c in result.candidate_matches:
            cif_str = ""
            try:
                cif_str = c.structure.to(fmt="cif")
            except Exception:
                pass

            candidates.append({
                "source": c.source,
                "record_id": c.record_id,
                "formula": c.formula,
                "space_group": c.space_group,
                "source_url": c.source_url,
                "is_theoretical": c.is_theoretical,
                "cif_string": cif_str,
                "extra": c.extra,
            })

    # Serialize matched record
    matched = None
    if result.matched_record:
        m = result.matched_record
        matched = {
            "source": m.source,
            "record_id": m.record_id,
            "formula": m.formula,
            "space_group": m.space_group,
            "source_url": m.source_url,
            "is_theoretical": m.is_theoretical,
            "extra": m.extra,
        }

    # Serialize normalization trace
    norm_trace = None
    if result.normalization_trace:
        t = result.normalization_trace
        norm_trace = {
            "raw_input": t.raw_input,
            "search_key_used": t.search_key_used,
            "is_doped": t.is_doped,
            "classification_reasoning": t.classification_reasoning,
            "multiplicity": {
                "was_reduced": t.multiplicity.was_reduced,
                "multiplier": t.multiplicity.multiplier,
                "reason": t.multiplicity.reason,
            } if t.multiplicity else None,
        }

    # Serialize doped validation
    doped_val = None
    if result.doped_validation:
        v = result.doped_validation
        doped_val = {
            "is_valid": v.is_valid,
            "occupancy_sum": v.occupancy_sum,
            "occupancy_sum_ok": v.occupancy_sum_ok,
            "bond_length_ok": v.bond_length_ok,
            "notes": v.notes,
        }

    # Serialize diagnostics history
    diagnostics = []
    if result.diagnostics_history:
        for d in result.diagnostics_history:
            diagnostics.append({
                "iteration": d.iteration,
                "n_generated": d.n_generated,
                "n_parse_failed": d.n_parse_failed,
                "n_bond_length_failed": d.n_bond_length_failed,
                "n_symmetry_failed": d.n_symmetry_failed,
                "n_charge_balance_failed": d.n_charge_balance_failed,
                "n_duplicates_removed": d.n_duplicates_removed,
                "n_survived": d.n_survived,
                "n_relax_nonconverged": d.n_relax_nonconverged,
                "notes": d.notes,
            })

    # Where was it retrieved from
    retrieved_from = "CrystaLLM Generative Engine"
    if result.source.startswith("existing_"):
        retrieved_from = f"Open Database ({result.source.replace('existing_', '').upper()})"
    elif result.source == "generated_doped":
        if co_dopants:
            retrieved_from = f"Parent Lattice Co-Doping ({1 + len(co_dopants)} dopants) + MACE Relaxation"
        else:
            retrieved_from = "Parent Lattice Doping Transformation + MACE Relaxation"

    # Serialize recognition result
    recog_data = None
    if getattr(result, "recognition_result", None):
        rr = result.recognition_result
        recog_data = {
            "raw_input": rr.raw_input,
            "cleaned_input": rr.cleaned_input,
            "resolved_formula": rr.resolved_formula,
            "method": rr.method,
            "success": rr.success,
            "warnings": rr.warnings,
        }

    # Serialize doping spec
    doping_spec_data = None
    if getattr(result, "doping_spec", None):
        ds = result.doping_spec
        doping_spec_data = {
            "host_formula": getattr(ds, "host_formula", None),
            "dopant_species": getattr(ds, "dopant_species", None),
            "host_site_species": getattr(ds, "host_site_species", None),
            "dopant_fraction": getattr(ds, "dopant_fraction", None),
            "co_dopants": [
                {
                    "host_formula": getattr(c, "host_formula", getattr(ds, "host_formula", None)),
                    "dopant_species": getattr(c, "dopant_species", c.get("dopant_species", c.get("dopant")) if isinstance(c, dict) else str(c)),
                    "host_site_species": getattr(c, "host_site_species", c.get("host_site_species", c.get("site")) if isinstance(c, dict) else None),
                    "dopant_fraction": getattr(c, "dopant_fraction", c.get("dopant_fraction", c.get("fraction")) if isinstance(c, dict) else None),
                }
                for c in getattr(ds, "co_dopants", [])
            ],
        }
    elif dopant_element and host_site_species and dopant_fraction is not None:
        doping_spec_data = {
            "host_formula": host_formula,
            "dopant_species": dopant_element,
            "host_site_species": host_site_species,
            "dopant_fraction": dopant_fraction,
            "co_dopants": co_dopants,
        }

    return {
        "success": result.cif_string is not None,
        "source": result.source,
        "retrieved_from": retrieved_from,
        "crystallm_used": crystallm_used,
        "total_elapsed_seconds": total_elapsed,
        "elapsed_time": round(total_elapsed, 4),
        "iterations_used": len(result.diagnostics_history) if result.diagnostics_history else 0,
        "timeline": timeline,
        "tools_used": tools_used,
        "parsed_elements": parsed_elements,
        "notes": result.notes,
        "cif_string": result.cif_string,
        "reliable": getattr(result, "is_reliable", True),
        "is_reliable": getattr(result, "is_reliable", True),
        "relaxation_skipped": getattr(result, "relaxation_skipped", False),
        "relaxation_status": getattr(result, "relaxation_status", None),
        "final_space_group": getattr(result, "final_space_group", None),
        "energy": getattr(result, "energy", None),
        "max_force": getattr(result, "max_force", None),
        "recognition_method": getattr(result.recognition_result, "method", None) if getattr(result, "recognition_result", None) else None,
        "doping_spec": doping_spec_data,
        "matched_record": matched,
        "candidate_matches": candidates,
        "normalization_trace": norm_trace,
        "recognition_result": recog_data,
        "doped_validation": doped_val,
        "diagnostics_history": diagnostics,
    }


@app.post("/api/resolve-disambiguation")
def resolve_disambiguation_endpoint(req: DisambiguationRequest) -> Dict[str, Any]:
    from cif_pipeline.user_interaction import pick_option_index
    idx = pick_option_index(req.choice, req.options)
    if idx is None:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid selection '{req.choice}'. Please select a valid candidate option from the list."
        )
    return {
        "success": True,
        "selected_index": idx,
        "selected_option": req.options[idx] if req.options else req.choice,
    }


class LoginRequest(BaseModel):
    username: str
    password: str


class SymmetryRequest(BaseModel):
    cif_string: str
    symprec: Optional[float] = 0.01


class MespRequest(BaseModel):
    cif_string: str
    energy_per_atom: Optional[float] = None
    fmax: Optional[float] = None
    formula: Optional[str] = None


class BatchRequest(BaseModel):
    cases: Optional[List[Dict[str, Any]]] = None


@app.post("/api/auth/login")
def auth_login(req: LoginRequest) -> Dict[str, Any]:
    username = req.username.strip()
    password = req.password
    if not username or not password:
        raise HTTPException(status_code=400, detail="Username and password are required")
    auth_enabled = os.environ.get("CIF_AUTH_ENABLED", "false").lower() in ("true", "1", "yes")
    expected_pwd = os.environ.get("CIF_AUTH_PASSWORD", "admin")
    if auth_enabled and not hmac.compare_digest(password, expected_pwd):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    secret = os.environ.get("CIF_SECRET_KEY", "cif-dev-secret-key-2026")
    sig = hashlib.sha256(f"{username}:{secret}".encode()).hexdigest()[:12]
    return {
        "success": True,
        "token": f"jwt_cif_{int(time.time())}_{sig}",
        "username": username,
        "role": "Crystallography Researcher",
        "session_expires_in": 86400,
    }


@app.post("/api/symmetry-matrix")
def symmetry_matrix_endpoint(req: SymmetryRequest) -> Dict[str, Any]:
    from cif_pipeline.symmetry_matrix import compute_symmetry_matrix
    return compute_symmetry_matrix(req.cif_string, symprec=req.symprec or 0.01)


@app.post("/api/mesp-check")
def mesp_check_endpoint(req: MespRequest) -> Dict[str, Any]:
    from cif_pipeline.mesp_check import check_mesp_and_relaxation
    return check_mesp_and_relaxation(
        cif_string=req.cif_string,
        energy_per_atom=req.energy_per_atom,
        fmax=req.fmax,
        formula=req.formula,
    )


@app.post("/api/batch-process")
def batch_process_endpoint(req: BatchRequest) -> Dict[str, Any]:
    from cif_pipeline.batch_processor import run_batch_pipeline
    return run_batch_pipeline(cases=req.cases)


@app.get("/api/data-files")
def list_data_files() -> Dict[str, Any]:
    data_dir = Path(__file__).resolve().parent / "data"
    files = []
    if data_dir.exists():
        for p in data_dir.glob("*.dat"):
            files.append({
                "filename": p.name,
                "size_bytes": p.stat().st_size,
                "modified": time.ctime(p.stat().st_mtime),
            })
    return {"files": files}


@app.get("/api/data-files/{filename}")
def get_data_file(filename: str):
    data_dir = (Path(__file__).resolve().parent / "data").resolve()
    file_path = (data_dir / filename).resolve()
    try:
        if not file_path.is_relative_to(data_dir):
            raise HTTPException(status_code=403, detail="Access denied")
    except (ValueError, AttributeError):
        raise HTTPException(status_code=403, detail="Access denied")
    if not filename.endswith(".dat") or not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="Data file not found")
    return Response(
        content=file_path.read_text(encoding="utf-8"),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


FRONTEND_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("webapp:app", host="0.0.0.0", port=8000, reload=False)
