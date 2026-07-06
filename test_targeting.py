"""
Phase 1.5 Step 10 — lawful targeting (risk-based prioritisation + target
packages) in modules/targeting.py.

Covers:
  * a target package built from a REAL analysed case: the §09B/§09C sections
    come from the actual pattern engine over a synthetic immigration ontology,
    and §16 from the real _build_risk_section (deterministic inline path);
  * full citation carry-through — every pattern keeps its evidence and source
    lines, the §16 basis lines are carried, data gaps are carried;
  * the MANDATORY human-authorisation notice, verbatim, on every package and
    on every prioritised list — with human_authorisation_required = True;
  * honest-empty and honest-missing behaviour — no patterns → package says so;
    no risk score → ranked last with an explicit note; malformed input → None
    (packages) or skipped-and-counted (lists), never guessed;
  * deterministic, evidence-based-only ranking: risk score desc, STRONG count,
    pattern count, then subject name — identity attributes are never inputs.

No LLM, no network: run_risk_agent is stubbed to fail so §16 exercises its
deterministic inline path. Run: PYTHONUTF8=1 python test_targeting.py
"""
import sys
from types import SimpleNamespace as NS

import networkx as nx

import modules.ai_agents as AIA
import modules.report_generator as RG
from modules.pattern_engine import analyze_ontology
from modules.targeting import (
    HUMAN_AUTHORISATION_NOTICE, build_target_package, prioritize_cases,
    render_priority_list, render_target_package,
)

results = []
def check(label, ok):
    results.append(bool(ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")


# ── synthetic ontology factories (duck-typed to the rule contract) ───────────
def onto(**kw):
    base = dict(subject_name="", subject=None, flags=[], graph=nx.Graph(),
                persons=[], phones=[], organizations=[], transactions=[],
                properties=[], comm_channels=[], legal_proceedings=[],
                deletion_events=[], timeline_events=[], locations=[])
    base.update(kw)
    return NS(**base)

def phone(number, type="domestic"):
    return NS(number=number, type=type, country="", source="cdr.csv")
def txn(amount, counterparty):
    return NS(date="2024-02-01", direction="out", amount=amount, cross_border=True,
              counterparty=counterparty, structured=False, source="remit.csv")
def loc(name):
    return NS(name=name, kind="observed", source="movement.csv")
def legal(kind="inquiry"):
    return NS(agency="FRRO Guwahati", status="active", date="2024-03-01",
              case_ref="IMM-1", kind=kind, source="frro_notice.pdf")

print("=" * 72)
print("REAL-SHAPE PACKAGE — §09B/§09C from the actual engine, §16 inline path")
print("=" * 72)

rich = onto(
    subject_name="Subject A",
    phones=[phone("+8801700000001", "international"), phone("+9779800000001", "international")]
           + [phone(f"+9196000000{i:02d}") for i in range(5)],
    transactions=[txn(35000, "Corridor Agent") for _ in range(6)],
    flags=["Forged passport recovered during search",
           "Tampered work visa seized from premises",
           "Overstay beyond visa validity confirmed by FRRO audit"],
    locations=[loc("Petrapole land port"), loc("Hili border checkpost")],
    timeline_events=[NS(date="2024-03-15", significance="HIGH", source="movement.csv",
                        description="Road transit staging near border crossing")],
    legal_proceedings=[legal()])
res = analyze_ontology(rich)
pa_section = RG._build_pattern_analysis_section(res)
imm_section = RG._build_immigration_profile_section(pa_section)
check("engine detects immigration case on the synthetic ontology",
      res["case_type_detected"] == "immigration" and pa_section["pattern_count"] >= 3)

# §16 via the real builder, on its deterministic inline path (agent stubbed out).
_orig_risk_agent = AIA.run_risk_agent
def _offline(*a, **k):
    raise RuntimeError("offline test — no agent")
AIA.run_risk_agent = _offline
try:
    risk_section = RG._build_risk_section(
        {"confirmed_name": "Subject A", "phones_found": ["+919600000001"]},
        None, None, pattern_analysis=pa_section)
finally:
    AIA.run_risk_agent = _orig_risk_agent
check("_build_risk_section exposes structured risk_score / risk_level",
      isinstance(risk_section.get("risk_score"), int)
      and bool(risk_section.get("risk_level")))
check("immigration weighting visible in the structured score",
      risk_section["risk_score"] >= (pa_section.get("immigration_risk") or {}).get("points", 0))

case = {"subject": "Subject A", "sections": {
    "pattern_analysis": pa_section,
    "immigration_profile": imm_section,
    "risk_assessment": risk_section,
    "data_gaps": {"items": ["employer records: not obtained"]}}}
pkg = build_target_package(case)
check("package builds from a real analysed case", pkg is not None)
check("subject carried", pkg and pkg["subject"] == "Subject A")
check("risk score and level carried structurally",
      pkg and pkg["risk_score"] == risk_section["risk_score"]
      and pkg["risk_level"] == risk_section["risk_level"])
check("all fired patterns carried",
      pkg and pkg["pattern_count"] == pa_section["pattern_count"]
      and pkg["strong_count"] >= 1)
check("every carried pattern retains its source citations",
      pkg and all(p.get("sources") for p in pkg["patterns"]))
check("package lines include per-pattern evidence citations",
      pkg and sum(1 for l in pkg["items"] if l.strip().startswith("evidence:")) >= 3
      and any("sources:" in l for l in pkg["items"]))
check("§16 basis lines carried into the package",
      pkg and pkg["risk_basis"] and pkg["risk_basis"] == risk_section["items"][:20]
      and any("RISK BASIS" in l for l in pkg["items"]))
check("immigration profile presence recorded",
      pkg and pkg["immigration_profile_present"] is True
      and any("IMMIGRATION VIOLATION PROFILE" in l for l in pkg["items"]))
check("data gaps carried (what the package does NOT establish)",
      pkg and pkg["data_gaps"] == ["employer records: not obtained"]
      and any("DATA GAPS" in l for l in pkg["items"]))
check("human_authorisation_required is True",
      pkg and pkg["human_authorisation_required"] is True)
check("authorisation notice present verbatim on the package",
      pkg and pkg["authorisation_notice"] == HUMAN_AUTHORISATION_NOTICE
      and HUMAN_AUTHORISATION_NOTICE in pkg["items"])
check("notice requires human authorisation before ANY action",
      "authorises NO" in HUMAN_AUTHORISATION_NOTICE
      and "human" in HUMAN_AUTHORISATION_NOTICE.lower()
      and "written authorisation" in HUMAN_AUTHORISATION_NOTICE)
check("notice states no identity attributes were used",
      "nationality, ethnicity, or religion" in HUMAN_AUTHORISATION_NOTICE)
rendered = render_target_package(pkg)
check("rendered package carries subject + notice + review framing",
      "Subject A" in rendered and HUMAN_AUTHORISATION_NOTICE in rendered
      and "FOR HUMAN REVIEW" in rendered)

print("=" * 72)
print("HONEST-EMPTY / HONEST-MISSING / MALFORMED")
print("=" * 72)

empty_case = {"subject": "Quiet Case", "sections": {
    "pattern_analysis": {"case_type": "undetermined", "patterns": [], "pattern_count": 0},
    "risk_assessment": {"risk_score": 12, "risk_level": "LOW",
                        "items": ["RISK SCORE: 12/100 — LOW"]},
    "data_gaps": {"items": []}}}
epkg = build_target_package(empty_case)
check("zero patterns → package still builds and says none fired",
      epkg is not None and epkg["pattern_count"] == 0
      and any("none fired" in l for l in epkg["items"]))

unscored = build_target_package({"subject": "Unscored Case", "sections": {
    "pattern_analysis": {"case_type": "financial", "pattern_count": 2, "patterns": [
        {"pattern_id": "X", "pattern_name": "X", "case_type": "financial",
         "confidence": "STRONG", "explanation": "e", "triggers": ["t"], "sources": ["s"]},
        {"pattern_id": "Y", "pattern_name": "Y", "case_type": "financial",
         "confidence": "MODERATE", "explanation": "e", "triggers": ["t"], "sources": ["s"]}]},
    "risk_assessment": {"risk_score": None, "risk_level": "UNKNOWN",
                        "items": ["Risk assessment data not available"]}}})
check("missing risk score reported as missing, never fabricated",
      unscored is not None and unscored["risk_score"] is None
      and any("no risk score available" in l for l in unscored["items"]))

check("malformed input → None (defensive)",
      build_target_package(None) is None and build_target_package("x") is None
      and build_target_package({}) is None
      and build_target_package({"subject": "S", "sections": {}}) is None)

bare = build_target_package({"subject_name": "Bare Sections",
                             "pattern_analysis": {"case_type": "cyber", "patterns": [],
                                                  "pattern_count": 0},
                             "risk_assessment": {"risk_score": 40, "risk_level": "MEDIUM",
                                                 "items": []}})
check("bare sections dict (no wrapper) accepted, subject_name honoured",
      bare is not None and bare["subject"] == "Bare Sections" and bare["risk_score"] == 40)

print("=" * 72)
print("PRIORITISATION — deterministic, evidence-based ranking")
print("=" * 72)

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

three = [mkcase("Mid Case", 55, "HIGH", 1, 2),
         mkcase("Top Case", 80, "CRITICAL", 3, 4),
         mkcase("Low Case", 20, "LOW", 0, 1)]
out = prioritize_cases(three)
order = [e["subject"] for e in out["prioritised"]]
check("ranked by risk score descending",
      order == ["Top Case", "Mid Case", "Low Case"])
check("rank numbers are 1..n",
      [e["rank"] for e in out["prioritised"]] == [1, 2, 3])
check("each entry's basis cites its score and pattern counts",
      all(f"risk {e['risk_score']}/100" in e["basis"]
          and "pattern(s) fired" in e["basis"] for e in out["prioritised"]))

tie = prioritize_cases([mkcase("Alpha", 60, "HIGH", 1, 3),
                        mkcase("Bravo", 60, "HIGH", 2, 3)])
check("score tie broken by STRONG pattern count",
      [e["subject"] for e in tie["prioritised"]] == ["Bravo", "Alpha"])

tie2 = prioritize_cases([mkcase("Alpha", 60, "HIGH", 1, 2),
                         mkcase("Bravo", 60, "HIGH", 1, 4)])
check("score+STRONG tie broken by total pattern count",
      [e["subject"] for e in tie2["prioritised"]] == ["Bravo", "Alpha"])

tie3 = prioritize_cases([mkcase("Bravo", 60, "HIGH", 1, 2),
                         mkcase("Alpha", 60, "HIGH", 1, 2)])
check("full tie broken by subject name (stable, deterministic)",
      [e["subject"] for e in tie3["prioritised"]] == ["Alpha", "Bravo"])

mixed = prioritize_cases([mkcase("No Score Many Patterns", None, "UNKNOWN", 3, 5),
                          mkcase("Scored Low", 10, "LOW", 0, 0)])
check("unscored case ranks LAST even with many patterns, and says why",
      [e["subject"] for e in mixed["prioritised"]] == ["Scored Low", "No Score Many Patterns"]
      and "no risk score available" in mixed["prioritised"][1]["basis"])

runs = [prioritize_cases(three) for _ in range(2)]
check("deterministic — identical output on identical input",
      runs[0] == runs[1])

nothing = prioritize_cases([])
check("empty input → empty list, notice still present",
      nothing["prioritised"] == [] and nothing["package_count"] == 0
      and nothing["authorisation_notice"] == HUMAN_AUTHORISATION_NOTICE
      and nothing["human_authorisation_required"] is True)

skipped = prioritize_cases([mkcase("Valid", 50, "MEDIUM", 0, 1), None, "junk", {}])
check("malformed entries skipped and counted — never guessed",
      skipped["package_count"] == 1 and skipped["skipped"] == 3)

check("every package in the ranked result carries the verbatim notice",
      all(p["authorisation_notice"] == HUMAN_AUTHORISATION_NOTICE
          and p["human_authorisation_required"] is True
          for p in out["packages"]))

# Identity-blindness: an identity attribute anywhere OUTSIDE the evidence
# inputs must not move the ranking — the rank key reads only score + patterns.
a = [mkcase("Case One", 50, "MEDIUM", 1, 2), mkcase("Case Two", 70, "HIGH", 1, 2)]
b = [mkcase("Case One", 50, "MEDIUM", 1, 2,
            extra_sections={"subject_identity": {"content": "Bangladeshi national, Muslim"}}),
     mkcase("Case Two", 70, "HIGH", 1, 2)]
check("identity attributes in other sections never change the ranking",
      [e["subject"] for e in prioritize_cases(a)["prioritised"]]
      == [e["subject"] for e in prioritize_cases(b)["prioritised"]])

txt = render_priority_list(out)
check("rendered priority list shows subjects in rank order with the notice",
      HUMAN_AUTHORISATION_NOTICE in txt
      and txt.find("Top Case") < txt.find("Mid Case") < txt.find("Low Case"))
check("rendered list flags skipped entries honestly",
      "malformed case(s) skipped" in render_priority_list(skipped))

print("=" * 72)
passed, total = sum(results), len(results)
print(f"SUMMARY: {passed}/{total} checks passed")
if passed == total:
    print("ALL TARGETING CHECKS PASSED"); sys.exit(0)
sys.exit(1)
