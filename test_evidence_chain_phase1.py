"""
EVIDENCE CHAIN — PHASE 1 TEST (mode shell + ontology via the shared pipeline).

Proves the two things Phase 1 requires:
  1. The mode is registered like the others (nav entry + dispatch + screen fn),
     and NO duplicate ingestion path was added (the old inline per-file helper is
     gone; there is exactly one files->ontology entry point).
  2. Selecting the mode and running files yields a typed ontology IDENTICAL to
     what Fusion / the report pipeline builds for the same files — proving one
     shared pipeline. Both Fusion (screen_fusion) and Evidence Chain call
     modules.case_pipeline.build_case_ontology, which builds the typed ontology
     via report_generator.build_typed_ontology — the exact function the report
     pipeline uses. So the ontology in the Evidence Chain bundle equals the one
     the report path builds from the same pipeline outputs, field for field.

No LLM, no network (mirrors run_case_regression's LLM-off recipe). No commit.
"""

import sys, os, re, pathlib, tempfile
from unittest import mock

REPO = r"C:\Users\maste\OneDrive\Desktop\aetherlens"
sys.path.insert(0, REPO)

# ── LLM / paid-call OFF (apply before importing pipeline modules) ─────────────
mock.patch("modules.entity_resolution._call_gemini", return_value="").start()
mock.patch("modules.entity_resolution._call_bedrock_for_fusion", return_value="").start()
import config
config.bedrock_client = None
config.get_bedrock_client = lambda: (None, "")
config.GEMINI_API_KEY = ""
_TMP = pathlib.Path(tempfile.mkdtemp(prefix="ec_p1_"))
config.DATABASE_PATH = _TMP / "x.db"
config.DATABASE_DIR = _TMP
config.EXPORTS_DIR = _TMP
import modules.report_generator
mock.patch.object(modules.report_generator, "_call_gemini_report", return_value=None).start()

from modules.case_pipeline import build_case_ontology
from modules.report_generator import build_typed_ontology

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail and not ok else ""))


# ── 1. Mode registration + single-pipeline structure (source-level, no app import) ──
_app_src = pathlib.Path(REPO, "app.py").read_text(encoding="utf-8")
check("nav entry registered: ('evidence_chain', 'EVIDENCE CHAIN') in ops_nav",
      '("evidence_chain",    "EVIDENCE CHAIN")' in _app_src
      or re.search(r'\(\s*"evidence_chain"\s*,\s*"EVIDENCE CHAIN"\s*\)', _app_src) is not None)
check("dispatch routes to screen_evidence_chain",
      'screen == "evidence_chain"' in _app_src and "screen_evidence_chain()" in _app_src)
check("screen function defined", "def screen_evidence_chain():" in _app_src)
check("NO duplicate ingestion path: old inline _process_single_file removed from app.py",
      "def _process_single_file(" not in _app_src)
check("app.py points to the shared pipeline",
      "modules.case_pipeline" in _app_src and "build_case_ontology" in _app_src)

_cp_src = pathlib.Path(REPO, "modules", "case_pipeline.py").read_text(encoding="utf-8")
check("single files->ontology entry point exists (build_case_ontology)",
      "def build_case_ontology(" in _cp_src)
check("shared pipeline builds the ontology via the report's build_typed_ontology",
      "build_typed_ontology" in _cp_src)


# ── 2. Ontology matches what the report/Fusion path builds for the same files ──
FILES = sorted(pathlib.Path(REPO, "test_data", "ghostwire").glob("GHOSTWIRE_*"))
staged = [{"name": p.name, "bytes": p.read_bytes()} for p in FILES]

bundle = build_case_ontology(staged, uid="ec_test", declared=True)
onto_ec = bundle.get("ontology")

check("Evidence Chain bundle ingested the case (>=1 raw document)",
      len(bundle.get("raw_documents") or []) >= 1,
      f"raw_documents={len(bundle.get('raw_documents') or [])}")
check("shared pipeline resolved a subject",
      bool(bundle.get("subject")) or bool((bundle.get("person") or {}).get("confirmed_name")),
      f"subject={bundle.get('subject')!r}")
check("typed ontology produced by the shared pipeline", onto_ec is not None
      and hasattr(onto_ec, "counts"))

# Rebuild the typed ontology through the EXACT report-pipeline function, from the
# SAME bundle inputs Fusion would pass — the two must be identical.
onto_report = build_typed_ontology(
    bundle["person"], graph_data=bundle["graph_data"],
    timeline_data=bundle["timeline_data"], behavioral_data=bundle["behavioral_data"],
    raw_documents=bundle["raw_documents"], assets_data=None)

ec_counts = onto_ec.counts() if onto_ec is not None else {}
rp_counts = onto_report.counts() if onto_report is not None else {}
check("ontology population IDENTICAL to the report/Fusion path (counts match)",
      ec_counts == rp_counts,
      f"ec={ec_counts} report={rp_counts}")
check("ontology subject IDENTICAL to the report/Fusion path",
      getattr(onto_ec, "subject_name", None) == getattr(onto_report, "subject_name", None),
      f"ec={getattr(onto_ec,'subject_name',None)!r} report={getattr(onto_report,'subject_name',None)!r}")
check("ontology flag stream IDENTICAL to the report/Fusion path",
      list(getattr(onto_ec, "flags", [])) == list(getattr(onto_report, "flags", [])))

# Determinism: same files -> same ontology population on a second run.
bundle2 = build_case_ontology(staged, uid="ec_test", declared=True)
check("deterministic: same files -> same ontology counts on re-run",
      bundle2["ontology"].counts() == ec_counts)

import shutil
shutil.rmtree(_TMP, ignore_errors=True)

print("\n" + "=" * 70)
npass = sum(1 for _, ok, _ in RESULTS if ok)
print(f"SUMMARY: {npass}/{len(RESULTS)} checks passed")
print("EVIDENCE CHAIN PHASE 1: " + ("ALL CHECKS PASSED" if npass == len(RESULTS)
                                    else "FAILURES PRESENT"))
print("=" * 70)
sys.exit(0 if npass == len(RESULTS) else 1)
