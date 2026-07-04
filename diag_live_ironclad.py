"""
TEMP DIAGNOSTIC (delete after use) — faithful headless replay of the LIVE FUSION
pipeline for IRONCLAD, so the PA_DEBUG dump at report_generator.py:2652 fires with
the SAME inputs the Streamlit app would feed. This does NOT hand-build ontology
inputs (that is diag_ironclad_patterns.py). It mirrors screen_fusion()/
_process_single_file() exactly: ingest_file per doc, relationship_mapper graph,
multi-doc resolve, keyword flags, build_timeline_from_fusion, then generate_report.
assets_data is left None because the live app only fills it from the SEPARATE
asset-upload panel (fusion_assets_staged), which a normal case-doc upload leaves empty.

Run:  PA_DEBUG=1 PYTHONUTF8=1 python diag_live_ironclad.py
"""
import os
os.environ.setdefault("PA_DEBUG", "1")

from modules.data_ingestion import ingest_file
from modules.entity_resolution import (
    resolve_entity_from_documents, resolve_entity_from_multiple_docs,
    clean_person_object,
)
from modules.relationship_mapper import (
    build_graph_from_person, build_graph,
    extract_relationships_from_structured_rows,
)
from modules.timeline import build_timeline_from_fusion
from modules.report_generator import inject_keyword_flags_from_docs, generate_report

DESK = os.path.join(os.path.expanduser("~"), "OneDrive", "Desktop")
DOCS = [
    "IRONCLAD_01_profile.pdf", "IRONCLAD_02_call_records.csv",
    "IRONCLAD_03_crypto_flow.csv", "IRONCLAD_04_incident_log.txt",
    "IRONCLAD_05_platform_metadata.csv", "IRONCLAD_06_email_receipts.csv",
]

all_results, all_ents, all_rels, all_struct_rows, staged = [], [], [], [], []
for name in DOCS:
    path = os.path.join(DESK, name)
    with open(path, "rb") as f:
        fbs = f.read()
    staged.append({"name": name, "bytes": fbs})
    result = ingest_file(fbs, name, "system", True)          # declared=True
    if not result.get("success"):
        print(f"[SKIP] {name}: {result.get('error')}")
        continue
    structured_rows = result.get("structured_rows", [])
    primary_subject = result.get("primary_subject", "") or name.rsplit(".", 1)[0]
    person, method = resolve_entity_from_documents(primary_subject, structured_rows, name)
    if not person.get("confirmed_name"):
        person["confirmed_name"] = primary_subject
    ingested_sr = {"query": primary_subject, "total": result.get("total_items", 0),
                   "results": [{"full_name": primary_subject, "platform": f"Document: {name}",
                                "snippet": "", "url": "", "confidence": 70}], "errors": {}}
    _, ents, rels = build_graph_from_person(person, ingested_sr)
    s_ents, s_rels = extract_relationships_from_structured_rows(structured_rows, name)
    ents.extend(s_ents); rels.extend(s_rels)
    all_results.append(result)
    all_ents.extend(ents); all_rels.extend(rels); all_struct_rows.extend(structured_rows)

# multi-doc resolve (as FUSION does)
primary_person, _pm = resolve_entity_from_multiple_docs(all_results)
if not (primary_person and primary_person.get("confirmed_name") not in (None, "", "Unknown Subject")):
    primary_person = {"confirmed_name": "Daniyal Farooqui"}

# dedupe entities by id + unified graph (as FUSION does)
seen, merged_ents = set(), []
for e in all_ents:
    if e["id"] not in seen:
        merged_ents.append(e); seen.add(e["id"])
G_full = build_graph(merged_ents, all_rels)

# keyword flags across all ingested docs (as FUSION does)
inject_keyword_flags_from_docs(primary_person, all_results)

# timeline from fusion (as FUSION does)
raw_docs_for_timeline = [
    {"filename": r.get("filename", ""), "raw_text": r.get("raw_text", ""),
     "structured_rows": r.get("structured_rows", [])}
    for r in all_results
]
tl_combined = build_timeline_from_fusion(primary_person or {}, raw_docs_for_timeline)

try:
    clean_person_object(primary_person)
except Exception:
    pass

print("\n##### LIVE-REPLAY: calling generate_report(mode=FUSION) — PA_DEBUG will dump inputs #####")
print(f"  ingested docs: {[r.get('filename') for r in all_results]}")
print(f"  merged_ents={len(merged_ents)}  graph nodes={G_full.number_of_nodes()} edges={G_full.number_of_edges()}")
print(f"  person flags={len(primary_person.get('anomaly_flags', []))}")

rd = generate_report(
    person=primary_person,
    search_results={"results": []},
    graph_data={"graph": G_full, "entities": merged_ents, "rels": all_rels},
    timeline_data=tl_combined,
    behavioral_data=None,
    user_id="system",
    mode="FUSION",
    raw_documents=all_results,
    assets_data=None,          # live: only the separate asset panel fills this
)

pa = (rd.get("sections", {}) or {}).get("pattern_analysis", {})
print("\n##### LIVE §09B RESULT #####")
print("  case_type      :", pa.get("case_type"))
print("  pattern_count  :", pa.get("pattern_count"))
print("  patterns       :", [p.pattern_id if hasattr(p, "pattern_id") else p for p in (pa.get("patterns") or [])])
