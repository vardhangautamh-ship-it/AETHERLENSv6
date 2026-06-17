"""
Regression suite for form-field caption stripping at ingestion (root-cause fix).

Form documents (college records, KYC, bank forms) are "Caption: Value" pairs.
The name extractor used to emit the CAPTION ("Student Name", "Account Holder")
as a name candidate, which then won subject resolution ("Student").

Structural rule: a capitalised phrase immediately followed by ':' or '|' is a
field caption, not a person — dropped regardless of the label word. The value
that follows is captured as its own match, so the real name survives.

Run: python3 test_caption_stripping.py
"""
import sys
from modules.data_ingestion import _extract_names, extract_subject_name

results = []
def check(label, ok):
    results.append(bool(ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")

FORM = """AMITY LAW SCHOOL — STUDENT RECORD
Student Name: Harshvardhan Gautam
Father's Name: Rajan Gautam
Account Holder: Harshvardhan Gautam
Nominee Name: Priya Sharma
Complainant: Arjun Mehta
Guarantor Name: Vikram Singh"""

names = [n["value"] for n in _extract_names(FORM)]
print(f"  extracted names: {names}")

print("\n[1] captions are NOT emitted as names")
for caption in ["Student Name", "Account Holder", "Nominee Name",
                "Guarantor Name", "Father", "Complainant"]:
    check(f"caption {caption!r} not a name", caption not in names)

print("\n[2] the real VALUES are kept")
for value in ["Harshvardhan Gautam", "Rajan Gautam", "Priya Sharma",
              "Arjun Mehta", "Vikram Singh"]:
    check(f"value {value!r} kept", value in names)

print("\n[3] subject resolves to the real person, not a label")
subj = extract_subject_name(FORM)
print(f"  subject = {subj!r}")
check("subject is a real person name", subj == "Harshvardhan Gautam")

print("\n[4] a normal name NOT followed by a colon is unaffected")
plain = "The meeting included Harshvardhan Gautam and Rajan Iyer at the venue."
pnames = [n["value"] for n in _extract_names(plain)]
check("plain-text names still extracted", "Harshvardhan Gautam" in pnames and "Rajan Iyer" in pnames)

print("\n" + "=" * 72)
passed, total = sum(results), len(results)
print(f"SUMMARY: {passed}/{total} checks passed")
print("ALL CAPTION-STRIPPING CHECKS PASSED" if passed == total else "SOME CHECKS FAILED")
sys.exit(0 if passed == total else 1)
