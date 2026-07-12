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
      "case_type_detected": "financial" | "cyber" | "immigration" | "general"
                            | "undetermined",
      "patterns": [PatternMatch, ...],          # sorted, STRONG first
      "summary_skeleton": [plain_explanation, ...],
      "immigration_risk": {"points": int, "factors": [...]},   # Phase 1 Step 7
      "counts": {...},                          # ontology population (diagnostics)
    }
"""

from __future__ import annotations

from modules import pattern_rules as PR
from modules.ontology import build_ontology


# STRONG sorts first. Lower rank = earlier.
_CONFIDENCE_RANK = {"STRONG": 0, "MODERATE": 1, "WEAK": 2}
# Deterministic case-type priority for tie-breaks.
_CASE_PRIORITY = [PR.FINANCIAL, PR.CYBER, PR.IMMIGRATION, PR.GENERAL]


def _evidence_case_type(onto) -> str:
    """Evidence fallback for the GENERAL branch (patterns fired, none typed).

    The financial rules encode NARROW typologies (structuring, offshore
    routing); a corruption/kickback case with rich typed money movement can
    miss every one of them while being plainly financial — the detector would
    then report GENERAL despite the ontology holding typed transactions and
    shell entities. This tier classifies from that evidence directly:
    deterministic counts over the typed ontology, no rule logic involved, and
    a fired typed pattern always outranks it (we only get here when none
    fired). Cyber/immigration rule coverage is broad enough that their cases
    fire typed patterns, so no equivalent tier exists for them.
    """
    txns = PR._attr(onto, "transactions", []) or []
    orgs = PR._attr(onto, "organizations", []) or []
    shells = [o for o in orgs
              if PR._norm(PR._attr(o, "type")) in ("shell", "front")]
    # Financial character = repeated typed money movement, or money movement
    # routed through shell/front entities. A single stray payment row stays
    # GENERAL (conservatism over completeness).
    if len(txns) >= 3 or (txns and shells):
        return PR.FINANCIAL
    return PR.GENERAL


def _detect_case_type(patterns, onto=None) -> str:
    """Deterministic case-type detection from the fired patterns.

    Financial/cyber/immigration patterns determine the case type; the
    cross-cutting general patterns (NETWORK_HUB, TIMELINE_CLUSTER) only break a
    tie or stand alone if nothing else fired. STRONG matches count double so a
    single strong signal outweighs several weak ones — still fully deterministic.
    When ONLY general patterns fired, the typed ontology evidence (if supplied)
    breaks the blindness — see _evidence_case_type.
    """
    if not patterns:
        return "undetermined"
    # Obstruction patterns (anti-forensic deletions) evidence concealment,
    # not the offence domain. Such a pattern loses its case-type vote ONLY
    # when it would carry its type ALONE against other typed evidence: a
    # laundering case whose sole "cyber" signal is a deletion stays
    # financial, while a case with substantive cyber patterns keeps the
    # anti-forensic vote (and a deletion-only case still counts as its own
    # signal). Rules still fire and appear in §09B at full strength.
    substantive = [p for p in patterns
                   if p.pattern_id not in PR.OBSTRUCTION_PATTERN_IDS]
    _other_typed = any(p.case_type in (PR.FINANCIAL, PR.CYBER, PR.IMMIGRATION)
                       for p in substantive)

    def _lone_obstruction(p):
        return (p.pattern_id in PR.OBSTRUCTION_PATTERN_IDS
                and not any(q.case_type == p.case_type for q in substantive))

    voters = [p for p in patterns
              if not (_other_typed and _lone_obstruction(p))]
    weight = {PR.FINANCIAL: 0, PR.CYBER: 0, PR.IMMIGRATION: 0, PR.GENERAL: 0}
    strongs = {PR.FINANCIAL: 0, PR.CYBER: 0, PR.IMMIGRATION: 0, PR.GENERAL: 0}
    for p in voters:
        weight[p.case_type] = weight.get(p.case_type, 0) + (2 if p.confidence == "STRONG" else 1)
        if p.confidence == "STRONG":
            strongs[p.case_type] = strongs.get(p.case_type, 0) + 1

    typed = {PR.FINANCIAL: weight[PR.FINANCIAL], PR.CYBER: weight[PR.CYBER],
             PR.IMMIGRATION: weight[PR.IMMIGRATION]}
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
    # Only general patterns fired — fall back to typed ontology evidence.
    return _evidence_case_type(onto) if onto is not None else PR.GENERAL


# ── Phase 1 Step 7 — immigration risk weighting (evidence-based) ──────────────
# Each FIRED immigration pattern contributes a deterministic weight scaled by
# its evidence confidence; the sum (capped) is offered to §16 Risk Assessment
# as an adder. Every point traces to a cited deterministic pattern conclusion —
# weights derive from documents, numbers and behaviour, NEVER from nationality,
# ethnicity or religion (the immigration rules cannot fire on those; see the
# HARD ETHICAL CONSTRAINT header in pattern_rules.py).
_IMM_RISK_WEIGHT = {"STRONG": 8, "MODERATE": 5, "WEAK": 2}
_IMM_RISK_CAP = 20


def _immigration_risk(patterns) -> dict:
    """Deterministic, confidence-weighted risk contribution of fired
    immigration patterns. {"points": 0, "factors": []} when none fired."""
    points, factors = 0, []
    for p in patterns:
        if str(getattr(p, "case_type", "")).lower() != PR.IMMIGRATION:
            continue
        w = _IMM_RISK_WEIGHT.get(str(getattr(p, "confidence", "")).upper(), 0)
        points += w
        factors.append({"pattern_id": p.pattern_id,
                        "pattern_name": p.pattern_name,
                        "confidence": p.confidence,
                        "weight": w})
    return {"points": min(points, _IMM_RISK_CAP), "factors": factors}


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
        "case_type_detected": _detect_case_type(patterns, onto),
        "patterns": patterns,
        "summary_skeleton": [m.plain_explanation for m in patterns],
        "immigration_risk": _immigration_risk(patterns),
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
