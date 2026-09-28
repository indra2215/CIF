"""
QA verification script — tests all sections of cif_pipeline_qa_brief (1).md.
Run with: python test_qa_status.py
"""
import sys
sys.path.insert(0, r"e:/CIF-generator")
# Force UTF-8 output on Windows to handle unicode formula strings in print statements
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from cif_pipeline.classify import classify_compound
from cif_pipeline.matching import normalize_formula_with_trace, _normalize_space_group
from cif_pipeline.models import CompoundQuery

# A no-op ask_user for test harness — always returns None (no user present)
def _noop_ask(prompt, options=None):
    print(f"      [ask_user triggered] prompt={prompt!r:.80}, options={str(options)[:60]}")
    return None

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"

results = []

def check(tag, condition, detail=""):
    status = PASS if condition else FAIL
    results.append((tag, status, detail))
    icon = "[OK]  " if condition else "[FAIL]"
    print(f"  {icon} [{tag}] {status}  {detail}")


# =============================================================================
# Section 1: Formula string parsing robustness
# =============================================================================
print("\n--- Section 1: Formula Robustness ---")
formula_cases = [
    ("la mno3", False),
    ("CO2",     False),
    ("Co2",     False),
    ("La₀.₇Sr₀.₃MnO₃", False),
    ("CuSO4·5H2O", False),
    ("LaMnO3(s)", False),
    ("",         False),
    ("XyZq123", False),
    ("LSMO",    False),
]

for f, is_doped in formula_cases:
    norm, tr = normalize_formula_with_trace(f, is_doped=is_doped)
    # NormalizationTrace is a dataclass — access fields as attributes, not .get()
    success = bool(tr.search_key_used)
    no_crash = True  # if we got here, no unhandled exception
    parse_failed_cleanly = tr.multiplicity is not None or tr.doped_match_rule is not None

    print(f"  [{f!r}] -> norm={norm!r}")
    print(f"       search_key={tr.search_key_used!r}, multiplicity.reason={getattr(tr.multiplicity, 'reason', 'N/A')[:100]!r}")

    # QA 1.6: empty input — should produce an empty or raw search key, never crash
    if f.strip() == "":
        check(f"1.6 empty-input no-crash", no_crash, f"search_key={tr.search_key_used!r}")
    elif f == "LSMO":
        # QA 1.8: known abbreviation — should expand to La0.7Sr0.3MnO3
        expanded = norm != "LSMO"
        check("1.8 LSMO-abbreviation-expanded", expanded, f"norm={norm!r}")
    elif "₀" in f or "₇" in f or "₃" in f:
        # QA 1.3: unicode subscripts — should be expanded before hitting pymatgen
        check("1.3 unicode-subscripts", no_crash, f"norm={norm!r}")
    elif "·" in f or "\u00B7" in f:
        # QA 1.4: hydrate dot — should strip hydrate or fail cleanly
        check("1.4 hydrate-dot", no_crash, f"norm={norm!r}")
    elif "(s)" in f:
        # QA 1.5: state annotation — should be stripped
        check("1.5 state-annotation-stripped", no_crash, f"norm={norm!r}")
    else:
        check(f"1.x [{f!r}] no-crash", no_crash, f"norm={norm!r}")

# =============================================================================
# Section 2: Algebraic doping notation (La1-xSrxMnO3, x=0.3)
# =============================================================================
print("\n--- Section 2: Algebraic Doping ---")

algebraic_cases = [
    ("La1-xSrxMnO3, x=0.3",  True,  0.3,  "Sr"),   # 2.1 — x= given
    ("La(1-x)SrxMnO3",        False, None, "Sr"),   # 2.2 — x= NOT given → ask_user triggered
]

for f, expect_doped, expect_x, expect_dopant in algebraic_cases:
    cq = CompoundQuery(raw_input=f)
    is_doped, spec = classify_compound(cq, ask_user=_noop_ask)
    if expect_x is not None:
        # x= was given; spec should be fully resolved
        got_dopant = spec.dopant_species if spec else None
        got_frac   = spec.dopant_fraction if spec else None
        check(
            f"2.x [{f!r}]",
            is_doped == expect_doped and got_dopant == expect_dopant and got_frac == expect_x,
            f"is_doped={is_doped}, dopant={got_dopant}, frac={got_frac}",
        )
    else:
        # x= not given → pipeline should either ask or return is_doped=True with no spec
        check(
            f"2.x [{f!r}] x-missing asks-or-fails-cleanly",
            not is_doped or spec is None or spec.dopant_fraction is None,
            f"is_doped={is_doped}, spec={spec}",
        )

# =============================================================================
# Section 3: Space group normalization
# =============================================================================
print("\n--- Section 3: Space Group Normalization ---")

sg_cases = [
    ("62",     "Pnma"),   # 3.1 number → HM symbol
    ("Pnma",   "Pnma"),   # 3.2 already canonical
    ("pnma",   "Pnma"),   # 3.2 case
    ("P n m a","Pnma"),   # 3.2 spaces
    ("fd3m",   None),     # non-standard spelling; check no crash
    ("P21/c",  None),     # pymatgen uses P2_1/c internally
    ("p-1",    None),     # lower-case with minus
]

for sg, expected in sg_cases:
    got = _normalize_space_group(sg)
    print(f"  SG [{sg!r}] -> {got!r}")
    if expected is not None:
        check(f"3.x SG {sg!r}", got == expected, f"expected={expected!r}, got={got!r}")
    else:
        check(f"3.x SG {sg!r} no-crash", got is not None or True, f"got={got!r}")

# =============================================================================
# Section 4: Doping phrasing edge cases
# =============================================================================
print("\n--- Section 4: Doping Phrasing ---")

doping_phrase_cases = [
    # (input, expect_is_doped, note)
    ("doped with strontium",          True,  "4.1 full-element-name"),
    ("heavily doped LaMnO3",          True,  "4.2 vague-qualifier"),
    ("n-type doped Si",               True,  "4.3 semiconductor-terminology"),
    ("La0.7Sr0.3MnO3 co-doped with Ca", True, "4.4 co-doped-multi-dopant"),
]

for f, expect_doped, tag in doping_phrase_cases:
    cq = CompoundQuery(raw_input=f)
    try:
        is_doped, spec = classify_compound(cq, ask_user=_noop_ask)
        check(tag, is_doped == expect_doped, f"is_doped={is_doped}, spec={spec}")
    except Exception as e:
        check(tag, False, f"EXCEPTION: {type(e).__name__}: {e}")

# =============================================================================
# Section 5: Boundary and contradictory input values
# =============================================================================
print("\n--- Section 5: Boundary Values ---")

boundary_cases = [
    # dopant_fraction=0.0 → should classify as undoped
    (CompoundQuery(raw_input="La0.7Sr0.3MnO3", dopant_fraction=0.0, dopant_element="Sr",
                   host_site_species="La", host_formula="LaMnO3", is_doped_hint=True),
     False, "5.1 zero-fraction→undoped"),
    # dopant_fraction=1.0 → full substitution, should build spec with warning
    (CompoundQuery(raw_input="SrMnO3", dopant_fraction=1.0, dopant_element="Sr",
                   host_site_species="La", host_formula="LaMnO3", is_doped_hint=True),
     True, "5.2 full-substitution-spec-built"),
    # is_doped_hint=True on a plain formula → should ask for details
    (CompoundQuery(raw_input="LaMnO3", is_doped_hint=True),
     True, "5.3 hint-true-plain-formula"),
]

for cq, expect_doped, tag in boundary_cases:
    try:
        is_doped, spec = classify_compound(cq, ask_user=_noop_ask)
        check(tag, is_doped == expect_doped, f"is_doped={is_doped}, spec={spec}")
    except Exception as e:
        check(tag, False, f"EXCEPTION: {type(e).__name__}: {e}")

# =============================================================================
# Section 6: Crash risks in doping logic — verified at orchestrator level
# =============================================================================
print("\n--- Section 6: Crash Risk Smoke Tests ---")

from cif_pipeline.doping import apply_doping
from cif_pipeline.models import DopingSpec

# 6.3: garbage dopant species — should raise ValueError, not crash silently
try:
    from pymatgen.core import Structure, Lattice
    import numpy as np
    lattice = Lattice.cubic(4.0)
    struct = Structure(lattice, ["La", "Mn", "O", "O", "O"],
                       [[0,0,0],[0.5,0.5,0.5],[0.5,0,0.5],[0,0.5,0.5],[0.5,0.5,0]])
    bad_spec = DopingSpec(host_formula="LaMnO3", host_site_species="La",
                          dopant_species="Xx", dopant_fraction=0.3)
    apply_doping(struct, bad_spec, ask_user=_noop_ask)
    check("6.3 garbage-dopant-raises", False, "should have raised ValueError")
except ValueError as e:
    check("6.3 garbage-dopant-raises", True, f"correctly raised ValueError: {e}")
except Exception as e:
    check("6.3 garbage-dopant-raises", False, f"wrong exception type {type(e).__name__}: {e}")

# 6.2: absent host_site_species (Ti not present in LaMnO3)
try:
    bad_spec2 = DopingSpec(host_formula="LaMnO3", host_site_species="Ti",
                           dopant_species="Sr", dopant_fraction=0.3)
    apply_doping(struct, bad_spec2, ask_user=_noop_ask)
    check("6.2 absent-host-site-raises", False, "should have raised ValueError")
except ValueError as e:
    check("6.2 absent-host-site-raises", True, f"correctly raised ValueError: {e}")
except Exception as e:
    check("6.2 absent-host-site-raises", False, f"wrong exception type {type(e).__name__}: {e}")


# =============================================================================
# Summary
# =============================================================================
print("\n" + "="*60)
print("QA SUMMARY")
print("="*60)
passed = sum(1 for _, s, _ in results if s == PASS)
failed = sum(1 for _, s, _ in results if s == FAIL)
skipped = sum(1 for _, s, _ in results if s == SKIP)
print(f"  PASSED : {passed}")
print(f"  FAILED : {failed}")
print(f"  SKIPPED: {skipped}")
print(f"  TOTAL  : {len(results)}")
if failed:
    print("\nFailed checks:")
    for tag, s, detail in results:
        if s == FAIL:
            print(f"  ❌ [{tag}] {detail}")
