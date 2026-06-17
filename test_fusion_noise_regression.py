"""
Regression suite for the GHOSTWIRE_01..07 fusion-report failures.

Reproduces and locks the three faults seen in a real Bedrock-generated report
where the subject came out as "Anthropic Billing":

  (A) A transactional / vendor email-sender label ("Anthropic Billing"),
      an email greeting ("Dear Sir") or a transaction line ("Swiggy Order")
      must never be accepted as the subject — even when the AI returns it.
  (B) The location field must drop spam-SMS labels ("Spam - Job offer") while
      KEEPING real places ("Gurugram", "Mumbai").
  (C) §05 network map and §08 key associations must exclude those same noise
      labels from graph nodes.

Run: python3 test_fusion_noise_regression.py
"""
import sys, json, unittest.mock as mock
import networkx as nx

import modules.entity_resolution as ER
from modules.entity_resolution import (
    is_bad_subject_name, clean_person_object,
    resolve_entity_from_multiple_docs, _is_noise_location,
)
from modules.relationship_mapper import get_key_associations, graph_summary

results = []
def check(label, ok):
    results.append(bool(ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")

print("=" * 72)
print("PART A — transactional/greeting labels are not valid subjects")
print("=" * 72)
BAD = ["Anthropic Billing", "Dear Sir", "Dear Madam", "Swiggy Order",
       "Netflix Subscription", "Amazon Purchase", "Zomato Order",
       "Stripe Payment", "Acme Support", "Acme Notifications", "noreply"]
GOOD = ["Harshvardhan Gautam", "Arjun Mehta", "Rajan Iyer", "Priya Sharma",
        "Linus Torvalds", "Bill Gates"]
for n in BAD:
    check(f"reject  {n!r}", is_bad_subject_name(n))
for n in GOOD:
    check(f"accept  {n!r}", not is_bad_subject_name(n))

print("\n" + "=" * 72)
print("PART B — location field drops spam labels, keeps real places")
print("=" * 72)
spam_locs = ["Spam - Job offer", "Spam - OTP fraud attempt", "Spam - Credit card",
             "Spam - Mutual fund", "Spam - Electricity bill", "Spam - Car insurance"]
real_locs = ["Gurugram", "Mumbai", "Sector 105", "New Delhi"]
for s in spam_locs:
    check(f"noise location: {s!r}", _is_noise_location(s))
for r in real_locs:
    check(f"real  location: {r!r}", not _is_noise_location(r))

p = {"location_stated": spam_locs + real_locs}
clean_person_object(p)
print(f"    kept after clean: {p['location_stated']}")
check("clean_person_object keeps exactly the real places",
      p["location_stated"] == real_locs)

print("\n" + "=" * 72)
print("PART C — graph §05/§08 exclude noise entity nodes")
print("=" * 72)
G = nx.DiGraph()
for name, t in [("Harshvardhan Gautam", "person"), ("Dear Sir", "person"),
                ("Swiggy Order", "org"), ("Netflix Subscription", "org"),
                ("Rajan Iyer", "person"), ("Gurugram", "location")]:
    G.add_node(name, label=name, node_type=t)
for other in ["Dear Sir", "Swiggy Order", "Netflix Subscription", "Rajan Iyer"]:
    G.add_edge("Harshvardhan Gautam", other)

assoc = [a["name"] for a in get_key_associations(G, "Harshvardhan Gautam")]
print(f"    §08 associations: {assoc}")
check("§08 keeps real associate Rajan Iyer", "Rajan Iyer" in assoc)
check("§08 drops 'Dear Sir'",       "Dear Sir" not in assoc)
check("§08 drops 'Swiggy Order'",   "Swiggy Order" not in assoc)
check("§08 drops 'Netflix Subscription'", "Netflix Subscription" not in assoc)

top = [n["label"] for n in graph_summary(G, subject_name="Harshvardhan Gautam")["top_nodes"]]
print(f"    §05 top_nodes   : {top}")
check("§05 drops 'Dear Sir'",     "Dear Sir" not in top)
check("§05 drops 'Swiggy Order'", "Swiggy Order" not in top)
check("§05 keeps real location 'Gurugram'", "Gurugram" in top)

print("\n" + "=" * 72)
print("PART D — end-to-end: AI returns 'Anthropic Billing', resolver keeps real subject")
print("=" * 72)
ai_json = json.dumps({
    "confirmed_name": "Anthropic Billing",
    "usernames": {"GitHub": "ghostwire"},
    "platforms_confirmed": ["GitHub"],
    "location_stated": ["Spam - Job offer", "Gurugram"],
})
with mock.patch.object(ER, "_call_bedrock_for_fusion", return_value=ai_json), \
     mock.patch.object(ER, "_call_gemini", return_value=""):
    def doc(fn, names, locs=None, primary=None):
        return {"filename": fn, "primary_subject": primary or "",
                "entities": {"names": [{"value": n} for n in names], "phones": [], "emails": [], "locations": []},
                "locations": locs or [], "structured_rows": [], "document_flags": [], "raw_text": " ".join(names)}
    docs = [
        doc("GHOSTWIRE_01_college_record.pdf", ["Harshvardhan Gautam", "Harshvardhan Gautam"],
            primary="Harshvardhan Gautam"),
        doc("GHOSTWIRE_06_email_receipts.csv", ["Anthropic Billing", "Dear Sir"],
            locs=["Spam - Job offer", "Gurugram"], primary="Anthropic Billing"),
    ]
    person, method = resolve_entity_from_multiple_docs(docs)
    clean_person_object(person)

print(f"    AI tried: 'Anthropic Billing'  ->  final subject: {person.get('confirmed_name')!r}")
print(f"    final locations: {person.get('location_stated')}")
check("subject is the real person, not the billing label",
      person.get("confirmed_name") == "Harshvardhan Gautam")
check("no spam location survives", not any(_is_noise_location(l) for l in person.get("location_stated", [])))

print("\n" + "=" * 72)
print("PART E — report #2: 'Student' label subject + promo/bank transaction nodes")
print("=" * 72)
# Academic/role label and bank/promo transaction lines must not win.
for n in ["Student", "Customer", "Cardholder", "Account Holder",
          "Current", "Wallet Recharge", "Big Billion Days"]:
    check(f"reject report#2 noise {n!r}", is_bad_subject_name(n))

# AI returns the role label 'Student'; resolver must keep the real person.
ai_student = json.dumps({"confirmed_name": "Student", "platforms_confirmed": ["GitHub"]})
with mock.patch.object(ER, "_call_bedrock_for_fusion", return_value=ai_student), \
     mock.patch.object(ER, "_call_gemini", return_value=""):
    def d(fn, names, primary=None):
        return {"filename": fn, "primary_subject": primary or "",
                "entities": {"names": [{"value": n} for n in names], "phones": [], "emails": [], "locations": []},
                "locations": [], "structured_rows": [], "document_flags": [], "raw_text": " ".join(names)}
    docs = [d("GHOSTWIRE_01_college_record.pdf", ["Harshvardhan Gautam", "Harshvardhan Gautam"],
              primary="Harshvardhan Gautam"),
            d("GHOSTWIRE_07_bank_statement.csv", ["Student", "Big Billion Days", "Wallet Recharge"])]
    person, _ = resolve_entity_from_multiple_docs(docs)
    clean_person_object(person)
print(f"    AI tried 'Student' -> final subject: {person.get('confirmed_name')!r}")
check("subject is the real person, not the role label 'Student'",
      person.get("confirmed_name") == "Harshvardhan Gautam")

# §08/§05 exclude isolated promo/transaction nodes; keep the connected associate.
G = nx.DiGraph()
for name, t in [("Harshvardhan Gautam", "person"), ("Current", "org"),
                ("Big Billion Days", "org"), ("Wallet Recharge", "org"),
                ("Hugging Face", "org"), ("Rajan Iyer", "person")]:
    G.add_node(name, label=name, node_type=t)
G.add_edge("Harshvardhan Gautam", "Rajan Iyer")          # only real connection
a2 = [a["name"] for a in get_key_associations(G, "Harshvardhan Gautam")]
print(f"    §08 associations: {a2}")
check("§08 keeps connected associate", a2 == ["Rajan Iyer"])
check("§08 drops isolated 'Hugging Face'/'Big Billion Days'/'Wallet Recharge'/'Current'",
      not any(x in a2 for x in ["Hugging Face", "Big Billion Days", "Wallet Recharge", "Current"]))

print("\n" + "=" * 72)
print("PART F — report #3: 'The Nodal Officer' letter-addressee label")
print("=" * 72)
# GHOSTWIRE_04_certin_inquiry.txt is a legal letter "To, The Nodal Officer, CERT-In..."
# The addressee role must never win as subject.
role_noise = [
    "The Nodal Officer", "The Inspector General", "The Director General",
    "The Registrar", "The Commissioner", "The Superintendent",
    "The Secretary", "The Chairman", "The Principal",
    "Officer Mehta",           # role-prefix case
    "Nodal Officer",           # no article, role suffix
]
for n in role_noise:
    check(f"reject role label {n!r}", is_bad_subject_name(n))

# Real personal names with these words in them must NOT be rejected.
ok_names = ["Harshvardhan Gautam", "Rajan Iyer", "Priya Sharma",
            "Linus Torvalds", "Bill Gates"]
for n in ok_names:
    check(f"keep real name {n!r}", not is_bad_subject_name(n))

# End-to-end: AI returns 'The Nodal Officer', resolver keeps real person.
ai_nodal = json.dumps({"confirmed_name": "The Nodal Officer", "platforms_confirmed": []})
with mock.patch.object(ER, "_call_bedrock_for_fusion", return_value=ai_nodal), \
     mock.patch.object(ER, "_call_gemini", return_value=""):
    def d3(fn, names, primary=None):
        return {"filename": fn, "primary_subject": primary or "",
                "entities": {"names": [{"value": n} for n in names], "phones": [], "emails": [], "locations": []},
                "locations": [], "structured_rows": [], "document_flags": [], "raw_text": " ".join(names)}
    docs = [
        d3("GHOSTWIRE_01_college_record.pdf", ["Harshvardhan Gautam", "Harshvardhan Gautam"],
           primary="Harshvardhan Gautam"),
        d3("GHOSTWIRE_04_certin_inquiry.txt",
           ["The Nodal Officer", "Harshvardhan Gautam", "The Nodal Officer"],
           primary="The Nodal Officer"),
    ]
    person3, _ = resolve_entity_from_multiple_docs(docs)
    clean_person_object(person3)
print(f"    AI tried 'The Nodal Officer' -> final subject: {person3.get('confirmed_name')!r}")
check("subject is the real person, not the role addressee",
      person3.get("confirmed_name") == "Harshvardhan Gautam")

print("\n" + "=" * 72)
passed, total = sum(results), len(results)
print(f"SUMMARY: {passed}/{total} checks passed")
print("ALL FUSION-NOISE CHECKS PASSED" if passed == total else "SOME CHECKS FAILED")
sys.exit(0 if passed == total else 1)
