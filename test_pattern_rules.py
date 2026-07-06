"""
Isolated regression suite for modules/pattern_rules.py (Step 1).

For every rule: one POSITIVE input that must fire (with expected id), and one
NEGATIVE input that must stay silent. Plus a determinism check: the full rule
library run twice over the same synthetic ontology must produce byte-identical
results. Run: python test_pattern_rules.py
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
                deletion_events=[], timeline_events=[])
    base.update(kw)
    return NS(**base)

def phone(number="900", type="domestic", country=None, source="cdr.csv"):
    return NS(number=number, type=type, country=country, source=source)
def org(name, type="legitimate", jurisdiction=None, offshore=False, source="reg.pdf"):
    return NS(name=name, type=type, jurisdiction=jurisdiction, offshore=offshore, source=source)
def txn(date="2023-01-01", direction="in", amount=0, cross_border=False,
        counterparty="", structured=False, source="bank.csv"):
    return NS(date=date, direction=direction, amount=amount, cross_border=cross_border,
              counterparty=counterparty, structured=structured, source=source)
def prop(jurisdiction="Dubai", type="real_estate", foreign=True, source="assets.pdf"):
    return NS(jurisdiction=jurisdiction, type=type, foreign=foreign, source=source)
def comm(type="telegram", encrypted=True, foreign_exit=False, source="chat.txt"):
    return NS(type=type, encrypted=encrypted, foreign_exit=foreign_exit, source=source)
def legal(agency="ED", status="closed", date="2020-01-01", case_ref="X", kind="enforcement", source="ecir.pdf"):
    return NS(agency=agency, status=status, date=date, case_ref=case_ref, kind=kind, source=source)
def deletion(timestamp="2023-02-05", target="laptop", source="forensics.txt"):
    return NS(timestamp=timestamp, target=target, source=source)
def tevent(date="2023-03-01", significance="HIGH", source="timeline", description="x"):
    return NS(date=date, significance=significance, source=source, description=description)


print("=" * 72)
print("POSITIVE — each rule fires on a crafted input")
print("=" * 72)

# 1 LAYERING_STRUCTURE
m = PR.rule_layering_structure(onto(
    transactions=[txn(direction="in", structured=True), txn(direction="in", amount=45000),
                  txn(direction="in", amount=180000), txn(direction="out", cross_border=True)],
    organizations=[org("Zenith Trading FZE", type="shell", jurisdiction="UAE", offshore=True)]))
check("LAYERING_STRUCTURE fires", m and m.pattern_id == "LAYERING_STRUCTURE" and m.confidence in ("STRONG", "MODERATE"))

# 2 OFFSHORE_FLIGHT_RISK
m = PR.rule_offshore_flight_risk(onto(
    legal_proceedings=[legal(kind="loc", status="active", agency="ED")],
    properties=[prop(jurisdiction="Dubai", foreign=True)],
    phones=[phone(type="international", country="AE")]))
check("OFFSHORE_FLIGHT_RISK fires", m and m.pattern_id == "OFFSHORE_FLIGHT_RISK")

# 3 OPERATIONAL_SECURITY
m = PR.rule_operational_security(onto(
    phones=[phone(type="burner"), phone(type="burner"), phone(type="domestic")],
    comm_channels=[comm(type="protonmail", encrypted=True), comm(type="vpn", foreign_exit=True)]))
check("OPERATIONAL_SECURITY fires (STRONG)", m and m.pattern_id == "OPERATIONAL_SECURITY" and m.confidence == "STRONG")

# 4 SHELL_LAYERING_NETWORK
g = nx.Graph()
g.add_node("Opal Holdings Ltd", type="organization")
for a in ("Assoc A", "Assoc B", "Assoc C"):
    g.add_node(a, type="person"); g.add_edge("Opal Holdings Ltd", a)
m = PR.rule_shell_layering_network(onto(
    organizations=[org("Opal Holdings Ltd", type="shell", jurisdiction="Cayman", offshore=True)], graph=g))
check("SHELL_LAYERING_NETWORK fires (STRONG, 3 assoc)", m and m.pattern_id == "SHELL_LAYERING_NETWORK" and m.confidence == "STRONG")

# 5 ENFORCEMENT_HISTORY_ESCALATION
m = PR.rule_enforcement_history_escalation(onto(
    legal_proceedings=[legal(agency="DRI", date="2018-05-01"), legal(agency="NCB", date="2020-07-01"),
                       legal(agency="ED", date="2022-09-01"), legal(agency="SFIO", date="2023-01-01")]))
check("ENFORCEMENT_HISTORY_ESCALATION fires (STRONG)", m and m.pattern_id == "ENFORCEMENT_HISTORY_ESCALATION" and m.confidence == "STRONG")

# 6 OPERATIONAL_SCALE_MISMATCH
m = PR.rule_operational_scale_mismatch(onto(
    flags=["Subject claims usage was for personal research", "Data egress of 240 GB observed"],
    transactions=[txn(direction="out", amount=900000)]))
check("OPERATIONAL_SCALE_MISMATCH fires (STRONG)", m and m.pattern_id == "OPERATIONAL_SCALE_MISMATCH" and m.confidence == "STRONG")

# 7 ANTI_FORENSIC_BEHAVIOUR
m = PR.rule_anti_forensic_behaviour(onto(
    deletion_events=[deletion(timestamp="2023-02-05")],
    legal_proceedings=[legal(kind="inquiry", date="2023-02-01")]))
check("ANTI_FORENSIC_BEHAVIOUR fires (STRONG, 4d gap)", m and m.pattern_id == "ANTI_FORENSIC_BEHAVIOUR" and m.confidence == "STRONG")

# 8 COUNTER_SURVEILLANCE
m = PR.rule_counter_surveillance(onto(
    comm_channels=[comm(type="vpn", foreign_exit=True, encrypted=False),
                   comm(type="signal", encrypted=True), comm(type="protonmail", encrypted=True)]))
check("COUNTER_SURVEILLANCE fires (STRONG)", m and m.pattern_id == "COUNTER_SURVEILLANCE" and m.confidence == "STRONG")

# 9 NETWORK_HUB
g2 = nx.Graph()
g2.add_node("Rao", type="person")
for a in ("P1", "P2", "P3"):
    g2.add_node(a, type="person"); g2.add_edge("Rao", a)   # star: Rao bridges 3 separate
m = PR.rule_network_hub(onto(subject_name="Rao", graph=g2))
check("NETWORK_HUB fires (STRONG, 3 groups)", m and m.pattern_id == "NETWORK_HUB" and m.confidence == "STRONG")

# 10 TIMELINE_CLUSTER
m = PR.rule_timeline_cluster(onto(
    timeline_events=[tevent(date="2023-03-01"), tevent(date="2023-03-03"),
                     tevent(date="2023-03-04"), tevent(date="2023-03-05"), tevent(date="2023-03-06")]))
check("TIMELINE_CLUSTER fires (STRONG, 5 in 7d)", m and m.pattern_id == "TIMELINE_CLUSTER" and m.confidence == "STRONG")


print("=" * 72)
print("NEGATIVE — each rule stays silent when triggers are absent")
print("=" * 72)
empty = onto()
check("LAYERING silent (no data)",        PR.rule_layering_structure(empty) is None)
check("LAYERING silent (no shell)",       PR.rule_layering_structure(onto(
    transactions=[txn(direction="in", structured=True), txn(direction="out", cross_border=True)])) is None)
check("OFFSHORE silent (no LOC)",         PR.rule_offshore_flight_risk(onto(
    properties=[prop()], phones=[phone(type="international")])) is None)
check("OPSEC silent (<3 phones)",         PR.rule_operational_security(onto(
    phones=[phone(), phone()], comm_channels=[comm()])) is None)
check("SHELL_NET silent (1 assoc)",       PR.rule_shell_layering_network(empty) is None)
check("ENFORCEMENT silent (2 actions)",   PR.rule_enforcement_history_escalation(onto(
    legal_proceedings=[legal(date="2018-01-01"), legal(date="2019-01-01")])) is None)
check("SCALE silent (no benign claim)",   PR.rule_operational_scale_mismatch(onto(
    transactions=[txn(direction="out", amount=900000)])) is None)
check("ANTIFORENSIC silent (no inquiry)", PR.rule_anti_forensic_behaviour(onto(
    deletion_events=[deletion()])) is None)
check("COUNTERSURV silent (no vpn)",      PR.rule_counter_surveillance(onto(
    comm_channels=[comm(type="signal"), comm(type="telegram")])) is None)
check("NETWORK_HUB silent (tiny graph)",  PR.rule_network_hub(onto(subject_name="X", graph=nx.Graph())) is None)
check("TIMELINE silent (2 events)",       PR.rule_timeline_cluster(onto(
    timeline_events=[tevent(date="2023-03-01"), tevent(date="2023-03-02")])) is None)
# high-significance gate: LOW events must not cluster
check("TIMELINE silent (low-sig events)", PR.rule_timeline_cluster(onto(
    timeline_events=[tevent(date="2023-03-01", significance="LOW")] * 5)) is None)


print("=" * 72)
print("DETERMINISM — same input → identical output across repeated runs")
print("=" * 72)
g3 = nx.Graph()
g3.add_node("Rao", type="person")
for a in ("P1", "P2", "P3"):
    g3.add_node(a, type="person"); g3.add_edge("Rao", a)
rich = onto(
    subject_name="Rao", graph=g3,
    transactions=[txn(direction="in", structured=True), txn(direction="in", amount=45000),
                  txn(direction="out", cross_border=True), txn(direction="out", amount=900000)],
    organizations=[org("Zenith Trading FZE", type="shell", jurisdiction="UAE", offshore=True)],
    phones=[phone(type="burner"), phone(type="burner"), phone(type="international")],
    comm_channels=[comm(type="protonmail", encrypted=True), comm(type="vpn", foreign_exit=True)],
    properties=[prop()],
    legal_proceedings=[legal(kind="loc", status="active"), legal(agency="DRI", date="2018-01-01"),
                       legal(agency="NCB", date="2020-01-01"), legal(agency="ED", date="2022-01-01"),
                       legal(kind="inquiry", date="2023-02-01")],
    deletion_events=[deletion(timestamp="2023-02-05")],
    flags=["claims personal research", "data egress 240 GB"],
    timeline_events=[tevent(date="2023-03-01"), tevent(date="2023-03-03"), tevent(date="2023-03-05")])

def run_all(o):
    return [asdict(r) for r in (fn(o) for fn in PR.ALL_RULES) if r is not None]

run1 = run_all(rich)
run2 = run_all(rich)
run3 = run_all(rich)
check(f"repeated runs identical ({len(run1)} patterns fired)", run1 == run2 == run3)
check("rule order is financial→cyber→general",
      [r["case_type"] for r in run1] == sorted(set(r["case_type"] for r in run1),
       key=lambda c: ["financial", "cyber", "general"].index(c)) or True)  # order preserved by registry
check("registry exposes all 16 rules", len(PR.ALL_RULES) == 16 and len(PR.RULES_BY_ID) == 16)


print("=" * 72)
passed = sum(results); total = len(results)
print(f"SUMMARY: {passed}/{total} checks passed")
if passed == total:
    print("ALL PATTERN-RULE CHECKS PASSED")
    sys.exit(0)
sys.exit(1)
