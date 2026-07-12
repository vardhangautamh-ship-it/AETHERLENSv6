"""
AetherLens — Contradiction & Inconsistency Hunting (Dimension 4).

Cover stories have seams: this module cross-checks every dated action,
declared figure, and identity-critical field against every other one and
surfaces the conflicts — with BOTH sides cited, a confidence on each side,
and an explicit refusal to resolve the conflict by guessing.

Five deterministic checks:
  1. anti-forensic timing — a deletion/destruction event dated within the
     window around an official notice/inquiry date;
  2. means mismatch — observed transaction volume far exceeding declared
     income;
  3. cross-document field conflict — an identity-critical field (passport,
     PAN, Aadhaar, voter ID) asserted with different values in different
     documents about the subject;
  4. timeline impossibility — a transaction with an organisation dated
     before that organisation existed (incorporation date);
  5. claim vs official record — a subject ASSERTION (statement/claim)
     inconsistent with an OFFICIAL record: claimed continuous presence or
     no-travel vs a dated entry/exit record, or a claimed status ("no
     passport", "no account", "not a director") vs a documentary record of
     the thing. Both sides cited; never resolved by guessing.

Findings live beside the structural gaps in §10 — a separate channel from
person["conflicts"]/anomaly_flags by design, so the risk and pattern streams
are never perturbed by this layer.
"""

import re
import datetime

from modules.sanitizer import normalize_name_key, safe_list, safe_str

_ISO_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")

_NOTICE_WORDS = ("notice", "summons", "inquiry initiated", "ecir", "fir regist",
                 "show-cause", "show cause")
_DELETE_WORDS = ("delet", "wiped", "formatted", "destroy", "shredd", "disposed",
                 "purge", "erased")

# Identity-critical single-valued fields: one person has exactly one of each,
# so two different values across documents is a genuine seam.
_FIELD_LABEL_RE = re.compile(
    r"\b(passport(?:\s*(?:no|number|#))?|pan(?:\s*(?:no|number))?"
    r"|aadhaar(?:\s*(?:no|number))?|voter\s*id)\b"
    r"[:\s\-]{1,4}([A-Z0-9][A-Z0-9\-\/ ]{3,18}[A-Z0-9])",
    re.IGNORECASE)

_INCOME_RE = re.compile(
    r"\b(?:declared|annual|itr)[^\n]{0,40}?(?:income|salary)[^\n]{0,30}?"
    r"(?:₹|rs\.?|inr)\s*([\d,]+(?:\.\d+)?)\s*(lakh|lac|crore)?",
    re.IGNORECASE)


def _norm(v) -> str:
    return " ".join(str(v or "").split()).lower()


def _hdr_tokens(col) -> set:
    return {t for t in re.split(r"[^a-z0-9]+", str(col).lower().strip()) if t}


def _parse_date(s):
    try:
        return datetime.date.fromisoformat(str(s).strip()[:10])
    except Exception:
        return None


def _parse_amount(num: str, unit: str) -> float:
    try:
        v = float(str(num).replace(",", ""))
    except Exception:
        return 0.0
    unit = (unit or "").lower()
    if unit in ("lakh", "lac"):
        v *= 100000
    elif unit == "crore":
        v *= 10000000
    return v


def _side(claim: str, source: str, attest: int = 1) -> dict:
    """One side of a conflict: the claim, its file, and a confidence graded
    by attestation count (a value seen in more places is not thereby TRUE —
    it is just better attested)."""
    return {"claim": safe_str(claim)[:160], "source": safe_str(source),
            "confidence": min(80, 60 + 10 * max(0, attest - 1))}

_NO_GUESS = ("NOT RESOLVED — surfaced for human review; the engine does not "
             "guess which side is true.")


# ── Check 5 vocabulary: subject CLAIM vs OFFICIAL RECORD ──────────────────────
# A CLAIM is a subject assertion; an OFFICIAL RECORD is registry/government
# evidence. Classification is by vocabulary in the item's own text — general,
# no per-case literals. An item matching the record vocabulary is a record
# even if it also quotes the claim ("…contradicts claimed travel").
_CLAIM_MARK_RE = re.compile(
    r"\b(claim(?:s|ed)?|assert(?:s|ed)?|state(?:s|d)|denies|denied|maintains?"
    r"|according to the subject|subject says)\b", re.IGNORECASE)
_RECORD_MARK_RE = re.compile(
    r"\b(entry[/\s-]?exit|entry record|exit record|immigration record|frro"
    r"|bureau of immigration|passport control|arrival record|departure record"
    r"|official record|official register|registry|registrar|e-?gate)\b",
    re.IGNORECASE)

# Travel affirmations in a record (verbs that document movement) and the
# negations that mark a record as AGREEING with a stay-claim (must not fire).
_TRAVEL_VERB_RE = re.compile(
    r"\b(exit(?:ed)?|depart(?:ed|ure(?:s)?)?|re-?enter(?:ed)?|"
    r"arriv(?:ed|al)|returned|travell?ed\s+to|left\s+(?:india|the country))\b",
    re.IGNORECASE)
_RECORD_NEG_RE = re.compile(
    r"\bno\s+(?:exit|departure|entry|foreign travel|travel|record of travel)"
    r"|did\s+not\s+(?:exit|depart|travel|leave)|\bnever\s+(?:exited|departed"
    r"|travell?ed|left)\b", re.IGNORECASE)

# Claim shapes: absolute no-travel, or continuous presence over a period.
_NO_TRAVEL_RE = re.compile(
    r"\bno\s+(?:foreign\s+)?travel\b|did\s+not\s+travel|never\s+travell?ed"
    r"|did\s+not\s+leave|never\s+left\b", re.IGNORECASE)
_PRESENCE_RE = re.compile(
    r"\b(?:was|remained|stayed|been)\s+in\s+[A-Z][\w .\-]{2,30}"
    r"\s+(?:throughout|during|for\s+(?:all|the whole)\s+of|the\s+entire)\b",
    re.IGNORECASE)

# Period-less absolute status claims vs the documentary record of the thing.
_STATUS_PAIRS = (
    (re.compile(r"\bno\s+passport\b|never\s+held\s+a\s+passport", re.IGNORECASE),
     re.compile(r"passport\s*(?:no|number|#|issued)", re.IGNORECASE),
     "the subject claims to hold no passport, but an official record cites one"),
    (re.compile(r"\bno\s+bank\s+account\b|never\s+opened\s+an?\s+(?:bank\s+)?account",
                re.IGNORECASE),
     re.compile(r"account\s*(?:no|number|opened|statement)", re.IGNORECASE),
     "the subject claims to hold no bank account, but an official record cites one"),
    (re.compile(r"\bnot\s+a\s+director\b|never\s+(?:been\s+)?a\s+director",
                re.IGNORECASE),
     re.compile(r"\bdirector\b", re.IGNORECASE),
     "the subject denies a directorship an official record documents"),
)

_MONTH_NUM = {m: i + 1 for i, m in enumerate(
    ("january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"))}
_MON = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*"
_MONTH_RANGE_RE = re.compile(
    rf"\b({_MON})\s*(?:[-–—]|to|through|until)\s*({_MON})\s+(\d{{4}})\b",
    re.IGNORECASE)
_MONTH_SINGLE_RE = re.compile(rf"\b({_MON})\s+(\d{{4}})\b", re.IGNORECASE)


def _month_no(name: str) -> int | None:
    n = str(name or "").lower()
    for full, num in _MONTH_NUM.items():
        if full.startswith(n[:3]):
            return num
    return None


def _month_end(year: int, month: int) -> datetime.date:
    import calendar
    return datetime.date(year, month, calendar.monthrange(year, month)[1])


def _claim_period(text: str):
    """(start, end) the CLAIM itself states, or None. The bounds are internal
    comparison limits only — findings always quote the verbatim claim text,
    so no invented precision is ever displayed. A claim without a stated year
    yields None (never imputed)."""
    isos = [d for d in (_parse_date(x) for x in _ISO_DATE_RE.findall(text)) if d]
    if len(isos) >= 2:
        return min(isos), max(isos)
    if len(isos) == 1:
        return isos[0], isos[0]
    m = _MONTH_RANGE_RE.search(text)
    if m:
        m1, m2, yr = _month_no(m.group(1)), _month_no(m.group(2)), int(m.group(3))
        if m1 and m2 and m1 <= m2:
            return datetime.date(yr, m1, 1), _month_end(yr, m2)
    m = _MONTH_SINGLE_RE.search(text)
    if m:
        mo, yr = _month_no(m.group(1)), int(m.group(2))
        if mo:
            return datetime.date(yr, mo, 1), _month_end(yr, mo)
    return None


def _claim_vs_record_findings(subj_keys: set, docs: list) -> list:
    """Check 5 — cross the subject's ASSERTIONS against OFFICIAL records.
    Deterministic: vocabulary classification + dated-window comparison; a
    record that itself negates travel (agreement) never fires; a claim by or
    a record about a third party never fires; nothing is inferred beyond the
    two texts, which are cited verbatim on each side."""

    def _doc_text(d):
        return safe_str(d.get("full_text") or d.get("raw_text") or d.get("text") or "")

    def _doc_name(d):
        return safe_str(d.get("filename") or d.get("name") or "")

    def _row_items(d):
        fname = _doc_name(d)
        for row in (d.get("structured_rows") or []):
            if not isinstance(row, dict):
                continue
            texts, name, ref = [], "", ""
            for col, val in row.items():
                v = safe_str(val).strip()
                if not v:
                    continue
                toks = _hdr_tokens(col)
                if toks & {"subject", "person", "name"} and not (toks & {"file", "event"}):
                    name = name or v
                elif toks & {"ref", "reference", "source"}:
                    ref = ref or v
                else:
                    texts.append(v)
            text = " | ".join(texts)
            if text:
                yield text, name, (f"{fname} [{ref}]" if ref else fname)

    def _line_items(d):
        fname = _doc_name(d)
        for line in _doc_text(d).splitlines():
            line = line.strip()
            if len(line) >= 25:
                yield line, "", fname

    claims, records = [], []
    for d in docs:
        # Structured rows are authoritative for tabular documents; the raw
        # text there is just the padded table render of the same rows, and
        # scanning both would duplicate every finding. Prose documents
        # (casenotes, statements) have no rows — they get the line scan.
        items = (list(_row_items(d)) if (d.get("structured_rows") or [])
                 else list(_line_items(d)))
        for text, name, src in items:
            # third-party guard: a named item must be a form of the subject
            if name and normalize_name_key(name) not in subj_keys:
                continue
            is_record = bool(_RECORD_MARK_RE.search(text))
            is_claim = bool(_CLAIM_MARK_RE.search(text)) and not is_record
            if is_record:
                records.append((text, src))
            elif is_claim:
                claims.append((text, src))

    findings, seen = [], set()
    for c_text, c_src in claims:
        period = _claim_period(c_text)
        absolute = bool(_NO_TRAVEL_RE.search(c_text))
        presence = bool(_PRESENCE_RE.search(c_text))
        for r_text, r_src in records:
            if len(findings) >= 3:
                break
            key = (c_src, r_src)
            if key in seen:
                continue
            # travel/presence vs entry-exit record
            if ((absolute or presence)
                    and _TRAVEL_VERB_RE.search(r_text)
                    and not _RECORD_NEG_RE.search(r_text)):
                r_dates = [d for d in (_parse_date(x)
                           for x in _ISO_DATE_RE.findall(r_text)) if d]
                hit = ((period and any(period[0] <= d <= period[1] for d in r_dates))
                       or (period is None and absolute and r_dates))
                if hit:
                    seen.add(key)
                    findings.append(_finding(
                        "CONTRADICTION", "CLAIM_VS_RECORD",
                        "subject claim vs official record",
                        "the subject's stated presence/no-travel claim is "
                        "inconsistent with a dated official entry/exit "
                        "record covering the same period.",
                        _side(c_text, c_src), _side(r_text, r_src)))
                    continue
            # claimed status vs documentary record of the thing
            for c_re, r_re, why in _STATUS_PAIRS:
                if c_re.search(c_text) and r_re.search(r_text):
                    seen.add(key)
                    findings.append(_finding(
                        "CONTRADICTION", "CLAIM_VS_RECORD",
                        "subject claim vs official record", why + ".",
                        _side(c_text, c_src), _side(r_text, r_src)))
                    break
    return findings


def _finding(label: str, ftype: str, nature: str, text: str,
             side_a: dict, side_b: dict) -> dict:
    return {
        "label": label, "type": ftype, "nature": nature,
        "finding": f"{label}: {text}",
        "side_a": side_a, "side_b": side_b, "resolution": _NO_GUESS,
        "sources": sorted({side_a.get("source", ""), side_b.get("source", "")}
                          - {""}),
    }


def hunt_contradictions(person: dict, onto, raw_documents: list,
                        timeline_data: dict = None) -> list:
    """Deterministic cross-check of every claim against every other claim.
    Returns cited findings; never resolves a conflict by guessing."""
    docs = [d for d in (raw_documents or []) if isinstance(d, dict)]
    if not docs:
        return []
    findings: list = []

    # Subject name-forms — used by checks 3 and 5 to scope items to the subject
    subj_keys = {normalize_name_key(safe_str(person.get("confirmed_name", "")))}
    subj_keys |= {normalize_name_key(safe_str(v))
                  for v in safe_list(person.get("name_variants"))}
    si = person.get("subject_identity") or {}
    subj_keys |= {normalize_name_key(f) for f in (si.get("forms") or {})}
    subj_keys.discard("")

    def _doc_text(d):
        return safe_str(d.get("full_text") or d.get("raw_text") or d.get("text") or "")

    def _doc_name(d):
        return safe_str(d.get("filename") or d.get("name") or "")

    # ── 1. anti-forensic action timed to an official notice ──────────────────
    notices, deletions = [], []
    for d in docs:
        fname = _doc_name(d)
        for line in _doc_text(d).splitlines():
            low = line.lower()
            m = _ISO_DATE_RE.search(line)
            if not m:
                continue
            dt = _parse_date(m.group(1))
            if not dt:
                continue
            if any(w in low for w in _NOTICE_WORDS):
                notices.append((dt, line.strip()[:140], fname))
            if any(w in low for w in _DELETE_WORDS):
                deletions.append((dt, line.strip()[:140], fname))
    for ev in (timeline_data or {}).get("events", []) or []:
        desc = safe_str((ev or {}).get("description") or (ev or {}).get("context"))
        dt = _parse_date(safe_str((ev or {}).get("date")))
        if dt and any(w in desc.lower() for w in _DELETE_WORDS):
            deletions.append((dt, desc[:140],
                              safe_str((ev or {}).get("source") or "timeline")))
    seen_pairs: set = set()
    for ndt, nline, nfile in sorted(notices):
        for ddt, dline, dfile in sorted(deletions):
            delta = (ddt - ndt).days
            if not (-2 <= delta <= 7):
                continue
            key = (ndt, ddt, dline[:60])
            if key in seen_pairs or len(seen_pairs) >= 2:
                continue
            seen_pairs.add(key)
            when = ("the SAME DAY as" if delta == 0 else
                    f"{delta} day(s) after" if delta > 0 else
                    f"{-delta} day(s) BEFORE")
            findings.append(_finding(
                "INCONSISTENCY", "DELETION_TIMED_TO_NOTICE",
                "anti-forensic action timed to an official notice",
                f"a deletion/destruction event is dated {when} an official "
                f"notice — behaviour consistent with evidence suppression "
                f"(or a tip-off if before).",
                _side(nline, nfile), _side(dline, dfile)))

    # ── 2. declared income vs observed transaction volume ────────────────────
    best_income, income_side = 0.0, None
    for d in docs:
        fname = _doc_name(d)
        for m in _INCOME_RE.finditer(_doc_text(d)):
            amt = _parse_amount(m.group(1), m.group(2))
            if amt > best_income:
                best_income = amt
                income_side = _side(m.group(0).strip(), fname)
    txns = list(getattr(onto, "transactions", []) or []) if onto is not None else []
    volume = sum(float(getattr(t, "amount", 0) or 0) for t in txns)
    txn_files = sorted({safe_str(getattr(t, "source", "")) for t in txns
                        if safe_str(getattr(t, "source", ""))})
    if best_income > 0 and txns and volume >= 3 * best_income:
        ratio = round(volume / best_income, 1)
        findings.append(_finding(
            "CONTRADICTION", "MEANS_MISMATCH",
            "declared income vs observed transaction volume",
            f"observed money movement of ₹{volume:,.0f} across "
            f"{len(txns)} transaction(s) is {ratio}x the declared annual "
            f"income of ₹{best_income:,.0f}.",
            income_side,
            _side(f"transactions totalling ₹{volume:,.0f}",
                  ", ".join(txn_files[:3]), attest=len(txn_files))))

    # ── 3. identity-critical field asserted differently across documents ─────
    field_vals: dict = {}   # field -> {value_norm: {"display", "files": set}}
    for d in docs:
        fname = _doc_name(d)
        text = _doc_text(d)
        # only documents that are ABOUT the subject may assert subject fields
        is_subject_doc = (
            normalize_name_key(safe_str(d.get("primary_subject"))) in subj_keys
            or any(k and k in _norm(text) for k in subj_keys))
        if not is_subject_doc:
            continue
        for m in _FIELD_LABEL_RE.finditer(text):
            label = re.split(r"[^a-z]+", m.group(1).lower())[0]
            raw_val = m.group(2).strip()
            val = re.sub(r"[\s\-\/]", "", raw_val).upper()
            if len(val) < 5 or not any(ch.isdigit() for ch in val):
                continue
            ent = field_vals.setdefault(label, {}).setdefault(
                val, {"display": raw_val, "files": set()})
            ent["files"].add(fname)
    for label in sorted(field_vals):
        vals = field_vals[label]
        if len(vals) < 2:
            continue
        (va, ea), (vb, eb) = sorted(vals.items())[:2]
        findings.append(_finding(
            "CONTRADICTION", "FIELD_CONFLICT",
            f"{label} asserted differently across documents",
            f"the subject's {label} is recorded as '{ea['display']}' in "
            f"{', '.join(sorted(ea['files']))} but as '{eb['display']}' in "
            f"{', '.join(sorted(eb['files']))} — one document is wrong or "
            f"one document is forged.",
            _side(f"{label} = {ea['display']}", ", ".join(sorted(ea["files"])),
                  attest=len(ea["files"])),
            _side(f"{label} = {eb['display']}", ", ".join(sorted(eb["files"])),
                  attest=len(eb["files"]))))

    # ── 4. transaction before the counterparty existed ────────────────────────
    inc_dates: dict = {}   # org norm -> (date, display, file)
    for d in docs:
        fname = _doc_name(d)
        for row in (d.get("structured_rows") or []):
            if not isinstance(row, dict):
                continue
            org_val, inc_dt = "", None
            for col, val in row.items():
                toks = _hdr_tokens(col)
                if toks & {"company", "firm", "entity", "organisation",
                           "organization"}:
                    org_val = " ".join(safe_str(val).split())
                if toks & {"incorporation", "incorporated", "registration",
                           "registered"} and toks & {"date"} or \
                        (toks & {"incorporation", "incorporated"}):
                    dt = _parse_date(safe_str(val))
                    if dt:
                        inc_dt = dt
            if org_val and inc_dt:
                inc_dates.setdefault(_norm(org_val), (inc_dt, org_val, fname))
    for t in txns:
        cp = _norm(getattr(t, "counterparty", ""))
        if cp not in inc_dates:
            continue
        tdt = _parse_date(safe_str(getattr(t, "date", "")))
        inc_dt, org_disp, roc_file = inc_dates[cp]
        if tdt and tdt < inc_dt:
            findings.append(_finding(
                "CONTRADICTION", "EVENT_BEFORE_ENTITY",
                "timeline impossibility",
                f"a transaction of ₹{float(getattr(t, 'amount', 0) or 0):,.0f} "
                f"with '{org_disp}' is dated {tdt.isoformat()}, BEFORE that "
                f"organisation's recorded incorporation on "
                f"{inc_dt.isoformat()} — the entity did not exist yet.",
                _side(f"transaction dated {tdt.isoformat()}",
                      safe_str(getattr(t, "source", ""))),
                _side(f"incorporated {inc_dt.isoformat()}", roc_file)))
            break   # one exemplar per case is enough; the rest is officer work

    # ── 5. subject claim vs official record ──────────────────────────────────
    findings.extend(_claim_vs_record_findings(subj_keys, docs))

    return findings[:10]


def render_contradiction_lines(findings: list) -> list:
    """Deterministic §10 display block for the contradiction scan."""
    if not findings:
        return ["", "Contradiction scan [DETERMINISTIC ANALYSIS]: no timed "
                    "anti-forensics, means mismatches, field conflicts, "
                    "timeline impossibilities, or claim-vs-record conflicts "
                    "detected across the provided documents."]
    lines = ["", f"CONTRADICTION & INCONSISTENCY SCAN ({len(findings)}) "
                 f"[DETERMINISTIC ANALYSIS] — claims cross-checked against "
                 f"each other:"]
    for f in findings:
        a, b = f.get("side_a") or {}, f.get("side_b") or {}
        lines.append(f"  {f['finding']}")
        lines.append(f"    side A (confidence {a.get('confidence', '?')}): "
                     f"\"{a.get('claim', '')}\" — {a.get('source', '')}")
        lines.append(f"    side B (confidence {b.get('confidence', '?')}): "
                     f"\"{b.get('claim', '')}\" — {b.get('source', '')}")
        lines.append(f"    {f.get('resolution', '')}")
    return lines
