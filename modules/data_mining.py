"""
PHASE 2 STEP 12 — CLUSTER / NETWORK DETECTION across analysed subjects.

Mines ACROSS the subjects of one analysed case set (in-memory input — NOT
persistent cross-case storage) for exactly four evidence-typed link kinds:

    shared phones · shared organizations · shared counterparties ·
    shared locations

HARD RULES (binding on every function here and on any future edit):

  * CITED LINKS ONLY, NO FABRICATED EDGES. A link exists only when the SAME
    normalised value appears in the typed evidence of two or more distinct
    subjects, and every link carries, for EVERY subject on it, the raw value
    as it appears in that subject's evidence plus its source file. Nothing is
    inferred, extrapolated, or guessed; absence of a link is not evidence of
    absence.
  * EVIDENCE-BASED ONLY. The miner reads phones, organizations, transaction
    counterparties, and locations from the typed case ontology — never flags,
    narrative text, or identity attributes (nationality/ethnicity/religion
    are not inputs and must never become inputs).
  * DETERMINISTIC AND GENERAL. Same input → same output; matching is
    vocabulary-driven and case/punctuation-insensitive; no case-name /
    subject-name / file-name branches.
  * DECISION SUPPORT ONLY. A shared value establishes association, not
    culpability. Every result carries MINING_NOTICE; action on any cluster
    requires human review and authorisation.
"""
import re
from collections import defaultdict

# Verbatim on every mining result. Do not shorten.
MINING_NOTICE = (
    "DETERMINISTIC DATA MINING — DECISION SUPPORT ONLY: every link below is "
    "cited to the source evidence of each linked case; no link is inferred "
    "or fabricated, and absence of a link is not evidence of absence. A "
    "shared value establishes association, not culpability. This output is "
    "for analyst review; any action requires human review and authorisation. "
    "Links derive from documents, numbers, and behaviour only — no "
    "nationality, ethnicity, or religion was used."
)

# Values too generic to constitute an evidentiary link between two subjects.
# Vocabulary-driven (compared after normalisation) — extend the vocabulary,
# never special-case a single investigation.
_GENERIC_TOKENS = {"unknown", "n a", "na", "none", "nil", "self", "cash", ""}
_GENERIC_LOCATIONS = {"india", "bharat"}

_MIN_PHONE_DIGITS = 7   # below this a digit string is not a phone line
_MSISDN_LEN = 10        # subscriber-number length used for prefix-tolerant match

_LINK_TYPES = ("shared_phone", "shared_organization",
               "shared_counterparty", "shared_location")


def _norm_text(s) -> str:
    """Case/punctuation-insensitive canonical form for names/places."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", str(s or "").lower())).strip()


def _norm_phone(s) -> str:
    """Digits-only canonical form; last 10 digits when longer (so a line with
    a country code matches the same line written without one — both raw forms
    stay visible in the citations). Empty when too short to be a line."""
    digits = re.sub(r"\D", "", str(s or ""))
    if len(digits) < _MIN_PHONE_DIGITS:
        return ""
    return digits[-_MSISDN_LEN:] if len(digits) > _MSISDN_LEN else digits


def _get(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _cite(raw, source) -> dict:
    src = str(source or "").strip()
    return {"raw": str(raw or ""),
            "source": src if src else "source not recorded in the analysed case"}


def extract_case_features(subject: str, onto) -> dict:
    """Serialisable projection of ONE case's linkable evidence from its typed
    ontology (the Phase 0.5 backbone — single source of truth). Reads only
    the four evidence types the miner links on."""
    feats = {"subject": str(subject or "Unknown Subject"),
             "phones": [], "organizations": [], "counterparties": [], "locations": []}
    for p in (_get(onto, "phones") or []):
        feats["phones"].append(_cite(_get(p, "number", ""), _get(p, "source", "")))
    for o in (_get(onto, "organizations") or []):
        feats["organizations"].append(_cite(_get(o, "name", ""), _get(o, "source", "")))
    for t in (_get(onto, "transactions") or []):
        cp = _get(t, "counterparty", "")
        if str(cp or "").strip():
            feats["counterparties"].append(_cite(cp, _get(t, "source", "")))
    for l in (_get(onto, "locations") or []):
        feats["locations"].append(_cite(_get(l, "name", ""), _get(l, "source", "")))
    return feats


def _normalise_case(case) -> dict | None:
    """Accept {"subject", "ontology"} or an extract_case_features() dict."""
    if not isinstance(case, dict):
        return None
    if case.get("ontology") is not None:
        return extract_case_features(case.get("subject", ""), case["ontology"])
    if any(isinstance(case.get(k), list)
           for k in ("phones", "organizations", "counterparties", "locations")):
        return {"subject": str(case.get("subject") or "Unknown Subject"),
                "phones": list(case.get("phones") or []),
                "organizations": list(case.get("organizations") or []),
                "counterparties": list(case.get("counterparties") or []),
                "locations": list(case.get("locations") or [])}
    return None


def _linkable(link_type: str, norm: str) -> bool:
    if not norm or norm in _GENERIC_TOKENS:
        return False
    if link_type == "shared_location" and norm in _GENERIC_LOCATIONS:
        return False
    if link_type != "shared_phone" and len(norm) < 3:
        return False
    return True


def mine_case_set(cases, link_types=_LINK_TYPES) -> dict:
    """Detect cited cross-subject links and the clusters they form.

    Returns plain data: links (each citing every subject on it), clusters
    (connected components over the links), unlinked subjects, and honest
    counts for skipped input. Deterministic ordering throughout.

    `link_types` restricts which of the four evidence-typed links are mined
    (the specialised Step 13 miners pass a subset so their rings reuse THIS
    detector rather than a second copy of the logic)."""
    features, skipped = {}, 0
    for case in (cases or []):
        f = _normalise_case(case)
        if f is None:
            skipped += 1
            continue
        # Same subject appearing twice is one subject — merge, never self-link.
        if f["subject"] in features:
            for k in ("phones", "organizations", "counterparties", "locations"):
                features[f["subject"]][k].extend(f[k])
        else:
            features[f["subject"]] = f
    subjects = sorted(features)

    _FEATURE_KEY = {"shared_phone": "phones", "shared_organization": "organizations",
                    "shared_counterparty": "counterparties", "shared_location": "locations"}
    links = []
    for link_type in link_types:
        norm_fn = _norm_phone if link_type == "shared_phone" else _norm_text
        by_value = defaultdict(lambda: defaultdict(list))   # norm → subject → cites
        for subj in subjects:
            for item in features[subj][_FEATURE_KEY[link_type]]:
                raw = _get(item, "raw", "") or _get(item, "value", "")
                norm = norm_fn(raw)
                if not _linkable(link_type, norm):
                    continue
                cite = _cite(raw, _get(item, "source", ""))
                if cite not in by_value[norm][subj]:
                    by_value[norm][subj].append(cite)
        for norm in sorted(by_value):
            per_subject = by_value[norm]
            if len(per_subject) < 2:      # a link needs >= 2 DISTINCT subjects
                continue
            raws = sorted({c["raw"] for cites in per_subject.values() for c in cites})
            links.append({
                "type": link_type,
                "value": raws[0] if len(raws) == 1 else f"{raws[0]} (also as: {', '.join(raws[1:])})",
                "subjects": sorted(per_subject),
                "citations": {s: list(per_subject[s]) for s in sorted(per_subject)},
            })

    # Clusters — connected components over the cited links (union-find).
    parent = {s: s for s in subjects}
    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for link in links:
        first = link["subjects"][0]
        for other in link["subjects"][1:]:
            ra, rb = find(first), find(other)
            if ra != rb:
                parent[max(ra, rb)] = min(ra, rb)
    groups = defaultdict(list)
    for s in subjects:
        groups[find(s)].append(s)

    clusters = []
    for members in groups.values():
        if len(members) < 2:
            continue
        mset = set(members)
        clinks = [l for l in links if set(l["subjects"]) & mset]
        clusters.append({
            "subjects": sorted(members),
            "size": len(members),
            "link_count": len(clinks),
            "link_types": sorted({l["type"] for l in clinks}),
        })
    clusters.sort(key=lambda c: (-c["size"], c["subjects"]))
    linked = {s for c in clusters for s in c["subjects"]}

    return {
        "subject_count": len(subjects),
        "links": links,
        "link_count": len(links),
        "clusters": clusters,
        "cluster_count": len(clusters),
        "unlinked_subjects": sorted(set(subjects) - linked),
        "skipped_malformed": skipped,
        "human_review_required": True,
        "mining_notice": MINING_NOTICE,
    }


def render_mining_result(result: dict) -> str:
    """Analyst-facing plain-text rendering of a mine_case_set() result."""
    if not isinstance(result, dict):
        return ""
    lines = ["CROSS-SUBJECT CLUSTER / NETWORK DETECTION (DECISION SUPPORT ONLY)",
             str(result.get("mining_notice") or MINING_NOTICE),
             ""]
    clusters = result.get("clusters") or []
    if not clusters:
        lines.append("No cited links between the analysed subjects.")
    for i, c in enumerate(clusters, 1):
        lines.append(f"CLUSTER {i}: {', '.join(c['subjects'])} "
                     f"({c['link_count']} cited link(s); "
                     f"types: {', '.join(c['link_types'])})")
    for l in (result.get("links") or []):
        lines.append(f"  [{l['type'].upper()}] {l['value']} — "
                     f"shared by: {', '.join(l['subjects'])}")
        for subj in l["subjects"]:
            for cite in l["citations"].get(subj, []):
                lines.append(f"      {subj}: \"{cite['raw']}\" — {cite['source']}")
    if result.get("unlinked_subjects"):
        lines.append(f"UNLINKED SUBJECTS (no cited link — NOT implicated): "
                     f"{', '.join(result['unlinked_subjects'])}")
    if result.get("skipped_malformed"):
        lines.append(f"({result['skipped_malformed']} malformed case(s) skipped — "
                     f"not mined, not guessed.)")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════
# PHASE 2 STEP 13 — SPECIALISED MINERS.
#
# Four deterministic, cited miners over an analysed case set: SIM-farming,
# document-fraud rings, remittance/hawala-style flows, and movement/timeline
# patterns. Each reuses TWO existing single-sources-of-truth and adds no third:
#
#   * WHAT counts as an indicator — the Phase 1 deterministic pattern rules in
#     pattern_rules.py (rule_sim_farming_signature, rule_document_fraud_cluster,
#     rule_remittance_corridor, rule_border_movement_cluster). A subject is
#     flagged ONLY when its own typed evidence trips that rule, and the rule's
#     own triggers + supporting sources become the citation — no re-derivation,
#     no second definition of "SIM farming" etc.
#   * HOW subjects link into a ring — the Step 12 cited-link detector above
#     (mine_case_set with a link_types subset). A ring is a cluster of flagged
#     subjects joined by a SHARED CITED VALUE (operator, supplier, corridor
#     counterparty, or crossing) — never a fabricated edge.
#
# Everything here is decision support: MINING_NOTICE (association not
# culpability, human review, no identity attributes) rides on every result.
# ══════════════════════════════════════════════════════════════════════════

# Each miner: the Phase-1 rule that defines its indicator, and the Step-12
# link type(s) whose SHARED value constitutes the ring. Vocabulary-style
# config — extend the table, never special-case one investigation.
_SPECIALISED = {
    "sim_farming": {
        "rule": "rule_sim_farming_signature",
        "link_types": ("shared_phone", "shared_organization", "shared_counterparty"),
        "ring_basis": "a shared SIM line, operator, or handler",
        "description": "bulk / pre-activated SIM infrastructure across subjects",
    },
    "document_fraud_ring": {
        "rule": "rule_document_fraud_cluster",
        "link_types": ("shared_organization", "shared_counterparty"),
        "ring_basis": "a shared forged-document supplier",
        "description": "travel/identity document fraud sharing a common supplier",
    },
    "remittance_hawala": {
        "rule": "rule_remittance_corridor",
        "link_types": ("shared_counterparty",),
        "ring_basis": "a shared corridor counterparty (operator/beneficiary)",
        "description": "repeated small cross-border outflows over a shared corridor",
    },
    "movement": {
        "rule": "rule_border_movement_cluster",
        "link_types": ("shared_location",),
        "ring_basis": "a shared border crossing / transit point",
        "description": "border movement converging on shared crossings",
    },
}


def _run_specialised_miner(cases, miner_name: str) -> dict:
    """Flag subjects whose OWN evidence trips the miner's Phase-1 rule (cited to
    the rule's triggers/sources), then group the flagged subjects into rings
    using the Step-12 cited-link detector on the miner's link type(s)."""
    from modules import pattern_rules as PR

    spec = _SPECIALISED[miner_name]
    rule_fn = getattr(PR, spec["rule"])

    flagged, flagged_cases, seen, skipped = [], [], set(), 0
    for case in (cases or []):
        if not isinstance(case, dict) or case.get("ontology") is None:
            skipped += 1
            continue
        subject = str(case.get("subject") or _get(case["ontology"], "subject_name", "")
                      or "Unknown Subject")
        if subject in seen:            # one subject is one subject
            continue
        try:
            match = rule_fn(case["ontology"])
        except Exception:
            match = None
        if match is None:
            continue
        seen.add(subject)
        flagged.append({
            "subject": subject,
            "confidence": getattr(match, "confidence", ""),
            "explanation": getattr(match, "plain_explanation", ""),
            "indicators": list(getattr(match, "triggers_met", []) or []),
            "sources": list(getattr(match, "supporting_sources", []) or []),
        })
        flagged_cases.append({"subject": subject, "ontology": case["ontology"]})

    # Rings — reuse the Step 12 detector on the flagged subset, restricted to
    # this miner's link type(s). Clusters of >= 2 flagged subjects sharing a
    # cited value ARE the rings; every ring link keeps its per-subject citation.
    mined = mine_case_set(flagged_cases, link_types=spec["link_types"])

    return {
        "miner": miner_name,
        "description": spec["description"],
        "ring_basis": spec["ring_basis"],
        "subjects_flagged": flagged,
        "flagged_count": len(flagged),
        "rings": mined["clusters"],
        "ring_links": mined["links"],
        "ring_count": mined["cluster_count"],
        "unlinked_flagged_subjects": mined["unlinked_subjects"],
        "skipped_malformed": skipped,
        "suggestion_only": True,
        "human_review_required": True,
        "mining_notice": MINING_NOTICE,
    }


def mine_sim_farming(cases) -> dict:
    """Cross-subject SIM-farming miner (reuses rule_sim_farming_signature)."""
    return _run_specialised_miner(cases, "sim_farming")


def mine_document_fraud_rings(cases) -> dict:
    """Cross-subject document-fraud-ring miner (reuses rule_document_fraud_cluster)."""
    return _run_specialised_miner(cases, "document_fraud_ring")


def mine_remittance_hawala(cases) -> dict:
    """Cross-subject remittance/hawala miner (reuses rule_remittance_corridor)."""
    return _run_specialised_miner(cases, "remittance_hawala")


def mine_movement_patterns(cases) -> dict:
    """Cross-subject movement/timeline miner (reuses rule_border_movement_cluster)."""
    return _run_specialised_miner(cases, "movement")


def run_all_specialised_miners(cases) -> dict:
    """Run all four specialised miners over one analysed case set."""
    return {name: _run_specialised_miner(cases, name) for name in _SPECIALISED}


def render_specialised_result(result: dict) -> str:
    """Analyst-facing plain-text rendering of one specialised-miner result."""
    if not isinstance(result, dict):
        return ""
    lines = [f"SPECIALISED MINER — {str(result.get('miner', '')).upper()} "
             f"({result.get('description', '')}) — DECISION SUPPORT ONLY",
             str(result.get("mining_notice") or MINING_NOTICE), ""]
    flagged = result.get("subjects_flagged") or []
    if not flagged:
        lines.append("No subject's own evidence trips this indicator.")
    for f in flagged:
        lines.append(f"FLAGGED: {f['subject']} [{f.get('confidence', '?')}] — "
                     f"{f.get('explanation', '')}")
        for ind in f.get("indicators", []):
            lines.append(f"    indicator: {ind}")
        if f.get("sources"):
            lines.append(f"    sources: {', '.join(str(s) for s in f['sources'])}")
    rings = result.get("rings") or []
    if rings:
        lines.append(f"RINGS ({result.get('ring_basis', 'a shared cited value')}):")
        for i, r in enumerate(rings, 1):
            lines.append(f"  RING {i}: {', '.join(r['subjects'])} "
                         f"({r['link_count']} cited link(s))")
        for l in (result.get("ring_links") or []):
            lines.append(f"    [{l['type'].upper()}] {l['value']} — "
                         f"shared by: {', '.join(l['subjects'])}")
            for subj in l["subjects"]:
                for cite in l["citations"].get(subj, []):
                    lines.append(f"        {subj}: \"{cite['raw']}\" — {cite['source']}")
    elif flagged:
        lines.append("No cited link between the flagged subjects — each flag "
                     "stands alone (NOT presented as a ring).")
    if result.get("skipped_malformed"):
        lines.append(f"({result['skipped_malformed']} case(s) without a typed "
                     f"ontology skipped — not mined, not guessed.)")
    return "\n".join(lines)
