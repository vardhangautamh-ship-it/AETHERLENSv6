"""
AetherLens — Contradiction & Inconsistency Hunting (Dimension 4).

Cover stories have seams: this module cross-checks every dated action,
declared figure, and identity-critical field against every other one and
surfaces the conflicts — with BOTH sides cited, a confidence on each side,
and an explicit refusal to resolve the conflict by guessing.

Four deterministic checks:
  1. anti-forensic timing — a deletion/destruction event dated within the
     window around an official notice/inquiry date;
  2. means mismatch — observed transaction volume far exceeding declared
     income;
  3. cross-document field conflict — an identity-critical field (passport,
     PAN, Aadhaar, voter ID) asserted with different values in different
     documents about the subject;
  4. timeline impossibility — a transaction with an organisation dated
     before that organisation existed (incorporation date).

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
    subj_keys = {normalize_name_key(safe_str(person.get("confirmed_name", "")))}
    subj_keys |= {normalize_name_key(safe_str(v))
                  for v in safe_list(person.get("name_variants"))}
    si = person.get("subject_identity") or {}
    subj_keys |= {normalize_name_key(f) for f in (si.get("forms") or {})}
    subj_keys.discard("")

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

    return findings[:10]


def render_contradiction_lines(findings: list) -> list:
    """Deterministic §10 display block for the contradiction scan."""
    if not findings:
        return ["", "Contradiction scan [DETERMINISTIC ANALYSIS]: no timed "
                    "anti-forensics, means mismatches, field conflicts, or "
                    "timeline impossibilities detected across the provided "
                    "documents."]
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
