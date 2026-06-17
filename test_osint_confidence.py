"""
Regression suite for OSINT confidence scoring (Phase OSINT — Deliverable 3).

The report confidence engine (calculate_stable_confidence) was document-centric:
it scored files / phones / timeline / graph / gaps but had NO input for confirmed
online presence. OSINT / live-search subjects carry zero uploaded documents, so a
fully-resolved web subject (e.g. a confirmed GitHub account) scored 0 — falsely
implying "no evidence".

Fix: a num_platforms signal (tiered bonus), fed from the report as the breadth of
confirmed online identity. Defaults to 0, so every document-mode caller is
unchanged.

Run: python3 test_osint_confidence.py
"""
import sys, unittest.mock as _mock
import modules.entity_resolution as ER
from modules.entity_resolution import calculate_stable_confidence as C

results = []
def check(label, ok):
    results.append(bool(ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")

print("=" * 72)
print("PART 1 — platform signal lifts OSINT (document-free) confidence off 0")
print("=" * 72)

osint_none = C(num_files=0, num_phones=0, num_timeline=0, num_graph_nodes=0,
               num_gaps=0, num_platforms=0)["confidence"]
osint_1    = C(num_files=0, num_phones=0, num_timeline=0, num_graph_nodes=2,
               num_gaps=0, num_platforms=1)["confidence"]
osint_4    = C(num_files=0, num_phones=0, num_timeline=0, num_graph_nodes=5,
               num_gaps=0, num_platforms=4)["confidence"]
print(f"    0 platforms / no docs : {osint_none}")
print(f"    1 platform  / no docs : {osint_1}")
print(f"    4 platforms / no docs : {osint_4}")
check("no evidence at all still scores 0", osint_none == 0)
check("1 confirmed platform scores > 0",  osint_1 > 0)
check("4 platforms scores higher than 1", osint_4 > osint_1)
check("web-only confidence stays moderate (<= 60)", osint_4 <= 60)

print("\n" + "=" * 72)
print("PART 2 — document-mode scores are UNCHANGED (param defaults to 0)")
print("=" * 72)
# Same args as before the change, with num_platforms omitted entirely.
doc_no_platforms_explicit = C(num_files=6, num_phones=6, num_timeline=0,
                              num_graph_nodes=15, num_gaps=4, num_platforms=0)["confidence"]
doc_no_platforms_omitted  = C(num_files=6, num_phones=6, num_timeline=0,
                              num_graph_nodes=15, num_gaps=4)["confidence"]
print(f"    6 files, num_platforms=0 : {doc_no_platforms_explicit}")
print(f"    6 files, omitted         : {doc_no_platforms_omitted}")
check("omitting num_platforms == passing 0",
      doc_no_platforms_explicit == doc_no_platforms_omitted)
check("document score with 0 platforms is 64 (unchanged baseline)",
      doc_no_platforms_omitted == 64)

print("\n" + "=" * 72)
print("PART 3 — end-to-end OSINT report: real subject + non-zero confidence")
print("=" * 72)
_mock.patch.object(ER, "_call_bedrock_for_fusion", return_value="").start()
_mock.patch.object(ER, "_call_gemini", return_value="").start()
from modules.report_generator import generate_report

person = {
    "confirmed_name": "Linus Torvalds",
    "usernames": {"GitHub": "torvalds"},
    "platforms_confirmed": ["GitHub", "Twitter", "Telegram", "HuggingFace"],
    "profile_urls": {"GitHub": "https://github.com/torvalds"},
    "github_data": {"repos": 8, "followers": 200000, "joined": "2011-09-03"},
    "bio_data": {"GitHub": "Creator of Linux and Git"},
    "location_stated": ["Portland"], "data_gaps": [],
    "anomaly_flags": [], "conflicts": [],
}
rep  = generate_report(person, mode="OSINT", user_id="regression")
conf = rep.get("sections", {}).get("overall_confidence", 0)
print(f"    subject    : {rep.get('subject')}")
print(f"    confidence : {conf}")
check("OSINT report keeps the real subject name (not 'Unknown Subject')",
      rep.get("subject") == "Linus Torvalds")
check("OSINT report confidence is non-zero", conf > 0)

print("\n" + "=" * 72)
passed, total = sum(results), len(results)
print(f"SUMMARY: {passed}/{total} checks passed")
print("ALL OSINT-CONFIDENCE CHECKS PASSED" if passed == total else "SOME CHECKS FAILED")
sys.exit(0 if passed == total else 1)
