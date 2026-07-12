"""
Phase 4 Step 14 — network-dismantling suggestions in modules/advanced_intel.py.

The "detected network" is the cited cross-subject links from the REAL Phase 2
miner (data_mining.mine_case_set) — the graph is built only from those cited
links, never fabricated. Covers:

  * graph grounded in the actual cited links: nodes/edges match the miner's
    output, and every edge carries the per-subject citations that establish it;
  * structural centrality on the real NetworkX graph — a cut vertex (whose
    removal fragments its cluster) is surfaced as an articulation point; the
    bridge that solely joins two parts is surfaced as a structurally central
    link; a redundant triangle correctly reports NO bridges / NO cut vertex;
  * decision-support framing on EVERY output — "for officer consideration,"
    autonomous=False, determination_of_guilt=False, human_review_required=True,
    and the verbatim DISMANTLING_NOTICE (not a directive, not guilt, no
    identity attributes);
  * grounded-or-silent: an empty / edge-less network yields no suggestions
    (nothing invented); identity attributes never enter the graph or ranking;
  * determinism and JSON-serialisability.

No LLM, no network. Run: PYTHONUTF8=1 python test_advanced_intel.py
"""
import json
import sys
from types import SimpleNamespace as NS

from modules.data_mining import mine_case_set
from modules.advanced_intel import (
    DISMANTLING_NOTICE, build_network_graph, render_dismantling_suggestions,
    suggest_network_dismantling,
)

results = []
def check(label, ok):
    results.append(bool(ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")


def case(subject, phones=(), orgs=(), counterparties=(), locations=()):
    """Org/counterparty items may carry a third element — the hard identifier
    (registration / account) the cross-case matcher links entities on."""
    def _org(t):
        return NS(name=t[0], type="front", jurisdiction="", offshore=False,
                  source=t[1], registration=(t[2] if len(t) > 2 else ""))
    def _txn(t):
        return NS(date="2024-01-01", direction="out", amount=1, cross_border=False,
                  counterparty=t[0], structured=False, source=t[1],
                  counterparty_account=(t[2] if len(t) > 2 else ""))
    return {"subject": subject, "ontology": NS(
        subject_name=subject,
        phones=[NS(number=n, type="domestic", country="", source=s) for n, s in phones],
        organizations=[_org(t) for t in orgs],
        transactions=[_txn(t) for t in counterparties],
        locations=[NS(name=n, kind="stated", source=s) for n, s in locations])}


print("=" * 72)
print("GRAPH — built only from the REAL miner's cited links")
print("=" * 72)

# Star topology: HUB links to A (phone), B (org), C (counterparty). The leaves
# share nothing with each other → HUB is a cut vertex; every spoke is a bridge.
# Spokes: Hub–A via a shared phone, Hub–B via a shared org registration,
# Hub–C via a shared counterparty account (all hard identifiers).
star_mined = mine_case_set([
    case("Hub", phones=[("+91900000001", "hub.csv")],
         orgs=[("Nexus Traders", "hub.pdf", "CIN-NEX-1")],
         counterparties=[("Beneficiary Z", "hub_bank.csv", "AC-BENZ-1")]),
    case("Leaf A", phones=[("+91900000001", "a.csv")]),
    case("Leaf B", orgs=[("Nexus Traders", "b.pdf", "CIN-NEX-1")]),
    case("Leaf C", counterparties=[("Beneficiary Z", "c_bank.csv", "AC-BENZ-1")])])
g, edge_ev = build_network_graph(star_mined)
check("graph nodes/edges match the miner's links (nothing fabricated)",
      set(g.nodes()) == {"Hub", "Leaf A", "Leaf B", "Leaf C"}
      and g.number_of_edges() == 3)
check("every graph edge carries the per-subject citations that establish it",
      all(edge_ev[e][0]["citations"] for e in edge_ev)
      and edge_ev[("Hub", "Leaf A")][0]["citations"]["Leaf A"][0]["source"] == "a.csv")

print("=" * 72)
print("STRUCTURE — cut vertex + bridges surfaced from the actual graph")
print("=" * 72)

star = suggest_network_dismantling(star_mined)
top = star["central_nodes"][0]
check("the hub is surfaced as the top structurally central node",
      top["subject"] == "Hub" and top["degree"] == 3)
check("the hub is identified as a cut vertex (removal fragments the network)",
      top["is_articulation_point"] is True and top["is_structurally_central"] is True
      and "cut vertex" in top["rationale"])
check("the hub's centrality is cited to the real links that create it",
      len(top["citations"]) == 3
      and {c["with"] for c in top["citations"]} == {"Leaf A", "Leaf B", "Leaf C"})
check("all three spokes surfaced as structurally central (bridge) links",
      len(star["central_links"]) == 3
      and all(l["is_bridge"] for l in star["central_links"])
      and star["has_structural_bridges"] is True)
check("leaves are NOT flagged as structurally central",
      all(not n["is_structurally_central"]
          for n in star["central_nodes"] if n["subject"] != "Hub"))

# Redundant triangle: A-B, B-C, A-C all linked → no cut vertex, no bridge.
# Triangle edges: A–B via shared phone, A–C via shared org registration,
# B–C via shared counterparty account (all hard identifiers).
tri = suggest_network_dismantling(mine_case_set([
    case("Subject A", phones=[("+91900000011", "a.csv")], orgs=[("Ring Org", "a.pdf", "CIN-RING-1")]),
    case("Subject B", phones=[("+91900000011", "b.csv")],
         counterparties=[("Ring Beneficiary", "b.csv", "AC-RING-9")]),
    case("Subject C", orgs=[("Ring Org", "c.pdf", "CIN-RING-1")],
         counterparties=[("Ring Beneficiary", "c.csv", "AC-RING-9")])]))
check("redundant triangle reports NO bridges and NO cut vertex (honest structure)",
      tri["central_links"] == [] and tri["has_structural_bridges"] is False
      and tri["redundant_connectivity"] is True
      and all(not n["is_articulation_point"] for n in tri["central_nodes"]))
check("triangle still ranks central nodes by degree (all degree 2)",
      len(tri["central_nodes"]) == 3
      and all(n["degree"] == 2 for n in tri["central_nodes"]))

print("=" * 72)
print("FRAMING — human-review-only, not autonomous, not guilt")
print("=" * 72)

check("result declares itself non-autonomous, non-guilt, human-reviewed",
      star["autonomous"] is False
      and star["determination_of_guilt"] is False
      and star["human_review_required"] is True)
check("verbatim notice present and states the constraints",
      star["dismantling_notice"] == DISMANTLING_NOTICE
      and "NOT an instruction" in DISMANTLING_NOTICE
      and "NOT a determination of guilt" in DISMANTLING_NOTICE
      and "human officer must weigh" in DISMANTLING_NOTICE
      and "nationality, ethnicity, or religion" in DISMANTLING_NOTICE)
check("every node suggestion is framed 'for officer consideration', not a directive",
      all("FOR OFFICER CONSIDERATION" in n["suggestion"]
          and "not a directive" in n["suggestion"] for n in star["central_nodes"]))
check("every bridge suggestion is framed for consideration, not a directive",
      all("FOR OFFICER CONSIDERATION" in l["suggestion"]
          and "not a directive" in l["suggestion"] for l in star["central_links"]))

print("=" * 72)
print("GROUNDED-OR-SILENT / IDENTITY-BLINDNESS / DETERMINISM")
print("=" * 72)

empty = suggest_network_dismantling(mine_case_set([
    case("Solo A", phones=[("+91900000021", "a.csv")]),
    case("Solo B", phones=[("+91900000022", "b.csv")])]))
check("network with no cited links → no suggestions (nothing invented)",
      empty["central_nodes"] == [] and empty["central_links"] == []
      and empty["network"]["edge_count"] == 0
      and empty["dismantling_notice"] == DISMANTLING_NOTICE)
check("empty/malformed input → empty graph, no crash",
      suggest_network_dismantling(None)["network"]["node_count"] == 0
      and suggest_network_dismantling({})["central_nodes"] == []
      and build_network_graph("junk")[0].number_of_nodes() == 0)

# Identity attributes live outside the cited links; the graph is built only
# from links, so they can never create an edge or change centrality.
id_a = suggest_network_dismantling(mine_case_set([
    case("Hub", phones=[("+91900000001", "h.csv")], orgs=[("Nexus", "h.pdf", "CIN-NX-2")]),
    case("Leaf A", phones=[("+91900000001", "a.csv")]),
    case("Leaf B", orgs=[("Nexus", "b.pdf", "CIN-NX-2")])]))
id_b_cases = [
    case("Hub", phones=[("+91900000001", "h.csv")], orgs=[("Nexus", "h.pdf", "CIN-NX-2")]),
    case("Leaf A", phones=[("+91900000001", "a.csv")]),
    case("Leaf B", orgs=[("Nexus", "b.pdf", "CIN-NX-2")])]
id_b_cases[0]["ontology"].flags = ["Bangladeshi national", "Muslim"]
id_b = suggest_network_dismantling(mine_case_set(id_b_cases))
check("identity attributes never enter the graph or change centrality",
      [n["subject"] for n in id_a["central_nodes"]]
      == [n["subject"] for n in id_b["central_nodes"]]
      and id_a["central_nodes"][0]["degree"] == id_b["central_nodes"][0]["degree"])

check("deterministic — identical output on identical input",
      suggest_network_dismantling(star_mined) == suggest_network_dismantling(star_mined))
check("result is pure JSON-serialisable data",
      isinstance(json.loads(json.dumps(star)), dict))

# Also accepts a specialised-miner result (uses its 'ring_links').
from modules.data_mining import mine_document_fraud_rings
ring = mine_document_fraud_rings([
    case2 := {"subject": "Forger A", "ontology": NS(
        subject_name="Forger A", flags=["Forged passport recovered", "Counterfeit visa found"],
        phones=[], transactions=[], locations=[],
        organizations=[NS(name="Supplier X", type="front", jurisdiction="", offshore=False,
                          source="a.pdf", registration="CIN-SUPX-1")],
        timeline_events=[], legal_proceedings=[])},
    {"subject": "Forger B", "ontology": NS(
        subject_name="Forger B", flags=["Tampered work permit seized", "Fake visa found"],
        phones=[], transactions=[], locations=[],
        organizations=[NS(name="Supplier X", type="front", jurisdiction="", offshore=False,
                          source="b.pdf", registration="CIN-SUPX-1")],
        timeline_events=[], legal_proceedings=[])}])
ring_sugg = suggest_network_dismantling(ring)
check("accepts a specialised-miner result and grounds on its ring_links",
      ring_sugg["network"]["edge_count"] == 1
      and {n["subject"] for n in ring_sugg["central_nodes"]} == {"Forger A", "Forger B"})

rendered = render_dismantling_suggestions(star)
check("rendered output carries the notice, the hub, and cited links",
      DISMANTLING_NOTICE in rendered and "Hub ***" in rendered
      and rendered.startswith("NETWORK-DISMANTLING SUGGESTIONS")
      and "STRUCTURALLY CENTRAL SUBJECTS" in rendered)
check("rendered empty network says there is nothing to suggest",
      "No cited links between subjects" in render_dismantling_suggestions(empty))

print("=" * 72)
passed, total = sum(results), len(results)
print(f"SUMMARY: {passed}/{total} checks passed")
if passed == total:
    print("ALL ADVANCED-INTEL CHECKS PASSED"); sys.exit(0)
sys.exit(1)
