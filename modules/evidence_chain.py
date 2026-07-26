"""
EVIDENCE CHAIN — the chain layer (the only genuinely new logic in the mode).

Everything here is a PROJECTION of the typed Ontology that the shared case
pipeline already built (modules.case_pipeline.build_case_ontology). It performs
NO new extraction, NO inference, and NEVER fabricates a circumstance, link, or
date. Each surfaced circumstance is a typed fact the ontology ALREADY recognises,
carried with its source cite VERBATIM and the existing typed label.

PHASE 2 — circumstance extraction (this file, so far):
  A "circumstance" is one typed fact tagged as a candidate link in a chain:
    - transaction : a financial movement (Ontology.transactions)
    - event       : a dated event (timeline_events / legal_proceedings /
                    deletion_events)
    - presence    : a place tied to the case (Ontology.locations)
    - contact     : a phone line or comms channel (Ontology.phones / comm_channels)
    - document    : an ingested source document (raw_documents)

  CITATION RULES (honest by construction):
    * A circumstance MUST carry a NON-EMPTY source cite. A typed fact whose
      source is empty (or a "not recorded" sentinel) is NOT given a fabricated
      cite and NOT surfaced — it is counted under `unsourced_excluded` and stays
      eligible for a gap flag later (Phase 4).
    * The cite is kept VERBATIM from the ontology; the evidence is never rewritten.
    * `sourced` is True only when the cite names an ingested FILE. A fact carried
      with a generic provenance tag (e.g. "record") that does not name a file is
      surfaced with sourced=False — a citation-gap candidate for Phase 4, not a
      solid link. (Root cause: some ontology collections — legal proceedings,
      deletion events, comm channels, many phones — are not threaded with their
      origin filename during build_ontology. Product debt, reported, not patched
      from this lens.)
"""

from modules.sanitizer import normalize_name_key, phone_key, parse_iso_date_strict

# The five circumstance kinds this lead engine recognises.
KINDS = ("transaction", "event", "presence", "contact", "document")


def _get(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _src(obj) -> str:
    """The source cite for a typed fact, or '' if the fact carries none.

    A "source not recorded"/"not available" sentinel is NOT a real cite, so it is
    treated as absent (the fact is excluded, never surfaced with a hollow cite)."""
    s = str(_get(obj, "source", "") or "").strip()
    if not s or "not recorded" in s.lower() or "not available" in s.lower():
        return ""
    return s


def _iso(date_str) -> str:
    """Source-precision ISO date string, or '' — never imputed/guessed."""
    d = parse_iso_date_strict(date_str)
    return d.isoformat() if d is not None else ""


def _circ(kind, idx, summary, source, sourced, *, date="", entities=None,
          label="", typed=None) -> dict:
    return {
        "kind": kind,
        "id": f"{kind}:{idx}",
        "summary": summary,
        "date": date,                      # ISO at source precision, or ""
        "entities": sorted(set(e for e in (entities or []) if e)),
        "source": source,                  # verbatim, guaranteed non-empty
        "sourced": bool(sourced),          # True only when the cite names a file
        "label": label,                    # the EXISTING typed label (significance/type/kind/…)
        "typed": typed or {},
    }


def extract_circumstances(ontology, raw_documents=None) -> dict:
    """Collect typed, cited circumstances from an already-built Ontology.

    Deterministic and read-only: no new analysis, no inference. Returns
    {subject, circumstances, count, by_kind, unsourced_excluded, file_cited,
     generic_cited}. Every circumstance carries a non-empty verbatim cite;
    `sourced` marks whether that cite names an ingested file."""
    circ = []
    unsourced = 0
    subject = str(_get(ontology, "subject_name", "") or "")
    known = {fn for fn in (str(_get(d, "filename", "") or _get(d, "name", "") or "").strip()
                           for d in (raw_documents or [])) if fn}

    def _is_file_cite(s: str) -> bool:
        return bool(s) and any(fn in s for fn in known)

    def add(kind, summary, source, **kw):
        nonlocal unsourced
        if not source:
            unsourced += 1
            return
        circ.append(_circ(kind, len(circ), summary, source, _is_file_cite(source), **kw))

    # ── transactions ──────────────────────────────────────────────────────────
    for t in (_get(ontology, "transactions", []) or []):
        direction = str(_get(t, "direction", "") or "")
        amount = _get(t, "amount", 0.0)
        cp = str(_get(t, "counterparty", "") or "").strip()
        cross = bool(_get(t, "cross_border", False))
        summary = (f"{direction or 'transfer'} {'cross-border ' if cross else ''}"
                   f"{amount} {'to' if direction == 'out' else 'from'} "
                   f"{cp or 'unknown party'}").strip()
        add("transaction", summary, _src(t), date=_iso(_get(t, "date", "")),
            entities=[normalize_name_key(cp)] if cp else [],
            label=("structured" if _get(t, "structured", False) else "transaction"),
            typed={"direction": direction, "amount": amount, "counterparty": cp,
                   "cross_border": cross})

    # ── events: timeline events ───────────────────────────────────────────────
    for e in (_get(ontology, "timeline_events", []) or []):
        desc = str(_get(e, "description", "") or "").strip()
        add("event", desc or "dated event", _src(e), date=_iso(_get(e, "date", "")),
            label=str(_get(e, "significance", "LOW") or "LOW"),
            typed={"significance": _get(e, "significance", "LOW"), "description": desc})

    # ── events: legal / enforcement proceedings ───────────────────────────────
    for lp in (_get(ontology, "legal_proceedings", []) or []):
        agency = str(_get(lp, "agency", "") or "").strip()
        kind_l = str(_get(lp, "kind", "") or "").strip()
        case_ref = str(_get(lp, "case_ref", "") or "").strip()
        summary = (f"{kind_l or 'legal'} — {agency or 'unnamed agency'}"
                   + (f" ({case_ref})" if case_ref else "")).strip()
        add("event", summary, _src(lp), date=_iso(_get(lp, "date", "")),
            entities=[normalize_name_key(agency)] if agency else [],
            label=(kind_l or "legal"),
            typed={"agency": agency, "kind": kind_l, "case_ref": case_ref,
                   "status": _get(lp, "status", "")})

    # ── events: deletion events ───────────────────────────────────────────────
    for de in (_get(ontology, "deletion_events", []) or []):
        target = str(_get(de, "target", "") or "").strip()
        add("event", f"deletion — {target or 'unspecified target'}", _src(de),
            date=_iso(_get(de, "timestamp", "")), label="deletion",
            typed={"target": target})

    # ── presence: locations ───────────────────────────────────────────────────
    for loc in (_get(ontology, "locations", []) or []):
        name = str(_get(loc, "name", "") or "").strip()
        if not name:
            continue
        add("presence", f"present at {name}", _src(loc),
            entities=[normalize_name_key(name)],
            label=str(_get(loc, "kind", "stated") or "stated"),
            typed={"name": name, "kind": _get(loc, "kind", "stated")})

    # ── contact: phone lines ──────────────────────────────────────────────────
    for ph in (_get(ontology, "phones", []) or []):
        num = str(_get(ph, "number", "") or "").strip()
        owner = str(_get(ph, "owner", "") or "").strip()
        pk = phone_key(num)
        add("contact", f"phone line {num}" + (f" ({owner})" if owner else ""), _src(ph),
            entities=[f"phone:{pk}"] if pk else [],
            label=str(_get(ph, "type", "domestic") or "domestic"),
            typed={"number": num, "owner": owner, "type": _get(ph, "type", "domestic")})

    # ── contact: comms channels ───────────────────────────────────────────────
    for cc in (_get(ontology, "comm_channels", []) or []):
        ctype = str(_get(cc, "type", "") or "").strip()
        add("contact", f"comms channel: {ctype or 'unknown'}", _src(cc),
            label=ctype or "channel",
            typed={"type": ctype, "encrypted": bool(_get(cc, "encrypted", False))})

    # ── document: ingested source files (each cited to itself) ────────────────
    for d in (raw_documents or []):
        fname = str(_get(d, "filename", "") or _get(d, "name", "") or "").strip()
        if not fname:
            unsourced += 1
            continue
        add("document", f"source document: {fname}", fname, label="ingested",
            typed={"filename": fname})

    # Deterministic order: dated first (chronological), then undated, then by
    # kind and summary — a stable ordering for the corkboard.
    circ.sort(key=lambda c: (c["date"] == "", c["date"], c["kind"], c["summary"]))
    for i, c in enumerate(circ):
        c["id"] = f"{c['kind']}:{i}"

    by_kind = {k: sum(1 for c in circ if c["kind"] == k) for k in KINDS}
    return {
        "subject": subject,
        "circumstances": circ,
        "count": len(circ),
        "by_kind": by_kind,
        "unsourced_excluded": unsourced,
        "file_cited": sum(1 for c in circ if c["sourced"]),
        "generic_cited": sum(1 for c in circ if not c["sourced"]),
    }


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 3 — timeline chaining.
#
# Order circumstances on the timeline (source-precision dates only — NEVER an
# imputed date), then link them into candidate chains ONLY where a real typed
# connection joins them. The linking rules, stated plainly:
#   * LINK on a shared NON-SUBJECT connective entity: a shared strong phone line
#     (a hard identifier) or a shared counterparty/agency (the same party the
#     subject dealt with, as recorded in THIS case).
#   * LINK on a non-location typed relationship edge between two such entities
#     (reuses the pipeline's relationship graph).
#   * NEVER link on: a location / city (excluded outright), a landline / shared-
#     office number (never anchors identity), the SUBJECT (every circumstance
#     involves the subject, so that is not a discriminating link — no trivial
#     star), or mere closeness in TIME (temporal proximity is coincidence, not a
#     link).
# Each link is tagged HARD (shared strong phone / hard identifier) or
# NAME_RESOLVED (shared party by canonical name — weaker; a lead, not proof). A
# chain is bounded later by its weakest link. Unconnected circumstances stay
# unchained. Every link carries the source cites of both circumstances.
# ══════════════════════════════════════════════════════════════════════════════

_LINK_BASES = ("shared_hard_identifier", "shared_party", "typed_edge")


def _is_landline(number: str) -> bool:
    try:
        from modules.entity_resolution import _is_landline_number
        return bool(_is_landline_number(number))
    except Exception:
        return False


def _party_key(name: str) -> str:
    k = normalize_name_key(name)
    return f"party:{k}" if k else ""


def link_keys(circ: dict, subject_key: str) -> set:
    """Connective identifiers for a circumstance, as {(key, strength)}.

    Location, landline and the subject are excluded — they never form a link.
    Presence, document and entity-less events return an empty set (they can only
    be chained by time, which is not a link, so they stay unchained)."""
    kind = circ["kind"]
    typed = circ.get("typed", {}) or {}
    out = set()
    subj = f"party:{subject_key}" if subject_key else ""
    if kind == "transaction":
        pk = _party_key(typed.get("counterparty", ""))
        if pk and pk != subj:
            out.add((pk, "NAME_RESOLVED"))
    elif kind == "event" and typed.get("agency"):
        pk = _party_key(typed.get("agency", ""))
        if pk and pk != subj:
            out.add((pk, "NAME_RESOLVED"))
    elif kind == "contact" and typed.get("number"):
        num = str(typed.get("number", "") or "")
        if not _is_landline(num):          # a landline never anchors a link
            ph = phone_key(num)
            if ph:
                out.add((f"phone:{ph}", "HARD"))
    return out


def _typed_edges(graph_data, subject_key: str) -> set:
    """Non-location relationship edges from the pipeline graph, as frozenset
    pairs of party keys. Location edges (located_near, loc: nodes) are dropped —
    the chain never links on a place."""
    edges = set()
    if not isinstance(graph_data, dict):
        return edges
    ents = graph_data.get("entities") or []
    keymap = {}
    for e in ents:
        eid = e.get("id")
        etype = str(e.get("type", "") or "")
        label = e.get("label") or e.get("value") or e.get("name") or ""
        if etype == "location" or str(eid).startswith("loc:"):
            keymap[eid] = None                 # mark as location → never a link
        else:
            keymap[eid] = _party_key(label)
    for r in (graph_data.get("rels") or []):
        a, b = keymap.get(r.get("source")), keymap.get(r.get("target"))
        if not a or not b or a == b:
            continue                            # missing, location, or self
        if a == f"party:{subject_key}" or b == f"party:{subject_key}":
            continue                            # subject-star is not a link
        edges.add(frozenset({a, b}))
    return edges


class _UF:
    def __init__(self, n): self.p = list(range(n))
    def find(self, x):
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]; x = self.p[x]
        return x
    def union(self, a, b): self.p[self.find(a)] = self.find(b)


def build_chains(circumstances, graph_data=None, subject="") -> dict:
    """Order circumstances on the timeline and link them into candidate chains on
    typed connections only. Deterministic. Returns
    {chains, chain_count, unchained, link_count, subject}."""
    subject_key = normalize_name_key(subject)
    circ = list(circumstances or [])
    n = len(circ)
    keys = [link_keys(c, subject_key) for c in circ]
    tedges = _typed_edges(graph_data, subject_key)

    uf = _UF(n)
    links = []
    for i in range(n):
        ki = keys[i]
        if not ki:
            continue
        ki_keys = {k for k, _ in ki}
        for j in range(i + 1, n):
            kj = keys[j]
            if not kj:
                continue
            kj_keys = {k for k, _ in kj}
            shared = ki_keys & kj_keys
            basis = key = strength = None
            if shared:
                key = sorted(shared)[0]
                if key.startswith("phone:"):
                    basis, strength = "shared_hard_identifier", "HARD"
                else:
                    basis, strength = "shared_party", "NAME_RESOLVED"
            else:
                hit = next((frozenset({a, b}) for a in ki_keys for b in kj_keys
                            if a != b and frozenset({a, b}) in tedges), None)
                if hit is not None:
                    basis, strength, key = "typed_edge", "NAME_RESOLVED", " — ".join(sorted(hit))
            if basis is None:
                continue
            uf.union(i, j)
            links.append({
                "a": circ[i]["id"], "b": circ[j]["id"],
                "basis": basis, "key": key, "strength": strength,
                "citations": [{"circumstance": circ[i]["id"], "source": circ[i]["source"]},
                              {"circumstance": circ[j]["id"], "source": circ[j]["source"]}],
            })

    # Group into connected components; a chain needs >= 2 circumstances.
    comp = {}
    for idx in range(n):
        comp.setdefault(uf.find(idx), []).append(idx)

    def _order(idxs):
        return sorted(idxs, key=lambda x: (circ[x]["date"] == "", circ[x]["date"],
                                           circ[x]["kind"], circ[x]["summary"]))

    chains, unchained = [], []
    link_by_pair = {frozenset({l["a"], l["b"]}): l for l in links}
    for members in comp.values():
        if len(members) < 2:
            unchained.append(circ[members[0]]["id"])
            continue
        ordered = _order(members)
        ids = [circ[x]["id"] for x in ordered]
        idset = set(ids)
        chain_links = [l for l in links if {l["a"], l["b"]} <= idset]
        dates = [circ[x]["date"] for x in ordered if circ[x]["date"]]
        strengths = [l["strength"] for l in chain_links]
        weakest = "NAME_RESOLVED" if "NAME_RESOLVED" in strengths else (
            "HARD" if strengths else "NONE")
        parties = sorted({k for x in ordered for k, _ in keys[x]})
        chains.append({
            "circumstance_ids": ids,
            "circumstances": [circ[x] for x in ordered],
            "links": chain_links,
            "parties": parties,
            "size": len(ids),
            "first_date": dates[0] if dates else "",
            "last_date": dates[-1] if dates else "",
            "weakest_link_strength": weakest,
        })

    # Deterministic: biggest chains first, then earliest, then by first id.
    chains.sort(key=lambda c: (-c["size"], c["first_date"] == "", c["first_date"],
                               c["circumstance_ids"][0]))
    unchained.sort()
    return {
        "subject": subject,
        "chains": chains,
        "chain_count": len(chains),
        "unchained": unchained,
        "link_count": len(links),
    }


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 4 — link strength + chain gaps.
#
# For each candidate chain, produce the two things that make a lead honest —
# nothing more:
#   * LINK STRENGTH: each link's strength (HARD shared identifier vs NAME_RESOLVED
#     shared party) and whether every circumstance is fully file-sourced. The
#     chain is bounded by its WEAKEST link and its weakest cite — a chain resting
#     on a weak or unverified circumstance SAYS SO and never reads as solid.
#   * CHAIN GAPS: within-chain step breaks ("the chain breaks between X and Y")
#     plus the case's structural gaps (reusing gap_detection) attributed to the
#     chain, in the NOT-PROVIDED / SEARCHED-ABSENT / UNDETERMINABLE vocabulary.
#
# NO exclusion test. NO weighing / generating / testing of innocent or
# alternative explanations — that is a court's job, not a lead's, and is
# deliberately out of scope here.
# ══════════════════════════════════════════════════════════════════════════════

_GAP_LABELS = ("NOT-PROVIDED", "SEARCHED-ABSENT", "UNDETERMINABLE")


def assess_chain_integrity(chain: dict) -> dict:
    """Link strength + within-chain step breaks for ONE chain. Pure, no I/O.

    verdict ∈ SOLID | QUALIFIED | BROKEN:
      * BROKEN   — the time-ordered chain has a hole (consecutive steps with no
                   direct cited link between them).
      * SOLID    — no holes, every link is a HARD identifier, and every
                   circumstance is fully file-sourced.
      * QUALIFIED — connected end-to-end but rests on a NAME_RESOLVED link or a
                   weakly-cited (generic) circumstance — a lead, not proof."""
    circ = chain.get("circumstances", []) or []
    ids = [c["id"] for c in circ]
    links = chain.get("links", []) or []
    linkset = {frozenset({l["a"], l["b"]}) for l in links}
    strengths = [l.get("strength") for l in links]
    all_hard = bool(strengths) and all(s == "HARD" for s in strengths)
    all_sourced = bool(circ) and all(bool(c.get("sourced")) for c in circ)

    breaks, holds = [], 1 if circ else 0
    counting = True
    for k in range(len(ids) - 1):
        if frozenset({ids[k], ids[k + 1]}) in linkset:
            if counting:
                holds += 1
        else:
            counting = False
            a, b = circ[k], circ[k + 1]
            breaks.append({
                "between": [a["id"], b["id"]],
                "after_index": k,
                "kind_label": "UNDETERMINABLE",
                "finding": (f"the chain breaks between «{a['summary']}»"
                            + (f" ({a['date']})" if a.get("date") else "")
                            + f" and «{b['summary']}»"
                            + (f" ({b['date']})" if b.get("date") else "")
                            + " — no cited typed connection joins these consecutive "
                              "steps (UNDETERMINABLE from the provided evidence)."),
            })

    weakest = chain.get("weakest_link_strength") or (
        "HARD" if all_hard else ("NAME_RESOLVED" if strengths else "NONE"))
    if breaks:
        verdict = "BROKEN"
    elif all_hard and all_sourced:
        verdict = "SOLID"
    else:
        verdict = "QUALIFIED"

    weak_cites = sum(1 for c in circ if not c.get("sourced"))
    reasons = []
    if breaks:
        reasons.append(f"{len(breaks)} internal break(s) — the sequence does not "
                       f"flow end-to-end")
    if weakest == "NAME_RESOLVED":
        reasons.append("weakest link is NAME_RESOLVED (shared party by name, not a "
                       "hard identifier) — verify, do not assume identity")
    if weak_cites:
        reasons.append(f"{weak_cites} circumstance(s) carry a generic cite (origin "
                       f"file not threaded) — citation is weak")
    if verdict == "SOLID":
        note = ("Chain holds end-to-end; every link is a hard identifier and every "
                "circumstance is fully file-sourced. Still a LEAD, not proof.")
    else:
        note = (f"Chain is {verdict}: " + "; ".join(reasons) + ". "
                "Treat as a lead to investigate — this is not proof and innocent "
                "explanations have not been considered (out of scope).")

    return {
        "verdict": verdict,
        "weakest_link_strength": weakest,
        "all_links_hard": all_hard,
        "all_circumstances_file_sourced": all_sourced,
        "weak_cite_count": weak_cites,
        "holds_through_steps": holds,
        "total_steps": len(ids),
        "step_breaks": breaks,
        "note": note,
    }


def _relevant_structural_gaps(chain: dict, case_gaps: list) -> list:
    """Case-level structural gaps (from gap_detection) whose source files overlap
    this chain's circumstances — attributed by shared source, not invented."""
    csrc = " ".join(str(c.get("source", "")) for c in chain.get("circumstances", []))
    out = []
    for g in (case_gaps or []):
        gs = g.get("sources") or []
        if any(s and str(s) in csrc for s in gs):
            out.append({
                "type": g.get("type", ""),
                "kind_label": g.get("kind_label", ""),
                "finding": g.get("finding", ""),
                "why_it_matters": g.get("why_it_matters", ""),
                "sources": gs,
            })
    return out


def annotate_chains(chain_result: dict, person=None, ontology=None,
                    raw_documents=None) -> dict:
    """Annotate every chain with link strength + gaps (in place, and returned).

    Reuses gap_detection.detect_structural_gaps for the case's structural gaps,
    then attaches each chain's integrity assessment and the structural gaps that
    touch it. Also carries the full case-level gap list under `case_gaps`."""
    case_gaps = []
    try:
        from modules.gap_detection import detect_structural_gaps
        case_gaps = detect_structural_gaps(person or {}, ontology, raw_documents or []) or []
    except Exception:
        case_gaps = []

    for chain in chain_result.get("chains", []):
        chain["integrity"] = assess_chain_integrity(chain)
        chain["structural_gaps"] = _relevant_structural_gaps(chain, case_gaps)

    chain_result["case_gaps"] = case_gaps
    return chain_result


# ══════════════════════════════════════════════════════════════════════════════
# PHASE 5 — lead label + mode output + (removable) LLM narrative.
#
# build_evidence_chains() runs the whole DETERMINISTIC chain layer end to end and
# stamps every chain with the unmissable LEAD label and the non-autonomy markers.
# render_evidence_chains() is a deterministic, LLM-free text rendering of that
# result. narrate_evidence_chains() is the ONLY LLM in the mode: it rephrases the
# already-detected chains/strengths/gaps into prose and is removable with zero
# loss — it never mutates the structured result and adds no fact, link, or
# conclusion. Everything a human acts on lives in the deterministic result.
# ══════════════════════════════════════════════════════════════════════════════

# Verbatim on every chain and on the result. Do not shorten.
LEAD_LABEL = (
    "This is a LEAD, not a conclusion. These circumstances are connected, not "
    "proven. Ordinary/innocent explanations may exist and have NOT been ruled "
    "out. For a human to investigate."
)


def build_evidence_chains(ontology, person=None, raw_documents=None,
                          graph_data=None, subject=None) -> dict:
    """Full deterministic chain layer: extract → chain → strength+gaps → label.

    No LLM. Every chain carries the LEAD label and non-autonomy markers; the
    result is the single source of truth the UI renders."""
    # Prefer the ontology's CANONICAL resolved subject (build_ontology set it from
    # the resolved person) over any caller-supplied name — the subject-star
    # exclusion in chaining must key off the true subject, not a stale label.
    subj = (str(_get(ontology, "subject_name", "") or "").strip()
            or (str(subject).strip() if subject else ""))
    ex = extract_circumstances(ontology, raw_documents=raw_documents)
    ch = build_chains(ex["circumstances"], graph_data=graph_data, subject=subj)
    ch = annotate_chains(ch, person=person, ontology=ontology, raw_documents=raw_documents)

    for chain in ch["chains"]:
        chain["lead_label"] = LEAD_LABEL
        chain["human_review_required"] = True
        chain["autonomous"] = False

    return {
        "subject": subj,
        "circumstances": ex["circumstances"],
        "circumstance_count": ex["count"],
        "by_kind": ex["by_kind"],
        "file_cited": ex["file_cited"],
        "generic_cited": ex["generic_cited"],
        "unsourced_excluded": ex["unsourced_excluded"],
        "chains": ch["chains"],
        "chain_count": ch["chain_count"],
        "unchained": ch["unchained"],
        "link_count": ch["link_count"],
        "case_gaps": ch["case_gaps"],
        "human_review_required": True,
        "autonomous": False,
        "lead_notice": LEAD_LABEL,
    }


def render_evidence_chains(result: dict) -> str:
    """Deterministic, LLM-free plain-text rendering of a build_evidence_chains
    result — chains, links, strength, gaps, and the LEAD label on every chain."""
    if not isinstance(result, dict):
        return ""
    lines = ["EVIDENCE CHAIN — CANDIDATE LEADS (DECISION SUPPORT — FOR HUMAN REVIEW)",
             str(result.get("lead_notice") or LEAD_LABEL), ""]
    subj = result.get("subject") or "Unknown Subject"
    lines.append(f"SUBJECT: {subj}")
    lines.append(f"{result.get('circumstance_count', 0)} circumstance(s) "
                 f"({result.get('file_cited', 0)} file-cited, "
                 f"{result.get('generic_cited', 0)} generic-cited, "
                 f"{result.get('unsourced_excluded', 0)} excluded for no cite); "
                 f"{result.get('chain_count', 0)} candidate chain(s); "
                 f"{len(result.get('unchained') or [])} unchained.")
    lines.append("")

    for n, chain in enumerate(result.get("chains") or [], 1):
        it = chain.get("integrity", {})
        lines.append(f"── CHAIN {n} — {it.get('verdict', '?')} "
                     f"(weakest link: {it.get('weakest_link_strength', '?')}; "
                     f"holds {it.get('holds_through_steps', 0)}/{it.get('total_steps', 0)} steps)")
        for c in chain.get("circumstances", []):
            mark = "" if c.get("sourced") else "  [weak cite — origin file not threaded]"
            date = c.get("date") or "undated"
            lines.append(f"   • [{date}] {c.get('summary', '')} — {c.get('source', '')}{mark}")
        for l in chain.get("links", []):
            lines.append(f"     ↳ link [{l.get('strength')}]: {l.get('basis')} "
                         f"({l.get('key')})")
        for b in it.get("step_breaks", []):
            lines.append(f"     ✂ {b.get('finding')}")
        for g in chain.get("structural_gaps", []):
            lines.append(f"     ⚑ GAP [{g.get('kind_label')}] {g.get('finding')}")
        lines.append(f"   NOTE: {it.get('note', '')}")
        lines.append(f"   LEAD: {chain.get('lead_label', LEAD_LABEL)}")
        lines.append("")

    if result.get("case_gaps"):
        lines.append("CASE-LEVEL STRUCTURAL GAPS (what the evidence does NOT establish):")
        for g in result["case_gaps"]:
            lines.append(f"   ⚑ [{g.get('kind_label')}] {g.get('finding')}")
    return "\n".join(lines)


def _narrative_brief(result: dict) -> str:
    """A deterministic digest of the structured result — the ONLY material the
    narrative LLM is given, so it can add nothing that is not already found."""
    parts = [f"SUBJECT: {result.get('subject')}",
             f"CHAINS: {result.get('chain_count', 0)}; "
             f"UNCHAINED: {len(result.get('unchained') or [])}"]
    for n, chain in enumerate(result.get("chains") or [], 1):
        it = chain.get("integrity", {})
        parts.append(f"Chain {n}: verdict={it.get('verdict')}, "
                     f"weakest_link={it.get('weakest_link_strength')}, "
                     f"steps={it.get('total_steps')}, breaks={len(it.get('step_breaks', []))}, "
                     f"structural_gaps={len(chain.get('structural_gaps', []))}. "
                     f"Circumstances: "
                     + " | ".join(f"[{c.get('date') or 'undated'}] {c.get('summary')}"
                                  for c in chain.get("circumstances", [])))
    return "\n".join(parts)


_NARRATIVE_INSTRUCTION = (
    "You are rephrasing an already-computed set of investigative LEADS into a "
    "short, neutral, readable summary. STRICT RULES: rephrase ONLY what is given "
    "below; you MUST NOT add any new circumstance, link, date, or entity; you MUST "
    "NOT assert a conclusion, guilt, or that anything is proven; you MUST NOT weigh "
    "or invent innocent/alternative explanations. Every item is a LEAD for a human "
    "to investigate, not proof. Describe which chains hold, which are weak, and "
    "where they break. Do not editorialise."
)


def _resolve_llm():
    try:
        from modules.entity_resolution import _call_gemini
        return _call_gemini
    except Exception:
        return None


def narrate_evidence_chains(result: dict, llm_fn=None) -> str:
    """OPTIONAL, REMOVABLE [AI NARRATIVE] pass. Rephrases the deterministic result
    into prose. Never mutates `result`; adds no fact. Returns '' when no LLM is
    available or it produces nothing — the mode loses nothing without it."""
    if not isinstance(result, dict):
        return ""
    fn = llm_fn or _resolve_llm()
    if fn is None:
        return ""
    brief = _narrative_brief(result)
    prompt = f"{_NARRATIVE_INSTRUCTION}\n\n---\n{brief}\n---"
    try:
        text = fn(prompt)
    except Exception:
        return ""
    text = str(text or "").strip()
    if not text:
        return ""
    return ("[AI NARRATIVE] — rephrasing only; adds no fact, link, or conclusion; "
            "these are leads, not proof.\n" + text)
