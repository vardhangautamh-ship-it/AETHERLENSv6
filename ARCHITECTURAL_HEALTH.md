# AETHERLENS — ARCHITECTURAL HEALTH SUMMARY (Part 4)

## 1. Is there accumulated patch-on-patch technical debt?

**Yes — unambiguously, and it is the proximate cause of the "fix it, it comes back"
pattern.** The evidence is structural, not stylistic:

### a. Multiple overlapping guard functions / denylists added over time, none retired
- **Four divergent name denylists** for the same job: `DOCUMENT_SKIP_LIST` +
  `PLACE_SKIP_LIST` + `NAME_STOPWORDS` + `FUSION_NAME_SKIPLIST` (`data_ingestion.py`)
  and `_IMPOSSIBLE_NAME_WORDS` + `_ENTITY_SKIP` + `FILENAME_SKIP_PATTERNS` +
  `_PLATFORM_TOKEN_SET` (`entity_resolution.py`). They overlap partially and disagree at
  the edges (`"law"` removed in one, `"constitution"` but not `"constitutional"`, cities
  in some lists and not others).
- **Two platform-token lists**: module-level `_PLATFORM_TOKEN_SET` and a re-declared
  local `_PLATFORM_NAMES` inside `detect_all_conflicts` with different membership —
  belt-and-suspenders that adds confusion without adding coverage.
- **Comments document the thrash directly**, e.g. `entity_resolution.py:411-415`
  ("Generic English words … were removed — they collide with real surnames"),
  `:445-446` ("Removed act, bill, code … these collide with real names"),
  `:374-376` ("Bare short fragments like 'val' were removed because a substring match
  rejected real names"). These are scars from prior false-positive fixes that each
  re-opened a false-negative hole.

### b. Duplicate logic paths where only one was kept current
- **Two risk scorers** (`run_risk_agent` live; `ontology.calculate_risk_score` fallback-only).
- **Three subject pickers** with no single authority (`extract_subject_name`,
  `extract_primary_subject_from_bytes`, `get_primary_subject`), the graph one **unguarded**.
- **Four phone cleaners**, with the only real validator trapped as a nested closure.
- **Two flag-assembly paths** that diverged (the agent pile vs the §09 display pile) —
  the defining defect of Symptom E.

### c. "Fix the symptom, not the cause" signatures
- Risk: a **hard cap** was added (kills `371`) instead of rescaling the formula (so it
  now sticks at `100`).
- Conflicts/phones: **downstream denylists / validators** keep growing instead of fixing
  the **upstream** artifact generators (`RE_NAME` cross-column capture; `_extract_phones`
  length-only check).
- Flags: the enrichment-before-agents problem was patched **for the risk section only**
  (`_build_risk_section` re-runs), leaving an inconsistent gate on next-step/tactical and
  a dead inline re-run.

### d. Defensive-coding noise masking failures
Heavy use of `safe_*` coercers, `@defensive` fallbacks, and broad `except Exception:
pass` (e.g. cross-platform discovery, timeline, ontology build, agent calls). These keep
the app from crashing but **silently convert "wrong" into "empty/Unknown,"** which is
exactly how a starved tactical agent quietly prints "0 flags" while everything around it
looks healthy.

---

## 2. Is the architecture sound enough to keep patching, or does entity resolution need a rewrite?

**The overall architecture is sound and worth keeping. Entity resolution does NOT need a
ground-up rewrite — it needs targeted consolidation of the duplicate paths.** The hybrid
principle (deterministic extraction/resolution first, LLM for narrative only) is already
present and correct in spirit; the failures come from **fragmentation**, not from a wrong
model.

Three of the five symptoms (B, C, and the `371` half of D) are **already largely
mitigated** in the live path by guards that work — proof the design can hold. The
remaining failures are concentrated, nameable, and fixable surgically (see
ROOT_CAUSE_REPORT). A full rewrite would discard working validation logic and re-incur
risk for no proportional gain.

**Recommendation: consolidate, do not rewrite.** Specifically, collapse the duplicate
paths into single authorities. This is a focused refactor of ~5 functions, not a new
subsystem.

---

## 3. Scoped consolidation plan (precise, hybrid-respecting)

> Deterministic extraction + resolution first; LLM only for narrative synthesis — kept intact.

### R1 — One subject authority
- **Keep** `entity_resolution.is_bad_subject_name` as the **single** name gate; widen
  `_IMPOSSIBLE_NAME_WORDS` and switch to stem/substring matching (≥5 chars).
- Route **all three** pickers through it, including the graph override at `app.py:2546`.
- Retire the local skip lists in `data_ingestion` (have the extractors call the one gate).
- *Files:* `entity_resolution.py`, `data_ingestion.py`, `app.py`. *~30 lines net.*

### R2 — One conflict source, fixed upstream
- Fix `_extract_names`/`RE_NAME` to not capture across table columns (reject `\t`/`\s{2,}`
  spans; or take tabular names only from `NAME_COLUMNS`).
- Delete the redundant inline `_PLATFORM_NAMES` block; keep `_is_platform_suffix` as the
  sole helper.
- *Files:* `data_ingestion.py`, `entity_resolution.py`. *~20 lines net.*

### R3 — One phone validator
- Lift `is_valid_phone` to module level in `entity_resolution.py`; call it from
  `extract_all_phones`, `build_phone_source_map`, and `data_ingestion._extract_phones`
  (or filter `entities["phones"]` once in `normalize_entities`).
- *Files:* `entity_resolution.py`, `data_ingestion.py`. *~15 lines net.*

### R4 — One flag pile, built before the agents (closes Symptom E)
- Introduce a single `build_anomaly_flags(person, raw_documents, behavioral_data,
  timeline_data)` that performs the §09 6-source aggregation **once**, and call it in
  **both** pipelines **before** `run_all_agents`, writing the result into
  `person["anomaly_flags"]`.
- Then §09 display, RiskAgent, NextStepAgent, and TacticalPlanAgent all read the **same**
  object. Remove the cache-preference gates on `_build_next_steps_section` and the §18
  block so they behave like `_build_risk_section`.
- *Files:* `report_generator.py` (extract the existing block into the helper), `app.py`
  (call before agents in FUSION at `:2647` and OSINT at `:1675`), `ai_agents.py`
  (orchestrator tactical anomalies from the same list). *~40 lines net; mostly moving
  existing code.*

### R5 — One risk formula
- Re-weight `run_risk_agent` with saturating tiers (keep the cap); count `n_anomalies`
  from the R4 unified list. Delete or clearly demote `ontology.calculate_risk_score` to a
  documented fallback.
- *Files:* `ai_agents.py`. *~15 lines net.*

**Net effect:** five "one true path" authorities replace ~13 overlapping
implementations. No new architecture, no model change, no UI rewrite — and every symptom
(A–E) maps to exactly one R-item. After R1–R5, future tuning happens in **one** place per
concern, which is precisely what was missing and what allowed every previous fix to
regress.

---

## Closing note
The codebase is **not** rotten; it is **forked internally** — the same decision is made
in several places that have drifted apart. Diagnosis-only confirms the bugs are
convergent (one root pattern: duplicated, time-skewed logic), so the remedy is
convergence: single sources of truth for subject, conflicts, phones, flags, and risk.
