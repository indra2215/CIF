"""
Example usage. Run: python -m cif_pipeline.main

API keys and model paths are automatically loaded from .env in the project root.
See .env for configuration options.
"""

from .models import CompoundQuery
from .orchestrator import run_pipeline
from .user_interaction import default_cli_ask


def example_undoped():
    """Search for / generate an undoped compound (LaMnO3)."""
    query = CompoundQuery(raw_input="LaMnO3")
    result = run_pipeline(query, ask_user=default_cli_ask)
    _print_result(result)


def example_doped():
    """Search for / generate a doped compound (La0.7Sr0.3MnO3)."""
    query = CompoundQuery(
        raw_input="La0.7Sr0.3MnO3",
        # you can pre-fill any of these if your web form already collected them,
        # and classify.py will only ask the user for whatever's still missing:
        host_formula="LaMnO3",
        dopant_element="Sr",
        host_site_species="La",
        dopant_fraction=0.3,
    )
    result = run_pipeline(query, ask_user=default_cli_ask)
    _print_result(result)


def example_simple():
    """Quick test with a common oxide (Fe2O3)."""
    query = CompoundQuery(raw_input="Fe2O3")
    result = run_pipeline(query, ask_user=default_cli_ask)
    _print_result(result)


def _print_result(result):
    print("\n" + "=" * 60)
    print(f"  Source:  {result.source}")
    print(f"  Notes:   {result.notes}")
    if result.normalization_trace:
        t = result.normalization_trace
        print(f"  Search key used: {t.search_key_used}")
        print(f"  Classification:  {t.classification_reasoning}")
        if t.multiplicity and t.multiplicity.was_reduced:
            print(f"  Multiplicity:    {t.multiplicity.reason}")
    if result.matched_record:
        m = result.matched_record
        print(f"  Matched record:  {m.source} / {m.record_id}")
        if m.source_url:
            print(f"  Source URL:      {m.source_url}")
    if result.candidate_matches:
        print(f"  Total candidates found: {len(result.candidate_matches)}")
    if result.doped_validation:
        v = result.doped_validation
        print(f"  Doped validation: valid={v.is_valid}, occ_sum={v.occupancy_sum:.4f}")
    if result.cif_string:
        lines = result.cif_string.strip().split("\n")
        print(f"  CIF: {len(lines)} lines")
        # Show first 5 lines as preview
        for line in lines[:5]:
            print(f"    {line}")
        if len(lines) > 5:
            print(f"    ... ({len(lines) - 5} more lines)")
    else:
        print("  CIF: None (see notes for reason)")
    print("=" * 60)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        formula = sys.argv[1]
        print(f"Running pipeline for: {formula}")
        query = CompoundQuery(raw_input=formula)
        result = run_pipeline(query, ask_user=default_cli_ask)
        _print_result(result)
    else:
        print("Running example: Fe2O3 (undoped)")
        example_simple()
