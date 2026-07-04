"""
TEMP DIAGNOSTIC (delete after use) — faithful LIVE §09B replay for all three
genres, feeding run_pattern_analysis the EXACT argument shapes the live call site
(report_generator.py:2652) produces: financial_data = None (the asset panel is
empty on a normal case-doc upload), records = flattened structured_rows from the
ingested docs, texts = raw doc text, entities/graph from the real relationship_mapper.
This proves Option B (transactions read from `records`) is general and does not
manufacture false transactions from non-financial rows across genres.

Run:  PA_DEBUG=1 PYTHONUTF8=1 python diag_live_three.py    (PA_DEBUG optional)
"""
import os

from modules.data_ingestion import ingest_file
from modules.entity_resolution import (
    resolve_entity_from_documents, resolve_entity_from_multiple_docs, clean_person_object,
)
from modules.relationship_mapper import (
    build_graph_from_person, build_graph, extract_relationships_from_structured_rows,
)
from modules.timeline import build_timeline_from_fusion
from modules.report_generator import inject_keyword_flags_from_docs
from modules.pattern_engine import run_pattern_analysis

DESK = os.path.join(os.path.expanduser("~"), "OneDrive", "Desktop")

CASES = {
    "IRONCLAD": dict(
        docs=["IRONCLAD_01_profile.pdf", "IRONCLAD_02_call_records.csv",
              "IRONCLAD_03_crypto_flow.csv", "IRONCLAD_04_incident_log.txt",
              "IRONCLAD_05_platform_metadata.csv", "IRONCLAD_06_email_receipts.csv"],
        expected={"ANTI_FORENSIC_BEHAVIOUR", "COUNTER_SURVEILLANCE",
                  "OPERATIONAL_SECURITY", "LAYERING_STRUCTURE"}),
    "CERBERUS": dict(
        docs=["CERBERUS_02_call_records.csv", "CERBERUS_03_financial.csv",
              "CERBERUS_04_surveillance.txt", "CERBERUS_05_investor_ledger.csv",
              "CERBERUS_06_email_receipts.csv"],
        expected={"LAYERING_STRUCTURE", "SHELL_LAYERING_NETWORK", "OPERATIONAL_SECURITY",
                  "ENFORCEMENT_HISTORY_ESCALATION", "NETWORK_HUB"}),
    "NIGHTJAR": dict(
        docs=["NIGHTJAR_02_call_records.csv", "NIGHTJAR_03_financial.csv",
              "NIGHTJAR_04_casenote.txt", "NIGHTJAR_05_movement.csv",
              "NIGHTJAR_06_document_fraud.csv"],
        expected={"NETWORK_HUB", "SHELL_LAYERING_NETWORK", "ENFORCEMENT_HISTORY_ESCALATION",
                  "OPERATIONAL_SECURITY", "TIMELINE_CLUSTER"}),
}


def run_case(name, cfg):
    all_results, all_ents, all_rels = [], [], []
    for doc in cfg["docs"]:
        with open(os.path.join(DESK, doc), "rb") as f:
            fbs = f.read()
        result = ingest_file(fbs, doc, "system", True)
        if not result.get("success"):
            continue
        srows = result.get("structured_rows", [])
        psubj = result.get("primary_subject", "") or doc.rsplit(".", 1)[0]
        person, _ = resolve_entity_from_documents(psubj, srows, doc)
        if not person.get("confirmed_name"):
            person["confirmed_name"] = psubj
        isr = {"query": psubj, "total": result.get("total_items", 0),
               "results": [{"full_name": psubj, "platform": f"Document: {doc}",
                            "snippet": "", "url": "", "confidence": 70}], "errors": {}}
        _, ents, rels = build_graph_from_person(person, isr)
        se, sr = extract_relationships_from_structured_rows(srows, doc)
        ents.extend(se); rels.extend(sr)
        all_results.append(result); all_ents.extend(ents); all_rels.extend(rels)

    primary_person, _pm = resolve_entity_from_multiple_docs(all_results)
    if not (primary_person and primary_person.get("confirmed_name") not in (None, "", "Unknown Subject")):
        primary_person = {"confirmed_name": cfg["docs"][0].split("_")[0]}
    seen, merged_ents = set(), []
    for e in all_ents:
        if e["id"] not in seen:
            merged_ents.append(e); seen.add(e["id"])
    G_full = build_graph(merged_ents, all_rels)
    inject_keyword_flags_from_docs(primary_person, all_results)
    tl = build_timeline_from_fusion(primary_person or {}, [
        {"filename": r.get("filename", ""), "raw_text": r.get("raw_text", ""),
         "structured_rows": r.get("structured_rows", [])} for r in all_results])
    try:
        clean_person_object(primary_person)
    except Exception:
        pass

    # EXACT live §09B input shapes (report_generator.py:2652)
    records = [row for r in all_results for row in (r.get("structured_rows") or [])
               if isinstance(row, dict)]
    texts = [str(r.get("raw_text") or r.get("full_text") or "") for r in all_results]

    result = run_pattern_analysis(
        person=primary_person,
        entities=merged_ents,
        flags=list(primary_person.get("anomaly_flags", [])),
        timeline=tl,
        graph=G_full,
        phones=primary_person.get("phones_found", []),
        financial_data=None,          # ← live: asset panel empty; records is the source
        records=records,
        texts=texts,
    )
    fired = {p.pattern_id for p in result["patterns"]}
    c = result["counts"]
    exp = cfg["expected"]
    print(f"\n{'='*74}\n{name}  (case_type={result['case_type_detected']})\n{'='*74}")
    print(f"  transactions={c['transactions']}  organizations={c['organizations']}  "
          f"records_in={len(records)}")
    for p in result["patterns"]:
        print(f"   FIRED  {p.pattern_id:32} [{p.confidence}] {p.case_type}")
    print(f"  expected fired : {sorted(exp & fired)}")
    miss = exp - fired
    if miss:
        print(f"  *** MISSING    : {sorted(miss)}")
    extra = fired - exp
    if extra:
        print(f"  (also fired)   : {sorted(extra)}")
    return name, exp, fired


if __name__ == "__main__":
    summ = [run_case(n, c) for n, c in CASES.items()]
    print(f"\n{'#'*74}\nSUMMARY\n{'#'*74}")
    for name, exp, fired in summ:
        miss = exp - fired
        print(f"  {name:10} {len(exp & fired)}/{len(exp)} expected"
              f"{'  -> MISSING ' + str(sorted(miss)) if miss else '  -> ALL EXPECTED FIRED'}")
