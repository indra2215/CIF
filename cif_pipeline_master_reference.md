# Compound → CIF Pipeline: Master Reference

This is the single up-to-date reference for the pipeline — supersedes the two earlier
planning docs. It covers: what the workflow does, exactly what's in the zip, every
edge case handled (and why), what's still stubbed/left to do, and a full evaluation
checklist mapped to every edge case below.

---

## 1. Goal

Given a compound (a formula, optionally described in plain text), return a validated,
relaxed `.cif` — reusing an existing structure wherever one already exists, and only
generating a new one via ML once existence has been genuinely ruled out. Doped and
undoped compounds are treated as fundamentally different problems from the first step.

---

## 2. Full Workflow

```
 CompoundQuery
      │
      ▼
 Stage 0: Classify doped vs undoped (classify.py)
      │  multi-level: UI hint → phrasing → formula-shape → ask user
      ▼
 Stage 0b: Normalize + trace (matching.py)
      │  undoped → reduce formula (record multiplicity)
      │  doped   → do NOT reduce (record match-rule explanation)
      ▼
 Stage 1: Search external DBs — MP, OQMD, JARVIS, COD (search_external.py)
      │  same normalization + polymorph rules for every source
      ▼ found? ──yes──► return existing_<SOURCE> + full candidate list
      │ no
      ▼
 Stage 2: Search internal DB (search_internal.py) — identical rules to Stage 1
      │
      ▼ found? ──yes──► return existing_internal + full candidate list
      │ no
      ▼
 Stage 3: Confirmed not found anywhere
      │
      ├─ undoped ──► Stage 4a: generate.py feedback loop
      │                 CrystaLLM → filter_rules.filter_candidates → MACE relax
      │                 → determine final space group POST-relaxation
      │                 → loop with diagnostics until converged or max_iterations
      │
      └─ doped ────► Stage 4b: doping.py
                        resolve parent (Stages 1–2 again, or Stage 4a if needed)
                        → apply_doping (explicit site selection)
                        → validate_doped_structure (SEPARATE from filter_candidates)
                        → MACE relax
                        → PipelineResult
```

---

## 3. What's In The Zip (`cif_pipeline/`)

| File | What it does | Status |
|---|---|---|
| `models.py` | All shared dataclasses: `CompoundQuery`, `DopingSpec`, `SearchMatch`, `GenerationDiagnostics`, `PipelineResult`, `NormalizationTrace`, `FormulaMultiplicityInfo`, `DopedStructureValidation` | **Complete** |
| `user_interaction.py` | `ask_user` abstraction — CLI implementation provided, web-form callback stubbed | CLI done, web hook **TODO** |
| `classify.py` | Multi-level doped/undoped classification, validated dopant extraction, `DopingSpec` construction | **Complete logic** |
| `matching.py` | Formula normalization (+ trace), doped-tolerance matching, polymorph disambiguation (never drops candidates) | **Complete logic** |
| `search_external.py` | Stage 1 — MP / OQMD / JARVIS / COD | Orchestration **complete**, all 4 database calls **stubbed** (TODO) |
| `search_internal.py` | Stage 2 — your DB, same rules as Stage 1 | Orchestration **complete**, DB call **stubbed** (TODO) |
| `filter_rules.py` | Compound-type classification + `filter_candidates` (batch, undoped) + `validate_doped_structure` (single, doped) | **Complete logic** |
| `generate.py` | Stage 4a feedback loop; post-relaxation space-group determination and disambiguation | **Complete logic**, CrystaLLM call **stubbed** (TODO) |
| `relax.py` | MACE-MP-0 wrapper + `determine_final_space_group` | **Complete logic**, actual MACE call **stubbed** (TODO) |
| `doping.py` | Parent resolution, site selection, `apply_doping_with_validation` | **Complete logic** |
| `orchestrator.py` | `run_pipeline()` — ties every stage together | **Complete** |
| `main.py` | Example usage | **Complete** |
| `README.md` | Architecture + design-decision writeup | **Complete**, kept in sync with this doc |

"Complete logic" means: the decision-making, data flow, and edge-case handling are
fully implemented and unit-testable today with mocked data. The four marked **TODO**
items are external system calls (real API/model credentials) — see Section 5.

---

## 4. Edge Cases — Full Catalogue

### 4.1 Classification (doped vs. undoped)

| Edge case | Handling | Where |
|---|---|---|
| Explicit "undoped" phrasing | Regex previously matched "Un" out of "**Un**doped" as a fake dopant — **fixed**: every extracted token is validated against pymatgen's real element list; `Element("Un")` raises, so it's rejected and the text correctly falls through to "not doped" | `classify.py::_parse_doping_phrase` |
| "X doped with Y%" phrasing | Percentage captured from the same regex match as the dopant symbol (not a separate global search that could grab an unrelated percentage elsewhere in the text) | `classify.py::_WITH_RE` etc. |
| Non-stoichiometric but genuinely undoped compound (e.g. `Fe0.95O`) | A single fractional species alone does **not** count as doping — `_looks_like_classic_doping_pattern` requires **two** fractional species whose amounts sum to ~1 on one site | `classify.py::_looks_like_classic_doping_pattern` |
| Genuinely ambiguous fractional formula | Falls through to `ask_user` with an explicit three-way choice (doped / non-stoichiometric-undoped / not sure) rather than guessing | `classify.py::classify_compound` |
| `DopingSpec` fields missing after classification | Each missing field (host formula, dopant element, host site species, dopant fraction) is asked for individually; if the user declines any of them, `spec` stays `None` and the orchestrator returns a `failed` result with a clear reason rather than proceeding with a half-built spec | `classify.py::classify_compound`, `orchestrator.py` |
| User declines to answer classification question | `ask_user` returning `None` maps to `is_doped=False` fallback via the "Not sure - treat as undoped" default path | `classify.py::classify_compound` |

### 4.2 Formula normalization & multiplicity

| Edge case | Handling | Where |
|---|---|---|
| Same undoped compound, different formula-unit scaling (`Fe2O3` vs `Fe4O6`) | Reduced via `Composition.get_reduced_composition_and_factor()`; the multiplier and reasoning are recorded in `FormulaMultiplicityInfo`, not silently discarded | `matching.py::normalize_formula_with_trace` |
| Doped formula reduction risk | Doped formulas are **never** reduced — reducing risks distorting the host/dopant ratio. Matching instead computes the ratio directly from raw atom counts | `matching.py::doped_formulas_match` |
| Doped compound stored in scaled-integer form in a database (`La7Sr3Mn10O30` vs `La0.7Sr0.3MnO3`) | Fixed bug: matching previously only compared amounts that were themselves non-integer, missing integer-form entries entirely. Now computes `amt_dopant / (amt_dopant + amt_host)` directly, which is scale-invariant regardless of representation | `matching.py::doped_formulas_match` |
| Unparseable formula string | `normalize_formula_with_trace` catches the exception, falls back to using the raw string as-is, and records that in the trace's `reason` field rather than crashing | `matching.py::normalize_formula_with_trace` |

### 4.3 Existing-structure search & polymorphs

| Edge case | Handling | Where |
|---|---|---|
| Multiple structural hits for one formula (polymorphs), no space group requested | **All** distinct hits are returned from search; if there's more than one distinct space group among them, all are surfaced via `ask_user` (capped at 10 per screen for UI readability only — the full list and true count are always available) | `matching.py::disambiguate_polymorphs` |
| Space group requested, single hit that doesn't match it | Fixed bug: old code checked `len(matches)==1` *before* checking the requested space group, so a single wrong-polymorph hit could be silently accepted. Now the space-group check always runs first | `matching.py::disambiguate_polymorphs` |
| Space group requested, none of the hits match | Surfaced as an explicit mismatch (`ask_user` with all mismatched options + "generate fresh" option) — never silently substituted | `matching.py::disambiguate_polymorphs` |
| Same formula, same (or unknown) space group across all hits | Collapsed to a single match automatically — no need to ask when there's nothing to disambiguate | `matching.py::disambiguate_polymorphs` |
| User declines to pick among ambiguous polymorphs (or a web-callback hasn't resolved) | `PipelineResult(source="ambiguous_polymorphs", candidate_matches=[...])` — a distinct, inspectable state, not a silent failure or an arbitrary pick | `orchestrator.py::run_pipeline` |
| Doped-compound search, no `DopingSpec` resolved yet | Search still runs on the raw formula unfiltered (best-effort); since `DopingSpec` is `None` there's nothing to tolerance-filter against, so results are effectively "found by raw string" only — acceptable because the pipeline will fail clearly later at the doping stage if this doesn't produce a usable answer | `search_external.py`, `search_internal.py` |
| COD text-search returning a false-positive metadata match | COD's documented API has no formula param — text search can match unrelated compounds sharing keywords. **Mandatory verification step**: every candidate's actual fetched CIF is parsed and its `reduced_formula` is checked against the search formula before acceptance | `search_external.py::_query_cod` docstring |

### 4.4 Stage 4a — undoped generation loop

| Edge case | Handling | Where |
|---|---|---|
| No candidates parse from CrystaLLM output | Counted separately as `n_parse_failed`; doesn't crash the loop, just reduces `n_survived` for that iteration | `filter_rules.py::filter_candidates` |
| Candidates with unphysical bond lengths | Compound-type-aware tolerance (oxide/halide/sulfide/nitride/phosphide/carbide/intermetallic/other), not one universal threshold | `filter_rules.py::check_bond_lengths` |
| Candidates that don't resolve to a sensible space group at all (pre-relaxation) | Caught by `check_symmetry`, counted in diagnostics | `filter_rules.py::check_symmetry` |
| Charge-balance check meaningless for intermetallics | Explicitly skipped for `intermetallic`/`other` compound types rather than producing false failures | `filter_rules.py::filter_candidates` |
| Duplicate candidates within one generation batch | Deduplicated via `StructureMatcher` before relaxation, avoiding wasted relaxation compute | `filter_rules.py::filter_candidates` |
| Zero candidates survive filtering | Loop inspects the dominant failure mode (bond length / duplicate / symmetry / charge balance) and adjusts temperature or notes the appropriate strategy before retrying, rather than blindly resampling with identical parameters | `generate.py::_decide_next_adjustment` |
| Candidates pass filtering but none relax to convergence | Counted as `n_relax_nonconverged`, feeds into the same adjustment logic | `generate.py::generate_with_feedback_loop` |
| **MACE does not decide the space group** | This was a live misunderstanding worth stating explicitly: MACE only relaxes atoms/cell to a local energy minimum. The actual space group of each candidate is determined **after** relaxation via `SpacegroupAnalyzer`, never trusted from a pre-relaxation label, since relaxation can shift atoms enough to reveal a different effective symmetry | `relax.py::determine_final_space_group` |
| Target space group given, but converged candidates relax into a different one | Only candidates whose **post-relaxation** space group actually matches count; others don't silently substitute — that iteration is treated as "no match" and retried | `generate.py::generate_with_feedback_loop` |
| No target space group given, converged candidates split across multiple distinct final space groups | Same "surface everything" policy as database polymorphs: all distinct space groups (with candidate counts and best energy per group) are presented via `ask_user`, with a "just use lowest energy overall" explicit option — never a silent cross-symmetry energy comparison | `generate.py::generate_with_feedback_loop` |
| `max_iterations` exhausted with no converged candidate | Returns `source="failed"` with the **complete** `diagnostics_history` across every iteration attached, so the failure is fully inspectable rather than a bare "didn't work" | `generate.py::generate_with_feedback_loop` |

### 4.5 Stage 4b — doped generation

| Edge case | Handling | Where |
|---|---|---|
| Parent (undoped host) not found in any database | Falls through to generating the parent via Stage 4a first, using the same feedback loop as any undoped compound | `doping.py::resolve_parent_structure` |
| Parent structure has multiple symmetry-distinct sites matching the requested host species | `ask_user` presents each candidate site with its fractional coordinates; if declined, defaults to the first candidate with an explicit warning logged (never silently assumed without a trace) | `doping.py::apply_doping` |
| Doped-site occupancy doesn't sum to ~1.0 after substitution | New, doping-specific check — flagged via `DopedStructureValidation.occupancy_sum_ok`; this is exactly the kind of construction error that needs to be caught before relaxation, not after | `filter_rules.py::validate_doped_structure` |
| Applying full undoped `filter_candidates` logic to a doped structure | Deliberately **not done** — a single already-built doped structure needs a different, smaller check (no batch dedup, no full symmetry re-check since the parent was already validated, occupancy-sum check that doesn't apply to undoped candidates, bond-length check scoped only to the doped site's neighbors) | `filter_rules.py::validate_doped_structure` vs. `filter_candidates` |
| Doped structure fails validation | Still returned (for inspection) but explicitly flagged in `PipelineResult.notes` as not to be treated as final, with `doped_validation` attached showing exactly what failed | `orchestrator.py::run_pipeline` |
| Doped structure passes validation but relaxation doesn't converge | Returned with a clear convergence warning in `notes`, distinct from the validation-failure case | `orchestrator.py::run_pipeline` |
| `DopingSpec` never fully resolved (user declined required info) | Orchestrator returns `source="failed"` immediately rather than attempting doping with incomplete information | `orchestrator.py::run_pipeline` |

### 4.6 Data-source-specific

| Edge case | Handling | Where |
|---|---|---|
| COD has no documented `formula=` search parameter | Documented two safe approaches instead of guessing an undocumented param: (1) text search + mandatory CIF-formula verification, (2) bulk MySQL dump for heavy repeated use | `search_external.py::_query_cod` |
| COD entries marked theoretical/predicted vs. experimentally measured | `SearchMatch.is_theoretical` flag carried through for UI display, so a predicted structure is never presented indistinguishably from a measured one | `models.py::SearchMatch` |
| Every source needs a UI-visible link back to the record | `SearchMatch.source_url` populated per-source (MP materials page, COD entry page, etc.); internal records should link back into your own UI | `search_external.py`, `search_internal.py` docstrings |

### 4.7 Transparency / UI-facing state

| Edge case | Handling | Where |
|---|---|---|
| Normalization happening invisibly | Every result carries a `NormalizationTrace` (raw input, search key used, doped/undoped reasoning, multiplicity info) meant to be rendered directly, not inferred by the user | `models.py::NormalizationTrace`, `orchestrator.py` |
| "Where did this CIF come from" | `PipelineResult.matched_record` for existing structures; `source` field distinguishes every possible origin (`existing_MP` / `existing_OQMD` / `existing_JARVIS` / `existing_COD` / `existing_internal` / `generated_undoped` / `generated_doped` / `ambiguous_polymorphs` / `failed`) | `models.py::PipelineResult` |

---

## 5. What's Still Not Done

### 5.1 External wiring (marked `TODO` in code, all logic around them is complete)

| Piece | What's needed |
|---|---|
| Materials Project | `pip install mp-api`, API key, implement in `search_external.py::_query_materials_project` |
| OQMD | Implement the REST call in `search_external.py::_query_oqmd` |
| JARVIS | `pip install jarvis-tools`, implement in `search_external.py::_query_jarvis` |
| COD | Implement the documented text-search-then-verify (or bulk-dump) approach in `search_external.py::_query_cod` |
| Internal database | Wire `search_internal.py::_query_internal_db` to your actual backing store |
| CrystaLLM | Install/checkpoint, implement `generate.py::_call_crystallm` |
| MACE-MP-0 | `pip install mace-torch ase`, checkpoint, implement `relax.py::relax_with_mace` |
| Web-form clarification UI | Replace `user_interaction.default_cli_ask` with a real callback into your web interface's form/modal system (this single hook is what every `ask_user` call in the whole pipeline routes through) |

### 5.2 Open design decisions (not bugs — deliberate scope limits, worth deciding on purpose)

- **Co-doping / multi-site doping**: `DopingSpec` currently assumes exactly one dopant on exactly one site type. Not implemented.
- **Bond-length rule granularity**: currently per-compound-type with generic tolerance multipliers, not per-element-pair. Worth tightening once real generation output shows where the generic rules are too loose/strict.
- **Generation adjustment strategy**: `_decide_next_adjustment` currently only tunes sampling temperature. Other knobs (sample count, space-group-conditioned prompting if CrystaLLM supports it, rejection sampling) are noted but not automated.
- **Classification heuristic reliability**: the "two fractional amounts summing to ~1" pattern is a reasonable first pass, not validated against a large set of real user-submitted formulas yet.
- **Post-relaxation symmetry tolerance (`symprec`)**: currently a fixed `0.1` default in `determine_final_space_group` and `check_symmetry`. Whether that's the right tolerance for your specific compound classes should be checked against known structures before trusting the automated symmetry labels.

---

## 6. Evaluation Checklist

Each item maps directly to an edge case in Section 4. Recommended order: classification → normalization → search/polymorphs → generation → doping → data sources.

**Classification**
- [ ] `"LaMnO3"` → undoped, no clarification asked
- [ ] `"La0.7Sr0.3MnO3"` → doped, dopant `Sr`, host site `La`, fraction `0.3` (via formula-shape heuristic)
- [ ] `"Sr-doped LaMnO3"`, `"LaMnO3 doped with Sr"`, `"LaMnO3 doped with 30% Sr"`, `"Sr doping of LaMnO3"` → all correctly extract dopant `Sr` (and fraction where stated)
- [ ] `"this is an undoped sample of LaMnO3"` → **not** doped (regression test for the "Un" bug)
- [ ] `"Fe0.95O"` → does **not** auto-classify as doped; triggers the ambiguous-fraction clarification question
- [ ] Declining every `ask_user` prompt during `DopingSpec` construction → pipeline returns `source="failed"` with a clear reason, never proceeds with a partial spec

**Normalization & multiplicity**
- [ ] `"Fe4O6"` search key resolves to `"Fe2O3"`, and `NormalizationTrace.multiplicity.multiplier == 2`
- [ ] `"La0.7Sr0.3MnO3"` search key stays exactly as typed (not reduced)
- [ ] A doped compound stored in a database as `"La7Sr3Mn10O30"` is still correctly matched by `doped_formulas_match` against a `DopingSpec` with `dopant_fraction=0.3`

**Search & polymorphs**
- [ ] A formula with exactly one database hit whose space group does NOT match a requested space group → surfaced as a mismatch, not silently returned
- [ ] A formula with 3 hits, all the same space group → auto-collapsed, no clarification asked
- [ ] A formula with hits split across 2+ distinct space groups, no space group requested → all surfaced via `ask_user`, `PipelineResult.candidate_matches` contains every hit
- [ ] A formula with 15+ polymorph hits → UI picker shows 10 with an accurate "5 more not shown" note, `candidate_matches` still contains all 15

**Generation loop**
- [ ] A composition where the first generation batch entirely fails bond-length checks → temperature increases before the next iteration (check `diagnostics_history[0].notes`)
- [ ] A composition that converges but candidates land in 2 different final space groups, no target requested → `ask_user` is called with both groups listed and energies shown
- [ ] Same case, but a target space group WAS requested and matches one of the groups → that group's best candidate is returned automatically, no `ask_user` call
- [ ] Same case, target space group requested but matches NEITHER converged group → iteration is treated as failed, loop retries
- [ ] A composition that never converges within `max_iterations` → `source="failed"`, full diagnostics history present across all iterations

**Doping**
- [ ] A known doped compound with a known, single-site parent → produces a validated, relaxed doped CIF with no clarification needed
- [ ] A parent with 2+ symmetry-distinct sites matching the host species → `ask_user` called with fractional coordinates for each
- [ ] A doping substitution constructed with a deliberately wrong occupancy sum (e.g. by manually breaking `DopingSpec.dopant_fraction`) → `DopedStructureValidation.occupancy_sum_ok == False`, surfaced clearly rather than silently passed through to relaxation

**Data sources**
- [ ] A COD text-search hit that does NOT actually match the requested formula in its fetched CIF → rejected during verification, not returned as a match
- [ ] A COD entry marked theoretical → `is_theoretical=True` visible in the returned `SearchMatch`

**UI/transparency (once wired to a real frontend)**
- [ ] Every `PipelineResult` with `source` starting with `existing_` renders a source badge + link from `matched_record.source_url`
- [ ] Every result renders the `normalization_trace` in some human-readable form (not just internally used)
- [ ] `source="ambiguous_polymorphs"` and `source="failed"` are visually distinct states in the UI, not both rendered as generic errors
