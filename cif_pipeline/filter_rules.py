"""
Compound-type-aware validity checks for generated candidate structures.

A single hardcoded "bond must be > 0.5 A" rule is too crude: reasonable
minimum M-O distances in an oxide are very different from reasonable M-M
distances in an intermetallic. This module first classifies the compound
type from its composition, then applies a rule set appropriate to that type.
"""

from __future__ import annotations
from typing import Tuple, List
from pymatgen.core import Structure, Composition, Element
from pymatgen.analysis.structure_matcher import StructureMatcher
from pymatgen.symmetry.analyzer import SpacegroupAnalyzer

from .models import DopedStructureValidation

ANION_GROUPS = {
    "oxide": {"O"},
    "sulfide": {"S"},
    "selenide": {"Se"},
    "nitride": {"N"},
    "halide": {"F", "Cl", "Br", "I"},
    "phosphide": {"P"},
    "carbide": {"C"},
}


def classify_compound_type(formula_or_comp) -> str:
    comp = formula_or_comp if isinstance(formula_or_comp, Composition) else Composition(formula_or_comp)
    elements = {str(el) for el in comp.elements}
    for name, anions in ANION_GROUPS.items():
        if elements & anions:
            return name
    # no classic anion present -> likely a metal/intermetallic alloy
    if all(Element(str(el)).is_metal for el in comp.elements):
        return "intermetallic"
    return "other"


# tolerance multiplier applied to (sum of covalent radii) to get a MINIMUM
# acceptable bond distance, and a multiplier for a generous MAXIMUM nearest-
# neighbour distance (catches wildly expanded/garbage cells), per compound type.
BOND_LENGTH_TOLERANCES = {
    "oxide":        {"min_mult": 0.65, "max_mult": 2.2},
    "sulfide":      {"min_mult": 0.65, "max_mult": 2.3},
    "selenide":     {"min_mult": 0.65, "max_mult": 2.3},
    "nitride":      {"min_mult": 0.65, "max_mult": 2.2},
    "halide":       {"min_mult": 0.60, "max_mult": 2.4},
    "phosphide":    {"min_mult": 0.65, "max_mult": 2.3},
    "carbide":      {"min_mult": 0.65, "max_mult": 2.2},
    "intermetallic":{"min_mult": 0.70, "max_mult": 2.0},
    "other":        {"min_mult": 0.60, "max_mult": 2.5},
}


def check_bond_lengths(structure: Structure, compound_type: str) -> Tuple[bool, List[str]]:
    violations = []
    tol = BOND_LENGTH_TOLERANCES.get(compound_type, BOND_LENGTH_TOLERANCES["other"])

    for i, site_i in enumerate(structure):
        neighbors = structure.get_neighbors(site_i, r=6.0)
        if not neighbors:
            continue
        nearest = min(neighbors, key=lambda n: n.nn_distance)
        el_i = site_i.specie if hasattr(site_i, "specie") else list(site_i.species.elements)[0]
        site_j = getattr(nearest, "site", nearest)
        el_j = site_j.specie if hasattr(site_j, "specie") else list(site_j.species.elements)[0]

        r_i = Element(str(el_i)).atomic_radius or 1.0
        r_j = Element(str(el_j)).atomic_radius or 1.0
        expected = r_i + r_j
        min_ok = expected * tol["min_mult"]
        max_ok = expected * tol["max_mult"]

        d = nearest.nn_distance
        if d < min_ok:
            violations.append(f"site {i} ({el_i}-{el_j}): {d:.2f}A < min {min_ok:.2f}A (too close)")
        elif d > max_ok:
            violations.append(f"site {i} ({el_i}-{el_j}): {d:.2f}A > max {max_ok:.2f}A (isolated/detached)")

    return (len(violations) == 0), violations


def check_symmetry(structure: Structure, symprec: float = 0.1) -> Tuple[bool, str]:
    try:
        sga = SpacegroupAnalyzer(structure, symprec=symprec)
        sg_symbol = sga.get_space_group_symbol()
        return True, sg_symbol
    except Exception as e:
        return False, f"symmetry analysis failed: {e}"


def check_charge_balance(structure: Structure) -> Tuple[bool, str]:
    """Lightweight plausibility check for ionic compound types (oxide/halide/etc).
    Skipped entirely for intermetallics, where formal oxidation states aren't meaningful.
    """
    try:
        comp = structure.composition
        oxi_guesses = comp.oxi_state_guesses(max_sites=-1)
        if not oxi_guesses:
            return False, "no charge-balanced oxidation state assignment found"
        return True, f"charge-balanced as {oxi_guesses[0]}"
    except Exception as e:
        return False, f"charge balance check failed: {e}"


def filter_candidates(
    cif_strings: List[str],
    compound_type: str,
) -> Tuple[List[Structure], dict]:
    """Filtering for Stage 4a ONLY: raw, freshly-generated UNDOPED candidates.
    Runs full bond-length + symmetry + charge-balance + duplicate checks,
    because every candidate here is an independent, unverified guess from
    CrystaLLM. Do NOT reuse this for doped structures - see
    validate_doped_structure() below for why that has to be a separate check.

    Returns (valid_structures, failure_breakdown) where failure_breakdown
    is a dict of {reason: [example strings]} for feedback to the generation loop.
    """
    matcher = StructureMatcher()
    valid: List[Structure] = []
    failures = {
        "parse": [],
        "disordered": [],
        "bond_length": [],
        "symmetry": [],
        "charge_balance": [],
        "duplicate": [],
        "oversized": [],
    }

    check_charge = compound_type not in ("intermetallic", "other")

    for cif_str in cif_strings:
        try:
            struct = Structure.from_str(cif_str, fmt="cif")
        except Exception as e:
            failures["parse"].append(str(e))
            continue

        if struct.num_sites == 0:
            failures["parse"].append("empty structure")
            continue

        # QA §7.1: undoped candidates must be fully ordered - no partial/disordered sites.
        # CrystaLLM could in theory produce a mixed-occupancy site; if it does, the
        # generated structure is NOT an undoped compound and must be rejected here.
        if not struct.is_ordered:
            disordered_sites = [
                f"site {i} ({site.species_string})"
                for i, site in enumerate(struct)
                if not site.is_ordered
            ]
            failures["disordered"].append(
                f"structure has {len(disordered_sites)} partial-occupancy site(s) "
                f"(should be fully ordered for undoped generation): "
                + ", ".join(disordered_sites[:3])
            )
            continue

        # QA §7.3: sanity-check the structure size - very large supercells (>200 atoms)
        # would cause pathological MACE runtimes and are almost certainly a generation
        # artifact rather than a sensible primitive cell.
        MAX_ATOMS = 200
        if struct.num_sites > MAX_ATOMS:
            failures["oversized"].append(
                f"structure has {struct.num_sites} atoms (limit: {MAX_ATOMS}) - "
                f"likely a supercell artifact from generation"
            )
            continue

        bonds_ok, bond_msgs = check_bond_lengths(struct, compound_type)
        if not bonds_ok:
            failures["bond_length"].extend(bond_msgs[:3])
            continue

        sym_ok, sym_info = check_symmetry(struct)
        if not sym_ok:
            failures["symmetry"].append(sym_info)
            continue

        if check_charge:
            charge_ok, charge_info = check_charge_balance(struct)
            if not charge_ok:
                failures["charge_balance"].append(charge_info)
                continue

        if any(matcher.fit(struct, existing) for existing in valid):
            failures["duplicate"].append("matched an already-accepted candidate")
            continue

        valid.append(struct)

    return valid, failures


def validate_doped_structure(
    doped_structure: Structure,
    host_site_species: str,
    dopant_species: str,
    dopant_fraction: float,
    compound_type: str,
    occupancy_tol: float = 0.01,
) -> DopedStructureValidation:
    """Filtering for Stage 4b ONLY: a single doped structure produced by
    substituting onto an ALREADY-VALIDATED parent structure. This is
    deliberately a separate, smaller function from filter_candidates() above,
    for reasons that matter:

    - There's exactly one structure to check, not a batch - no deduplication
      step makes sense here.
    - Symmetry is inherited from the (already-checked) parent; re-running a
      full space-group check on a structure that now has a disordered/partial-
      occupancy site is a different and less meaningful question ("does this
      still look like a sensible space group with a mixed-occupancy site?")
      than what check_symmetry answers for an ordered candidate, so it is
      intentionally NOT re-run here.
    - The one check that IS new and specific to doping: does the doped site's
      occupancy actually sum to 1.0 (host_fraction + dopant_fraction)? A
      substitution that leaves a vacancy-like sum (e.g. by construction error)
      needs to be caught before relaxation, not after.
    - Bond-length checking is still meaningful and IS re-run, but only for
      neighbors of the doped site specifically, since that's the only part of
      the structure that changed.
    """
    occ_sum = None
    bond_ok, bond_issues = True, []

    for i, site in enumerate(doped_structure):
        species_dict = {str(el): amt for el, amt in site.species.items()}
        if host_site_species in species_dict or dopant_species in species_dict:
            occ_sum = species_dict.get(host_site_species, 0.0) + species_dict.get(dopant_species, 0.0)

            neighbors = doped_structure.get_neighbors(site, r=6.0)
            if neighbors:
                nearest = min(neighbors, key=lambda n: n.nn_distance)
                tol = BOND_LENGTH_TOLERANCES.get(compound_type, BOND_LENGTH_TOLERANCES["other"])
                r_host = Element(host_site_species).atomic_radius or 1.0
                r_dopant = Element(dopant_species).atomic_radius or 1.0
                site_neighbor = getattr(nearest, "site", nearest)
                el_neighbor = site_neighbor.specie if hasattr(site_neighbor, "specie") else list(site_neighbor.species.elements)[0]
                r_neighbor = Element(str(el_neighbor)).atomic_radius or 1.0
                # use whichever of host/dopant radius gives the tighter (more
                # conservative) minimum, since the site is now a mixture of both
                expected = min(r_host, r_dopant) + r_neighbor
                min_ok = expected * tol["min_mult"]
                if nearest.nn_distance < min_ok:
                    bond_ok = False
                    bond_issues.append(
                        f"doped site {i}: nearest-neighbor distance {nearest.nn_distance:.2f}A "
                        f"< minimum {min_ok:.2f}A"
                    )
            break  # only one site should match a freshly-doped structure

    occupancy_ok = occ_sum is not None and abs(occ_sum - 1.0) <= occupancy_tol
    is_valid = occupancy_ok and bond_ok

    notes = []
    if occ_sum is None:
        notes.append(f"no site found containing '{host_site_species}' or '{dopant_species}' - doping may not have been applied")
    elif not occupancy_ok:
        notes.append(f"doped site occupancy sums to {occ_sum:.4f}, expected 1.0 (+/-{occupancy_tol})")
    if not bond_ok:
        notes.append("bond-length check around doped site failed")
    if is_valid:
        notes.append("doped structure passed occupancy and local bond-length checks")

    return DopedStructureValidation(
        is_valid=is_valid,
        occupancy_sum_ok=occupancy_ok,
        occupancy_sum=occ_sum if occ_sum is not None else 0.0,
        bond_length_ok=bond_ok,
        bond_length_issues=bond_issues,
        notes="; ".join(notes),
    )
