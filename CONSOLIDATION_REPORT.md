# AETHERLENS — Consolidation Report

**Date:** 2026-06-26
**Base commit:** `a2ec9d7` (local `master` → tracks `origin/main`, the live Streamlit deploy source)
**Status:** All five fixes complete. Uncommitted working tree = live base + the edits below.
**Scope discipline:** No new features. No whole-module rewrites. Every duplicated copy
that was consolidated was **deleted**, not left as a dead twin. All legal safeguards,
audit logging, and the declaration gate are untouched. The hybrid principle is preserved:
deterministic extraction/resolution first, LLM only for narrative synthesis.

---

## The disease (recap)

The forensic autopsy (`MODULE_MAP.md`, `ROOT_CAUSE_REPORT.md`, `ARCHITECTURAL_HEALTH.md`)
diagnosed a single recurring pathology: **duplicated logic with divergent copies**. The
same rule (flag building, subject selection, phone validation, risk scoring, name
matching) existed in multiple places that had drifted apart, so a fix in one copy left the
others stale. The cure throughout was **a single source of truth per rule**.

> **Re-grounding note.** The three audit docs and the original R1–R5 plan describe code
> ~22 commits older than the live `origin/main`. Before implementing, the local branch was
> synced fast-forward to the live base. Two of the five fixes (R-E, R-A) were found to be
> **already implemented equivalently on `origin/main`** and required no further local edit;
> the stale local versions were reverted and archived as a reference patch. The remaining
> three (R-C, R-D, R-B) were implemented fresh against the live base.

---

## R-E — Unify the flag piles  ✅ already live

**Goal:** One canonical anomaly-flag list (`person["anomaly_flags"]`) consumed by all six
agents, instead of several independently-built flag collections.

**Outcome:** `origin/main` already contains the equivalent consolidation (`cba419a`,
`98ced07`): a single `build_anomaly_flags` path with deduplication, and every agent reads
`person["anomaly_flags"]`. **No local edit required.** Verified the unified list reaches the
report dict before agents run — see `test_pipeline_ordering.py` ("All 11 anomalies unified
(3 doc + 8 rule)").

---

## R-A — Single subject picker  ✅ already live

**Goal:** One subject-selection authority with one noise gate, instead of divergent
name-picking heuristics across ingestion and resolution.

**Outcome:** `origin/main` already contains the equivalent consolidation (`a2ec9d7`,
`ab6fdb9`): `is_bad_subject_name` is the single gate, with caption-stripping and noise
rejection. **No local edit required.** Verified by `test_fusion_noise_regression.py` Part A
(rejects "Anthropic Billing", "Dear Sir", "Swiggy Order"; keeps "Harshvardhan Gautam",
"Linus Torvalds", etc.).

---

## R-C — One phone validator  ✅ implemented this run

**The duplication:** `is_valid_phone` existed as an ~88-line nested closure *inside*
`extract_all_phones`, unreachable by any other caller. Meanwhile `data_ingestion._extract_phones`
relied on a weaker length-only check, and `build_phone_source_map` did no validation — so
ISP-column fragments, IP addresses, order/TXN IDs and CDR duration padding leaked in as
"phone numbers" through the paths that didn't share the strict rule.

**The cure — single source of truth:**

| File | Change |
|------|--------|
| `modules/entity_resolution.py` | **Lifted `is_valid_phone` to module level** (immediately before `extract_all_phones`). **Deleted** the ~88-line nested closure. Logic preserved exactly (rejects ISP-column fragments, IPs, order/invoice/TXN IDs, date strings, CDR duration padding, ISP account IDs, data-volume prefixes; domestic 6–9 first-digit + `+91` checks; 7–15 digit length). |
| `modules/entity_resolution.py` | `build_phone_source_map` — added `and is_valid_phone(clean)` guard to all three sources (entity list, structured rows, raw text). |
| `modules/data_ingestion.py` | `_extract_phones._add` — `from modules.entity_resolution import is_valid_phone` (local import) + `if not is_valid_phone(raw): return`, replacing the length-only check. |

**Verify:** `python test_entity_resolution_regression.py` → 19/19.
(`test_phone_enrichment.py` shows 6 pre-existing failures from the optional `phonenumbers`
library being absent — identical on pristine code, **not** a regression of this change.)

---

## R-D — Risk formula recalibration  ✅ implemented this run

**The problem:** `run_risk_agent` used a **linear** formula
(`sources*5 + anomalies*6 + 18·has_assets`, minus a stray `entity_count`-based `-12`
penalty on a dead variable). It saturated immediately — two genuinely different serious
subjects both pinned to the same near-max score, and thin profiles scored alarmingly high.
`ontology.calculate_risk_score` was a second, undocumented scoring path inviting drift.

**The cure — saturating tiers + one documented authority:**

`modules/ai_agents.py` → `run_risk_agent`:
```python
def _saturate(n, tiers):
    score = 0.0
    for count, pts in tiers:
        if n <= 0: break
        take = n if count is None else min(n, count)
        score += take * pts; n -= take
    return score

anomaly_pts  = _saturate(n_anomalies, [(3, 3), (3, 6), (None, 1.5)])
source_pts   = _saturate(sources,     [(6, 6), (None, 2)])
asset_pts    = 10 if has_assets else 0
thin_penalty = 8 if (sources <= 2 and n_anomalies <= 2) else 0
base_score   = int(max(0, min(90, round(anomaly_pts + source_pts + asset_pts - thin_penalty))))
```
- **Deleted** the dead `entity_count = len(str(p))` variable and its `-12` penalty.
- Added **risk-factor de-duplication** after the factor list is built (normalize text via
  `re.sub(r"[^a-z0-9]","",...)[:40]`, keep first occurrence).
- Level thresholds unchanged: ≥75 CRITICAL, ≥55 HIGH, ≥35 MEDIUM, else LOW.

`modules/ontology.py` → `calculate_risk_score`: added a docstring **demoting it to a
documented fallback** — "the live risk authority is `ai_agents.run_risk_agent`, invoked
solely by `report_generator._build_risk_section` when RiskAgent is unavailable."

**Resulting spread (cap binds only at the extreme):**
| Profile | sources/flags/assets | score | level |
|---|---|---|---|
| sparse | 2 / 3 | 21 | LOW |
| medium | 6 / 6 | 63 | HIGH |
| moderate | 5 / 8 | 60 | HIGH |
| heavy | 7 / 12 / assets | 84 | CRITICAL |
| extreme | 8 / 20 / assets | 90 | CRITICAL |

heavy ≠ extreme (two serious cases now differ); the 90 cap rarely binds.

**Verify:** `python test_pipeline_ordering.py` → 16/16 (includes "sparse score < 25",
"medium score 60–85").

---

## R-B — Name regex / CSV column bleed  ✅ implemented this run

**The duplication:** the person-name regex existed as multiple byte-identical-but-independent
copies — two distinct divergent-copy pairs:
1. *Free-text scanner*, duplicated within `data_ingestion.py`: `RE_NAME` and `_NAME_FREQ_RE`.
2. *Anchored single-cell matcher*, duplicated across modules: `name_re` (`data_ingestion`)
   and `_REAL_NAME_RE` (`relationship_mapper`).

**The cure — single sources of truth:**

| File | Change |
|------|--------|
| `modules/entity_resolution.py` | **NEW** canonical `RE_PERSON_NAME_CELL = ^([A-Z][a-z]{1,20}(?:\s+...){1,3})$`, placed in the shared base both modules already import. |
| `modules/data_ingestion.py` | Moved the `[ \t]+` (no cross-line bridging) rationale comment onto `RE_NAME`; **deleted** `_NAME_FREQ_RE`; frequency pass now uses `RE_NAME`. |
| `modules/data_ingestion.py` | **Deleted** local `name_re`; now `from modules.entity_resolution import RE_PERSON_NAME_CELL as name_re`. |
| `modules/relationship_mapper.py` | **Deleted** the `_REAL_NAME_RE` literal; now `_REAL_NAME_RE = RE_PERSON_NAME_CELL` (true identity, not a copy). |

**On column bleed:** the `[ \t]+` separator was **deliberately left intact**. The live
frequency-normalization comment (`data_ingestion.py:310`) documents that double-spaced
names ("Arjun  Mehta") are legitimate PDF-extraction artifacts in this corpus — narrowing
the separator to a single space would regress real names. Bleed is already contained by
`NAME_STOPWORDS` + the document/place skip-lists + the cross-line guard, all preserved.
This fix is therefore a pure structural consolidation with **no semantic change**.

**Verify:** `grep -rn "_NAME_FREQ_RE" modules/*.py` → no matches;
`python -c "import modules.relationship_mapper, modules.data_ingestion"` imports cleanly;
all three suites green.

---

## Verification summary

| Suite | Result |
|---|---|
| `test_pipeline_ordering.py` | **16/16** |
| `test_entity_resolution_regression.py` | **19/19** |
| `test_fusion_noise_regression.py` | **47/47** |
| `test_phone_enrichment.py` | 19/25 (6 pre-existing, optional `phonenumbers` lib absent — not a regression) |

**Files touched (uncommitted):**
`modules/ai_agents.py`, `modules/data_ingestion.py`, `modules/entity_resolution.py`,
`modules/ontology.py`, `modules/relationship_mapper.py`.

**Not done (intentionally):** nothing committed or pushed; no Streamlit redeploy. Those
remain yours to perform after reviewing this report.
