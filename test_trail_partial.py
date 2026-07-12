"""
CHIMERA Tier-2 Item 7 stress test — partial trail reconstruction
(modules/trail_following.py subject-side mode).

Ground truth: from the subject's own records alone the engine lays out the
cited legs, flags a blank-counterparty entry as an OBSCURED HOP, notes a
round-trip SHAPE (consistent-with, never asserted) when money leaves and a
comparable credit returns with the counterparty blank — and fabricates
NOTHING. Control: legs that genuinely don't connect must NOT be connected.
The original all-named multi-hop reconstruction must keep working.
Run: python test_trail_partial.py
"""
from modules.trail_following import (follow_trails, extract_flow_edges,
                                     extract_obscured_legs, render_trail_lines)

results = []
def check(label, ok):
    results.append(bool(ok)); print(f"  [{'PASS' if ok else 'FAIL'}] {label}")


PERSON = {"confirmed_name": "Arjun Mehta", "anchor_identities": []}

def fin_doc(rows, filename="bank_statement.csv"):
    return [{"filename": filename, "structured_rows": rows, "raw_text": ""}]

def types(findings):
    return sorted({f["type"] for f in findings})


# ── 1. subject-side chain, blank return hop, round-trip shape ─────────────────
rows = [
    {"date": "2023-01-10", "account_holder": "Arjun Mehta",
     "transaction_type": "KICKBACK_IN", "counterparty": "Vendor Kickbacks Ltd",
     "amount_inr": "5000000", "transaction_ref": "TXN-1"},
    {"date": "2023-02-01", "account_holder": "Arjun Mehta",
     "transaction_type": "TRANSFER_OUT", "counterparty": "Shell Alpha Pvt Ltd",
     "amount_inr": "4000000", "transaction_ref": "TXN-2"},
    {"date": "2023-02-20", "account_holder": "Shell Alpha Pvt Ltd",
     "transaction_type": "REMITTANCE_OUT", "counterparty": "Offshore Beta FZE",
     "amount_inr": "3800000", "transaction_ref": "TXN-3"},
    {"date": "2023-04-01", "account_holder": "Arjun Mehta",
     "transaction_type": "TRANSFER_IN", "counterparty": "",
     "amount_inr": "3600000", "transaction_ref": "TXN-4"},
]
f = follow_trails(PERSON, None, fin_doc(rows))
t = types(f)
check("does NOT punt (findings produced from subject-side records)", bool(f))
check("obscured hop flagged", "OBSCURED_HOP" in t)
check("round-trip SHAPE noted (subject-side)", "SUBJECT_SIDE_ROUNDTRIP" in t)
check("broken trail at the offshore hop still reported", "BROKEN_TRAIL" in t)
check("subject-side legs laid out", "SUBJECT_SIDE_PARTIAL" in t)
blob = " ".join(x["finding"] for x in f)
check("no CIRCULAR_FLOW asserted (loop is NOT closed)",
      "CIRCULAR_FLOW" not in t)
check("round-trip is consistent-with, origin NOT inferred",
      "CONSISTENT WITH" in blob and "NOT been inferred" in blob)
check("obscured leg carries no invented counterparty name",
      "BLANK" in blob and "Offshore Beta FZE ->" not in blob.split(
          "PARTIAL ROUND-TRIP")[0].split("OBSCURED HOP")[-1])
check("every cited amount is a source amount",
      all(s in blob for s in ("5,000,000", "4,000,000", "3,800,000", "3,600,000")))

# ── 2. control: legs that genuinely don't connect must NOT be connected ───────
rows_nc = [
    {"date": "2023-01-10", "account_holder": "Arjun Mehta",
     "transaction_type": "TRANSFER_OUT", "counterparty": "Shell Alpha Pvt Ltd",
     "amount_inr": "1000000", "transaction_ref": "TXN-1"},
    # blank credit: tiny amount, far outside the 50%-115% window AND >90 days
    {"date": "2023-08-25", "account_holder": "Arjun Mehta",
     "transaction_type": "TRANSFER_IN", "counterparty": "",
     "amount_inr": "100000", "transaction_ref": "TXN-2"},
]
f_nc = follow_trails(PERSON, None, fin_doc(rows_nc))
t_nc = types(f_nc)
check("control: NO round-trip invented for unconnectable legs",
      "SUBJECT_SIDE_ROUNDTRIP" not in t_nc)
check("control: blank credit still flagged as obscured hop (honest)",
      "OBSCURED_HOP" in t_nc)
check("control: legs still laid out without connecting them",
      "SUBJECT_SIDE_PARTIAL" in t_nc)

# ── 3. named-edge regression: full circular flow still reconstructs ───────────
rows_circ = [
    {"date": "2023-01-05", "account_holder": "Arjun Mehta",
     "direction": "out", "counterparty": "Hop One Ltd",
     "amount": "2000000"},
    {"date": "2023-01-20", "account_holder": "Hop One Ltd",
     "direction": "out", "counterparty": "Hop Two FZE",
     "amount": "1900000"},
    {"date": "2023-02-10", "account_holder": "Hop Two FZE",
     "direction": "out", "counterparty": "Arjun Mehta",
     "amount": "1800000"},
]
f_circ = follow_trails(PERSON, None, fin_doc(rows_circ))
check("named-edge CIRCULAR_FLOW still reconstructs (direction column)",
      "CIRCULAR_FLOW" in types(f_circ))

# same loop expressed via transaction_type instead of a direction column
rows_circ2 = [dict(r) for r in rows_circ]
for r in rows_circ2:
    r["transaction_type"] = "TRANSFER_OUT"; r.pop("direction")
f_circ2 = follow_trails(PERSON, None, fin_doc(rows_circ2))
check("named-edge CIRCULAR_FLOW reconstructs from transaction_type",
      "CIRCULAR_FLOW" in types(f_circ2))

# ── 4. edge extraction guards ──────────────────────────────────────────────────
check("blank-cp rows never become graph edges",
      len(extract_flow_edges(PERSON, fin_doc(rows))) == 3)
legs = extract_obscured_legs(PERSON, fin_doc(rows))
check("obscured extractor finds exactly the blank subject-side leg",
      len(legs) == 1 and legs[0]["amount"] == 3600000.0)
check("third party's blank row is NOT a subject leg",
      not extract_obscured_legs(PERSON, fin_doc([
          {"date": "2023-01-10", "account_holder": "Someone Else",
           "transaction_type": "TRANSFER_IN", "counterparty": "",
           "amount_inr": "999999"}])))
check("directionless blank row is skipped (never guessed)",
      not extract_obscured_legs(PERSON, fin_doc([
          {"date": "2023-01-10", "account_holder": "Arjun Mehta",
           "transaction_type": "ASSET_PURCHASE", "counterparty": "",
           "amount_inr": "999999"}])))

# ── 5. determinism + rendering ─────────────────────────────────────────────────
check("deterministic across runs",
      follow_trails(PERSON, None, fin_doc(rows))
      == follow_trails(PERSON, None, fin_doc(rows)))
lines = " ".join(render_trail_lines(f))
check("renders with the no-inference banner",
      "No hop has been inferred" in lines)


print()
if all(results):
    print("ALL TRAIL-PARTIAL CHECKS PASSED")
else:
    print(f"SUMMARY: {sum(results)}/{len(results)} checks passed")
    raise SystemExit(1)
