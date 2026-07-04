"""
TEMP DIAGNOSTIC (delete after use) — three-genre generality proof for §09B.

Runs IRONCLAD (cyber), CERBERUS (financial/Ponzi) and NIGHTJAR (trafficking)
through the SAME run_pattern_analysis the live pipeline calls, with inputs built
the same way (flags via keyword scan, graph via relationship_mapper, timeline via
build_timeline_from_fusion, financial rows + all structured records). No mapping
code is case-aware: this harness only assembles generic shapes from each case's
files. Run:  PYTHONUTF8=1 python diag_three_genre.py
"""
import os, csv, json

from modules.report_generator import inject_keyword_flags_from_docs
from modules.pattern_engine import run_pattern_analysis
from modules.relationship_mapper import extract_relationships_from_structured_rows, build_graph
from modules.timeline import build_timeline_from_fusion

DESK = os.path.join(os.path.expanduser("~"), "OneDrive", "Desktop")

CASES = {
    "IRONCLAD": dict(
        docs=["IRONCLAD_01_profile.pdf", "IRONCLAD_02_call_records.csv",
              "IRONCLAD_03_crypto_flow.csv", "IRONCLAD_04_incident_log.txt",
              "IRONCLAD_05_platform_metadata.csv", "IRONCLAD_06_email_receipts.csv"],
        csvs=["IRONCLAD_02_call_records.csv", "IRONCLAD_03_crypto_flow.csv",
              "IRONCLAD_05_platform_metadata.csv", "IRONCLAD_06_email_receipts.csv"],
        cdr="IRONCLAD_02_call_records.csv", financial=["IRONCLAD_03_crypto_flow.csv"],
        text=["IRONCLAD_04_incident_log.txt"],
        expected={"ANTI_FORENSIC_BEHAVIOUR", "COUNTER_SURVEILLANCE",
                  "OPERATIONAL_SECURITY", "LAYERING_STRUCTURE"}),
    "CERBERUS": dict(
        docs=["CERBERUS_02_call_records.csv", "CERBERUS_03_financial.csv",
              "CERBERUS_04_surveillance.txt", "CERBERUS_05_investor_ledger.csv",
              "CERBERUS_06_email_receipts.csv"],
        csvs=["CERBERUS_02_call_records.csv", "CERBERUS_03_financial.csv",
              "CERBERUS_05_investor_ledger.csv", "CERBERUS_06_email_receipts.csv"],
        cdr="CERBERUS_02_call_records.csv",
        financial=["CERBERUS_03_financial.csv", "CERBERUS_05_investor_ledger.csv"],
        text=["CERBERUS_04_surveillance.txt"],
        expected={"LAYERING_STRUCTURE", "SHELL_LAYERING_NETWORK", "OPERATIONAL_SECURITY",
                  "ENFORCEMENT_HISTORY_ESCALATION", "NETWORK_HUB"}),
    "NIGHTJAR": dict(
        docs=["NIGHTJAR_02_call_records.csv", "NIGHTJAR_03_financial.csv",
              "NIGHTJAR_04_casenote.txt", "NIGHTJAR_05_movement.csv",
              "NIGHTJAR_06_document_fraud.csv"],
        csvs=["NIGHTJAR_02_call_records.csv", "NIGHTJAR_03_financial.csv",
              "NIGHTJAR_05_movement.csv", "NIGHTJAR_06_document_fraud.csv"],
        cdr="NIGHTJAR_02_call_records.csv", financial=["NIGHTJAR_03_financial.csv"],
        text=["NIGHTJAR_04_casenote.txt"],
        expected={"NETWORK_HUB", "SHELL_LAYERING_NETWORK", "ENFORCEMENT_HISTORY_ESCALATION",
                  "OPERATIONAL_SECURITY", "TIMELINE_CLUSTER"}),
}


def _read(name):
    try:
        with open(os.path.join(DESK, name), encoding="utf-8", errors="ignore") as f:
            return f.read()
    except Exception:
        return ""


def _rows(name):
    try:
        with open(os.path.join(DESK, name), encoding="utf-8", errors="ignore") as f:
            return list(csv.DictReader(f))
    except Exception:
        return []


def run_case(name, cfg):
    subject = json.load(open(os.path.join(DESK, f"{name}_MANIFESTATION.json")))["subject"]
    phones = json.load(open(os.path.join(DESK, f"{name}_MANIFESTATION.json"))) \
        .get("junk_seeded", {}).get("valid_phones", [])

    # records = every structured row from every CSV
    records = []
    for c in cfg["csvs"]:
        records.extend(_rows(c))

    # flags = keyword scan over raw doc text (txt + csv text), exactly like pipeline
    person = {"confirmed_name": subject, "name": subject,
              "phones_found": [{"number": p} for p in phones], "anomaly_flags": []}
    raw_documents = [{"filename": n, "raw_text": _read(n)} for n in cfg["text"]] + \
                    [{"filename": c, "raw_text": _read(c)} for c in cfg["csvs"]]
    inject_keyword_flags_from_docs(person, raw_documents)
    flags = list(person["anomaly_flags"])

    # graph + entities from the call-detail records (real relationship_mapper)
    ents, rels = extract_relationships_from_structured_rows(_rows(cfg["cdr"]), cfg["cdr"])
    graph = build_graph(ents, rels)

    # financial_data = the financial CSV rows
    financial = []
    for f in cfg["financial"]:
        financial.extend([{"source_file": f, **r} for r in _rows(f)])

    # timeline from raw text + structured rows (real builder)
    tl_docs = [{"filename": n, "raw_text": _read(n),
                "structured_rows": _rows(n) if n.endswith(".csv") else []} for n in cfg["docs"]]
    timeline = build_timeline_from_fusion(person, tl_docs)

    texts = [_read(n) for n in cfg["docs"]]
    result = run_pattern_analysis(person=person, entities=ents, flags=flags,
                                  timeline=timeline, graph=graph,
                                  phones=person["phones_found"],
                                  financial_data=financial, records=records, texts=texts)
    fired = {p.pattern_id for p in result["patterns"]}
    exp = cfg["expected"]
    print(f"\n{'='*72}\n{name}  (case_type={result['case_type_detected']})\n{'='*72}")
    print("  counts:", result["counts"])
    for p in result["patterns"]:
        print(f"   FIRED  {p.pattern_id:32} [{p.confidence}]")
    miss = exp - fired
    print(f"  expected fired : {sorted(exp & fired)}")
    if miss:
        print(f"  *** MISSING    : {sorted(miss)}")
    extra = fired - exp
    if extra:
        print(f"  (also fired)   : {sorted(extra)}")
    return name, exp, fired


if __name__ == "__main__":
    summary = [run_case(n, c) for n, c in CASES.items()]
    print(f"\n{'#'*72}\nSUMMARY\n{'#'*72}")
    ok = True
    for name, exp, fired in summary:
        miss = exp - fired
        status = "ALL EXPECTED FIRED" if not miss else f"MISSING {sorted(miss)}"
        if miss:
            ok = False
        print(f"  {name:10} {len(exp & fired)}/{len(exp)} expected  -> {status}")
    print("\n", "THREE-GENRE PROOF PASSED" if ok else "NOT ALL EXPECTED PATTERNS FIRED")
