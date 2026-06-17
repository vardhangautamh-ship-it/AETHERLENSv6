"""
Regression suite for the Phase 4 entity-resolution fixes.

Locks in:
  (a) Address/location boilerplate NEVER appears as a §08 Key Association
      — on GhostWire (real) and on a Jupiter-style boilerplate fixture.
  (b) Name variants (case / whitespace) collapse to a single canonical vote
      and never raise a false NAME_CONFLICT.
  (c) resolve_entity_from_multiple_docs output is IDENTICAL across two runs
      on the same input (deterministic / reproducible).

Plus per-fix unit checks:
  Fix 1 — deterministic platform extraction populates §03 with no AI.
  Fix 5 — cross-file boilerplate detection flags shared addresses.

Run: python3 test_entity_resolution_regression.py
"""
import sys, os, types, pathlib, json, copy

# ─── config stub + pdfplumber mock + LLM-off (deterministic) ─────────────────
cfg = types.ModuleType("config")
cfg.GEMINI_API_KEY     = ""
cfg.GEMINI_ENDPOINT    = ""
cfg.DATABASE_PATH      = ":memory:"
cfg.EXPORTS_DIR        = pathlib.Path("exports")
cfg.bedrock_client     = None
cfg.get_bedrock_client = lambda: (None, "")
sys.modules["config"]  = cfg

_mock_pdf = types.ModuleType("pdfplumber")
class _MockPdfCtx:
    def __enter__(self): return self
    def __exit__(self, *a): pass
    pages = []
_mock_pdf.open = lambda *a, **kw: _MockPdfCtx()
sys.modules["pdfplumber"] = _mock_pdf

import unittest.mock as _mock
_mock.patch("modules.entity_resolution._call_bedrock_for_fusion", return_value="").start()
_mock.patch("modules.entity_resolution._call_gemini", return_value="").start()

from modules.data_ingestion      import ingest_file, extract_primary_subject_from_bytes
from modules.entity_resolution   import (
    resolve_entity_from_multiple_docs,
    extract_platforms_from_rows,
    detect_all_conflicts,
)
from modules.sanitizer           import most_common_by_key, normalize_name_key
from modules.relationship_mapper import (
    build_graph, build_graph_from_person,
    extract_relationships_from_structured_rows,
    graph_summary, detect_boilerplate_locations,
)
from modules.report_generator    import generate_report

GW_DIR   = pathlib.Path("test_data/ghostwire")
GW_FILES = sorted(GW_DIR.glob("GHOSTWIRE_*.csv"))

results = []
def check(label, ok):
    results.append(bool(ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")

_LOCATION_SIGNS = ("gurugram", "dubai", "new york", "cyber city",
                   "manesar", "industrial area", "amity", "sector", "(location)")

def _assoc_has_location(assoc_list):
    """True if any §08 association string looks like a place/address."""
    for a in assoc_list:
        s = (a if isinstance(a, str) else str(a)).lower()
        if any(sign in s for sign in _LOCATION_SIGNS):
            return True
    return False

def _build_graph_like_app(person, all_results):
    """Mirror app.py fusion graph assembly (build_graph_from_person + struct rows)."""
    all_ents, all_rels = [], []
    for res in all_results:
        _, ents, rels = build_graph_from_person(person, {"results": []})
        s_ents, s_rels = extract_relationships_from_structured_rows(
            res.get("structured_rows", []), res["filename"])
        ents.extend(s_ents); rels.extend(s_rels)
        all_ents.extend(ents); all_rels.extend(rels)
    seen, merged = set(), []
    for e in all_ents:
        if e["id"] not in seen:
            merged.append(e); seen.add(e["id"])
    return build_graph(merged, all_rels)

# ══════════════════════════════════════════════════════════════════════════════
print("=" * 72)
print("PART 1 — GhostWire (real data)")
print("=" * 72)

all_results = []
for fp in GW_FILES:
    r = ingest_file(fp.read_bytes(), fp.name, "regression", declared=True)
    assert r["success"], f"ingest failed: {fp.name}"
    all_results.append(r)

person, method = resolve_entity_from_multiple_docs(all_results)

# ── Fix 1: deterministic platform extraction ─────────────────────────────────
print("\n[1] Fix 1 — deterministic platform/username extraction (no AI)")
det = extract_platforms_from_rows(all_results)
print(f"    platforms detected : {det['platforms_confirmed']}")
print(f"    usernames detected : {det['usernames']}")
check("GitHub found from rows",      any("github"   in p.lower() for p in det["platforms_confirmed"]))
check("Telegram found from rows",    any("telegram" in p.lower() for p in det["platforms_confirmed"]))
check("HuggingFace found from rows", any("hugging"  in p.lower() for p in det["platforms_confirmed"]))
check("Twitter found from rows",     any("twitter"  in p.lower() for p in det["platforms_confirmed"]))
check("@reels NOT in platforms (status=SPAM filtered)",
      not any("reels" in str(v).lower() for v in det["usernames"].values()))
check("@spam NOT in platforms (status=SPAM filtered)",
      not any("spam"  in str(v).lower() for v in det["usernames"].values()))
check("resolved person carries platforms",
      any("github" in p.lower() for p in person.get("platforms_confirmed", [])))

# ── (a) §08 has no location boilerplate ──────────────────────────────────────
print("\n[2] (a) GhostWire §08 Key Associations exclude all location strings")
G = _build_graph_like_app(person, all_results)
boiler = detect_boilerplate_locations(all_results)
summ = graph_summary(G, subject_name=person.get("confirmed_name", ""), boilerplate=boiler)
rep = generate_report(person, graph_data={"summary": summ}, raw_documents=all_results,
                      user_id="regression", mode="full")
assoc = rep.get("sections", {}).get("key_associations", {}).get("associations", [])
print(f"    §08 associations: {assoc}")
check("§08 contains NO location/address string", not _assoc_has_location(assoc))
check("§08 still lists the real associate (Rajan Iyer)",
      any("rajan" in str(a).lower() for a in assoc))

# ── (c) determinism ──────────────────────────────────────────────────────────
print("\n[3] (c) resolve_entity_from_multiple_docs is identical across 2 runs")
def _fingerprint(p):
    return json.dumps({
        "name":      p.get("confirmed_name"),
        "platforms": sorted(p.get("platforms_confirmed", [])),
        "usernames": {k: p["usernames"][k] for k in sorted(p.get("usernames", {}))},
        "phones":    sorted(p.get("phones_found", [])),
        "conflicts": [c.get("flag") for c in p.get("conflicts", [])],
    }, sort_keys=True)
p1, _ = resolve_entity_from_multiple_docs(copy.deepcopy(all_results))
p2, _ = resolve_entity_from_multiple_docs(copy.deepcopy(all_results))
check("two runs produce identical resolver fingerprint", _fingerprint(p1) == _fingerprint(p2))

# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 72)
print("PART 2 — Jupiter-style boilerplate fixture (synthetic, regression only)")
print("=" * 72)

# Every document carries the SAME institutional address ("Manesar Industrial
# Area") — exactly the cross-file boilerplate pattern from Operation Jupiter.
def _doc(fn, loc):
    return {
        "filename": fn,
        "locations": [{"value": loc}],
        "structured_rows": [{"subject": "Harshvardhan Gautam", "location": loc, "notes": "x"}],
        "entities": {"names": [{"value": "Harshvardhan Gautam"}]},
        "raw_text": f"Harshvardhan Gautam, {loc}",
    }
BOILER = "Manesar Industrial Area"
jup_docs = [_doc(f"JUPITER_{i}.pdf", BOILER) for i in range(5)]

print("\n[4] Fix 5 — boilerplate appearing in all files is detected")
bp = detect_boilerplate_locations(jup_docs)
print(f"    boilerplate set: {bp}")
check("'Manesar Industrial Area' flagged as boilerplate", BOILER.lower() in bp)

print("\n[5] (a) Boilerplate excluded from top_nodes ranking")
jperson = {"confirmed_name": "Harshvardhan Gautam",
           "location_stated": [BOILER], "platforms_confirmed": [], "usernames": {}}
Gj = _build_graph_like_app(jperson, jup_docs)
jsumm = graph_summary(Gj, subject_name="Harshvardhan Gautam", boilerplate=bp)
top_labels = [n.get("label", "").lower() for n in jsumm.get("top_nodes", [])]
print(f"    top_nodes labels: {top_labels}")
check("boilerplate address NOT in top_nodes", BOILER.lower() not in top_labels)

print("\n[6] (a) Boilerplate never reaches §08 even via supplement fallback")
jrep = generate_report(jperson, graph_data={"summary": jsumm}, raw_documents=jup_docs,
                       user_id="regression", mode="full")
jassoc = jrep.get("sections", {}).get("key_associations", {}).get("associations", [])
print(f"    §08 associations: {jassoc}")
check("'Manesar Industrial Area' NOT a Key Association", not _assoc_has_location(jassoc))

# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 72)
print("PART 3 — Unit checks (normalization + conflict)")
print("=" * 72)

print("\n[7] (b) Name variants collapse to one canonical vote")
variants = (["Arjun Mehta"] * 8) + ["ARJUN MEHTA", "Arjun  Mehta"]
ranked = most_common_by_key(variants)
print(f"    most_common_by_key: {ranked}")
check("variants collapse to a single key", len(ranked) == 1)
check("collapsed count == 10",            ranked and ranked[0][1] == 10)
check("display form is title-case 'Arjun Mehta'", ranked and ranked[0][0] == "Arjun Mehta")
check("normalize_name_key folds case+space",
      normalize_name_key("Arjun  MEHTA ") == normalize_name_key("arjun mehta"))

print("\n[8] (b) No false NAME_CONFLICT on case variant of the same name")
def _cdoc(fn, names):
    return {"filename": fn, "entities": {"names": [{"value": v} for v in names]},
            "locations": [], "raw_text": ""}
docs_case = [
    _cdoc("a.csv", ["Arjun Mehta"]),
    _cdoc("b.csv", ["ARJUN MEHTA", "Arjun Mehta"]),   # ALLCAPS export
    _cdoc("c.csv", ["arjun mehta"]),                   # OCR lowercase
]
pc = {"confirmed_name": "Arjun Mehta", "anomaly_flags": [], "conflicts": []}
conf = detect_all_conflicts(docs_case, "Arjun Mehta", pc)
name_conf = [c for c in conf if c["type"] == "NAME_CONFLICT"]
print(f"    NAME_CONFLICTs fired: {len(name_conf)}")
check("no false NAME_CONFLICT on case variants", len(name_conf) == 0)

print("\n[9] (b) A genuinely different name still conflicts (no over-suppression)")
docs_real = [
    _cdoc("a.csv", ["Arjun Mehta"]),
    _cdoc("b.csv", ["Arjun Sharma"]),   # different surname, shares 'Arjun' only
    _cdoc("c.csv", ["Arjun Mehta Patel"]),  # 2-token overlap → real variant
]
pr = {"confirmed_name": "Arjun Mehta", "anomaly_flags": [], "conflicts": []}
conf_r = detect_all_conflicts(docs_real, "Arjun Mehta", pr)
print(f"    conflicts: {[c['type'] for c in conf_r]}")
check("real multi-token variant still detected",
      any(c["type"] == "NAME_CONFLICT" for c in conf_r))

# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 72)
passed, total = sum(results), len(results)
print(f"SUMMARY: {passed}/{total} checks passed")
print("ALL REGRESSION CHECKS PASSED" if passed == total else "SOME CHECKS FAILED — review above")
sys.exit(0 if passed == total else 1)
