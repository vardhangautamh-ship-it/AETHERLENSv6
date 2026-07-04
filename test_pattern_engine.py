"""
Step 3 verification — modules/pattern_engine.

Confirms: build→analyze wiring, STRONG-first ordering, deterministic case-type
detection, the zero-pattern path, and byte-identical output across repeated runs
(same case files → same patterns → same order, every run).
Run: python test_pattern_engine.py
"""
import sys
from dataclasses import asdict
import networkx as nx

from modules.pattern_engine import run_pattern_analysis, analyze_ontology
from modules.ontology import build_ontology
import modules.pattern_rules as PR

results = []
def check(label, ok):
    results.append(bool(ok)); print(f"  [{'PASS' if ok else 'FAIL'}] {label}")


# ── a rich financial+cyber case (shapes the real pipeline produces) ───────────
g = nx.Graph()
g.add_node("Rohan Verma", type="person")
for a in ("Assoc One", "Assoc Two"):
    g.add_node(a, type="person"); g.add_edge("Rohan Verma", a)

person = {
    "confirmed_name": "Rohan Verma",
    "anomaly_flags": [
        "Active lookout circular (LOC) issued 2023-02-01",
        "ED enforcement case 2018-06-01", "DRI proceedings 2020-08-01", "NCB action 2022-03-01",
        "CERT-In inquiry notice served 2023-02-01",
        "Subject uses ProtonMail and Telegram", "Foreign VPN exit node from Switzerland",
        "Device data wiped 2023-02-05 anti-forensic deletion",
        "Subject claims personal research", "Data egress of 240 GB observed",
    ],
    "phones_found": [{"number": "+919820144109", "type": "burner"},
                     {"number": "+919820144110", "tags": ["burner"]},
                     {"number": "+14155552671"}],
}
entities = {"organizations": [{"name": "Zenith Trading FZE", "type": "shell", "jurisdiction": "UAE"}],
            "properties": [{"jurisdiction": "Dubai", "type": "apartment"}]}
financial = {"transactions": [
    {"date": "2023-01-02", "direction": "credit", "amount": 45000, "structured": True},
    {"date": "2023-01-05", "direction": "credit", "amount": 180000},
    {"date": "2023-01-09", "direction": "debit", "amount": 900000, "cross_border": True}]}
timeline = {"events": [{"date": "2023-03-01", "significance": "HIGH"},
                       {"date": "2023-03-03", "significance": "HIGH"},
                       {"date": "2023-03-05", "significance": "HIGH"}]}

res = run_pattern_analysis(person, entities, person["anomaly_flags"], timeline, g,
                           person["phones_found"], financial)

print("=" * 72)
print("ENGINE — structured result")
print("=" * 72)
print("  case_type:", res["case_type_detected"])
print("  patterns :", [f"{p.pattern_id}/{p.confidence}" for p in res["patterns"]])
check("result has required keys",
      set(res) >= {"case_type_detected", "patterns", "summary_skeleton"})
check("patterns fired", len(res["patterns"]) >= 7)
# Weight tie (financial 6 = cyber 6) → the type with more STRONG matches wins
# (cyber has 3 STRONGs vs financial's 2); fixed priority only breaks a full tie.
check("case_type is cyber (weight tie -> more STRONG matches wins)", res["case_type_detected"] == "cyber")

# STRONG-first ordering
ranks = [{"STRONG": 0, "MODERATE": 1, "WEAK": 2}[p.confidence] for p in res["patterns"]]
check("confidence ordering is non-decreasing (STRONG first)", ranks == sorted(ranks))
check("summary_skeleton matches pattern order",
      res["summary_skeleton"] == [p.plain_explanation for p in res["patterns"]])

print("=" * 72)
print("DETERMINISM — same case → identical result across repeated runs")
print("=" * 72)
def fingerprint(r):
    return ([asdict(p) for p in r["patterns"]], r["case_type_detected"], r["summary_skeleton"])
r1 = fingerprint(run_pattern_analysis(person, entities, person["anomaly_flags"], timeline, g,
                                      person["phones_found"], financial))
r2 = fingerprint(run_pattern_analysis(person, entities, person["anomaly_flags"], timeline, g,
                                      person["phones_found"], financial))
r3 = fingerprint(run_pattern_analysis(person, entities, person["anomaly_flags"], timeline, g,
                                      person["phones_found"], financial))
check("3 repeated runs are byte-identical", r1 == r2 == r3)

print("=" * 72)
print("CASE-TYPE — cyber-dominant case")
print("=" * 72)
cyber_person = {"confirmed_name": "Neo",
                "anomaly_flags": ["Subject uses ProtonMail and Signal", "Foreign VPN exit node",
                                  "CERT-In inquiry 2023-02-01", "data wiped 2023-02-03",
                                  "claims personal research", "data egress 500 GB"]}
cres = run_pattern_analysis(cyber_person, {}, cyber_person["anomaly_flags"], None, None, None, None)
print("  fired:", [p.pattern_id for p in cres["patterns"]], "→", cres["case_type_detected"])
check("cyber case detected as cyber", cres["case_type_detected"] == "cyber")

print("=" * 72)
print("ZERO — no patterns path")
print("=" * 72)
zres = run_pattern_analysis({"confirmed_name": "Nobody"})
print("  ", zres["case_type_detected"], zres["patterns"])
check("no patterns → empty list", zres["patterns"] == [])
check("no patterns → undetermined", zres["case_type_detected"] == "undetermined")
check("no patterns → empty skeleton", zres["summary_skeleton"] == [])

print("=" * 72)
passed, total = sum(results), len(results)
print(f"SUMMARY: {passed}/{total} checks passed")
if passed == total:
    print("ALL PATTERN-ENGINE CHECKS PASSED"); sys.exit(0)
sys.exit(1)
