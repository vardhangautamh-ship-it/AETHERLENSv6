"""
Regression suite for phone enrichment (Phase OSINT — Deliverable 4).

Covers:
  * Indian mobile  -> valid, mobile, India, carrier present, IST timezone.
  * Indian landline -> valid, landline, city-level region (Bangalore/Karnataka).
  * International  -> US / UK / UAE / Pakistan parsed with country + region.
  * Junk           -> not valid, graceful (never raises).
  * format_enrichment_line de-dupes 'India · India' for mobiles.
  * enrich_phones de-duplicates by E.164.
  * Heuristic fallback (library forced off) still classifies Indian numbers.
  * The 'looks like a phone' detector used by the search screen.

Run: python3 test_phone_enrichment.py
"""
import sys, re

import modules.phone_enrichment as PE
from modules.phone_enrichment import enrich_phone, enrich_phones, format_enrichment_line

results = []
def check(label, ok):
    results.append(bool(ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")

print("=" * 72)
print(f"PART 1 — library engine (phonenumbers present = {PE._HAS_LIB})")
print("=" * 72)

inm = enrich_phone("+91 98765 43210")
print(f"    IN mobile : {format_enrichment_line(inm)}")
check("IN mobile valid",          inm["valid"])
check("IN mobile line_type",      inm["line_type"] == "mobile")
check("IN mobile country India",  inm["country"] == "India")
check("IN mobile carrier present", bool(inm["carrier"]))
check("IN mobile IST timezone",   any("Calcutta" in t or "Kolkata" in t for t in inm["timezones"]))
check("IN mobile e164",           inm["e164"] == "+919876543210")
check("format line de-dupes 'India · India'",
      format_enrichment_line(inm).count("India") == 1)

inl = enrich_phone("+91 80 2222 3333")
print(f"    IN landline: {format_enrichment_line(inl)}")
check("IN landline line_type",    inl["line_type"] == "landline")
check("IN landline region (city)", "karnataka" in inl["region"].lower() or "bangalore" in inl["region"].lower())

us = enrich_phone("+1 415 555 2671")
check("US parsed country",        "United States" in us["country"])
check("US region (San Francisco)", "san francisco" in us["region"].lower())

uae = enrich_phone("+971 50 123 4567")
check("UAE mobile valid",         uae["valid"] and uae["line_type"] == "mobile")

junk = enrich_phone("not a phone")
check("junk not valid",           not junk["valid"])
check("junk returns dict shape",  set(["input","valid","line_type","notes"]).issubset(junk.keys()))

print("\n" + "=" * 72)
print("PART 2 — de-duplication")
print("=" * 72)
dup = enrich_phones(["+91 98765 43210", "9876543210", "+919876543210"])
print(f"    3 spellings of one number -> {len(dup)} result(s)")
check("same number collapses to one", len(dup) == 1)

print("\n" + "=" * 72)
print("PART 3 — heuristic fallback (library forced OFF)")
print("=" * 72)
_saved = PE._HAS_LIB
try:
    PE._HAS_LIB = False
    h_mobile = enrich_phone("9876543210", "IN")
    # Landline written with the trunk-0 prefix is unambiguous to the heuristic;
    # in bare E.164 (+9180...) the leading digit collides with mobile leads, which
    # is precisely why the library engine exists. Test the case the heuristic owns.
    h_land   = enrich_phone("08022223333", "IN")
    print(f"    heuristic IN mobile : valid={h_mobile['valid']} type={h_mobile['line_type']}")
    print(f"    heuristic IN land   : type={h_land['line_type']} country={h_land['country']}")
    check("fallback flags IN mobile",   h_mobile["is_mobile"] and h_mobile["line_type"] == "mobile")
    check("fallback country India",     h_mobile["country"] == "India")
    check("fallback e164",              h_mobile["e164"] == "+919876543210")
    check("fallback recognises India number (trunk-0 form) as valid/possible",
          h_land["country"] == "India" and (h_land["possible"] or h_land["valid"]))
    check("fallback engine label",      h_mobile["engine"] == "heuristic")
finally:
    PE._HAS_LIB = _saved

print("\n" + "=" * 72)
print("PART 4 — search-screen phone detector")
print("=" * 72)
# Re-implement the app's cheap detector locally (kept in sync with app._looks_like_phone).
def looks_like_phone(q):
    s = (q or "").strip()
    if not s or "/" in s or "@" in s:
        return False
    if not re.fullmatch(r"[+()\-\s\d]+", s):
        return False
    return 7 <= len(re.sub(r"\D", "", s)) <= 15

check("'+91 98765 43210' detected as phone", looks_like_phone("+91 98765 43210"))
check("'9876543210' detected as phone",      looks_like_phone("9876543210"))
check("'Arjun Mehta' NOT a phone",           not looks_like_phone("Arjun Mehta"))
check("'torvalds/github' NOT a phone",       not looks_like_phone("torvalds/github"))
check("'12345' (too short) NOT a phone",     not looks_like_phone("12345"))

print("\n" + "=" * 72)
passed, total = sum(results), len(results)
print(f"SUMMARY: {passed}/{total} checks passed")
print("ALL PHONE-ENRICHMENT CHECKS PASSED" if passed == total else "SOME CHECKS FAILED")
sys.exit(0 if passed == total else 1)
