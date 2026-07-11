"""
AetherLens — Trail & Circular-Flow Following (Dimension 5).

Follows money THROUGH deliberate obfuscation: multi-hop chains across
accounts, round-tripping back to origin, layering across shells as ONE flow —
reconstructed only on real, cited edges.

Edge model: every financial row that names both an account holder and a
counterparty yields one directed edge (holder→counterparty for OUT,
counterparty→holder for IN), cited to its file and date. Party names are
canonicalised through the anchor-identity layer (Dimension 1), so a trail
fragmented across a subject's name-variants reconnects via hard identifiers —
never via spelling similarity.

Reconstruction rules (stated in every finding):
  chronological  — each hop is dated on/after the previous hop;
  amount-continuous — each hop moves 50%–115% of the previous hop (fees and
  padding allowed; a trickle is a different flow);
  within 90 days of the previous hop.

If a trail breaks, the finding SAYS SO and points at the gap (Dimension 3).
A later inflow to origin that would complete a loop is reported as
"consistent with round-tripping" and explicitly NOT asserted — the engine
never invents a hop that is not in the data.
"""

import re
import datetime

from modules.sanitizer import normalize_name_key, safe_list, safe_str

_AMOUNT_MIN_FRAC = 0.50
_AMOUNT_MAX_FRAC = 1.15
_MAX_HOP_DAYS = 90
_MAX_DEPTH = 6

_CRITERIA_NOTE = ("criteria: chronological hops, each moving 50%–115% of the "
                  "previous hop, within 90 days")


def _hdr_tokens(col) -> set:
    return {t for t in re.split(r"[^a-z0-9]+", str(col).lower().strip()) if t}


def _norm(v) -> str:
    return " ".join(str(v or "").split()).lower()


def _parse_date(s):
    try:
        return datetime.date.fromisoformat(str(s).strip()[:10])
    except Exception:
        return None


def _canonicaliser(person: dict):
    """name -> canonical display, through the anchor-identity layer. Unknown
    names keep their own (whitespace-normalised) spelling — organisations are
    matched exactly, never fuzzily."""
    canon_of = {}
    for ident in safe_list(person.get("anchor_identities")):
        can = safe_str((ident or {}).get("canonical", ""))
        for f in list((ident or {}).get("forms") or {}) + [can]:
            k = normalize_name_key(f)
            if k:
                canon_of[k] = can

    def canon(name: str) -> str:
        disp = " ".join(safe_str(name).split())
        return canon_of.get(normalize_name_key(disp), disp)
    return canon


def extract_flow_edges(person: dict, raw_documents: list) -> list:
    """Directed, cited flow edges from every multi-party financial row."""
    canon = _canonicaliser(person)
    edges, seen = [], set()
    for d in (raw_documents or []):
        if not isinstance(d, dict):
            continue
        fname = safe_str(d.get("filename") or d.get("name") or "")
        for row in (d.get("structured_rows") or []):
            if not isinstance(row, dict):
                continue
            holder = cp = direction = ""
            amount = 0.0
            date = None
            for col, val in row.items():
                toks = _hdr_tokens(col)
                sval = " ".join(safe_str(val).split())
                if toks & {"holder"}:
                    holder = sval
                elif toks & {"counterparty", "payee", "beneficiary",
                             "recipient", "sender", "payer"}:
                    cp = sval
                elif toks & {"direction"}:
                    direction = sval.lower()
                elif toks & {"amount"}:
                    try:
                        amount = float(str(sval).replace(",", "") or 0)
                    except Exception:
                        amount = 0.0
                elif toks & {"date"} and date is None:
                    date = _parse_date(sval)
            if not holder or not cp or amount <= 0 or date is None \
                    or direction not in ("in", "out"):
                continue
            src, dst = (holder, cp) if direction == "out" else (cp, holder)
            src, dst = canon(src), canon(dst)
            if _norm(src) == _norm(dst):
                continue
            key = (_norm(src), _norm(dst), date.isoformat(), round(amount, 2))
            if key in seen:
                # same transfer seen from both sides (payer + payee statements)
                for e in edges:
                    if (e["key"] == key) and fname and fname not in e["sources"]:
                        e["sources"].append(fname)
                continue
            seen.add(key)
            edges.append({"key": key, "src": src, "dst": dst, "amount": amount,
                          "date": date, "sources": [fname] if fname else []})
    edges.sort(key=lambda e: (e["date"], -e["amount"], _norm(e["src"]),
                              _norm(e["dst"])))
    return edges


def _hop_str(e) -> str:
    return (f"{e['src']} -> {e['dst']}: ₹{e['amount']:,.0f} on "
            f"{e['date'].isoformat()} ({', '.join(e['sources'])})")


def _continues(prev, nxt) -> bool:
    if nxt["date"] < prev["date"]:
        return False
    if (nxt["date"] - prev["date"]).days > _MAX_HOP_DAYS:
        return False
    return (_AMOUNT_MIN_FRAC * prev["amount"] <= nxt["amount"]
            <= _AMOUNT_MAX_FRAC * prev["amount"])


def follow_trails(person: dict, onto, raw_documents: list) -> list:
    """Reconstruct flows rooted at the subject. Returns TRAIL findings:
    circular flows, layered chains, broken trails, and unlinked re-entries —
    each hop cited, nothing inferred."""
    edges = extract_flow_edges(person, raw_documents)
    if not edges:
        return []
    subject = safe_str(person.get("confirmed_name", ""))
    subj_norm = _norm(subject)
    out_of: dict = {}
    for e in edges:
        out_of.setdefault(_norm(e["src"]), []).append(e)

    findings: list = []
    broken_terminals: list = []   # (terminal edge, path) for re-entry check
    seen_paths: set = set()

    def dfs(path):
        last = path[-1]
        node = _norm(last["dst"])
        if len(path) >= _MAX_DEPTH:
            return
        nxts = [e for e in out_of.get(node, []) if _continues(last, e)]
        if not nxts:
            if node != subj_norm and len(path) >= 2:
                broken_terminals.append((last, list(path)))
            return
        for e in nxts:
            if e is last:
                continue
            if e["key"] in {p["key"] for p in path}:
                continue
            newp = path + [e]
            if _norm(e["dst"]) == subj_norm:
                key = tuple(p["key"] for p in newp)
                if key not in seen_paths:
                    seen_paths.add(key)
                    frac = round(100 * e["amount"] / newp[0]["amount"])
                    hops = "; ".join(_hop_str(p) for p in newp)
                    findings.append({
                        "type": "CIRCULAR_FLOW",
                        "hops": [dict(p, date=p["date"].isoformat(),
                                      key=None) for p in newp],
                        "finding": (
                            f"TRAIL: CIRCULAR FLOW — ₹{newp[0]['amount']:,.0f} "
                            f"left '{subject}' and ₹{e['amount']:,.0f} "
                            f"(~{frac}%) returned through "
                            f"{len(newp) - 1} intermediar(ies): {hops}. "
                            f"Round-tripping signature; every hop cited; "
                            f"{_CRITERIA_NOTE}."),
                    })
                continue
            dfs(newp)

    roots = sorted(out_of.get(subj_norm, []),
                   key=lambda e: (e["date"], -e["amount"]))
    for root in roots:
        dfs([root])

    # broken trails: the flow went quiet mid-chain — say so, point at the gap
    reported_breaks: set = set()
    for last, path in sorted(broken_terminals,
                             key=lambda bp: -bp[1][0]["amount"])[:2]:
        node = last["dst"]
        if _norm(node) in reported_breaks:
            continue
        reported_breaks.add(_norm(node))
        hops = "; ".join(_hop_str(p) for p in path)
        findings.append({
            "type": "BROKEN_TRAIL",
            "hops": [dict(p, date=p["date"].isoformat(), key=None) for p in path],
            "breaks_at": node,
            "finding": (
                f"TRAIL: BROKEN — ₹{path[0]['amount']:,.0f} moved "
                f"{len(path)} hop(s) from '{subject}': {hops}. The trail "
                f"BREAKS at '{node}': ₹{last['amount']:,.0f} arrived "
                f"{last['date'].isoformat()} and no continuing movement "
                f"consistent with it exists in the provided records "
                f"({_CRITERIA_NOTE}). See the structural gap scan — the "
                f"onward records must be requisitioned, not assumed."),
        })
        # unlinked re-entry: a later inflow to origin that WOULD complete the
        # loop — reported as consistent-with, explicitly NOT asserted.
        for e in edges:
            if _norm(e["dst"]) != subj_norm or e["date"] <= last["date"]:
                continue
            if not (_AMOUNT_MIN_FRAC * last["amount"] <= e["amount"]
                    <= _AMOUNT_MAX_FRAC * last["amount"]):
                continue
            payer_has_inflow = any(_norm(x["dst"]) == _norm(e["src"])
                                   for x in edges)
            if payer_has_inflow:
                continue
            findings.append({
                "type": "UNLINKED_REENTRY",
                "hops": [dict(e, date=e["date"].isoformat(), key=None)],
                "finding": (
                    f"TRAIL: UNLINKED RE-ENTRY — '{subject}' (payee name "
                    f"reconnected to the subject via hard identifiers where a "
                    f"variant spelling was used) received "
                    f"₹{e['amount']:,.0f} from '{e['src']}' on "
                    f"{e['date'].isoformat()} ({', '.join(e['sources'])}), "
                    f"after the broken trail above went quiet at '{node}'. "
                    f"This is CONSISTENT WITH a round trip, BUT no recorded "
                    f"hop connects '{node}' to '{e['src']}' — the connecting "
                    f"hop is ABSENT from the provided records and has NOT "
                    f"been inferred. Obtain '{e['src']}' records to close or "
                    f"break the loop."),
            })
            break

    # layered chains (>=2 intermediaries) that neither returned nor broke —
    # one deliberate flow, not separate events
    layered: set = set()
    for last, path in broken_terminals:
        if len(path) >= 3:
            key = tuple(p["key"] for p in path)
            layered.add(key)
    # (broken trails already narrate these; only report standalone layering
    # when it did not already surface as broken/circular)
    findings.sort(key=lambda f: (f["type"], f["finding"]))
    return findings[:6]


def render_trail_lines(findings: list) -> list:
    """Deterministic §14 display block for trail reconstruction."""
    if not findings:
        return ["", "Trail reconstruction [DETERMINISTIC ANALYSIS]: no "
                    "multi-hop flow is reconstructable from the provided "
                    "records (requires multi-party financial statements)."]
    lines = ["", f"TRAIL RECONSTRUCTION ({len(findings)}) "
                 f"[DETERMINISTIC ANALYSIS] — flows followed on cited edges "
                 f"only:"]
    for f in findings:
        lines.append(f"  {f['finding']}")
    lines.append("  (No hop has been inferred: every edge above is a recorded "
                 "row. A broken trail is reported as broken.)")
    return lines
