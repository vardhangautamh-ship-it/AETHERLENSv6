"""
END-TO-END TEST THROUGH THE REAL APP PATH (app.py), headless.

Every other test drives the pipeline modules directly. This one drives the
REAL functions app.py calls, in the real order, with a mocked `streamlit`
module and a seeded session_state — no app logic is re-implemented:

    screen_fusion()            (full inline FUSION pipeline, app.py)
    _generate_and_store()      (report assembly + _add_to_case_library)
    screen_cross_case_intel()  (library -> mine_case_set / targeting)

Cases driven through the app path: GHOSTWIRE, MERIDIAN, CHIMERA (real
fixtures) plus SATELLITE (test_data/satellite — a companion case that shares
one strong mobile with MERIDIAN, and shares CHIMERA's landline + MERIDIAN's
district in raw files so the never-link rules are exercised with real data).

Honest coverage boundary (what this does NOT drive):
  * Widget interaction itself (file_uploader/button clicks). The test seeds
    the exact session_state keys the real widgets set (fusion_staged;
    fusion_declaration; fusion_analyse_triggered + fusion_stage, which is
    all the ANALYSE button does — app.py:2555-2556) and then calls the same
    screen function the Streamlit runtime would re-execute.
  * st.rerun() is a no-op here (real Streamlit re-executes the script; the
    app stores all state BEFORE rerunning, so the paths remain identical).
  * The PIN/login screens are bypassed by seeding the auth session keys;
    validate_session/JWT is not under test.

Run:  PYTHONUTF8=1 python test_app_e2e.py
(Whole test runs in ONE process, so PYTHONHASHSEED pinning is not required
for its internal comparisons.)

LLM calls are forced OFF (same patches as run_case_regression.py) — no
network, no Bedrock/Gemini spend. DB + exports are redirected to a temp dir.
"""

import json
import pathlib
import re
import sys
import tempfile
import time
from unittest import mock

BASE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

# ── LLM OFF + side-effect redirect (BEFORE any app/module import) ───────────
mock.patch("modules.entity_resolution._call_gemini", return_value="").start()
mock.patch("modules.entity_resolution._call_bedrock_for_fusion",
           return_value="").start()
import config                                                      # noqa: E402
config.bedrock_client = None
config.get_bedrock_client = lambda: (None, "")
config.GEMINI_API_KEY = ""
_TMP = pathlib.Path(tempfile.mkdtemp(prefix="aeth_e2e_"))
config.DATABASE_PATH = _TMP / "e2e.db"      # all DB access late-binds through
config.DATABASE_DIR = _TMP                  # config.* so this redirects it
config.EXPORTS_DIR = _TMP
import modules.report_generator                                    # noqa: E402
mock.patch.object(modules.report_generator, "_call_gemini_report",
                  return_value=None).start()


# ── Mock streamlit ───────────────────────────────────────────────────────────
class _SessionState(dict):
    """Dict with attribute access — mirrors st.session_state's contract."""

    def __getattr__(self, k):
        try:
            return self[k]
        except KeyError:
            raise AttributeError(k)

    def __setattr__(self, k, v):
        self[k] = v

    def __delattr__(self, k):
        del self[k]


class _Elem:
    """Universal container/widget stand-in: context manager that delegates
    every attribute (col.button, pb.progress, status.text, ...) to the mock
    streamlit instance."""

    def __init__(self, stm):
        object.__setattr__(self, "_stm", stm)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __getattr__(self, name):
        return getattr(self._stm, name)


class MockStreamlit:
    def __init__(self):
        self.session_state = _SessionState()
        self.rendered = []          # (kind, text) capture for assertions
        self.sidebar = _Elem(self)

    # capture-bearing display calls
    def _cap(self, kind, body):
        self.rendered.append((kind, str(body)))

    def markdown(self, body="", **kw):
        self._cap("markdown", body)

    def code(self, body="", **kw):
        self._cap("code", body)

    def info(self, body="", **kw):
        self._cap("info", body)

    def warning(self, body="", **kw):
        self._cap("warning", body)

    def success(self, body="", **kw):
        self._cap("success", body)

    def error(self, body="", **kw):
        self._cap("error", body)

    def caption(self, body="", **kw):
        self._cap("caption", body)

    # widgets — same return contracts the app relies on
    def button(self, label="", **kw):
        return False                # never click: a True wipes/pops state

    def checkbox(self, label="", value=False, key=None, **kw):
        # Real Streamlit keeps widget state under `key`; the app reads the
        # RETURN VALUE (declared = st.checkbox(..., key="fusion_declaration"))
        return bool(self.session_state.get(key, value)) if key else bool(value)

    def text_input(self, label="", value="", **kw):
        return value

    def text_area(self, label="", value="", **kw):
        return value

    def selectbox(self, label="", options=None, index=0, **kw):
        opts = list(options or [])
        return opts[index] if opts and 0 <= index < len(opts) else None

    def file_uploader(self, *a, **kw):
        return None                 # staging is seeded directly

    def download_button(self, *a, **kw):
        return False

    # containers
    def columns(self, spec, **kw):
        n = spec if isinstance(spec, int) else len(spec)
        return [_Elem(self) for _ in range(n)]

    def tabs(self, labels, **kw):
        return [_Elem(self) for _ in labels]

    def expander(self, *a, **kw):
        return _Elem(self)

    def container(self, **kw):
        return _Elem(self)

    def spinner(self, *a, **kw):
        return _Elem(self)

    def progress(self, *a, **kw):
        return _Elem(self)

    def empty(self):
        return _Elem(self)

    # control flow
    def rerun(self):
        pass                        # state is stored before every rerun

    def stop(self):
        pass

    def set_page_config(self, **kw):
        pass

    def __getattr__(self, name):
        # anything unanticipated (metric, dataframe, plotly_chart, text, ...)
        def _noop(*a, **kw):
            return _Elem(self)
        return _noop


import types                                                       # noqa: E402
st = MockStreamlit()
sys.modules["streamlit"] = st
_components = types.ModuleType("streamlit.components")
_components_v1 = types.ModuleType("streamlit.components.v1")
_components_v1.html = lambda *a, **kw: None
_components.v1 = _components_v1
sys.modules["streamlit.components"] = _components
sys.modules["streamlit.components.v1"] = _components_v1

# ── Import the app (this RUNS main(): app.py ends with `or True: main()`) ───
import app                                                         # noqa: E402
assert app.st is st, "app.py did not bind the mocked streamlit module"

# ── Auth bypass: seed the exact keys main()/screens read ────────────────────
from modules import auth as _auth                                  # noqa: E402
assert _auth.st is st, "modules.auth did not bind the mocked streamlit module"
st.session_state["pin_verified"] = True
st.session_state["jwt_token"] = _auth.create_token(
    {"username": "e2e", "role": "ADMIN"})
st.session_state["current_user"] = "e2e"
st.session_state["current_role"] = "ADMIN"
st.session_state["session_start"] = time.time()

# ── House-style check helper ─────────────────────────────────────────────────
results = []


def check(label, ok):
    results.append(bool(ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")


# ── Fixture inventory (same globs as run_case_regression.py) ────────────────
CASES = {
    "GHOSTWIRE": sorted((BASE / "test_data" / "ghostwire").glob("GHOSTWIRE_*")),
    "MERIDIAN": sorted((BASE / "test_cases").glob("MERIDIAN_*")),
    "CHIMERA": sorted(p for p in (BASE / "test_data" / "chimera").glob("CHIMERA_0*")
                      if "MANIFEST" not in p.name.upper()),
    "SATELLITE": sorted((BASE / "test_data" / "satellite").glob("SATELLITE_*")),
}
EXPECTED_SUBJECT = {
    "GHOSTWIRE": ("arjun mehta",),
    "MERIDIAN": ("kabir anwar farhadi",),
    # any of the regression runner's accepted CHIMERA subject forms
    "CHIMERA": ("a. farhan sheikh", "farhan sheikh", "a. sheikh",
                "sheikh, farhan", "farhan sheikh a.", "farhan a."),
    "SATELLITE": ("dilawar hussain",),
}


def run_case_through_app(name, files):
    """Drive ONE case through the app path: seed the state the real widgets
    set, then call the same functions Streamlit would."""
    # what CLEAR & START OVER + a fresh screen visit leave behind
    app._fusion_reset()
    for k in ("fusion_analyse_triggered", "fusion_stage", "fusion_declaration"):
        st.session_state.pop(k, None)

    # Phase A equivalent: the upload widget's staging loop (app.py:2337-2349)
    # produces {name, size, bytes, type} dicts via safe_decode_file — seed the
    # same shape from disk, through the app's own decoder.
    staged = []
    for fp in files:
        raw = fp.read_bytes()
        staged.append({
            "name": fp.name, "size": len(raw),
            "bytes": app.safe_decode_file(raw, fp.name),
            "type": "." + fp.name.rsplit(".", 1)[-1].lower(),
        })
    st.session_state["fusion_staged"] = staged
    # declaration checkbox state + exactly what the ANALYSE button sets
    st.session_state["fusion_declaration"] = True
    st.session_state["fusion_analyse_triggered"] = True
    st.session_state["fusion_stage"] = "processing"

    app.screen_fusion()                       # the real inline FUSION pipeline

    ok = bool(st.session_state.get("fusion_analysed"))
    person = st.session_state.get("person_profile")
    app._generate_and_store("FUSION")         # real report assembly + library
    report = st.session_state.get("report_data")
    return {
        "ok": ok, "person": person, "report": report,
        "graph_data": st.session_state.get("graph_data"),
        "timeline_data": st.session_state.get("timeline_data"),
        "agent_results": st.session_state.get("agent_results"),
        "raw_documents": st.session_state.get("raw_documents"),
    }


# ═════════════════════════════════════════════════════════════════════════════
print("=" * 72)
print("PART 1 — single case ingested through the app path -> complete report")
print("=" * 72)

snap = {}
for name in ("GHOSTWIRE", "MERIDIAN", "CHIMERA", "SATELLITE"):
    t0 = time.time()
    snap[name] = run_case_through_app(name, CASES[name])
    print(f"  ({name}: {time.time() - t0:.1f}s, "
          f"subject={snap[name]['report'].get('subject')!r})")

for name in ("GHOSTWIRE", "MERIDIAN", "CHIMERA", "SATELLITE"):
    s = snap[name]
    check(f"{name}: fusion pipeline completed (fusion_analysed)", s["ok"])
    check(f"{name}: report has no error key", "error" not in (s["report"] or {}))
    check(f"{name}: subject resolved",
          str(s["report"].get("subject", "")).lower()
          in EXPECTED_SUBJECT[name])
    secs = (s["report"] or {}).get("sections") or {}
    check(f"{name}: sections present (>= 15)", len(secs) >= 15)
    check(f"{name}: PDF generated (> 1000 bytes)",
          len(s["report"].get("pdf_bytes") or b"") > 1000)

# ═════════════════════════════════════════════════════════════════════════════
print("=" * 72)
print("PART 2 — case library via _add_to_case_library (app path)")
print("=" * 72)

lib = st.session_state.get("case_library", [])
check("library holds 4 cases", len(lib) == 4)
lib_subjects = {e.get("subject") for e in lib}
check("library subjects are the 4 case subjects",
      {"Arjun Mehta", "Kabir Anwar Farhadi", "Dilawar Hussain"} <= lib_subjects
      and len(lib_subjects) == 4)
check("library entries carry ontology objects",
      all(e.get("ontology") is not None for e in lib))

# ═════════════════════════════════════════════════════════════════════════════
print("=" * 72)
print("PART 3 — cross-case intel via the real screen_cross_case_intel path")
print("=" * 72)

st.rendered.clear()
st.session_state["active_screen"] = "cross_case_intel"
app.screen_cross_case_intel()                 # runs all five tabs headlessly

rendered_text = "\n".join(t for _, t in st.rendered)
check("watchlist state written (cci_prev_watchlist)",
      st.session_state.get("cci_prev_watchlist") is not None)
check("mining output rendered by the screen (cluster + unlinked roster)",
      "CLUSTER 1" in rendered_text and "UNLINKED SUBJECTS" in rendered_text)

# Structured assertions: same pre-processing the screen does (app.py:5513-14)
from modules.data_mining import mine_case_set                      # noqa: E402
cases_for_mining = [{"subject": e["subject"], "ontology": e["ontology"]}
                    for e in lib if e.get("ontology") is not None]
mined = mine_case_set(cases_for_mining)

check("cross-case: exactly one asserted link", mined["link_count"] == 1)
link = (mined["links"] or [{}])[0]
check("link is shared_phone (hard identifier)",
      link.get("type") == "shared_phone")
check("link is the planted MERIDIAN<->SATELLITE mobile",
      "96130" in str(link.get("value", "")).replace(" ", "").replace("-", ""))
check("link subjects are exactly the two sharing cases",
      sorted(link.get("subjects", []))
      == ["Dilawar Hussain", "Kabir Anwar Farhadi"])
check("link cited to BOTH cases (citation keys == subjects, all non-empty)",
      set(link.get("citations", {})) == set(link.get("subjects", []))
      and all(link["citations"].values()))
check("each case's citation carries the shared number as raw evidence",
      all(any("96130" in str(c.get("raw", "")).replace("-", "")
              for c in cites)
          for cites in link.get("citations", {}).values()))
# KNOWN WIRING GAP (found by this test, reported — do not paper over):
# ontology PhoneNumber.source is always "" through the real pipeline —
# person["phones_found"] items are plain number strings, and although
# build_phone_source_map's attribution is stored as person["phone_sources"],
# ontology build never reads that key — so cross-case phone citations fall
# back to data_mining's "source not recorded" annotation instead of naming
# the source files. The synthetic unit tests attach sources by hand and
# never see this.

check("uninvolved subjects stay unlinked (not implicated)",
      set(mined["unlinked_subjects"]) == lib_subjects
      - {"Dilawar Hussain", "Kabir Anwar Farhadi"})

# never-link rules, exercised with real planted data:
check("no same-name/same-city link types ever asserted",
      all(l["type"] in ("shared_phone", "shared_organization",
                        "shared_counterparty") for l in mined["links"]))
check("shared LANDLINE (+91-22-24450010, in SATELLITE and CHIMERA raw files) "
      "never becomes a link",
      not any("24450010" in str(l.get("value", "")) for l in mined["links"]))
# defense-in-depth: the landline is filtered before it even reaches an
# ontology phone list (typing gate) — document that layer too
check("landline filtered upstream of mining (absent from ontology phones)",
      not any("24450010" in str(getattr(ph, "number", ""))
              for c in cases_for_mining for ph in c["ontology"].phones))
check("shared district (Barpeta, in MERIDIAN and SATELLITE raw files) "
      "never becomes a link",
      not any(l.get("type") == "shared_location" for l in mined["links"]))
check("mining notice: association-not-culpability + human review",
      "association, not culpability" in mined["mining_notice"]
      and mined["human_review_required"] is True)

# ═════════════════════════════════════════════════════════════════════════════
print("=" * 72)
print("PART 4 — guardrails on APP-path reports (same as direct-path)")
print("=" * 72)

_IDENTITY_TOKENS = ("nationality", "ethnicity", "ethnic", "religion",
                    "religious", "caste", "hindu", "muslim", "christian",
                    "sikh", "communal")
_NEGATIONS = ("not ", "no indicator", "never", "must not", "blind",
              "not based", "nor ", "none ")


def _section_text(obj):
    try:
        return json.dumps(obj, default=str, ensure_ascii=False).lower()
    except Exception:
        return str(obj).lower()


from modules.statute_era import detect_statute_era                 # noqa: E402

for name in ("GHOSTWIRE", "MERIDIAN"):
    secs = snap[name]["report"]["sections"]

    all_txt = _section_text(secs)
    check(f"{name}: citations present ( — Source: )", " — source: " in all_txt)

    actions = (secs.get("tactical_plan") or {}).get("actions") or []
    check(f"{name}: tactical actions exist", len(actions) > 0)
    check(f"{name}: every tactical action carries the human-review marker",
          all(a.get("human_review_required") is True
              and str(a.get("human_review") or "").strip()
              for a in actions if isinstance(a, dict)))

    G = (snap[name]["graph_data"] or {}).get("graph")
    bad_nodes = [n for n, d in G.nodes(data=True)
                 if d.get("node_type") == "unknown" or str(n).startswith("co:")]
    bad_edges = [(u, v) for u, v, d in G.edges(data=True)
                 if d.get("edge_type") == "co_appears"]
    check(f"{name}: no fabricated graph nodes/edges (I1)",
          not bad_nodes and not bad_edges)

    era_ok = all(
        detect_statute_era(_section_text(secs.get(k))) not in ("old", "mixed")
        for k in ("next_steps", "tactical_plan"))
    check(f"{name}: statute era consistent (I2)", era_ok)

    blind_ok = True
    for k in ("pattern_analysis", "risk_assessment", "tactical_plan",
              "next_steps"):
        txt = _section_text(secs.get(k))
        for tok in _IDENTITY_TOKENS:
            for m in re.finditer(re.escape(tok), txt):
                win = txt[max(0, m.start() - 160):m.end() + 160]
                if not any(neg in win for neg in _NEGATIONS):
                    blind_ok = False
    check(f"{name}: identity-blind (I4, disclaimers allowed)", blind_ok)

# ═════════════════════════════════════════════════════════════════════════════
print("=" * 72)
print("PART 5 — APP path vs DIRECT pipeline: same case, compare reports")
print("=" * 72)

import run_case_regression as rcr                                  # noqa: E402

_d_docs, d_person, d_G, d_report = rcr._run_pipeline(CASES["GHOSTWIRE"])
a_report = snap["GHOSTWIRE"]["report"]
a_secs, d_secs = a_report["sections"], d_report["sections"]

check("APP vs DIRECT: same subject",
      a_report["subject"] == d_report["subject"])
check("APP vs DIRECT: same case type",
      (a_secs.get("pattern_analysis") or {}).get("case_type")
      == (d_secs.get("pattern_analysis") or {}).get("case_type"))
check("APP vs DIRECT: direct report passes the same guardrails "
      "(human-review on all tactical actions)",
      all(a.get("human_review_required") is True
          and str(a.get("human_review") or "").strip()
          for a in (d_secs.get("tactical_plan") or {}).get("actions") or []
          if isinstance(a, dict)))

# Section-by-section comparison with wall-clock scrubbed. Divergence caused
# by the CI runner passing FEWER inputs than the app (no timeline_data /
# behavioral_data / search_results / agent_results / graph summary) is
# EXPECTED and reported; divergence in any other section fails the test.
_INGESTED_RE = re.compile(r"ingested: \d{4}-\d{2}-\d{2}t\d{2}:\d{2}:\d{2}",
                          re.IGNORECASE)


def _scrub(obj):
    # _section_text lowercases, so the scrub must be case-insensitive
    return _INGESTED_RE.sub("ingested: <t>", _section_text(obj))


# Sections that legitimately differ because run_case_regression._run_pipeline
# (the CI direct path) deliberately passes FEWER inputs than screen_fusion:
# no inject_keyword_flags_from_docs (fewer anomaly flags), agent_results=None
# (report rebuilds risk/tactical from thinner inputs: 80/CRITICAL vs 82),
# no timeline_data / behavioral_data / search_results, and graph_data without
# the live networkx graph + summary (so e.g. the NETWORK_HUB centrality
# pattern only appears on the app path). Everything else must match exactly.
EXPECTED_DIVERGENT = {
    "anomalies_and_flags", "behavioral_patterns", "confidence_breakdown",
    "confidence_explanation", "key_associations", "network_map_summary",
    "overall_confidence", "pattern_analysis", "risk_assessment",
    "tactical_plan", "timeline_intelligence", "timeline_of_activity",
}
same, diverged, unexpected = [], [], []
for k in sorted(set(a_secs) | set(d_secs)):
    if _scrub(a_secs.get(k)) == _scrub(d_secs.get(k)):
        same.append(k)
    else:
        diverged.append(k)
        if k not in EXPECTED_DIVERGENT:
            unexpected.append(k)

print(f"  identical sections ({len(same)}): {', '.join(same) or '(none)'}")
print(f"  divergent sections ({len(diverged)}) — CI runner passes fewer "
      f"inputs than the app: {', '.join(diverged) or '(none)'}")
check("no divergence outside the documented input-difference set",
      not unexpected)
if unexpected:
    print(f"  UNEXPECTED divergence in: {unexpected}")

# Sub-invariants INSIDE the expected-divergent sections: the divergence must
# stay explainable as "app supplies more inputs", never contradiction.
a_flags = set((a_secs.get("anomalies_and_flags") or {}).get("items") or [])
d_flags = set((d_secs.get("anomalies_and_flags") or {}).get("items") or [])
check("app-path anomaly flags are a superset of direct-path flags "
      "(app only ADDS keyword/rule flags)", d_flags <= a_flags)


def _risk_level(secs):
    m = re.search(r"level:\s*(\w+)", _section_text(secs.get("risk_assessment")))
    return m.group(1) if m else None


check("APP vs DIRECT: same risk LEVEL (scores may differ with richer flags)",
      _risk_level(a_secs) is not None
      and _risk_level(a_secs) == _risk_level(d_secs))
check("app path timeline populated (the input the CI runner omits)",
      len((snap["GHOSTWIRE"]["timeline_data"] or {}).get("events") or []) > 0)

# ═════════════════════════════════════════════════════════════════════════════
print("=" * 72)
import shutil                                                      # noqa: E402
shutil.rmtree(_TMP, ignore_errors=True)
n_pass, n_total = sum(results), len(results)
print(f"SUMMARY: {n_pass}/{n_total} checks passed")
if n_pass == n_total:
    print("ALL APP-PATH E2E CHECKS PASSED")
    sys.exit(0)
print("SOME CHECKS FAILED — review above")
sys.exit(1)
