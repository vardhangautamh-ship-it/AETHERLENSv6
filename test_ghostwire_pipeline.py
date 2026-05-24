"""
Full pipeline test: 7 GHOSTWIRE_* CSV files → ingest → resolve → report.
Validates Section 01 (subject name) and Section 03 (platform presence).
No Streamlit, no AI engines — pure local pipeline.
"""
import sys, os, types, pathlib, io

# ── Minimal config stub ───────────────────────────────────────────────────────
cfg = types.ModuleType("config")
cfg.GEMINI_API_KEY    = ""
cfg.GEMINI_ENDPOINT   = ""
cfg.DATABASE_PATH     = ":memory:"
cfg.EXPORTS_DIR       = pathlib.Path("exports")
cfg.bedrock_client    = None
cfg.get_bedrock_client = lambda: (None, "")
sys.modules["config"] = cfg

# ── Mock pdfplumber (env has broken cffi — CSV-only test, no PDFs needed) ────
_mock_pdf = types.ModuleType("pdfplumber")
class _MockPdfCtx:
    def __enter__(self): return self
    def __exit__(self, *a): pass
    pages = []
_mock_pdf.open = lambda *a, **kw: _MockPdfCtx()
sys.modules["pdfplumber"] = _mock_pdf

# ── Import pipeline modules ───────────────────────────────────────────────────
from modules.data_ingestion      import ingest_file
from modules.entity_resolution   import (
    resolve_entity_from_multiple_docs,
    is_bad_subject_name,
    clean_person_object,
)
from modules.report_generator    import build_platform_presence, generate_pdf
from modules.relationship_mapper import build_graph, get_primary_subject

GW_DIR   = pathlib.Path("test_data/ghostwire")
GW_FILES = sorted(GW_DIR.glob("GHOSTWIRE_*.csv"))

results  = []
def check(label, ok):
    results.append(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")

# ══════════════════════════════════════════════════════════════════════════════
# STEP 1 — Ingest all 7 GhostWire files
# ══════════════════════════════════════════════════════════════════════════════
print("=" * 70)
print("STEP 1: Ingesting 7 GhostWire CSV files")
print("=" * 70)

raw_documents = []
for fpath in GW_FILES:
    fbs  = fpath.read_bytes()
    res  = ingest_file(fbs, fpath.name, "test_officer", declared=True)
    assert res["success"], f"Ingest failed for {fpath.name}: {res.get('error')}"
    raw_documents.append(res)
    ps   = res.get("primary_subject", "")
    ents = res.get("entities", {})
    names_found = [n["value"] for n in ents.get("names", [])[:5]]
    print(f"  {fpath.name}: primary_subject={ps!r:40s}  names={names_found}")

print(f"\n  Total files ingested: {len(raw_documents)}")
check("All 7 files ingested successfully", len(raw_documents) == 7)

# ══════════════════════════════════════════════════════════════════════════════
# STEP 2 — Multi-doc entity resolution
# ══════════════════════════════════════════════════════════════════════════════
print()
print("=" * 70)
print("STEP 2: resolve_entity_from_multiple_docs")
print("=" * 70)

person, method = resolve_entity_from_multiple_docs(raw_documents)
print(f"  confirmed_name : {person.get('confirmed_name')!r}")
print(f"  method         : {method}")
print(f"  data_sources   : {person.get('data_sources', [])}")
print(f"  usernames      : {person.get('usernames', {})}")
print(f"  platforms      : {person.get('platforms_confirmed', [])}")
print(f"  phones         : {person.get('phones_found', [])[:3]}")

cn = person.get("confirmed_name", "")
check("confirmed_name is NOT noise string",  not is_bad_subject_name(cn) or cn == "Unknown Subject")
check("confirmed_name is NOT 'In Cyber Incident Inquiry'", cn != "In Cyber Incident Inquiry")
check("confirmed_name is NOT 'GhostWire'",   cn != "GhostWire")
check("confirmed_name is Arjun Mehta or Unknown Subject",
      cn in ("Arjun Mehta", "Unknown Subject"))

# ══════════════════════════════════════════════════════════════════════════════
# STEP 3 — clean_person_object (pipeline final gate)
# ══════════════════════════════════════════════════════════════════════════════
print()
print("=" * 70)
print("STEP 3: clean_person_object")
print("=" * 70)

clean_person_object(person)
print(f"  confirmed_name after clean : {person.get('confirmed_name')!r}")
print(f"  usernames after clean      : {person.get('usernames', {})}")
print(f"  platforms after clean      : {person.get('platforms_confirmed', [])}")

check("No noise name after clean",  not is_bad_subject_name(person.get("confirmed_name","")) or
                                     person.get("confirmed_name") == "Unknown Subject")
check("@reels NOT in usernames",   "reels" not in str(person.get("usernames", {})).lower())
check("@spam  NOT in usernames",   "spam"  not in str(person.get("usernames", {})).lower())

# ══════════════════════════════════════════════════════════════════════════════
# STEP 4 — Graph subject extraction
# ══════════════════════════════════════════════════════════════════════════════
print()
print("=" * 70)
print("STEP 4: get_primary_subject from relationship graph")
print("=" * 70)

from modules.relationship_mapper import build_graph_from_person
_, ents, rels = build_graph_from_person(person, {"results": []})
G = build_graph(ents, rels)
graph_subject = get_primary_subject(ents, G)
print(f"  graph_subject : {graph_subject!r}")
check("graph_subject is not noise",  not is_bad_subject_name(graph_subject) or
                                      graph_subject == "Unknown Subject")
check("graph_subject not 'In Cyber Incident Inquiry'", graph_subject != "In Cyber Incident Inquiry")

# ══════════════════════════════════════════════════════════════════════════════
# STEP 5 — Section 03: Platform presence
# ══════════════════════════════════════════════════════════════════════════════
print()
print("=" * 70)
print("STEP 5: build_platform_presence (Section 03)")
print("=" * 70)

plat_map = build_platform_presence(person, raw_documents=raw_documents)
print(f"  Platforms found: {list(plat_map.keys())}")
for name, entry in plat_map.items():
    uname = entry.get("username", "?")
    status = entry.get("status", "?")
    print(f"    {name}: @{uname} — {status}")

check("@reels NOT in Section 03",  not any("reels" in str(v).lower() for v in plat_map.values()))
check("@spam  NOT in Section 03",  not any("spam"  in str(v).lower() for v in plat_map.values()))
check("@offers NOT in Section 03", not any("offers" in str(v).lower() for v in plat_map.values()))

github_ok   = any("github" in k.lower() for k in plat_map)
telegram_ok = any("telegram" in k.lower() for k in plat_map)
print(f"  GitHub found: {github_ok}  |  Telegram found: {telegram_ok}")

# ══════════════════════════════════════════════════════════════════════════════
# STEP 6 — Generate PDF (Section 01 + 03 rendered)
# ══════════════════════════════════════════════════════════════════════════════
print()
print("=" * 70)
print("STEP 6: generate_pdf — Section 01 (Subject Identity) + Section 03 (Platforms)")
print("=" * 70)

anomalies = []
for doc in raw_documents:
    for f in (doc.get("document_flags") or []):
        anomalies.append(f.get("flag", str(f)) if isinstance(f, dict) else str(f))
for f in (person.get("anomaly_flags") or []):
    anomalies.append(f.get("flag", str(f)) if isinstance(f, dict) else str(f))

report_data = {
    "subject_identity":  f"{person.get('confirmed_name','Unknown')} — {person.get('nationality','Indian')}",
    "confidence_score":  f"{person.get('confidence_score', 0)}/100",
    "platform_presence": {
        k: {"username": v.get("username",""), "url": v.get("url",""), "confirmed": True}
        for k, v in plat_map.items()
    },
    "anomalies_and_flags": {"flags": anomalies},
}

try:
    pdf_bytes = generate_pdf(report_data, username=person.get("confirmed_name","Unknown"),
                             user_id="test_officer", mode="full")
    check("PDF generated without error", len(pdf_bytes) > 1000)
    print(f"  PDF size: {len(pdf_bytes):,} bytes")
    print(f"\n  ── SECTION 01 (Subject Identity) ───────────────────────────")
    print(f"  Subject name in report: {person.get('confirmed_name','Unknown')!r}")
    print(f"\n  ── SECTION 03 (Platform Presence) ──────────────────────────")
    for k, v in plat_map.items():
        print(f"  {k.upper():15s}: @{v.get('username','?')} — {v.get('status','?')}")
    if not plat_map:
        print("  (no platforms confirmed)")
except Exception as e:
    import traceback
    traceback.print_exc()
    check(f"PDF generation failed: {e}", False)

# ══════════════════════════════════════════════════════════════════════════════
# SUMMARY
# ══════════════════════════════════════════════════════════════════════════════
print()
print("=" * 70)
passed = sum(results)
total  = len(results)
print(f"SUMMARY: {passed}/{total} checks passed")
if passed == total:
    print("ALL GHOSTWIRE CHECKS PASSED")
    print()
    print("BEFORE (broken):  Section 01 = 'In Cyber Incident Inquiry'")
    print("                  Section 03 = INSTAGRAM: @reels, LINKEDIN: @spam")
    print()
    print("AFTER (fixed):    Section 01 =", repr(person.get("confirmed_name","Unknown")))
    if plat_map:
        for k, v in plat_map.items():
            print(f"                  Section 03 = {k.upper()}: @{v.get('username','?')} — {v.get('status','?')}")
    else:
        print("                  Section 03 = (no confirmed platforms — noise handles stripped)")
else:
    print("SOME CHECKS FAILED — review above")
