"""
SHARED CASE PIPELINE — the ONE files-to-typed-ontology path for user modes.

Both FUSION and the EVIDENCE CHAIN mode are LENSES over this single pipeline:
they call build_case_ontology() to turn uploaded files into the resolved person,
relationship graph, timeline, and the typed Ontology — then each mode applies its
own layer on top (Fusion: digital twin + agents + report; Evidence Chain: the
chain layer). There is deliberately NO second ingestion/resolution/ontology path
here — every step below CALLS an existing module function; none of the ingestion,
entity-resolution, or ontology-build LOGIC is reimplemented. The typed ontology
is assembled by report_generator.build_typed_ontology, the exact same function the
report pipeline uses, so no mode can drift from another.

Deterministic and UI-free: no Streamlit, no LLM, no agents, no digital-twin
persistence. An optional `progress(pct, message)` callback lets a UI mode surface
progress without this module depending on any UI. The regression test harness
(run_case_regression._run_pipeline) is intentionally NOT routed through here — it
is a minimal calibrated direct path, not a user mode.
"""

from modules.data_ingestion import ingest_file
from modules.entity_resolution import (
    resolve_entity_from_documents, resolve_entity_from_multiple_docs,
    clean_person_object, is_bad_subject_name,
)
from modules.relationship_mapper import (
    build_graph_from_person, build_graph, graph_summary, get_primary_subject,
    extract_relationships_from_structured_rows, detect_boilerplate_locations,
)
from modules.timeline import build_timeline, build_timeline_from_fusion
from modules.behavioral_analysis import analyze as _analyze, detect_rule_based_anomalies
from modules.report_generator import build_typed_ontology, inject_keyword_flags_from_docs


def _noop(pct, message):  # default progress sink
    pass


def _process_one(fbs, fname, uid, declared):
    """Full per-file pipeline on one file's bytes (UI-free port of the Fusion
    per-file step). Returns
    (result, person, method, ents, rels, tl, behavioral_data, structured_rows,
     primary_subject) or a (None, ...) tuple when ingestion fails."""
    result = ingest_file(fbs, fname, uid, declared)
    if not result.get("success"):
        return None, None, None, [], [], {}, {}, [], ""

    structured_rows = result.get("structured_rows", [])
    primary_subject = result.get("primary_subject", "")
    doc_flags       = result.get("document_flags", [])
    doc_locations   = result.get("locations", [])
    entities        = result["entities"]

    if not primary_subject:
        skip = {"Location Timeline", "Date Time", "City State", "Activity Type",
                "Work Entry", "NexaTech", "Not found", "Unknown", "HIGH", "MEDIUM", "LOW"}
        names_list = [
            n["value"] for n in entities.get("names", [])[:10]
            if n["value"] not in skip and not is_bad_subject_name(n["value"])
        ]
        if names_list:
            primary_subject = names_list[0]
        else:
            stem = fname.rsplit(".", 1)[0].replace("_", " ").replace("-", " ")
            primary_subject = stem if not is_bad_subject_name(stem) else ""

    person, method = resolve_entity_from_documents(primary_subject, structured_rows, fname)
    if not person.get("confirmed_name") or person["confirmed_name"] in ("Unknown", ""):
        if primary_subject and not is_bad_subject_name(primary_subject):
            person["confirmed_name"] = primary_subject
    person["_resolution_method"] = method

    if doc_flags:
        existing_flags = person.get("anomaly_flags", [])
        person["anomaly_flags"] = existing_flags + [
            {"flag": f.get("flag", str(f)), "source": f.get("source", fname), "severity": "MEDIUM"}
            if isinstance(f, dict) else {"flag": str(f), "source": fname, "severity": "MEDIUM"}
            for f in doc_flags
        ]

    if doc_locations:
        existing_locs = set(person.get("location_stated", []))
        for loc in doc_locations:
            if loc not in existing_locs:
                person.setdefault("location_stated", []).append(loc)
                existing_locs.add(loc)

    ingested_sr = {
        "query":   primary_subject,
        "total":   result["total_items"],
        "results": [{"full_name": primary_subject, "platform": f"Document: {fname}",
                     "snippet": "", "url": "", "confidence": 70}],
        "errors":  {},
    }

    _, ents, rels = build_graph_from_person(person, ingested_sr)
    struct_ents, struct_rels = extract_relationships_from_structured_rows(structured_rows, fname)
    ents.extend(struct_ents)
    rels.extend(struct_rels)

    tl = build_timeline(person, ingested_sr)

    behav_result, behav_method = _analyze(
        {"person": person, "search_results": ingested_sr},
        structured_rows=structured_rows,
    )
    behavioral_data = {"assessment": behav_result, "method": behav_method}

    try:
        clean_person_object(person)
    except Exception:
        pass

    return result, person, method, ents, rels, tl, behavioral_data, structured_rows, primary_subject


def build_case_ontology(files, uid="case_pipeline", declared=True,
                        progress=None, assets_data=None):
    """Turn a list of staged files into the shared analysis bundle.

    files: list of {"name": str, "bytes": bytes} (already-decoded staged files).
    progress: optional callable(pct:int, message:str) for UI feedback.
    assets_data: optional list of asset dicts (financial inventory) for the
                 ontology; None/empty is normal.

    Returns a bundle dict:
      {person, resolution_method, subject, graph_data, timeline_data,
       behavioral_data, rule_anomalies, raw_documents, assets_data, ontology}
    where `ontology` is the typed Ontology (report_generator.build_typed_ontology).
    Deterministic; reuses existing module functions only; no LLM/agents."""
    progress = progress or _noop

    all_ents, all_rels, all_struct_rows, all_results = [], [], [], []
    primary_person = None
    primary_method = "local-fallback"
    primary_subject_name = ""

    total = len(files) or 1
    errors = []            # per-file failures — SURFACED, never silently swallowed
    for i, sf in enumerate(files):
        _fname = sf.get("name", "?") if isinstance(sf, dict) else "?"
        progress(int(5 + 55 * i / total), f"Processing {_fname}")
        try:
            result, person, method, ents, rels, tl, behavioral_data, struct_rows, psubj = \
                _process_one(sf["bytes"], sf["name"], uid, declared)
        except Exception as _pfe:
            import traceback
            traceback.print_exc()
            errors.append({"file": _fname, "error": f"{type(_pfe).__name__}: {_pfe}"})
            continue
        if result is None:
            errors.append({"file": _fname, "error": "ingestion returned no usable result"})
            continue
        all_results.append(result)
        all_ents.extend(ents)
        all_rels.extend(rels)
        all_struct_rows.extend(struct_rows)
        if primary_person is None and person:
            primary_person = person
            primary_method = method
            primary_subject_name = psubj

    # ── Multi-document entity resolution (overrides per-file primary) ──────────
    if all_results:
        progress(65, "Resolving cross-document identities")
        try:
            md_person, md_method = resolve_entity_from_multiple_docs(all_results)
            if md_person and md_person.get("confirmed_name") not in (None, "", "Unknown Subject"):
                primary_person = md_person
                primary_method = md_method
        except Exception:
            pass

    # ── Cross-file dedup + unified graph ──────────────────────────────────────
    progress(72, "Building unified relationship graph")
    seen_ent_ids, merged_ents = set(), []
    for e in all_ents:
        if e["id"] not in seen_ent_ids:
            merged_ents.append(e)
            seen_ent_ids.add(e["id"])
    G_full = build_graph(merged_ents, all_rels)

    # Validate primary subject against the graph (prevents a location/org node
    # that dominated extraction from masquerading as the subject).
    graph_subject = get_primary_subject(merged_ents, G_full)
    if graph_subject and graph_subject != "Unknown Subject" and not is_bad_subject_name(graph_subject):
        current_name = (primary_person or {}).get("confirmed_name", "")
        if not current_name or current_name in ("Unknown Subject", "Unknown", ""):
            if primary_person:
                primary_person["confirmed_name"] = graph_subject
            primary_subject_name = graph_subject
        elif is_bad_subject_name(current_name):
            if primary_person:
                primary_person["confirmed_name"] = graph_subject
            primary_subject_name = graph_subject

    _boilerplate = detect_boilerplate_locations(all_results)
    graph_summ = graph_summary(
        G_full,
        subject_name=(primary_person or {}).get("confirmed_name", ""),
        boilerplate=_boilerplate,
    )

    # ── Combined behavioural analysis + rule anomalies + timeline ─────────────
    progress(80, "Behavioural analysis and timeline")
    ingested_sr_combined = {
        "query":   primary_subject_name,
        "total":   len(merged_ents),
        "results": [{"full_name": primary_subject_name, "platform": f"Document: {sf['name']}",
                     "snippet": "", "url": "", "confidence": 70} for sf in files],
        "errors":  {},
    }
    behav_result, behav_method = _analyze(
        {"person": primary_person or {}, "search_results": ingested_sr_combined},
        structured_rows=all_struct_rows,
    )
    behavioral_data = {"assessment": behav_result, "method": behav_method}
    rule_anomalies = detect_rule_based_anomalies(all_struct_rows)

    raw_docs_for_timeline = [
        {"filename": r.get("filename", sf["name"]),
         "raw_text": r.get("raw_text", ""),
         "structured_rows": r.get("structured_rows", [])}
        for sf, r in zip(files, all_results)
    ]
    tl_combined = (build_timeline_from_fusion(primary_person or {}, raw_docs_for_timeline)
                   if all_results else build_timeline(primary_person or {}, ingested_sr_combined))

    if primary_person is None:
        primary_person = {}

    # ── Deterministic flag enrichment (keyword scan + rule anomaly merge) ─────
    progress(90, "Flag enrichment")
    try:
        inject_keyword_flags_from_docs(primary_person, all_results)
    except Exception:
        pass
    _existing_flags = primary_person.get("anomaly_flags", []) or []
    _existing_texts = {
        (f.get("flag", str(f)) if isinstance(f, dict) else str(f)).lower()
        for f in _existing_flags
    }
    for ra in (rule_anomalies or []):
        flag_text = ra.get("flag", str(ra)) if isinstance(ra, dict) else str(ra)
        if flag_text.lower() not in _existing_texts:
            _existing_texts.add(flag_text.lower())
            _existing_flags.append({
                "flag":     flag_text,
                "detail":   ra.get("detail", "") if isinstance(ra, dict) else "",
                "source":   "rule-based-detector",
                "severity": "MEDIUM",
            })
    primary_person["anomaly_flags"] = _existing_flags

    try:
        clean_person_object(primary_person)
    except Exception:
        pass

    graph_data = {"graph": G_full, "entities": merged_ents, "rels": all_rels,
                  "relationships": all_rels, "summary": graph_summ}

    progress(96, "Building typed ontology")
    ontology = build_typed_ontology(
        primary_person, graph_data=graph_data, timeline_data=tl_combined,
        behavioral_data=behavioral_data, raw_documents=all_results,
        assets_data=assets_data)

    progress(100, "Complete")
    return {
        "person":            primary_person,
        "resolution_method": primary_method,
        "subject":           primary_subject_name,
        "graph_data":        graph_data,
        "timeline_data":     tl_combined,
        "behavioral_data":   behavioral_data,
        "rule_anomalies":    rule_anomalies,
        "raw_documents":     all_results,
        "errors":            errors,
        "all_struct_rows":   all_struct_rows,
        "search_results":    ingested_sr_combined,
        "assets_data":       assets_data or [],
        "ontology":          ontology,
    }
