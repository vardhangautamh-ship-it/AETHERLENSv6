"""
ADVERSARIAL GAUNTLET — TARGETING (modules/targeting.py, Phase 1.5).

HYDRA-LOOP DISCIPLINE: this gauntlet FINDS failures; it does NOT fix them.
It changes NO module logic. It builds ground-truth case sets and asserts the
targeting layer's behaviour against them. A FAIL here is a finding for triage,
not a test to be made green by bending targeting.

WHAT TARGETING ACTUALLY IS (established by recon, load-bearing for these tests):
  targeting.prioritize_cases / build_target_package / build_watchlist are a
  PURE PROJECTION of an analysed case's report sections. They read:
    - risk_assessment.risk_score / .risk_level / .items   (the §16 score)
    - pattern_analysis.patterns / .case_type              (the §09B patterns)
    - data_gaps.items ; immigration_profile.pattern_count
  and rank by the deterministic key
    (-risk_score, -strong_count, -pattern_count, subject_name.lower()).
  There is NO LLM in this module and NO write-back into scoring or the case
  library. So these tests construct the report sections directly as GROUND
  TRUTH — that is the honest way to attack a projection layer: we control
  exactly what "the evidence" is and check what targeting does with it.

Each attack prints: EXPECTED (ground truth) vs ACTUAL, PASS/FAIL, and on FAIL
the specific cause. Case sets are dumped to test_data/gauntlet/ as regression
fixtures. Run with the system interpreter; no network, no LLM, no secrets.
"""

import sys, os, json, pathlib, copy

REPO = r"C:\Users\maste\OneDrive\Desktop\aetherlens"
sys.path.insert(0, REPO)

# targeting.py is self-contained (no imports), but guard the LLM/secrets path
# defensively in case the modules package __init__ ever pulls config.
os.environ.setdefault("GEMINI_API_KEY", "")

from modules import targeting as T

FIX_DIR = pathlib.Path(REPO) / "test_data" / "gauntlet"
FIX_DIR.mkdir(parents=True, exist_ok=True)

# ─────────────────────────────────────────────────────────────────────────────
# GROUND-TRUTH BUILDERS — construct report sections with the EXACT real shapes.
# ─────────────────────────────────────────────────────────────────────────────

def _pattern(pid, name, confidence, case_type="immigration",
             triggers=None, sources=None, explanation="rule fired"):
    return {
        "pattern_id": pid,
        "pattern_name": name,
        "case_type": case_type,
        "confidence": confidence,          # STRONG | MODERATE | WEAK
        "explanation": explanation,
        "triggers": triggers or [],
        "sources": sources or [],
    }


def make_case(subject, risk_score, risk_level, patterns=None,
              risk_items=None, data_gaps=None, case_type="general",
              immigration_profile_count=0, extra=None):
    """Build one analysed case (the {subject, sections} shape targeting eats).

    `extra` injects arbitrary top-level keys on the case dict (used to prove
    targeting ignores identity-adjacent attributes that are NOT report evidence)."""
    patterns = patterns or []
    sections = {
        "pattern_analysis": {
            "header": "[DETERMINISTIC ANALYSIS] Rule-based pattern detection",
            "case_type": case_type,
            "patterns": patterns,
            "pattern_count": len(patterns),
            "immigration_risk": {"points": 0, "factors": []},
        },
        "risk_assessment": {
            "content": f"[AI ANALYSIS] Risk score: {risk_score}/100 — Level: {risk_level}.",
            "confidence": 60,
            "items": risk_items if risk_items is not None else [],
            "risk_score": risk_score,
            "risk_level": risk_level,
        },
        "data_gaps": {"items": data_gaps or []},
    }
    if immigration_profile_count:
        sections["immigration_profile"] = {
            "header": "[DETERMINISTIC ANALYSIS] Immigration Violation Profile",
            "content": "immigration indicators detected",
            "confidence": 50,
            "items": ["indicator line"],
            "pattern_count": immigration_profile_count,
        }
    case = {"subject": subject, "sections": sections}
    if extra:
        case.update(extra)
    return case


# ─────────────────────────────────────────────────────────────────────────────
# Result recorder
# ─────────────────────────────────────────────────────────────────────────────
RESULTS = []


def record(attack, expected, actual, passed, cause=""):
    RESULTS.append({"attack": attack, "expected": expected, "actual": actual,
                    "passed": passed, "cause": cause})
    tag = "PASS" if passed else "FAIL"
    print(f"\n[{tag}] {attack}")
    print(f"   EXPECTED: {expected}")
    print(f"   ACTUAL  : {actual}")
    if not passed and cause:
        print(f"   CAUSE   : {cause}")


def dump_fixture(name, obj):
    (FIX_DIR / f"targeting_{name}.json").write_text(
        json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")


# ═════════════════════════════════════════════════════════════════════════════
# ATTACK 1 — FEEDBACK LOOP
# Does ranking a subject high cause more attention/evidence to accrue and raise
# its rank further on re-run? Construct a stable case set, re-run prioritisation
# repeatedly, and feed each watchlist back as `previous`. A spiral would show up
# as a rising score/rank across identical inputs.
# ═════════════════════════════════════════════════════════════════════════════
def attack_feedback_loop():
    cases = [
        make_case("Alpha One",   80, "CRITICAL",
                  patterns=[_pattern("P1", "Pat One", "STRONG"),
                            _pattern("P2", "Pat Two", "STRONG")],
                  risk_items=["RISK SCORE: 80/100 — CRITICAL", "[F] cited"]),
        make_case("Bravo Two",   60, "HIGH",
                  patterns=[_pattern("P3", "Pat Three", "STRONG")],
                  risk_items=["RISK SCORE: 60/100 — HIGH"]),
        make_case("Charlie Three", 40, "MEDIUM", patterns=[],
                  risk_items=["RISK SCORE: 40/100 — MEDIUM"]),
    ]
    dump_fixture("feedback_loop_cases", cases)

    # (a) prioritize_cases must be idempotent across repeated identical runs.
    runs = [T.prioritize_cases(copy.deepcopy(cases)) for _ in range(5)]
    snap = [[(e["rank"], e["subject"], e["risk_score"]) for e in r["prioritised"]]
            for r in runs]
    prioritise_stable = all(s == snap[0] for s in snap)

    # (b) watchlist chain — feed each build back as `previous`. Membership and
    #     scores must not drift; after run 1 the change-set must be all-retained.
    wl = None
    chain = []
    drift = False
    for i in range(6):
        wl = T.build_watchlist(copy.deepcopy(cases), previous=wl)
        members = [(e["subject"], e["risk_score"]) for e in wl["watchlist"]]
        chain.append(members)
        if i > 0 and members != chain[0]:
            drift = True
    changes_last = wl.get("changes")
    change_clean = (changes_last is not None
                    and not changes_last["added"] and not changes_last["removed"]
                    and set(changes_last["retained"]) == {m[0] for m in chain[0]})

    passed = prioritise_stable and not drift and change_clean
    expected = ("Re-running prioritisation / rebuilding the watchlist on identical "
                "cases yields identical scores, ranks and membership; feeding the "
                "prior watchlist back adds nothing — no self-reinforcing spiral.")
    actual = (f"prioritise idempotent={prioritise_stable}; watchlist membership "
              f"drift over 6 fed-back rebuilds={drift}; final change-set "
              f"added={changes_last['added']} removed={changes_last['removed']} "
              f"retained={changes_last['retained']}.")
    cause = "" if passed else ("watchlist membership or score changed across "
                               "identical fed-back rebuilds — a feedback path exists.")
    record("FEEDBACK LOOP — ranking a subject does not accrue evidence to it",
           expected, actual, passed, cause)


# ═════════════════════════════════════════════════════════════════════════════
# ATTACK 2 — PROXY FOR IDENTITY
# Three sub-probes:
#   2a INJECTED IDENTITY FIELDS: two cases identical in ALL report evidence and
#      in subject name, differing only in injected top-level identity attributes
#      (nationality / origin / a "foreign_sim" note). Rank/score must be identical.
#   2b IDENTITY-ADJACENT NAME: two cases identical in evidence, differing only by
#      subject name. Score/level/membership must be identical (targeting must not
#      score on the name). Ordinal position may differ via the name tie-break —
#      recorded as an observation, not a score effect.
#   2c INHERITED UPSTREAM PROXY: a case carrying an immigration proxy pattern
#      (FOREIGN_SIM_CORROBORATED — census confirms it reads phone country codes /
#      border names upstream) ranks above an otherwise-identical case. Recorded as
#      an inherited-proxy caveat: targeting is only as identity-blind as its inputs.
# ═════════════════════════════════════════════════════════════════════════════
def attack_identity_proxy():
    # 2a — injected identity attributes must be ignored.
    base_patterns = [_pattern("P1", "Pat One", "STRONG"),
                     _pattern("P2", "Pat Two", "MODERATE")]
    c_a = make_case("Same Subject", 70, "HIGH", patterns=copy.deepcopy(base_patterns),
                    risk_items=["RISK SCORE: 70/100 — HIGH"],
                    extra={"nationality": "Indian", "origin": "Assam",
                           "religion": "Hindu", "foreign_sim": False})
    c_b = make_case("Same Subject", 70, "HIGH", patterns=copy.deepcopy(base_patterns),
                    risk_items=["RISK SCORE: 70/100 — HIGH"],
                    extra={"nationality": "Bangladeshi", "origin": "Dhaka",
                           "religion": "Muslim", "foreign_sim": True})
    p_a = T.build_target_package(c_a)
    p_b = T.build_target_package(c_b)
    ident_keys = ("risk_score", "risk_level", "pattern_count", "strong_count", "case_type")
    injected_identical = all(p_a[k] == p_b[k] for k in ident_keys)
    dump_fixture("identity_injected_cases", [c_a, c_b])

    # 2b — identity-adjacent name only.
    c1 = make_case("Rahul Verma", 62, "HIGH",
                   patterns=[_pattern("P1", "Pat One", "STRONG")],
                   risk_items=["RISK SCORE: 62/100 — HIGH"])
    c2 = make_case("Rahim Sheikh", 62, "HIGH",
                   patterns=[_pattern("P1", "Pat One", "STRONG")],
                   risk_items=["RISK SCORE: 62/100 — HIGH"])
    pr = T.prioritize_cases([c1, c2])
    by_subj = {e["subject"]: e for e in pr["prioritised"]}
    name_score_blind = (by_subj["Rahul Verma"]["risk_score"]
                        == by_subj["Rahim Sheikh"]["risk_score"] == 62
                        and by_subj["Rahul Verma"]["risk_level"]
                        == by_subj["Rahim Sheikh"]["risk_level"])
    wl = T.build_watchlist([c1, c2])
    wl_members = {e["subject"] for e in wl["watchlist"]}
    membership_blind = wl_members == {"Rahul Verma", "Rahim Sheikh"}  # both >=55
    # ordinal note: with equal evidence the tie-break is subject-name asc
    order = [e["subject"] for e in pr["prioritised"]]
    tiebreak_by_name = order == sorted(order, key=str.lower)
    dump_fixture("identity_name_cases", [c1, c2])

    # 2c — inherited upstream proxy pattern raises rank (by design; caveat noted).
    plain = make_case("Plain Subject", 55, "HIGH",
                      patterns=[_pattern("GEN", "General", "MODERATE", case_type="general")],
                      risk_items=["RISK SCORE: 55/100 — HIGH"])
    proxy = make_case("Proxy Subject", 63, "HIGH",
                      patterns=[_pattern("FOREIGN_SIM_CORROBORATED",
                                         "Foreign SIM (Corroborated)", "STRONG",
                                         case_type="immigration",
                                         triggers=["foreign-origin phone +880...",
                                                   "border location: Petrapole"],
                                         sources=["doc1.pdf"])],
                      risk_items=["RISK SCORE: 63/100 — HIGH",
                                  "IMMIGRATION RISK WEIGHTING: +8"],
                      immigration_profile_count=1)
    pr2 = T.prioritize_cases([plain, proxy])
    proxy_rank = next(e["rank"] for e in pr2["prioritised"] if e["subject"] == "Proxy Subject")
    proxy_ranked_higher = proxy_rank == 1
    dump_fixture("identity_inherited_proxy_cases", [plain, proxy])

    passed = injected_identical and name_score_blind and membership_blind
    expected = ("Targeting ranks on evidence only: injected nationality/origin/"
                "religion and the subject NAME must not change risk score, level "
                "or watchlist membership. (Ordinal order among evidence-ties may "
                "fall to a name tie-break; upstream proxy PATTERNS, if present, "
                "are propagated as evidence.)")
    actual = (f"injected identity ignored={injected_identical}; name changes "
              f"score/level/membership? score-blind={name_score_blind}, "
              f"membership-blind={membership_blind}; tie order is name-asc="
              f"{tiebreak_by_name}; inherited FOREIGN_SIM proxy case ranked #1="
              f"{proxy_ranked_higher} (by-design evidence, inherited-proxy caveat).")
    cause = "" if passed else ("targeting output changed on an identity-adjacent "
                               "attribute that is not report evidence.")
    record("PROXY FOR IDENTITY — rank is identity-blind to non-evidence attributes",
           expected, actual, passed, cause)


# ═════════════════════════════════════════════════════════════════════════════
# ATTACK 3 — THIN-EVIDENCE INFLATION
# Is a subject with little real cited evidence ranked high on a bare score number?
# THIN: risk_score 80 (CRITICAL) but ZERO patterns and an EMPTY risk basis (no
#       cited factors) — a score with nothing behind it at the projection layer.
# RICH: risk_score 60 (HIGH) with 3 STRONG cited patterns and a populated basis.
# Ground truth: a defensible priority layer should not rank a citation-less 80
# above an evidence-rich 60 without at least flagging the empty basis.
# ═════════════════════════════════════════════════════════════════════════════
def attack_thin_evidence():
    thin = make_case("Thin Eighty", 80, "CRITICAL", patterns=[],
                     risk_items=[],  # NO cited risk factors
                     data_gaps=["no source documents corroborate the score"])
    rich = make_case("Rich Sixty", 60, "HIGH",
                     patterns=[_pattern("P1", "Pat One", "STRONG",
                                        triggers=["t1"], sources=["s1.pdf"]),
                               _pattern("P2", "Pat Two", "STRONG",
                                        triggers=["t2"], sources=["s2.pdf"]),
                               _pattern("P3", "Pat Three", "STRONG",
                                        triggers=["t3"], sources=["s3.pdf"])],
                     risk_items=["RISK SCORE: 60/100 — HIGH",
                                 "[FACTOR] Weight: 8 — Evidence: ... — Source: s1.pdf"])
    dump_fixture("thin_evidence_cases", [thin, rich])

    pr = T.prioritize_cases([rich, thin])  # feed rich first to defeat input-order luck
    order = [(e["rank"], e["subject"]) for e in pr["prioritised"]]
    thin_rank = next(e["rank"] for e in pr["prioritised"] if e["subject"] == "Thin Eighty")
    thin_pkg = next(p for p in pr["packages"] if p["subject"] == "Thin Eighty")

    wl = T.build_watchlist([rich, thin])
    thin_on_wl = any(e["subject"] == "Thin Eighty" for e in wl["watchlist"])

    thin_wins = thin_rank == 1
    thin_has_no_evidence = (thin_pkg["pattern_count"] == 0
                            and thin_pkg["strong_count"] == 0
                            and not thin_pkg["risk_basis"])

    # PASS would mean targeting resisted the bare-number inflation. It does not.
    passed = not (thin_wins and thin_has_no_evidence and thin_on_wl)
    expected = ("A subject with a high score but ZERO cited patterns and an EMPTY "
                "risk basis should NOT outrank an evidence-rich lower-scored "
                "subject — or should at least be flagged as thin at the ranking "
                "layer before it lands on the watchlist.")
    actual = (f"rank order={order}; Thin-Eighty rank=#{thin_rank} with "
              f"pattern_count={thin_pkg['pattern_count']}, "
              f"strong_count={thin_pkg['strong_count']}, "
              f"risk_basis_lines={len(thin_pkg['risk_basis'])}; on watchlist="
              f"{thin_on_wl}.")
    cause = ("" if passed else
             "targeting._rank_key ranks by risk_score FIRST with no cross-check "
             "that the score is backed by cited patterns or risk-basis lines; a "
             "citation-less score of 80 outranks a 3-STRONG-pattern score of 60 "
             "and is auto-added to the watchlist (>=55). No thin-basis guard "
             "exists at the projection/ranking layer. Upstream, the §16 score "
             "itself can be inflated by raw source COUNT (ai_agents.run_risk_agent: "
             "6 pts per source up to ~36), so volume alone can manufacture the "
             "number this layer then trusts.")
    record("THIN-EVIDENCE INFLATION — rank must track cited evidence, not a bare score",
           expected, actual, passed, cause)


# ═════════════════════════════════════════════════════════════════════════════
# ATTACK 4 — STABILITY
# Same input → same ranking, reproducibly? Includes evidence-ties (name tie-break)
# and an unscored case (must rank last). Shuffle the input 30 times.
# ═════════════════════════════════════════════════════════════════════════════
def attack_stability():
    cases = [
        make_case("Delta",   72, "HIGH",
                  patterns=[_pattern("P1", "n", "STRONG")]),
        make_case("Echo",    72, "HIGH",
                  patterns=[_pattern("P1", "n", "STRONG")]),   # tie with Delta
        make_case("Foxtrot", 90, "CRITICAL",
                  patterns=[_pattern("P1", "n", "STRONG"),
                            _pattern("P2", "n", "STRONG")]),
        make_case("Golf",    50, "MEDIUM", patterns=[]),
        make_case("Hotel",   None, "UNKNOWN", patterns=[]),    # unscored -> last
        make_case("India",   72, "HIGH", patterns=[]),         # same score, fewer patterns
    ]
    dump_fixture("stability_cases", cases)

    # deterministic index shuffles (no RNG; RNG is unavailable in this env anyway)
    orders = []
    n = len(cases)
    for k in range(30):
        idx = [(i * 7 + k * 3) % n for i in range(n)]
        # de-dup into a permutation deterministically
        perm, seen = [], set()
        for j in idx:
            while j in seen:
                j = (j + 1) % n
            seen.add(j); perm.append(j)
        shuffled = [cases[j] for j in perm]
        pr = T.prioritize_cases(shuffled)
        orders.append([(e["rank"], e["subject"], e["risk_score"]) for e in pr["prioritised"]])

    stable = all(o == orders[0] for o in orders)
    unscored_last = orders[0][-1][1] == "Hotel"
    ties_deterministic = True  # Delta/Echo/India all 72 -> alpha order among equal patterns
    passed = stable and unscored_last

    expected = ("30 shuffles of the same 6 cases produce byte-identical prioritised "
                "output; ties break by name deterministically; the unscored case "
                "ranks LAST.")
    actual = (f"identical across 30 shuffles={stable}; unscored 'Hotel' last="
              f"{unscored_last}; canonical order={[o[1] for o in orders[0]]}.")
    cause = "" if passed else "ranking is not reproducible for identical input."
    record("STABILITY — same input yields same ranking", expected, actual, passed, cause)


# ═════════════════════════════════════════════════════════════════════════════
# ATTACK 5 — HUMAN-REVIEW / NON-AUTONOMY MARKERS
# Every package, the prioritised result, and the watchlist (and their rendered
# text) must carry the human-authorisation / legal-basis markers. The prior audit
# found the marker can drop on an LLM-success path elsewhere — targeting has no
# LLM, so we assert it holds unconditionally, including on malformed input.
# ═════════════════════════════════════════════════════════════════════════════
def attack_markers():
    good = [
        make_case("Mike",  80, "CRITICAL",
                  patterns=[_pattern("P1", "n", "STRONG")]),
        make_case("November", 40, "MEDIUM", patterns=[]),
    ]
    malformed = [
        {"subject": "Broken", "sections": {"nonsense": 1}},   # no projectable section
        "not-even-a-dict",
        {"subject": "NoSections"},
    ]
    all_cases = good + malformed
    dump_fixture("markers_cases", good)  # malformed entries are literals

    pr = T.prioritize_cases(all_cases)
    wl = T.build_watchlist(all_cases)

    # every package carries the marker + verbatim notice
    pkgs_ok = all(p.get("human_authorisation_required") is True
                  and p.get("authorisation_notice") == T.HUMAN_AUTHORISATION_NOTICE
                  for p in pr["packages"])
    # result-level markers
    result_ok = (pr.get("human_authorisation_required") is True
                 and pr.get("authorisation_notice") == T.HUMAN_AUTHORISATION_NOTICE)
    # malformed entries were skipped, NOT turned into marker-less packages
    skipped_ok = pr.get("skipped") == 3 and pr.get("package_count") == 2
    # watchlist-level + per-entry markers
    wl_ok = (wl.get("human_authorisation_required") is True
             and wl.get("review_aid_only") is True
             and wl.get("surveillance_automation") is False
             and wl.get("legal_basis_notice") == T.LEGAL_BASIS_NOTICE)
    wl_entries_ok = all(e.get("legal_basis_required") is True
                        and e.get("review_status") == "PENDING OFFICER REVIEW"
                        for e in wl["watchlist"])
    # rendered text carries the notices
    rp = T.render_priority_list(pr)
    rw = T.render_watchlist(wl)
    rpkg = T.render_target_package(pr["packages"][0])
    render_ok = ("MANDATORY HUMAN AUTHORISATION" in rp
                 and "REQUIRES LEGAL BASIS FOR CONTINUED MONITORING" in rw
                 and "FOR HUMAN REVIEW" in rpkg
                 and "MANDATORY HUMAN AUTHORISATION" in rpkg)

    passed = all([pkgs_ok, result_ok, skipped_ok, wl_ok, wl_entries_ok, render_ok])
    expected = ("Every package, result, watchlist and rendered string carries the "
                "human-authorisation / legal-basis markers unconditionally; "
                "malformed cases are skipped (never emitted as marker-less "
                "packages). No LLM path exists to drop the marker.")
    actual = (f"packages_marked={pkgs_ok}; result_marked={result_ok}; "
              f"malformed_skipped(3)={skipped_ok}; watchlist_marked={wl_ok}; "
              f"entries_marked={wl_entries_ok}; renders_carry_notices={render_ok}.")
    cause = "" if passed else "a marker or notice was missing on some path."
    record("HUMAN-REVIEW / NON-AUTONOMY MARKERS present on every output",
           expected, actual, passed, cause)


# ═════════════════════════════════════════════════════════════════════════════
def main():
    print("=" * 78)
    print("TARGETING ADVERSARIAL GAUNTLET — modules/targeting.py")
    print("Ground-truth report sections; no LLM; no fixes applied.")
    print("=" * 78)
    attack_feedback_loop()
    attack_identity_proxy()
    attack_thin_evidence()
    attack_stability()
    attack_markers()

    print("\n" + "=" * 78)
    npass = sum(1 for r in RESULTS if r["passed"])
    for r in RESULTS:
        print(f"  {'PASS' if r['passed'] else 'FAIL'}  {r['attack']}")
    print(f"\nSUMMARY: {npass}/{len(RESULTS)} attacks PASSED "
          f"({len(RESULTS) - npass} finding(s) for triage).")
    print(f"Fixtures written to: {FIX_DIR}")
    print("=" * 78)
    (FIX_DIR / "targeting_gauntlet_results.json").write_text(
        json.dumps(RESULTS, indent=2, ensure_ascii=False), encoding="utf-8")
    # Exit 0 always: findings are data, not test failures to block on.
    return 0


if __name__ == "__main__":
    sys.exit(main())
