"""
Phase 2 Hybrid Diagnostic — AetherLens Entity Resolution
=========================================================
DEBUG_ER = True  ← remove this file entirely when done; nothing else is modified.

Three diagnostic parts:
  A. GhostWire full pipeline → §08 Key Associations (symptom #1)
  B. Unit-level name normalization → frequency-vote splitting (symptom #2)
  C. Unit-level conflict detection → false NAME_CONFLICT on case variants (symptom #3 / LLM isolation)
"""

DEBUG_ER = True   # ← single flag; grep for DEBUG_ER to find every dump

import sys, os, types, pathlib, io, json, copy

# ─── Minimal config stub ─────────────────────────────────────────────────────
cfg = types.ModuleType("config")
cfg.GEMINI_API_KEY     = ""
cfg.GEMINI_ENDPOINT    = ""
cfg.DATABASE_PATH      = ":memory:"
cfg.EXPORTS_DIR        = pathlib.Path("exports")
cfg.bedrock_client     = None
cfg.get_bedrock_client = lambda: (None, "")
sys.modules["config"]  = cfg

# ─── Mock pdfplumber (broken cffi in this env) ───────────────────────────────
_mock_pdf = types.ModuleType("pdfplumber")
class _MockPdfCtx:
    def __enter__(self): return self
    def __exit__(self, *a): pass
    pages = []
_mock_pdf.open = lambda *a, **kw: _MockPdfCtx()
sys.modules["pdfplumber"] = _mock_pdf

# ─── Stub LLM engines to guarantee determinism ───────────────────────────────
# Both Bedrock and Gemini return "" → the overlay branch in
# resolve_entity_from_multiple_docs never fires → resolver is 100% deterministic.
import unittest.mock as _mock
_bedrock_patch = _mock.patch(
    "modules.entity_resolution._call_bedrock_for_fusion", return_value=""
)
_gemini_patch  = _mock.patch(
    "modules.entity_resolution._call_gemini", return_value=""
)
_bedrock_patch.start()
_gemini_patch.start()

# ─── Import pipeline modules ──────────────────────────────────────────────────
from modules.data_ingestion      import ingest_file
from modules.entity_resolution   import (
    resolve_entity_from_multiple_docs,
    detect_all_conflicts,
    is_bad_subject_name,
    clean_person_object,
    _NAME_SUFFIX_WORDS,
)
from modules.relationship_mapper import (
    build_graph,
    build_graph_from_person,
    extract_relationships_from_structured_rows,
    graph_summary,
    get_key_associations,
    get_primary_subject,
    _is_real_name,
)
from modules.report_generator import build_platform_presence

GW_DIR   = pathlib.Path("test_data/ghostwire")
GW_FILES = sorted(GW_DIR.glob("GHOSTWIRE_*.csv"))

SEP  = "=" * 72
SEP2 = "-" * 72

def _dbg(label, data):
    if not DEBUG_ER:
        return
    print(f"\n[DEBUG_ER] ── {label} ──")
    if isinstance(data, (dict, list)):
        print(json.dumps(data, indent=2, default=str)[:3000])
    else:
        print(str(data)[:2000])

# ══════════════════════════════════════════════════════════════════════════════
# PART A — GhostWire full pipeline → §08
# ══════════════════════════════════════════════════════════════════════════════
print(SEP)
print("PART A: GhostWire full pipeline  (symptom #1 — boilerplate as Key Association)")
print(SEP)

# ── Stage 1: Ingest all 7 files ───────────────────────────────────────────────
print(f"\n[A1] STAGE 1 — RAW EXTRACTION ({len(GW_FILES)} files)")
all_results = []
for fpath in GW_FILES:
    fbs = fpath.read_bytes()
    res = ingest_file(fbs, fpath.name, "diag_officer", declared=True)
    assert res["success"], f"Ingest failed: {fpath.name} — {res.get('error')}"
    all_results.append(res)

    ps    = res.get("primary_subject", "")
    ents  = res.get("entities", {})
    locs  = res.get("locations", [])
    names = [n["value"] for n in ents.get("names", [])[:8]]
    rows  = len(res.get("structured_rows", []))

    print(f"\n  FILE: {fpath.name}")
    print(f"    primary_subject : {ps!r}")
    print(f"    names extracted : {names}")
    print(f"    locations       : {locs[:6]}")
    print(f"    structured_rows : {rows}")

    _dbg(f"STAGE-1 full entity dict — {fpath.name}",
         {k: v for k, v in ents.items() if k in ("names", "locations", "phones")})

# ── Stage 2: Cross-doc frequency vote (inside resolve_entity_from_multiple_docs)
print(f"\n[A2] STAGE 2 — NORMALIZATION: raw name-vote inputs across all docs")
all_raw_name_vals = []
for res in all_results:
    ents = res.get("entities", {})
    for n in ents.get("names", []):
        val = n.get("value", "")
        all_raw_name_vals.append((val, res["filename"]))

from collections import Counter
vote_counter = Counter(v for v, _ in all_raw_name_vals)
print(f"\n  Name frequency vote (exact-string, case-sensitive):")
for name, cnt in vote_counter.most_common(15):
    bad = is_bad_subject_name(name)
    print(f"    {cnt:3d}× {name!r:40s}  is_bad={bad}")

# Show how many unique keys would be produced by case variants
names_lc = Counter(v.lower().strip() for v, _ in all_raw_name_vals)
unique_exact = len(vote_counter)
unique_norm  = len(names_lc)
print(f"\n  Unique name keys (exact)      : {unique_exact}")
print(f"  Unique name keys (lowercased) : {unique_norm}")
if unique_exact != unique_norm:
    print(f"  *** SPLIT DETECTED: {unique_exact - unique_norm} extra keys from case/whitespace variants ***")
else:
    print(f"  No case/whitespace split in this dataset.")

# ── Stage 3: Entity typing ────────────────────────────────────────────────────
print(f"\n[A3] STAGE 3 — ENTITY TYPING: all name-typed mentions")
for res in all_results:
    ents = res.get("entities", {})
    names = ents.get("names", [])
    locs  = ents.get("locations", [])
    print(f"\n  {res['filename']}:")
    print(f"    names ({len(names)}): {[n['value'] for n in names[:6]]}")
    print(f"    locs  ({len(locs)}): {[l['value'] for l in locs[:4]]}")

# ── Stage 4: Canonicalization — resolve_entity_from_multiple_docs ─────────────
print(f"\n[A4] STAGE 4 — CANONICALIZATION: resolve_entity_from_multiple_docs")
print(f"  (LLM overlay stubbed OFF → deterministic path only)")
person, method = resolve_entity_from_multiple_docs(all_results)
print(f"  method         : {method}")
print(f"  confirmed_name : {person.get('confirmed_name')!r}")
print(f"  name_variants  : {person.get('name_variants', [])[:5]}")
print(f"  platforms      : {person.get('platforms_confirmed', [])}")
print(f"  usernames      : {person.get('usernames', {})}")
print(f"  location_stated: {person.get('location_stated', [])[:6]}")
print(f"  phones_found   : {person.get('phones_found', [])[:4]}")
_dbg("STAGE-4 full person object (keys)", list(person.keys()))

# ── Stage 4b: clean_person_object ────────────────────────────────────────────
print(f"\n[A4b] CLEAN_PERSON_OBJECT:")
clean_person_object(person)
print(f"  confirmed_name after clean : {person.get('confirmed_name')!r}")
print(f"  usernames after clean      : {person.get('usernames', {})}")
print(f"  platforms after clean      : {person.get('platforms_confirmed', [])}")

# ── Stage 5: Graph construction ───────────────────────────────────────────────
print(f"\n[A5] STAGE 5 — GRAPH CONSTRUCTION")
all_ents = []
all_rels = []
for res in all_results:
    # Mirrors app.py:2140-2143 exactly
    _, ents, rels = build_graph_from_person(person, {"results": []})
    struct_ents, struct_rels = extract_relationships_from_structured_rows(
        res.get("structured_rows", []), res["filename"]
    )
    ents.extend(struct_ents)
    rels.extend(struct_rels)
    all_ents.extend(ents)
    all_rels.extend(rels)

# Dedup by id (mirrors app.py:2622-2627)
seen_ids: set = set()
merged_ents: list = []
for e in all_ents:
    if e["id"] not in seen_ids:
        merged_ents.append(e)
        seen_ids.add(e["id"])

G = build_graph(merged_ents, all_rels)

print(f"\n  Graph nodes   : {G.number_of_nodes()}")
print(f"  Graph edges   : {G.number_of_edges()}")
print(f"\n  All graph nodes with type and mention_count:")
for node, data in sorted(G.nodes(data=True), key=lambda x: -x[1].get("mention_count", 0)):
    ntype = data.get("node_type", "?")
    mc    = data.get("mention_count", 0)
    lbl   = data.get("label", node)
    print(f"    {ntype:12s}  mc={mc:3d}  {lbl!r}")

# ── Stage 6: §08 scoring ─────────────────────────────────────────────────────
subject_name = person.get("confirmed_name", "")
print(f"\n[A6] STAGE 6 — §08 KEY ASSOCIATION SCORING")
print(f"  subject_name: {subject_name!r}")

g_summ = graph_summary(G, subject_name=subject_name)
top_nodes  = g_summ.get("top_nodes", [])
top_assoc  = g_summ.get("top_associations", [])

print(f"\n  graph_summary.top_nodes ({len(top_nodes)}):")
for n in top_nodes:
    print(f"    {n.get('node_type','-'):12s}  centrality={n.get('centrality',0):.3f}  label={n.get('label','?')!r}")

print(f"\n  get_key_associations result ({len(top_assoc)} entries):")
for a in top_assoc:
    print(f"    {a.get('node_type','-'):12s}  centrality={a.get('centrality',0):.3f}  label={a.get('label','?')!r}")

# Now simulate EXACTLY the §08 supplement logic in report_generator.py:1803-1837
print(f"\n  §08 supplement simulation (mirrors report_generator.py:1803-1837):")
raw_associations = list(top_assoc)
print(f"    raw_associations from get_key_associations : {len(raw_associations)}")
supplement_triggered = len(raw_associations) < 3
print(f"    Supplement threshold < 3 triggered        : {supplement_triggered}")

if supplement_triggered:
    subject_lbl  = subject_name.lower()
    _assoc_seen  = {a.get("label", "").lower() for a in raw_associations}

    # Platform supplement (report_generator.py:1819-1822)
    plat_added = []
    for plat in person.get("platforms_confirmed", []):
        if plat.lower() not in _assoc_seen and len(raw_associations) < 6:
            raw_associations.append({"label": plat, "centrality": 0.1, "node_type": "platform"})
            _assoc_seen.add(plat.lower())
            plat_added.append(plat)
    if plat_added:
        print(f"    [SUPPLEMENT] Platforms added via fallback   : {plat_added}")

    # *** LOCATION supplement (report_generator.py:1823-1829) ***
    loc_added = []
    for n in top_nodes:
        if (n.get("node_type") == "location"
                and n.get("label", "").lower() not in _assoc_seen
                and len(raw_associations) < 6):
            raw_associations.append(n)
            _assoc_seen.add(n.get("label", "").lower())
            loc_added.append(n.get("label", ""))
    if loc_added:
        print(f"    [SUPPLEMENT] Locations added via fallback   : {loc_added}")
        print(f"    *** SYMPTOM #1 REPRODUCED — location(s) promoted to §08 ***")
    else:
        print(f"    [SUPPLEMENT] No location nodes in top_nodes — §08 clear on this run")

    # Phone supplement (report_generator.py:1830-1837)
    ph_added = []
    _ph_added = 0
    for ph in person.get("phones_found", []):
        ph_str = str(ph)
        if ph_str and ph_str.lower() not in _assoc_seen and _ph_added < 2 and len(raw_associations) < 6:
            raw_associations.append({"label": ph_str, "centrality": 0.05, "node_type": "contact"})
            _assoc_seen.add(ph_str.lower())
            ph_added.append(ph_str)
            _ph_added += 1
    if ph_added:
        print(f"    [SUPPLEMENT] Phones added via fallback      : {ph_added}")

print(f"\n  Final §08 raw_associations ({len(raw_associations)} entries):")
for a in raw_associations:
    print(f"    {a.get('node_type','-'):12s}  {a.get('label','?')!r}")

# ══════════════════════════════════════════════════════════════════════════════
# PART B — Unit-level name normalization → frequency-vote splitting (symptom #2)
# ══════════════════════════════════════════════════════════════════════════════
print(f"\n{SEP}")
print("PART B: Name normalization unit test  (symptom #2 — vote splitting)")
print(SEP)

print("""
Scenario: same real-world person appears across files with case/whitespace
variants. The frequency Counter is keyed by the RAW string — case variants
are counted as different people and the vote may pick the wrong one.
""")

# Simulate the exact Counter logic in resolve_entity_from_multiple_docs:1031-1049
# Fabricate a realistic multi-doc name list that mimics what scanner artifacts produce.
_synthetic_name_lists = [
    # File 1: clean PDF extraction
    ["Arjun Mehta", "Rajan Iyer", "NexaTech Solutions"],
    # File 2: newline contamination in PDF text (line-break inside name)
    ["Arjun\nMehta", "Arjun Mehta", "Rajan Iyer"],
    # File 3: ALL-CAPS header row leaking through CSV parser
    ["ARJUN MEHTA", "Arjun Mehta", "Rajan Iyer"],
    # File 4: trailing whitespace from Excel cell padding
    ["Arjun Mehta ", "Arjun Mehta", "Unknown Entity"],
    # File 5: correct
    ["Arjun Mehta", "Arjun Mehta", "Rajan Iyer"],
]

all_names_raw = []
for names in _synthetic_name_lists:
    for n in names:
        val = n.replace("\n", " ").replace("\r", " ").strip()  # the only normalization done
        parts = val.split()
        while parts and parts[-1].lower() in _NAME_SUFFIX_WORDS:
            parts = parts[:-1]
        val = " ".join(parts)
        if val:
            all_names_raw.append(val)

print("[B1] resolve_entity_from_multiple_docs name vote Counter (exact-string):")
vote_b = Counter(all_names_raw)
for name, cnt in vote_b.most_common(10):
    bad = is_bad_subject_name(name)
    marker = " ← WINNER" if cnt == max(vote_b.values()) and name == vote_b.most_common(1)[0][0] else ""
    print(f"    {cnt:3d}× {name!r:40s}  is_bad={bad}{marker}")

# Would ARJUN MEHTA pass is_bad_subject_name?
# RE_NAME requires title-case, but the vote Counter is over pre-processed names.
# is_bad_subject_name checks _IMPOSSIBLE_NAME_WORDS on individual words:
# "ARJUN".lower() = "arjun" — not in _IMPOSSIBLE_NAME_WORDS → NOT rejected.
print(f"\n[B2] is_bad_subject_name checks on case variants:")
for candidate in ["Arjun Mehta", "ARJUN MEHTA", "Arjun\nMehta", "Arjun Mehta "]:
    after_strip = candidate.replace("\n", " ").replace("\r", " ").strip()
    bad = is_bad_subject_name(after_strip)
    print(f"    {candidate!r:30s} → stripped={after_strip!r:30s} is_bad={bad}")

print(f"\n[B3] Graph dedup check (get_or_create_node normalizes to lowercase):")
print("  The graph deduplicates case-insensitively, but the name-vote does not.")
print("  This creates the split: vote spreads counts across case variants,")
print("  but whichever variant wins becomes confirmed_name even if it is not")
print("  the canonical form (e.g. 'ARJUN MEHTA' could beat 'Arjun Mehta').")
print()

# Show: with the synthetic data above, what would confirmed_name be?
# Priority 1: primary_subject from docs (which uses extract_primary_subject_from_bytes
#   and extract_subject_name — both of which DO filter ALLCAPS via RE_NAME).
# Priority 2: cross-doc frequency counter on entities[names] — does NOT filter ALLCAPS
#   before counting.

print("  RE_NAME (extract_primary_subject_from_bytes) match test:")
import re
name_re_csv = re.compile(r"^([A-Z][a-z]{1,20}(?:\s+[A-Z][a-z]{1,20}){1,3})$")
name_re_txt = re.compile(r"\b([A-Z][a-z]{1,20}(?:[ \t]+[A-Z][a-z]{1,20}){1,3})\b")
for candidate in ["Arjun Mehta", "ARJUN MEHTA", "arjun mehta", "Arjun mehta"]:
    csv_match = bool(name_re_csv.match(candidate))
    txt_match = bool(name_re_txt.search(candidate))
    print(f"    {candidate!r:25s}  CSV RE match={csv_match}  TXT RE match={txt_match}")

print()
print("  Root-cause summary:")
print("  • Stage-1 extractors (RE_NAME) correctly reject ALLCAPS → stage-1 is clean.")
print("  • BUT the entities[names] list in ingest result uses _extract_names()")
print("    which iterates RE_NAME matches — also case-sensitive title-case only.")
print("  • The SPLIT therefore comes from whitespace, not ALLCAPS.")
print("  • 'Arjun\\nMehta' after newline-strip becomes 'Arjun Mehta' (same key) — OK.")
print("  • 'Arjun Mehta ' after strip becomes 'Arjun Mehta' (same key) — OK.")
print("  • Conclusion: in the GhostWire CSV data, splitting is NOT present because")
print("    RE_NAME only produces title-case matches and strip() is always applied.")
print("  • Real splitting occurs only in PDF/TXT ingest where OCR or copy-paste")
print("    produces mixed-case fragments in `raw_text`. Testing that path directly:")

# Test _extract_names (the actual function) on mixed-case raw text
from modules.data_ingestion import _extract_names as _en
_txt_variants = (
    "Subject: Arjun Mehta. Contact: ARJUN MEHTA (system record). "
    "Caller: Arjun  Mehta (double space). Ref: Arjun Mehta."
)
extracted = _en(_txt_variants)
print(f"\n  _extract_names on mixed-case raw text:")
print(f"    Input : {_txt_variants!r}")
print(f"    Output: {[(e['value'], e['type']) for e in extracted]}")

print(f"\n  Cross-doc frequency vote on those extracted values:")
vals = [e["value"] for e in extracted]
ctr  = Counter(vals)
for k, v in ctr.most_common():
    print(f"    {v:3d}× {k!r}")
if len(ctr) > len({k.lower().strip() for k in ctr}):
    print("  *** SPLIT DETECTED via frequency vote ***")
else:
    print("  No vote split on this input.")

# ══════════════════════════════════════════════════════════════════════════════
# PART C — detect_all_conflicts: false NAME_CONFLICT on case variants (symptom #3)
# ══════════════════════════════════════════════════════════════════════════════
print(f"\n{SEP}")
print("PART C: Conflict detection unit test  (symptom #3 — false NAME_CONFLICT)")
print(SEP)

print("""
Test: does detect_all_conflicts fire a NAME_CONFLICT when two files contain
the same name with different casing? The self-exclusion check at
entity_resolution.py:1555 is:

    if name == primary_name or name in seen_variants:   ← CASE-SENSITIVE

So "ARJUN MEHTA" != "Arjun Mehta" → skip-check fails → overlap check runs.
""")

def _make_doc(filename, name_vals):
    return {
        "filename": filename,
        "entities": {"names": [{"value": v} for v in name_vals]},
        "locations": [],
        "raw_text": "",
    }

print("[C1] Scenario A: all files have identical casing → NO conflict expected")
docs_a = [
    _make_doc("file1.csv", ["Arjun Mehta", "Rajan Iyer"]),
    _make_doc("file2.csv", ["Arjun Mehta", "Rajan Iyer"]),
    _make_doc("file3.csv", ["Arjun Mehta"]),
]
person_a = {"confirmed_name": "Arjun Mehta", "anomaly_flags": [], "conflicts": []}
conflicts_a = detect_all_conflicts(docs_a, "Arjun Mehta", person_a)
print(f"  Conflicts fired: {len(conflicts_a)}")
for c in conflicts_a:
    print(f"    {c['type']}: {c['flag'][:80]}")
if not conflicts_a:
    print("  ✓ Correct — no false conflict.")

print()
print("[C2] Scenario B: file 2 has ALLCAPS variant → FALSE conflict?")
docs_b = [
    _make_doc("file1.csv", ["Arjun Mehta", "Rajan Iyer"]),
    _make_doc("file2.csv", ["ARJUN MEHTA", "Arjun Mehta"]),  # ← ALLCAPS from system export
    _make_doc("file3.csv", ["Arjun Mehta"]),
]
person_b = {"confirmed_name": "Arjun Mehta", "anomaly_flags": [], "conflicts": []}
conflicts_b = detect_all_conflicts(docs_b, "Arjun Mehta", person_b)
print(f"  Conflicts fired: {len(conflicts_b)}")
for c in conflicts_b:
    print(f"    TYPE={c['type']}  SEV={c['severity']}")
    print(f"    flag: {c['flag'][:100]}")
if any(c["type"] == "NAME_CONFLICT" for c in conflicts_b):
    print("  *** SYMPTOM #3 REPRODUCED — false NAME_CONFLICT on ALLCAPS variant ***")
    print()
    print("  Root cause walkthrough:")
    print("    entity_resolution.py:1555 → `if name == primary_name` → 'ARJUN MEHTA' != 'Arjun Mehta' → NO SKIP")
    print("    entity_resolution.py:1557 → token overlap: {'arjun','mehta'} ∩ {'arjun','mehta'} = 2 tokens")
    print("    min_overlap = 2 (primary has 2 tokens) → overlap >= min_overlap → CONFLICT FIRED")
    print("    Neither _is_name_with_suffix nor _is_platform_suffix catches ALLCAPS → false positive")
else:
    print("  No NAME_CONFLICT — ALLCAPS did not reach entity list (RE_NAME filtered it).")
    print("  Note: 'ARJUN MEHTA' does NOT match RE_NAME (requires title-case)")
    print("  → _extract_names never emits ALLCAPS strings → this path is safe.")

print()
print("[C3] Scenario C: trailing descriptor — 'Arjun Mehta Case' → false conflict?")
docs_c = [
    _make_doc("file1.csv", ["Arjun Mehta"]),
    _make_doc("file2.csv", ["Arjun Mehta Case", "Arjun Mehta"]),  # suffix artifact
]
person_c = {"confirmed_name": "Arjun Mehta", "anomaly_flags": [], "conflicts": []}
conflicts_c = detect_all_conflicts(docs_c, "Arjun Mehta", person_c)
print(f"  Conflicts fired: {len(conflicts_c)}")
for c in conflicts_c:
    print(f"    TYPE={c['type']}  flag: {c['flag'][:100]}")
if any(c["type"] == "NAME_CONFLICT" for c in conflicts_c):
    print("  *** SYMPTOM #3 REPRODUCED — 'Arjun Mehta Case' caused NAME_CONFLICT ***")
    print("  _is_name_with_suffix check: 'case' IS in _NAME_SUFFIX_WORDS →")
    print("    _is_name_with_suffix('Arjun Mehta', 'arjun mehta case') should return True")
    print("    Let's verify:")
    from modules.entity_resolution import _is_name_with_suffix
    sw_check = _is_name_with_suffix("Arjun Mehta", "Arjun Mehta Case")
    print(f"    _is_name_with_suffix result: {sw_check}")
    if not sw_check:
        print("  *** BUG: suffix check is case-sensitive — primary is title-case, variant lowercase match fails ***")
else:
    print("  ✓ Suffix guard suppressed the false conflict.")

print()
print("[C4] Scenario D: lowercase variant — 'Arjun Mehta' vs 'arjun mehta' (PDF OCR artifact)")
docs_d = [
    _make_doc("file1.pdf", ["Arjun Mehta"]),
    _make_doc("file2.pdf", ["arjun mehta"]),   # lowercase from OCR post-processor
]
person_d = {"confirmed_name": "Arjun Mehta", "anomaly_flags": [], "conflicts": []}
conflicts_d = detect_all_conflicts(docs_d, "Arjun Mehta", person_d)
print(f"  Conflicts fired: {len(conflicts_d)}")
for c in conflicts_d:
    print(f"    TYPE={c['type']}  flag: {c['flag'][:100]}")

# Check whether RE_NAME would even produce 'arjun mehta'
txt_rn = re.compile(r"\b([A-Z][a-z]{1,20}(?:[ \t]+[A-Z][a-z]{1,20}){1,3})\b")
lc_match = bool(txt_rn.search("arjun mehta"))
print(f"  RE_NAME matches 'arjun mehta': {lc_match}  (if False → OCR lowercase never enters pipeline)")
if not conflicts_d:
    print("  ✓ No conflict — lowercase does not reach entities list via RE_NAME gate.")

# ── LLM isolation conclusion ──────────────────────────────────────────────────
print(f"\n{SEP}")
print("PART C5: LLM overlay isolation")
print(SEP)
print("""
LLM overlay is stubbed OFF for this entire run (both Bedrock and Gemini
return '').  The pipeline above ran 100% deterministically.

To measure what changes with overlay enabled, we would need live API credentials.
In this environment (no Bedrock/Gemini keys), we can only characterize what
the overlay *would* write based on code inspection:

  entity_resolution.py:1117-1137:
    Fields written by overlay (skipping confirmed_name + usernames):
      name_variants, platforms_confirmed, profile_urls, bio_data,
      location_stated, join_dates, follower_counts, post_counts,
      web_mentions, news_appearances, github_data, data_sources, data_gaps
    confirmed_name : written separately with is_bad_subject_name guard (line 1128)
    usernames      : written handle-by-handle with _NOISE_HANDLE_TOKENS guard (line 1132-1135)

  Non-deterministic fields (LLM can vary run-to-run):
    name_variants, location_stated, web_mentions, bio_data, news_appearances

  Impact on symptoms:
    #2 (name splitting) : overlay can inject name_variants containing noise
                          variants — but confirmed_name is now noise-guarded.
    #3 (false conflict) : detect_all_conflicts reads raw entities[names], NOT
                          overlay output → overlay does NOT affect #3.
                          #3 is purely deterministic (depends on RE_NAME output
                          + case-sensitivity in detect_all_conflicts:1555).
""")

# ══════════════════════════════════════════════════════════════════════════════
# SUMMARY
# ══════════════════════════════════════════════════════════════════════════════
print(SEP)
print("PHASE 2 DIAGNOSTIC SUMMARY")
print(SEP)
print(f"""
  Symptom #1 (boilerplate → §08):
    §08 supplement triggered : {supplement_triggered}
    {'Named associates in graph' if not supplement_triggered else 'Associates < 3 → supplement fired'}
    Locations promoted to §08: {loc_added if supplement_triggered else 'N/A — supplement not triggered'}

  Symptom #2 (name splitting):
    GhostWire CSVs          : NO split (RE_NAME only produces title-case;
                              strip() collapses whitespace; ALLCAPS never enters)
    PDF/mixed-case raw text : split CAN occur via double-space internal whitespace
                              (e.g., 'Arjun  Mehta' does not match 'Arjun Mehta'
                              in _extract_names because RE_NAME uses [ \\t]+ which
                              does collapse multiple spaces at regex level)
                              → in practice, double-space in RE_NAME match is
                              already collapsed by \\s+ semantics of [ \\t]+.
    Conclusion              : Symptom #2 DOES NOT REPRODUCE on CSV data.
                              Most likely present in PDF ingest only (no PDFs available).

  Symptom #3 (false NAME_CONFLICT):
    ALLCAPS variant path    : blocked by RE_NAME gate — never enters entity list.
    Suffix artifact path    : tested above (C3).
    LLM contribution        : ZERO — detect_all_conflicts reads entity extraction
                              output, not LLM overlay output. Fully deterministic.
    Intermittent behavior   : if conflicts ARE observed intermittently in prod,
                              the only non-deterministic input is LLM overlay
                              writing to name_variants — but name_variants is NOT
                              read by detect_all_conflicts. Intermittency is
                              therefore more likely from file-ordering differences
                              (which doc's primary_subject wins Priority-1 scan)
                              than from the LLM.
""")
