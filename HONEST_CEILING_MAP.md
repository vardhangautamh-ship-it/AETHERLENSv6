# AETHERLENS — HONEST CEILING MAP
### What still breaks or limits the system, and what we did about it.

## Purpose
This document records the known failure envelope of AETHERLENS. It exists because
a system whose legal defensibility rests on "we never assert what we can't cite"
must be equally rigorous about stating its own limits. Every entry was found by
deliberate adversarial testing of our own modules — not discovered in the field.
Each is stated as: **finding → risk → status → mitigation**.

**Methodology note:** findings come from purpose-built gauntlets (HYDRA, CHIMERA,
targeting/predictive adversarial suites) and a full read-only system audit. The
gauntlets are designed to DEFEAT our modules, not confirm them; a passing gauntlet
is one we couldn't break on the dimensions tested. This map is therefore bounded
by what our tests target — see "Known boundaries of this map" at the end.

Verified as of commit `51c3a58`.

---

## A. FOUND AND FIXED
*(surfaced by our own gauntlets; resolved and re-verified)*

### A1. Targeting — thin-evidence inflation
- **FINDING:** the prioritisation ranker sorted on `risk_score` first, so a subject
  with a high score but ZERO cited patterns and an empty evidence basis could
  outrank an evidence-rich lower-scored subject and auto-enter a watchlist.
- **RISK:** high (accusatory-direction). Asserting importance that isn't cited —
  the same defect class as fabricated associations. A watchlist entry with no cited
  basis.
- **STATUS: FIXED.** A corroboration guard now flags a pattern-less/basis-less high
  score as `thin_basis` and routes it to `uncorroborated_review` instead of the
  watchlist. Verified: the gauntlet case resolves; real corroborated cases are NOT
  falsely flagged (no over-correction); identity-blindness and determinism
  preserved.

### A2. Predictive — dispersed-cadence over-precision
- **FINDING:** the irregular-cadence caveat was mathematically unreachable for any
  3-occurrence series (the most common groundable size). A series like
  [2 days, 150 days] got a specific projected date and a stated "~76-day cadence"
  with no noise caveat — reading noise as rhythm.
- **RISK:** moderate (over-precision, not over-certainty). The top-level
  WEAK/speculative/non-determinative labels were always intact; only the granular
  "rough extrapolation" signal was missing.
- **STATUS: FIXED.** The irregularity criterion now works for 3+ occurrence series
  and flags noisy cadence; regular series are not falsely flagged; top-level honesty
  labels unchanged. Gauntlet resolves 8/8.

### A3. Ontology source-threading gap (was ceiling-map B3)
- **FINDING:** `build_ontology` dropped the real origin filename for several typed
  entities — `legal_proceedings`, `deletion_events`, `comm_channels` were stamped a
  generic `"record"`; phones came out with an empty source; cross-case phone
  citations then fell back to `"source not recorded"`. A partial exception to the
  core promise that every fact cites its source.
- **RISK:** moderate (defensibility). Real, important circumstances (enforcement
  notices, deletion events, phone links) were surfaced but could not always name the
  exact file they came from.
- **STATUS: FIXED.** `build_ontology` now threads each fact's REAL origin, reusing
  the existing carriers — the row's `_source_file` (same as `Transaction.source`),
  the timeline event's own `source`, the enclosing-document filename for text-line
  events (behind a strict `len(texts)==len(documents)` alignment guard so a text
  event is cited to the document it appears in, never a guessed file), and the
  `person["phone_sources"]` map matched by the same `phone_key(min_digits=7)`
  (multiple files comma-joined, the pattern `Location.source` already uses).
  `"record"`/`""` is retained ONLY as the genuine-absence fallback — nothing is
  back-filled. **Verified:** CHIMERA moved from 46 file-cited / 17 generic / 7
  excluded → 70 / 0 / 0 (same shape on MERIDIAN/GHOSTWIRE/HYDRA); every threaded
  cite genuinely contains the fact when checked against the pipeline's EXTRACTED
  text (0 phone/event mismatches — no fabrication); genuine-absence facts still come
  out `sourced=False` (a sourceless text event → `"record"`; a phone absent from the
  map → `""` → excluded, not dressed up); cross-case shared-phone links now name the
  real files on both sides; ontology parity with Fusion preserved; full suite
  unchanged (37 green / pre-existing `test_hardening` 49/50). Deterministic-first,
  identity-blind, and no-fabrication all intact.

---

## B. KNOWN-OPEN — BOUNDED
*(real, understood, mitigated downstream; not yet fully closed)*

### B1. §16 risk score — volume-inflatable displayed number
- **FINDING:** the deterministic risk scorer awards points per source document
  (6 each for the first six, then 2 each). With zero anomalies and zero patterns,
  pure document volume yields: 6 files→36 (MEDIUM), 16 files→56 (HIGH),
  26 files→76 (CRITICAL). Document volume alone can push the DISPLAYED §16 score
  into HIGH/CRITICAL.
- **RISK:** moderate for a skeptical evaluator; low operationally. The real crossing
  is ~16 files, not the ~6 earlier believed; at realistic counts (≤10) it stays
  MEDIUM (<55). But the number an officer reads at the top of a report is still
  inflatable by volume.
- **STATUS: OPEN** (scorer unchanged by design). **MITIGATION:** the A1 targeting
  guard bounds the downstream harm — a pattern-less high score cannot silently reach
  a watchlist; it is flagged `thin_basis`. The guard protects the watchlist, NOT the
  displayed number.
- **VERIFICATION GAP:** guard logic and scorer arithmetic confirmed separately; a
  full 16-source case has not been run end-to-end through targeting. The interaction
  is reasoned, not yet demonstrated.
- **DECISION PENDING:** fix the scorer so the score tracks pattern/anomaly evidence
  rather than document count, or formally accept this as a documented limit with the
  mitigation stated.

### B2. Name-normalization divergence
- **FINDING:** name normalization is implemented in three places with divergent
  behaviour — `sanitizer.normalize_name_key` (whitespace-collapse + casefold) vs
  `pattern_rules._norm` / `ontology._pa_norm` (strip+lower, no whitespace collapse)
  vs `data_mining._norm_text` (strips punctuation). They diverge on double-spaces
  and punctuated/hyphenated names.
- **RISK:** moderate, but FAILS IN THE SAFE DIRECTION. The divergence can cause a
  MISSED within-case match or a MISSED cross-case link — it errs toward
  under-linking, never toward fabricating a link.
- **STATUS: OPEN** (recorded debt). **DECISION PENDING:** finish consolidation to a
  single normalizer, or document as an accepted safe-direction limit.

---

## C. COVERAGE BOUNDARIES
*(not defects — limits of what the test suite proves)*

### C1. Auth / security / license — untested
- **FINDING:** `ip_lookup`, `license`, `security` have zero test coverage and are not
  imported by the production pipeline (optional/off-path). `auth.py` is only
  partially tested — the E2E harness bypasses PIN/login/JWT, so session validation,
  password hashing, RBAC, and audit logging are untested.
- **RISK:** low for a demo (off the analysis hot path); material for a PRODUCTION or
  procurement claim.
- **STATUS: OPEN.** "Auth and security are tested" is NOT a claim the current suite
  supports. Must be closed before any real deployment; safe to disclose for a
  capability demo.

### C2. UI screen coverage
- **FINDING:** 16 of 18 `app.py` screen functions have no automated test (only Fusion
  and cross-case intel are covered via the mock harness). `gap_detection`,
  `predicate_chain`, `account_timeline`, `ui_components` have no direct unit test
  (exercised only indirectly).
- **RISK:** low-moderate. The demo surface (Fusion, cross-case, Evidence Chain) is
  covered end-to-end; peripheral screens are not.
- **STATUS: OPEN, coverage boundary.** Disclose honestly; expand coverage before
  production.

---

## D. ARCHITECTURAL / EPISTEMIC LIMITS
*(not bugs — honest limits of the approach itself)*

### D1. Exhaustive only over what it can TYPE
The ontology walks every record deterministically — no fatigue, no hallucinated
links. But it surfaces only relationships its vocabulary can type. A relationship
the rulebook doesn't recognise is walked past silently and reproducibly. The
defensible claim is "we surface every link the rulebook can see, and flag where the
chain has a hole (gap detection)" — NOT "we leave nothing."

### D2. No exclusion test (deliberate)
The system surfaces what the data supports; it does not weigh whether an innocent
explanation also fits the same circumstances (Sharad Birdhichand limbs 2 & 4).
Generating alternative hypotheses deterministically is an open abductive-reasoning
problem; handing it to the LLM would break deterministic-first. This is scoped OUT
by design. Consequence: outputs are LEADS, not verdicts — every incriminating
inference counts incriminating signals and has no competing-explanation term.
Honesty is carried by explicit "this is a lead, not a conclusion; ordinary
explanations may exist" labelling and by gap detection, not by an exclusion engine.

---

## KNOWN BOUNDARIES OF THIS MAP
- Destructive testing only finds what the tests target. The `co_appears` fabrication
  (since fixed) existed for months because no gauntlet had probed the graph builder
  — proof that "passes our gauntlets" means "survives the attacks we designed," not
  "cannot fail." Untested attack surfaces may hold unknown failures.
- Guardrails verified as of commit `51c3a58`. Any change to scoring, ranking, entity
  resolution, or the LLM boundary can regress them; the CI regression runner exists
  to catch this but covers only wired fixtures.
- "Demo-solid" ≠ "production-certified." Section C gaps (auth/security/coverage) must
  close before real deployment.

---

## AUDIT ADDENDUM — proposed additions (not in the original draft)
*Surfaced by the full read-only audit and the Evidence Chain build; recorded here so
the map matches the complete finding set. Fold into the sections above or drop, as you
judge.*

*(B3 — the ontology source-threading gap — has been FIXED and moved up to section
A as A3.)*

### C3. Standing test baseline — `test_hardening` 49/50 (NAME_CONFLICT)
- **FINDING:** the full suite is 37 green / 1 red, and the one red is a long-standing
  baseline: `test_hardening.py` fails its `NAME_CONFLICT detected` check in
  `entity_resolution.detect_all_conflicts` (LOCATION_CONFLICT and DOB_CONFLICT are
  detected; NAME_CONFLICT is not, for that fixture).
- **RISK:** unclassified pending diagnosis. If it is a real miss it errs toward
  under-flagging an inconsistency (safe direction, like B2); it may instead be
  test-drift. This has NOT been diagnosed read-only — stated honestly rather than
  guessed.
- **STATUS: OPEN, known baseline.** Every green report in this program is reported
  against "37 green / 1 known-fail"; the 1 is this. Diagnose real-miss vs test-drift
  before claiming the suite is fully green.
