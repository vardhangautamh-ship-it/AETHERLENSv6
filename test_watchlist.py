"""
Phase 1.5 Step 11 — dynamic watchlist (review-gated) in modules/targeting.py.

Covers:
  * evidence-based membership: only cases at/above the §16 HIGH boundary
    (WATCHLIST_MIN_SCORE) are listed; the boundary itself is exercised
    (55 in, 54 out); below-threshold and malformed cases are counted, never
    guessed; unscored cases go to MANUAL TRIAGE, never auto-listed;
  * review-gating: every entry is PENDING OFFICER REVIEW with
    legal_basis_required=True; the verbatim LEGAL_BASIS_NOTICE and the
    Phase 1.5 HUMAN_AUTHORISATION_NOTICE appear on every list; the structure
    declares review_aid_only=True, surveillance_automation=False, and an
    automated_action of NONE — and is pure JSON-serialisable data (nothing
    executable);
  * the DYNAMIC property: membership is recomputed from current evidence and
    added/removed/retained changes are reported against a previous build;
  * determinism and identity-blindness (identity words outside the evidence
    never change membership).

No LLM, no network. Run: PYTHONUTF8=1 python test_watchlist.py
"""
import json
import sys

from modules.targeting import (
    HUMAN_AUTHORISATION_NOTICE, LEGAL_BASIS_NOTICE, WATCHLIST_MIN_SCORE,
    build_watchlist, render_watchlist,
)

results = []
def check(label, ok):
    results.append(bool(ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")


def mkcase(name, score, level="HIGH", strongs=0, total=0, case_type="financial",
           extra_sections=None):
    pats = [{"pattern_id": f"P{i}", "pattern_name": f"Pattern {i}",
             "case_type": case_type,
             "confidence": "STRONG" if i < strongs else "MODERATE",
             "explanation": "e", "triggers": ["t"], "sources": ["s.csv"]}
            for i in range(total)]
    sections = {
        "pattern_analysis": {"case_type": case_type, "patterns": pats,
                             "pattern_count": total},
        "risk_assessment": {"risk_score": score, "risk_level": level,
                            "items": ([f"RISK SCORE: {score}/100 — {level}"]
                                      if score is not None else [])},
        "data_gaps": {"items": []}}
    sections.update(extra_sections or {})
    return {"subject": name, "sections": sections}


print("=" * 72)
print("MEMBERSHIP — evidence-based, threshold-gated, honest about the rest")
print("=" * 72)

cases = [mkcase("Critical Case", 82, "CRITICAL", 3, 4),
         mkcase("High Case", 61, "HIGH", 1, 2),
         mkcase("Low Case", 20, "LOW", 0, 1),
         mkcase("Unscored Case", None, "UNKNOWN", 2, 3)]
wl = build_watchlist(cases)
names = [e["subject"] for e in wl["watchlist"]]
check("high-priority cases listed, ranked by risk",
      names == ["Critical Case", "High Case"] and wl["watchlist_count"] == 2)
check("below-threshold case excluded and counted",
      "Low Case" not in names and wl["excluded_below_threshold"] == 1)
check("unscored case NEVER auto-listed — sent to manual triage with a note",
      "Unscored Case" not in names
      and len(wl["manual_triage"]) == 1
      and wl["manual_triage"][0]["subject"] == "Unscored Case"
      and "manual officer triage" in wl["manual_triage"][0]["note"])
check("threshold is the §16 HIGH boundary and is documented in the output",
      wl["threshold"]["min_risk_score"] == WATCHLIST_MIN_SCORE == 55
      and "HIGH" in wl["threshold"]["explanation"])

edge = build_watchlist([mkcase("Exactly 55", 55, "HIGH", 0, 1),
                        mkcase("Just Under", 54, "MEDIUM", 0, 1)])
check("boundary exact: 55 listed, 54 excluded",
      [e["subject"] for e in edge["watchlist"]] == ["Exactly 55"]
      and edge["excluded_below_threshold"] == 1)

check("each entry states WHY it is listed, citing score and threshold",
      all(f">= watchlist threshold {WATCHLIST_MIN_SCORE}" in e["listed_because"]
          and f"{e['risk_score']}/100" in e["listed_because"]
          for e in wl["watchlist"]))

print("=" * 72)
print("REVIEW-GATING — legal basis, human review, no automated action")
print("=" * 72)

check("every entry is PENDING OFFICER REVIEW with legal_basis_required",
      all(e["review_status"] == "PENDING OFFICER REVIEW"
          and e["legal_basis_required"] is True
          and "legal basis" in e["legal_basis_note"]
          for e in wl["watchlist"]))
check("verbatim legal-basis notice present on the list",
      wl["legal_basis_notice"] == LEGAL_BASIS_NOTICE)
check("notice says continued monitoring requires a legal basis",
      "LEGAL BASIS FOR CONTINUED MONITORING" in LEGAL_BASIS_NOTICE
      and "independent, documented legal basis" in LEGAL_BASIS_NOTICE)
check("notice says review aid, not surveillance automation",
      "review aid" in LEGAL_BASIS_NOTICE
      and "not surveillance automation" in LEGAL_BASIS_NOTICE)
check("structure declares itself review-aid-only with NO automated action",
      wl["review_aid_only"] is True
      and wl["surveillance_automation"] is False
      and wl["automated_action"].startswith("NONE"))
check("Phase 1.5 human-authorisation notice also present",
      wl["authorisation_notice"] == HUMAN_AUTHORISATION_NOTICE
      and wl["human_authorisation_required"] is True)
check("watchlist is pure data — JSON-serialisable, nothing executable",
      isinstance(json.loads(json.dumps(wl)), dict))

rendered = render_watchlist(wl)
check("rendered list opens with the review-aid banner",
      rendered.startswith("DYNAMIC WATCHLIST — REVIEW AID ONLY. "
                          "NOT SURVEILLANCE AUTOMATION."))
check("rendered list carries both notices and every listed subject",
      LEGAL_BASIS_NOTICE in rendered and HUMAN_AUTHORISATION_NOTICE in rendered
      and all(n in rendered for n in names)
      and "MANUAL TRIAGE: Unscored Case" in rendered)

print("=" * 72)
print("DYNAMIC — membership follows the evidence; changes reported")
print("=" * 72)

first = build_watchlist([mkcase("Stays High", 70, "HIGH", 1, 2),
                         mkcase("Drops Off", 65, "HIGH", 1, 2)])
second = build_watchlist([mkcase("Stays High", 70, "HIGH", 1, 2),
                          mkcase("Drops Off", 30, "LOW", 0, 1),
                          mkcase("New Arrival", 80, "CRITICAL", 2, 3)],
                         previous=first)
check("subject whose evidence no longer qualifies is REMOVED and reported",
      "Drops Off" not in [e["subject"] for e in second["watchlist"]]
      and second["changes"]["removed"] == ["Drops Off"])
check("newly qualifying subject reported as added",
      second["changes"]["added"] == ["New Arrival"])
check("still-qualifying subject reported as retained",
      second["changes"]["retained"] == ["Stays High"])
check("no previous build → no changes block (not a fabricated empty diff)",
      first["changes"] is None)
check("rendered changes visible to the reviewing officer",
      "MEMBERSHIP CHANGES" in render_watchlist(second)
      and "removed (evidence no longer qualifies): Drops Off"
      in render_watchlist(second))

print("=" * 72)
print("DETERMINISM / HONESTY / IDENTITY-BLINDNESS")
print("=" * 72)

check("deterministic — identical output on identical input",
      build_watchlist(cases) == build_watchlist(cases))
check("empty input → empty watchlist, notices still present",
      build_watchlist([])["watchlist"] == []
      and build_watchlist([])["legal_basis_notice"] == LEGAL_BASIS_NOTICE)
check("malformed entries skipped and counted",
      build_watchlist([mkcase("Valid", 60), None, "junk"])["skipped_malformed"] == 2)

a = build_watchlist([mkcase("Case One", 70, "HIGH", 1, 2)])
b = build_watchlist([mkcase("Case One", 70, "HIGH", 1, 2,
                            extra_sections={"subject_identity":
                                            {"content": "Bangladeshi national, Muslim"}})])
check("identity attributes outside the evidence never change membership",
      [e["subject"] for e in a["watchlist"]] == [e["subject"] for e in b["watchlist"]]
      and a["watchlist"][0]["listed_because"] == b["watchlist"][0]["listed_because"])

print("=" * 72)
passed, total = sum(results), len(results)
print(f"SUMMARY: {passed}/{total} checks passed")
if passed == total:
    print("ALL WATCHLIST CHECKS PASSED"); sys.exit(0)
sys.exit(1)
