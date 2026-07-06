"""
Phase 1 Step 9 — §09C Immigration Violation Profile.

Drives the SYNTHETIC MERIDIAN immigration case (fictional, investigation-side,
no tradecraft — test_cases/MERIDIAN_*) through the real deterministic report
core: ingest_file -> build_timeline_from_fusion -> build_ontology ->
analyze_ontology -> _build_pattern_analysis_section -> the new
_build_immigration_profile_section, then verifies:

  * the profile populates with the immigration indicators, each cited;
  * the evidence-based-only disclaimer is visible in the section;
  * the §16 risk-contribution line is present;
  * the section is absent when no immigration pattern fired (honest-empty);
  * the PDF data mapping carries it, and generate_pdf renders without error.

No LLM and no network: the person object is supplied directly (as entity
resolution would yield it) so the run is deterministic. The section is built at
the single §09B convergence point in _generate_report_inner, so both the OSINT
and FUSION pipelines receive it identically — there is no per-pipeline path.

Run: PYTHONUTF8=1 python test_immigration_profile.py
"""
import os, sys

from modules.data_ingestion import ingest_file
from modules.timeline import build_timeline_from_fusion
from modules.ontology import build_ontology
from modules.pattern_engine import analyze_ontology
from modules.report_generator import (
    _build_pattern_analysis_section, _build_immigration_profile_section,
    _sections_to_pdf_data, generate_pdf, _IMMIGRATION_PROFILE_DISCLAIMER,
)

results = []
def check(label, ok):
    results.append(bool(ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")

CASE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_cases")
CASE = ["MERIDIAN_01_casenote.txt", "MERIDIAN_02_call_records.csv",
        "MERIDIAN_03_remittance.csv", "MERIDIAN_04_document_fraud.csv",
        "MERIDIAN_05_movement.csv", "MERIDIAN_06_immigration_record.csv"]

# ── Ingest the synthetic case (deterministic) ─────────────────────────────────
all_results = []
for doc in CASE:
    with open(os.path.join(CASE_DIR, doc), "rb") as f:
        r = ingest_file(f.read(), doc, "system", True)
    if r.get("success"):
        all_results.append(r)

print("=" * 72)
print("INGEST — synthetic MERIDIAN immigration case")
print("=" * 72)
check("all 6 case files ingested", len(all_results) == 6)

# Subject phone lines, as multi-doc entity resolution would yield them.
phones = ["+91-96130-70011", "+91-96130-70012", "+91-96130-70013",
          "+91-96130-70014", "+91-96130-70015", "+91-96130-70016",
          "+91-96130-70017", "+880-1711-500431", "+977-9801-224510"]
person = {"confirmed_name": "Kabir Anwar Farhadi", "phones_found": phones}

tl = build_timeline_from_fusion(person, [
    {"filename": r.get("filename", ""), "raw_text": r.get("raw_text", ""),
     "structured_rows": r.get("structured_rows", [])} for r in all_results])
records = [row for r in all_results for row in (r.get("structured_rows") or [])
           if isinstance(row, dict)]
texts = [str(r.get("raw_text") or "") for r in all_results]

onto = build_ontology(person=person, entities=[], flags=[], timeline=tl, graph=None,
                      phones=phones, financial_data=None, records=records, texts=texts,
                      documents=all_results)
res = analyze_ontology(onto)
pa_section = _build_pattern_analysis_section(res)

print("=" * 72)
print("§09C — Immigration Violation Profile populates from detected patterns")
print("=" * 72)
check("case type detected as immigration", res["case_type_detected"] == "immigration")

sec = _build_immigration_profile_section(pa_section)
check("profile section built (not None)", sec is not None)

imm_ids = {p.pattern_id for p in res["patterns"] if p.case_type == "immigration"}
check(f"multiple immigration indicators detected ({len(imm_ids)})", len(imm_ids) >= 3)
check("profile pattern_count matches immigration indicators",
      sec and sec.get("pattern_count") == len(imm_ids))

joined = "\n".join(sec.get("items", [])) if sec else ""
check("DOCUMENT_FRAUD_CLUSTER cited in profile",
      "DOCUMENT FRAUD CLUSTER" in joined)
check("profile carries per-indicator cited evidence lines",
      sec and sum(1 for it in sec["items"] if it.strip().startswith("evidence:")) >= 3)
check("profile cites source files",
      any("sources:" in it for it in (sec.get("items") if sec else [])))
check("§16 risk-contribution line present",
      any("RISK CONTRIBUTION:" in it for it in (sec.get("items") if sec else [])))

# The HARD ETHICAL CONSTRAINT is visible in the section itself.
check("evidence-based-only disclaimer visible in the profile",
      sec and _IMMIGRATION_PROFILE_DISCLAIMER in sec["items"])
check("disclaimer forbids identity attributes explicitly",
      "nationality, ethnicity, or religion" in _IMMIGRATION_PROFILE_DISCLAIMER)

check("content header labels it DETERMINISTIC and evidence-based",
      sec and "DETERMINISTIC ANALYSIS" in sec["content"]
      and "Evidence-based only" in sec["content"])

print("=" * 72)
print("WIRE-IN — PDF data mapping + honest-empty behaviour")
print("=" * 72)

# The PDF flat-data mapping carries the profile as cited lines.
pdf_map = _sections_to_pdf_data({"pattern_analysis": pa_section,
                                 "immigration_profile": sec})
check("immigration_profile mapped into PDF data as a list",
      isinstance(pdf_map.get("immigration_profile"), list)
      and len(pdf_map["immigration_profile"]) > len(sec["items"]) - 1)

# generate_pdf renders without error when the profile is present.
try:
    pdf_bytes = generate_pdf(pdf_map, "Kabir Anwar Farhadi", "system", "FUSION")
    check("generate_pdf renders a non-empty PDF with §09C present",
          isinstance(pdf_bytes, (bytes, bytearray)) and len(pdf_bytes) > 1000)
except Exception as e:
    check(f"generate_pdf renders a non-empty PDF with §09C present (raised {e})", False)

# Honest-empty: a §09B section with no immigration patterns yields NO profile.
non_imm = {"patterns": [
    {"pattern_id": "LAYERING_STRUCTURE", "pattern_name": "Layering Structure",
     "case_type": "financial", "confidence": "STRONG", "explanation": "x",
     "triggers": ["a"], "sources": ["b"]}],
    "case_type": "financial", "immigration_risk": {"points": 0, "factors": []}}
check("no immigration patterns → no profile section (absent, not empty noise)",
      _build_immigration_profile_section(non_imm) is None)
check("empty / malformed input → None (defensive)",
      _build_immigration_profile_section({}) is None
      and _build_immigration_profile_section(None) is None)

print("=" * 72)
passed, total = sum(results), len(results)
print(f"SUMMARY: {passed}/{total} checks passed")
if passed == total:
    print("ALL IMMIGRATION-PROFILE CHECKS PASSED"); sys.exit(0)
sys.exit(1)
