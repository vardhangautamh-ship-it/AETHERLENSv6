"""
AETHERLENS — Pattern Engine (Step 3).

The deterministic bridge between the typed ontology (Step 2) and the pattern
inference library (Step 1). It performs no analysis of its own — it orchestrates:

    build_ontology(...)  →  run every rule  →  collect fired matches
                         →  sort by confidence  →  detect case type

Fully deterministic: the SAME case files yield the SAME patterns in the SAME
order on every run. No randomness, no clock, no LLM. The (optional) LLM narrative
wrapper is added later in the report layer and never feeds back into this result.

Public API:
    analyze_ontology(onto)  -> result dict           (rules over a prebuilt onto)
    run_pattern_analysis(person, entities, flags, timeline, graph, phones,
                         financial_data) -> result dict   (build + analyze)

Result shape:
    {
      "case_type_detected": "financial" | "cyber" | "general" | "undetermined",
      "patterns": [PatternMatch, ...],          # sorted, STRONG first
      "summary_skeleton": [plain_explanation, ...],
      "counts": {...},                          # ontology population (diagnostics)
    }
"""

from __future__ import annotations

from modules import pattern_rules as PR
from modules.ontology import build_ontology


# STRONG sorts first. Lower rank = earlier.
_CONFIDENCE_RANK = {"STRONG": 0, "MODERATE": 1, "WEAK": 2}
# Deterministic case-type priority for tie-breaks.
_CASE_PRIORITY = [PR.FINANCIAL, PR.CYBER, PR.GENERAL]


def _detect_case_type(patterns) -> str:
    """Deterministic case-type detection from the fired patterns.

    Financial/cyber patterns determine the case type; the cross-cutting general
    patterns (NETWORK_HUB, TIMELINE_CLUSTER) only break a financial/cyber tie or
    stand alone if nothing else fired. STRONG matches count double so a single
    strong signal outweighs several weak ones — still fully deterministic.
    """
    if not patterns:
        return "undetermined"
    weight = {PR.FINANCIAL: 0, PR.CYBER: 0, PR.GENERAL: 0}
    strongs = {PR.FINANCIAL: 0, PR.CYBER: 0, PR.GENERAL: 0}
    for p in patterns:
        weight[p.case_type] = weight.get(p.case_type, 0) + (2 if p.confidence == "STRONG" else 1)
        if p.confidence == "STRONG":
            strongs[p.case_type] = strongs.get(p.case_type, 0) + 1

    typed = {PR.FINANCIAL: weight[PR.FINANCIAL], PR.CYBER: weight[PR.CYBER]}
    best = max(typed.values())
    if best > 0:
        tied = [ct for ct in _CASE_PRIORITY if typed.get(ct, 0) == best]
        if len(tied) > 1:
            # Weight tie → the type with more STRONG matches wins (a case that
            # laundered its proceeds shouldn't read as financial when its
            # strongest signals are cyber). Only a full tie falls back to the
            # fixed priority order. Still fully deterministic.
            best_strong = max(strongs.get(ct, 0) for ct in tied)
            tied = [ct for ct in tied if strongs.get(ct, 0) == best_strong]
        return tied[0]
    # only general patterns fired
    return PR.GENERAL


def analyze_ontology(onto) -> dict:
    """Run every rule over a prebuilt Ontology and assemble the sorted result."""
    fired = []
    for idx, rule in enumerate(PR.ALL_RULES):     # registry order is stable
        match = rule(onto)
        if match is not None:
            fired.append((idx, match))

    # Stable sort: primary = confidence rank, secondary = original registry order.
    fired.sort(key=lambda pair: (_CONFIDENCE_RANK.get(pair[1].confidence, 9), pair[0]))
    patterns = [m for _, m in fired]

    return {
        "case_type_detected": _detect_case_type(patterns),
        "patterns": patterns,
        "summary_skeleton": [m.plain_explanation for m in patterns],
        "counts": onto.counts() if hasattr(onto, "counts") else {},
    }


def run_pattern_analysis(person, entities=None, flags=None, timeline=None,
                         graph=None, phones=None, financial_data=None, records=None,
                         texts=None) -> dict:
    """Build the ontology from raw resolver output, then analyze it.

    This is the single entry point both report pipelines (FUSION and OSINT) call,
    at the same point, with the same arguments — no per-pipeline special-casing.
    `records` carries the structured source rows and `texts` the raw narrative
    text, so dated deletion/legal/comm events keep their dates (HOP 3).
    """
    onto = build_ontology(person, entities, flags, timeline, graph, phones,
                          financial_data, records, texts)
    return analyze_ontology(onto)
