# AETHERLENS — MODULE MAP (Part 1)

> Forensic codebase map. Scope = live application source only.
> **Excluded:** `venv/`, `.claude/worktrees/*` (stale worktree copies of the same
> modules — confirmed not on the live import path), and `test_*.py` / `tests_shot*.py`
> (standalone test scripts, not imported by the app).
> Generated read-only. No source files were modified.

---

## 1. File inventory (live modules)

| File | LOC | Primary responsibility |
|------|----:|------------------------|
| `app.py` | 5345 | Streamlit UI + the two orchestration pipelines (OSINT search, FUSION multi-doc). Entry point. |
| `config.py` | 271 | Env/secrets loading, Bedrock + Gemini client construction, cloud-secret bootstrap. |
| `modules/data_ingestion.py` | 1069 | Parse uploaded CSV/XLSX/PDF/TXT → text, structured rows, entities, primary_subject, document_flags. |
| `modules/entity_resolution.py` | 1689 | Build the canonical Person Object; subject selection, phone/email extraction, conflict detection, confidence scoring. |
| `modules/ai_agents.py` | 1384 | Six LLM agents (Risk/Pattern/NextStep/Compliance/TacticalPlan/Timeline) + orchestrator. Bedrock primary, Gemini fallback. |
| `modules/report_generator.py` | 2353 | Assemble the multi-section report, run/re-run agent sections, render PDF. |
| `modules/relationship_mapper.py` | 808 | Build NetworkX graph, pick graph primary subject, key associations, graph summary. |
| `modules/ontology.py` | 1032 | Digital-twin entity dataclasses, `calculate_risk_score` (weighted), OntologyGraph + DB persistence. |
| `modules/behavioral_analysis.py` | 540 | Behavioral assessment (LLM + local), `detect_rule_based_anomalies` over structured rows. |
| `modules/timeline.py` | 1018 | Date extraction, timeline build/fusion, contradiction + gap detection, plotly render. |
| `modules/account_timeline.py` | 426 | Account-creation timeline + flags from join dates. |
| `modules/search.py` | 1668 | OSINT lookups (GitHub/Reddit/Twitter/IG/LinkedIn/YT/DDG/News/Wikipedia), linked-profile discovery. |
| `modules/sanitizer.py` | 181 | `safe_*` coercion helpers, `safe_phone`, `@defensive` decorator. |
| `modules/security.py` | 651 | Data lineage, hash-chained audit, compartments, DPDP compliance helpers. |
| `modules/auth.py` | 455 | SQLite users, bcrypt/JWT login, PIN lock, admin user mgmt, audit. |
| `modules/ip_lookup.py` | 98 | IP geolocation via HTTP API. |
| `modules/license.py` | 142 | Machine-ID license key generation/verification. |
| `modules/ui_components.py` | 423 | HTML/CSS snippet builders for the Streamlit UI. |
| `modules/__init__.py` | 0 | Package marker. |

---

## 2. LLM/AI call sites (Bedrock / Gemini)

| Module | Function(s) | Engine | Notes |
|--------|-------------|--------|-------|
| `config.py` | `get_bedrock_client`, `test_bedrock_connection`, `test_gemini_connection` | both | Client construction (ap-south-1 / Mumbai). |
| `ai_agents.py` | `_call_bedrock` → `_call_ai` (Bedrock primary, 3 retries) → `_call_gemini` fallback. Sets module global `LAST_ENGINE_USED`. | both | Used by every agent (`run_risk_agent`, `run_pattern_agent`, `run_next_step_agent`, `run_compliance_agent`, `run_tactical_plan_agent`, `run_timeline_analysis_agent`). |
| `entity_resolution.py` | `_call_bedrock_for_fusion`, `_call_gemini` | both | Fusion entity resolution (`resolve_entity_from_multiple_docs`, `resolve_entity_from_documents`, `resolve_entity`). |
| `behavioral_analysis.py` | `_analyze_inner` → calls Bedrock/Gemini | both | Behavioral narrative; local fallback `_local_fallback`. |

All other modules are deterministic (no LLM calls).

---

## 3. Key functions by module (entity-resolution-relevant only)

### `data_ingestion.py`
- `ingest_file(bytes, filename, user_id, declared)` → master parse; returns dict with `entities`, `structured_rows`, `primary_subject`, `document_flags`, `locations`, `full_text`, `raw_text`, `graph_entities`.
- `extract_subject_name(text)` — **free-text** subject picker (label patterns, then frequency `_NAME_FREQ_RE`). Used for PDF/TXT. Filters with `DOCUMENT_SKIP_LIST` + `PLACE_SKIP_LIST`.
- `extract_primary_subject_from_bytes(bytes, suffix)` — **structured** subject picker for CSV/XLSX (`NAME_COLUMNS`, two-word capitalised regex). Filters with `FUSION_NAME_SKIPLIST` + `NAME_STOPWORDS`.
- `_extract_names(text)` — NER: `RE_NAME` capitalised-token sequences → name entities. **Allows multi-space/tab between tokens.**
- `_extract_phones(text)` — phone entities; **only a 7–15 digit length check** (no semantic validation).
- `normalize_entities(text, source)` — runs all extractors → `entities` dict consumed downstream.
- `extract_flags_from_text`, `extract_locations_from_text`, `detect_file_subtype`, `parse_traffic_challans`, `parse_anpr_logs`.

### `entity_resolution.py`
- `resolve_entity_from_multiple_docs(raw_documents)` → **(A) primary subject selection**, AI fusion overlay, **(C) phone extraction**, **(B) conflict detection**, confidence. The live multi-doc resolver.
- `resolve_entity_from_documents(subject, rows, filename)` — per-file resolver (FUSION per-file pass + single doc).
- `is_bad_subject_name(candidate, raw_documents)` — denylist guard (`_IMPOSSIBLE_NAME_WORDS`, `_PLATFORM_TOKEN_SET`, `FILENAME_SKIP_PATTERNS`, role titles, filename match).
- `detect_all_conflicts(raw_documents, primary_name, person)` — **(B)** NAME/LOCATION/DOB conflict generator. Sole emitter of "NAME CONFLICT".
- `_is_platform_suffix`, `_is_name_with_suffix` — conflict guards.
- `extract_all_phones(raw_documents)` — **(C)** phone extractor with **nested** `is_valid_phone` validator.
- `build_phone_source_map(raw_documents)` — per-file phone attribution. **Does NOT call `is_valid_phone`.**
- `calculate_confidence`, `calculate_stable_confidence`, `calculate_evidence_based_confidence`, `detect_data_gaps`.

### `ai_agents.py`
- `run_risk_agent(person, anomalies, graph, uid)` — **(D)** deterministic base score + LLM explanation. Hard cap 0–100 at the score line.
- `run_next_step_agent(report, uid)` — reads `report["sections"]["anomalies_and_flags"]["flags"]` + `person.anomaly_flags/conflicts/behavioral_flags`.
- `run_tactical_plan_agent(person, assets, report_data, uid)` — reads `report_data["anomalies"]` / `["anomaly_flags"]`.
- `run_pattern_agent`, `run_compliance_agent`, `run_timeline_analysis_agent`.
- `AgentOrchestrator.run_all_agents(ontology, report, mode, uid, assets, raw_documents)` — **(E)** fan-out; builds tactical anomalies **only from `report["person"]`**.

### `relationship_mapper.py`
- `get_primary_subject(entities, graph)` — **(A) second subject selector**; returns most-mentioned `person`-typed node. **No `is_bad_subject_name` check.**
- `build_graph`, `build_graph_from_person`, `graph_summary`, `get_key_associations`, `is_valid_node_name`, `get_or_create_node`.

### `ontology.py`
- `calculate_risk_score(person_entity, …)` — **(D) second risk scorer** (weighted `RISK_FACTORS`). Live only as `_build_risk_section` fallback.
- `build_digital_twin`, `OntologyGraph`, entity dataclasses.

### `report_generator.py`
- `generate_report(...)` → `_generate_report_inner(...)` — builds `sections`, renders PDF. Returns dict containing top-level `sections`.
- `_build_risk_section(person, agent_results, raw_documents)` — **(D/E)** ALWAYS re-runs `run_risk_agent` with a freshly built keyword-scanned flag list (ignores cached `agent_results["risk"]`).
- `_build_next_steps_section(agent_results, person)` — **(E)** PREFERS cached `agent_results["next_steps"]`; inline re-run only as fallback.
- `_build_extracted_intelligence_section(person)` — **(C)** §14; iterates `person["phones_found"]` (validated), attributes via `phone_sources`.
- The §09 anomaly assembly block (`_add_anomaly`, lines ~1655–1741) — **(E)** aggregates 6 flag sources incl. a raw-document keyword scan.

---

## 4. Dependency / call-chain map — upload → report

```
                          ┌────────────────────────────────────────────────────────┐
                          │                       app.py                            │
                          └────────────────────────────────────────────────────────┘
                                  │                                   │
                ┌─────────────────┘                                   └────────────────┐
        OSINT pipeline (screen_*)                               FUSION pipeline (screen_fusion)
        run_search() [search.py]                                 file_uploader → staged files
                │                                                       │
        build_person_profile()                            for each file: _process_single_file()
        [entity_resolution.resolve_entity]                        └─ ingest_file()  [data_ingestion]
                │                                                        ├─ extract_primary_subject_from_bytes / extract_subject_name
                │                                                        ├─ normalize_entities → _extract_names / _extract_phones
                │                                                        └─ document_flags, locations, structured_rows
                │                                                  └─ resolve_entity_from_documents()  [entity_resolution]
                │                                                       (per-file person; replaced below)
                │                                                       │
                │                                       resolve_entity_from_multiple_docs(all_results)  [entity_resolution]
                │                                            ├─ (A) primary subject (primary_subject → freq → is_bad guard)
                │                                            ├─ AI fusion overlay (_call_bedrock_for_fusion / _call_gemini)
                │                                            ├─ (C) extract_all_phones (+ is_valid_phone), build_phone_source_map
                │                                            ├─ (B) detect_all_conflicts → person.anomaly_flags + person.conflicts
                │                                            └─ confidence (calculate_confidence / calculate_stable_confidence)
                │                                                       │
                │                                       build_graph + get_primary_subject()  [relationship_mapper]
                │                                            └─ (A) graph override of confirmed_name (UNGUARDED)
                │                                                       │
                │                                       _analyze() [behavioral]  +  detect_rule_based_anomalies()
                │                                            (results held in behavioral_data / rule_anomalies —
                │                                             NOT written back into primary_person)
                │                                                       │
                │                                       build_digital_twin() [ontology]
                │                                                       │
   generate_report(person, …)  [report_generator]          (E) orchestrator.run_all_agents(
        (1st pass, no agent_results)                              ont_json, report={"person": primary_person})  ◄── ONLY person
                │                                                  ├─ run_risk_agent (person)
   orchestrator.run_all_agents(                                   ├─ run_pattern_agent (ontology)
        report={"person": person, **rd})  ◄── incl. sections      ├─ run_next_step_agent({"person":…})  ◄── no sections
        │                                                         ├─ run_compliance_agent
   generate_report(…, agent_results)  (2nd pass)                  └─ run_tactical_plan_agent(anomalies from person only)
                │                                                       │
                ▼                                                       ▼
        report_generator builds sections (BOTH pipelines):
            §09 anomalies_and_flags  = 6 sources incl. raw-doc keyword scan   ◄── ENRICHED, built here
            §16 risk_assessment      = _build_risk_section → ALWAYS re-runs run_risk_agent w/ keyword scan
            §17 next_steps           = _build_next_steps_section → prefers CACHED agent_results
            §18 tactical_plan        = prefers CACHED agent_results; inline re-run gated behind "no actions"
                │
                ▼
        _sections_to_pdf_data → generate_pdf
```

### Critical ordering fact (drives Symptom E)
Anomaly-flag **enrichment** (timeline anomalies, behavioral `rule_anomalies`/`behavioral_flags`,
`account_creation_flags`, and the raw-document keyword scan for CERT-In / FEMA / IT-Act / DPDP /
deletion) is produced **inside `report_generator` at report-build time** — i.e. **after** the agents
have already run. The agents are handed the bare `person` object whose `anomaly_flags` contains only
`document_flags + conflicts`. See ROOT_CAUSE_REPORT.md §E.

### Duplicate / parallel logic paths (full detail in ROOT_CAUSE_REPORT.md)
- **Primary subject:** `data_ingestion.extract_subject_name` **and** `extract_primary_subject_from_bytes` **and** `relationship_mapper.get_primary_subject` (graph) — three pickers, four divergent denylists.
- **Risk score:** `ai_agents.run_risk_agent` (live) **and** `ontology.calculate_risk_score` (fallback only).
- **Phone extraction:** `entity_resolution.extract_all_phones` (validated) **and** `data_ingestion._extract_phones` (length-only) **and** `build_phone_source_map` (unvalidated) **and** `sanitizer.safe_phone`.
- **Name-conflict guards:** module-level `_PLATFORM_TOKEN_SET` + helpers **and** a local `_PLATFORM_NAMES` list re-declared inside `detect_all_conflicts`.
