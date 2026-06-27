"""
Step 2 verification — modules/ontology.build_ontology().

Confirms the builder maps realistic resolver-shaped dict input into a populated,
typed Ontology, derives comm/legal/deletion entities from §09 flag text when not
explicitly typed, and that the Step 1 pattern rules run cleanly over the result.
Run: python test_ontology_build.py
"""
import sys
import networkx as nx

from modules.ontology import (
    build_ontology, Ontology, Person, PhoneNumber, Organization,
    Transaction, Property, CommChannel, LegalProceeding, DeletionEvent, TimelineEvent,
)
import modules.pattern_rules as PR

results = []
def check(label, ok):
    results.append(bool(ok)); print(f"  [{'PASS' if ok else 'FAIL'}] {label}")


# ── synthetic resolver output (shapes the real pipeline produces) ─────────────
g = nx.Graph()
g.add_node("Rohan Verma", type="person")
for a in ("Assoc One", "Assoc Two", "Zenith Trading FZE"):
    g.add_node(a, type="person" if a.startswith("Assoc") else "organization")
    g.add_edge("Rohan Verma", a)

person = {
    "confirmed_name": "Rohan Verma",
    "role": "director",
    "anomaly_flags": [
        "Active lookout circular (LOC) issued 2023-02-01",
        "ED enforcement case registered 2018-06-01",
        "DRI proceedings noted 2020-08-01",
        "NCB action 2022-03-01",
        "CERT-In inquiry notice served 2023-02-01",
        "Subject uses ProtonMail and Telegram for communications",
        "Foreign VPN exit node detected from Switzerland",
        "Device data wiped 2023-02-05 — anti-forensic deletion",
        "Subject claims activity was personal research",
        "Data egress of 240 GB observed",
    ],
    "phones_found": [
        {"number": "+919820144109", "type": "burner"},
        {"number": "+919820144110", "tags": ["burner"]},
        {"number": "+14155552671"},
    ],
}
entities = {
    "organizations": [
        {"name": "Zenith Trading FZE", "type": "shell", "jurisdiction": "UAE"},
    ],
    "properties": [
        {"jurisdiction": "Dubai", "type": "apartment"},
    ],
}
financial_data = {
    "transactions": [
        {"date": "2023-01-02", "direction": "credit", "amount": 45000, "structured": True},
        {"date": "2023-01-05", "direction": "credit", "amount": 180000},
        {"date": "2023-01-09", "direction": "debit", "amount": 900000, "cross_border": True},
    ],
}
timeline = {"events": [
    {"date": "2023-03-01", "significance": "HIGH"},
    {"date": "2023-03-03", "significance": "HIGH"},
    {"date": "2023-03-05", "significance": "HIGH"},
]}

onto = build_ontology(person, entities, person["anomaly_flags"], timeline, g,
                      person["phones_found"], financial_data)

print("=" * 72)
print("BUILDER — typed ontology is populated from resolver output")
print("=" * 72)
print("  counts:", onto.counts())
check("returns an Ontology", isinstance(onto, Ontology))
check("subject_name resolved", onto.subject_name == "Rohan Verma")
check("subject Person flagged is_subject", onto.subject and onto.subject.is_subject)
check("3 phones typed", len(onto.phones) == 3)
check("2 burners classified", sum(1 for p in onto.phones if p.type == "burner") == 2)
check("intl phone classified", any(p.type == "international" for p in onto.phones))
check("shell org offshore-derived", any(o.type == "shell" and o.offshore for o in onto.organizations))
check("foreign property derived", any(p.foreign for p in onto.properties))
check("3 transactions mapped (credit→in/debit→out)",
      len(onto.transactions) == 3 and any(t.direction == "out" and t.cross_border for t in onto.transactions))
check("comm channels derived from flags (proton+telegram+vpn)",
      {c.type for c in onto.comm_channels} >= {"protonmail", "telegram", "vpn"})
check("foreign VPN exit derived", any(c.type == "vpn" and c.foreign_exit for c in onto.comm_channels))
check("legal proceedings derived (LOC + 3 enforcement)",
      any(l.kind == "loc" for l in onto.legal_proceedings)
      and sum(1 for l in onto.legal_proceedings if l.kind == "enforcement") >= 3)
check("deletion event derived from flag", len(onto.deletion_events) >= 1)
check("timeline events mapped", len(onto.timeline_events) == 3)

print("=" * 72)
print("INTEGRATION — Step 1 rules run over the built ontology")
print("=" * 72)
fired = [r for r in (fn(onto) for fn in PR.ALL_RULES) if r is not None]
ids = {r.pattern_id for r in fired}
print("  fired:", sorted(ids))
for expect in ("LAYERING_STRUCTURE", "OFFSHORE_FLIGHT_RISK", "OPERATIONAL_SECURITY",
               "ENFORCEMENT_HISTORY_ESCALATION", "OPERATIONAL_SCALE_MISMATCH",
               "ANTI_FORENSIC_BEHAVIOUR", "COUNTER_SURVEILLANCE", "NETWORK_HUB", "TIMELINE_CLUSTER"):
    check(f"{expect} fires over built ontology", expect in ids)

print("=" * 72)
print("EMPTY — builder is defensive on minimal input")
print("=" * 72)
empty = build_ontology({})
check("empty person → Ontology with subject placeholder, no crash",
      isinstance(empty, Ontology) and len(empty.persons) == 1)
check("no rules fire on empty ontology",
      all(fn(empty) is None for fn in PR.ALL_RULES))

print("=" * 72)
passed, total = sum(results), len(results)
print(f"SUMMARY: {passed}/{total} checks passed")
if passed == total:
    print("ALL ONTOLOGY-BUILD CHECKS PASSED"); sys.exit(0)
sys.exit(1)
