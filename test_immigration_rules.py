"""
Isolated regression suite for the Phase 1 IMMIGRATION rules in
modules/pattern_rules.py (rules 11-16).

For every rule: one POSITIVE input that must fire (with expected id), and one
NEGATIVE input that must stay silent. Plus:
  * the FOREIGN_SIM sole-signal guard — foreign-origin phone lines must NEVER
    fire anything alone, or with only one corroborating evidence class;
  * an IDENTITY-BLINDNESS check — flags carrying nationality/religion words but
    no behavioural evidence must fire NO immigration rule (the HARD ETHICAL
    CONSTRAINT: indicators are evidence-based, never identity attributes);
  * a determinism check over a rich immigration ontology.

Run: python test_immigration_rules.py
"""
import sys
from dataclasses import asdict
from types import SimpleNamespace as NS

import networkx as nx

import modules.pattern_rules as PR

results = []
def check(label, ok):
    results.append(bool(ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")


# ── synthetic ontology + entity factories (duck-typed to the rule contract) ──
def onto(**kw):
    base = dict(subject_name="", subject=None, flags=[], graph=nx.Graph(),
                persons=[], phones=[], organizations=[], transactions=[],
                properties=[], comm_channels=[], legal_proceedings=[],
                deletion_events=[], timeline_events=[], locations=[])
    base.update(kw)
    return NS(**base)

def phone(number="+919000000001", type="domestic", country="", source="cdr.csv"):
    return NS(number=number, type=type, country=country, source=source)
def txn(date="2023-01-01", direction="out", amount=40000, cross_border=True,
        counterparty="", structured=False, source="bank.csv"):
    return NS(date=date, direction=direction, amount=amount, cross_border=cross_border,
              counterparty=counterparty, structured=structured, source=source)
def legal(agency="FRRO Delhi", status="active", date="2023-02-01", case_ref="N-1",
          kind="notice", source="notice.pdf"):
    return NS(agency=agency, status=status, date=date, case_ref=case_ref, kind=kind, source=source)
def loc(name, kind="stated", source="movement.csv"):
    return NS(name=name, kind=kind, source=source)
def tevent(date="2023-03-01", significance="HIGH", source="timeline", description=""):
    return NS(date=date, significance=significance, source=source, description=description)


print("=" * 72)
print("POSITIVE — each immigration rule fires on a crafted input")
print("=" * 72)

# 11 FOREIGN_SIM_CORROBORATED — foreign line + 2 corroborating classes
m = PR.rule_foreign_sim_corroborated(onto(
    phones=[phone(number="+8801700000001", type="international")],
    transactions=[txn(counterparty="Agent A"), txn(counterparty="Agent A"),
                  txn(counterparty="Agent B")],
    flags=["Forged passport recovered during search"]))
check("FOREIGN_SIM_CORROBORATED fires (2 classes)",
      m is not None and m.pattern_id == "FOREIGN_SIM_CORROBORATED"
      and m.case_type == "immigration")
check("FOREIGN_SIM triggers say never-sole",
      m is not None and any("never sole" in t for t in m.triggers_met))

# 12 REMITTANCE_CORRIDOR — 6 small transfers, repeated counterparty → STRONG
m = PR.rule_remittance_corridor(onto(
    transactions=[txn(amount=35000, counterparty="Hawala X") for _ in range(6)]))
check("REMITTANCE_CORRIDOR fires STRONG",
      m is not None and m.pattern_id == "REMITTANCE_CORRIDOR" and m.confidence == "STRONG")

# WEAK tier: exactly 4 small transfers, all distinct counterparties
m = PR.rule_remittance_corridor(onto(
    transactions=[txn(amount=30000, counterparty=f"R{i}") for i in range(4)]))
check("REMITTANCE_CORRIDOR WEAK tier (4 small, no repetition)",
      m is not None and m.confidence == "WEAK")

# 13 DOCUMENT_FRAUD_CLUSTER — 2 indicators + official notice → STRONG
m = PR.rule_document_fraud_cluster(onto(
    flags=["Counterfeit visa sticker identified on passport page 7",
           "Tampered work permit seized at premises"],
    legal_proceedings=[legal(kind="notice")]))
check("DOCUMENT_FRAUD_CLUSTER fires STRONG (2 indicators + notice)",
      m is not None and m.pattern_id == "DOCUMENT_FRAUD_CLUSTER" and m.confidence == "STRONG")

# MODERATE tier: 2 indicators, no proceeding
m = PR.rule_document_fraud_cluster(onto(
    flags=["Forged passport recovered", "Fake residence permit found in vehicle"]))
check("DOCUMENT_FRAUD_CLUSTER MODERATE (2 indicators)",
      m is not None and m.confidence == "MODERATE")

# 14 SIM_FARMING_SIGNATURE — 5 distinct lines, 2 burners
m = PR.rule_sim_farming_signature(onto(
    phones=[phone(number=f"+9190000000{i}", type=("burner" if i < 2 else "domestic"))
            for i in range(5)]))
check("SIM_FARMING_SIGNATURE fires (5 lines, 2 burners)",
      m is not None and m.pattern_id == "SIM_FARMING_SIGNATURE")

# STRONG via farm-infrastructure flag
m = PR.rule_sim_farming_signature(onto(
    phones=[phone(number=f"+9190000000{i}", type="burner") for i in range(5)],
    flags=["GSM gateway with bulk sim racks recovered"]))
check("SIM_FARMING STRONG on infrastructure flag",
      m is not None and m.confidence == "STRONG")

# 15 BORDER_MOVEMENT_CLUSTER — 3 border locations + 2 movement records → STRONG
m = PR.rule_border_movement_cluster(onto(
    locations=[loc("Petrapole land port"), loc("Hili border checkpost"),
               loc("Changrabandha crossing")],
    flags=["Vehicle transit recorded 2023-04-01", "Movement route Kolkata northbound"]))
check("BORDER_MOVEMENT_CLUSTER fires STRONG (3 locs + 2 moves)",
      m is not None and m.pattern_id == "BORDER_MOVEMENT_CLUSTER" and m.confidence == "STRONG")

# WEAK tier: 2 border locations + 1 movement record
m = PR.rule_border_movement_cluster(onto(
    locations=[loc("Sunauli border"), loc("Raxaul checkpost")],
    flags=["Subject travelled toward the frontier on 2023-04-02"]))
check("BORDER_MOVEMENT_CLUSTER WEAK tier", m is not None and m.confidence == "WEAK")

# 16 ENTRY_RECORD_INCONSISTENCY — 2 indicators + FRRO notice → STRONG
m = PR.rule_entry_record_inconsistency(onto(
    flags=["Visa expired 2022-11-30, overstay of 14 months",
           "No entry record found for the claimed 2021 arrival"],
    legal_proceedings=[legal(agency="FRRO Kolkata", kind="notice")]))
check("ENTRY_RECORD_INCONSISTENCY fires STRONG",
      m is not None and m.pattern_id == "ENTRY_RECORD_INCONSISTENCY"
      and m.confidence == "STRONG")

# WEAK tier: single indicator, no official proceeding
m = PR.rule_entry_record_inconsistency(onto(flags=["Overstay noted in file"]))
check("ENTRY_RECORD_INCONSISTENCY WEAK tier", m is not None and m.confidence == "WEAK")


print("=" * 72)
print("GUARDS — the sole-signal and identity-blindness constraints")
print("=" * 72)

# Foreign-origin lines ALONE (even many of them) must never fire.
check("foreign SIMs alone fire NOTHING", PR.rule_foreign_sim_corroborated(onto(
    phones=[phone(number=f"+880170000000{i}", type="international") for i in range(3)]
)) is None)

# Foreign line + only ONE corroborating class must not fire either.
check("foreign SIM + 1 class stays silent", PR.rule_foreign_sim_corroborated(onto(
    phones=[phone(number="+8801700000001", type="international")],
    flags=["Forged passport recovered"])) is None)

# IDENTITY-BLINDNESS: nationality/religion words with no behavioural evidence
# must fire NO immigration rule. The HARD ETHICAL CONSTRAINT, tested.
identity_only = onto(
    flags=["Subject is a Bangladeshi national", "Subject is Muslim",
           "Family of Nepali origin", "Subject is Hindu"])
fired = [fn(identity_only) for fn in (
    PR.rule_foreign_sim_corroborated, PR.rule_remittance_corridor,
    PR.rule_document_fraud_cluster, PR.rule_sim_farming_signature,
    PR.rule_border_movement_cluster, PR.rule_entry_record_inconsistency)]
check("identity attributes alone fire NO immigration rule",
      all(f is None for f in fired))


print("=" * 72)
print("NEGATIVE — each rule stays silent without its evidence")
print("=" * 72)

check("REMITTANCE silent (3 transfers)", PR.rule_remittance_corridor(onto(
    transactions=[txn(amount=30000) for _ in range(3)])) is None)
check("REMITTANCE silent (large one-off wires, no repetition)",
      PR.rule_remittance_corridor(onto(
          transactions=[txn(amount=5_000_000, counterparty=f"W{i}") for i in range(4)])) is None)
check("DOC_FRAUD silent (1 indicator)", PR.rule_document_fraud_cluster(onto(
    flags=["Forged passport recovered"])) is None)
check("DOC_FRAUD silent (financial forgery, no travel doc)",
      PR.rule_document_fraud_cluster(onto(
          flags=["Forged invoice submitted to bank", "Fake purchase order"])) is None)
check("SIM_FARMING silent (4 lines)", PR.rule_sim_farming_signature(onto(
    phones=[phone(number=f"+9190000000{i}", type="burner") for i in range(4)])) is None)
check("BORDER silent (1 border location)", PR.rule_border_movement_cluster(onto(
    locations=[loc("Petrapole land port")], flags=["Vehicle transit recorded"])) is None)
check("BORDER silent (no movement corroboration)", PR.rule_border_movement_cluster(onto(
    locations=[loc("Sunauli border"), loc("Raxaul checkpost")])) is None)
check("ENTRY silent (no indicators)", PR.rule_entry_record_inconsistency(onto(
    flags=["Subject holds a valid long-term visa"])) is None)


print("=" * 72)
print("DETERMINISM — same input → identical output across repeated runs")
print("=" * 72)
rich = onto(
    phones=[phone(number=f"+880170000000{i}", type="international") for i in range(2)]
           + [phone(number=f"+9190000000{i}", type="burner") for i in range(4)],
    transactions=[txn(amount=35000, counterparty="Hawala X") for _ in range(6)],
    locations=[loc("Petrapole land port"), loc("Hili border checkpost"),
               loc("Changrabandha crossing")],
    flags=["Forged passport recovered", "Tampered work permit seized",
           "Vehicle transit recorded", "Movement route northbound",
           "Overstay of 14 months", "No entry record found"],
    legal_proceedings=[legal(agency="FRRO Kolkata", kind="notice")])

def run_all(o):
    return [asdict(r) for r in (fn(o) for fn in PR.ALL_RULES) if r is not None]

run1, run2, run3 = run_all(rich), run_all(rich), run_all(rich)
check(f"repeated runs identical ({len(run1)} patterns fired)", run1 == run2 == run3)
imm_ids = {r["pattern_id"] for r in run1 if r["case_type"] == "immigration"}
check("all 6 immigration rules fire on the rich immigration ontology",
      imm_ids >= {"FOREIGN_SIM_CORROBORATED", "REMITTANCE_CORRIDOR",
                  "DOCUMENT_FRAUD_CLUSTER", "SIM_FARMING_SIGNATURE",
                  "BORDER_MOVEMENT_CLUSTER", "ENTRY_RECORD_INCONSISTENCY"})
check("every immigration match carries cited triggers and an explanation",
      all(r["triggers_met"] and r["plain_explanation"]
          for r in run1 if r["case_type"] == "immigration"))


print("=" * 72)
print("STEP 7 — case-type detection + immigration risk weighting")
print("=" * 72)
from modules.pattern_engine import analyze_ontology, _detect_case_type, _immigration_risk

# Immigration-dominant ontology (the `rich` fixture) → case type "immigration".
res = analyze_ontology(rich)
check("immigration-dominant case detected as 'immigration'",
      res["case_type_detected"] == "immigration")

# Confidence-weighted risk contribution: every point traces to a fired pattern.
ir = res["immigration_risk"]
expected_pts = min(20, sum({"STRONG": 8, "MODERATE": 5, "WEAK": 2}[p.confidence]
                           for p in res["patterns"] if p.case_type == "immigration"))
check(f"immigration risk points = confidence-weighted sum, capped "
      f"({ir['points']} pts from {len(ir['factors'])} patterns)",
      ir["points"] == expected_pts and ir["points"] > 0
      and len(ir["factors"]) == sum(1 for p in res["patterns"]
                                    if p.case_type == "immigration"))
check("every risk factor cites its pattern id and confidence",
      all(f.get("pattern_id") and f.get("confidence") and f.get("weight", 0) > 0
          for f in ir["factors"]))

# No immigration patterns → zero contribution (existing cases unaffected).
fin_only = onto(transactions=[NS(date="2023-01-01", direction="in", amount=45000,
                                 cross_border=False, counterparty="", structured=True,
                                 source="bank.csv")])
res_fin = analyze_ontology(fin_only)
check("no immigration patterns → 0 risk points",
      res_fin["immigration_risk"]["points"] == 0
      and res_fin["immigration_risk"]["factors"] == [])

# Tie-break consistency: equal weight financial vs immigration → financial wins
# (fixed priority order, same behaviour as the existing financial/cyber tie).
FIN = NS(pattern_id="F", pattern_name="F", case_type="financial",
         confidence="STRONG", triggers_met=[], plain_explanation="", supporting_sources=[])
IMM = NS(pattern_id="I", pattern_name="I", case_type="immigration",
         confidence="STRONG", triggers_met=[], plain_explanation="", supporting_sources=[])
check("full tie financial vs immigration → financial (fixed priority)",
      _detect_case_type([FIN, IMM]) == "financial")
CYB = NS(pattern_id="C", pattern_name="C", case_type="cyber",
         confidence="MODERATE", triggers_met=[], plain_explanation="", supporting_sources=[])
check("immigration outweighs a weaker cyber signal",
      _detect_case_type([IMM, CYB]) == "immigration")
check("cyber-only input still detected as cyber",
      _detect_case_type([CYB, CYB]) == "cyber")

print("=" * 72)
print("STEP 8 — Foreigners/Passport Act tactical templates")
print("=" * 72)
from modules.ai_agents import _tactical_plan_fallback

person = {"confirmed_name": "Test Subject"}

# Immigration evidence in flags → immigration Action 2 with the right statutes.
tp = _tactical_plan_fallback(person, [
    "DOCUMENT_FRAUD_CLUSTER fired: forged passport endorsement",
    "Overstay of 14 months per FRRO records"], [])
a2 = tp["actions"][1]
check("immigration Action 2 = SECURE TRAVEL DOCUMENTS",
      "TRAVEL DOCUMENTS" in a2["title"])
check("Action 2 legal basis cites Passport Act 1967 + Foreigners Act 1946 + BNSS",
      all(s in a2["legal_basis"] for s in ("Passport Act 1967", "Foreigners Act 1946", "BNSS")))
check("case summary labels Immigration Offence",
      "Immigration Offence" in tp["case_summary"])
check("critical warning carries the no-profiling safeguard",
      "nationality" in tp["critical_warning"] and "NOT" in tp["critical_warning"])
check("immigration Action 2 human review is MANDATORY",
      a2.get("human_review_required") is True and "MANDATORY" in a2.get("human_review", ""))
check("all 6 actions carry human-review markers",
      len(tp["actions"]) == 6
      and all(a.get("human_review_required") is True and a.get("human_review")
              for a in tp["actions"]))
check("sequencing intact (Action 3 waits for 1+2; Action 6 waits for all)",
      tp["actions"][2]["depends_on"] == [1, 2]
      and tp["actions"][5]["depends_on"] == [1, 2, 3, 4, 5])

# The §09B case type is authoritative even when flag text lacks the tokens.
tp = _tactical_plan_fallback(person, ["7 phones in use"], [],
                             case_type_hint="immigration")
check("case_type_hint='immigration' drives the template (single source of truth)",
      "TRAVEL DOCUMENTS" in tp["actions"][1]["title"])

# Financial cases are untouched — FREEZE branch still wins on financial flags.
tp = _tactical_plan_fallback(person, ["PMLA flagged", "HAWALA transfer detected"], [])
check("financial flags still produce FREEZE Action 2",
      "FREEZE" in tp["actions"][1]["title"])

# Identity attributes alone must NOT trigger the immigration template.
tp = _tactical_plan_fallback(person, ["Subject is a Bangladeshi national",
                                      "Subject is Muslim"], [])
check("identity attributes alone do NOT trigger the immigration template",
      "TRAVEL DOCUMENTS" not in tp["actions"][1]["title"]
      and "Immigration" not in tp["case_summary"])

print("=" * 72)
passed = sum(results); total = len(results)
print(f"SUMMARY: {passed}/{total} checks passed")
if passed == total:
    print("ALL IMMIGRATION-RULE CHECKS PASSED")
    sys.exit(0)
sys.exit(1)
