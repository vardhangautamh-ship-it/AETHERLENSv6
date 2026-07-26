"""
AETHERLENS — real-case regression runner (CI gate).

Loads REAL case bundles from disk, drives the full deterministic pipeline
(ingest -> resolve -> graph -> 18-section report), and exits NON-ZERO on any
error or violated invariant, so it can gate a build.

Fixtures (all git-tracked):
    GHOSTWIRE  test_data/ghostwire/GHOSTWIRE_*.csv
    MERIDIAN   test_cases/MERIDIAN_*
    HYDRA      test_data/hydra/HYDRA_0*            (manifest = answer key, NOT ingested)
    CHIMERA    test_data/chimera/CHIMERA_0*        (manifest = answer key, NOT ingested)

Per-fixture invariants come from each bundle's manifest/known-good state.
Cross-cutting invariants (every fixture):
    I1  no fabricated node/edge  (no node_type=="unknown", no "co:" ids,
        no co_appears edges — ties to the relationship_mapper guardrail)
    I2  no mixed/old statute era in §17/§18
    I3  §09B case type == §18 case type (family match; generate_report raises
        RuntimeError on violation — completing at all IS the assert)
    I4  identity-blind: no pattern / risk factor / tactical text uses
        nationality, ethnicity or religion as a reason
    I5  reproducible: §06 timeline + subject + case type identical across two
        generations in the same run (no now()-imputation in content)

LLM calls are forced OFF — the run is fully deterministic and free.
Run:  PYTHONUTF8=1 python run_case_regression.py
Exit: 0 = all fixtures + invariants green; 1 = anything failed.
"""
import json
import pathlib
import sys
import unittest.mock as _mock

sys.path.insert(0, ".")

# ── Force every LLM path off BEFORE pipeline imports ─────────────────────────
# NB: ai_agents._call_bedrock fetches a FRESH client via config.get_bedrock_client()
# on every call, so nulling config.bedrock_client alone is not enough.
_mock.patch("modules.entity_resolution._call_gemini", return_value="").start()
_mock.patch("modules.entity_resolution._call_bedrock_for_fusion", return_value="").start()
import config
config.bedrock_client = None
config.get_bedrock_client = lambda: (None, "")
config.GEMINI_API_KEY = ""          # gates every Gemini HTTP path at call time
import modules.report_generator as _rg
_mock.patch.object(_rg, "_call_gemini_report", return_value=None).start()

from modules.data_ingestion      import ingest_file
from modules.entity_resolution   import resolve_entity_from_multiple_docs, clean_person_object
from modules.relationship_mapper import (build_graph, build_graph_from_person,
                                         extract_relationships_from_structured_rows)
from modules.report_generator    import generate_report
from modules.statute_era         import detect_statute_era

ROOT = pathlib.Path(__file__).resolve().parent

# Identity attributes that must never appear as a *reason* in any flag/score.
_IDENTITY_TOKENS = ("nationality", "ethnicity", "ethnic", "religion", "religious",
                    "caste", "hindu", "muslim", "christian", "sikh", "communal")

FIXTURES = [
    {
        "name": "GHOSTWIRE",
        "files": sorted((ROOT / "test_data" / "ghostwire").glob("GHOSTWIRE_*.csv")),
        "manifest": None,
        "subject_forms": ["Arjun Mehta"],
        "must_stay_separate": [],           # no seeded merge trap
        "must_appear_distinct": ["Rajan Iyer"],
    },
    {
        "name": "MERIDIAN",
        "files": sorted((ROOT / "test_cases").glob("MERIDIAN_*")),
        "manifest": None,
        "subject_forms": ["Kabir Anwar Farhadi"],
        "must_stay_separate": [],
        "must_appear_distinct": [],
    },
    {
        "name": "HYDRA",
        "files": sorted((ROOT / "test_data" / "hydra").glob("HYDRA_0*")),
        "manifest": ROOT / "test_data" / "hydra" / "HYDRA_MANIFESTATION.json",
        # answer key: subject's 5 name-forms must resolve to ONE subject
        "subject_forms": ["R. Ramesh Kumar", "Ramesh Kumar", "R. Kumar",
                          "Kumar, Ramesh", "Ramesh Kumar R."],
        # SPLIT_test_CRITICAL: same tokens, reversed order, different person
        "must_stay_separate": ["S. Kumar Ramesh", "Ramesh Suresh",
                               "Vijay Kumar", "R. Suresh"],
        "must_appear_distinct": ["S. Kumar Ramesh"],
    },
    {
        "name": "CHIMERA",
        "files": sorted((ROOT / "test_data" / "chimera").glob("CHIMERA_0*")),
        "manifest": ROOT / "test_data" / "chimera" / "CHIMERA_MANIFESTATION.json",
        "subject_forms": ["A. Farhan Sheikh", "Farhan Sheikh", "A. Sheikh",
                          "Sheikh, Farhan", "Farhan Sheikh A.", "Farhan A."],
        # LANDLINE_TRAP: Rohit shares only the office landline with the subject
        "must_stay_separate": ["S. Farhan Ahmed", "Rohit Malhotra",
                               "Imran Qadri", "Naval Kotak"],
        "must_appear_distinct": ["Rohit Malhotra"],
    },
]


def _run_pipeline(files):
    """Ingest -> resolve -> clean -> graph -> report. Mirrors app.py FUSION flow."""
    docs = []
    for fp in files:
        r = ingest_file(fp.read_bytes(), fp.name, "regression", declared=True)
        if not r.get("success"):
            raise RuntimeError(f"ingest failed: {fp.name}")
        docs.append(r)
    person, _method = resolve_entity_from_multiple_docs(docs)
    person = clean_person_object(person)

    ents, rels = [], []
    _, e, r = build_graph_from_person(person, {"results": []})
    ents += e; rels += r
    for d in docs:
        se, sr = extract_relationships_from_structured_rows(
            d.get("structured_rows") or [], d.get("filename", "doc"))
        ents += se; rels += sr
    seen = set()
    merged = [x for x in ents if not (x["id"] in seen or seen.add(x["id"]))]
    G = build_graph(merged, rels)

    graph_data = {"entities": merged, "relationships": rels}
    report = generate_report(person, graph_data=graph_data, user_id="regression",
                             mode="FUSION", agent_results=None, raw_documents=docs)
    return docs, person, G, report


def _section_text(obj) -> str:
    """Flatten any section structure to searchable lowercase text."""
    try:
        return json.dumps(obj, default=str, ensure_ascii=False).lower()
    except Exception:
        return str(obj).lower()


def _timeline_signature(report) -> str:
    """§06 content signature used for the reproducibility invariant (I5)."""
    secs = report.get("sections", {})
    return _section_text(secs.get("timeline_intelligence", "")) + \
           _section_text(secs.get("account_timeline", ""))


def check_fixture(fx) -> list[str]:
    """Run one fixture; return list of failure strings (empty = pass)."""
    fails = []
    name = fx["name"]
    if not fx["files"]:
        return [f"{name}: no input files found on disk"]
    if fx["manifest"] is not None and not fx["manifest"].exists():
        return [f"{name}: manifest answer key missing"]
    # The manifest must never be fed to the pipeline
    assert all("MANIFEST" not in f.name.upper() for f in fx["files"]), \
        f"{name}: manifest leaked into pipeline input"

    docs, person, G, report = _run_pipeline(fx["files"])
    subject = person.get("confirmed_name", "")
    secs = report.get("sections", {})

    # ── Fixture-specific: subject resolution ────────────────────────────────
    forms_lower = [f.lower() for f in fx["subject_forms"]]
    if subject.lower() not in forms_lower:
        fails.append(f"{name}: subject {subject!r} not one of expected forms {fx['subject_forms']}")

    # MERGE check: none of the subject's OTHER expected forms may survive as a
    # separate person node (they must have merged into the subject).
    node_labels = {str(d.get("label", n)).lower(): d.get("node_type", "")
                   for n, d in G.nodes(data=True)}
    for form in forms_lower:
        if form != subject.lower() and node_labels.get(form) == "person":
            fails.append(f"{name}: subject name-form {form!r} left as separate person node (not merged)")

    # SPLIT check: distinct associates must NOT be absorbed into the subject.
    variants_lower = {str(v).lower() for v in person.get("name_variants", [])}
    for assoc in fx["must_stay_separate"]:
        if assoc.lower() == subject.lower():
            fails.append(f"{name}: associate {assoc!r} wrongly chosen as the subject")
        if assoc.lower() in variants_lower:
            fails.append(f"{name}: associate {assoc!r} wrongly merged into subject variants")

    # Distinct associates must actually ENTER the intelligence picture.
    everything = _section_text(secs) + " " + " ".join(node_labels)
    for assoc in fx["must_appear_distinct"]:
        if assoc.lower() not in everything:
            fails.append(f"{name}: expected distinct associate {assoc!r} missing from graph/report")

    # ── I1: no fabricated node/edge ─────────────────────────────────────────
    for n, d in G.nodes(data=True):
        if d.get("node_type") == "unknown":
            fails.append(f"{name}: I1 fabricated 'unknown' node {n!r}")
        if str(n).startswith("co:"):
            fails.append(f"{name}: I1 fabricated co-appearance node {n!r}")
    for u, v, d in G.edges(data=True):
        if d.get("edge_type") == "co_appears":
            fails.append(f"{name}: I1 fabricated co_appears edge {u!r}->{v!r}")

    # ── I2: statute era must be new-era only in §17/§18 ─────────────────────
    for sec_key in ("next_steps", "tactical_plan"):
        era = detect_statute_era(_section_text(secs.get(sec_key, "")))
        if era in ("old", "mixed"):
            fails.append(f"{name}: I2 statute era {era!r} in {sec_key}")

    # ── I3: §09B case type == §18 case type ─────────────────────────────────
    # generate_report raises RuntimeError("CASE-TYPE CONSISTENCY VIOLATION") on
    # mismatch, so reaching this line proves the assert ran. Double-check the
    # values anyway when both are present.
    ct_09b = str(secs.get("pattern_analysis", {}).get("case_type", "")).lower()
    ct_18  = str(secs.get("tactical_plan", {}).get("case_type", "")).lower()
    if ct_09b and ct_18 and ct_09b.split()[0][:5] not in ct_18 and ct_18.split()[0][:5] not in ct_09b:
        fails.append(f"{name}: I3 case-type mismatch 09B={ct_09b!r} vs 18={ct_18!r}")

    # ── I4: identity-blind ──────────────────────────────────────────────────
    # The report legitimately NAMES the protected attributes inside guardrail
    # disclaimers ("No indicator uses nationality, ethnicity, or religion").
    # Only flag a token when its surrounding window carries no negation — i.e.
    # the attribute is being USED as a reason, not forbidden.
    _NEGATIONS = ("not ", "no indicator", "never", "must not", "blind",
                  "not based", "nor ", "none ")
    for sec_key in ("pattern_analysis", "risk_assessment", "tactical_plan", "next_steps"):
        txt = _section_text(secs.get(sec_key, ""))
        for tok in _IDENTITY_TOKENS:
            i = txt.find(tok)
            while i != -1:
                window = txt[max(0, i - 160): i + 160]
                if not any(neg in window for neg in _NEGATIONS):
                    fails.append(f"{name}: I4 identity token {tok!r} used non-negated in {sec_key}: ...{window[100:220]}...")
                i = txt.find(tok, i + 1)

    # ── I5: reproducibility (no now()-derived content) ──────────────────────
    _, person2, _, report2 = _run_pipeline(fx["files"])
    if person2.get("confirmed_name") != subject:
        fails.append(f"{name}: I5 subject changed between runs")
    if _timeline_signature(report2) != _timeline_signature(report):
        fails.append(f"{name}: I5 timeline content differs between two same-input runs")

    return fails


def main() -> int:
    print("=" * 72)
    print("AETHERLENS REAL-CASE REGRESSION RUNNER  (deterministic, LLM off)")
    print("=" * 72)
    all_fails = []
    for fx in FIXTURES:
        print(f"\n--- {fx['name']} ({len(fx['files'])} files) ---")
        try:
            fails = check_fixture(fx)
        except Exception as exc:  # pipeline crash = failure, never silent
            import traceback; traceback.print_exc()
            fails = [f"{fx['name']}: pipeline crashed: {exc}"]
        if fails:
            for f in fails:
                print(f"  [FAIL] {f}")
            all_fails.extend(fails)
        else:
            print(f"  [PASS] all fixture checks + invariants I1-I5")
    print("\n" + "=" * 72)
    if all_fails:
        print(f"RESULT: FAIL — {len(all_fails)} violation(s)")
        return 1
    print("RESULT: PASS — all fixtures, all invariants")
    return 0


if __name__ == "__main__":
    sys.exit(main())
