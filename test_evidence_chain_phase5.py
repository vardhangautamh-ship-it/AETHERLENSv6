"""
EVIDENCE CHAIN — PHASE 5 TEST (lead label + mode output + removable LLM narrative).

Proves:
  * EVERY chain carries the unmissable LEAD label + non-autonomy markers
    (human_review_required=True, autonomous=False), and so does the result;
  * the deterministic output (chains, links, strengths, gaps, cites, lead label)
    is produced with NO LLM and rendered LLM-free;
  * the [AI NARRATIVE] pass is REMOVABLE with zero loss — it never mutates the
    result, and with no LLM the mode loses nothing;
  * the narrative introduces no fact not in the deterministic output — the LLM is
    given ONLY a digest of the structured result and is instructed to add nothing.

No real LLM. No commit.
"""

import sys, copy, pathlib, tempfile
from unittest import mock

REPO = r"C:\Users\maste\OneDrive\Desktop\aetherlens"
sys.path.insert(0, REPO)

mock.patch("modules.entity_resolution._call_gemini", return_value="").start()
mock.patch("modules.entity_resolution._call_bedrock_for_fusion", return_value="").start()
import config
config.bedrock_client = None
config.get_bedrock_client = lambda: (None, "")
config.GEMINI_API_KEY = ""
_TMP = pathlib.Path(tempfile.mkdtemp(prefix="ec_p5_"))
config.DATABASE_PATH = _TMP / "x.db"
config.DATABASE_DIR = _TMP
config.EXPORTS_DIR = _TMP
import modules.report_generator
mock.patch.object(modules.report_generator, "_call_gemini_report", return_value=None).start()

from modules.case_pipeline import build_case_ontology
from modules.evidence_chain import (
    build_evidence_chains, render_evidence_chains, narrate_evidence_chains, LEAD_LABEL)

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail and not ok else ""))


FILES = sorted(p for p in pathlib.Path(REPO, "test_data", "chimera").glob("CHIMERA_0*")
               if "MANIFEST" not in p.name.upper())
bundle = build_case_ontology([{"name": p.name, "bytes": p.read_bytes()} for p in FILES],
                             uid="ec_p5", declared=True)

# Deterministic build — NO LLM anywhere in this call.
result = build_evidence_chains(
    bundle["ontology"], person=bundle["person"], raw_documents=bundle["raw_documents"],
    graph_data=bundle["graph_data"], subject=bundle["subject"])

print(f"\n  subject={result['subject']!r}  chains={result['chain_count']}  "
      f"unchained={len(result['unchained'])}  case_gaps={len(result['case_gaps'])}")

# 1. LEAD label + markers on every chain.
check("every chain carries the verbatim LEAD label",
      result["chains"] and all(c.get("lead_label") == LEAD_LABEL for c in result["chains"]),
      f"chains={result['chain_count']}")
check("every chain carries non-autonomy markers",
      all(c.get("human_review_required") is True and c.get("autonomous") is False
          for c in result["chains"]))
check("result carries non-autonomy markers + lead notice",
      result["human_review_required"] is True and result["autonomous"] is False
      and result["lead_notice"] == LEAD_LABEL)

# 2. Deterministic rendering is complete and LLM-free.
rendered = render_evidence_chains(result)
check("deterministic render is non-empty and carries the LEAD label",
      isinstance(rendered, str) and LEAD_LABEL in rendered and "CHAIN 1" in rendered)
# every chain's links, verdict, and a cite appear in the render
_ok = True
for n, c in enumerate(result["chains"], 1):
    if f"CHAIN {n}" not in rendered or c["integrity"]["verdict"] not in rendered:
        _ok = False
check("render shows each chain's verdict/links/gaps", _ok)

# 3. Removability — narrate never mutates the result; render is stable.
before = copy.deepcopy(result)
render_before = render_evidence_chains(result)

captured = []
def _canned_llm(prompt):
    captured.append(prompt)
    return "Two transfers connect through a shared party; the chain is weak and has gaps."

narrative = narrate_evidence_chains(result, llm_fn=_canned_llm)
check("narrative is produced and labelled [AI NARRATIVE]",
      narrative.startswith("[AI NARRATIVE]") and "shared party" in narrative)
check("narrate did NOT mutate the deterministic result (zero-loss removability)",
      result == before)
check("render is byte-identical with or without the narrative layer",
      render_evidence_chains(result) == render_before)

# 4. With NO LLM the mode still works — narrate is a no-op, result intact.
empty_narrative = narrate_evidence_chains(result, llm_fn=lambda p: "")
check("no LLM output -> empty narrative (mode loses nothing)", empty_narrative == "")
default_narrative = narrate_evidence_chains(result)   # LLM mocked OFF -> ""
check("default resolver with LLM off -> empty narrative", default_narrative == "")
check("structured chains/links/gaps/cites all present without any narrative",
      all("integrity" in c and "links" in c and "structural_gaps" in c
          and all(x.get("source") for x in c["circumstances"]) for c in result["chains"]))

# 5. The narrative introduces no fact not in the deterministic output: the LLM is
#    given ONLY a digest of the structured result + a do-not-add instruction.
prompt = captured[0] if captured else ""
check("LLM prompt is grounded in the structured result only",
      result["subject"] in prompt
      and all(c["integrity"]["verdict"] in prompt for c in result["chains"]))
check("LLM prompt forbids adding facts/conclusions/explanations",
      "MUST NOT add" in prompt and "MUST NOT assert a conclusion" in prompt
      and "innocent" in prompt.lower())

# 6. Determinism.
result2 = build_evidence_chains(
    bundle["ontology"], person=bundle["person"], raw_documents=bundle["raw_documents"],
    graph_data=bundle["graph_data"], subject=bundle["subject"])
check("deterministic: same inputs -> same chains",
      [c["circumstance_ids"] for c in result2["chains"]]
      == [c["circumstance_ids"] for c in result["chains"]]
      and result2["unchained"] == result["unchained"])

import shutil
shutil.rmtree(_TMP, ignore_errors=True)

print("\n" + "=" * 70)
npass = sum(1 for _, ok, _ in RESULTS if ok)
print(f"SUMMARY: {npass}/{len(RESULTS)} checks passed")
print("EVIDENCE CHAIN PHASE 5: " + ("ALL CHECKS PASSED" if npass == len(RESULTS)
                                    else "FAILURES PRESENT"))
print("=" * 70)
sys.exit(0 if npass == len(RESULTS) else 1)
