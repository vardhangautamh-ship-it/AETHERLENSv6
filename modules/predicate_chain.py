"""
AetherLens — Predicate-Chain Integrity (Dimension 2).

Every load-bearing conclusion (the §16 risk level, each §09B pattern finding,
each account attribution) is modelled as a chain of foundational predicates,
and its CONCLUSION CONFIDENCE is bounded by the weakest predicate in the
chain — a conclusion is never reported more certain than its shakiest
foundational fact. The weakest predicate is named together with what would
overturn it, so a broken bottom predicate can never silently support a
confident top conclusion (the HYDRA failure class).

Deterministic and cited throughout: predicate confidences derive from
countable evidence properties (number of independent hard identifiers,
number of corroborating rows/files), never from an LLM. Display-additive
only — risk scores, levels, and fired patterns are measurements and are
never altered here.
"""

from modules.sanitizer import normalize_name_key, safe_list, safe_str


def _header_tokens(col) -> set:
    import re
    return {t for t in re.split(r"[^a-z0-9]+", str(col).lower().strip()) if t}


# Column-header tokens that assert CONTROL of an organisation (not mere
# transacting-with). A transfer TO a shell proves a money flow; a director/
# owner/partner row proves control — only the latter supports an ownership
# predicate.
_CONTROL_TOKENS = {"director", "owner", "proprietor", "partner", "promoter",
                   "shareholder", "beneficial", "signatory", "authorised",
                   "authorized", "designated"}


def identity_predicate(person: dict) -> dict:
    """P-IDENTITY: the records describe one real subject. Confidence graded
    by the anchor evidence recorded by entity resolution (Dimension 1)."""
    si = (person or {}).get("subject_identity") or {}
    anchors = safe_list(si.get("anchors"))
    sources = safe_list(si.get("sources"))
    name = safe_str((person or {}).get("confirmed_name", ""))
    if len(anchors) >= 2:
        conf = 90
        basis = (f"{len(anchors)} independent hard identifier(s) "
                 f"({', '.join(anchors[:3])}) across {len(sources)} file(s)")
    elif len(anchors) == 1:
        conf = 72
        basis = f"a single hard identifier ({anchors[0]})"
    else:
        conf = 48
        basis = ("name-only resolution — no hard identifier corroborates "
                 "the subject's identity")
    return {
        "id": "P-IDENTITY", "confidence": conf,
        "statement": f"Identity match: the records describe one subject, '{name}'",
        "basis": basis, "sources": sources[:4],
        "overturn": ("If the identity match is disproven (the records describe "
                     "a different person), every subject-attributed conclusion "
                     "falls."),
    }


def _subject_form_keys(person: dict) -> set:
    forms = {safe_str((person or {}).get("confirmed_name", ""))}
    forms |= {safe_str(v) for v in safe_list((person or {}).get("name_variants"))}
    si = (person or {}).get("subject_identity") or {}
    forms |= {safe_str(f) for f in (si.get("forms") or {})}
    forms.add(safe_str(si.get("canonical", "")))
    return {normalize_name_key(f) for f in forms if f.strip()}


def ownership_predicates(person: dict, onto, raw_documents: list) -> list:
    """P-OWNERSHIP: subject controls / is an officer of an organisation.
    Counted ONLY from rows where a subject name-form sits in a control-role
    column (director/owner/partner/…) and the organisation is named on the
    same row. Graded by corroboration: one uncorroborated row is a WEAK
    predicate and says so."""
    orgs = list(getattr(onto, "organizations", []) or []) if onto is not None else []
    if not orgs:
        return []
    forms = _subject_form_keys(person)
    org_by_norm = {}
    for o in orgs:
        nm = normalize_name_key(getattr(o, "name", ""))
        if nm:
            org_by_norm.setdefault(nm, o)

    hits: dict = {}   # org norm -> {"rows": int, "files": set}
    for d in (raw_documents or []):
        fname = safe_str(d.get("filename") or d.get("name") or "")
        for row in (d.get("structured_rows") or []):
            if not isinstance(row, dict):
                continue
            subj_in_control = any(
                _header_tokens(c) & _CONTROL_TOKENS
                and normalize_name_key(str(v or "")) in forms
                for c, v in row.items())
            if not subj_in_control:
                continue
            row_vals = {normalize_name_key(v) for v in row.values()}
            for on in org_by_norm:
                if on in row_vals:
                    h = hits.setdefault(on, {"rows": 0, "files": set()})
                    h["rows"] += 1
                    if fname:
                        h["files"].add(fname)

    preds = []
    for on in sorted(hits):
        h = hits[on]
        o = org_by_norm[on]
        files = sorted(h["files"])
        oname = safe_str(getattr(o, "name", ""))
        otype = safe_str(getattr(o, "type", "")) or "organization"
        if getattr(o, "offshore", False):
            otype += ", offshore"
        if len(files) >= 2:
            conf = 85
            basis = f"control asserted by {h['rows']} row(s) across {len(files)} files"
        elif h["rows"] >= 2:
            conf = 68
            basis = f"control asserted by {h['rows']} rows in a single file"
        else:
            conf = 52
            basis = "a SINGLE uncorroborated control row in a single file"
        preds.append({
            "id": f"P-OWNERSHIP:{oname}", "confidence": conf,
            "statement": f"Subject controls / is an officer of '{oname}' ({otype})",
            "basis": f"{basis}: {', '.join(files) or 'unattributed'}",
            "sources": files[:4], "org_type": safe_str(getattr(o, "type", "")),
            "offshore": bool(getattr(o, "offshore", False)),
            "overturn": (f"If that record is discredited or names a different "
                         f"person, every conclusion resting on control of "
                         f"'{oname}' falls."),
        })
    return preds[:3]


def _evidence_predicate(pattern_section: dict, raw_documents: list) -> dict:
    """P-EVIDENCE: the findings feeding the risk conclusion are grounded in
    case files. Graded by the number of distinct files cited."""
    pats = (pattern_section or {}).get("patterns") or []
    if pats:
        src_files = sorted({safe_str(s) for p in pats
                            for s in (p.get("sources") or []) if safe_str(s)})
        n = len(src_files)
        conf = 88 if n >= 2 else (68 if n == 1 else 50)
        return {
            "id": "P-EVIDENCE", "confidence": conf,
            "statement": (f"{len(pats)} deterministic pattern finding(s) are "
                          f"grounded in the case files"),
            "basis": f"pattern evidence cites {n} distinct file(s)",
            "sources": src_files[:4],
            "overturn": ("If the cited files are discredited, the pattern "
                         "findings lose their evidentiary basis."),
        }
    files = [safe_str(d.get("filename") or d.get("name") or "")
             for d in (raw_documents or [])]
    files = [f for f in files if f]
    n = len(files)
    conf = 85 if n >= 3 else (70 if n == 2 else 55)
    return {
        "id": "P-EVIDENCE", "confidence": conf,
        "statement": "Risk factors are grounded in the uploaded case files",
        "basis": f"{n} case file(s) ingested", "sources": files[:4],
        "overturn": ("If the case files are discredited, the risk factors "
                     "lose their evidentiary basis."),
    }


def build_risk_chain(person: dict, pattern_section: dict, risk_section: dict,
                     onto, raw_documents: list) -> dict:
    """The §16 conclusion's predicate chain. Ownership predicates enter the
    chain only when they are load-bearing for risk (shell/front/offshore
    organisations control-linked to the subject)."""
    preds = [identity_predicate(person),
             _evidence_predicate(pattern_section, raw_documents)]
    for p in ownership_predicates(person, onto, raw_documents):
        if p.get("org_type") in ("shell", "front") or p.get("offshore"):
            preds.append(p)
    chain_conf = min(p["confidence"] for p in preds)
    weakest = min(preds, key=lambda p: p["confidence"])   # first minimum wins
    score = (risk_section or {}).get("risk_score", 0)
    level = (risk_section or {}).get("risk_level", "?")
    return {
        "conclusion": f"RISK {level} ({score}/100)",
        "predicates": preds,
        "chain_confidence": chain_conf,
        "weakest": weakest,
    }


def render_chain_lines(chain: dict) -> list:
    """Deterministic §16 display block for a predicate chain."""
    lines = ["", "PREDICATE CHAIN — CONCLUSION INTEGRITY [DETERMINISTIC ANALYSIS]:"]
    lines.append(
        f"  conclusion: {chain['conclusion']} — conclusion confidence: "
        f"{chain['chain_confidence']}/100 (bounded by the weakest predicate; "
        f"a conclusion is never more certain than its shakiest foundation)")
    for p in chain["predicates"]:
        src = f" — Sources: {', '.join(p.get('sources') or [])}" if p.get("sources") else ""
        lines.append(f"  <- [{p['id']}] ({p['confidence']}/100) "
                     f"{p['statement']} — {p['basis']}{src}")
    w = chain["weakest"]
    lines.append(f"  WEAKEST PREDICATE: [{w['id']}] ({w['confidence']}/100) — "
                 f"{w['basis']}. {w['overturn']}")
    return lines


def annotate_conclusions(sections: dict, person: dict, onto,
                         raw_documents: list) -> None:
    """Attach predicate chains to the report's load-bearing conclusions.
    Mutates sections/person in place; display-additive only (no score, level,
    pattern, or attribution changes). Non-fatal by contract of the caller."""
    pattern_section = sections.get("pattern_analysis") or {}
    risk_section = sections.get("risk_assessment")
    if not isinstance(risk_section, dict):
        return

    own_preds = ownership_predicates(person, onto, raw_documents)
    pid = identity_predicate(person)

    # ── §16 risk conclusion ───────────────────────────────────────────────────
    chain = build_risk_chain(person, pattern_section, risk_section, onto,
                             raw_documents)
    risk_section["predicate_chain"] = chain["predicates"]
    risk_section["conclusion_confidence"] = chain["chain_confidence"]
    risk_section["weakest_predicate"] = chain["weakest"]
    items = risk_section.get("items")
    if isinstance(items, list):
        items.extend(render_chain_lines(chain))

    # ── each §09B pattern finding ─────────────────────────────────────────────
    for p in pattern_section.get("patterns") or []:
        srcs = [safe_str(s) for s in (p.get("sources") or []) if safe_str(s)]
        ev_conf = 88 if len(set(srcs)) >= 2 else (68 if srcs else 50)
        chain_preds = [pid, {"id": "P-EVIDENCE", "confidence": ev_conf}]
        blob = normalize_name_key(f"{p.get('explanation', '')} {' '.join(p.get('triggers') or [])}")
        for op in own_preds:
            oname = normalize_name_key(op["id"].split(":", 1)[-1])
            if oname and oname in blob:
                chain_preds.append(op)
        weakest = min(chain_preds, key=lambda x: x["confidence"])
        p["chain_confidence"] = weakest["confidence"]
        p["weakest_predicate"] = {
            "id": weakest["id"], "confidence": weakest["confidence"],
            "basis": weakest.get("basis", ""),
            "overturn": weakest.get("overturn", ""),
        }

    # ── each account attribution (benami consolidation) ──────────────────────
    for a in safe_list(person.get("account_attribution")):
        if not isinstance(a, dict):
            continue
        via = safe_list(a.get("via_aliases"))
        if len(via) > 1:
            a["confidence"] = min(85, pid["confidence"])
            a["basis"] = ("rests on the identity merge of the holder's "
                          "name-forms (see identity screen)")
        else:
            a["confidence"] = 85
            a["basis"] = "direct single-form binding on the account rows"

    print(f"[REPORT] Predicate chains: risk conclusion bounded at "
          f"{chain['chain_confidence']}/100 by {chain['weakest']['id']}; "
          f"{len(pattern_section.get('patterns') or [])} pattern(s) and "
          f"{len(safe_list(person.get('account_attribution')))} attribution(s) "
          f"annotated.")
