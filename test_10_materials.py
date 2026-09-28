"""
Comprehensive 10-Material Test Suite for the CIF Generation & Relaxation Pipeline.
Tests 10 diverse chemical classes across the periodic table:
  1. Si          - Elemental semiconductor (diamond cubic Fd-3m)
  2. Fe2O3       - Transition metal oxide (hematite R-3c)
  3. TiO2        - Polymorphic oxide (rutile / anatase)
  4. NaCl        - Alkali halide (rock-salt Fm-3m)
  5. Al2O3       - Main-group oxide (corundum R-3c)
  6. Cu          - Elemental transition metal (FCC Fm-3m)
  7. MoS2        - Layered 2D transition metal dichalcogenide (P6_3/mmc)
  8. ZnO         - II-VI Wurtzite semiconductor (hexagonal P6_3mc)
  9. CaF2        - Alkaline-earth halide (fluorite Fm-3m)
 10. Fe1.9Ti0.1O3 - Doped solid solution (Ti on Fe site, validated & relaxed)
"""

import time
from cif_pipeline.models import CompoundQuery
from cif_pipeline.orchestrator import run_pipeline
from pymatgen.core import Structure

TEST_MATERIALS = [
    {
        "name": "Silicon (Si)",
        "query": CompoundQuery(raw_input="Si"),
        "class": "Elemental Semiconductor",
    },
    {
        "name": "Hematite (Fe2O3)",
        "query": CompoundQuery(raw_input="Fe2O3"),
        "class": "Transition Metal Oxide",
    },
    {
        "name": "Titanium Dioxide (TiO2)",
        "query": CompoundQuery(raw_input="TiO2"),
        "class": "Polymorphic Oxide",
    },
    {
        "name": "Halite / Rock Salt (NaCl)",
        "query": CompoundQuery(raw_input="NaCl"),
        "class": "Alkali Halide",
    },
    {
        "name": "Alumina (Al2O3)",
        "query": CompoundQuery(raw_input="Al2O3"),
        "class": "Main-Group Oxide",
    },
    {
        "name": "Copper (Cu)",
        "query": CompoundQuery(raw_input="Cu"),
        "class": "FCC Metal",
    },
    {
        "name": "Molybdenum Disulfide (MoS2)",
        "query": CompoundQuery(raw_input="MoS2"),
        "class": "2D Layered Dichalcogenide",
    },
    {
        "name": "Zinc Oxide (ZnO)",
        "query": CompoundQuery(raw_input="ZnO"),
        "class": "II-VI Semiconductor",
    },
    {
        "name": "Fluorite (CaF2)",
        "query": CompoundQuery(raw_input="CaF2"),
        "class": "Alkaline-Earth Halide",
    },
    {
        "name": "Ti-doped Hematite (Fe1.9Ti0.1O3)",
        "query": CompoundQuery(
            raw_input="Fe1.9Ti0.1O3",
            host_formula="Fe2O3",
            dopant_element="Ti",
            host_site_species="Fe",
            dopant_fraction=0.05,
        ),
        "class": "Doped Solid Solution",
    },
]


def run_benchmark():
    print("=" * 80)
    print(" CIF PIPELINE: 10-MATERIAL EXTENSIVE ENGINE BENCHMARK")
    print("=" * 80)

    results_table = []

    for idx, item in enumerate(TEST_MATERIALS, 1):
        name = item["name"]
        query = item["query"]
        category = item["class"]

        print(f"\n[{idx}/10] Testing {name} ({category})...")
        t0 = time.perf_counter()

        def test_ask(q, opts=None):
            return opts[0] if opts else None

        res = run_pipeline(query, ask_user=test_ask)
        elapsed = round(time.perf_counter() - t0, 2)

        has_cif = res.cif_string is not None
        cif_lines = len(res.cif_string.splitlines()) if has_cif else 0

        sg_symbol = "N/A"
        parsed_valid = False
        if has_cif:
            try:
                struct = Structure.from_str(res.cif_string, fmt="cif")
                sg_symbol = res.matched_record.space_group if (res.matched_record and res.matched_record.space_group) else "Standardized"
                parsed_valid = True
            except Exception as e:
                sg_symbol = f"ParseErr: {e}"

        record_id = res.matched_record.record_id if res.matched_record else "Generated"
        source = res.source
        status_str = "PASS" if (has_cif and parsed_valid) else "FAIL"

        print(f"    Source:       {source}")
        print(f"    Record ID:    {record_id}")
        print(f"    Space Group:  {sg_symbol}")
        print(f"    CIF Output:   {cif_lines} lines (Valid: {parsed_valid})")
        print(f"    Elapsed Time: {elapsed}s")
        print(f"    Status:       {status_str}")

        results_table.append({
            "idx": idx,
            "material": name,
            "class": category,
            "source": source,
            "record": record_id,
            "sg": sg_symbol,
            "lines": cif_lines,
            "time": f"{elapsed}s",
            "status": status_str,
        })

    print("\n" + "=" * 80)
    print(" BENCHMARK SUMMARY TABLE (10/10)")
    print("=" * 80)
    header = f"{'#':<3} | {'Material':<32} | {'Class':<25} | {'Source':<16} | {'Time':<7} | {'Status'}"
    print(header)
    print("-" * len(header))
    for r in results_table:
        print(f"{r['idx']:<3} | {r['material']:<32} | {r['class']:<25} | {r['source']:<16} | {r['time']:<7} | {r['status']}")
    print("=" * 80)


if __name__ == "__main__":
    run_benchmark()
