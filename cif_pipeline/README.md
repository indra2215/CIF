# Compound -> CIF Pipeline

## Files

| File | Responsibility |
|---|---|
| `models.py` | Shared dataclasses: `CompoundQuery`, `DopingSpec`, `SearchMatch`, `GenerationDiagnostics`, `PipelineResult` |
| `user_interaction.py` | Swappable "ask the user a clarifying question" hook (CLI stub + web-form stub) |
| `classify.py` | Doped vs. undoped classification, multi-level with user fallback; builds `DopingSpec` |
| `matching.py` | Formula normalization rules (doped vs. undoped) + polymorph disambiguation, shared by both search stages |
| `search_external.py` | Stage 1 — Materials Project / OQMD / JARVIS / COD search |
| `search_internal.py` | Stage 2 — your internal database search (same matching rules as Stage 1) |
| `filter_rules.py` | Compound-type classification (oxide/halide/sulfide/.../intermetallic) + type-aware bond length, symmetry, charge-balance checks |
| `generate.py` | Stage 4a — CrystaLLM generation as a **feedback loop**: generate → filter → relax → diagnose failures → adjust params → retry |
| `relax.py` | MACE-MP-0 relaxation wrapper with convergence diagnostics |
| `doping.py` | Stage 4b — parent structure resolution + explicit site-level doping transformation |
| `orchestrator.py` | Ties every stage together into `run_pipeline()` |
| `main.py` | Example usage |

## Key design decisions (per your questions)

**Normalization:** undoped formulas are reduced to a canonical form before
searching (`Fe2O3` == `Fe4O6`). Doped formulas are **never** reduced — the
fractional site occupancy (`La0.7Sr0.3`) is the chemically meaningful part of
the query, so doped-formula matching instead compares (host species, dopant
species, dopant fraction) with a tolerance, in `matching.doped_formulas_match`.
Every normalization decision is captured in a `NormalizationTrace` object
(`models.py`) attached to `PipelineResult.normalization_trace` — this is what
the UI should render so the user can see exactly what search key was used and
why, including the formula-unit multiplicity relationship for undoped
compounds (`FormulaMultiplicityInfo` — e.g. "you entered Fe4O6, that's 2x the
reduced formula Fe2O3").

**Polymorphs:** all four external sources (MP, OQMD, JARVIS, COD) plus the
internal DB return **every** structural hit for a formula, never just the
first. When no space group is specified and multiple distinct polymorphs
exist, the policy is: **surface all of them, never silently narrow to
"some"** — `matching.disambiguate_polymorphs` returns the complete candidate
list alongside whatever was chosen, and `PipelineResult.candidate_matches`
carries that full list to the UI even when a pick was made, so a picker can
always be rendered with the true total count (with a `MAX_POLYMORPHS_SHOWN_AT_ONCE`
cap purely for single-screen readability — the underlying list is never
truncated). A requested space group is honored strictly: if none of the hits
match it, that's surfaced as a mismatch requiring a decision, not silently
substituted.

**Provenance for the UI:** every `SearchMatch` now carries a `source_url`
(direct link to the record) and `is_theoretical` flag (COD marks some entries
as theoretical/predicted rather than measured) — `PipelineResult.matched_record`
tells the UI exactly which record was used and where it came from.

**Classification is multi-level** (`classify.py`): explicit UI hint → explicit
"X doped with Y" phrasing → formula-shape heuristic (co-occupying fractional
sites) → if still ambiguous, ask the user, with a "not sure" default to undoped.

**Filtering is compound-type-aware AND split by pipeline stage**
(`filter_rules.py`): `filter_candidates()` is used only for raw, freshly-generated
undoped candidates in Stage 4a (batch: bond-length + symmetry + charge-balance +
duplicate checks). `validate_doped_structure()` is a deliberately separate,
smaller function for Stage 4b: no deduplication (there's one structure, not a
batch), no full symmetry re-check (the parent was already validated; a
disordered doped site makes a full space-group re-check a different and less
meaningful question), a new occupancy-sum check (host_fraction + dopant_fraction
should sum to ~1.0), and bond-length checking scoped to just the doped site's
neighbors, since that's the only part of the structure that changed.

**Generation is a real loop, not one shot** (`generate.py`): each iteration's
filter + relax failures are inspected, and the dominant failure mode (bad
bonds vs. duplicates vs. non-convergence) drives a parameter adjustment
(mainly temperature right now — extend `_decide_next_adjustment` as you learn
more about your CrystaLLM build's behavior) before the next batch of samples.

**Doping requires an explicit `DopingSpec`** (`doping.py`): host formula, host
site species, dopant species, dopant fraction, and (once the parent structure
is known) which symmetry-distinct site. Nothing here is inferred from a bare
formula string — `classify.py` asks the user for anything missing, and
`doping.apply_doping` asks again if the parent turns out to have multiple
candidate sites. `apply_doping_with_validation` then runs the result through
`validate_doped_structure` before relaxation.

## Databases queried in Stage 1

Materials Project, OQMD, JARVIS, and **COD (Crystallography Open Database)**.
COD's officially documented REST endpoint only supports `id`/`smarts`/`text`/`format`
params (no direct `formula=` param) — see the detailed docstring in
`search_external.py::_query_cod` for the verified two-step approach (metadata
text search, then mandatory formula verification against the actual fetched
CIF) plus a link to COD's full-dump option for heavier use.

## What still needs wiring (marked `TODO` in each file)

- `search_external.py`: real `mp-api`, OQMD REST, `jarvis-tools`, and COD calls (see `_query_cod`'s docstring for the verified endpoint approach)
- `generate.py`: real CrystaLLM / CrystaLLM-pie invocation
- `relax.py`: real MACE-MP-0 + ASE relaxation (`mace-torch` install + checkpoint)
- `search_internal.py`: your actual backing database
- `user_interaction.web_form_ask_stub`: hook into your web interface's
  clarification UI so ambiguous cases (doped vs. undoped, which polymorph,
  which doping site) surface as a form to the user instead of a CLI prompt

## Install (once you wire the TODOs)

```bash
pip install pymatgen mace-torch mp-api ase
# + jarvis-tools if you use it
# + CrystaLLM per its own repo instructions
```

## Run the example

```bash
python -m cif_pipeline.main
```
