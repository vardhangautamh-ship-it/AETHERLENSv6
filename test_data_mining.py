"""
Phase 2 Step 12 — cross-subject cluster/network detection in
modules/data_mining.py.

Covers:
  * feature extraction from the REAL typed Ontology dataclasses (phones,
    organizations, transaction counterparties, locations — with sources),
    and JSON-serialisability of everything produced;
  * cited links only: every link carries, for EVERY subject on it, the raw
    value from that subject's evidence plus its source; a missing source is
    labelled honestly, never invented;
  * no fabricated edges: nothing links without an exact normalised-value
    match in >= 2 DISTINCT subjects; junk/short phones never link; generic
    values (cash, unknown, india) never link; values repeated within ONE
    subject never link; duplicate input entries for the same subject merge
    without self-linking; flags/identity text are not link inputs at all;
  * matching generality: phone format variance (+91-96130-70011 vs
    9613070011) and case/punctuation variance in names match, with both raw
    forms preserved in the citations;
  * clusters: transitive components (A-B phone + B-C org → one A,B,C
    cluster), disjoint pairs stay separate, unlinked subjects are reported
    as NOT implicated;
  * determinism, honest empty/malformed handling, and the verbatim
    MINING_NOTICE (decision support, human review, association-not-
    culpability) on every result.

No LLM, no network. Run: PYTHONUTF8=1 python test_data_mining.py
"""
import json
import sys
from types import SimpleNamespace as NS

from modules.ontology import (
    Location, Ontology, Organization, PhoneNumber, Transaction,
)
from modules.data_mining import (
    MINING_NOTICE, extract_case_features, mine_case_set, render_mining_result,
)

results = []
def check(label, ok):
    results.append(bool(ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")


def case(subject, phones=(), orgs=(), counterparties=(), locations=(), flags=()):
    """Duck-typed case ontology matching the typed contract. Org and
    counterparty items may be (name, source) or (name, source, hard_id) —
    the hard_id is the registration / counterparty account the cross-case
    matcher links on (name alone only ever raises an UNVERIFIED flag)."""
    def _org(t):
        n, s = t[0], t[1]
        reg = t[2] if len(t) > 2 else ""
        return NS(name=n, type="legitimate", jurisdiction="", offshore=False,
                  source=s, registration=reg)
    def _txn(t):
        c, s = t[0], t[1]
        acct = t[2] if len(t) > 2 else ""
        return NS(date="2024-01-01", direction="out", amount=10000,
                  cross_border=False, counterparty=c, structured=False,
                  source=s, counterparty_account=acct)
    return {"subject": subject, "ontology": NS(
        subject_name=subject, flags=list(flags),
        phones=[NS(number=n, type="domestic", country="", source=s) for n, s in phones],
        organizations=[_org(t) for t in orgs],
        transactions=[_txn(t) for t in counterparties],
        locations=[NS(name=n, kind="stated", source=s) for n, s in locations])}


print("=" * 72)
print("EXTRACTION — real typed Ontology in, serialisable cited features out")
print("=" * 72)

real = Ontology(
    subject_name="Real Subject",
    phones=[PhoneNumber(number="+91-96130-70011", source="cdr.csv")],
    organizations=[Organization(name="Meridian Overseas Pvt. Ltd", source="roc.pdf")],
    transactions=[Transaction(counterparty="Corridor Agent", source="bank.csv")],
    locations=[Location(name="Petrapole land port", source="movement.csv")])
feats = extract_case_features("Real Subject", real)
check("features extracted from the REAL typed dataclasses",
      feats["phones"][0]["raw"] == "+91-96130-70011"
      and feats["organizations"][0]["raw"] == "Meridian Overseas Pvt. Ltd"
      and feats["counterparties"][0]["raw"] == "Corridor Agent"
      and feats["locations"][0]["raw"] == "Petrapole land port")
check("every extracted feature carries its source file",
      all(item["source"] for k in ("phones", "organizations", "counterparties",
                                   "locations") for item in feats[k]))
check("features are JSON-serialisable", isinstance(json.loads(json.dumps(feats)), dict))

print("=" * 72)
print("LINKS — cited only, matched generally, never fabricated")
print("=" * 72)

two = mine_case_set([
    case("Subject A", phones=[("+91-96130-70011", "a_cdr.csv")]),
    case("Subject B", phones=[("9613070011", "b_cdr.csv")])])
check("shared phone links two subjects despite format variance",
      two["link_count"] == 1 and two["links"][0]["type"] == "shared_phone"
      and two["links"][0]["subjects"] == ["Subject A", "Subject B"])
check("phone link citations keep BOTH raw forms with their sources",
      two["links"][0]["citations"]["Subject A"][0] ==
      {"raw": "+91-96130-70011", "source": "a_cdr.csv"}
      and two["links"][0]["citations"]["Subject B"][0] ==
      {"raw": "9613070011", "source": "b_cdr.csv"})

# Cross-case org link now anchors on a shared REGISTRATION (hard identifier),
# not the name. Name still displayed case/punctuation-insensitively.
org_link = mine_case_set([
    case("Subject A", orgs=[("Meridian Overseas Pvt. Ltd", "a_roc.pdf", "LLP-AAB-1234")]),
    case("Subject B", orgs=[("MERIDIAN OVERSEAS PVT LTD", "b_txn.csv", "llp-aab-1234")])])
check("shared organization links on a shared registration (hard id)",
      org_link["link_count"] == 1
      and org_link["links"][0]["type"] == "shared_organization"
      and org_link["links"][0].get("hard_id"))

# Counterparty anchors on a shared account; location still links by value at
# this step (Weakness 3 removes sole-location links next).
cp_loc = mine_case_set([
    case("Subject A", counterparties=[("Hawala X", "a_bank.csv", "AC-5500")],
         locations=[("Hili border checkpost", "a_move.csv")]),
    case("Subject B", counterparties=[("hawala x", "b_bank.csv", "AC-5500")],
         locations=[("HILI BORDER CHECKPOST", "b_move.csv")])])
# Weakness 3: shared location is never a sole link. The counterparty account
# links the two subjects; the shared location then CORROBORATES that link.
check("shared counterparty (via account) links; shared location corroborates, not a 2nd link",
      cp_loc["link_count"] == 1
      and cp_loc["links"][0]["type"] == "shared_counterparty"
      and len(cp_loc["location_corroborations"]) == 1
      and not cp_loc["shared_location_context"])

check("every link cites EVERY subject on it",
      all(set(l["citations"]) == set(l["subjects"])
          and all(l["citations"][s] for s in l["subjects"])
          for r in (two, org_link, cp_loc) for l in r["links"]))

nosrc = mine_case_set([case("Subject A", counterparties=[("Hawala X", "", "AC-77")]),
                       case("Subject B", counterparties=[("Hawala X", "b.csv", "AC-77")])])
check("missing source labelled honestly, never invented",
      nosrc["link_count"] == 1
      and nosrc["links"][0]["citations"]["Subject A"][0]["source"]
      == "source not recorded in the analysed case")

none_shared = mine_case_set([
    case("Subject A", phones=[("+91-96130-70011", "a.csv")],
         orgs=[("Alpha Traders", "a.pdf")]),
    case("Subject B", phones=[("+91-98220-11122", "b.csv")],
         orgs=[("Bravo Exports", "b.pdf")])])
check("nothing shared → zero links (no fabricated edges)",
      none_shared["link_count"] == 0 and none_shared["cluster_count"] == 0)

junk = mine_case_set([case("Subject A", phones=[("12345", "a.csv")]),
                      case("Subject B", phones=[("12345", "b.csv")])])
check("junk short digit strings never link as phones", junk["link_count"] == 0)

generic = mine_case_set([
    case("Subject A", counterparties=[("CASH", "a.csv"), ("unknown", "a.csv")],
         locations=[("India", "a.csv")]),
    case("Subject B", counterparties=[("cash", "b.csv"), ("Unknown", "b.csv")],
         locations=[("INDIA", "b.csv")])])
check("generic values (cash/unknown/india) never constitute a link",
      generic["link_count"] == 0)

within_one = mine_case_set([
    case("Subject A", phones=[("+91-96130-70011", "a1.csv"),
                              ("+91-96130-70011", "a2.csv")]),
    case("Subject B", phones=[("+91-98220-11122", "b.csv")])])
check("a value repeated within ONE subject never links",
      within_one["link_count"] == 0)

dup = mine_case_set([case("Subject A", phones=[("+91-96130-70011", "a1.csv")]),
                     case("Subject A", phones=[("+91-96130-70011", "a2.csv")]),
                     case("Subject B", phones=[("+91-98220-11122", "b.csv")])])
check("duplicate entries for the same subject merge — no self-link",
      dup["subject_count"] == 2 and dup["link_count"] == 0)

ident = mine_case_set([
    case("Subject A", phones=[("+91-96130-70011", "a.csv")],
         flags=["Bangladeshi national", "Muslim community reference"]),
    case("Subject B", phones=[("+91-98220-11122", "b.csv")],
         flags=["Bangladeshi national", "Muslim community reference"])])
check("flags / identity text are NOT link inputs — no link from shared identity words",
      ident["link_count"] == 0)

print("=" * 72)
print("CLUSTERS — transitive components, disjoint pairs, unlinked honesty")
print("=" * 72)

chain = mine_case_set([
    case("Subject A", phones=[("+91-96130-70011", "a.csv")]),
    case("Subject B", phones=[("+91-96130-70011", "b.csv")],
         orgs=[("Meridian Overseas", "b.pdf", "CIN-MER-9")]),
    case("Subject C", orgs=[("Meridian Overseas", "c.pdf", "CIN-MER-9")]),
    case("Subject D", phones=[("+91-90000-00009", "d.csv")])])
check("A-B phone + B-C org (shared registration) → ONE transitive cluster of A,B,C",
      chain["cluster_count"] == 1
      and chain["clusters"][0]["subjects"] == ["Subject A", "Subject B", "Subject C"]
      and chain["clusters"][0]["link_types"]
      == ["shared_organization", "shared_phone"])
check("unlinked subject reported as NOT implicated, outside every cluster",
      chain["unlinked_subjects"] == ["Subject D"])

# Weakness 3: C and D share ONLY a location — that is NOT a link, so they do
# not form a cluster; only the phone-linked A–B pair is a cluster. The shared
# location surfaces as context, never an edge.
pairs = mine_case_set([
    case("Subject A", phones=[("+91-96130-70011", "a.csv")]),
    case("Subject B", phones=[("+91-96130-70011", "b.csv")]),
    case("Subject C", locations=[("Moreh crossing", "c.csv")]),
    case("Subject D", locations=[("Moreh crossing", "d.csv")])])
check("phone pair clusters; a location-only pair does NOT (location is not a link)",
      pairs["cluster_count"] == 1
      and pairs["clusters"][0]["subjects"] == ["Subject A", "Subject B"]
      and any(set(c["subjects"]) == {"Subject C", "Subject D"}
              for c in pairs["shared_location_context"])
      and set(pairs["unlinked_subjects"]) == {"Subject C", "Subject D"})

print("=" * 72)
print("RESULT-LEVEL — determinism, honesty, notice, rendering")
print("=" * 72)

check("deterministic — identical output on identical input",
      mine_case_set([case("Subject A", phones=[("+91-96130-70011", "a.csv")]),
                     case("Subject B", phones=[("+91-96130-70011", "b.csv")])])
      == mine_case_set([case("Subject A", phones=[("+91-96130-70011", "a.csv")]),
                        case("Subject B", phones=[("+91-96130-70011", "b.csv")])]))
check("empty input → empty result, notice still present",
      mine_case_set([])["link_count"] == 0
      and mine_case_set([])["mining_notice"] == MINING_NOTICE)
check("malformed entries skipped and counted — never guessed",
      mine_case_set([case("Subject A"), None, "junk", 42])["skipped_malformed"] == 3)
check("result is pure JSON-serialisable data",
      isinstance(json.loads(json.dumps(chain)), dict))
check("verbatim notice: decision support, human review, association-not-culpability",
      chain["mining_notice"] == MINING_NOTICE
      and chain["human_review_required"] is True
      and "association, not culpability" in MINING_NOTICE
      and "human review" in MINING_NOTICE
      and "absence of a link is not evidence of absence" in MINING_NOTICE
      and "nationality, ethnicity, or religion" in MINING_NOTICE)

txt = render_mining_result(chain)
check("rendered result shows clusters, per-subject citations, and the notice",
      "CLUSTER 1: Subject A, Subject B, Subject C" in txt
      and MINING_NOTICE in txt
      and 'Subject A: "+91-96130-70011" — a.csv' in txt
      and "UNLINKED SUBJECTS (no cited link — NOT implicated): Subject D" in txt)
check("features dict (without ontology) accepted as mining input",
      mine_case_set([feats,
                     {"subject": "Other", "phones":
                      [{"raw": "+91-96130-70011", "source": "o.csv"}]}])["link_count"] == 1)

print("=" * 72)
passed, total = sum(results), len(results)
print(f"SUMMARY: {passed}/{total} checks passed")
if passed == total:
    print("ALL DATA-MINING CHECKS PASSED"); sys.exit(0)
sys.exit(1)
