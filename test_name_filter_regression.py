"""
Regression suite for the subject-name noise filter (Phase OSINT — Deliverable 2).

Background: is_bad_subject_name() is the Phase 0 guard that stops document
titles / spam labels / filenames from being chosen as the investigation
subject. Running the live OSINT pipeline surfaced that it was ALSO rejecting
legitimate human names, which blanked the report subject to "Unknown Subject"
and forced confidence to 0:

  * Check 9 matched FILENAME_SKIP_PATTERNS as naked substrings, so 'val'
    rejected 'Torvalds'/'Sandoval', 'naka' rejected 'Tanaka', 'sea' rejected
    'Sean', 'bridge' rejected 'Bridges'/'Bridget'.
  * 'bill' (a noise/legal token) rejected the common first name 'Bill'
    (Gates / Murray / Clinton).
  * 'park' (a location token) rejected the very common surname 'Park'
    (Park Chan-wook, Rosa Park) — matched even on a token boundary, so it was
    removed from the filename patterns.
  * 'law'/'laws' rejected the surnames 'Law' (Jude Law) and 'Laws'.

This suite locks in: real names pass, genuine noise is still rejected.

Run: python3 test_name_filter_regression.py
"""
import sys
import modules.entity_resolution as ER

results = []
def check(label, ok):
    results.append(bool(ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")

# ── Real names that were (or could be) wrongly rejected ──────────────────────
REAL_NAMES = [
    # (name, the buggy mechanism it exercises)
    ("Linus Torvalds", "'val' substring in Torvalds"),
    ("Diego Sandoval", "'val' substring in Sandoval"),
    ("Hiroshi Tanaka", "'naka' substring in Tanaka"),
    ("Sean Connery",   "'sea' substring in Sean"),
    ("Jeff Bridges",   "'bridge' substring in Bridges"),
    ("Bridget Jones",  "'bridge' substring in Bridget"),
    ("Naval Ravikant", "'val' substring in Naval"),
    ("Frank Linklater","'link' substring in Linklater"),
    ("Bill Gates",     "'bill' noise/legal token"),
    ("Bill Murray",    "'bill' noise/legal token"),
    ("Bill Clinton",   "'bill' noise/legal token"),
    ("Park Chan-wook", "'park' location token (surname)"),
    ("Rosa Park",      "'park' location token (surname)"),
    ("Jude Law",       "'law' legal token (surname)"),
    ("John Laws",      "'laws' legal token (surname)"),
    # control names that never tripped the bug — must keep passing
    ("Arjun Mehta",    "control"),
    ("Priya Sharma",   "control"),
    ("Sundar Pichai",  "control"),
    ("Mark Zuckerberg","control"),
]

# ── Genuine noise that MUST still be rejected (no over-correction) ───────────
TRUE_NOISE = [
    "In Cyber Incident Inquiry",
    "Operation Jupiter",
    "Manesar Campus",
    "@spam",
    "reels",
    "test_data",
    "val1",
    "anpr_log",
    "GHOSTWIRE",
    "Source A",
    "Empty Data",
    "Electricity Bill",     # 'electricity' token still catches bill-spam
    "Personal Loan Offer",
    "financial_records",
    "Cyber Park Sector",    # 'cyber' + 'sector' still catch park-location noise
]

print("=" * 72)
print("PART 1 — legitimate human names must NOT be rejected")
print("=" * 72)
for name, why in REAL_NAMES:
    check(f"{name:18} accepted  ({why})", not ER.is_bad_subject_name(name))

print("\n" + "=" * 72)
print("PART 2 — genuine noise must STILL be rejected")
print("=" * 72)
for noise in TRUE_NOISE:
    check(f"{noise:26} rejected", ER.is_bad_subject_name(noise))

print("\n" + "=" * 72)
passed, total = sum(results), len(results)
print(f"SUMMARY: {passed}/{total} checks passed")
print("ALL NAME-FILTER CHECKS PASSED" if passed == total else "SOME CHECKS FAILED")
sys.exit(0 if passed == total else 1)
