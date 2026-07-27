"""
PHASE 1.5 STEP 10 — LAWFUL TARGETING: risk-based prioritisation + target packages.

DECISION SUPPORT ONLY — HARD CONSTRAINT (binding on every function here and on
any future edit to this module):

  * Every output of this module is a summary FOR HUMAN REVIEW. Nothing here
    authorises, schedules, or triggers any action. Every package and every
    prioritised list carries HUMAN_AUTHORISATION_NOTICE verbatim and sets
    human_authorisation_required = True.
  * Prioritisation is EVIDENCE-BASED ONLY: it reads the deterministic risk
    score (§16) and the fired §09B patterns (documents, numbers, behaviour).
    It never reads — and its inputs do not contain — nationality, ethnicity,
    or religion. No identity attribute may ever become a ranking input.
  * Deterministic and honest: same input → same output; missing values are
    reported as missing (an unscored case ranks LAST with an explicit note),
    never fabricated. No case-name / subject-name / file-name branches.

Input contract (duck-typed, both pipelines): an "analysed case" is either the
dict returned by _generate_report_inner (has "sections" and "subject") or a
bare sections dict (optionally with a "subject"/"subject_name" key beside it).
This module only PROJECTS what §09B / §09C / §16 / §10 already established —
it performs no new analysis, so the report stays the single source of truth.
"""

# Verbatim on every package and every prioritised list. Do not shorten.
HUMAN_AUTHORISATION_NOTICE = (
    "MANDATORY HUMAN AUTHORISATION: This is decision support only. It is an "
    "evidence-cited summary prepared for review by a competent human officer. "
    "It authorises NO investigative, enforcement, or field action. Any action "
    "requires independent human review and written authorisation under "
    "applicable law. Prioritisation is evidence-based (documents, numbers, "
    "behaviour) — no nationality, ethnicity, or religion was used."
)

_MAX_RISK_BASIS_LINES = 20


def _extract_sections(case) -> dict | None:
    """Normalise an analysed case to its sections dict, or None if malformed."""
    if not isinstance(case, dict):
        return None
    sections = case.get("sections") if isinstance(case.get("sections"), dict) else case
    # A sections dict must show at least one of the sections we project from.
    for key in ("pattern_analysis", "risk_assessment", "immigration_profile", "data_gaps"):
        if isinstance(sections.get(key), dict):
            return sections
    return None


def _extract_subject(case, sections: dict) -> str:
    for key in ("subject", "subject_name"):
        v = case.get(key) if isinstance(case, dict) else None
        if isinstance(v, str) and v.strip():
            return v.strip()
    v = sections.get("subject") if isinstance(sections, dict) else None
    return v.strip() if isinstance(v, str) and v.strip() else "Unknown Subject"


def _has_cited_basis(risk_basis) -> bool:
    """True when at least one §16 risk-basis line carries a per-factor citation
    (an Evidence:/Source: marker). The bare RISK SCORE:/CONFIDENCE: header lines
    do NOT corroborate the number — cited evidence is the currency, not the score."""
    for l in (risk_basis or []):
        low = str(l).lower()
        if "evidence:" in low or "source:" in low or "sources:" in low:
            return True
    return False


def build_target_package(case) -> dict | None:
    """Build ONE officer-review target package from an analysed case.

    Pure projection of the report sections (no new analysis, no LLM). Every
    pattern keeps its citations; the risk basis lines are carried as rendered
    by §16 (already cited per factor). Returns None on malformed input."""
    sections = _extract_sections(case)
    if sections is None:
        return None
    subject = _extract_subject(case, sections)

    pa = sections.get("pattern_analysis") if isinstance(sections.get("pattern_analysis"), dict) else {}
    patterns = [dict(p) for p in (pa.get("patterns") or []) if isinstance(p, dict)]
    strong_count = sum(1 for p in patterns
                       if str(p.get("confidence", "")).upper() == "STRONG")

    risk = sections.get("risk_assessment") if isinstance(sections.get("risk_assessment"), dict) else {}
    risk_score = risk.get("risk_score", None)
    risk_score = int(risk_score) if isinstance(risk_score, (int, float)) else None
    risk_level = str(risk.get("risk_level") or "UNKNOWN")
    risk_basis = [str(l) for l in (risk.get("items") or [])][:_MAX_RISK_BASIS_LINES]

    gaps_sec = sections.get("data_gaps") if isinstance(sections.get("data_gaps"), dict) else {}
    data_gaps = [str(g) for g in (gaps_sec.get("items") or [])]

    imm = sections.get("immigration_profile")
    imm_present = isinstance(imm, dict) and bool(imm.get("pattern_count"))

    # Corroboration guard (ranking-layer, identity-blind): a scored subject is
    # CORROBORATED when the number is backed by cited evidence targeting can see
    # — at least one fired §09B pattern OR at least one cited §16 basis line. A
    # scored-but-uncorroborated subject (thin_basis) is ranked below cited
    # evidence and never silently auto-watchlisted. Derived ONLY from evidence
    # counts — no identity attribute is read.
    corroborated = bool(patterns) or _has_cited_basis(risk_basis)
    thin_basis = (risk_score is not None) and not corroborated

    pkg = {
        "subject": subject,
        "case_type": str(pa.get("case_type") or "undetermined"),
        "risk_score": risk_score,
        "risk_level": risk_level,
        "risk_basis": risk_basis,
        "patterns": patterns,
        "pattern_count": len(patterns),
        "strong_count": strong_count,
        "immigration_profile_present": imm_present,
        "data_gaps": data_gaps,
        "corroborated": corroborated,
        "thin_basis": thin_basis,
        "human_authorisation_required": True,
        "authorisation_notice": HUMAN_AUTHORISATION_NOTICE,
    }
    pkg["items"] = _render_package_lines(pkg)
    return pkg


def _render_package_lines(pkg: dict) -> list:
    """Officer-facing plain-text lines for a package (UI / PDF)."""
    lines = [f"TARGET PACKAGE — {pkg['subject']} (FOR HUMAN REVIEW — "
             f"authorises no action)"]
    if pkg["risk_score"] is None:
        lines.append("RISK: no risk score available for this case — ranked "
                     "last; review the underlying report before any decision.")
    else:
        lines.append(f"RISK: {pkg['risk_score']}/100 — {pkg['risk_level']} "
                     f"(deterministic §16 score; basis below)")
    lines.append(f"CASE TYPE: {pkg['case_type'].upper()} — "
                 f"{pkg['pattern_count']} pattern(s) fired "
                 f"({pkg['strong_count']} STRONG)")
    if pkg.get("thin_basis"):
        lines.append("⚠ UNCORROBORATED — this score is NOT backed by any cited "
                     "pattern or cited risk-basis line; ranked below cited-evidence "
                     "subjects and NOT auto-watchlisted. Treat as a lead for human "
                     "review, not as established priority.")
    if not pkg["patterns"]:
        lines.append("PATTERNS: none fired — no deterministic behavioural "
                     "indicators; see risk basis and data gaps.")
    for p in pkg["patterns"]:
        lines.append(f"  [{str(p.get('confidence', '?')).upper()}] "
                     f"{str(p.get('pattern_name', p.get('pattern_id', '?'))).upper()}"
                     f" — {p.get('explanation', '')}")
        for t in (p.get("triggers") or []):
            lines.append(f"      evidence: {t}")
        if p.get("sources"):
            lines.append(f"      sources: {', '.join(str(s) for s in p['sources'])}")
    if pkg["immigration_profile_present"]:
        lines.append("IMMIGRATION VIOLATION PROFILE: present in the report "
                     "(§09C) — evidence-based only, see its disclaimer.")
    if pkg["risk_basis"]:
        lines.append("RISK BASIS (§16, per-factor citations):")
        lines.extend(f"  {l}" for l in pkg["risk_basis"])
    if pkg["data_gaps"]:
        lines.append("DATA GAPS (what this package does NOT establish):")
        lines.extend(f"  {g}" for g in pkg["data_gaps"])
    lines.append(pkg["authorisation_notice"])
    return lines


def _rank_key(pkg: dict):
    """Deterministic ranking key — evidence-based inputs ONLY.

    Cited evidence is the currency, not the number. Three tiers, in order:
    0 = corroborated scored (score backed by a cited pattern or cited risk-basis
    line); 1 = thin-basis scored (a bare number with no cited backing); 2 =
    unscored (no §16 score — ranked last, unchanged). So a bare number can never
    outrank cited evidence, and an unscored case still ranks last. Within each
    tier: risk score desc, STRONG pattern count desc, total pattern count desc,
    subject name asc (stable tie-break). No identity attribute is ever an input."""
    score_val = pkg.get("risk_score")
    scored = isinstance(score_val, (int, float))
    score = int(score_val) if scored else -1
    tier = 2 if not scored else (1 if pkg.get("thin_basis") else 0)
    return (tier, -score, -int(pkg.get("strong_count") or 0),
            -int(pkg.get("pattern_count") or 0),
            str(pkg.get("subject", "")).lower())


def prioritize_cases(cases) -> dict:
    """Given a set of analysed cases, return the prioritised list plus one
    target package per subject, each requiring human authorisation.

    Malformed entries are skipped and counted — never guessed at. Ranking uses
    only the deterministic risk score and fired-pattern counts (see _rank_key);
    identity attributes are not inputs and must never become inputs."""
    packages, skipped = [], 0
    for case in (cases or []):
        pkg = build_target_package(case)
        if pkg is None:
            skipped += 1
        else:
            packages.append(pkg)
    packages.sort(key=_rank_key)

    prioritised = []
    for rank, pkg in enumerate(packages, 1):
        if pkg["risk_score"] is None:
            basis = (f"no risk score available — ranked last; "
                     f"{pkg['pattern_count']} pattern(s) fired "
                     f"({pkg['strong_count']} STRONG)")
        elif pkg.get("thin_basis"):
            basis = (f"risk {pkg['risk_score']}/100 ({pkg['risk_level']}) — "
                     f"UNCORROBORATED: no cited patterns and no cited risk basis; "
                     f"ranked below cited-evidence subjects, not auto-watchlisted "
                     f"— needs human review")
        else:
            basis = (f"risk {pkg['risk_score']}/100 ({pkg['risk_level']}); "
                     f"{pkg['pattern_count']} pattern(s) fired "
                     f"({pkg['strong_count']} STRONG); "
                     f"case type {pkg['case_type']}")
        prioritised.append({
            "rank": rank,
            "subject": pkg["subject"],
            "risk_score": pkg["risk_score"],
            "risk_level": pkg["risk_level"],
            "pattern_count": pkg["pattern_count"],
            "strong_count": pkg["strong_count"],
            "case_type": pkg["case_type"],
            "corroborated": bool(pkg.get("corroborated")),
            "thin_basis": bool(pkg.get("thin_basis")),
            "basis": basis,
        })

    return {
        "prioritised": prioritised,
        "packages": packages,
        "package_count": len(packages),
        "skipped": skipped,
        "human_authorisation_required": True,
        "authorisation_notice": HUMAN_AUTHORISATION_NOTICE,
    }


def render_priority_list(result: dict) -> str:
    """Officer-facing plain-text rendering of a prioritize_cases() result."""
    if not isinstance(result, dict):
        return ""
    lines = ["PRIORITISED CASE LIST (DECISION SUPPORT — FOR HUMAN REVIEW ONLY)",
             str(result.get("authorisation_notice") or HUMAN_AUTHORISATION_NOTICE),
             ""]
    entries = result.get("prioritised") or []
    if not entries:
        lines.append("No analysed cases to prioritise.")
    for e in entries:
        flag = " [UNCORROBORATED]" if e.get("thin_basis") else ""
        lines.append(f"{e.get('rank', '?'):>3}. {e.get('subject', '?')}{flag} — "
                     f"{e.get('basis', '')}")
    if result.get("skipped"):
        lines.append(f"({result['skipped']} malformed case(s) skipped — "
                     f"not ranked, not guessed.)")
    return "\n".join(lines)


def render_target_package(pkg: dict) -> str:
    """Officer-facing plain-text rendering of one target package."""
    if not isinstance(pkg, dict):
        return ""
    return "\n".join(pkg.get("items") or _render_package_lines(pkg))


# ══════════════════════════════════════════════════════════════════════════
# PHASE 1.5 STEP 11 — DYNAMIC WATCHLIST (review-gated).
#
# THIS IS A REVIEW AID, NOT SURVEILLANCE AUTOMATION. The watchlist is a pure
# data structure that flags high-priority subjects FOR OFFICER REVIEW. It
# triggers nothing, schedules nothing, monitors nothing, and connects to no
# feed. "Dynamic" means membership is recomputed from the current analysed
# evidence on every build — subjects whose evidence no longer qualifies are
# dropped and the removal is reported to the reviewing officer. Continued
# monitoring of any listed subject requires an independent LEGAL BASIS; the
# structure says so on every list and on every entry.
# ══════════════════════════════════════════════════════════════════════════

# Verbatim on every watchlist and echoed per entry. Do not shorten.
LEGAL_BASIS_NOTICE = (
    "REQUIRES LEGAL BASIS FOR CONTINUED MONITORING: inclusion on this "
    "watchlist is a flag for officer review only. It is NOT an authorisation "
    "to monitor, surveil, or act. Continued monitoring of any listed subject "
    "requires an independent, documented legal basis under applicable law, "
    "assessed by a competent human officer. This watchlist is a review aid, "
    "not surveillance automation — it triggers no automated action."
)

# Same threshold at which §16 labels a score HIGH (see _build_risk_section /
# RiskAgent: >=75 CRITICAL, >=55 HIGH). One shared boundary — not a new one.
WATCHLIST_MIN_SCORE = 55


def build_watchlist(cases, previous: dict | None = None) -> dict:
    """Build the review-gated watchlist from a set of analysed cases.

    Evidence-based membership ONLY: a case is flagged high-priority when its
    deterministic §16 risk score is >= WATCHLIST_MIN_SCORE (the §16 HIGH
    boundary). Unscored cases are NEVER listed automatically — they go to a
    manual-triage list so nothing is silently dropped or silently escalated.
    When `previous` (an earlier build_watchlist result) is given, the officer
    also gets added/removed/retained membership changes.

    The result is plain data for human review: no callbacks, no actions, no
    scheduling. Every entry carries legal_basis_required=True and a
    PENDING OFFICER REVIEW status that only a human can move."""
    ranked = prioritize_cases(cases)

    entries, manual_triage, uncorroborated_review, excluded = [], [], [], 0
    for e in ranked["prioritised"]:
        if e["risk_score"] is None:
            manual_triage.append({
                "subject": e["subject"],
                "note": ("no deterministic risk score available — NOT listed "
                         "automatically; requires manual officer triage"),
                "pattern_count": e["pattern_count"],
                "strong_count": e["strong_count"],
            })
        elif e["risk_score"] >= WATCHLIST_MIN_SCORE and e.get("thin_basis"):
            # High score but UNCORROBORATED — never silently auto-watchlisted.
            # Surfaced for a human to review before any watchlisting (flag, don't
            # assert; surface, don't hide).
            uncorroborated_review.append({
                "subject": e["subject"],
                "risk_score": e["risk_score"],
                "risk_level": e["risk_level"],
                "pattern_count": e["pattern_count"],
                "strong_count": e["strong_count"],
                "case_type": e["case_type"],
                "note": ("high score but UNCORROBORATED — no cited patterns and no "
                         "cited risk basis; NOT auto-watchlisted; requires human "
                         "review before any watchlisting"),
                "review_status": "PENDING OFFICER REVIEW",
                "legal_basis_required": True,
            })
        elif e["risk_score"] >= WATCHLIST_MIN_SCORE:
            entries.append({
                "rank": len(entries) + 1,
                "subject": e["subject"],
                "risk_score": e["risk_score"],
                "risk_level": e["risk_level"],
                "pattern_count": e["pattern_count"],
                "strong_count": e["strong_count"],
                "case_type": e["case_type"],
                "listed_because": (f"deterministic risk {e['risk_score']}/100 "
                                   f"({e['risk_level']}) >= watchlist threshold "
                                   f"{WATCHLIST_MIN_SCORE} (§16 HIGH boundary); "
                                   f"{e['pattern_count']} pattern(s) fired "
                                   f"({e['strong_count']} STRONG)"),
                "review_status": "PENDING OFFICER REVIEW",
                "legal_basis_required": True,
                "legal_basis_note": ("continued monitoring requires an "
                                     "independent documented legal basis — "
                                     "see list-level notice"),
            })
        else:
            excluded += 1

    current = {e["subject"] for e in entries}
    prev = {str(e.get("subject", "")) for e in ((previous or {}).get("watchlist") or [])
            if isinstance(e, dict)}
    changes = {
        "added":    sorted(current - prev),
        "removed":  sorted(prev - current),
        "retained": sorted(current & prev),
    } if previous is not None else None

    return {
        "watchlist": entries,
        "watchlist_count": len(entries),
        "manual_triage": manual_triage,
        "uncorroborated_review": uncorroborated_review,
        "uncorroborated_count": len(uncorroborated_review),
        "excluded_below_threshold": excluded,
        "skipped_malformed": ranked["skipped"],
        "threshold": {
            "min_risk_score": WATCHLIST_MIN_SCORE,
            "explanation": ("same boundary at which §16 labels a score HIGH; "
                            "membership is evidence-based only — no identity "
                            "attributes are inputs"),
        },
        "changes": changes,
        "review_aid_only": True,
        "surveillance_automation": False,
        "automated_action": "NONE — this structure flags for review; it triggers nothing",
        "legal_basis_notice": LEGAL_BASIS_NOTICE,
        "human_authorisation_required": True,
        "authorisation_notice": HUMAN_AUTHORISATION_NOTICE,
    }


def render_watchlist(wl: dict) -> str:
    """Officer-facing plain-text rendering of a build_watchlist() result."""
    if not isinstance(wl, dict):
        return ""
    lines = ["DYNAMIC WATCHLIST — REVIEW AID ONLY. NOT SURVEILLANCE AUTOMATION.",
             str(wl.get("legal_basis_notice") or LEGAL_BASIS_NOTICE),
             ""]
    entries = wl.get("watchlist") or []
    if not entries:
        lines.append("No subjects meet the watchlist threshold.")
    for e in entries:
        lines.append(f"{e.get('rank', '?'):>3}. {e.get('subject', '?')} — "
                     f"{e.get('listed_because', '')} — "
                     f"[{e.get('review_status', 'PENDING OFFICER REVIEW')}]")
    for u in (wl.get("uncorroborated_review") or []):
        lines.append(f"  ⚠ UNCORROBORATED (high score, NOT watchlisted): "
                     f"{u.get('subject', '?')} — risk {u.get('risk_score', '?')}/100 "
                     f"({u.get('risk_level', '?')}) — {u.get('note', '')}")
    for t in (wl.get("manual_triage") or []):
        lines.append(f"  ⚑ MANUAL TRIAGE: {t.get('subject', '?')} — {t.get('note', '')}")
    if wl.get("excluded_below_threshold"):
        lines.append(f"({wl['excluded_below_threshold']} analysed case(s) below "
                     f"threshold — not listed.)")
    if wl.get("skipped_malformed"):
        lines.append(f"({wl['skipped_malformed']} malformed case(s) skipped — "
                     f"not listed, not guessed.)")
    ch = wl.get("changes")
    if isinstance(ch, dict):
        lines.append("MEMBERSHIP CHANGES since previous review:")
        lines.append(f"  added: {', '.join(ch.get('added') or []) or 'none'}")
        lines.append(f"  removed (evidence no longer qualifies): "
                     f"{', '.join(ch.get('removed') or []) or 'none'}")
        lines.append(f"  retained: {', '.join(ch.get('retained') or []) or 'none'}")
    lines.append(str(wl.get("authorisation_notice") or HUMAN_AUTHORISATION_NOTICE))
    return "\n".join(lines)
