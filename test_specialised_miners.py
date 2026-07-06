"""
Phase 2 Step 13 — specialised cross-subject miners in modules/data_mining.py:
SIM-farming, document-fraud rings, remittance/hawala, movement/timeline.

Each miner reuses TWO existing single-sources-of-truth (no third is created):
  * the Phase-1 deterministic rule in pattern_rules.py defines the indicator
    (a subject is flagged only when its OWN typed evidence trips that rule, and
    the rule's triggers/sources become the citation);
  * the Step-12 cited-link detector forms the rings (a ring is a cluster of
    flagged subjects joined by a SHARED CITED value — supplier, operator,
    corridor counterparty, or crossing).

Covers, per miner: a positive case that flags two subjects AND links them into
a cited ring; a flagged-but-unlinked subject reported as standing alone (NOT a
ring); a below-threshold subject never flagged; ring links restricted to the
miner's link type(s); full citation carry-through. Plus cross-cutting checks:
identity attributes never flag or link; determinism; JSON-serialisability;
honest handling of cases without a typed ontology; the run-all aggregator; and
the verbatim MINING_NOTICE (association-not-culpability, human review, no
identity attributes) on every result.

No LLM, no network. Run: PYTHONUTF8=1 python test_specialised_miners.py
"""
import json
import sys
from types import SimpleNamespace as NS

from modules.data_mining import (
    MINING_NOTICE, mine_document_fraud_rings, mine_movement_patterns,
    mine_remittance_hawala, mine_sim_farming, render_specialised_result,
    run_all_specialised_miners,
)

results = []
def check(label, ok):
    results.append(bool(ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")


def onto(subject, phones=(), orgs=(), txns=(), locations=(), flags=(),
         timeline=(), legals=()):
    """Duck-typed case ontology matching the pattern-rule contract."""
    return {"subject": subject, "ontology": NS(
        subject_name=subject, flags=list(flags), graph=None,
        phones=[NS(number=n, type=t, country="", source=s) for n, t, s in phones],
        organizations=[NS(name=n, type="front", jurisdiction="", offshore=False,
                          source=s) for n, s in orgs],
        transactions=[NS(date="2024-01-01", direction="out", amount=a,
                         cross_border=True, counterparty=c, structured=False,
                         source=s) for a, c, s in txns],
        properties=[], comm_channels=[],
        legal_proceedings=[NS(agency="FRRO", status="active", date="2024-01-01",
                             case_ref="R", kind=k, source=s) for k, s in legals],
        deletion_events=[],
        locations=[NS(name=n, kind="observed", source=s) for n, s in locations],
        timeline_events=[NS(date="2024-03-15", significance="HIGH", source=s,
                           description=d) for d, s in timeline])}


def burners(prefix, source, n_lines=5, n_burn=2):
    return [(f"{prefix}{i:04d}", "burner" if i < n_burn else "domestic", source)
            for i in range(n_lines)]


print("=" * 72)
print("SIM-FARMING — flag via rule, ring via shared operator")
print("=" * 72)

sim = mine_sim_farming([
    onto("Farmer A", phones=burners("+91900010", "a_cdr.csv"),
         orgs=[("SimOps Handler LLP", "a_roc.pdf")]),
    onto("Farmer B", phones=burners("+91900020", "b_cdr.csv"),
         orgs=[("SIMOPS HANDLER LLP", "b_txn.csv")]),
    onto("Solo Farmer C", phones=burners("+91900030", "c_cdr.csv")),
    onto("Not A Farmer", phones=[("+91900040001", "domestic", "d.csv"),
                                 ("+91900040002", "domestic", "d.csv")])])
flagged = {f["subject"] for f in sim["subjects_flagged"]}
check("subjects whose own evidence trips the SIM rule are flagged",
      flagged == {"Farmer A", "Farmer B", "Solo Farmer C"})
check("below-threshold subject never flagged", "Not A Farmer" not in flagged)
check("each flag carries the rule's indicators and sources",
      all(f["indicators"] and f["sources"] for f in sim["subjects_flagged"]))
check("flagged subjects sharing an operator form ONE cited ring",
      sim["ring_count"] == 1
      and sim["rings"][0]["subjects"] == ["Farmer A", "Farmer B"])
check("flagged-but-unlinked subject reported standing alone, not a ring",
      "Solo Farmer C" in sim["unlinked_flagged_subjects"])
check("ring link cites both subjects to the shared operator",
      any(l["type"] == "shared_organization"
          and set(l["citations"]) == {"Farmer A", "Farmer B"}
          for l in sim["ring_links"]))

print("=" * 72)
print("DOCUMENT-FRAUD RING — flag via rule, ring via shared supplier")
print("=" * 72)

doc = mine_document_fraud_rings([
    onto("Forger A", flags=["Forged passport recovered during search",
                            "Counterfeit visa sticker identified"],
         orgs=[("Overseas Agent B", "a_seizure.pdf")]),
    onto("Forger B", flags=["Tampered work permit seized",
                            "Fake residence permit found in vehicle"],
         orgs=[("OVERSEAS AGENT B", "b_seizure.pdf")]),
    onto("Lone Forger", flags=["Forged passport recovered",
                               "Counterfeit visa found"]),
    onto("Clean Subject", flags=["Late tax filing noted"])])
dflagged = {f["subject"] for f in doc["subjects_flagged"]}
check("document-fraud rule flags subjects with 2+ travel-doc-fraud indicators",
      dflagged == {"Forger A", "Forger B", "Lone Forger"})
check("subject with no document-fraud indicator not flagged",
      "Clean Subject" not in dflagged)
check("shared forged-document supplier forms a cited ring",
      doc["ring_count"] == 1
      and doc["rings"][0]["subjects"] == ["Forger A", "Forger B"]
      and doc["rings"][0]["link_types"] == ["shared_organization"])
check("lone forger (no shared supplier) stands alone, not a ring",
      doc["unlinked_flagged_subjects"] == ["Lone Forger"])

# A shared PHONE between two doc-fraud subjects must NOT create a doc-fraud
# ring — the ring type is restricted to supplier links (org/counterparty).
doc_wrongtype = mine_document_fraud_rings([
    onto("Forger A", flags=["Forged passport recovered", "Counterfeit visa found"],
         phones=[("+91900099001", "domestic", "a.csv")]),
    onto("Forger B", flags=["Tampered work permit seized", "Fake visa found"],
         phones=[("+91900099001", "domestic", "b.csv")])])
check("shared phone does NOT form a document-fraud ring (link type restricted)",
      doc_wrongtype["ring_count"] == 0)

print("=" * 72)
print("REMITTANCE / HAWALA — flag via rule, ring via shared corridor counterparty")
print("=" * 72)

rem = mine_remittance_hawala([
    onto("Remitter A", txns=[(35000, "Corridor Agent", "a_bank.csv") for _ in range(6)]),
    onto("Remitter B", txns=[(30000, "CORRIDOR AGENT", "b_bank.csv") for _ in range(6)]),
    onto("Isolated Remitter", txns=[(40000, "Other Beneficiary", "c.csv")
                                    for _ in range(5)]),
    onto("Small Sender", txns=[(20000, "Someone", "d.csv") for _ in range(3)])])
rflagged = {f["subject"] for f in rem["subjects_flagged"]}
check("remittance rule flags subjects with 4+ outbound cross-border transfers",
      {"Remitter A", "Remitter B", "Isolated Remitter"} <= rflagged
      and "Small Sender" not in rflagged)
check("shared corridor counterparty forms a cited ring",
      rem["ring_count"] == 1
      and rem["rings"][0]["subjects"] == ["Remitter A", "Remitter B"]
      and rem["rings"][0]["link_types"] == ["shared_counterparty"])
check("remitter to a different beneficiary stands alone",
      "Isolated Remitter" in rem["unlinked_flagged_subjects"])

print("=" * 72)
print("MOVEMENT / TIMELINE — flag via rule, ring via shared crossing")
print("=" * 72)

mov = mine_movement_patterns([
    onto("Traveller A",
         locations=[("Petrapole land port", "a_move.csv"), ("Hili border checkpost", "a_move.csv")],
         timeline=[("Road transit staging near crossing", "a_move.csv")]),
    onto("Traveller B",
         locations=[("PETRAPOLE LAND PORT", "b_move.csv"), ("Moreh crossing", "b_move.csv")],
         timeline=[("Vehicle movement toward border", "b_move.csv")]),
    onto("Distant Traveller",
         locations=[("Raxaul crossing", "c.csv"), ("Jogbani border", "c.csv")],
         timeline=[("Transit recorded", "c.csv")]),
    onto("No Border Subject", locations=[("Central Delhi office", "e.csv")],
         timeline=[("Meeting held", "e.csv")])])
mflagged = {f["subject"] for f in mov["subjects_flagged"]}
check("movement rule flags subjects with 2+ border locations + movement",
      {"Traveller A", "Traveller B", "Distant Traveller"} <= mflagged
      and "No Border Subject" not in mflagged)
check("shared crossing forms a cited ring",
      mov["ring_count"] == 1
      and mov["rings"][0]["subjects"] == ["Traveller A", "Traveller B"]
      and mov["rings"][0]["link_types"] == ["shared_location"])
check("traveller through different crossings stands alone",
      "Distant Traveller" in mov["unlinked_flagged_subjects"])

print("=" * 72)
print("CROSS-CUTTING — identity-blindness, determinism, honesty, notice")
print("=" * 72)

ident = mine_document_fraud_rings([
    onto("Subject A", flags=["Bangladeshi national", "Muslim community member"],
         orgs=[("Shared Org", "a.pdf")]),
    onto("Subject B", flags=["Bangladeshi national", "Nepali origin"],
         orgs=[("Shared Org", "b.pdf")])])
check("identity attributes never flag a subject and never form a ring",
      ident["flagged_count"] == 0 and ident["ring_count"] == 0)

check("deterministic — identical output on identical input",
      mine_sim_farming([onto("Farmer A", phones=burners("+91900010", "a.csv"),
                             orgs=[("Op", "a.pdf")]),
                        onto("Farmer B", phones=burners("+91900020", "b.csv"),
                             orgs=[("Op", "b.pdf")])])
      == mine_sim_farming([onto("Farmer A", phones=burners("+91900010", "a.csv"),
                                orgs=[("Op", "a.pdf")]),
                           onto("Farmer B", phones=burners("+91900020", "b.csv"),
                                orgs=[("Op", "b.pdf")])]))
check("empty input → nothing flagged, notice still present",
      mine_sim_farming([])["flagged_count"] == 0
      and mine_sim_farming([])["mining_notice"] == MINING_NOTICE)
check("cases without a typed ontology skipped and counted",
      mine_sim_farming([{"subject": "No Onto"}, None, "junk"])["skipped_malformed"] == 3)

allm = run_all_specialised_miners([
    onto("Forger A", flags=["Forged passport recovered", "Counterfeit visa found"],
         orgs=[("Overseas Agent B", "a.pdf")]),
    onto("Forger B", flags=["Tampered work permit seized", "Fake visa found"],
         orgs=[("Overseas Agent B", "b.pdf")])])
check("run-all returns all four specialised miners",
      set(allm) == {"sim_farming", "document_fraud_ring", "remittance_hawala", "movement"})
check("every miner result carries the verbatim decision-support notice",
      all(r["mining_notice"] == MINING_NOTICE
          and r["human_review_required"] is True
          and r["suggestion_only"] is True
          for r in allm.values()))
check("every miner result is pure JSON-serialisable data",
      all(isinstance(json.loads(json.dumps(r)), dict) for r in allm.values()))

rendered = render_specialised_result(sim)
check("rendered result shows flags, the ring, per-subject citations, and notice",
      "SPECIALISED MINER — SIM_FARMING" in rendered
      and "FLAGGED: Farmer A" in rendered
      and "RING 1: Farmer A, Farmer B" in rendered
      and MINING_NOTICE in rendered)
check("rendered result explicitly says a lone flag is NOT a ring",
      "each flag stands alone" in render_specialised_result(
          mine_document_fraud_rings([
              onto("Lone Forger", flags=["Forged passport recovered",
                                         "Counterfeit visa found"])])))

print("=" * 72)
passed, total = sum(results), len(results)
print(f"SUMMARY: {passed}/{total} checks passed")
if passed == total:
    print("ALL SPECIALISED-MINER CHECKS PASSED"); sys.exit(0)
sys.exit(1)
