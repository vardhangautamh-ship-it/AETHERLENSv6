"""
Smoke-test: generalized _tactical_plan_fallback covers all 4 case types.
"""
import sys, os, types, pathlib
cfg = types.ModuleType("config")
cfg.GEMINI_API_KEY = ""; cfg.GEMINI_ENDPOINT = ""; cfg.DATABASE_PATH = ":memory:"
cfg.EXPORTS_DIR = pathlib.Path("exports"); cfg.bedrock_client = None
cfg.get_bedrock_client = lambda: (None, "")
sys.modules["config"] = cfg

from modules.ai_agents import _tactical_plan_fallback, run_tactical_plan_agent

results = []
def check(label, ok):
    results.append(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")

# ── Case A: Financial/PMLA (Harshvardhan-style) ───────────────────────────────
print("=" * 60)
print("CASE A: Financial / PMLA (Harshvardhan Gautam)")
print("=" * 60)
person_a = {"confirmed_name": "Harshvardhan Gautam"}
anomalies_a = [
    "PMLA flagged", "HAWALA transfer detected", "ED indicator present",
    "CERT-In inquiry confirmed", "IT Act violation flagged",
]
r = _tactical_plan_fallback(person_a, anomalies_a, [])
a2 = r["actions"][1]["title"]
print(f"  Action 2: {a2}")
print(f"  Summary:  {r['case_summary']}")
check("Action 2 = FREEZE BANK ACCOUNTS", "FREEZE" in a2)

# ── Case B: Pure Cyber (no financial flags) ───────────────────────────────────
print()
print("=" * 60)
print("CASE B: Cyber / IT Act (Rohan Sharma)")
print("=" * 60)
person_b = {"confirmed_name": "Rohan Sharma"}
anomalies_b = [
    "IT Act Section 66 violation", "Unauthorized server access",
    "Platform data scraping confirmed",
]
r = _tactical_plan_fallback(person_b, anomalies_b, [])
a2 = r["actions"][1]["title"]
print(f"  Action 2: {a2}")
print(f"  Summary:  {r['case_summary']}")
check("Action 2 = JUDICIAL INTERCEPTION ORDER", "INTERCEPTION" in a2)

# ── Case C: Drug/Narcotics (no financial, no cyber) ───────────────────────────
print()
print("=" * 60)
print("CASE C: Drug / NDPS (Vikram Nair)")
print("=" * 60)
person_c = {"confirmed_name": "Vikram Nair"}
anomalies_c = [
    "NDPS flagged", "NCB investigation open",
    "Narcotics supply chain suspected",
]
r = _tactical_plan_fallback(person_c, anomalies_c, [])
a2 = r["actions"][1]["title"]
print(f"  Action 2: {a2}")
print(f"  Summary:  {r['case_summary']}")
# No financial/cyber → general else → PRESERVE ALL PLATFORM & CLOUD DATA
check("Action 2 = PRESERVE ALL PLATFORM & CLOUD DATA", "PRESERVE" in a2)
check("case_summary mentions Drug/Narcotics", "Drug" in r["case_summary"] or "Narcotics" in r["case_summary"])

# ── Case D: Pure General OSINT (no flags at all) ──────────────────────────────
print()
print("=" * 60)
print("CASE D: General OSINT (Priya Kapoor)")
print("=" * 60)
person_d = {"confirmed_name": "Priya Kapoor"}
r = _tactical_plan_fallback(person_d, [], [])
a2 = r["actions"][1]["title"]
print(f"  Action 2: {a2}")
print(f"  Summary:  {r['case_summary']}")
check("Action 2 = PRESERVE ALL PLATFORM & CLOUD DATA", "PRESERVE" in a2)
check("case_summary mentions General OSINT", "General OSINT" in r["case_summary"])

# ── Structural integrity: all 4 cases ─────────────────────────────────────────
print()
print("=" * 60)
print("STRUCTURAL CHECKS (all 4 case types)")
print("=" * 60)
cases = [
    ("Financial", person_a, anomalies_a),
    ("Cyber",     person_b, anomalies_b),
    ("Drug",      person_c, anomalies_c),
    ("OSINT",     person_d, []),
]
for label, person, anomalies in cases:
    r       = _tactical_plan_fallback(person, anomalies, [])
    actions = r["actions"]
    ids     = [a["id"] for a in actions]
    a1_par  = actions[0]["parallel_with"]
    a6_dep  = actions[5]["depends_on"]
    a1_prio = actions[0]["priority"]
    a6_prio = actions[5]["priority"]
    print(f"\n  [{label}]")
    check(f"{label}: exactly 6 actions {ids}", len(actions) == 6 and ids == [1,2,3,4,5,6])
    check(f"{label}: Action 1 parallel_with=[2]", a1_par == [2])
    check(f"{label}: Action 1 priority=CRITICAL", a1_prio == "CRITICAL")
    check(f"{label}: Action 6 depends_on=[1,2,3,4,5]", a6_dep == [1,2,3,4,5])
    check(f"{label}: Action 6 priority=MEDIUM", a6_prio == "MEDIUM")
    check(f"{label}: has case_summary str", bool(r.get("case_summary")))
    check(f"{label}: has critical_warning str", bool(r.get("critical_warning")))

# ── run_tactical_plan_agent new signature ─────────────────────────────────────
print()
print("=" * 60)
print("NEW SIGNATURE: run_tactical_plan_agent(person, assets, report_data, user_id)")
print("=" * 60)
report_data = {"anomalies": anomalies_a}
r = run_tactical_plan_agent(person_a, [], report_data, "test_user")
check("agent='TacticalPlanAgent'", r.get("agent") == "TacticalPlanAgent")
check("method field present", bool(r.get("method")))
check("generated_at present", bool(r.get("generated_at")))
check("exactly 6 actions returned", len(r.get("actions", [])) == 6)

# ── Summary ───────────────────────────────────────────────────────────────────
print()
print("=" * 60)
passed = sum(results)
total  = len(results)
print(f"SUMMARY: {passed}/{total} checks passed")
if passed == total:
    print("ALL CHECKS PASSED - Tactical Plan works for every case type")
else:
    print("SOME CHECKS FAILED - review above")
