"""
EVIDENCE CHAIN — PHASE 3 TEST (timeline chaining).

On CHIMERA proves:
  * chains form ONLY on real typed connections (shared strong phone / shared
    party / a non-location typed edge) — every link is justified by re-derivation;
  * NO coincidental links — never on a location/city, never on a landline, never
    on the subject-star, never on temporal proximity alone;
  * dates are source-precision (ISO or empty), never imputed;
  * every link is cited to both circumstances' sources;
  * unconnected circumstances stay unchained (chains + unchained partition all);
  * deterministic.

No LLM, no network. No commit.
"""

import sys, re, pathlib, tempfile
from unittest import mock

REPO = r"C:\Users\maste\OneDrive\Desktop\aetherlens"
sys.path.insert(0, REPO)

mock.patch("modules.entity_resolution._call_gemini", return_value="").start()
mock.patch("modules.entity_resolution._call_bedrock_for_fusion", return_value="").start()
import config
config.bedrock_client = None
config.get_bedrock_client = lambda: (None, "")
config.GEMINI_API_KEY = ""
_TMP = pathlib.Path(tempfile.mkdtemp(prefix="ec_p3_"))
config.DATABASE_PATH = _TMP / "x.db"
config.DATABASE_DIR = _TMP
config.EXPORTS_DIR = _TMP
import modules.report_generator
mock.patch.object(modules.report_generator, "_call_gemini_report", return_value=None).start()

from modules.case_pipeline import build_case_ontology
from modules.evidence_chain import (
    extract_circumstances, build_chains, link_keys, _typed_edges, _LINK_BASES)
from modules.sanitizer import normalize_name_key, phone_key

RESULTS = []
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail and not ok else ""))


FILES = sorted(p for p in pathlib.Path(REPO, "test_data", "chimera").glob("CHIMERA_0*")
               if "MANIFEST" not in p.name.upper())
staged = [{"name": p.name, "bytes": p.read_bytes()} for p in FILES]

bundle = build_case_ontology(staged, uid="ec_p3", declared=True)
graph_data = bundle["graph_data"]
res = extract_circumstances(bundle["ontology"], raw_documents=bundle["raw_documents"])
circ = res["circumstances"]
circ_by_id = {c["id"]: c for c in circ}
subject = res["subject"]
subject_key = normalize_name_key(subject)

ch = build_chains(circ, graph_data=graph_data, subject=subject)
chains = ch["chains"]
all_links = [l for c in chains for l in c["links"]]

print(f"\n  subject={subject!r}  circumstances={len(circ)}")
print(f"  chains={ch['chain_count']}  links={ch['link_count']}  unchained={len(ch['unchained'])}")
for c in chains[:6]:
    print(f"    chain size={c['size']} weakest={c['weakest_link_strength']} "
          f"parties={c['parties']} {c['first_date']}..{c['last_date']}")

check("at least one candidate chain formed", ch["chain_count"] >= 1,
      f"chains={ch['chain_count']}")

check("every link has a recognised typed basis + a key",
      all(l["basis"] in _LINK_BASES and l["key"] for l in all_links))

# Every link is justified by a re-derived typed connection (never coincidence).
tedges = _typed_edges(graph_data, subject_key)
def _justified(l):
    ka = {k for k, _ in link_keys(circ_by_id[l["a"]], subject_key)}
    kb = {k for k, _ in link_keys(circ_by_id[l["b"]], subject_key)}
    if ka & kb:
        return True
    return any(frozenset({a, b}) in tedges for a in ka for b in kb if a != b)
unjustified = [l for l in all_links if not _justified(l)]
check("every link is justified by a shared typed connection (no coincidence)",
      not unjustified, f"{len(unjustified)} unjustified")

# NEVER on a location/city — presence circumstances are never link endpoints, and
# no link key is a location token.
presence_ids = {c["id"] for c in circ if c["kind"] == "presence"}
loc_endpoint = [l for l in all_links if l["a"] in presence_ids or l["b"] in presence_ids]
check("no link touches a location/presence circumstance (never link on city)",
      not loc_endpoint, f"{len(loc_endpoint)} location links")

# NEVER on a landline — CHIMERA's shared office landline must not anchor a link.
LANDLINE = "+91-22-24450010"
ll_key = f"phone:{phone_key(LANDLINE)}"
ll_links = [l for l in all_links if l["key"] == ll_key]
check("landline never anchors a link (shared-office number excluded)",
      not ll_links, f"{len(ll_links)} landline links; key={ll_key}")

# NEVER the subject-star.
subj_links = [l for l in all_links if l["key"] == f"party:{subject_key}"]
check("subject is never the linking key (no trivial subject-star)", not subj_links,
      f"{len(subj_links)} subject links")

# Dates in chains are source-precision (ISO) or empty — never imputed.
bad_date = [c for ch1 in chains for c in ch1["circumstances"]
            if c["date"] and not _ISO.match(c["date"])]
check("chain circumstance dates are ISO or empty (no imputed dates)", not bad_date,
      f"{len(bad_date)} bad")

# Every link is cited to both circumstances' sources.
uncited = [l for l in all_links
           if len(l["citations"]) != 2 or not all(c["source"] for c in l["citations"])]
check("every link is cited to both circumstances", not uncited, f"{len(uncited)} uncited")

# Unconnected circumstances stay unchained; chains + unchained partition all.
chained_ids = {cid for c in chains for cid in c["circumstance_ids"]}
unchained_ids = set(ch["unchained"])
check("unconnected circumstances stay unchained (non-empty)", len(unchained_ids) > 0,
      f"unchained={len(unchained_ids)}")
check("chains and unchained partition all circumstances (nothing lost/duplicated)",
      chained_ids.isdisjoint(unchained_ids)
      and chained_ids | unchained_ids == set(circ_by_id),
      f"chained={len(chained_ids)} unchained={len(unchained_ids)} total={len(circ)}")

# Determinism.
ch2 = build_chains(circ, graph_data=graph_data, subject=subject)
check("deterministic: same input -> same chains",
      [c["circumstance_ids"] for c in ch2["chains"]] == [c["circumstance_ids"] for c in chains]
      and ch2["unchained"] == ch["unchained"])

import shutil
shutil.rmtree(_TMP, ignore_errors=True)

print("\n" + "=" * 70)
npass = sum(1 for _, ok, _ in RESULTS if ok)
print(f"SUMMARY: {npass}/{len(RESULTS)} checks passed")
print("EVIDENCE CHAIN PHASE 3: " + ("ALL CHECKS PASSED" if npass == len(RESULTS)
                                    else "FAILURES PRESENT"))
print("=" * 70)
sys.exit(0 if npass == len(RESULTS) else 1)
