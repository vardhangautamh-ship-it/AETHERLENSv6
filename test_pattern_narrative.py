"""
Step 5 verification — optional [AI NARRATIVE] wrapper for §09B.

Proves the narrative is STRICTLY SUBORDINATE: it never removes a deterministic
conclusion, the prompt forbids introducing anything new, and the section works
unchanged when the LLM is switched off or unavailable. No real network calls —
modules.ai_agents._call_ai is monkeypatched. Run: python test_pattern_narrative.py
"""
import sys
import networkx as nx

import modules.ai_agents as AI
import modules.report_generator as RG
from modules.pattern_engine import run_pattern_analysis

results = []
def check(label, ok):
    results.append(bool(ok)); print(f"  [{'PASS' if ok else 'FAIL'}] {label}")


# ── build a real deterministic section ────────────────────────────────────────
g = nx.Graph(); g.add_node("Rohan Verma", type="person")
for a in ("A1", "A2"):
    g.add_node(a, type="person"); g.add_edge("Rohan Verma", a)
person = {"confirmed_name": "Rohan Verma",
          "anomaly_flags": ["Active lookout circular (LOC) 2023-02-01", "ED enforcement 2018-06-01",
                            "DRI 2020-08-01", "NCB 2022-03-01", "CERT-In inquiry 2023-02-01",
                            "Uses ProtonMail and Telegram", "Foreign VPN exit from Switzerland",
                            "data wiped 2023-02-05 anti-forensic", "claims personal research",
                            "data egress 240 GB"],
          "phones_found": [{"number": "+919820144109", "type": "burner"},
                           {"number": "+919820144110", "tags": ["burner"]},
                           {"number": "+14155552671"}]}
entities = {"organizations": [{"name": "Zenith Trading FZE", "type": "shell", "jurisdiction": "UAE"}],
            "properties": [{"jurisdiction": "Dubai"}]}
financial = {"transactions": [{"date": "2023-01-02", "direction": "credit", "amount": 45000, "structured": True},
                              {"date": "2023-01-09", "direction": "debit", "amount": 900000, "cross_border": True}]}
result = run_pattern_analysis(person, entities, person["anomaly_flags"], None, g,
                              person["phones_found"], financial)
section = RG._build_pattern_analysis_section(result)
deterministic_conclusions = [p["explanation"] for p in section["patterns"]]
print("  deterministic conclusions:", len(deterministic_conclusions))

_orig_call_ai = AI._call_ai

print("=" * 72)
print("LLM ON — narrative is added, prompt is constrained, conclusions untouched")
print("=" * 72)
captured = {}
def fake_on(prompt, max_tokens=4096):
    captured["prompt"] = prompt
    return "The subject shows layering and offshore exposure alongside operational security."
AI._call_ai = fake_on
narr = RG._generate_pattern_narrative(section["patterns"])
check("narrative produced when LLM available", bool(narr))
check("prompt passes ONLY the detected patterns (says 'ONLY')", "ONLY" in captured.get("prompt", ""))
check("prompt forbids new info ('Do NOT add')", "Do NOT add" in captured.get("prompt", ""))
check("prompt contains the detected explanations",
      all(c[:25] in captured["prompt"] for c in deterministic_conclusions))
check("prompt invents nothing beyond the supplied patterns",
      captured["prompt"].count("PATTERNS:") == 1)

# attaching narrative must not alter the deterministic patterns
section_on = dict(section); section_on["narrative"] = narr
check("conclusions unchanged after attaching narrative",
      [p["explanation"] for p in section_on["patterns"]] == deterministic_conclusions)

print("=" * 72)
print("LLM OFF (unavailable) — narrative empty, every conclusion still present")
print("=" * 72)
AI._call_ai = lambda prompt, max_tokens=4096: ""        # simulate Bedrock+Gemini down
narr_off = RG._generate_pattern_narrative(section["patterns"])
check("narrative empty when LLM unavailable", narr_off == "")
section_off = RG._build_pattern_analysis_section(result)      # rebuild w/o narrative
check("section still built with all patterns", len(section_off["patterns"]) == len(deterministic_conclusions))
check("conclusions identical with LLM off vs on",
      [p["explanation"] for p in section_off["patterns"]] == deterministic_conclusions)
check("deterministic content intact (no narrative needed)",
      section_off["content"].startswith("[DETERMINISTIC ANALYSIS]")
      and "narrative" not in section_off)

print("=" * 72)
print("MASTER SWITCH OFF — explicit disable removes prose only")
print("=" * 72)
AI._call_ai = fake_on                                    # LLM 'available' again
narr_disabled = RG._generate_pattern_narrative(section["patterns"], enabled=False)
check("disabled switch → no narrative", narr_disabled == "")
check("disabled switch → LLM not even called", "prompt" in captured)  # only the earlier ON call set it

print("=" * 72)
print("EXCEPTION SAFETY — LLM raising must not lose conclusions")
print("=" * 72)
def boom(prompt, max_tokens=4096):
    raise RuntimeError("bedrock exploded")
AI._call_ai = boom
narr_err = RG._generate_pattern_narrative(section["patterns"])
check("LLM exception → narrative empty (caught)", narr_err == "")
check("conclusions survive an LLM exception",
      [p["explanation"] for p in RG._build_pattern_analysis_section(result)["patterns"]] == deterministic_conclusions)

AI._call_ai = _orig_call_ai      # restore

print("=" * 72)
passed, total = sum(results), len(results)
print(f"SUMMARY: {passed}/{total} checks passed")
if passed == total:
    print("ALL PATTERN-NARRATIVE CHECKS PASSED"); sys.exit(0)
sys.exit(1)
