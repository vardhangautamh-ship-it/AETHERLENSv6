"""
ADVERSARIAL GAUNTLET — PREDICTIVE (modules/predictive.py, modules/advanced_intel.py).
Phase 4. This is the highest-stakes surface: prediction is where a defensible
tool can quietly become a pre-crime tool. Attacked hardest.

HYDRA-LOOP DISCIPLINE: this gauntlet FINDS failures; it does NOT fix them. It
changes NO module logic. A FAIL is a finding for triage, not a test to be made
green by bending the modules.

WHAT THESE MODULES ARE (established by recon, load-bearing for the tests):
  * predictive.predict_from_ontology projects ONE median interval ahead from an
    ALREADY-DEMONSTRATED, dated, typed transaction series (>=3 dated occurrences).
    It imports no LLM. It reads only typed transaction fields.
  * advanced_intel.suggest_network_dismantling ranks structurally-central nodes/
    links in a cited link graph (betweenness / articulation points / bridges).
    It imports no LLM. It reads only the cited links passed in.
  * app.py wiring (5581, 5593) calls each function and passes the result DIRECTLY
    to the pure render_* function. No LLM sees or alters either structured dict.

The gauntlet feeds GROUND-TRUTH ontologies / link graphs it fully controls and
checks labelling, grounding, identity-blindness, determinism, autonomy markers,
and honest degradation. Case sets are dumped to test_data/gauntlet/ as fixtures.
"""

import sys, os, json, pathlib, copy
from datetime import date

REPO = r"C:\Users\maste\OneDrive\Desktop\aetherlens"
sys.path.insert(0, REPO)
os.environ.setdefault("GEMINI_API_KEY", "")

from modules import predictive as P
from modules import advanced_intel as A

FIX_DIR = pathlib.Path(REPO) / "test_data" / "gauntlet"
FIX_DIR.mkdir(parents=True, exist_ok=True)

RESULTS = []


def record(attack, expected, actual, passed, cause="", observation=""):
    RESULTS.append({"attack": attack, "expected": expected, "actual": actual,
                    "passed": passed, "cause": cause, "observation": observation})
    tag = "PASS" if passed else "FAIL"
    print(f"\n[{tag}] {attack}")
    print(f"   EXPECTED: {expected}")
    print(f"   ACTUAL  : {actual}")
    if not passed and cause:
        print(f"   CAUSE   : {cause}")
    if observation:
        print(f"   OBSERVE : {observation}")


def dump(name, obj):
    (FIX_DIR / f"predictive_{name}.json").write_text(
        json.dumps(obj, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


# ─── ground-truth builders ───────────────────────────────────────────────────
def txn(cp, date_str, amount="10000", direction="outbound", cross_border=False,
        source="ledger.csv"):
    return {"counterparty": cp, "date": date_str, "amount": amount,
            "direction": direction, "cross_border": cross_border, "source": source}


def onto(transactions, **extra):
    o = {"transactions": transactions}
    o.update(extra)
    return o


def link(a, b, ltype="shared_phone", value="+91-98111-22334",
         raw_a="phone +91-98111-22334", raw_b="phone +91-98111-22334",
         src_a="a.pdf", src_b="b.pdf", **extra):
    d = {"type": ltype, "value": value, "subjects": [a, b],
         "citations": {a: [{"raw": raw_a, "source": src_a}],
                       b: [{"raw": raw_b, "source": src_b}]}}
    d.update(extra)
    return d


BANNED_FINDING_TAGS = ("[VERIFIED DATA]", "[DETERMINISTIC ANALYSIS]",
                       "[VERIFIED]", "[CONFIRMED]", "[DETERMINATION]")


def _all_text(result, rendered):
    return json.dumps(result, default=str, ensure_ascii=False) + "\n" + (rendered or "")


# ═════════════════════════════════════════════════════════════════════════════
# P1 — SPECULATION-AS-FINDING
# Every predictive output MUST be labelled speculative/weak/for-consideration and
# MUST NOT carry [VERIFIED DATA] / [DETERMINISTIC ANALYSIS] weight.
# ═════════════════════════════════════════════════════════════════════════════
def attack_speculation_as_finding():
    o = onto([txn("Rahim Traders", "2024-01-01"),
              txn("Rahim Traders", "2024-01-15"),
              txn("Rahim Traders", "2024-01-29"),
              txn("Rahim Traders", "2024-02-12")])
    res = P.predict_from_ontology(o)
    rendered = P.render_predictions(res)
    dump("speculation_case", o)

    preds = res["predictions"]
    labelling_ok = bool(preds) and all(
        p.get("speculative") is True and p.get("determinative") is False
        and str(p.get("confidence")).upper() == "WEAK"
        and "SPECULATIVE" in p.get("suggestion", "").upper()
        for p in preds)
    result_markers = (res.get("autonomous") is False
                      and res.get("human_review_required") is True
                      and res.get("determinative") is False
                      and res.get("prediction_notice") == P.PREDICTION_NOTICE)
    blob = _all_text(res, rendered)
    no_finding_weight = not any(tag in blob for tag in BANNED_FINDING_TAGS)
    banner_ok = "SPECULATIVE" in rendered and "FOR HUMAN REVIEW" in rendered

    passed = labelling_ok and result_markers and no_finding_weight and banner_ok
    expected = ("Every projection carries speculative=True, determinative=False, "
                "confidence=WEAK and a SPECULATIVE suggestion; the result carries "
                "autonomous=False + human_review_required + PREDICTION_NOTICE; NO "
                "output bears a [VERIFIED DATA]/[DETERMINISTIC ANALYSIS] tag.")
    actual = (f"per-pred labelling={labelling_ok}; result markers={result_markers}; "
              f"no finding-weight tag={no_finding_weight}; render banner={banner_ok}; "
              f"predictions={len(preds)}.")
    cause = "" if passed else "a predictive output lacked speculative labelling or bore finding-weight."
    record("SPECULATION-AS-FINDING — predictions labelled speculative, never finding-weight",
           expected, actual, passed, cause)


# ═════════════════════════════════════════════════════════════════════════════
# P2 — FABRICATED FORWARD-INFERENCE
# It must invent no future event/association/escalation beyond extrapolating the
# observed series by exactly one median interval, and every citation must trace
# to a real input occurrence.
# ═════════════════════════════════════════════════════════════════════════════
def attack_fabricated_inference():
    dates = ["2024-01-01", "2024-01-15", "2024-01-29"]
    o = onto([txn("Meridian Exports", d) for d in dates]
             # a lone extra counterparty (1 occurrence) must NOT become a prediction
             + [txn("Ghost Counterparty", "2024-03-01")])
    res = P.predict_from_ontology(o)
    dump("fabrication_case", o)

    preds = res["predictions"]
    one_series = len(preds) == 1
    p0 = preds[0] if preds else {}

    # (a) exactly one median interval ahead — projected == last + median
    proj_ok = False
    if p0:
        med = p0["median_interval_days"]
        last = date.fromisoformat(p0["last_date"])
        expect_proj = date.fromordinal(last.toordinal() + med).isoformat()
        proj_ok = p0["projected_next_date"] == expect_proj == "2024-02-12"

    # (b) citations trace to real input dates only; no invented occurrence
    input_dates = set(dates)
    cites_real = bool(p0) and all(c["date"] in input_dates for c in p0["citations"]) \
        and len(p0["citations"]) == 3

    # (c) label references an existing demonstrated counterparty, not an invented one
    label_grounded = bool(p0) and "meridian exports" in p0["label"].lower()

    # (d) the lone Ghost Counterparty (1 occurrence) is NOT predicted; it is
    #     recorded in not_built (refusal stated, not hidden), inventing nothing.
    ghost_not_predicted = all("ghost counterparty" not in p["label"].lower() for p in preds)
    ghost_in_not_built = any("ghost counterparty" in nb.lower() for nb in res["not_built"])

    # (e) no escalation/certainty language; hedged and non-determinative
    sug = p0.get("suggestion", "")
    hedged = ("may recur" in sug and "IF it continues" in sug
              and "not a determination that it will occur" in sug)
    no_escalation = not any(w in sug.lower() for w in
                            ("escalat", "will escalate", "certain to", "definitely",
                             "guilty", "guilt"))

    # (f) empty-evidence subject invents nothing
    empty_res = P.predict_from_ontology(onto([]))
    empty_ok = empty_res["prediction_count"] == 0 and not empty_res["predictions"]

    passed = all([one_series, proj_ok, cites_real, label_grounded,
                  ghost_not_predicted, ghost_in_not_built, hedged, no_escalation, empty_ok])
    expected = ("Projects exactly one median interval from the observed series "
                "(2024-02-12); every citation is a real input occurrence; the lone "
                "single-occurrence counterparty is refused (not_built), not invented; "
                "no escalation/certainty language; empty evidence invents nothing.")
    actual = (f"one_series={one_series}; projected={p0.get('projected_next_date')} "
              f"(expected 2024-02-12) ok={proj_ok}; citations_all_real={cites_real}; "
              f"label_grounded={label_grounded}; ghost_refused={ghost_not_predicted and ghost_in_not_built}; "
              f"hedged={hedged}; no_escalation={no_escalation}; empty_invents_nothing={empty_ok}.")
    cause = "" if passed else "a future event, association, or escalation was fabricated beyond the observed series."
    record("FABRICATED FORWARD-INFERENCE — no invented future event/association/escalation",
           expected, actual, passed, cause)


# ═════════════════════════════════════════════════════════════════════════════
# P3 — IDENTITY-BASED PREDICTION
# Identity-matched subjects must yield identical predictions; identity alone must
# never produce a prediction.
# ═════════════════════════════════════════════════════════════════════════════
def attack_identity_prediction():
    base = [txn("Trade House", "2024-01-01"),
            txn("Trade House", "2024-02-01"),
            txn("Trade House", "2024-03-01")]
    o_plain = onto(copy.deepcopy(base))
    o_identity = onto(copy.deepcopy(base),
                      subject_nationality="Bangladeshi", religion="Muslim",
                      ethnicity="Bengali", origin="Dhaka",
                      flags=["foreign national", "border district resident"],
                      names=["Abdul Rahman"])
    r_plain = P.predict_from_ontology(o_plain)
    r_identity = P.predict_from_ontology(o_identity)
    dump("identity_matched_cases", [o_plain, o_identity])

    identical = r_plain == r_identity

    # identity alone (no groundable dated series) must not predict
    o_identity_only = onto([txn("Foreign Corp", "2024-01-01", cross_border=True)],
                           subject_nationality="Bangladeshi", religion="Muslim")
    r_id_only = P.predict_from_ontology(o_identity_only)
    identity_alone_silent = r_id_only["prediction_count"] == 0

    passed = identical and identity_alone_silent
    expected = ("Two subjects with identical transaction evidence but different "
                "nationality/religion/ethnicity/origin yield IDENTICAL predictions; "
                "a subject whose only 'signal' is identity + one foreign txn yields "
                "NO prediction.")
    actual = (f"identity-matched predictions identical={identical}; "
              f"identity-alone produced {r_id_only['prediction_count']} prediction(s) "
              f"(expected 0) → silent={identity_alone_silent}.")
    cause = "" if passed else "prediction changed with an identity attribute, or arose from identity alone."
    record("IDENTITY-BASED PREDICTION — identical evidence, identical prediction",
           expected, actual, passed, cause)


# ═════════════════════════════════════════════════════════════════════════════
# P4 — DETERMINISTIC-FIRST
# The predictive logic must be deterministic; any LLM must be strictly terminal
# narrative over a deterministically-derived prediction (never alter structured state).
# ═════════════════════════════════════════════════════════════════════════════
def attack_deterministic_first():
    base = [txn("Cadence Co", "2024-01-01"),
            txn("Cadence Co", "2024-01-11"),
            txn("Cadence Co", "2024-01-21")]
    r1 = P.predict_from_ontology(onto(copy.deepcopy(base)))
    r2 = P.predict_from_ontology(onto(copy.deepcopy(base)))
    deterministic = r1 == r2

    # no LLM inside either Phase-4 module (source-level proof)
    pred_src = pathlib.Path(REPO, "modules", "predictive.py").read_text(encoding="utf-8").lower()
    adv_src = pathlib.Path(REPO, "modules", "advanced_intel.py").read_text(encoding="utf-8").lower()
    llm_tokens = ("gemini", "bedrock", "_call_gemini", "_call_bedrock", "openai", "anthropic")
    pred_llm_free = not any(tok in pred_src for tok in llm_tokens)
    adv_llm_free = not any(tok in adv_src for tok in llm_tokens)

    passed = deterministic and pred_llm_free and adv_llm_free
    expected = ("Same input → identical structured prediction; neither Phase-4 "
                "module imports or calls an LLM. (Recon: app.py:5581/5593 pass the "
                "structured result straight to the pure render_* fn — the LLM never "
                "sees or alters it; narration is strictly terminal.)")
    actual = (f"predict determinism={deterministic}; predictive.py LLM-free="
              f"{pred_llm_free}; advanced_intel.py LLM-free={adv_llm_free}.")
    cause = "" if passed else "predictive logic non-deterministic or an LLM is embedded in the module."
    record("DETERMINISTIC-FIRST — prediction is deterministic; LLM is terminal-only",
           expected, actual, passed, cause,
           observation=("Wiring verified by recon, not re-executed here: the app "
                        "renders the pure dict; no narration path mutates it."))


# ═════════════════════════════════════════════════════════════════════════════
# A1 — NETWORK-DISMANTLING autonomy + no-fabrication (advanced_intel)
# Suggestions must stay recommendations for a HUMAN (autonomous=False,
# human_review_required=True), grounded in the actual graph, never directives,
# never fabricating nodes/edges.
# ═════════════════════════════════════════════════════════════════════════════
def attack_network_dismantling():
    # chain A—B—C: B is a cut vertex; both edges are bridges.
    net = [link("Adil Khan", "Bilal Rana", value="+91-90000-00001"),
           link("Bilal Rana", "Chirag Sen", value="+91-90000-00002")]
    res = A.suggest_network_dismantling(net)
    rendered = A.render_dismantling_suggestions(res)
    dump("dismantling_chain", net)

    markers = (res.get("autonomous") is False
               and res.get("human_review_required") is True
               and res.get("determination_of_guilt") is False
               and res.get("dismantling_notice") == A.DISMANTLING_NOTICE)

    # every node suggestion is hedged as consideration, not a directive
    node_sugs = [n["suggestion"] for n in res["central_nodes"]]
    link_sugs = [l["suggestion"] for l in res["central_links"]]
    all_sugs = node_sugs + link_sugs
    hedged = bool(all_sugs) and all(
        "FOR OFFICER CONSIDERATION" in s and "not a directive" in s
        and "not a finding of guilt" in s for s in all_sugs)
    # no bare operational directive against a person
    banned = ("arrest", "detain", "raid", "seize", "apprehend", "must arrest",
              "take down", "eliminate")
    no_directive = not any(b in (rendered or "").lower() for b in banned)

    # no fabricated nodes: graph nodes ⊆ input subjects
    input_subjects = {"Adil Khan", "Bilal Rana", "Chirag Sen"}
    graph_nodes = set(sum(res["network"]["components"], []))
    no_fabrication = graph_nodes <= input_subjects and graph_nodes == input_subjects

    # structural truth: B is the articulation point, ranked first
    b_first = res["central_nodes"][0]["subject"] == "Bilal Rana" \
        and res["central_nodes"][0]["is_articulation_point"] is True

    # single-subject link and empty network invent nothing
    solo = A.suggest_network_dismantling([link("Solo", "Solo")]  # same subject twice
                                         if False else [{"subjects": ["Solo"], "citations": {}}])
    solo_ok = solo["network"]["node_count"] == 0 and solo["central_nodes"] == []
    empty = A.suggest_network_dismantling([])
    empty_ok = (empty["network"]["node_count"] == 0 and empty["central_nodes"] == []
                and empty.get("dismantling_notice") == A.DISMANTLING_NOTICE)

    passed = all([markers, hedged, no_directive, no_fabrication, b_first, solo_ok, empty_ok])
    expected = ("autonomous=False + human_review_required=True + guilt=False + notice "
                "on every result; every suggestion hedged 'for officer consideration / "
                "not a directive / not a finding of guilt'; nodes ⊆ input subjects "
                "(no fabrication); cut-vertex ranked first; solo/empty invent nothing.")
    actual = (f"markers={markers}; hedged={hedged}; no_operational_directive={no_directive}; "
              f"no_fabricated_nodes={no_fabrication}; cut_vertex_first={b_first}; "
              f"solo_invents_nothing={solo_ok}; empty_ok={empty_ok}.")
    cause = "" if passed else "a suggestion read as a directive, dropped a marker, or fabricated a node."
    record("NETWORK-DISMANTLING — suggestions stay human-review recommendations",
           expected, actual, passed, cause,
           observation=("FRAMING NOTE for triage: the module/notice/header use the "
                        "word 'DISMANTLING' and node rationales say 'removal would "
                        "fragment its cluster'. Every item is hedged and all autonomy "
                        "markers hold, but the action-oriented vocabulary ('dismantling', "
                        "'removal', '***') reads more directive-adjacent than the rest "
                        "of the system — a skeptical evaluator will notice it."))


# ═════════════════════════════════════════════════════════════════════════════
# A2 — IDENTITY-BLINDNESS of advanced_intel ranking
# Extra identity keys on links must not change the structural ranking.
# ═════════════════════════════════════════════════════════════════════════════
def attack_dismantling_identity():
    plain = [link("Node One", "Node Two", value="+91-1"),
             link("Node Two", "Node Three", value="+91-2")]
    tagged = [link("Node One", "Node Two", value="+91-1",
                   nationality="Bangladeshi", religion="Muslim"),
              link("Node Two", "Node Three", value="+91-2",
                   nationality="Indian", religion="Hindu")]
    r_plain = A.suggest_network_dismantling(plain)
    r_tagged = A.suggest_network_dismantling(tagged)
    dump("dismantling_identity_cases", {"plain": plain, "tagged": tagged})

    identical = r_plain == r_tagged
    passed = identical
    expected = ("Identity attributes attached to links are ignored: the structural "
                "ranking (degrees, articulation points, betweenness, order) is "
                "identical with or without them.")
    actual = f"ranking identical with identity keys added={identical}."
    cause = "" if passed else "an identity attribute on a link changed the structural output."
    record("IDENTITY-BLINDNESS (advanced_intel) — ranking ignores identity keys",
           expected, actual, passed, cause)


# ═════════════════════════════════════════════════════════════════════════════
# P5 — ESCALATION HONESTY
# When evidence is thin, it must SAY SO (not_built) and never emit a confident
# prediction. Confidence must never exceed WEAK; degenerate cadence degrades.
# ═════════════════════════════════════════════════════════════════════════════
def attack_escalation_honesty():
    # (a) only 2 dated occurrences → below threshold → not built, stated
    thin = P.predict_from_ontology(onto([txn("X Co", "2024-01-01"),
                                         txn("X Co", "2024-02-01")]))
    thin_ok = thin["prediction_count"] == 0 and any(
        "need >= 3" in nb or "need >=" in nb for nb in thin["not_built"])

    # (b) 3 occurrences all on the SAME date → zero interval → not groundable, stated
    zero_iv = P.predict_from_ontology(onto([txn("Y Co", "2024-01-01"),
                                            txn("Y Co", "2024-01-01"),
                                            txn("Y Co", "2024-01-01")]))
    zero_ok = zero_iv["prediction_count"] == 0 and any(
        "no positive interval" in nb for nb in zero_iv["not_built"])

    # (c) garbage/undated occurrences are counted undated, never fabricated into a series
    garbage = P.predict_from_ontology(onto([txn("Z Co", "FIR 2023"),
                                            txn("Z Co", "CR/0614"),
                                            txn("Z Co", "not-a-date")]))
    garbage_ok = garbage["prediction_count"] == 0

    # (d) confidence NEVER exceeds WEAK on any groundable case (7 clean occurrences)
    strong_series = P.predict_from_ontology(onto([txn("V Co", f"2024-{m:02d}-01")
                                                  for m in range(1, 8)]))
    never_strong = all(str(p.get("confidence")).upper() == "WEAK"
                       for p in strong_series["predictions"])

    # (e) the irregular-cadence guard DOES work when it is reachable — a 4-occurrence
    #     series with intervals [2, 2, 148] trips max > 3*median and gets the caveat.
    reachable = P.predict_from_ontology(onto([txn("U Co", "2024-01-01"),
                                              txn("U Co", "2024-01-03"),
                                              txn("U Co", "2024-01-05"),
                                              txn("U Co", "2024-06-01")]))
    r_pred = reachable["predictions"][0] if reachable["predictions"] else {}
    guard_works = (bool(r_pred) and r_pred.get("irregular_cadence") is True
                   and "rough extrapolation" in r_pred.get("basis", ""))

    dump("escalation_honesty_cases", {
        "thin": thin["not_built"], "zero_iv": zero_iv["not_built"],
        "garbage": garbage["not_built"],
        "seven_occurrence_conf": [p.get("confidence") for p in strong_series["predictions"]],
        "reachable_irregular_flag": r_pred.get("irregular_cadence")})

    passed = all([thin_ok, zero_ok, garbage_ok, never_strong, guard_works])
    expected = ("Thin (<3), zero-interval, and undated/garbage series produce NO "
                "prediction and state the reason in not_built; confidence never "
                "exceeds WEAK; and the irregular-cadence caveat DOES fire on a "
                "4-occurrence dispersed series (intervals [2,2,148]).")
    actual = (f"thin_refused={thin_ok}; zero_interval_refused={zero_ok}; "
              f"garbage_refused={garbage_ok}; confidence_never_above_WEAK={never_strong}; "
              f"irregular_guard_fires_at_4+_occurrences={guard_works}.")
    cause = "" if passed else "a thin/degenerate series produced a confident prediction instead of degrading."
    record("ESCALATION HONESTY — thin evidence degrades; confidence capped at WEAK",
           expected, actual, passed, cause)


def attack_dispersed_cadence_blind_spot():
    """REGRESSION probe (was a finding, now fixed): the irregular-cadence caveat
    must be REACHABLE for the minimum groundable series (3 occurrences → 2
    intervals). The guard is now max(interval) > 3*min(interval), so a dispersed
    3-point series like [2, 150] days (75:1) IS flagged and carries the 'rough
    extrapolation' caveat — while a genuinely regular 3-point series is NOT
    flagged (no over-correction). Top-level WEAK/speculative/determinative labels
    are unchanged; only the per-item noisy-cadence signal was repaired."""
    dispersed = P.predict_from_ontology(onto([txn("Q Co", "2024-01-01"),
                                              txn("Q Co", "2024-01-03"),
                                              txn("Q Co", "2024-06-01")]))
    d0 = dispersed["predictions"][0] if dispersed["predictions"] else {}
    dump("dispersed_cadence_case", {"prediction": d0})
    d_flagged = bool(d0) and d0.get("irregular_cadence") is True
    d_caveat = bool(d0) and "rough extrapolation" in d0.get("basis", "")
    d_labels_honest = (bool(d0) and str(d0.get("confidence")).upper() == "WEAK"
                       and d0.get("speculative") is True
                       and d0.get("determinative") is False)

    # No over-correction: a genuinely regular 3-point series ([14, 14]) stays clean.
    regular = P.predict_from_ontology(onto([txn("R Co", "2024-01-01"),
                                            txn("R Co", "2024-01-15"),
                                            txn("R Co", "2024-01-29")]))
    r0 = regular["predictions"][0] if regular["predictions"] else {}
    r_clean = bool(r0) and r0.get("irregular_cadence") is False

    passed = d_flagged and d_caveat and d_labels_honest and r_clean
    expected = ("A dispersed 3-occurrence series [2,150] fires irregular_cadence "
                "with the 'rough extrapolation' caveat; a regular 3-occurrence "
                "series [14,14] does NOT; top-level WEAK/speculative labels "
                "unchanged (over-precision repaired, certainty labels untouched).")
    actual = (f"dispersed: flagged={d_flagged}, caveat={d_caveat}, "
              f"labels_honest={d_labels_honest}; regular: clean={r_clean}.")
    cause = "" if passed else ("irregular-cadence guard did not behave: dispersed "
                               "not flagged, regular falsely flagged, or a top-level "
                               "label changed.")
    record("ESCALATION HONESTY — dispersed 3-occurrence cadence is flagged irregular; regular is not",
           expected, actual, passed, cause)


def main():
    print("=" * 78)
    print("PREDICTIVE ADVERSARIAL GAUNTLET — predictive.py + advanced_intel.py")
    print("Ground-truth ontologies / link graphs; no LLM; no fixes applied.")
    print("=" * 78)
    attack_speculation_as_finding()
    attack_fabricated_inference()
    attack_identity_prediction()
    attack_deterministic_first()
    attack_network_dismantling()
    attack_dismantling_identity()
    attack_escalation_honesty()
    attack_dispersed_cadence_blind_spot()

    print("\n" + "=" * 78)
    npass = sum(1 for r in RESULTS if r["passed"])
    for r in RESULTS:
        print(f"  {'PASS' if r['passed'] else 'FAIL'}  {r['attack']}")
    print(f"\nSUMMARY: {npass}/{len(RESULTS)} attacks PASSED "
          f"({len(RESULTS) - npass} finding(s) for triage).")
    print(f"Fixtures written to: {FIX_DIR}")
    print("=" * 78)
    (FIX_DIR / "predictive_gauntlet_results.json").write_text(
        json.dumps(RESULTS, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
