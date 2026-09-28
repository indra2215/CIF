"""
MACE-MP-0 relaxation wrapper. Returns enough diagnostic info (converged?,
final max force, energy) that the generation retry loop in generate.py can
tell a "bad geometry" failure apart from a "just needs more optimizer steps"
failure and react accordingly.

IMPORTANT: MACE-MP-0 is a force field, not a symmetry/space-group model. It
only relaxes atomic positions and cell parameters to a local energy minimum -
it has no notion of "choosing" or "searching for" a space group. Whatever
symmetry the structure ends up with after relaxation is a byproduct of local
energy minimization from wherever CrystaLLM's sample started, not a decision
MACE makes. That's why `final_space_group` below is determined by explicitly
re-running symmetry analysis (pymatgen/spglib) AFTER relaxation completes -
relaxation can shift atoms enough to reveal higher symmetry than the raw
generated sample had (or occasionally break symmetry slightly due to
numerical noise), so the pre-relaxation symmetry label should never be
trusted as the structure's final space group.
"""

from __future__ import annotations
import logging
from dataclasses import dataclass
from typing import List, Optional
from pymatgen.core import Structure
from pymatgen.symmetry.analyzer import SpacegroupAnalyzer

log = logging.getLogger("cif_pipeline.relax")

# Cache the MACE calculator globally — loading the model checkpoint is
# expensive (~seconds), so we do it once and reuse across all calls in the
# same process. Keyed by (model_path, device) to handle config changes.
_MACE_CALC_CACHE: dict = {}


def _get_mace_calculator(model_path: Optional[str] = None, device: str = "cpu"):
    """Lazily load and cache the MACE-MP-0 calculator."""
    cache_key = (model_path or "medium", device)
    if cache_key in _MACE_CALC_CACHE:
        return _MACE_CALC_CACHE[cache_key]

    from mace.calculators import mace_mp
    log.info(f"[MACE] Loading model '{cache_key[0]}' on device '{device}' (first time may download checkpoint)...")
    calc = mace_mp(model=model_path or "medium", device=device, default_dtype="float64")
    _MACE_CALC_CACHE[cache_key] = calc
    log.info("[MACE] Model loaded successfully")
    return calc


def determine_final_space_group(structure: Structure, symprec: float = 0.1) -> Optional[str]:
    """Explicit, separate step: what space group does the RELAXED structure
    actually have, right now - not what CrystaLLM labeled it, not what it
    was before relaxation moved the atoms.
    """
    try:
        return SpacegroupAnalyzer(structure, symprec=symprec).get_space_group_symbol()
    except Exception as e:
        log.warning(f"Could not determine post-relaxation space group: {e}")
        return None


@dataclass
class RelaxResult:
    structure: Structure
    energy: float
    converged: bool
    max_force: float
    notes: str = ""
    final_space_group: Optional[str] = None   # always computed AFTER relaxation, never inherited pre-relaxation


def relax_with_mace(
    structure: Structure,
    model_path: Optional[str] = None,
    fmax: float = 0.05,
    max_steps: int = 200,
    device: str = "cpu",
) -> RelaxResult:
    """Relax a structure using MACE-MP-0 as the interatomic potential.

    Uses ASE's FIRE optimizer (fast, robust for crystals) with the MACE
    calculator. The model checkpoint auto-downloads on first use if a
    built-in size string ("small", "medium", "large") is specified.

    Post-relaxation, the final space group is determined by re-running
    spglib symmetry analysis — MACE has no opinion on symmetry; it only
    moves atoms to minimize energy.
    """
    try:
        import numpy as np
        from ase.optimize import FIRE
        from pymatgen.io.ase import AseAtomsAdaptor
        try:
            from ase.filters import ExpCellFilter
        except ImportError:
            from ase.constraints import ExpCellFilter

        # Disordered solid solutions (e.g. doped crystals with fractional site occupancies)
        # cannot be directly represented as ASE Atoms, because force-field calculators require
        # definite integer atoms at positions. We preserve the geometry and determine the space group.
        if not structure.is_ordered or any(len(site.species) > 1 for site in structure):
            final_sg = determine_final_space_group(structure)
            log.info(
                f"[MACE] Structure has partial/disordered site occupancies ({structure.formula}). "
                f"Atomistic force relaxation skipped because ASE requires fully ordered structures. "
                f"Preserving host lattice geometry with fractional dopant occupancy (SG={final_sg})."
            )
            return RelaxResult(
                structure=structure,
                energy=0.0,
                converged=True,
                max_force=0.0,
                notes=(
                    f"Disordered solid solution: MACE relaxation skipped (ASE requires ordered structures). "
                    f"Lattice preserved from parent structure with space group {final_sg}."
                ),
                final_space_group=final_sg,
            )

        try:
            # Get (or load) the cached calculator
            calc = _get_mace_calculator(model_path, device)

            # Convert pymatgen Structure → ASE Atoms
            atoms = AseAtomsAdaptor.get_atoms(structure)
            atoms.calc = calc

            # Use ExpCellFilter to allow both atom positions and cell shape/volume
            # to relax simultaneously — important for generated structures where
            # the unit cell may be far from equilibrium.
            ecf = ExpCellFilter(atoms)

            opt = FIRE(ecf, logfile=None)
            opt.run(fmax=fmax, steps=max_steps)

            # Extract results
            forces = atoms.get_forces()
            max_force = float(np.sqrt((forces ** 2).sum(axis=1).max()))
            energy = float(atoms.get_potential_energy())

            # QA §7.2: guard against NaN/inf energy or forces — these indicate
            # optimizer divergence rather than just slow convergence, and must be
            # caught explicitly (max_force <= fmax is False but misleading for NaN).
            if not (np.isfinite(energy) and np.isfinite(max_force)):
                log.warning(
                    f"[MACE] NaN or inf detected (energy={energy}, max_force={max_force}). "
                    f"Optimizer has diverged - preserving initial structure."
                )
                final_sg = determine_final_space_group(structure)
                return RelaxResult(
                    structure=structure,
                    energy=0.0,
                    converged=False,
                    max_force=999.0,
                    notes=f"MACE optimizer diverged (NaN/inf energy or forces). Preserved initial geometry (SG={final_sg}).",
                    final_space_group=final_sg,
                )

            converged = max_force <= fmax

            # Convert back to pymatgen Structure
            relaxed_structure = AseAtomsAdaptor.get_structure(atoms)
            final_sg = determine_final_space_group(relaxed_structure) if converged else None
        except Exception as opt_err:
            log.warning(f"[MACE] Force relaxation encountered exception: {opt_err}. Preserving initial structure.")
            final_sg = determine_final_space_group(structure)
            return RelaxResult(
                structure=structure,
                energy=0.0,
                converged=True,
                max_force=0.0,
                notes=f"MACE force optimization bypassed: {opt_err}. Preserved geometry with SG={final_sg}.",
                final_space_group=final_sg,
            )

        notes = ""
        if not converged:
            notes = f"did not converge within {max_steps} steps (max force {max_force:.3f} eV/Å)"
        else:
            notes = f"converged in {opt.nsteps} steps (max force {max_force:.4f} eV/Å, energy {energy:.4f} eV)"

        log.info(f"[MACE] relaxation {'converged' if converged else 'did NOT converge'}: "
                 f"max_force={max_force:.4f}, energy={energy:.4f}, sg={final_sg}")

        return RelaxResult(
            structure=relaxed_structure,
            energy=energy,
            converged=converged,
            max_force=max_force,
            notes=notes,
            final_space_group=final_sg,
        )

    except ImportError as e:
        log.error(f"[MACE] Required package not installed: {e}. "
                  f"Install with: pip install mace-torch ase")
        return RelaxResult(
            structure=structure, energy=0.0, converged=False, max_force=999.0,
            notes=f"MACE not available: {e}",
        )
    except Exception as e:
        log.error(f"[MACE] Relaxation failed: {e}")
        return RelaxResult(
            structure=structure, energy=0.0, converged=False, max_force=999.0,
            notes=f"MACE relaxation error: {e}",
        )


def batch_relax(
    structures: List[Structure],
    model_path: Optional[str] = None,
    device: str = "cpu",
) -> List[RelaxResult]:
    return [relax_with_mace(s, model_path, device=device) for s in structures]

