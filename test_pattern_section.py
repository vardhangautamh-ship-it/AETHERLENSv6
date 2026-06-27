"""
Step 4 verification — §09B Pattern Analysis section wiring in report_generator.

Confirms the deterministic section builder, the rendered text/structure, the
pass-through into the flat PDF data, the section ordering (09B sits right after
09), and that the section is identical across repeated runs. Does NOT run the
full Streamlit app. Run: python test_pattern_section.py
"""
import sys
import networkx as nx

import modules.report_generator as RG
from modules.pattern_engine import run_pattern_analysis

results = []
def check(label, ok):
    results.append(bool(ok)); print(f"  [{'PASS' if ok else 'FAIL'}] {label}")


# ── a rich case via the engine ────────────────────────────────────────────────
g = nx.Graph()
g.add_node("Rohan Verma", type="person")
for a in ("Assoc One", "Assoc Two"):
    g.add_node(a, type="person"); g.add_edge("Rohan Verma", a)
person = {"confirmed_name": "Rohan Verma",
          "anomaly_flags": ["Active lookout circular (LOC) 2023-02-01", "ED enforcement 2018-06-01",
                            "DRI 2020-08-01", "NCB 2022-03-01", "CERT-In inquiry 2023-02-01",
                            "Uses ProtonMail and Telegram", "Foreign VPN exit from Switzerland",
                            "data wiped 2023-02-05 anti-forensic", "claims personal research",
                            "data egress 240 GB"],
          "phones_found": [{"number": "+919820144109", "type": "burner"},
                           {"number": "+919820144110", "tags": ["burner"]},
                           {"number": "+14155552671"}]}
entities = {"organizations": [{"name": "Zenith Trading FZE", "type": "shell", "jurisdiction": "UAE"}],
            "properties": [{"jurisdiction": "Dubai"}]}
financial = {"transactions": [{"date": "2023-01-02", "direction": "credit", "amount": 45000, "structured": True},
                              {"date": "2023-01-09", "direction": "debit", "amount": 900000, "cross_border": True}]}

result = run_pattern_analysis(person, entities, person["anomaly_flags"], None, g,
                              person["phones_found"], financial)
sec = RG._build_pattern_analysis_section(result)

print("=" * 72)
print("SECTION BUILDER")
print("=" * 72)
check("header marks deterministic", "[DETERMINISTIC ANALYSIS]" in sec["header"])
check("case_type present", sec["case_type"] in ("financial", "cyber", "general", "undetermined"))
check("patterns list of dicts with required keys",
      sec["patterns"] and all({"pattern_id", "pattern_name", "confidence", "explanation",
                               "triggers", "sources"} <= set(p) for p in sec["patterns"]))
check("pattern_count matches", sec["pattern_count"] == len(sec["patterns"]))
content = sec["content"]
check("content has deterministic header line", content.startswith("[DETERMINISTIC ANALYSIS]"))
check("content lists detected case type", "Detected case type:" in content)
check("content shows a confidence tag", "[STRONG]" in content or "[MODERATE]" in content)

print("=" * 72)
print("PDF PASS-THROUGH (_sections_to_pdf_data)")
print("=" * 72)
pdf_data = RG._sections_to_pdf_data({"pattern_analysis": sec, "anomalies_and_flags": {"flags": []}})
check("pattern_analysis survives into flat PDF data", "pattern_analysis" in pdf_data)
check("PDF data keeps structured patterns", pdf_data["pattern_analysis"]["patterns"] == sec["patterns"])

print("=" * 72)
print("SECTION ORDER (PDF section_defs has 09B right after 09)")
print("=" * 72)
# read the source to confirm ordering without rendering a PDF
src = open("modules/report_generator.py", encoding="utf-8").read()
i09 = src.find('"anomalies"),')
i09b = src.find('"pattern_analysis"),')
i10 = src.find('"data_gaps"),')
check("09B registered between 09 and 10", -1 < i09 < i09b < i10)

print("=" * 72)
print("EMPTY-CASE rendering")
print("=" * 72)
empty_sec = RG._build_pattern_analysis_section(run_pattern_analysis({"confirmed_name": "Nobody"}))
check("zero patterns → undetermined", empty_sec["case_type"] == "undetermined")
check("zero patterns → explicit 'no correlations' line",
      "No significant cross-pattern correlations detected" in empty_sec["content"])
check("zero patterns → empty list (no invented patterns)", empty_sec["patterns"] == [])

print("=" * 72)
print("DETERMINISM")
print("=" * 72)
s2 = RG._build_pattern_analysis_section(run_pattern_analysis(person, entities, person["anomaly_flags"],
                                                             None, g, person["phones_found"], financial))
check("section identical across runs", sec == s2)

print("=" * 72)
passed, total = sum(results), len(results)
print(f"SUMMARY: {passed}/{total} checks passed")
if passed == total:
    print("ALL PATTERN-SECTION CHECKS PASSED"); sys.exit(0)
sys.exit(1)
