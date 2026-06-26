# AETHERLENS — ROOT CAUSE REPORT (Parts 2 & 3)

## Plain-English summary (for a non-engineer)

AetherLens reads several uploaded files and tries to figure out **who** the case is
about, then has AI "agents" write up the risk, the next steps, and a tactical plan.
The trouble is that the program collects its evidence in **two separate piles that
were never kept in sync**. One pile is the *display* pile — the colourful list of red
flags you see on screen in Section 09 ("CERT-In inquiry confirmed", "International
financial transfer"). The other pile is the *agent* pile — the much smaller set of
flags actually handed to the AI when it writes the tactical plan. The display pile is
built **late**, at the moment the report is drawn; the agents run **earlier**, so they
only ever see the small pile. That is why Section 09 can be full of red flags while the
tactical plan underneath insists there are "0 confirmed evidence flags." Separately,
the "who is this person" logic and the "is this a real phone number" logic were each
fixed many times by **bolting on more and more keyword blocklists** in *one* of the
several places that do that job — so a fix added in one spot quietly leaves the other
spots (a second name-picker, a second phone cleaner, a second risk formula) still
broken. The blocklists also have gaps ("law", "constitutional", "account" were never
added), and the risk score uses a formula whose numbers add up past 100 so easily that
almost every multi-file case slams into the 100/100 ceiling. None of this is caused by
the AI being offline — the AI is live; it is simply being fed the wrong, smaller pile.

---

# PART 2 — Forensic trace

## Live execution path (verified)

**FUSION pipeline** (the multi-document upload path the GHOSTWIRE/JUPITER test files use):

`screen_fusion()` → `_process_single_file()` → `data_ingestion.ingest_file()` →
`entity_resolution.resolve_entity_from_documents()` (per file) →
`entity_resolution.resolve_entity_from_multiple_docs()` (replaces per-file result,
`app.py:2489`) → `relationship_mapper.get_primary_subject()` graph override
(`app.py:2539`) → `behavioral_analysis.analyze()` + `detect_rule_based_anomalies()`
(`app.py:2567,2576`) → `ontology.build_digital_twin()` →
**`AgentOrchestrator.run_all_agents(ont_json, {"person": primary_person})`**
(`app.py:2647`) → later `report_generator.generate_report(..., agent_results=…)`
(`app.py:3159`, inside `_fusion_show_results`).

**Agents run once** (app.py), their output is cached in
`st.session_state.agent_results`, and the report **reuses** that cache. The report's
own §09 enrichment happens *after* the agents have run. This ordering is the spine of
Symptom E.

---

## Timeline diagram — actual order of operations (single FUSION run)

```
T0  Upload N files  ────────────────────────────────────────────────────────────────
T1  ingest_file() per file
        • primary_subject  set here  ◄── ENTITY NAME determined (CSV: extract_primary_subject_from_bytes;
                                          PDF/TXT: extract_subject_name)   [Symptom A origin]
        • _extract_names() builds name entities incl. multi-space artifacts  [Symptom B origin]
        • _extract_phones() builds entities["phones"] (length-only, dirty)   [Symptom C origin]
        • document_flags extracted from [FLAG] lines
T2  resolve_entity_from_documents() per file (person built, then discarded)
T3  resolve_entity_from_multiple_docs(all_results)
        • primary subject re-selected (+ is_bad_subject_name guard)  ◄── ENTITY NAME (2)
        • AI fusion overlay (Bedrock→Gemini)
        • extract_all_phones()+is_valid_phone → person.phones_found (CLEAN)  ◄── PHONES finalised
        • detect_all_conflicts() → person.anomaly_flags += conflicts        ◄── FLAGS BUILD (partial)
        • confidence scored
T4  build_graph(); get_primary_subject() may OVERWRITE confirmed_name        ◄── ENTITY NAME (3, UNGUARDED)
T5  behavioral analyze() + detect_rule_based_anomalies()
        • rule_anomalies + behavioral_flags produced
        • ⚠ NOT written back into primary_person
T6  build_digital_twin()
T7  ┌── run_all_agents({"person": primary_person})   ◄── AGENTS INVOKED ───────────────┐
    │     RiskAgent(person)          → RISK SCORE computed [Symptom D]                  │
    │     PatternAgent(ontology)                                                        │
    │     NextStepAgent({"person":…})  ◄── sees ONLY person.anomaly_flags(+conflicts)   │
    │     ComplianceAgent                                                               │
    │     TacticalPlanAgent(anomalies built from person only)  ◄── "0 evidence flags"   │
    └──────────────────────────────────────────────────────────────────────────────────┘
        • At this instant person.anomaly_flags = document_flags + conflicts ONLY.
        • behavioral/timeline/account/keyword-scan flags are NOT present.
T8  agent_results cached in session_state
T9  generate_report(person, agent_results)
        • §09 anomalies_and_flags = 6 SOURCES incl. raw-doc KEYWORD SCAN   ◄── FLAGS BUILD (full, LATE)
              "CERT-In inquiry confirmed", "International financial transfer — FEMA 1999", …
        • §16 risk  = _build_risk_section → RE-RUNS RiskAgent w/ keyword scan (immune)
        • §17 next  = _build_next_steps_section → uses CACHED agent_results (stale, sparse)
        • §18 tact  = uses CACHED agent_results; inline enriched re-run is GATED OFF
T10 PDF render
```

**The single most important line:** at **T7** the agents see the flag pile from **T3**;
the rich pile is not built until **T9**. Display (T9) and agent-input (T7) are two
different objects produced at two different times.

---

## SYMPTOM A — False primary subject

**Functions involved & whether live:**
- `data_ingestion.extract_subject_name` (`data_ingestion.py:269`) — LIVE for PDF/TXT. Skip lists: `DOCUMENT_SKIP_LIST` + `PLACE_SKIP_LIST`.
- `data_ingestion.extract_primary_subject_from_bytes` (`:326`) — LIVE for CSV/XLSX. Skip lists: `FUSION_NAME_SKIPLIST` + `NAME_STOPWORDS`.
- `entity_resolution.is_bad_subject_name` (`:464`) — LIVE guard, called at `resolve_entity_from_multiple_docs:868,897` and `app.py:2024,2036`. Denylist: `_IMPOSSIBLE_NAME_WORDS` + `_PLATFORM_TOKEN_SET` + `FILENAME_SKIP_PATTERNS`.
- `relationship_mapper.get_primary_subject` (`:735`) — **LIVE, DUPLICATE selector**, called at `app.py:2539`. **Returns its node with no `is_bad_subject_name` check.**

**DUPLICATE LOGIC PATH:** three subject pickers feed `confirmed_name`, governed by **four
non-identical denylists**. A string rejected by one is accepted by another.

**Root cause:** denylist **fragmentation + gaps**, not absence of a guard.
- `is_bad_subject_name("Criminal Procedure")` → **rejected** ("procedure" ∈ `_IMPOSSIBLE_NAME_WORDS`, `entity_resolution.py:448`).
- `is_bad_subject_name("Constitutional Law")` → **ACCEPTED**. The set contains `"constitution"` but **not** `"constitutional"` (no stemming), and `"law"` was deliberately **removed** (`entity_resolution.py:445-446`, to spare "Jude Law"). So the sibling string passes.
- Location strings (`"Manesar Industrial Area"`, `"Nariman Point Office"`): `is_bad_subject_name` has no `"area"/"industrial"/"office"` token, so it does **not** reject them; only `data_ingestion`'s `PLACE_SKIP_LIST` (a *different* list) catches some of these — and only on the PDF/TXT free-text path, not on the structured path or the graph-override path.
- `get_primary_subject` (`app.py:2539-2547`) overwrites `confirmed_name` with the top graph **person**-node when the current name is empty/`Unknown` — **without** `is_bad_subject_name`. Any string that `_extract_names` mis-typed as a person (it survives the `_loc_words` filter at `data_ingestion.py:1029` only if none of its tokens is a known location word) becomes the subject unchecked.

**Why previous fixes did not fully resolve it:** every fix added more words to **one**
list (usually `_IMPOSSIBLE_NAME_WORDS`). Because there are four divergent lists and an
**unguarded** graph-override path, a word added to one list still slips through the
others. The removal of `"law"`/`"act"` to avoid false-rejecting real names
(`Jude Law`, `Bill`) re-opened the academic-title hole for `"Constitutional Law"`.

**Confidence:** **High** that fragmentation + the unguarded `get_primary_subject`
override is the mechanism. **Medium** on which exact string surfaces in any given run
(depends on file mix and which picker wins).

**Recommended surgical fix:**
1. Route the graph override through the guard:
   `app.py:2546` — only assign `graph_subject` if `not is_bad_subject_name(graph_subject, raw_documents)`.
2. Have `data_ingestion.extract_subject_name` / `extract_primary_subject_from_bytes`
   call `is_bad_subject_name` as the **single** final gate instead of their own local
   skip lists (collapse four lists → one).
3. Add the missing tokens to `_IMPOSSIBLE_NAME_WORDS`: `"constitutional"`, `"law"`*,
   `"area"`, `"industrial"`, `"office"`, `"jurisprudence"` — *and* switch the membership
   test to match on a **stem/substring of length ≥ 5** so `constitution*` covers
   `constitutional`. (\*Re-add `"law"` only as a *multi-word* check: reject `"… Law"`
   when a preceding token is also a non-name word, preserving `"Jude Law"`.)

---

## SYMPTOM B — False name conflicts ("Harshvardhan Instagram", doubled names)

**Functions involved & whether live:**
- `entity_resolution.detect_all_conflicts` (`:1360`) — **sole** emitter of "NAME CONFLICT". LIVE, reached only from `resolve_entity_from_multiple_docs:992`, and only when `primary_name != "Unknown Subject"`.
- Guards inside it: `_is_name_with_suffix` (`:419`), `_is_platform_suffix` (`:1304`), plus an **inline local list** `_PLATFORM_NAMES` (`:1408`).
- `data_ingestion._extract_names` (`:572`) using `RE_NAME` (`:149`) — **LIVE upstream artifact generator**.

**Verification that the guard IS reachable and DOES fire (skeptical trace):** for
`primary="Harshvardhan"`, variant `"Harshvardhan Instagram"` →
`_is_platform_suffix` case 2: `v.startswith("harshvardhan ")` and remaining token
`"instagram"` ∈ `_PLATFORM_TOKEN_SET` → returns **True** → `continue` (suppressed).
Doubled `"Harshvardhan Harshvardhan"` → `_is_platform_suffix` case 1/2 → **True**.
**So in the current code the three cited examples ARE suppressed.** The guard is not
dead and not shadowed in a harmful way.

**SCOPE/SHADOWING finding (as the brief asked):** the inline `_PLATFORM_NAMES` list
(`:1408`) is a **near-duplicate** of module-level `_PLATFORM_TOKEN_SET` with *different*
membership (`"hugging face"` with a space vs `"huggingface"`/`"hugging_face"`). It runs
*after* `_is_platform_suffix` already decided, and only does exact `"primary + platform"`
matching — strictly weaker than the helper. It is **redundant**, not the cause, but it is
exactly the kind of divergent constant that makes the file look "guarded everywhere"
while the real gaps sit elsewhere.

**Root cause (the actual recurrence mechanism):** the artifact is **manufactured
upstream and only suppressed by a denylist downstream.** `RE_NAME`
(`r"\b([A-Z][a-z]{1,20}(?:[ \t]+[A-Z][a-z]{1,20}){1,3})\b"`, `data_ingestion.py:149`)
allows **multiple spaces/tabs** between tokens. When a CSV is rendered with
`df.to_string()`, adjacent columns (`subject_name="Harshvardhan"`,
`platform="Instagram"`) sit on the same line separated by padding spaces, so
`_extract_names` captures the cross-column string `"Harshvardhan   Instagram"` as a
single two-word "name". `detect_all_conflicts` then suppresses it **only if the second
token is in the platform denylist.** Tokens that were deliberately **removed** from
`_PLATFORM_TOKEN_SET` (`account`, `user`, `handle`, `page`, `channel`, `official`,
`verified` — see the comment at `entity_resolution.py:411-415`) and any non-platform
neighbour column (a city, a status, `"Active"`, `"Verified"`, a second platform like
`Koo`/`Mastodon` not in the set) are **NOT** suppressed → a false NAME CONFLICT is
emitted.

**Why previous fixes did not fully resolve it:** the fixes were all **downstream
denylist patches** (`_PLATFORM_TOKEN_SET`, `_is_platform_suffix`, the inline list). A
denylist can never enumerate every value that might sit in the column next to the name.
Each test run with a new column layout surfaces a new neighbour token the list does not
contain → "the guard exists, yet the symptom recurs." The **structural** cause —
`_extract_names` concatenating across table columns because `RE_NAME` permits
multi-space gaps over `df.to_string()` output — was never addressed.

**Confidence:** **High** on the mechanism (upstream artifact + downstream denylist with
gaps). The cited Instagram/Telegram/doubled cases are currently suppressed; the residual
live failures are non-platform / removed-token neighbours.

**Recommended surgical fix (attack the source, not the denylist):**
1. In `_extract_names`, **collapse internal whitespace before matching** and reject any
   candidate whose original span contained `\t` or 2+ consecutive spaces (those are
   column boundaries, never intra-name spacing):
   change `RE_NAME` inner gap from `[ \t]+` to a **single space** `[ ][ ]?`, or skip a
   match if `re.search(r"\s{2,}|\t", m.group())`.
2. Better still, do not run free-text `_extract_names` over `df.to_string()` for tabular
   files — take names only from `NAME_COLUMNS` cells (the structured path already exists).
3. Delete the redundant inline `_PLATFORM_NAMES` block (`:1408-1424`); rely on the single
   `_is_platform_suffix` helper to remove the divergent-constant smell.

---

## SYMPTOM C — Dirty phone numbers in §14

**Functions involved & whether live:**
- `entity_resolution.extract_all_phones` (`:580`) with **nested** `is_valid_phone` (`:616`) — LIVE; sets `person["phones_found"]` (`resolve_entity_from_multiple_docs:975`).
- `entity_resolution.build_phone_source_map` (`:735`) — LIVE; sets `person["phone_sources"]`. **Does NOT call `is_valid_phone`** (only `safe_phone` + `PHONE_REGEX`).
- `data_ingestion._extract_phones` (`:484`) — LIVE; builds `entities["phones"]` with **only a 7–15 digit length check.**
- `sanitizer.safe_phone` (`:121`) — a third, lighter cleaner.
- `report_generator._build_extracted_intelligence_section` (`:1210`) — §14 renderer; iterates `person["phones_found"]`, looks up attribution in `phone_sources`.

**DUPLICATE LOGIC PATH:** four phone cleaners; the only **semantic** validator
(`is_valid_phone`) is a **nested closure** that cannot be reused by the other three.

**Root cause:** §14 currently iterates the **validated** `phones_found`, so the cited
order-ID / IP-fragment / ISP-volume strings ARE filtered today
(`is_valid_phone` rejects `^\d{2,4}-\d{5,7}$` order refs at `:642`, IPs at `:631`,
`^\d{1,4}\s+\d+$` ISP fragments at `:627`, `_DATA_PREFIXES` volumes at `:681`). The
historical dirtiness came from these strings reaching §14 **before** `is_valid_phone`
existed, *and* from the parallel extractors that never had it:
- `entities["phones"]` (`_extract_phones`) still contains order IDs / IP fragments / data
  volumes — it only length-checks. Anything rendered from `entities["phones"]` (graph
  nodes, ontology `DeviceEntity`, behavioral) is still dirty.
- `phone_sources` (`build_phone_source_map`) is built **without** `is_valid_phone`; its
  keys can include invalid numbers. They are currently invisible only because §14 keys
  off `phones_found` — a coincidental shield, not a guarantee.

**Why previous fixes did not fully resolve it:** the validator was added in **one**
function (`extract_all_phones`) as a **nested** helper, so the sibling extractors
(`_extract_phones`, `build_phone_source_map`) never gained it. Each render path that
bypasses `phones_found` re-exposes the dirt; "fixing the phone parser" fixed only one of
four parsers.

**Confidence:** **High** that §14's specific path is clean now and that the
duplicate-extractor divergence is the recurrence vector. **Medium** on exactly which UI
surface re-displayed dirty numbers in past runs (graph/ontology vs §14).

**Recommended surgical fix:**
1. **Promote `is_valid_phone` to a module-level function** in `entity_resolution.py`
   (lift it out of `extract_all_phones`).
2. Call it inside `build_phone_source_map` (filter before `raw_map.setdefault`) and have
   `data_ingestion._extract_phones` import and apply it (or filter `entities["phones"]`
   once at the end of `normalize_entities`). One validator, every path.

---

## SYMPTOM D — Risk score (371/100, then stuck 100/100)

**Functions involved & whether live:**
- `ai_agents.run_risk_agent` (`:344`) — **LIVE** score. Re-run every report build by `_build_risk_section:967`.
- `ontology.calculate_risk_score` (`:188`) — **DEAD in normal path**; only the `_build_risk_section` fallback (`report_generator.py:1008`) when `run_risk_agent` raises. DUPLICATE scorer.

**Root cause:** the live formula (`ai_agents.py:391-398`):
```
base_score = (sources * 7) + (n_anomalies * 9) + (20 if has_assets else 0)
if entity_count < 5000: base_score -= 15
if sources <= 4:        base_score -= 12
base_score = max(0, min(100, base_score))   # hard cap
```
- **`371/100`** was the **pre-cap** behaviour. The cap at `:398` now makes >100
  impossible → that exact symptom is **fixed**.
- **Stuck `100/100`** is the cap doing its job over a formula that **overshoots
  trivially**. A 7-file case = `7×7 = 49` from sources alone; just `6` structured flags
  = `6×9 = 54`; sum `103` → capped to exactly `100`. A case that "should be 70–85" pins
  to 100 because `7/source` and `9/flag` are far too large and there is **no
  normalization and no diminishing-returns curve** — modest evidence saturates the scale.
- Secondary defect: `n_anomalies` counts `person["anomaly_flags"]`
  (`p.get("anomalies",[]) or p.get("anomaly_flags",[]) or flags`, `:386`) — the **sparse**
  structured set, **not** the enriched/displayed flags — so the score does not even track
  the §09 flag count shown to the user.

**Why previous fixes did not fully resolve it:** the fix added a **cap** (treats the
symptom: "number > 100") instead of rescaling the **formula** (the cause: "sum routinely
exceeds 100"). Capping converts every over-scoring case into an identical `100`, which is
why it flipped from "impossible 371" to "stuck 100".

**Confidence:** **High.**

**Recommended surgical fix (re-weight, keep the cap):** replace the additive coefficients
with saturating tiers so realistic inputs land mid-scale, e.g.
```
src_pts  = {0:0,1:8,2:16,3:22}.get(min(sources,3), 26)          # caps ~26
flag_pts = min(n_anomalies, 6) * 6 + max(0, n_anomalies-6) * 2   # diminishing after 6
asset_pts = 15 if has_assets else 0
base_score = max(0, min(100, src_pts + flag_pts + asset_pts))
```
and count `n_anomalies` from the **same enriched flag list** the report displays (see
Symptom E fix), so the score and §09 agree.

---

## SYMPTOM E — Anomaly flags not reaching NextStep / Tactical agents ("0 confirmed evidence flag(s)")

**This is the central defect. It is an EXECUTION-ORDER + DATA-STRUCTURE-MISMATCH bug, not a dead guard.**

**Functions involved & whether live (all LIVE):**
- `AgentOrchestrator.run_all_agents` (`ai_agents.py:1302`) — builds tactical anomalies **only** from `report["person"]` (`:1326-1336`).
- `run_next_step_agent` (`ai_agents.py:582`) — reads `report["sections"]["anomalies_and_flags"]["flags"]` **first** (`:590-592`), then `person.anomaly_flags/conflicts/behavioral_flags`.
- `run_tactical_plan_agent` (`ai_agents.py:1153`) — reads `report_data["anomalies"]` / `["anomaly_flags"]` (`:1167`).
- report_generator §09 assembly (`:1655-1741`) — builds the rich flag list from **6 sources** including the raw-document **keyword scan** (`:1706-1727`).
- `_build_risk_section` (`:915`) — **always** re-runs RiskAgent with its **own** keyword scan (`:936-967`).
- `_build_next_steps_section` (`:1041`) — **prefers cached** `agent_results["next_steps"]` (`:1049-1050`).
- §18 inline tactical re-run (`:2283-2307`) — reads `sections["anomalies_and_flags"]["flags"]` (`:2294-2296`) **but is gated** behind `if not agent_results["tactical_plan"].get("actions")` (`:2284`).

**The two divergent flag piles:**

| | Built where | Contents |
|---|---|---|
| **Agent pile** (T7) | `app.py:2647`, `run_all_agents(report={"person": primary_person})` | `person.anomaly_flags` = `document_flags` + `conflicts` **only** |
| **Display pile** (T9) | `report_generator` §09 | + timeline anomalies + behavioral `rule_anomalies`/`behavioral_flags` + `account_creation_flags` + **raw-doc keyword scan** (CERT-In, FEMA, IT-Act, DPDP, deletion) |

**Root cause, traced:**
1. **FUSION calls the agents with no report sections.** `app.py:2647` passes
   `report = {"person": primary_person or {}}`. There is no `"sections"` key and no
   `"anomalies"` key, so `run_next_step_agent`'s primary source
   (`report["sections"]["anomalies_and_flags"]["flags"]`, `:591-592`) is empty. It falls
   back to `person.anomaly_flags` — the sparse pile.
   *(Contrast: the OSINT pipeline at `app.py:1675-1680` passes
   `report = {"person": person, **rd}`, which **does** include the report `sections`, so
   `run_next_step_agent` gets the enriched flags **in OSINT only**.)*
2. **Tactical is broken in BOTH pipelines.** The orchestrator builds tactical anomalies
   exclusively from `person_data` (`:1326-1336`); the keyword-scan / timeline /
   behavioral flags live only in the report sections, never in `person`. So
   `run_tactical_plan_agent` receives the sparse pile and prints
   `"… {len(anomalies)} confirmed evidence flag(s) …"` = small/zero (`ai_agents.py:1142`).
3. **The enrichment runs AFTER the agents.** Timeline/behavioral/keyword flags are first
   materialised inside `report_generator` at T9 — after T7. The agents could not have
   seen them.
4. **Behavioral flags are never written back to `person`.** `app.py:2567` computes
   `behav_result` into `behavioral_data`, and `detect_rule_based_anomalies` (`:2576`) into
   `rule_anomalies` — neither is merged into `primary_person.anomaly_flags`/
   `behavioral_flags` before T7.
5. **The risk path was specially patched; next-step/tactical were not.**
   `_build_risk_section` ignores the cached risk and **re-runs RiskAgent with its own
   keyword scan** (`:936-967`) — so §16 is immune and looks correct, masking the bug.
   `_build_next_steps_section` and the §18 block instead **prefer the cached
   agent_results** and only re-run inline when those are empty (`:1053`, `:2284`). In the
   normal pipeline the cache is **never** empty (app.py already produced it), so the
   inline re-run that *does* read the enriched `sections` flags (`:2294-2296`) is
   **effectively dead code**.

**Why previous fixes did not fully resolve it:**
- The developer comment at `report_generator.py:1703-1705` ("…added here so they are
  visible to `run_next_step_agent`…") encodes the **intended** contract: NextStep reads
  `sections["anomalies_and_flags"]["flags"]`. That contract holds for **OSINT** (sections
  are passed) but is **silently violated for FUSION** (only `{"person":…}` is passed) —
  and FUSION is the document-upload path the test cases exercise. The fix was written for
  one of the two call sites.
- The "always re-run with enrichment" remedy was applied to **risk only**. The same
  three-line pattern was not mirrored into next-step/tactical, and the gate
  `if not …actions` actively prevents the enriched inline re-run from ever firing once the
  cached (sparse) plan exists. So the symptom persists exactly where the remedy was not
  copied.
- Because §16 risk *does* show live Bedrock output and a real score (it re-runs), and
  §12 shows `claude-sonnet-4-bedrock`, the system **looks** live — reinforcing the false
  belief that "agents are working," when next-step/tactical are simply starved of flags.

**Confidence:** **Very high.** This is a directly traced object/timing mismatch, not an
inference.

**Recommended surgical fix (any one closes it; do 1+2 for completeness):**
1. **Build the enriched flag list once, before the agents, and inject it into `person`.**
   In `app.py` just before `run_all_agents` (`:2647`), run the same keyword scan +
   merge `rule_anomalies` + `behav_result["behavioral_flags"]` + timeline anomalies into
   `primary_person["anomaly_flags"]`. Then every agent (which already reads
   `person.anomaly_flags`) gets the full pile. *(Smallest, most robust change.)*
2. **Pass the report sections to the FUSION agents**, mirroring OSINT: generate the
   report first (or a lightweight sections dict) and call
   `run_all_agents(ont_json, {"person": primary_person, **rd}, …)`. Also have the
   orchestrator build tactical `all_anomalies` from
   `report.get("sections",{}).get("anomalies_and_flags",{}).get("flags",[])` in addition
   to `person` (`ai_agents.py:1326`).
3. **Remove the cache-preference gate** so §17/§18 mirror §16: have
   `_build_next_steps_section` and the §18 block **always** rebuild from the enriched
   `sections` flags (drop the `if not …` short-circuit at `report_generator.py:1053` and
   `:2284`), exactly as `_build_risk_section` already does.
