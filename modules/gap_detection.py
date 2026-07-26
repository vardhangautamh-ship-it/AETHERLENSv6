"""
AetherLens — Structural Gap Detection (Dimension 3).

Hunts ABSENCES: what the provided data should contain but does not. The HYDRA
failure class this answers is entities silently vanishing — a gap the system
does not notice is a conclusion the officer cannot challenge.

Four deterministic checks, every finding cited to files:
  1. unbound identifiers — a phone/account that appears in the case but is
     never bound to any person;
  2. flow asymmetry — one-directional money/call records, and credits whose
     origin field is simply empty;
  3. named-party footprint — a person named in a control role (director,
     owner, …) with zero presence in the case's financial records;
  4. trail termination — money routed to a counterparty that has no
     receiving-side records anywhere in the case ("trail goes cold").

Three honest kinds of unknown, stated on every finding:
  SEARCHED-ABSENT  — we looked in the provided data and found nothing;
  NOT-PROVIDED     — the record type that would answer this was never uploaded;
  UNDETERMINABLE   — the provided structure cannot answer this reliably.

NEVER fabricates the missing piece: a person-shaped value spotted in a
non-binding column is cited as a manual-verification pointer, never bound.
Findings live in person["structural_gaps"] and render in §10 — a separate
channel from person["data_gaps"] and the anomaly/risk streams by design.
"""

import re

from modules.sanitizer import normalize_name_key, safe_list, safe_str

KIND_LABELS = {
    "searched_absent": "SEARCHED-ABSENT",
    "not_provided":    "NOT-PROVIDED",
    "undeterminable":  "UNDETERMINABLE",
}

# Bounded scan for a person-shaped fragment INSIDE free text (a remarks cell).
# Candidates are validated against the shared person-cell machinery before
# being cited — and they are only ever cited, never bound.
_NAME_FRAGMENT_RE = re.compile(r"\b([A-Z][a-z]{2,20}(?:\s+[A-Z][a-z]{1,20}){1,2})\b")


def _gap(gtype: str, kind: str, finding: str, why: str, sources: list) -> dict:
    return {
        "type": gtype, "kind": kind, "kind_label": KIND_LABELS[kind],
        "finding": finding, "why_it_matters": why,
        "sources": sorted({safe_str(s) for s in sources if safe_str(s)}),
    }


def _person_shaped_fragments(text: str) -> list:
    """Person-shaped fragments inside free text, validated with the same
    stopword machinery the extractor uses. Citation pointers only."""
    from modules.entity_resolution import RE_PERSON_NAME_CELL
    from modules.data_ingestion import NAME_STOPWORDS, _ORG_NAME_WORDS
    out = []
    for m in _NAME_FRAGMENT_RE.finditer(str(text or "")):
        frag = m.group(1)
        if not RE_PERSON_NAME_CELL.match(frag):
            continue
        words = frag.split()
        if any(w in NAME_STOPWORDS or w in _ORG_NAME_WORDS for w in words):
            continue
        if frag not in out:
            out.append(frag)
    return out[:2]


def detect_structural_gaps(person: dict, onto, raw_documents: list) -> list:
    """Deterministic structural-absence scan. Returns cited gap findings;
    flags only — never invents the missing content."""
    from modules.entity_resolution import _bind_id_col_type, _bind_norm_phone
    from modules.data_ingestion import _is_name_column
    from modules.predicate_chain import _CONTROL_TOKENS, _subject_form_keys
    try:
        from modules.data_mining import _GENERIC_TOKENS
    except Exception:
        _GENERIC_TOKENS = set()

    docs = [d for d in (raw_documents or []) if isinstance(d, dict)]
    if not docs:
        return []
    gaps: list = []

    def _hdr_tokens(col):
        return {t for t in re.split(r"[^a-z0-9]+", str(col).lower().strip()) if t}

    # ── 1. unbound identifiers (phones / accounts) ────────────────────────────
    bound_keys: set = set()
    for ident in safe_list(person.get("anchor_identities")):
        bound_keys |= set((ident or {}).get("anchor_map") or {})

    seen: dict = {}   # key -> {"display", "files", "cols", "hints"}
    for d in docs:
        fname = safe_str(d.get("filename") or d.get("name") or "")
        for row in (d.get("structured_rows") or []):
            if not isinstance(row, dict):
                continue
            for col, val in row.items():
                if _is_name_column(col):
                    continue
                t = _bind_id_col_type(col)
                if t not in ("phone", "account"):
                    continue
                sval = safe_str(val).strip()
                if not sval or sval.lower() in ("nan", "none", "-"):
                    continue
                if t == "phone":
                    normp = _bind_norm_phone(sval)
                    if not normp:
                        continue
                    key = f"phone:{normp}"
                else:
                    if len(sval) < 4:
                        continue
                    key = f"acct:{sval.lower()}"
                ent = seen.setdefault(key, {"display": sval, "files": set(),
                                            "cols": set(), "hints": []})
                ent["files"].add(fname)
                ent["cols"].add(safe_str(col))
                # A person-shaped value in a NON-name column of the same row
                # is a verification pointer, never a binding.
                for c2, v2 in row.items():
                    if c2 == col or _is_name_column(c2):
                        continue
                    for frag in _person_shaped_fragments(v2):
                        hint = (frag, safe_str(c2))
                        if hint not in ent["hints"]:
                            ent["hints"].append(hint)

    # phones found only in free text join the unbound check too
    for ph, srcs in (person.get("phone_sources") or {}).items():
        k10 = _bind_norm_phone(ph)
        if k10:
            key = f"phone:{k10}"
            if key not in seen and key not in bound_keys:
                seen[key] = {"display": safe_str(ph),
                             "files": set(safe_list(srcs)), "cols": set(),
                             "hints": []}

    for key in sorted(seen):
        if key in bound_keys:
            continue
        ent = seen[key]
        what = "phone" if key.startswith("phone:") else "account"
        if ent["hints"]:
            frag, col = ent["hints"][0]
            gaps.append(_gap(
                "UNBOUND_IDENTIFIER", "undeterminable",
                f"GAP: {what} {ent['display']} is never bound to a person. A "
                f"person-shaped value ('{frag}') appears in non-binding column "
                f"'{col}' of the same row — cited for manual verification, NOT "
                f"bound.",
                "An identifier without an owner is an entity that can vanish "
                "from every person-level conclusion.",
                ent["files"]))
        else:
            gaps.append(_gap(
                "UNBOUND_IDENTIFIER", "searched_absent",
                f"GAP: {what} {ent['display']} appears in the case but no name "
                f"ever co-occurs with it in the provided data.",
                "An identifier without an owner is an entity that can vanish "
                "from every person-level conclusion.",
                ent["files"]))
        if sum(1 for g in gaps if g["type"] == "UNBOUND_IDENTIFIER") >= 6:
            break

    # ── 2. flow asymmetry ─────────────────────────────────────────────────────
    txns = list(getattr(onto, "transactions", []) or []) if onto is not None else []
    ins = [t for t in txns if safe_str(getattr(t, "direction", "")) == "in"]
    outs = [t for t in txns if safe_str(getattr(t, "direction", "")) == "out"]
    if len(outs) >= 2 and not ins:
        gaps.append(_gap(
            "ONE_WAY_FLOW", "not_provided",
            f"GAP: all {len(outs)} recorded money movements are OUTBOUND — no "
            f"inflow records were provided; the origin of the subject's funds "
            f"is absent from this case.",
            "Without inflows, source-of-funds conclusions cannot be grounded.",
            [getattr(t, "source", "") for t in outs]))
    elif len(ins) >= 2 and not outs:
        gaps.append(_gap(
            "ONE_WAY_FLOW", "not_provided",
            f"GAP: all {len(ins)} recorded money movements are INBOUND — no "
            f"outflow records were provided; the application of funds is "
            f"absent from this case.",
            "Without outflows, use-of-funds conclusions cannot be grounded.",
            [getattr(t, "source", "") for t in ins]))

    orphan_credits = [t for t in ins if not safe_str(getattr(t, "counterparty", "")).strip()]
    if orphan_credits:
        total = sum(float(getattr(t, "amount", 0) or 0) for t in orphan_credits)
        gaps.append(_gap(
            "MISSING_ORIGIN", "searched_absent",
            f"GAP: {len(orphan_credits)} credit(s) totalling "
            f"₹{total:,.0f} into the subject's account carry NO recorded "
            f"origin — the counterparty field is empty on the provided rows.",
            "Money with no recorded origin is the classic head of a laundering "
            "chain; the origin must be obtained, not assumed.",
            [getattr(t, "source", "") for t in orphan_credits]))

    # one-directional call records (only when a direction column exists —
    # without one, direction cannot be determined and is not guessed)
    _dir_vals = {"in": 0, "out": 0}
    _dir_files: set = set()
    for d in docs:
        fname = safe_str(d.get("filename") or d.get("name") or "")
        for row in (d.get("structured_rows") or []):
            if not isinstance(row, dict):
                continue
            for col, val in row.items():
                toks = _hdr_tokens(col)
                if not (toks & {"call", "direction"} and toks & {"type", "direction"}):
                    continue
                v = normalize_name_key(val)
                if v in ("in", "incoming", "inbound", "received"):
                    _dir_vals["in"] += 1
                    _dir_files.add(fname)
                elif v in ("out", "outgoing", "outbound", "dialled", "dialed"):
                    _dir_vals["out"] += 1
                    _dir_files.add(fname)
    if _dir_vals["in"] + _dir_vals["out"] >= 3 and 0 in _dir_vals.values():
        present = "OUTGOING" if _dir_vals["out"] else "INCOMING"
        missing = "inbound" if _dir_vals["out"] else "outbound"
        gaps.append(_gap(
            "ONE_WAY_CALLS", "not_provided",
            f"GAP: all {max(_dir_vals.values())} call records are {present} — "
            f"the {missing} CDR was not provided.",
            "One-directional call records hide who initiates contact.",
            _dir_files))

    # ── 3. named-party footprint ──────────────────────────────────────────────
    from modules.entity_resolution import RE_PERSON_NAME_CELL
    subject_keys = _subject_form_keys(person)
    fin_rows: list = []       # (file, row) of financial rows
    control_people: dict = {} # norm -> {"display", "files"}
    for d in docs:
        fname = safe_str(d.get("filename") or d.get("name") or "")
        for row in (d.get("structured_rows") or []):
            if not isinstance(row, dict):
                continue
            hdrs = {c: _hdr_tokens(c) for c in row}
            if any(_bind_id_col_type(c) == "account" or (toks & {"amount", "txn", "balance"})
                   for c, toks in hdrs.items()):
                fin_rows.append((fname, row))
            for col, val in row.items():
                if not (hdrs[col] & _CONTROL_TOKENS):
                    continue
                sval = " ".join(safe_str(val).split())
                if not sval or not RE_PERSON_NAME_CELL.match(sval):
                    continue
                k = normalize_name_key(sval)
                if k in subject_keys:
                    continue
                ent = control_people.setdefault(k, {"display": sval, "files": set()})
                ent["files"].add(fname)

    for k in sorted(control_people):
        ent = control_people[k]
        if fin_rows:
            present = any(
                k == normalize_name_key(" ".join(safe_str(v).split()))
                for _, row in fin_rows for v in row.values())
            if not present:
                gaps.append(_gap(
                    "NO_FINANCIAL_FOOTPRINT", "searched_absent",
                    f"GAP: '{ent['display']}' is named in a control role "
                    f"({', '.join(sorted(ent['files']))}) but has ZERO footprint "
                    f"in the provided financial records — unexplained absence: "
                    f"data gap or pure front.",
                    "A controller who never touches the money is either "
                    "under-documented or a nominee.",
                    ent["files"]))
        else:
            gaps.append(_gap(
                "NO_FINANCIAL_FOOTPRINT", "not_provided",
                f"GAP: '{ent['display']}' is named in a control role but the "
                f"case contains no financial records at all to check against.",
                "Control claims cannot be tested without financial records.",
                ent["files"]))

    # ── 4. trail termination ──────────────────────────────────────────────────
    receivers: dict = {}   # counterparty norm -> {"display","n","total","files"}
    for t in outs:
        cp = " ".join(safe_str(getattr(t, "counterparty", "")).split())
        if not cp:
            continue
        cpn = normalize_name_key(cp)
        if cpn in _GENERIC_TOKENS:
            continue
        ent = receivers.setdefault(cpn, {"display": cp, "n": 0, "total": 0.0,
                                         "files": set()})
        ent["n"] += 1
        ent["total"] += float(getattr(t, "amount", 0) or 0)
        src = safe_str(getattr(t, "source", ""))
        if src:
            ent["files"].add(src)
    in_side: set = {normalize_name_key(getattr(t, "counterparty", "")) for t in ins}
    # "Receiving-side records" means the entity HOLDS something on financial
    # rows (account holder / payer). Two non-evidence appearances must not
    # silence a cold trail: a ROC registration row is not money movement, and
    # the counterparty/payee label of the very transfers that created the
    # trail is the trail itself, not its receiving side.
    _RECEIVER_LABEL_TOKENS = {"counterparty", "payee", "beneficiary",
                              "recipient", "receiver"}
    holder_side: set = set()
    for fname, row in fin_rows:
        for col, val in row.items():
            if _is_name_column(col) and not (_hdr_tokens(col) & _RECEIVER_LABEL_TOKENS):
                holder_side.add(normalize_name_key(val))
    for cpn in sorted(receivers):
        ent = receivers[cpn]
        if ent["n"] < 2 and ent["total"] < 500000:
            continue
        if cpn in in_side or cpn in holder_side:
            continue
        gaps.append(_gap(
            "TRAIL_COLD", "not_provided",
            f"GAP: TRAIL GOES COLD — ₹{ent['total']:,.0f} routed to "
            f"'{ent['display']}' across {ent['n']} transfer(s), but the case "
            f"contains no receiving-side records (no account, no onward "
            f"movement) for this entity.",
            "A flow that terminates at an entity with no records is where "
            "layering hides; the next records must be requisitioned.",
            ent["files"]))

    return gaps[:12]


def render_gap_lines(gaps: list) -> list:
    """Deterministic §10 display block for structural gaps."""
    if not gaps:
        return ["", "Structural gap scan [DETERMINISTIC ANALYSIS]: no unbound "
                    "identifiers, one-way flows, footprint absences, or cold "
                    "trails detected."]
    lines = ["", f"STRUCTURAL GAP SCAN ({len(gaps)}) [DETERMINISTIC ANALYSIS] "
                 f"— what should be here but is not:"]
    for g in gaps:
        src = f" Sources: {', '.join(g.get('sources') or [])}." if g.get("sources") else ""
        lines.append(f"  [{g['kind_label']}] {g['finding']} Why it matters: "
                     f"{g['why_it_matters']}{src}")
    lines.append(
        "  (Unknown kinds: SEARCHED-ABSENT = looked in the provided data, "
        "found nothing; NOT-PROVIDED = the record type was never uploaded; "
        "UNDETERMINABLE = the provided structure cannot answer reliably. No "
        "missing content has been inferred or fabricated.)")
    return lines
