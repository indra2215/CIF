# CIF Pipeline — QA & Logic-Correction Brief

**Purpose of this document**: hand this to whoever (or whatever) is reviewing the
`cif_pipeline/` codebase next. It lists every known risk area, a concrete test
input for each, what the code is *supposed* to do, and where in the code to look.
Work through it top to bottom, note pass/fail, and fix what fails — this doc is
meant to be actionable on its own without needing the full prior conversation.

**Package under review**: `cif_pipeline/` (Python package — `models.py`,
`user_interaction.py`, `classify.py`, `matching.py`, `search_external.py`,
`search_internal.py`, `filter_rules.py`, `generate.py`, `relax.py`, `doping.py`,
`orchestrator.py`, `main.py`).

**How to use this doc**: for each checkpoint below —
1. Run the listed test input(s) through the relevant function (or the full
   `run_pipeline()` if the checkpoint spans multiple stages).
2. Compare actual behavior to "Expected".
3. If it doesn't match, fix the logic in the file(s) named under "Code location".
4. Record the result using the template in Section 9.

Already-fixed issues are marked ✅ **FIXED** — re-verify them as regression
tests, don't assume they still hold after further changes.

---

## 1. Formula string parsing robustness

Users will not type clean, pymatgen-ready formula strings. Every row below should
be run through `classify.classify_compound` → `matching.normalize_formula_with_trace`
and confirm no unhandled exception reaches the caller.

| # | Input | Expected | Code location |
|---|---|---|---|
| 1.1 | `"la mno3"` (lowercase) | `Composition()` parsing fails — must be caught and surfaced as a clear "could not parse formula" result, not an unhandled exception or a 500-equivalent | `matching.py::normalize_formula_with_trace` (has a try/except — confirm it actually catches this case) |
| 1.2 | `"CO2"` vs `"Co2"` | These are chemically different (carbon dioxide vs. cobalt) — confirm the pipeline never silently treats one as the other; a case-sensitive typo should either parse as the (wrong) chemistry the user typed, or be flagged — decide and document which |
| 1.3 | `"La₀.₇Sr₀.₃MnO₃"` (Unicode subscript digits, common from paper copy-paste) | Almost certainly fails to parse as fractional — classification regexes and `Composition()` both assume ASCII digits. Confirm the failure is clean, and consider adding a Unicode-digit normalization pre-step | `classify.py`, `matching.py` |
| 1.4 | `"CuSO4·5H2O"` (hydrate, middle-dot U+00B7) | Confirm whether `Composition()` accepts this character or only a plain ASCII period; if not accepted, confirm a clean failure | `matching.py::normalize_formula_with_trace` |
| 1.5 | `"LaMnO3(s)"` (trailing state annotation) | Confirm this either parses correctly (stripping the annotation) or fails cleanly — should not silently produce a wrong formula | `matching.py` |
| 1.6 | `""`, `None`, `"   "` | Should be rejected at input validation, before classification even runs | `orchestrator.py::run_pipeline` (consider adding an explicit guard at the top) |
| 1.7 | `"XyZq123"` (nonsense) | Clean parse failure, no downstream crash | `matching.py::normalize_formula_with_trace` |
| 1.8 | `"LSMO"` (a real, common materials-science abbreviation for La0.7Sr0.3MnO3) | Not a valid formula string at all — `Composition()` will fail. Decide: should the pipeline recognize common abbreviations, or is failing and asking the user to spell it out acceptable? Document the decision either way | `classify.py` / new lookup table if abbreviation support is added |

---

## 2. The "1-x / x=" doping notation — known gap, not yet implemented

Real papers overwhelmingly write doped formulas as **`La1-xSrxMnO3, x=0.3`**, not
`La0.7Sr0.3MnO3`. The current classifier has no logic to parse this algebraic form
into a `DopingSpec`.

| # | Input | Expected today | Action needed |
|---|---|---|---|
| 2.1 | `"La1-xSrxMnO3, x=0.3"` | Fails to parse as a formula at all (the literal `x` isn't a valid coefficient) — will fall through to failure or an unhelpful `ask_user` | **This is a real feature gap.** Add a dedicated regex/parser in `classify.py` that recognizes the `A(1-x)B(x)...` pattern plus a separate `x=<value>` clause, and constructs the equivalent decimal formula (`La0.7Sr0.3MnO3`) before handing off to the existing classification logic |
| 2.2 | `"La(1-x)SrxMnO3"` (no `x=` value given at all) | Should trigger an explicit `ask_user` for the value of `x`, not fail silently | Same as above — once the pattern is recognized, missing `x` value should route into the existing "ask for dopant fraction" flow |

---

## 3. Space group input normalization — known gap

| # | Input | Expected today | Action needed |
|---|---|---|---|
| 3.1 | User requests space group `"62"` (International Tables number) for a compound whose database entry stores `"Pnma"` (Hermann-Mauguin symbol) | Currently a **plain string comparison** in `matching.disambiguate_polymorphs` — `"62" != "Pnma"` even though they're the same space group, causing a false "no match" | Add a normalization step (e.g. via `pymatgen.symmetry.groups.SpaceGroup` to convert between number and symbol) before comparing, in `matching.py` |
| 3.2 | `"P n m a"` (spaces) vs `"Pnma"` vs `"pnma"` (case) requested for the same actual space group | Same false-mismatch risk from formatting differences alone | Normalize whitespace/case before comparison in `matching.py::disambiguate_polymorphs` |
| 3.3 | Same space group number, different origin choice (e.g. `Fd-3m` origin 1 vs. origin 2) | Even a normalized number/symbol match doesn't guarantee the same structural setting — decide whether this distinction matters for your use case and document it | `matching.py` (design decision, not just a bug) |

---

## 4. Doping phrasing beyond the basic cases already tested

| # | Input | Expected | Code location |
|---|---|---|---|
| 4.1 | `"doped with strontium"` (full element name, not symbol) | The regex only matches symbol-shaped tokens (`[A-Z][a-z]?`) — "Strontium" won't match. Confirm this correctly falls through to `ask_user` for the dopant rather than silently failing to detect doping at all | `classify.py::_parse_doping_phrase` |
| 4.2 | `"heavily doped LaMnO3"` (vague qualifier, no element named) | Detects "doped" but extracts no valid dopant symbol → should ask the user, not crash | `classify.py::classify_compound` |
| 4.3 | `"n-type doped Si"` (semiconductor terminology, no dopant named) | Same as above — correctly detects doping intent, must ask for the missing dopant | `classify.py` |
| 4.4 | `"La0.7Sr0.3MnO3 co-doped with Ca"` (three species, two dopants) | `DopingSpec` only models one dopant on one site. Confirm this fails/flags cleanly (e.g. "co-doping not supported") rather than silently picking one dopant and dropping the other | `classify.py`, `doping.py` — currently **not handled**, needs an explicit guard that detects >1 non-host fractional species and refuses cleanly instead of mis-building a `DopingSpec` |

---

## 5. Boundary and contradictory input values

| # | Input | Expected | Code location |
|---|---|---|---|
| 5.1 | `dopant_fraction = 0.0` | Not really doping (pure host) — decide whether to reject this at `DopingSpec` construction with a clear message, or silently treat it as undoped | `classify.py::classify_compound` |
| 5.2 | `dopant_fraction = 1.0` | Full substitution, arguably not "doping" either — same decision needed | `classify.py::classify_compound` |
| 5.3 | `is_doped_hint=True` explicitly set on a `CompoundQuery` whose formula is obviously undoped (e.g. `"LaMnO3"`) | Explicit hint currently wins outright. Confirm the pipeline then correctly asks for host/dopant/site/fraction (since the formula gives no clue), rather than failing confusingly | `classify.py::classify_compound` |
| 5.4 | `"La-0.3Sr1.3MnO3"` (negative coefficient, likely a typo) | Should fail formula parsing cleanly, never propagate a nonsensical structure downstream | `matching.py` |

---

## 6. Crash risks in doping logic

| # | Test | Expected | Code location |
|---|---|---|---|
| 6.1 ✅ FIXED | `host_site_species` set to a typo/garbage value (e.g. `"Xx"`) that matches no real site in the parent structure | Previously raised an **uncaught `ValueError`**, crashing the whole `run_pipeline()` call. Now caught in `orchestrator.py` and converted into a clean `PipelineResult(source="failed", notes=...)`. **Re-verify this still holds after any further changes to `doping.py` or `orchestrator.py`.** | `orchestrator.py` (try/except around `apply_doping_with_validation`), `doping.py::apply_doping` |
| 6.2 | Parent structure has zero sites of the requested `host_site_species` at all (not a typo — genuinely absent element) | Same code path as 6.1 — confirm the message clearly says the species wasn't found, not a generic error | `doping.py::apply_doping` |
| 6.3 | Parent structure has the host species, but the requested `dopant_species` is not a valid element symbol at all (garbage string) | `apply_doping` builds `{host: 1-frac, dopant: frac}` without validating `dopant_species` against pymatgen's `Element` list first — confirm what happens with a garbage dopant string; add validation if it currently produces a broken/unparseable structure | `doping.py::apply_doping` |

---

## 7. Structural sanity assumptions not yet enforced

| # | Risk | Expected / action needed | Code location |
|---|---|---|---|
| 7.1 | A CrystaLLM candidate intended to be **undoped** comes back with an accidental disordered/partial-occupancy site | Nothing currently checks that an undoped-generation candidate is fully ordered. Add an explicit "no disordered sites" check to `filter_candidates` for the undoped path | `filter_rules.py::filter_candidates` |
| 7.2 | MACE relaxation produces `NaN`/`inf` energy, or the optimizer diverges rather than just failing to converge within `max_steps` | `relax_with_mace`'s convergence check currently only compares `max_force <= fmax` — a diverging or NaN run isn't distinguished from "needs more steps." Add an explicit NaN/inf/divergence guard once the real MACE call is wired in | `relax.py::relax_with_mace` |
| 7.3 | A generated candidate produces an unreasonably large cell/supercell | No size or timeout guard exists before handing a structure to MACE. Add a sanity check (e.g. max atom count) once real relaxation is wired in, to avoid pathological runtimes | `relax.py`, `filter_rules.py::filter_candidates` |

---

## 8. Sequencing / consistency checks (not single-input bugs, but flow correctness)

| # | Test | Expected |
|---|---|---|
| 8.1 | Submit the exact same compound twice in a row (after internal DB is wired up and the first run's result has been stored there) | Second call should be satisfied entirely by Stage 2 (internal DB) — confirm Stage 1 (external) still runs first as designed, and that generation is never re-triggered for something now stored internally |
| 8.2 | A compound that has entries in both external and internal DBs simultaneously | Confirm Stage 1 short-circuits correctly (returns before Stage 2 is ever queried) — this is about call-order correctness, not data correctness |
| 8.3 | Every `ask_user` call in a multi-question flow (e.g. building a full `DopingSpec` from scratch) is declined one at a time | Confirm the pipeline stops cleanly at the *first* missing required piece rather than continuing to ask for the rest and then failing confusingly at the end |

---

## 9. Reporting template

For each checkpoint tested, record:

```
Checkpoint #: 
Input tested: 
Expected: 
Actual: 
Pass / Fail: 
Fix made (file + description, if Fail): 
```

Group fixes by file when reporting back, so changes to `classify.py`,
`matching.py`, `doping.py`, `filter_rules.py`, `relax.py`, and `orchestrator.py`
can each be reviewed as a coherent diff rather than scattered edits.

---

## 10. Priority order

1. **Section 1** (basic parsing robustness) and **Section 6** (doping crash
   risks) first — these are "does it fall over" tests and the cheapest to fix.
2. **Section 4 and 5** (doping phrasing/boundary values) next — highest
   input-variability surface area.
3. **Section 2 (`x=` notation)** and **Section 3 (space group normalization)**
   are real feature gaps, not bugs — decide now whether to build them before
   broader testing, since real users will hit both constantly.
4. **Section 7** (structural sanity) only becomes testable once MACE/CrystaLLM
   are actually wired in — flag as deferred until then, but don't forget it.
5. **Section 8** (sequencing) last, once Sections 1–6 pass reliably.

---

## 11. Database coverage, relaxation-correctness, and file-integrity checkpoints

*(Note on this section: it started from a voice-transcribed request, so "Kavati"
below is read as **COD (Crystallography Open Database)** and "amazing relaxation"
as **MACE relaxation** — flagging the interpretation here in case it's wrong.)*

These ten checks focus on three things the earlier sections didn't cover in
depth: whether external databases actually return *useful* coverage (not just
zero-vs-one), whether relaxation is doing real physical work (not silently a
no-op), and whether the final CIF file is genuinely valid and portable.

| # | Checkpoint | Test | Expected | Code location |
|---|---|---|---|---|
| 11.1 | Does a database return more than 2 entries for a real polymorphic compound? | Query `SiO2` and `TiO2` against COD specifically (COD's coverage is mineral-heavy and inconsistent) | At least one of these should return >2 verified matches through `_query_cod`, exercising the polymorph-disambiguation path rather than just the 0-or-1-match path | `search_external.py::_query_cod`, `matching.py::disambiguate_polymorphs` |
| 11.2 | Incomplete/"unknown" fields *within* a returned structure (not a missing compound — a structure that came back but is missing data) | Manually construct a `SearchMatch` with `space_group=None` and `formula=None` and pass it through `disambiguate_polymorphs` and `doped_formulas_match` | Neither function should crash — both should treat missing fields as "unknown" and degrade gracefully (e.g. fall into the "ask user" path rather than raising) | `matching.py::disambiguate_polymorphs`, `matching.py::doped_formulas_match` |
| 11.3 | Relaxation actually moves atoms | Manually perturb a known-good CIF's atom positions by ~0.3 Å, then run it through `relax_with_mace`; compare pre- and post-relaxation coordinates directly | Relaxed coordinates should differ meaningfully from the perturbed input — this is a positive control proving the relaxation call does real work | `relax.py::relax_with_mace` |
| 11.4 | Doping correctness, checked as a set (site + occupancy + bond lengths together, not separately) | Run `La0.7Sr0.3MnO3` end to end | (a) correct La site chosen, (b) `DopedStructureValidation.occupancy_sum` within tolerance of 1.0, (c) both La–O and Sr–O bond lengths individually reasonable (not just averaged/masked by each other) | `doping.py::apply_doping_with_validation`, `filter_rules.py::validate_doped_structure` |
| 11.5 | **Post-relaxation non-transformation** — does the compound come out identical to what went in, and if so, why | Run (i) a genuinely novel composition (nothing in any database) through generation+relaxation, and (ii) the perturbed structure from 11.3 as a positive control | Case (i) should essentially never be identical pre/post-relaxation. Case (ii) MUST move — if it doesn't, relaxation itself is broken (e.g. a stub, zero-step optimizer, or misconfigured force calculator), not a property of the compound. Also check `max_force` *before* optimization starts — if it's already ~0 pre-optimization, that's the real signal something upstream is wrong | `relax.py::relax_with_mace` |
| 11.6 | CIF file format validity ("test 1 about the file") | Round-trip every output CIF: write the string, re-parse with `Structure.from_str(..., fmt="cif")` | No exception, no site-count mismatch; confirm required blocks are present (`_cell_length_a/b/c`, `_cell_angle_alpha/beta/gamma`, a `_symmetry`/`_space_group` block, complete `_atom_site_*` loop) | Wherever `.to(fmt="cif")` is called — `generate.py`, `doping.py`, `orchestrator.py` |
| 11.7 | CIF file actually imports/renders in external tools | Open at least one relaxed CIF from each source (existing-database CIF as a control, freshly-generated-and-relaxed as the real risk case) in an external viewer (VESTA, OVITO, or similar) | Both should open cleanly — this catches formatting pymatgen tolerates but stricter external parsers reject (missing `data_` header, non-standard symmetry-operator formatting) | CIF-writing step in `generate.py`/`doping.py`/`orchestrator.py` |
| 11.8 | Record ID stability across repeated queries | Query the same compound twice in separate runs | Same database record ID returned both times — not a different "equally valid" polymorph each time due to unstable result ordering | `search_external.py`, `search_internal.py` |
| 11.9 | Space group consistency between pipeline reasoning and the saved file | Compare the space group in `PipelineResult.notes` (from `determine_final_space_group`) against a fresh, independent `SpacegroupAnalyzer` run on the actual saved CIF file on disk | Must match exactly — confirms the file written to disk is the same structure the pipeline reasoned about, not a stale/mismatched version | `relax.py::determine_final_space_group`, file-writing step |
| 11.10 | Doped CIF round-trip preserves partial occupancy | Write a doped+relaxed CIF, re-parse it, check the disordered site's occupancy fractions | Fractions must survive exactly (e.g. `La: 0.7, Sr: 0.3`) — not silently rounded to 1.0 or dropped. This is a known CIF-writer footgun | `doping.py`, CIF-writing step |

### Priority within this section
Do **11.5 and 11.3 together first** — they're a matched pair (positive control
+ real case) and the single most important pair in this whole document, since a
silently-broken relaxation step would make every other check in this file
meaningless (a candidate that "passes" filtering and "converges" instantly
because nothing actually moved is not a validated structure). Then **11.6/11.7**
(file integrity) before trusting any output for external use. **11.1/11.2**
(database coverage/missing-field handling) and **11.8/11.9** (stability/
consistency) can follow once the core pipeline is confirmed to be doing real
work. **11.4/11.10** (doping-specific) last, since they depend on 11.3–11.5
already being verified correct.
