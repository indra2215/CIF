"""
cif_pipeline/symmetry_matrix.py

Crystallographic Symmetry Matrix and Space Group Analysis module.
Extracts space group, point group, Wyckoff positions, symmetry operations,
metric tensors, and symmetry matrices from CIF strings or pymatgen Structures.
"""

from __future__ import annotations
import io
import math
import logging
from typing import Dict, Any, List, Optional

log = logging.getLogger("cif_pipeline.symmetry")

try:
    from pymatgen.core import Structure
    from pymatgen.symmetry.analyzer import SpacegroupAnalyzer
    from pymatgen.io.cif import CifParser
    HAS_PYMATGEN = True
except ImportError:
    HAS_PYMATGEN = False


def compute_symmetry_matrix(cif_string: str, symprec: float = 0.01) -> Dict[str, Any]:
    """
    Analyzes a CIF structure and computes full crystallographic symmetry data:
    - Space group symbol & number
    - Crystal system & point group
    - Wyckoff positions table
    - Symmetry operations (rotation matrices + translation vectors)
    - Metric tensor (cell parameters)
    - Symmetry matrix (3x3 transformations)
    """
    if not cif_string or not cif_string.strip():
        return {"error": "Empty CIF string provided"}

    if not HAS_PYMATGEN:
        return _fallback_symmetry_matrix(cif_string)

    try:
        parser = CifParser(io.StringIO(cif_string))
        if hasattr(parser, "parse_structures"):
            structures = parser.parse_structures(primitive=False)
        else:
            structures = parser.get_structures(primitive=False)
        if not structures:
            return _fallback_symmetry_matrix(cif_string)

        struct = structures[0]
        sga = SpacegroupAnalyzer(struct, symprec=symprec)

        sg_symbol = sga.get_space_group_symbol()
        sg_number = sga.get_space_group_number()
        crystal_sys = sga.get_crystal_system()
        point_group = sga.get_point_group_symbol()
        hall_symbol = sga.get_hall()

        # Symmetry operations
        sym_ops = sga.get_symmetry_operations(cartesian=False)
        ops_data = []
        for i, op in enumerate(sym_ops[:24]):  # Cap at 24 for UI display
            ops_data.append({
                "index": i + 1,
                "xyz_string": op.as_xyz_string(),
                "rotation_matrix": op.rotation_matrix.tolist(),
                "translation_vector": [round(float(x), 4) for x in op.translation_vector],
            })

        # Wyckoff positions
        sym_dataset = sga.get_symmetry_dataset()
        wyckoff_positions = []
        if sym_dataset:
            wyckoffs = sym_dataset.get("wyckoffs", [])
            equivalent_atoms = sym_dataset.get("equivalent_atoms", [])
            for i, site in enumerate(struct.sites):
                wyckoff_letter = wyckoffs[i] if i < len(wyckoffs) else "?"
                wyckoff_positions.append({
                    "site_index": i + 1,
                    "element": site.specie.symbol,
                    "wyckoff": wyckoff_letter,
                    "multiplicity": len([eq for eq in equivalent_atoms if eq == equivalent_atoms[i]]) if i < len(equivalent_atoms) else 1,
                    "coords": [round(float(c), 4) for c in site.frac_coords],
                    "equivalent_index": int(equivalent_atoms[i]) if i < len(equivalent_atoms) else i,
                })

        # Metric tensor of unit cell: G = [ [a.a, a.b, a.c], [b.a, b.b, b.c], [c.a, c.b, c.c] ]
        lattice = struct.lattice
        a, b, c = lattice.a, lattice.b, lattice.c
        alpha, beta, gamma = math.radians(lattice.alpha), math.radians(lattice.beta), math.radians(lattice.gamma)
        
        g11 = a * a
        g12 = a * b * math.cos(gamma)
        g13 = a * c * math.cos(beta)
        g22 = b * b
        g23 = b * c * math.cos(alpha)
        g33 = c * c

        metric_tensor = [
            [round(g11, 4), round(g12, 4), round(g13, 4)],
            [round(g12, 4), round(g22, 4), round(g23, 4)],
            [round(g13, 4), round(g23, 4), round(g33, 4)],
        ]

        # Transformation matrix (Direct to Cartesian)
        direct_to_cart = [
            [round(float(x), 4) for x in row]
            for row in lattice.matrix.tolist()
        ]

        # Centrosymmetric & Polar checks
        # Centrosymmetric if inversion operation exists [-1, 0, 0], [0, -1, 0], [0, 0, -1]
        is_centro = any(
            op.rotation_matrix[0][0] == -1 and op.rotation_matrix[1][1] == -1 and op.rotation_matrix[2][2] == -1
            and op.rotation_matrix[0][1] == 0 and op.rotation_matrix[0][2] == 0
            for op in sym_ops
        )
        # Polar if point group belongs to polar classes: 1, 2, m, mm2, 4, 4mm, 3, 3m, 6, 6mm
        polar_point_groups = {"1", "2", "m", "mm2", "4", "4mm", "3", "3m", "6", "6mm"}
        is_polar = point_group in polar_point_groups

        return {
            "success": True,
            "space_group_symbol": sg_symbol,
            "space_group_number": sg_number,
            "crystal_system": crystal_sys,
            "point_group": point_group,
            "hall_symbol": hall_symbol,
            "total_symmetry_operations": len(sym_ops),
            "operations": ops_data,
            "wyckoff_positions": wyckoff_positions,
            "cell_parameters": {
                "a": round(lattice.a, 4),
                "b": round(lattice.b, 4),
                "c": round(lattice.c, 4),
                "alpha": round(lattice.alpha, 2),
                "beta": round(lattice.beta, 2),
                "gamma": round(lattice.gamma, 2),
                "volume": round(lattice.volume, 3),
            },
            "metric_tensor": metric_tensor,
            "direct_to_cartesian_matrix": direct_to_cart,
            "is_centrosymmetric": is_centro,
            "is_polar": is_polar,
            "fallback": False,
            "cell_parameters_source": "pymatgen_spacegroup_analyzer",
        }

    except Exception as e:
        log.warning(f"pymatgen symmetry calculation failed: {e}. Falling back to basic parser.")
        return _fallback_symmetry_matrix(cif_string)


def _fallback_symmetry_matrix(cif_string: str) -> Dict[str, Any]:
    """Fallback parser extracting basic spacegroup and cell info from CIF text."""
    sg_symbol = "P 1"
    sg_number = 1
    a, b, c = 5.0, 5.0, 5.0
    alpha, beta, gamma = 90.0, 90.0, 90.0

    for line in cif_string.splitlines():
        line = line.strip()
        if line.startswith("_symmetry_space_group_name_H-M") or line.startswith("_space_group.name_H-M_alt"):
            parts = line.split(None, 1)
            if len(parts) > 1:
                sg_symbol = parts[1].strip("'\"")
        elif line.startswith("_symmetry_Int_Tables_number") or line.startswith("_space_group_IT_number"):
            parts = line.split()
            if len(parts) > 1:
                try:
                    sg_number = int(parts[1])
                except ValueError:
                    pass
        elif line.startswith("_cell_length_a"):
            try: a = float(line.split()[1].split("(")[0])
            except (ValueError, IndexError): pass
        elif line.startswith("_cell_length_b"):
            try: b = float(line.split()[1].split("(")[0])
            except (ValueError, IndexError): pass
        elif line.startswith("_cell_length_c"):
            try: c = float(line.split()[1].split("(")[0])
            except (ValueError, IndexError): pass
        elif line.startswith("_cell_angle_alpha"):
            try: alpha = float(line.split()[1].split("(")[0])
            except (ValueError, IndexError): pass
        elif line.startswith("_cell_angle_beta"):
            try: beta = float(line.split()[1].split("(")[0])
            except (ValueError, IndexError): pass
        elif line.startswith("_cell_angle_gamma"):
            try: gamma = float(line.split()[1].split("(")[0])
            except (ValueError, IndexError): pass

    import math
    ca = math.cos(math.radians(alpha))
    cb = math.cos(math.radians(beta))
    cg = math.cos(math.radians(gamma))
    vol_factor = math.sqrt(max(0.0, 1.0 - ca**2 - cb**2 - cg**2 + 2 * ca * cb * cg))
    cell_vol = round(a * b * c * vol_factor, 3)

    return {
        "success": True,
        "fallback": True,
        "cell_parameters_source": "cif_text_regex",
        "space_group_symbol": sg_symbol,
        "space_group_number": sg_number,
        "crystal_system": "triclinic" if sg_number <= 2 else "monoclinic" if sg_number <= 15 else "orthorhombic" if sg_number <= 74 else "tetragonal" if sg_number <= 142 else "trigonal" if sg_number <= 167 else "hexagonal" if sg_number <= 194 else "cubic",
        "point_group": "Unknown",
        "hall_symbol": "P 1",
        "total_symmetry_operations": 1,
        "operations": [{
            "index": 1,
            "xyz_string": "x, y, z",
            "rotation_matrix": [[1,0,0],[0,1,0],[0,0,1]],
            "translation_vector": [0.0, 0.0, 0.0]
        }],
        "wyckoff_positions": [],
        "cell_parameters": {
            "a": a, "b": b, "c": c,
            "alpha": alpha, "beta": beta, "gamma": gamma,
            "volume": cell_vol,
        },
        "metric_tensor": [
            [round(a*a, 4), 0.0, 0.0],
            [0.0, round(b*b, 4), 0.0],
            [0.0, 0.0, round(c*c, 4)]
        ],
        "direct_to_cartesian_matrix": [
            [a, 0.0, 0.0],
            [0.0, b, 0.0],
            [0.0, 0.0, c]
        ],
        "is_centrosymmetric": False,
        "is_polar": False,
    }
