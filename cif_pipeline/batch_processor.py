"""
cif_pipeline/batch_processor.py

Batch testing and processing engine for the CIF pipeline.
Executes test matrices across compounds, evaluates polymorphs,
exports formatted .dat scientific data files for analysis and plotting.
"""

from __future__ import annotations
import os
import time
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional

from cif_pipeline.models import CompoundQuery
from cif_pipeline.orchestrator import run_pipeline

log = logging.getLogger("cif_pipeline.batch")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

BENCHMARK_10_MATERIALS = [
    {"formula": "Fe2O3", "category": "Trigonal Oxide (Hematite)", "space_group": "R-3c"},
    {"formula": "Fe1.9Ti0.1O3", "category": "Doped Oxide (Ti in Fe)", "host": "Fe2O3", "dopant": "Ti", "site": "Fe", "fraction": 0.05},
    {"formula": "Fe1.85Ti0.1Cr0.05O3", "category": "Co-Doped Oxide", "host": "Fe2O3", "dopant": "Ti", "site": "Fe", "fraction": 0.05, "co_dopants": [{"dopant": "Cr", "site": "Fe", "fraction": 0.025}]},
    {"formula": "TiO2", "category": "Polymorphic Oxide (Rutile/Anatase)", "space_group": "P4_2/mnm"},
    {"formula": "NaCl", "category": "Rock-salt Halide", "space_group": "Fm-3m"},
    {"formula": "Si", "category": "Diamond Semiconductor", "space_group": "Fd-3m"},
    {"formula": "Cu", "category": "FCC Transition Metal", "space_group": "Fm-3m"},
    {"formula": "Al2O3", "category": "Corundum Rhombohedral", "space_group": "R-3c"},
    {"formula": "MoS2", "category": "Layered 2D Dichalcogenide", "space_group": "P6_3/mmc"},
    {"formula": "ZnO", "category": "Wurtzite II-VI Semiconductor", "space_group": "P6_3mc"},
    {"formula": "CaF2", "category": "Fluorite Alkaline-Earth Halide", "space_group": "Fm-3m"},
]


def _batch_ask_handler(question: str, options: Optional[List[str]] = None) -> Optional[str]:
    """Auto-selects the first option in batch mode to avoid stdin blocking."""
    if options:
        log.info(f"[Batch] Auto-selected '{options[0]}' for: {question[:80]}")
        return options[0]
    return None


def run_batch_pipeline(cases: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """
    Executes a batch run across test cases, generates statistics,
    and writes out `.dat` scientific data files.
    """
    if cases is None:
        cases = BENCHMARK_10_MATERIALS

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    results = []
    start_all = time.time()

    for idx, item in enumerate(cases, start=1):
        f = item.get("formula", "")
        cat = item.get("category", "General")
        is_doped = bool(item.get("dopant") or item.get("host"))
        doping_spec = None

        q = CompoundQuery(
            raw_input=f,
            is_doped_hint=is_doped,
            host_formula=item.get("host"),
            dopant_element=item.get("dopant"),
            host_site_species=item.get("site"),
            dopant_fraction=float(item.get("fraction")) if item.get("fraction") else None,
            space_group=item.get("space_group"),
            co_dopants=item.get("co_dopants", []),
        )

        t0 = time.time()
        res = run_pipeline(q, ask_user=_batch_ask_handler)
        elapsed = time.time() - t0

        cif_len = len(res.cif_string) if res.cif_string else 0
        sg_found = res.matched_record.space_group if res.matched_record else (item.get("space_group") or "P 1")
        source = res.source or "unknown"

        results.append({
            "index": idx,
            "formula": f,
            "category": cat,
            "success": bool(res.cif_string),
            "source": source,
            "space_group": sg_found,
            "cif_chars": cif_len,
            "elapsed_seconds": round(elapsed, 3),
            "cif_string": res.cif_string,
        })

    total_time = round(time.time() - start_all, 2)
    success_count = sum(1 for r in results if r["success"])

    # Generate .dat file
    dat_path = DATA_DIR / "batch_test_cases.dat"
    write_dat_file(dat_path, results, total_time)

    return {
        "total_cases": len(results),
        "success_count": success_count,
        "failed_count": len(results) - success_count,
        "success_rate_percent": round((success_count / len(results)) * 100, 1) if results else 0,
        "total_time_seconds": total_time,
        "results": results,
        "dat_file": str(dat_path.name),
    }


def write_dat_file(filepath: Path, results: List[Dict[str, Any]], total_time: float):
    """Writes a standardized scientific .dat column file."""
    lines = [
        "# ==========================================================================",
        "# CIF ARCHITECT v2.0 — Scientific Benchmark Batch Data (.dat)",
        f"# Generated: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}",
        f"# Total Cases: {len(results)} | Total Time: {total_time:.2f}s",
        "# Columns: Index  Formula  Category  Success  Source  SpaceGroup  CifBytes  TimeSec",
        "# ==========================================================================",
        f"{'Index':<6} {'Formula':<20} {'Success':<8} {'Source':<18} {'SpaceGroup':<14} {'CifBytes':<10} {'TimeSec':<8} Category",
        "-" * 100,
    ]

    for r in results:
        succ = "1" if r["success"] else "0"
        lines.append(
            f"{r['index']:<6} {r['formula']:<20} {succ:<8} {r['source']:<18} {str(r['space_group']):<14} {r['cif_chars']:<10} {r['elapsed_seconds']:<8.3f} {r['category']}"
        )

    lines.append("# EOF\n")
    filepath.write_text("\n".join(lines), encoding="utf-8")
    log.info(f"Wrote batch data file: {filepath}")
