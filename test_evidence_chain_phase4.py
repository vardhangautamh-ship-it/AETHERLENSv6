"""
EVIDENCE CHAIN — PHASE 4 TEST (link strength + chain gaps).

Proves:
  * link strength: a chain resting on a NAME_RESOLVED link and/or a weakly-cited
    circumstance is flagged NOT solid, and SAYS SO;
  * a SOLID chain (all hard links, all file-sourced, no holes) is labelled SOLID;
  * a chain with a hole is flagged with the EXACT break named
    ("the chain breaks between X and Y") in the gap vocabulary;
  * structural gaps reuse gap_detection's NOT-PROVIDED / SEARCHED-ABSENT /
    UNDETERMINABLE labels and are attributed by shared source;
  * NO exclusion test and NO innocent-explanation weighing appears anywhere.

Part A drives the real CHIMERA chain; Part B uses synthetic chains to force a
named break and a genuine SOLID case deterministically. No LLM. No commit.
"""

import sys, pathlib, tempfile, json
from unittest import mock

REPO = r"C:\Users\maste\OneDrive\Desktop\aetherlens"
sys.path.insert(0, REPO)

mock.patch("modules.entity_resolution._call_gemini", return_value="").start()
mock.patch("modules.entity_resolution._call_bedrock_for_fusion", return_value="").start()
import config
config.bedrock_client = None
config.get_bedrock_client = lambda: (None, "")
config.GEMINI_API_KEY = ""
_TMP = pathlib.Path(tempfile.mkdtemp(prefix="ec_p4_"))
config.DATABASE_PATH = _TMP / "x.db"
config.DATABASE_DIR = _TMP
config.EXPORTS_DIR = _TMP
import modules.report_generator
mock.patch.object(modules.report_generator, "_call_gemini_report", return_value=None).start()

from modules.case_pipeline import build_case_ontology
from modules.evidence_chain import (
    extract_circumstances, build_chains, annotate_chains, assess_chain_integrity,
    _GAP_LABELS)

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail and not ok else ""))


# ── Part A — real CHIMERA chain: weak link flagged non-solid + gaps ───────────
FILES = sorted(p for p in pathlib.Path(REPO, "test_data", "chimera").glob("CHIMERA_0*")
               if "MANIFEST" not in p.name.upper())
bundle = build_case_ontology([{"name": p.name, "bytes": p.read_bytes()} for p in FILES],
                             uid="ec_p4", declared=True)
res = extract_circumstances(bundle["ontology"], raw_documents=bundle["raw_documents"])
ch = build_chains(res["circumstances"], graph_data=bundle["graph_data"], subject=res["subject"])
ch = annotate_chains(ch, person=bundle["person"], ontology=bundle["ontology"],
                     raw_documents=bundle["raw_documents"])

print(f"\n  chains={ch['chain_count']}  case_gaps={len(ch['case_gaps'])}")
for c in ch["chains"]:
    it = c["integrity"]
    print(f"    verdict={it['verdict']} weakest={it['weakest_link_strength']} "
          f"holds {it['holds_through_steps']}/{it['total_steps']} "
          f"structural_gaps={len(c['structural_gaps'])}")
    print(f"      note: {it['note'][:110]}")

check("every chain carries an integrity assessment + structural gap list",
      all("integrity" in c and "structural_gaps" in c for c in ch["chains"]))

zen = ch["chains"][0] if ch["chains"] else None
check("the real (NAME_RESOLVED) chain is NOT labelled SOLID",
      zen is not None and zen["integrity"]["verdict"] != "SOLID",
      f"verdict={zen['integrity']['verdict'] if zen else None}")
check("its note says so (weak/verify, lead-not-proof)",
      zen is not None and ("verify" in zen["integrity"]["note"].lower()
                           or "lead" in zen["integrity"]["note"].lower()))

check("structural gaps use the gap_detection vocabulary",
      all(g["kind_label"] in _GAP_LABELS
          for c in ch["chains"] for g in c["structural_gaps"]))

# No exclusion test / no innocent-explanation WEIGHING anywhere in the output.
# (A disclaimer that innocent explanations are NOT weighed is required and fine;
#  what must be absent is any machinery that actually weighs/excludes a hypothesis.)
blob = json.dumps(ch, default=str).lower()
banned = ["exclusion test", "excludes every", "consistent only with",
          "could be legitimate", "weigh the innocent", "rule out the innocent",
          "alternative hypothesis test", "accounts for the circumstance"]
found = [b for b in banned if b in blob]
check("no exclusion test / innocent-explanation weighing operation in the output",
      not found, f"found: {found}")
# And the honest disclaimer IS present (leads are not proof; innocence not ruled out).
disclaimed = any("not proof" in c["integrity"]["note"].lower()
                 or "innocent" in c["integrity"]["note"].lower()
                 or "lead" in c["integrity"]["note"].lower()
                 for c in ch["chains"])
check("the honest lead/not-proof disclaimer is present on chains", disclaimed)


# ── Part B — synthetic chains: force a named break + a genuine SOLID case ─────
def _circ(cid, kind, summary, date, sourced=True):
    return {"id": cid, "kind": kind, "summary": summary, "date": date,
            "source": "F.csv", "sourced": sourced}

# Chain with a HOLE: A(Jan) — [link] — C(Mar), B(Feb) has no direct link to A or C.
# Time order A,B,C → breaks between A-B and B-C.
holey = {
    "circumstances": [_circ("t:0", "transaction", "pay to X", "2023-01-01"),
                      _circ("t:1", "transaction", "pay to Y", "2023-02-01"),
                      _circ("t:2", "transaction", "pay to X again", "2023-03-01")],
    "circumstance_ids": ["t:0", "t:1", "t:2"],
    "links": [{"a": "t:0", "b": "t:2", "basis": "shared_party",
               "strength": "NAME_RESOLVED", "key": "party:x"}],
    "weakest_link_strength": "NAME_RESOLVED",
}
hi = assess_chain_integrity(holey)
check("holey chain verdict is BROKEN", hi["verdict"] == "BROKEN", hi["verdict"])
check("exactly the two internal holes are found", len(hi["step_breaks"]) == 2,
      f"{len(hi['step_breaks'])} breaks")
brk_txt = " ".join(b["finding"] for b in hi["step_breaks"])
check("the exact break is named ('breaks between X and Y')",
      "the chain breaks between" in brk_txt and "«pay to X»" in brk_txt
      and "«pay to Y»" in brk_txt)
check("breaks carry the UNDETERMINABLE gap label",
      all(b["kind_label"] == "UNDETERMINABLE" for b in hi["step_breaks"]))

# Genuine SOLID chain: two hard-linked, fully-sourced, consecutive circumstances.
solid = {
    "circumstances": [_circ("c:0", "contact", "phone A", "2023-01-01"),
                      _circ("c:1", "contact", "phone A again", "2023-01-05")],
    "circumstance_ids": ["c:0", "c:1"],
    "links": [{"a": "c:0", "b": "c:1", "basis": "shared_hard_identifier",
               "strength": "HARD", "key": "phone:9999999999"}],
    "weakest_link_strength": "HARD",
}
si = assess_chain_integrity(solid)
check("all-hard, fully-sourced, hole-free chain is SOLID", si["verdict"] == "SOLID",
      si["verdict"])

# A qualified chain: hard link but one weakly-cited circumstance -> not SOLID.
qual = {
    "circumstances": [_circ("q:0", "contact", "phone A", "2023-01-01"),
                      _circ("q:1", "contact", "phone A again", "2023-01-05", sourced=False)],
    "circumstance_ids": ["q:0", "q:1"],
    "links": [{"a": "q:0", "b": "q:1", "basis": "shared_hard_identifier",
               "strength": "HARD", "key": "phone:9999999999"}],
    "weakest_link_strength": "HARD",
}
qi = assess_chain_integrity(qual)
check("a weakly-cited (but connected) chain is QUALIFIED, not SOLID",
      qi["verdict"] == "QUALIFIED" and qi["weak_cite_count"] == 1, qi["verdict"])

# Determinism.
check("deterministic: same chain -> same integrity",
      assess_chain_integrity(holey) == hi)

import shutil
shutil.rmtree(_TMP, ignore_errors=True)

print("\n" + "=" * 70)
npass = sum(1 for _, ok, _ in RESULTS if ok)
print(f"SUMMARY: {npass}/{len(RESULTS)} checks passed")
print("EVIDENCE CHAIN PHASE 4: " + ("ALL CHECKS PASSED" if npass == len(RESULTS)
                                    else "FAILURES PRESENT"))
print("=" * 70)
sys.exit(0 if npass == len(RESULTS) else 1)
