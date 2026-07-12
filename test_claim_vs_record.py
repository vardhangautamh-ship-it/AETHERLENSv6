"""
CHIMERA Tier-2 Item 6 stress test — claim-vs-record contradiction detector
(modules/contradiction_hunt.py check 5).

Ground truth: a subject CLAIM that contradicts an OFFICIAL record is surfaced
with BOTH sides cited and an explicit no-guess resolution; a claim that AGREES
with the record (travel dated outside the claimed period, or a record that
itself negates travel) must NOT fire; third-party records must NOT fire; the
finding must quote only the two source texts (no invented facts).
Run: python test_claim_vs_record.py
"""
from modules.contradiction_hunt import hunt_contradictions

results = []
def check(label, ok):
    results.append(bool(ok)); print(f"  [{'PASS' if ok else 'FAIL'}] {label}")


PERSON = {"confirmed_name": "Arjun Mehta", "name_variants": ["A. Mehta"]}

def doc(filename, rows=None, text=""):
    return {"filename": filename, "structured_rows": rows or [],
            "raw_text": text, "entities": {}}

def cvr(findings):
    return [f for f in findings if f.get("type") == "CLAIM_VS_RECORD"]


# ── 1. contradiction: presence claim vs dated entry/exit record ───────────────
docs = [doc("case_events.csv", rows=[
    {"date": "2023-05-06", "subject_name": "Arjun Mehta",
     "event_type": "CLAIMED_TRAVEL",
     "detail": "Subject claims he was in Mumbai throughout April-May 2023 "
               "(no foreign travel)",
     "source_ref": "STMT/2023/0100"},
    {"date": "2023-05-06", "subject_name": "A. Mehta",
     "event_type": "ENTRY_RECORD",
     "detail": "Immigration entry/exit record shows subject exited to Dubai "
               "2023-04-28 and re-entered 2023-05-01",
     "source_ref": "FRRO/REC/2023/0200"},
])]
f = cvr(hunt_contradictions(PERSON, None, docs))
check("contradicting pair FIRES", len(f) == 1)
if f:
    a, b = f[0]["side_a"], f[0]["side_b"]
    check("claim side cited (STMT source)",  "STMT/2023/0100" in a["source"])
    check("record side cited (FRRO source)", "FRRO/REC/2023/0200" in b["source"])
    check("both sides carry a confidence",
          isinstance(a.get("confidence"), int) and isinstance(b.get("confidence"), int))
    check("resolution is explicit no-guess", "does not guess" in f[0]["resolution"])
    joined = (a["claim"] + b["claim"])
    check("finding quotes only source texts (no invented location/date)",
          "Mumbai" in joined and "Dubai" in joined and "2023-04-28" in joined)
else:
    for label in ("claim side cited", "record side cited", "confidences",
                  "no-guess", "quotes sources"):
        check(label, False)

# ── 2. control: record dated OUTSIDE the claimed period must NOT fire ─────────
docs = [doc("case_events.csv", rows=[
    {"subject_name": "Arjun Mehta", "event_type": "CLAIMED_TRAVEL",
     "detail": "Subject claims he was in Mumbai throughout April-May 2023 "
               "(no foreign travel)", "source_ref": "STMT/1"},
    {"subject_name": "Arjun Mehta", "event_type": "ENTRY_RECORD",
     "detail": "Immigration entry/exit record shows subject exited to Dubai "
               "2023-07-15 and re-entered 2023-07-20", "source_ref": "FRRO/2"},
])]
check("agreeing pair (travel outside claimed period) does NOT fire",
      not cvr(hunt_contradictions(PERSON, None, docs)))

# ── 3. control: record that itself negates travel must NOT fire ───────────────
docs = [doc("case_events.csv", rows=[
    {"subject_name": "Arjun Mehta", "event_type": "CLAIMED_TRAVEL",
     "detail": "Subject claims no foreign travel during April-May 2023",
     "source_ref": "STMT/1"},
    {"subject_name": "Arjun Mehta", "event_type": "ENTRY_RECORD",
     "detail": "Immigration entry/exit record 2023-04-30: no exit or "
               "departure recorded for the subject", "source_ref": "FRRO/3"},
])]
check("agreeing pair (record confirms no departure) does NOT fire",
      not cvr(hunt_contradictions(PERSON, None, docs)))

# ── 4. absolute negation (no period stated) vs any dated travel record ────────
docs = [doc("case_note.txt", text=(
    "Subject statement: he claims he never travelled abroad. STMT/4\n"
    "FRRO official record: subject departed to Singapore 2022-11-03 and "
    "re-entered 2022-11-09.\n"))]
check("absolute never-travelled claim vs dated record FIRES",
      len(cvr(hunt_contradictions(PERSON, None, docs))) == 1)

# ── 5. claimed status vs documentary record (period-less pair) ────────────────
docs = [doc("case_events.csv", rows=[
    {"subject_name": "Arjun Mehta", "event_type": "STATEMENT",
     "detail": "Subject maintains he holds no passport", "source_ref": "STMT/5"},
    {"subject_name": "Arjun Mehta", "event_type": "DOC_RECORD",
     "detail": "Official register: passport number M1234567 issued 2019-03-04",
     "source_ref": "RPO/6"},
])]
check("claimed no-passport vs passport record FIRES",
      len(cvr(hunt_contradictions(PERSON, None, docs))) == 1)

# ── 6. third-party guard: a record about a DIFFERENT person must NOT fire ─────
docs = [doc("case_events.csv", rows=[
    {"subject_name": "Arjun Mehta", "event_type": "CLAIMED_TRAVEL",
     "detail": "Subject claims no foreign travel during April-May 2023",
     "source_ref": "STMT/1"},
    {"subject_name": "Rakesh Gupta", "event_type": "ENTRY_RECORD",
     "detail": "Immigration entry/exit record shows subject exited to Dubai "
               "2023-04-28 and re-entered 2023-05-01", "source_ref": "FRRO/7"},
])]
check("third party's travel record does NOT fire against subject's claim",
      not cvr(hunt_contradictions(PERSON, None, docs)))

# ── 7. determinism: same inputs, identical findings ────────────────────────────
docs = [doc("case_events.csv", rows=[
    {"subject_name": "Arjun Mehta", "event_type": "CLAIMED_TRAVEL",
     "detail": "Subject claims he was in Mumbai throughout April-May 2023 "
               "(no foreign travel)", "source_ref": "STMT/1"},
    {"subject_name": "Arjun Mehta", "event_type": "ENTRY_RECORD",
     "detail": "Immigration entry/exit record shows subject exited to Dubai "
               "2023-04-28 and re-entered 2023-05-01", "source_ref": "FRRO/8"},
])]
check("deterministic across runs",
      cvr(hunt_contradictions(PERSON, None, docs))
      == cvr(hunt_contradictions(PERSON, None, docs)))


print()
if all(results):
    print("ALL CLAIM-VS-RECORD CHECKS PASSED")
else:
    print(f"SUMMARY: {sum(results)}/{len(results)} checks passed")
    raise SystemExit(1)
