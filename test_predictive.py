"""
Phase 4 Step 15 — bounded predictive elements in modules/predictive.py.

The only predictive element is RECURRENCE PROJECTION of an already-demonstrated,
dated behaviour. Covers:

  * a genuinely groundable projection: >= 3 dated cited transfers to the same
    counterparty yield a WEAK, speculative, non-determinative projection whose
    next date is exactly ONE median interval past the last occurrence, cited to
    every occurrence;
  * boundedness: only one interval ahead is ever projected; confidence is
    always WEAK; speculative=True and determinative=False on every projection;
  * the refuse-and-say-so rule: a 2-occurrence series is NOT projected and the
    reason appears in `not_built`; undated occurrences are counted but never
    dated by guesswork; unparseable dates never fabricate a series;
  * identity-blindness: identity attributes are not read and cannot create or
    change a projection (no predictive policing);
  * decision-support framing: PREDICTION_NOTICE verbatim (speculative, human
    review, not a determination, no new behaviour/escalation/identity) on every
    result; autonomous=False; human_review_required=True;
  * determinism, JSON-serialisability, honest empty handling, and irregular-
    cadence disclosure.

No LLM, no network. Run: PYTHONUTF8=1 python test_predictive.py
"""
import json
import sys
from datetime import date
from types import SimpleNamespace as NS

from modules.predictive import (
    PREDICTION_NOTICE, predict_from_ontology, render_predictions,
)

results = []
def check(label, ok):
    results.append(bool(ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")


def txn(d, cp, amount=30000, direction="out", cross_border=True, source="bank.csv"):
    return NS(date=d, direction=direction, amount=amount, cross_border=cross_border,
              counterparty=cp, structured=False, source=source)

def onto(transactions=(), flags=()):
    return NS(subject_name="S", flags=list(flags), transactions=list(transactions),
              phones=[], organizations=[], locations=[], timeline_events=[],
              legal_proceedings=[])


print("=" * 72)
print("GROUNDABLE — recurrence of a demonstrated, dated, cited behaviour")
print("=" * 72)

# Four monthly transfers to the same counterparty → median interval ~30/31d.
good = predict_from_ontology(onto([
    txn("2024-01-01", "Corridor Agent", source="jan.csv"),
    txn("2024-01-31", "Corridor Agent", source="feb.csv"),
    txn("2024-03-01", "Corridor Agent", source="mar.csv"),
    txn("2024-03-31", "Corridor Agent", source="apr.csv")]))
check("a groundable recurrence yields exactly one projection",
      good["prediction_count"] == 1)
p = good["predictions"][0]
check("projection counts every dated occurrence and cites them",
      p["occurrences"] == 4 and len(p["citations"]) == 4
      and all(c["source"] for c in p["citations"]))
check("projected next date is ONE median interval past the last occurrence",
      p["median_interval_days"] == 30
      and p["projected_next_date"]
      == date(2024, 3, 31).replace().fromordinal(date(2024, 3, 31).toordinal() + 30).isoformat())
check("projection is WEAK, speculative, and non-determinative",
      p["confidence"] == "WEAK" and p["speculative"] is True
      and p["determinative"] is False)
check("projection is labelled speculative and conditional ('IF it continues')",
      "SPECULATIVE" in p["suggestion"] and "IF it continues" in p["suggestion"]
      and "not a determination that it will occur" in p["suggestion"])
check("basis states the cited window and cadence",
      "4 cited occurrence(s) between 2024-01-01 and 2024-03-31" in p["basis"]
      and "median interval" in p["basis"])

print("=" * 72)
print("REFUSE-AND-SAY-SO — ungroundable series are not built, with a reason")
print("=" * 72)

two = predict_from_ontology(onto([
    txn("2024-01-01", "Rare Payee"), txn("2024-02-01", "Rare Payee")]))
check("a 2-occurrence series is NOT projected",
      two["prediction_count"] == 0)
check("the reason is stated in not_built (not hidden)",
      any("only 2 dated occurrence" in r and "Rare Payee" in r
          for r in two["not_built"]))

undated = predict_from_ontology(onto([
    txn("2024-01-01", "Mixed Payee"), txn("", "Mixed Payee"),
    txn("not-a-date", "Mixed Payee")]))
check("undated / unparseable dates never fabricate a datable series",
      undated["prediction_count"] == 0
      and any("Mixed Payee" in r for r in undated["not_built"]))

# A datable series survives alongside undated noise on the SAME counterparty.
mixed = predict_from_ontology(onto([
    txn("2024-01-01", "Payee"), txn("2024-01-31", "Payee"),
    txn("2024-03-01", "Payee"), txn("", "Payee")]))
check("undated occurrences counted but excluded from the dated cadence",
      mixed["prediction_count"] == 1 and mixed["predictions"][0]["occurrences"] == 3)

print("=" * 72)
print("BOUNDEDNESS / IDENTITY-BLINDNESS / IRREGULARITY")
print("=" * 72)

check("only ONE interval ahead is ever projected (single next date, not a chain)",
      isinstance(p["projected_next_date"], str)
      and "projected_dates" not in p and "projected_next_dates" not in p)

# Identity attributes in flags must not create or alter any projection.
base_txns = [txn("2024-01-01", "Agent"), txn("2024-01-31", "Agent"),
             txn("2024-03-01", "Agent")]
id_a = predict_from_ontology(onto(base_txns))
id_b = predict_from_ontology(onto(base_txns, flags=["Bangladeshi national", "Muslim"]))
check("identity attributes never create or change a projection",
      id_a["predictions"] == id_b["predictions"]
      and id_a["prediction_count"] == 1)

irregular = predict_from_ontology(onto([
    txn("2024-01-01", "Erratic"), txn("2024-01-08", "Erratic"),
    txn("2024-01-15", "Erratic"), txn("2024-09-01", "Erratic")]))
check("irregular cadence is disclosed, not hidden",
      irregular["prediction_count"] == 1
      and irregular["predictions"][0]["irregular_cadence"] is True
      and "irregular" in irregular["predictions"][0]["basis"])

print("=" * 72)
print("FRAMING / DETERMINISM / HONESTY")
print("=" * 72)

check("result declares itself non-autonomous, non-determinative, human-reviewed",
      good["autonomous"] is False and good["determinative"] is False
      and good["human_review_required"] is True)
check("verbatim notice states the bounds (speculative, no new behaviour, no identity)",
      good["prediction_notice"] == PREDICTION_NOTICE
      and "NOT a prediction that it will" in PREDICTION_NOTICE
      and "No new behaviour, no escalation" in PREDICTION_NOTICE
      and "nationality, ethnicity, or religion" in PREDICTION_NOTICE)
check("deterministic — identical output on identical input",
      predict_from_ontology(onto(base_txns)) == predict_from_ontology(onto(base_txns)))
check("result is pure JSON-serialisable data",
      isinstance(json.loads(json.dumps(good)), dict))
check("empty / no-transaction case → no predictions, notice still present",
      predict_from_ontology(onto([]))["prediction_count"] == 0
      and predict_from_ontology(onto([]))["prediction_notice"] == PREDICTION_NOTICE)
check("None ontology → no predictions, no crash",
      predict_from_ontology(None)["prediction_count"] == 0)

rendered = render_predictions(good)
check("rendered output carries the notice, the weak/speculative label, and citations",
      PREDICTION_NOTICE in rendered and "[WEAK / SPECULATIVE]" in rendered
      and "occurrence 2024-01-01" in rendered)
check("rendered no-prediction case says nothing was built and why",
      "no predictive element built" in render_predictions(predict_from_ontology(onto([])))
      and "NOT BUILT" in render_predictions(two))

print("=" * 72)
passed, total = sum(results), len(results)
print(f"SUMMARY: {passed}/{total} checks passed")
if passed == total:
    print("ALL PREDICTIVE CHECKS PASSED"); sys.exit(0)
sys.exit(1)
