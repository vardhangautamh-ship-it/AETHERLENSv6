"""
Cross-case matching hardening — one section per fixed weakness.

Governing rule (HYDRA/CHIMERA doctrine at cross-case scale): cross-case
links and identity merges anchor on HARD SHARED IDENTIFIERS only. Name,
org-name, city, and landline overlaps may FLAG for human review but never
auto-assert a link or fuse two entities. When uncertain — flag, never assert.

Run: PYTHONUTF8=1 python test_cross_case_matching.py
"""
from types import SimpleNamespace as NS

from modules.data_mining import (mine_case_set, render_mining_result,
                                 assign_cross_case_identities)

results = []
def check(label, ok):
    results.append(bool(ok)); print(f"  [{'PASS' if ok else 'FAIL'}] {label}")


def case(subject, phones=(), orgs=(), counterparties=(), locations=()):
    """Duck-typed case ontology. Org items may be (name, source) or
    (name, source, registration); counterparty items (name, source) or
    (name, source, account) — the third element is the hard identifier the
    cross-case matcher links entities on."""
    def _org(t):
        return NS(name=t[0], type="", jurisdiction="", offshore=False,
                  source=t[1], registration=(t[2] if len(t) > 2 else ""))
    def _txn(t):
        return NS(date="2024-01-01", direction="out", amount=10000,
                  cross_border=False, counterparty=t[0], structured=False,
                  source=t[1], counterparty_account=(t[2] if len(t) > 2 else ""))
    return {"subject": subject, "ontology": NS(
        subject_name=subject, flags=[],
        phones=[NS(number=n, type="", country="", source=s, owner="")
                for n, s in phones],
        organizations=[_org(t) for t in orgs],
        transactions=[_txn(t) for t in counterparties],
        locations=[NS(name=n, kind="stated", source=s) for n, s in locations])}


print("=" * 72)
print("WEAKNESS 1 — same-name subjects across cases never fuse on the name")
print("=" * 72)

# TRAP: two DIFFERENT people named "Rahul Sharma" — different mobiles,
# different accounts' worth of evidence. Must NOT fuse.
trap = mine_case_set([
    case("Rahul Sharma", phones=[("+91-98110-11111", "caseA_cdr.csv")],
         orgs=[("Alpha Traders", "caseA_roc.csv")]),
    case("Rahul Sharma", phones=[("+91-99220-22222", "caseB_cdr.csv")],
         orgs=[("Beta Exports", "caseB_roc.csv")]),
])
check("two same-named people with different mobiles stay SEPARATE",
      trap["subject_count"] == 2)
check("both entries are case-qualified (nobody silently owns the bare name)",
      sorted(s for s in ([trap["unlinked_subjects"]] and trap["unlinked_subjects"]))
      == ["Rahul Sharma (case 1)", "Rahul Sharma (case 2)"])
check("no cross-case link fabricated between them", trap["link_count"] == 0)
check("no same-person merge asserted", not trap["same_person_merges"])
flags = trap["unverified_same_name"]
check("'possible same person — UNVERIFIED' flag raised for human review",
      len(flags) == 1 and flags[0]["name"] == "Rahul Sharma"
      and "human review" in flags[0]["note"]
      and len(flags[0]["parties"]) == 2)
rendered = render_mining_result(trap)
check("flag renders as UNVERIFIED, not as a link/cluster",
      "POSSIBLE SAME PERSON — UNVERIFIED" in rendered
      and "CLUSTER" not in rendered.replace("CLUSTER / NETWORK", ""))

# GENUINE: the same person across two cases, anchored on a shared personal
# mobile. May merge — flagged and cited to BOTH cases.
genuine = mine_case_set([
    case("Aman Verma", phones=[("+91-97000-33333", "caseC_cdr.csv")],
         orgs=[("Gamma Logistics", "caseC_roc.csv")]),
    case("Aman Verma", phones=[("97000 33333", "caseD_bank.csv")],
         counterparties=[("Delta Ventures", "caseD_bank.csv")]),
])
check("same person sharing a personal mobile resolves to ONE subject",
      genuine["subject_count"] == 1)
m = genuine["same_person_merges"]
check("merge is audited and anchored on the mobile, never the name",
      len(m) == 1 and "personal mobile" in m[0]["anchor"]
      and "never on the name" in m[0]["note"])
check("merge cites BOTH cases' raw forms and sources",
      "caseC_cdr.csv" in str(m[0]["citations"]) and "caseD_bank.csv" in str(m[0]["citations"]))

# LANDLINE GUARD (single source of truth with the single-case fix): a shared
# OFFICE LANDLINE never fuses two same-named people.
landline = mine_case_set([
    case("Rahul Sharma", phones=[("+91-22-24450010", "caseE_cdr.csv")]),
    case("Rahul Sharma", phones=[("022-24450010", "caseF_cdr.csv")]),
])
check("a shared landline never merges two same-named subjects",
      landline["subject_count"] == 2 and not landline["same_person_merges"]
      and len(landline["unverified_same_name"]) == 1)

# determinism + resolver unit shape
r1 = assign_cross_case_identities([case("X Y", phones=[("+91-98110-11111", "a")]),
                                   case("X Y", phones=[("+91-98110-11111", "b")])])
check("resolver merges duplicate same-person input (regression: no self-link)",
      r1[0][0][0] == r1[0][1][0] and not r1[2])
check("deterministic across runs",
      mine_case_set([case("Rahul Sharma", phones=[("+91-98110-11111", "a.csv")]),
                     case("Rahul Sharma", phones=[("+91-99220-22222", "b.csv")])])
      == mine_case_set([case("Rahul Sharma", phones=[("+91-98110-11111", "a.csv")]),
                        case("Rahul Sharma", phones=[("+91-99220-22222", "b.csv")])]))


print("=" * 72)
print("WEAKNESS 2 — org/counterparty link on a hard identifier, name only flags")
print("=" * 72)

# TRAP: two same-named orgs across cases with NO shared hard identifier.
# Must NOT link — flag only.
orgtrap = mine_case_set([
    case("Subject P", orgs=[("Global Traders", "caseP_roc.pdf")]),
    case("Subject Q", orgs=[("GLOBAL TRADERS", "caseQ_txn.csv")]),
])
check("same org NAME, no shared registration → NOT linked",
      orgtrap["link_count"] == 0 and orgtrap["cluster_count"] == 0)
ef = orgtrap["unverified_same_entity"]
check("bare org-name overlap raises a 'possible same entity — UNVERIFIED' flag",
      len(ef) == 1 and ef[0]["entity_kind"] == "organization"
      and set(ef[0]["subjects"]) == {"Subject P", "Subject Q"}
      and "NOT linked" in ef[0]["note"])
check("subjects P and Q are NOT clustered on the name",
      set(orgtrap["unlinked_subjects"]) == {"Subject P", "Subject Q"})

# GENUINE: two orgs across cases sharing a real registration → linked, cited.
orggen = mine_case_set([
    case("Subject R", orgs=[("Meridian Consulting LLP", "caseR_roc.pdf", "LLP-AAB-9931")]),
    case("Subject S", orgs=[("Meridian Consulting", "caseS_txn.csv", "llp-aab-9931")]),
])
check("shared registration links the two subjects (hard-id anchored)",
      orggen["link_count"] == 1
      and orggen["links"][0]["type"] == "shared_organization"
      and orggen["links"][0].get("hard_id"))
check("hard-id org link cites BOTH cases",
      set(orggen["links"][0]["citations"]) == {"Subject R", "Subject S"}
      and "caseR_roc.pdf" in str(orggen["links"][0]["citations"])
      and "caseS_txn.csv" in str(orggen["links"][0]["citations"]))
check("genuine hard-id link raises NO redundant unverified flag",
      not orggen["unverified_same_entity"])

# Counterparty: bare name flags, shared account links.
cptrap = mine_case_set([
    case("Subject T", counterparties=[("Cash Point", "t.csv")]),
    case("Subject U", counterparties=[("CASH POINT", "u.csv")]),
])
cpgen = mine_case_set([
    case("Subject V", counterparties=[("Hawala Node", "v.csv", "AC-778899")]),
    case("Subject W", counterparties=[("hawala node", "w.csv", "AC-778899")]),
])
check("same counterparty NAME, no account → flag, not link",
      cptrap["link_count"] == 0 and len(cptrap["unverified_same_entity"]) == 1)
check("shared counterparty ACCOUNT → linked, cited",
      cpgen["link_count"] == 1
      and cpgen["links"][0]["type"] == "shared_counterparty")

# Differing registrations on the same name → different entities, no link.
orgdiff = mine_case_set([
    case("Subject X", orgs=[("Apex Holdings", "x.csv", "CIN-111")]),
    case("Subject Y", orgs=[("Apex Holdings", "y.csv", "CIN-222")]),
])
check("same name but DIFFERENT registrations → not linked (distinct entities)",
      orgdiff["link_count"] == 0)


print("=" * 72)
print("WEAKNESS 3 — a shared city/location is never a sole cross-case link")
print("=" * 72)

# TRAP: three subjects across three cases all in "Mumbai", nothing else shared.
# Must NOT link.
citytrap = mine_case_set([
    case("Subject M1", locations=[("Mumbai", "m1.csv")]),
    case("Subject M2", locations=[("Mumbai", "m2.csv")]),
    case("Subject M3", locations=[("Mumbai", "m3.csv")]),
])
check("three subjects sharing only a city → NO links",
      citytrap["link_count"] == 0 and citytrap["cluster_count"] == 0)
check("all three remain unlinked (a shared city is not a connection)",
      set(citytrap["unlinked_subjects"]) == {"Subject M1", "Subject M2", "Subject M3"})
check("shared city surfaces as CONTEXT only, explicitly not a link",
      any(set(c["subjects"]) == {"Subject M1", "Subject M2", "Subject M3"}
          and "not a cross-case link" in c["note"]
          for c in citytrap["shared_location_context"]))

# Even a highly specific shared premises is not a sole link.
premises = mine_case_set([
    case("Subject N1", locations=[("Flat 4B Nirmal Tower Andheri", "n1.csv")]),
    case("Subject N2", locations=[("flat 4b nirmal tower andheri", "n2.csv")]),
])
check("a specific shared premises alone is still NOT a link (context only)",
      premises["link_count"] == 0 and len(premises["shared_location_context"]) == 1)

# GENUINE: a hard-identifier link that also shares a location → still links on
# the identifier; the location corroborates, never replaces it.
loc_corrob = mine_case_set([
    case("Subject K1", phones=[("+91-98765-43210", "k1.csv")],
         locations=[("Kolkata", "k1.csv")]),
    case("Subject K2", phones=[("+91-98765-43210", "k2.csv")],
         locations=[("KOLKATA", "k2.csv")]),
])
check("hard-id link still links when a location is also shared",
      loc_corrob["link_count"] == 1
      and loc_corrob["links"][0]["type"] == "shared_phone")
check("shared location is recorded as CORROBORATION of the hard-id link",
      len(loc_corrob["location_corroborations"]) == 1
      and set(loc_corrob["location_corroborations"][0]["subjects"]) == {"Subject K1", "Subject K2"}
      and not loc_corrob["shared_location_context"])


print("=" * 72)
print("WEAKNESS 4 — landline / office line never a strong cross-case link")
print("=" * 72)

# TRAP: two DIFFERENT people across two cases sharing ONLY an office landline
# (mixed formats). Must NOT link or fuse. (Different names → also W1's flag.)
lltrap = mine_case_set([
    case("Ravi Desai", phones=[("+91-22-24450010", "caseA_cdr.csv")]),
    case("Sameer Joshi", phones=[("022-24450010", "caseB_cdr.csv")]),
])
check("a shared office landline creates NO cross-case link",
      lltrap["link_count"] == 0 and lltrap["cluster_count"] == 0)
check("both subjects remain unlinked on the landline",
      set(lltrap["unlinked_subjects"]) == {"Ravi Desai", "Sameer Joshi"})
check("shared landline surfaces as CONTEXT only, never a link",
      any(set(c["subjects"]) == {"Ravi Desai", "Sameer Joshi"}
          and "never a cross-case link" in c["note"]
          for c in lltrap["shared_landline_context"]))

# Same landline shared by two same-NAMED cases must NOT fuse them either
# (W1 + W4 together: neither the name nor the office line is an anchor).
llsamename = mine_case_set([
    case("Ravi Desai", phones=[("+91-22-24450010", "a.csv")]),
    case("Ravi Desai", phones=[("+91-22-24450010", "b.csv")]),
])
check("a shared landline never fuses two same-named subjects",
      llsamename["subject_count"] == 2
      and not llsamename["same_person_merges"]
      and len(llsamename["unverified_same_name"]) == 1)

# GENUINE: two cases sharing a personal MOBILE → linked, cited to both.
mobilelink = mine_case_set([
    case("Operator One", phones=[("+91-98200-41100", "caseC_cdr.csv")]),
    case("Operator Two", phones=[("98200 41100", "caseD_cdr.csv")]),
])
check("a shared personal mobile DOES link across cases (hard id)",
      mobilelink["link_count"] == 1
      and mobilelink["links"][0]["type"] == "shared_phone"
      and set(mobilelink["links"][0]["citations"]) == {"Operator One", "Operator Two"})

# Landline corroborates a mobile-anchored link rather than standing alone.
mixed = mine_case_set([
    case("Hub One", phones=[("+91-98200-41100", "h1.csv"), ("+91-22-24450010", "h1.csv")]),
    case("Hub Two", phones=[("+91-98200-41100", "h2.csv"), ("022-24450010", "h2.csv")]),
])
check("mobile links the pair; the shared landline corroborates (not a 2nd link)",
      mixed["link_count"] == 1
      and mixed["links"][0]["type"] == "shared_phone"
      and len(mixed["weak_phone_corroborations"]) == 1
      and not mixed["shared_landline_context"])
check("single source of truth with the single-case landline typing",
      __import__("modules.entity_resolution", fromlist=["_is_landline_number"])
      ._is_landline_number("+91-22-24450010") is True
      and __import__("modules.entity_resolution", fromlist=["_is_landline_number"])
      ._is_landline_number("+91-98200-41100") is False)


print("=" * 72)
print("COMBINED — all four traps + genuine links at once, nothing fabricated")
print("=" * 72)

from modules.data_mining import mine_document_fraud_rings

combined = mine_case_set([
    # GENUINE links (shared hard identifiers) — must be caught, cited to both.
    case("Anil Kapoor", phones=[("+91-98111-00001", "g1.csv")]),
    case("Bela Rao",    phones=[("98111 00001", "g2.csv")]),                 # mobile
    case("Chandra Bose", orgs=[("Zenith LLP", "g3.pdf", "CIN-Z1")]),
    case("Deepa Nair",   orgs=[("ZENITH LLP", "g4.csv", "cin-z1")]),         # org reg
    case("Esha Roy",   counterparties=[("Nodal Agent", "g5.csv", "AC-NODE")]),
    case("Farid Khan", counterparties=[("NODAL AGENT", "g6.csv", "AC-NODE")]),  # cp acct
    # TRAP 1 — same name, different mobiles → NOT fused.
    case("Rahul Sharma", phones=[("+91-97000-11111", "t1.csv")]),
    case("Rahul Sharma", phones=[("+91-96000-22222", "t2.csv")]),
    # TRAP 2 — same org NAME, no registration → NOT linked.
    case("Manoj Iyer",  orgs=[("Global Traders", "t3.pdf")]),
    case("Nisha Pillai", orgs=[("GLOBAL TRADERS", "t4.csv")]),
    # TRAP 3 — same city only → NOT linked.
    case("Omar Sheikh", locations=[("Mumbai", "t5.csv")]),
    case("Pooja Shah",  locations=[("Mumbai", "t6.csv")]),
    case("Qadir Ali",   locations=[("MUMBAI", "t7.csv")]),
    # TRAP 4 — shared office landline only → NOT linked.
    case("Ravi Desai",  phones=[("+91-22-24450010", "t8.csv")]),
    case("Sunil Mehta", phones=[("022-24450010", "t9.csv")]),
    # Genuinely unconnected subject.
    case("Tarun Gill",  phones=[("+91-90000-99999", "i1.csv")]),
])

genuine_pairs = [{"Anil Kapoor", "Bela Rao"}, {"Chandra Bose", "Deepa Nair"},
                 {"Esha Roy", "Farid Khan"}]
cluster_sets = [set(c["subjects"]) for c in combined["clusters"]]
check("all THREE genuine hard-identifier links are caught",
      combined["link_count"] == 3
      and all(gp in cluster_sets for gp in genuine_pairs))
check("every genuine link is cited to BOTH cases",
      all(set(l["citations"]) == set(l["subjects"]) and all(l["citations"].values())
          for l in combined["links"]))
check("exactly three clusters — one per genuine pair, nothing fabricated",
      combined["cluster_count"] == 3
      and all(len(c["subjects"]) == 2 for c in combined["clusters"]))

trap_subjects = {"Rahul Sharma (case 7)", "Rahul Sharma (case 8)", "Manoj Iyer",
                 "Nisha Pillai", "Omar Sheikh", "Pooja Shah", "Qadir Ali",
                 "Ravi Desai", "Sunil Mehta", "Tarun Gill"}
clustered = {s for c in combined["clusters"] for s in c["subjects"]}
check("NO trap subject appears in any cluster (no false cross-case link)",
      not (trap_subjects & clustered))
check("same-name trap → NOT fused, raised as UNVERIFIED",
      any(f["name"] == "Rahul Sharma" for f in combined["unverified_same_name"])
      and not any("Rahul Sharma" in c["subjects"] for c in combined["clusters"]))
check("same-org-name trap → NOT linked, raised as UNVERIFIED entity",
      any(f["entity_kind"] == "organization" and set(f["subjects"]) == {"Manoj Iyer", "Nisha Pillai"}
          for f in combined["unverified_same_entity"]))
check("same-city trap → CONTEXT only, not a link",
      any(set(c["subjects"]) == {"Omar Sheikh", "Pooja Shah", "Qadir Ali"}
          for c in combined["shared_location_context"]))
check("shared-landline trap → CONTEXT only, not a link",
      any(set(c["subjects"]) == {"Ravi Desai", "Sunil Mehta"}
          for c in combined["shared_landline_context"]))
check("the genuinely unconnected subject is reported NOT linked",
      "Tarun Gill" in combined["unlinked_subjects"])
check("association-not-culpability framing intact on the combined result",
      combined["human_review_required"] is True
      and "association, not culpability" in combined["mining_notice"])
check("combined result is pure JSON-serialisable data (no objects leaked)",
      isinstance(__import__("json").loads(__import__("json").dumps(combined)), dict))

# Specialised miner must not inflate a name-only shared supplier into a ring.
ring_trap = mine_document_fraud_rings([
    case_doc := {"subject": "Forger X", "ontology": NS(
        subject_name="Forger X",
        flags=["Forged passport recovered during search", "Counterfeit visa sticker identified"],
        phones=[], transactions=[], locations=[], timeline_events=[], legal_proceedings=[],
        organizations=[NS(name="Shady Supplier", type="front", jurisdiction="",
                          offshore=False, source="x.pdf", registration="")])},
    {"subject": "Forger Y", "ontology": NS(
        subject_name="Forger Y",
        flags=["Tampered work permit seized", "Fake residence permit found in vehicle"],
        phones=[], transactions=[], locations=[], timeline_events=[], legal_proceedings=[],
        organizations=[NS(name="SHADY SUPPLIER", type="front", jurisdiction="",
                          offshore=False, source="y.pdf", registration="")])}])
check("specialised miner flags both subjects but forms NO ring on a name-only supplier",
      ring_trap["flagged_count"] == 2 and ring_trap["ring_count"] == 0
      and len(ring_trap["unverified_same_entity"]) == 1)


print()
if all(results):
    print("ALL CROSS-CASE MATCHING CHECKS PASSED")
else:
    print(f"SUMMARY: {sum(results)}/{len(results)} checks passed")
    raise SystemExit(1)
