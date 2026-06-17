"""
Generalization suite — proves noise handling works on inputs NEVER in any
fixture, and that the case-specific 'ghostwire'/'jupiter' literals were removed
in favour of structural rules.

Locks in:
  1. "Brand + Function" sender labels are rejected for brands we never listed
     (structural rule on the final token), e.g. "Globex Notifications".
  2. Operation codenames are rejected via the generic "operation <codename>"
     pattern for codenames we never listed (Trident, Bluebird, …).
  3. A bare filename-derived codename is rejected structurally via the
     filename-stem match (Check 10) when the file IS the codename.
  4. The removed literals no longer special-case ghostwire/jupiter: a BARE
     codename with no 'operation' prefix and no matching file is treated as a
     possible real name (we do NOT hardcode it).
  5. No new false positives: real surnames that collide with function words
     (Sales, Service, Care) are still accepted.

Run: python3 test_noise_generalization.py
"""
import sys
from modules.entity_resolution import is_bad_subject_name as bad

results = []
def check(label, ok):
    results.append(bool(ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")

print("=" * 72)
print("1 — unseen 'Brand + Function' sender labels rejected (structural)")
print("=" * 72)
for n in ["Globex Notifications", "Initech Helpdesk", "Wonka Payments",
          "Umbrella Refund", "Hooli Subscription", "Acme Team",
          "Cyberdyne Updates", "Stark Invoices", "Wayne Receipts",
          "Tyrell Reminders", "Soylent Admin"]:
    check(f"reject {n!r}", bad(n))

print("\n" + "=" * 72)
print("2 — unseen operation codenames rejected via generic 'operation X'")
print("=" * 72)
for n in ["Operation Trident", "Operation Bluebird", "Operation Falcon",
          "Operation Nightfall", "Operation GhostWire", "Operation Jupiter"]:
    check(f"reject {n!r}", bad(n))

print("\n" + "=" * 72)
print("3 — bare codename rejected structurally when the FILE is the codename")
print("=" * 72)
docs = [{"filename": "TRIDENT.csv"}, {"filename": "GhostWire.pdf"}]
check("reject 'Trident' when file is TRIDENT.csv",  bad("Trident", docs))
check("reject 'GhostWire' when file is GhostWire.pdf", bad("GhostWire", docs))

print("\n" + "=" * 72)
print("4 — NO hardcoded codenames: a bare codename with no context is allowed")
print("=" * 72)
# This documents intended behaviour after removing the literals: a lone word
# could be a real alias, so we do NOT auto-reject it without a signal.
check("bare 'Jupiter' (no 'operation', no matching file) is NOT auto-rejected",
      not bad("Jupiter"))
check("bare 'GhostWire' (no context) is NOT auto-rejected",
      not bad("GhostWire"))

print("\n" + "=" * 72)
print("5 — no new false positives: real surnames that look like function words")
print("=" * 72)
for n in ["Maria Sales", "Robert Service", "Jonathan Care", "Henry Bell",
          "Arjun Mehta", "Linus Torvalds", "Harshvardhan Gautam", "Bill Gates"]:
    check(f"accept {n!r}", not bad(n))

print("\n" + "=" * 72)
passed, total = sum(results), len(results)
print(f"SUMMARY: {passed}/{total} checks passed")
print("ALL GENERALIZATION CHECKS PASSED" if passed == total else "SOME CHECKS FAILED")
sys.exit(0 if passed == total else 1)
