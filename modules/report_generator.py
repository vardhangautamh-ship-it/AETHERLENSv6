"""
AetherLens — Report Generator Module
Gemini-powered 12-section intelligence report with PDF export via ReportLab.

PDF architecture: SimpleDocTemplate.build(onFirstPage=, onLaterPages=)
  -> background callback fires BEFORE Platypus places content on each page
  -> content is drawn ON TOP of the black background (correct order)
  -> old _AetherCanvas approach painted background OVER content (wrong order)
"""

import io
import json
import re
import datetime
import sqlite3
from pathlib import Path

import requests
from modules.sanitizer import defensive
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer,
    Table, TableStyle, PageBreak, HRFlowable,
    KeepTogether,
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.lib.colors import Color

import config

# ══════════════════════════════════════════════════════════════════════════════
# SECTION B — RULE 5: UNSOURCED SENTENCE FILTER
# ══════════════════════════════════════════════════════════════════════════════

_SOURCE_INDICATORS = [
    "source:", "— source:", "via ", "[verified data]", "[ai analysis]",
    "confirmed via", "based on", "according to", "extracted from",
    "public records", "electoral", "whois", "linkedin", "twitter", "github",
    "facebook", "telegram", "api", "osint", "document:", "file:", "per ",
    "cited in", "ingested from", "data sources:", "reported by", "ref:",
]

_UNSOURCED_SKIP_PREFIXES = ("step ", "phase ", "legal basis", "authorization",
                             "priority", "abort", "resource", "estimated")


def filter_unsourced_sentences(report_text: str, strict: bool = False) -> str:
    """
    Flag lines in AI-generated report text that contain factual claims
    but no source attribution.
    Appends ' [UNVERIFIED — no source cited]' to qualifying unsourced lines.
    """
    if not report_text:
        return report_text
    lines  = report_text.split("\n")
    result = []
    for line in lines:
        stripped = line.strip()
        # Skip empty lines, headers, bullet points; skip short lines only in non-strict mode
        if not stripped or stripped.startswith(("[", "#", "•", "⚑", "-", "*", "{", "}")):
            result.append(line)
            continue
        if not strict and len(stripped) < 25:
            result.append(line)
            continue
        lower = stripped.lower()
        # Skip instructional / procedural lines
        if any(lower.startswith(p) for p in _UNSOURCED_SKIP_PREFIXES):
            result.append(line)
            continue
        # Skip lines that already acknowledge absence of data
        if any(k in lower for k in ("not found", "not available", "none", "unknown",
                                     "no ", "pending", "not determined")):
            result.append(line)
            continue
        has_source = any(ind in lower for ind in _SOURCE_INDICATORS)
        if has_source:
            result.append(line)
        elif strict:
            result.append(line + " [UNVERIFIED — no source cited]")
        else:
            # Non-strict: only flag lines that read as specific factual claims
            looks_factual = (
                any(c.isupper() for c in stripped[:60])   # contains a proper noun/acronym
                and not lower.startswith(("if ", "when ", "ensure", "verify", "check",
                                          "confirm", "review", "conduct", "run ", "use "))
            )
            result.append(line + " [UNVERIFIED — no source cited]" if looks_factual else line)
    return "\n".join(result)


# ── Colours ────────────────────────────────────────────────────────────────────

BLACK       = colors.HexColor('#000000')
PURPLE      = colors.HexColor('#7B2FBE')
DUST_PURPLE = colors.HexColor('#9D4EDD')
OFF_WHITE   = colors.HexColor('#F0EAD6')
DARK        = colors.HexColor('#0a0a0a')
DARKER      = colors.HexColor('#111111')
DIM         = colors.HexColor('#555555')
RED         = colors.HexColor('#FF4B4B')
GREEN       = colors.HexColor('#4BFF91')
C_WATERMARK = Color(0.482, 0.184, 0.745, alpha=0.08)


# ── Paragraph styles ───────────────────────────────────────────────────────────

_STYLES = {
    "title": ParagraphStyle(
        name="ATitle",
        fontName="Helvetica-BoldOblique",
        fontSize=22,
        textColor=PURPLE,
        spaceAfter=6 * mm,
        alignment=TA_CENTER,
        leading=26,
    ),
    "subtitle": ParagraphStyle(
        name="ASubtitle",
        fontName="Helvetica-Oblique",
        fontSize=11,
        textColor=DUST_PURPLE,
        spaceAfter=4 * mm,
        alignment=TA_CENTER,
        leading=14,
    ),
    "section_header": ParagraphStyle(
        name="ASectionHeader",
        fontName="Helvetica-BoldOblique",
        fontSize=13,
        textColor=PURPLE,
        spaceBefore=6 * mm,
        spaceAfter=3 * mm,
        leading=16,
    ),
    "body": ParagraphStyle(
        name="ABody",
        fontName="Helvetica-Oblique",
        fontSize=9,
        textColor=OFF_WHITE,
        spaceAfter=2 * mm,
        leading=14,
    ),
    "verified": ParagraphStyle(
        name="AVerified",
        fontName="Helvetica-Oblique",
        fontSize=9,
        textColor=OFF_WHITE,
        spaceAfter=2 * mm,
        leading=14,
        leftIndent=5 * mm,
    ),
    "ai_analysis": ParagraphStyle(
        name="AAIAnalysis",
        fontName="Helvetica-Oblique",
        fontSize=9,
        textColor=DUST_PURPLE,
        spaceAfter=2 * mm,
        leading=14,
        leftIndent=5 * mm,
    ),
    "label": ParagraphStyle(
        name="ALabel",
        fontName="Helvetica-BoldOblique",
        fontSize=8,
        textColor=DUST_PURPLE,
        spaceAfter=1 * mm,
        leading=10,
    ),
    "meta": ParagraphStyle(
        name="AMeta",
        fontName="Helvetica-Oblique",
        fontSize=8,
        textColor=DUST_PURPLE,
        spaceAfter=1 * mm,
        alignment=TA_CENTER,
        leading=10,
    ),
    "gap": ParagraphStyle(
        name="AGap",
        fontName="Helvetica-Oblique",
        fontSize=8,
        textColor=DIM,
        spaceAfter=1 * mm,
        leading=10,
        leftIndent=5 * mm,
    ),
    "flag": ParagraphStyle(
        name="AFlag",
        fontName="Helvetica-BoldOblique",
        fontSize=8,
        textColor=RED,
        spaceAfter=2 * mm,
        leading=10,
        leftIndent=5 * mm,
    ),
    "url": ParagraphStyle(
        name="AURL",
        fontName="Helvetica-Oblique",
        fontSize=7,
        textColor=DIM,
        spaceAfter=1 * mm,
        leading=9,
        leftIndent=5 * mm,
    ),
}

W, H = A4


# ══════════════════════════════════════════════════════════════════════════════
# PAGE BACKGROUND — drawn BEFORE Platypus content on every page
# ══════════════════════════════════════════════════════════════════════════════

def _add_page_background(canv, doc):
    """
    Called by SimpleDocTemplate before any content is placed on each page.
    Draws: full black background -> watermark -> header bar -> footer bar.
    Platypus flowables are drawn on top after this function returns.
    """
    canv.saveState()

    # 1. Full black background
    canv.setFillColor(BLACK)
    canv.rect(0, 0, W, H, fill=1, stroke=0)

    # 2. RESTRICTED diagonal watermark (semi-transparent purple)
    canv.saveState()
    canv.setFillColor(C_WATERMARK)
    canv.setFont("Helvetica-Bold", 60)
    canv.translate(W / 2, H / 2)
    canv.rotate(45)
    canv.drawCentredString(0, 0, "RESTRICTED")
    canv.restoreState()

    # 3. Top header bar (solid purple strip)
    canv.setFillColor(PURPLE)
    canv.rect(0, H - 15 * mm, W, 15 * mm, fill=1, stroke=0)
    canv.setFillColor(OFF_WHITE)
    canv.setFont("Helvetica-BoldOblique", 10)
    canv.drawString(10 * mm, H - 10 * mm, "AETHERLENS")
    canv.drawRightString(W - 10 * mm, H - 10 * mm, "RESTRICTED")

    # 4. Bottom footer bar (dark strip with page number)
    canv.setFillColor(DARK)
    canv.rect(0, 0, W, 12 * mm, fill=1, stroke=0)
    canv.setFillColor(DUST_PURPLE)
    canv.setFont("Helvetica-Oblique", 8)
    ts = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    canv.drawString(10 * mm, 4 * mm, f"Generated: {ts}")
    canv.drawRightString(W - 10 * mm, 4 * mm, f"Page {doc.page}")

    canv.restoreState()


# ══════════════════════════════════════════════════════════════════════════════
# TEXT SAFETY HELPER
# ══════════════════════════════════════════════════════════════════════════════

def _safe(text) -> str:
    """Escape HTML special chars so ReportLab Paragraph doesn't choke."""
    s = str(text) if text else "Not found."
    return (s
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _hr(color=DUST_PURPLE, thickness=0.5):
    return HRFlowable(width="100%", thickness=thickness, color=color,
                      spaceAfter=2 * mm, spaceBefore=1 * mm)


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 18 — TACTICAL OPERATION PLAN RENDERER
# ══════════════════════════════════════════════════════════════════════════════

_PRIORITY_ICON = {
    "CRITICAL": "🔴",
    "HIGH":     "🟠",
    "MEDIUM":   "🟡",
    "LOW":      "🟢",
}

_PRIORITY_COLOR = {
    "CRITICAL": colors.HexColor('#FF4B4B'),
    "HIGH":     colors.HexColor('#FF8C00'),
    "MEDIUM":   DUST_PURPLE,
    "LOW":      DIM,
}


def render_tactical_plan(tactical_plan: dict) -> list:
    """
    Render Section 18 — Tactical Operation Plan.
    Self-contained: includes section header, case summary, warning, and all 6 actions.
    Called from generate_pdf() when report_data["tactical_plan"] has actions.
    """
    if not tactical_plan or not tactical_plan.get("actions"):
        return [
            Paragraph("18. TACTICAL OPERATION PLAN", _STYLES["section_header"]),
            _hr(PURPLE, 1.0),
            Paragraph("No Tactical Operation Plan available.", _STYLES["verified"]),
            Spacer(1, 4 * mm),
        ]

    block = []

    # ── Section header ────────────────────────────────────────────────────────
    block.append(Paragraph("18. TACTICAL OPERATION PLAN", _STYLES["section_header"]))
    block.append(_hr(PURPLE, 1.0))

    # Method badge
    method = tactical_plan.get("method", "")
    badge  = "[AI ANALYSIS]" if "ai" in method.lower() else "[RULE-BASED]"
    block.append(Paragraph(f"{badge} TacticalPlanAgent", _STYLES["label"]))

    # Case summary
    case_summary = tactical_plan.get("case_summary", "")
    if case_summary:
        block.append(Paragraph(
            _safe(f"CASE ASSESSMENT: {case_summary}"), _STYLES["body"]
        ))

    # Critical sequencing warning
    critical_warning = tactical_plan.get("critical_warning", "")
    if critical_warning:
        block.append(Paragraph(
            _safe(f"⚠ CRITICAL SEQUENCING WARNING: {critical_warning}"),
            _STYLES["flag"],
        ))

    block.append(Spacer(1, 3 * mm))

    # ── Actions ───────────────────────────────────────────────────────────────
    for act in tactical_plan.get("actions", []):
        if not isinstance(act, dict):
            continue

        act_id       = act.get("id", "?")
        act_title    = act.get("title", "")
        act_priority = str(act.get("priority", "MEDIUM")).upper()
        act_ts       = str(act.get("time_sensitivity", "MEDIUM")).upper()
        act_tw       = act.get("time_window", "")
        act_desc     = act.get("description", "")
        act_legal    = act.get("legal_basis", "")
        act_agency   = act.get("agency", "")
        act_auth     = act.get("authority_required", "")
        act_dep      = act.get("depends_on", [])
        act_blk      = act.get("blocks", [])
        act_par      = act.get("parallel_with", [])
        act_rdel     = act.get("risk_if_delayed", "")
        act_rrev     = act.get("risk_if_reversed", "")
        act_reward   = act.get("reward", "")

        icon    = _PRIORITY_ICON.get(act_priority, "•")
        p_col   = _PRIORITY_COLOR.get(act_priority, DUST_PURPLE)

        # Action title line: 🔴 ACTION 1 [CRITICAL] — TITLE | 0-24 hours
        title_txt = f"{icon} ACTION {act_id} [{act_priority}] — {act_title.upper()} | {act_tw}"
        title_style = _STYLES["flag"] if act_priority == "CRITICAL" else _STYLES["section_header"]
        block.append(Paragraph(_safe(title_txt), title_style))

        # Compact metadata table: PRIORITY | TIME SENSITIVITY | TIME WINDOW
        hdr_data = [[
            f"PRIORITY: {act_priority}",
            f"SENSITIVITY: {act_ts}",
            f"⏱ {act_tw}",
        ]]
        hdr_tbl = Table(hdr_data, colWidths=[55 * mm, 60 * mm, 45 * mm])
        hdr_tbl.setStyle(TableStyle([
            ("BACKGROUND",    (0, 0), (-1, -1), DARKER),
            ("TEXTCOLOR",     (0, 0), (0,  0),  p_col),
            ("TEXTCOLOR",     (1, 0), (1,  0),  p_col),
            ("TEXTCOLOR",     (2, 0), (2,  0),  OFF_WHITE),
            ("FONTNAME",      (0, 0), (-1, -1), "Helvetica-BoldOblique"),
            ("FONTSIZE",      (0, 0), (-1, -1), 8),
            ("GRID",          (0, 0), (-1, -1), 0.3, PURPLE),
            ("TOPPADDING",    (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LEFTPADDING",   (0, 0), (-1, -1), 5),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 5),
        ]))
        block.append(hdr_tbl)
        block.append(Spacer(1, 1 * mm))

        # What
        if act_desc:
            block.append(Paragraph(_safe(f"  What: {act_desc}"), _STYLES["verified"]))

        # Legal basis
        if act_legal:
            block.append(Paragraph(
                _safe(f"  ⚖ Legal Basis: {act_legal}"), _STYLES["label"]
            ))

        # Authority + Agency
        if act_agency or act_auth:
            block.append(Paragraph(
                _safe(f"  Authority: {act_auth}  |  Agency: {act_agency}"),
                _STYLES["verified"],
            ))

        # Dependencies / blocks / parallel
        seq_parts = []
        if act_dep:
            seq_parts.append(f"Depends on: Action(s) {', '.join(str(i) for i in act_dep)}")
        else:
            seq_parts.append("Depends on: —")
        if act_blk:
            seq_parts.append(f"Blocks: Action(s) {', '.join(str(i) for i in act_blk)}")
        if act_par:
            seq_parts.append(f"Parallel with: Action(s) {', '.join(str(i) for i in act_par)}")
        block.append(Paragraph(_safe("  → " + "  |  ".join(seq_parts)), _STYLES["gap"]))

        # ⚠ DO NOT execute before depends_on complete
        if act_dep:
            block.append(Paragraph(
                _safe(f"  ⚠ DO NOT execute before Action(s) {', '.join(str(i) for i in act_dep)} are complete."),
                _STYLES["flag"],
            ))

        # Risk lines
        if act_rdel or act_rrev:
            risk_txt = f"  Risk if delayed: {act_rdel}  |  Risk if reversed: {act_rrev}"
            risk_style = (
                _STYLES["flag"]
                if act_rdel in ("HIGH", "CRITICAL") or act_rrev in ("HIGH", "CRITICAL")
                else _STYLES["gap"]
            )
            block.append(Paragraph(_safe(risk_txt), risk_style))

        # Evidence secured / reward
        if act_reward:
            block.append(Paragraph(
                _safe(f"  ★ Evidence secured: {act_reward}"), _STYLES["ai_analysis"]
            ))

        block.append(Spacer(1, 3 * mm))
        block.append(_hr(DIM, 0.3))

    block.append(Spacer(1, 4 * mm))
    return block


# ══════════════════════════════════════════════════════════════════════════════
# PDF BUILDER
# ══════════════════════════════════════════════════════════════════════════════

def generate_pdf(
    report_data:  dict,
    username:     str,
    user_id:      str,
    mode:         str,
    gemini_used:  bool = False,
    bedrock_used: bool = False,
) -> bytes:
    """
    Build a full intelligence PDF from a report_data dict.

    report_data keys:
        subject_identity, confidence_score, platform_presence, location_data,
        network_summary, timeline, behavioral_patterns, associations,
        anomalies, data_gaps, source_log, ai_notes

    Values may be str, list, or dict — all handled.
    Returns PDF as bytes.
    """
    buffer = io.BytesIO()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=20 * mm,      # clears 15mm header bar + 5mm gap
        bottomMargin=20 * mm,   # clears 12mm footer bar + 8mm gap
        title="AetherLens Intelligence Report",
        author="AetherLens",
    )

    story = []

    # ── COVER / METADATA PAGE ─────────────────────────────────────────────────
    story.append(Spacer(1, 10 * mm))
    story.append(Paragraph("A E T H E R L E N S", _STYLES["title"]))
    story.append(Paragraph("INTELLIGENCE REPORT", _STYLES["title"]))
    story.append(_hr(PURPLE, 1.0))
    story.append(Spacer(1, 5 * mm))

    engines = []
    if bedrock_used:
        engines.append("Claude Sonnet 4 · Bedrock Mumbai")
    if gemini_used:
        engines.append("Gemini 2.5 Flash")
    if not engines:
        engines.append("Local Rules (AI unavailable)")

    meta_rows = [
        ["CLASSIFICATION", "RESTRICTED — AUTHORIZED USE ONLY"],
        ["SUBJECT",        _safe(username)],
        ["GENERATED",      datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")],
        ["AUTHORIZED USER", _safe(user_id)],
        ["MODE",           _safe(mode)],
        ["AI ENGINES",     " + ".join(engines)],
        ["OVERALL CONF.",  _safe(report_data.get("confidence_score", "—"))],
    ]

    meta_table = Table(meta_rows, colWidths=[50 * mm, 110 * mm])
    meta_table.setStyle(TableStyle([
        ("BACKGROUND",      (0, 0), (-1, -1), DARK),
        ("TEXTCOLOR",       (0, 0), (0, -1),  PURPLE),
        ("TEXTCOLOR",       (1, 0), (1, -1),  OFF_WHITE),
        ("FONTNAME",        (0, 0), (-1, -1), "Helvetica-Oblique"),
        ("FONTSIZE",        (0, 0), (-1, -1), 9),
        ("ROWBACKGROUNDS",  (0, 0), (-1, -1), [DARK, DARKER]),
        ("GRID",            (0, 0), (-1, -1), 0.5, PURPLE),
        ("TOPPADDING",      (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING",   (0, 0), (-1, -1), 4),
        ("LEFTPADDING",     (0, 0), (-1, -1), 6),
        ("RIGHTPADDING",    (0, 0), (-1, -1), 6),
    ]))
    story.append(meta_table)
    story.append(Spacer(1, 5 * mm))
    story.append(Paragraph(
        "This report contains intelligence derived exclusively from publicly available "
        "sources. All AI-generated analysis is clearly labeled. Verified data is sourced. "
        "This document is RESTRICTED — authorized personnel only.",
        _STYLES["meta"],
    ))
    story.append(Spacer(1, 8 * mm))
    story.append(PageBreak())

    # ── REPORT SECTIONS ───────────────────────────────────────────────────────
    AI_SECTIONS = {"behavioral_patterns", "ai_notes", "anomalies"}

    section_defs = [
        ("01. SUBJECT IDENTITY",           "subject_identity"),
        ("02. CONFIDENCE SCORE",           "confidence_score"),
        ("03. PLATFORM PRESENCE",          "platform_presence"),
        ("04. PUBLIC LOCATION DATA",       "location_data"),
        ("05. NETWORK MAP SUMMARY",        "network_summary"),
        ("06. TIMELINE OF ACTIVITY",              "timeline"),
        ("06B. TIMELINE INTELLIGENCE ANALYSIS",   "timeline_intelligence"),
        ("07. BEHAVIORAL PATTERNS",               "behavioral_patterns"),
        ("08. KEY ASSOCIATIONS",           "associations"),
        ("09. ANOMALIES + FLAGS",          "anomalies"),
        ("10. DATA GAPS",                  "data_gaps"),
        ("11. SOURCE LOG",                 "source_log"),
        ("12. AI ENGINE NOTES",            "ai_notes"),
        ("13. LINKED PROFILES",            "linked_profiles"),
        ("14. EXTRACTED INTELLIGENCE",     "extracted_intelligence"),
        ("15. ACCOUNT CREATION TIMELINE",  "account_timeline"),
        ("16. RISK ASSESSMENT",            "risk_assessment"),
        ("17. INVESTIGATIVE NEXT STEPS",   "next_steps"),
    ]
    # Section 18 — Tactical Operation Plan (present whenever tactical_plan with actions exists)
    _tp = report_data.get("tactical_plan")
    if isinstance(_tp, dict) and _tp.get("actions"):
        section_defs.append(("18. TACTICAL OPERATION PLAN", "tactical_plan"))

    for title, key in section_defs:
        # ── Section 18 — Tactical Operation Plan: standalone renderer ──────────
        if key == "tactical_plan":
            content = report_data.get(key)
            story.append(KeepTogether(render_tactical_plan(content)))
            continue

        block = []
        block.append(Paragraph(_safe(title), _STYLES["section_header"]))
        block.append(_hr())

        is_ai = key in AI_SECTIONS
        if is_ai:
            block.append(Paragraph(
                "[ AI ANALYSIS — not verified fact ]",
                _STYLES["label"],
            ))

        content = report_data.get(key)

        if content is None or content == "" or content == []:
            block.append(Paragraph("Not found.", _STYLES["verified"]))

        elif isinstance(content, list):
            style = _STYLES["ai_analysis"] if is_ai else _STYLES["verified"]
            for item in content[:40]:
                block.append(Paragraph(_safe(f"• {item}"), style))

        elif isinstance(content, dict):
            style = _STYLES["ai_analysis"] if is_ai else _STYLES["verified"]
            for k, v in list(content.items())[:30]:
                block.append(Paragraph(_safe(str(k).upper()), _STYLES["label"]))
                block.append(Paragraph(
                    _safe(str(v)) if v else "Not found.",
                    style,
                ))

        else:
            style = _STYLES["ai_analysis"] if is_ai else _STYLES["verified"]
            block.append(Paragraph(_safe(str(content)), style))
            # §02 — append confidence explanation on the line below the score
            if key == "confidence_score":
                expl = report_data.get("confidence_explanation", "")
                if expl:
                    block.append(Paragraph(_safe(expl), _STYLES["verified"]))

        block.append(Spacer(1, 4 * mm))
        story.append(KeepTogether(block))

    # ── END OF REPORT ─────────────────────────────────────────────────────────
    story.append(Spacer(1, 6 * mm))
    story.append(_hr(PURPLE, 1.0))
    story.append(Paragraph(
        "END OF REPORT — AETHERLENS RESTRICTED INTELLIGENCE OUTPUT",
        _STYLES["meta"],
    ))

    # Build — background callback fires BEFORE each page's content is placed
    doc.build(
        story,
        onFirstPage=_add_page_background,
        onLaterPages=_add_page_background,
    )

    buffer.seek(0)
    return buffer.getvalue()


# ══════════════════════════════════════════════════════════════════════════════
# SAVE PDF TO DISK
# ══════════════════════════════════════════════════════════════════════════════

def save_pdf_to_exports(pdf_bytes: bytes, username: str, user_id: str) -> str:
    """Save PDF bytes to exports/ folder. Returns filepath string."""
    exports_dir = config.EXPORTS_DIR
    exports_dir.mkdir(parents=True, exist_ok=True)
    ts        = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    safe_name = re.sub(r"[^\w\-]", "_", str(username))[:40]
    filename  = f"AETHERLENS_{safe_name}_{ts}.pdf"
    filepath  = exports_dir / filename
    with open(filepath, "wb") as f:
        f.write(pdf_bytes)
    return str(filepath)


# ══════════════════════════════════════════════════════════════════════════════
# SECTION DATA — Gemini API call
# ══════════════════════════════════════════════════════════════════════════════

REPORT_PROMPT = """You are AETHERLENS, a professional restricted intelligence reporting engine.

Generate a structured intelligence report from the provided data ONLY.

ABSOLUTE RULES:
1. Zero hallucinations. Only state what is directly present in the input data.
2. Every factual claim must cite its exact source (e.g., "Source: GitHub API").
3. Write "Not found" for every missing field — never omit sections.
4. Label all AI-generated analysis with "[AI ANALYSIS]" — separate from "[VERIFIED DATA]".
5. Confidence scores 0-100 per section based on evidence quality.
6. Return ONLY valid JSON matching the exact schema below. No markdown, no code fences.

JSON SCHEMA (fill every field):
{
  "subject_identity": {
    "content": "Full identity summary with source citations",
    "confidence": 0,
    "verified_items": ["item — Source: X"]
  },
  "platform_presence": {
    "content": "Platform summary",
    "confidence": 0,
    "platforms": {"platform_name": "url_or_Not found"}
  },
  "public_location_data": {
    "content": "Location summary — explicitly stated data only",
    "confidence": 0,
    "locations": ["location — Source: X"]
  },
  "network_map_summary": {
    "content": "Connection summary",
    "confidence": 0,
    "connections": ["connection description"]
  },
  "timeline_of_activity": {
    "content": "Timeline narrative",
    "confidence": 0,
    "events": ["YYYY-MM-DD: event — Source: X"]
  },
  "behavioral_patterns": {
    "content": "[AI ANALYSIS] Behavioral assessment",
    "confidence": 0,
    "flags": ["flag — Source: X"]
  },
  "key_associations": {
    "content": "Confirmed associations only",
    "confidence": 0,
    "associations": ["association — Source: X"]
  },
  "anomalies_and_flags": {
    "content": "Inconsistencies and anomalies found",
    "flags": ["flag description"]
  },
  "data_gaps": {
    "items": ["field: reason not found"]
  },
  "source_log": {
    "urls": ["url — platform"]
  },
  "ai_engine_notes": {
    "content": "[AI ANALYSIS] Engine observations and caveats",
    "model": "gemini-2.5-flash"
  },
  "overall_confidence": 0
}

SUBJECT DATA PAYLOAD:
{payload}

Return the JSON report now:"""


def _call_gemini_report(payload_str: str) -> dict | None:
    """
    Call Gemini for report section generation.
    Retries up to 3 times on 503/429/transient errors with short backoff.
    Returns parsed dict on success, None on all-retry failure.
    """
    import time as _time
    api_key = config.GEMINI_API_KEY
    if not api_key or api_key == "your_gemini_key_here":
        return None
    url  = f"{config.GEMINI_ENDPOINT}?key={api_key}"
    body = {
        "contents": [{"parts": [{"text": REPORT_PROMPT.replace("{payload}", payload_str)}]}],
        "generationConfig": {"temperature": 0.05, "maxOutputTokens": 8192, "topP": 0.95},
        "safetySettings": [
            {"category": "HARM_CATEGORY_HARASSMENT",        "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH",       "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
        ],
    }
    _RETRYABLE = {429, 500, 502, 503, 504}
    for attempt in range(3):
        try:
            resp = requests.post(url, json=body, timeout=60)
            if resp.status_code in _RETRYABLE:
                wait = (attempt + 1) * 5  # 5s, 10s, 15s
                print(f"[GEMINI REPORT] HTTP {resp.status_code} on attempt {attempt+1}/3 — retrying in {wait}s")
                _time.sleep(wait)
                continue
            resp.raise_for_status()
            data = resp.json()
            candidates = data.get("candidates", [])
            if not candidates:
                print(f"[GEMINI REPORT] No candidates in response (attempt {attempt+1})")
                return None
            content = candidates[0].get("content", {})
            parts = content.get("parts", [])
            if not parts:
                print(f"[GEMINI REPORT] Empty parts in response (attempt {attempt+1})")
                return None
            text = parts[0].get("text", "")
            if not text:
                print(f"[GEMINI REPORT] Empty text in response (attempt {attempt+1})")
                return None
            text = re.sub(r"```(?:json)?", "", text).strip()
            try:
                return json.loads(text)
            except Exception:
                m = re.search(r"\{.*\}", text, re.DOTALL)
                if m:
                    try:
                        return json.loads(m.group())
                    except Exception:
                        pass
                print(f"[GEMINI REPORT] JSON parse failed on attempt {attempt+1} — text[:200]: {text[:200]}")
                return None  # parse failure is not retryable
        except Exception as exc:
            print(f"[GEMINI REPORT] Exception on attempt {attempt+1}/3: {exc}")
            if attempt < 2:
                _time.sleep((attempt + 1) * 5)
    print("[GEMINI REPORT] All 3 attempts failed — using local fallback")
    return None


# ══════════════════════════════════════════════════════════════════════════════
# LOCAL FALLBACK SECTION BUILDER
# ══════════════════════════════════════════════════════════════════════════════

def build_platform_presence(person: dict) -> dict:
    """
    Build platform presence from ALL sources with case-insensitive dedup.
    Sources: platforms_confirmed → usernames dict → confirmed_linked_profiles.
    Normalises platform keys to title-case so "github"/"GitHub"/"GITHUB"
    all collapse to a single "Github" entry (no Section 03 duplicates).
    """
    # _seen maps normalised-lowercase key → canonical display name
    _seen: dict = {}
    platforms: dict = {}

    def _add(raw_name: str, entry: dict):
        key = raw_name.strip().lower()
        if key and key not in _seen:
            canonical = raw_name.strip().title()
            _seen[key] = canonical
            platforms[canonical] = entry

    # From confirmed platforms list
    for p in person.get("platforms_confirmed", []):
        _add(p, {
            "status":   "CONFIRMED",
            "url":      person.get("profile_urls", {}).get(p, "Not found"),
            "username": person.get("usernames", {}).get(p, "Not found"),
        })

    # From usernames dict (catches cases like Telegram not in platforms_confirmed)
    for platform, username in person.get("usernames", {}).items():
        _add(platform, {
            "status":   "CONFIRMED",
            "url":      person.get("profile_urls", {}).get(platform, "Not public"),
            "username": str(username),
        })

    # From confirmed linked profiles
    for profile in person.get("confirmed_linked_profiles", []):
        p = profile.get("platform", "")
        if p:
            _add(p, {
                "status":   "CONFIRMED",
                "url":      profile.get("url", ""),
                "username": profile.get("username", ""),
            })

    return platforms


def build_source_log(raw_documents: list, search_results: dict = None) -> list:
    """
    Fix 1D: Build source log from all uploaded documents and search sources.
    raw_documents: list of ingest_file() result dicts.
    """
    log = []
    now_str = datetime.datetime.utcnow().isoformat()

    for doc in (raw_documents or []):
        fname    = doc.get("filename", doc.get("source", "unknown"))
        ftype    = doc.get("file_type", "unknown")
        fsize    = doc.get("file_size", "")
        entities = doc.get("entities", {})
        log.append(
            f"FILE: {fname} | TYPE: {ftype}"
            + (f" | SIZE: {fsize}" if fsize else "")
            + f" | Ingested: {doc.get('ingested_at', now_str)[:19]}"
            + f" | Entities: {doc.get('total_items', len(entities))}"
            + f" | Names: {len(entities.get('names', []))}"
            + f" | Phones: {len(entities.get('phones', []))}"
            + f" | Dates: {len(entities.get('dates', []))}"
            + f" | Data retrieved: entities, relationships, timeline, phone numbers, locations"
        )

    # Also include search result sources
    for r in (search_results or {}).get("results", []):
        url = r.get("url", "")
        plat = r.get("platform", "")
        if url:
            log.append(f"URL: {url} | PLATFORM: {plat} | search result")

    return log or ["No sources logged."]


def _build_risk_section(person: dict, agent_results: dict = None, raw_documents: list = None) -> dict:
    """
    Build section 16 — Risk Assessment.
    Always builds the full enriched anomaly list from person dict + raw document
    keyword scan, then runs RiskAgent with it — guarantees flags are never empty
    regardless of when anomaly_flags was populated upstream.
    NEVER returns empty — always has risk_score and risk_level.
    """
    # ── Always build complete flag list from all sources ──────────────────────
    try:
        from modules.ai_agents import run_risk_agent

        # 1. Structured flags from person dict
        _anomalies = []
        for _f in (person or {}).get("anomaly_flags", []) or []:
            _anomalies.append(_f.get("flag", str(_f)) if isinstance(_f, dict) else str(_f))
        for _c in (person or {}).get("conflicts", []) or []:
            _anomalies.append(_c.get("flag", str(_c)) if isinstance(_c, dict) else str(_c))
        for _b in (person or {}).get("behavioral_flags", []) or []:
            _anomalies.append(str(_b))

        # 2. Keyword scan across raw document text — catches flags not yet in
        #    anomaly_flags (CERT-In, IT Act, DPDP, evidence deletion, FEMA, etc.)
        _seen: set = set(_anomalies)
        _KEYWORD_FLAGS = [
            (("CERT-IN", "CERT-In", "CERTIN", "COMPUTER EMERGENCY RESPONSE"),
             "CERT-In inquiry confirmed"),
            (("IT ACT", "INFORMATION TECHNOLOGY ACT", "SECTION 43", "SECTION 66", "SECTION 69"),
             "IT Act violation flagged"),
            (("DPDP", "DATA PROTECTION"),
             "DPDP Act breach suspected"),
            (("DELETED", "DELETION", "REPO_DELETE", "POST_DELETE", "MODEL_DELETE"),
             "Evidence deletion confirmed"),
            (("FEMA", "INTERNATIONAL_DEBIT", "USD", "FOREIGN EXCHANGE"),
             "International financial transfer — FEMA 1999 may apply"),
            (("UNAUTHORISED", "UNAUTHORIZED"),
             "Unauthorised access flagged"),
            (("SCRAPING", "SCRAPED", "DATA SCRAPE"),
             "Unauthorised data scraping flagged"),
            (("DEPLOYED", "DEPLOYMENT", "MALWARE", "EXPLOIT", "MALICIOUS"),
             "Malicious deployment / exploit activity flagged"),
        ]
        for doc in (raw_documents or []):
            doc_text = str(
                doc.get("full_text", "") or doc.get("raw_text", "") or doc.get("content", "")
            ).upper()
            for keywords, flag_label in _KEYWORD_FLAGS:
                if flag_label not in _seen and any(kw in doc_text for kw in keywords):
                    _anomalies.append(flag_label)
                    _seen.add(flag_label)

        # 3. Run RiskAgent with enriched flag list — always fresh, never stale
        risk_data = run_risk_agent(person, _anomalies)
    except Exception:
        risk_data = None

    if risk_data and risk_data.get("risk_score") is not None:
        score  = risk_data.get("risk_score", 0)
        level  = risk_data.get("risk_level", "LOW")
        factors = risk_data.get("risk_factors", [])
        conf   = risk_data.get("confidence", 0)
        notes  = risk_data.get("mitigation_notes", "")
        summary = risk_data.get("summary", "")

        lines = [
            f"RISK SCORE: {score}/100 — {level}",
            f"CONFIDENCE: {conf}/100",
        ]
        if summary:
            lines.append(f"SUMMARY: {summary}")
        if notes:
            lines.append(f"MITIGATION: {notes}")
        if factors:
            lines.append(f"RISK FACTORS ({len(factors)}):")
            for f in factors[:10]:
                if isinstance(f, dict):
                    lines.append(
                        f"  [{f.get('factor','?')}] "
                        f"Weight: {f.get('weight',0)} — "
                        f"Evidence: {str(f.get('evidence',''))[:100]} — "
                        f"Source: {f.get('source','')}"
                    )
                else:
                    lines.append(f"  {f}")

        return {
            "content": f"[AI ANALYSIS] Risk score: {score}/100 — Level: {level}. Source: RiskAgent.",
            "confidence": int(conf),
            "items": lines,
        }

    # Inline fallback: rule-based risk from person object
    try:
        from modules.ontology import PersonEntity, calculate_risk_score as _calc_risk
        pe = PersonEntity(
            name=person.get("confirmed_name", ""),
            name_variants=person.get("name_variants", []),
            phones=person.get("phones_found", []),
            locations=person.get("location_stated", []),
            data_sources=person.get("data_sources", []),
            accounts=list(person.get("join_dates", {}).keys()),
        )
        risk_result = _calc_risk(pe)
        score = risk_result.get("risk_score", 0)
        level = risk_result.get("risk_level", "LOW")
        factors = risk_result.get("risk_factors", risk_result.get("factors", []))
        lines = [
            f"RISK SCORE: {score}/100 — {level}",
            f"(Calculated inline — RiskAgent not available)",
        ]
        for f in factors[:10]:
            if isinstance(f, dict):
                lines.append(f"  [{f.get('factor','?')}] Weight: {f.get('weight',0)} — {str(f.get('evidence',''))[:80]}")
        return {
            "content": f"[VERIFIED DATA] Risk score: {score}/100 — Level: {level}.",
            "confidence": 50,
            "items": lines,
        }
    except Exception:
        return {
            "content": "[AI ANALYSIS] Risk assessment not available.",
            "confidence": 0,
            "items": ["Risk assessment data not available — run RiskAgent or reprocess."],
        }


def _build_next_steps_section(agent_results: dict = None, person: dict = None) -> dict:
    """
    Build section 17 — Investigative Next Steps.
    Priority: agent_results["next_steps"] -> run NextStepAgent inline -> rule-based.
    NEVER returns empty — always has ≥5 actionable steps.
    """
    # Try agent results first
    ns_data = None
    if agent_results:
        ns_data = agent_results.get("next_steps")

    # If agent result missing or incomplete, run NextStepAgent inline now
    if not ns_data or not (ns_data.get("next_steps") or ns_data.get("steps")):
        try:
            from modules.ai_agents import run_next_step_agent
            _p = person or {}
            # Feed anomaly strings from person object so domain triggers fire
            _anomalies = []
            for _f in _p.get("conflicts", []):
                _anomalies.append(_f.get("flag", str(_f)) if isinstance(_f, dict) else str(_f))
            for _f in _p.get("anomaly_flags", []):
                _anomalies.append(_f.get("flag", str(_f)) if isinstance(_f, dict) else str(_f))
            report_stub = {"person": _p, "anomalies": _anomalies}
            ns_data = run_next_step_agent(report_stub)
        except Exception:
            ns_data = None

    if ns_data and (ns_data.get("next_steps") or ns_data.get("steps")):
        steps = ns_data.get("next_steps") or ns_data.get("steps", [])
        summary = ns_data.get("summary", "")
        lines = []
        if summary:
            lines.append(f"SUMMARY: {summary}")
        for i, step in enumerate(steps[:8], 1):
            if isinstance(step, dict):
                # Support both old schema ("action") and new schema ("step")
                action   = step.get("step") or step.get("action", "")
                n        = step.get("step_number", step.get("number", i))
                basis    = step.get("legal_basis", "")
                auth     = step.get("authorization_required", "")
                gap      = step.get("fills_gap") or step.get("data_gap_filled", "")
                priority = step.get("priority", "")
                value    = step.get("value") or step.get("estimated_value", "")
                lines.append(
                    f"STEP {n}: {action}"
                    + (f" | Legal basis: {basis}" if basis else "")
                    + (f" | Authorization: {auth}" if auth else "")
                    + (f" | Priority: {priority}" if priority else "")
                    + (f" | Value: {value}" if value else "")
                    + (f" | Fills gap: {gap}" if gap else "")
                )
            else:
                lines.append(f"• {step}")

        return {
            "content": f"[AI ANALYSIS] {len(steps)} investigative step(s) generated by NextStepAgent.",
            "confidence": 75,
            "items": lines or ["No next steps generated."],
        }

    # Rule-based fallback
    person = person or {}
    lines = []
    step_n = 1

    if not person.get("phones_found"):
        lines.append(
            f"STEP {step_n}: Request CDR (Call Detail Records) from telecom provider. "
            f"Legal basis: Section 91 CrPC / IT Act Sec 69. Authorization: Court order. "
            f"Priority: 1 — Fills gap: Phone number history."
        )
        step_n += 1

    if not person.get("location_stated"):
        lines.append(
            f"STEP {step_n}: Conduct IMEI/tower location analysis with telecom. "
            f"Legal basis: TRAI regulations / CrPC 91. Authorization: ACP approval. "
            f"Priority: 2 — Fills gap: Physical movement trail."
        )
        step_n += 1

    if not person.get("confirmed_linked_profiles"):
        lines.append(
            f"STEP {step_n}: Cross-reference usernames across dark web + social platforms. "
            f"Legal basis: IT Act 2000, Sec 69. Authorization: Designated Officer. "
            f"Priority: 3 — Fills gap: Digital identity linkage."
        )
        step_n += 1

    lines.append(
        f"STEP {step_n}: Verify identity documents against government databases (Aadhaar/PAN). "
        f"Legal basis: DPDP Act 2023, IT Act 2000. Authorization: Nodal officer. "
        f"Priority: {step_n} — Fills gap: Identity confirmation."
    )
    step_n += 1

    lines.append(
        f"STEP {step_n}: Conduct physical surveillance per SOP. "
        f"Legal basis: CrPC 41/41A. Authorization: Inspector level and above. "
        f"Priority: {step_n} — Fills gap: Current physical location."
    )

    return {
        "content": "[VERIFIED DATA] Rule-based investigative next steps. Run NextStepAgent for AI-enhanced guidance.",
        "confidence": 40,
        "items": lines,
    }


def _build_linked_profiles_section(person: dict) -> dict:
    """Build section 13 data from Person Object linked profiles."""
    confirmed = person.get("confirmed_linked_profiles", [])
    potential = person.get("potential_linked_profiles", [])
    summary   = person.get("cross_platform_summary", {})

    lines = []
    if confirmed:
        lines.append(f"CONFIRMED LINKED ACCOUNTS ({len(confirmed)}):")
        for c in confirmed[:20]:
            pub  = c.get("public_data", {})
            bio  = pub.get("bio", "") or pub.get("snippet", "")
            line = (
                f"[CONFIRMED] {c.get('platform','')} — @{c.get('username','')} "
                f"— {c.get('url','')} "
                f"— Match: {c.get('match_reason','')} "
                f"— Confidence: {c.get('confidence',100)}%"
            )
            if bio:
                line += f" — {bio[:80]}"
            lines.append(line)
    else:
        lines.append("No confirmed linked accounts found.")

    if potential:
        lines.append(f"\nPOTENTIAL LINKED ACCOUNTS ({len(potential)}):")
        for p in potential[:20]:
            pub    = p.get("public_data", {})
            bio    = pub.get("bio", "") or pub.get("snippet", "")
            conf_v = p.get("confidence", 0)
            flag   = " [LOW CONFIDENCE — VERIFY MANUALLY]" if conf_v < 70 else ""
            line   = (
                f"[POTENTIAL] {p.get('platform','')} — @{p.get('username','')} "
                f"— {p.get('url','')} "
                f"— Match: {p.get('match_reason','')} "
                f"— Confidence: {conf_v}%{flag}"
            )
            if bio:
                line += f" — {bio[:80]}"
            lines.append(line)

    plats = summary.get("platforms_present", [])
    if plats:
        lines.append(f"\nCross-platform summary: {len(confirmed)} confirmed, {len(potential)} potential. "
                     f"Platforms identified: {', '.join(plats)}.")

    checked = summary.get("platforms_checked", [])
    if checked:
        lines.append(f"Platforms checked during discovery: {', '.join(checked)}.")

    return {
        "content": (
            f"[VERIFIED DATA] Cross-platform discovery: "
            f"{len(confirmed)} confirmed linked accounts, {len(potential)} potential linked accounts."
        ),
        "confidence": 90 if confirmed else (50 if potential else 0),
        "items": lines or ["No cross-platform discovery data available."],
    }


def _build_extracted_intelligence_section(person: dict) -> dict:
    """Build section 14 data from Person Object extracted contact intelligence."""
    emails   = person.get("emails_found", [])
    phones   = person.get("phones_found", [])
    websites = person.get("websites_found", [])
    li_intel = person.get("linkedin_intelligence", {})
    confirmed= person.get("confirmed_linked_profiles", [])

    lines = []

    # Emails
    if emails:
        lines.append(f"EMAILS FOUND ({len(emails)}):")
        for email in emails[:10]:
            status = "VERIFIED" if sum(1 for c in confirmed if email in str(c)) > 1 else "SINGLE SOURCE"
            lines.append(f"  {email} — Source: LinkedIn/profile data — {status}")
    else:
        lines.append("Emails: None found in public data.")

    # Phones — per-file source attribution from phone_sources map
    phone_sources = person.get("phone_sources", {})
    if phones:
        lines.append(f"\nPHONE NUMBERS ({len(phones)}):")
        for phone in phones[:30]:
            srcs = phone_sources.get(phone, [])
            if srcs:
                src_label = ", ".join(srcs[:3])
                if len(srcs) > 3:
                    src_label += f" +{len(srcs) - 3} more"
            else:
                src_label = "document data"
            lines.append(f"  {phone} — Source: {src_label} — EXTRACTED")
    else:
        lines.append("Phone numbers: None found in public data.")

    # Social handles from confirmed accounts
    social_handles = []
    for c in confirmed[:20]:
        if c.get("username") and c.get("platform"):
            social_handles.append(
                f"  {c['platform']}: @{c['username']} — {c.get('url','')} — CONFIRMED"
            )
    if social_handles:
        lines.append(f"\nSOCIAL HANDLES CONFIRMED ({len(social_handles)}):")
        lines.extend(social_handles)
    else:
        lines.append("\nSocial handles: None confirmed beyond initial search.")

    # Websites
    if websites:
        lines.append(f"\nWEBSITES FOUND ({len(websites)}):")
        for w in websites[:5]:
            lines.append(f"  {w} — Source: LinkedIn/profile data — SINGLE SOURCE")
    else:
        lines.append("Websites: None found in public data.")

    # LinkedIn summary
    if li_intel and li_intel.get("name"):
        lines.append("\nLINKEDIN INTELLIGENCE SUMMARY:")
        if li_intel.get("name"):
            lines.append(f"  Name: {li_intel['name']}")
        if li_intel.get("headline"):
            lines.append(f"  Headline: {li_intel['headline'][:120]}")
        if li_intel.get("location"):
            lines.append(f"  Location: {li_intel['location']}")
        if li_intel.get("company"):
            lines.append(f"  Company: {li_intel['company']}")
        if li_intel.get("education"):
            lines.append(f"  Education: {', '.join(li_intel['education'][:3])}")
        if li_intel.get("twitter_found"):
            lines.append(f"  Twitter/X: @{li_intel['twitter_found']} — CONFIRMED via LinkedIn")
        if li_intel.get("github_found"):
            lines.append(f"  GitHub: {li_intel['github_found']} — CONFIRMED via LinkedIn")
        if li_intel.get("instagram_found"):
            lines.append(f"  Instagram: @{li_intel['instagram_found']} — CONFIRMED via LinkedIn")
        if li_intel.get("website_found"):
            lines.append(f"  Website: {li_intel['website_found']}")
        for other in li_intel.get("other_socials", [])[:3]:
            lines.append(f"  {other}")
        lines.append(f"  LinkedIn data confidence: {li_intel.get('confidence', 0)}%")
    else:
        lines.append("\nLinkedIn intelligence: Not available for this target.")

    total = len(emails) + len(phones) + len(websites) + len(social_handles)
    return {
        "content": (
            f"[VERIFIED DATA] Extracted intelligence: "
            f"{len(emails)} email(s), {len(phones)} phone(s), "
            f"{len(websites)} website(s), {len(social_handles)} social handle(s) confirmed."
        ),
        "confidence": min(90, total * 15) if total > 0 else 0,
        "items": lines or ["No intelligence extracted from linked profiles."],
    }


def _build_account_timeline_section(person: dict) -> dict:
    """Build section 15 data from Person Object account timeline."""
    timeline = person.get("account_timeline", [])
    oldest   = person.get("oldest_account", {})
    newest   = person.get("newest_account", {})
    flags    = person.get("account_creation_flags", [])
    age      = person.get("digital_age_years", 0)

    lines = []

    if not timeline:
        lines.append("No account creation date data available for this subject.")
        return {
            "content": "[VERIFIED DATA] Account creation timeline: No date data available.",
            "confidence": 0,
            "items": lines,
        }

    lines.append(f"DIGITAL IDENTITY AGE: {age} year(s)")
    lines.append("")

    # Timeline table rows
    lines.append("ACCOUNT CREATION CHRONOLOGY:")
    for e in timeline:
        platform  = e.get("platform", "Unknown")
        date_str  = e.get("join_date_str", e.get("date", "Unknown"))
        conf      = e.get("confidence", "")
        age_yrs   = e.get("age_years", 0)
        ptype     = e.get("profile_type", "primary")
        source    = e.get("source", "")
        flag_str  = " [LINKED]" if ptype != "primary" else ""
        lines.append(
            f"  {platform}: {date_str} — {age_yrs} yrs old — "
            f"Confidence: {conf}{flag_str} — Source: {source}"
        )

    # Oldest / newest
    if oldest:
        lines.append("")
        lines.append(
            f"OLDEST ACCOUNT: {oldest.get('platform','?')} — "
            f"{oldest.get('join_date_str', oldest.get('date','?'))} — "
            f"Confidence: {oldest.get('confidence','?')}"
        )
    if newest and newest.get("platform") != oldest.get("platform"):
        lines.append(
            f"NEWEST ACCOUNT: {newest.get('platform','?')} — "
            f"{newest.get('join_date_str', newest.get('date','?'))} — "
            f"Confidence: {newest.get('confidence','?')}"
        )

    # Pattern flags
    if flags:
        lines.append("")
        lines.append(f"PATTERN FLAGS ({len(flags)}):")
        for f in flags:
            sev = f.get("severity", "")
            detail = f.get("detail", f.get("flag", ""))
            lines.append(f"  [{sev}] {detail}")

    exact_count = sum(1 for e in timeline if e.get("confidence") == "EXACT")
    confidence = min(90, 40 + exact_count * 15)

    return {
        "content": (
            f"[VERIFIED DATA] Account timeline: {len(timeline)} platform(s) with date data. "
            f"Digital identity age: {age} year(s). "
            f"{len([f for f in flags if f.get('flag') not in ('OLDEST_ACCOUNT','NO_DATE_DATA')])} pattern flag(s) detected."
        ),
        "confidence": confidence,
        "items": lines,
    }


_ENGINE_DISPLAY_MAP = {
    "claude-sonnet-4-bedrock": "Claude Sonnet 4 via AWS Bedrock ap-south-1 Mumbai — Data stays in India",
    "claude-opus-4-bedrock":   "Claude Opus 4 via AWS Bedrock ap-south-1 Mumbai — Data stays in India",
    "gemini":                  "Gemini 2.5 Flash via Google API",
    "gemini-fallback":         "Gemini 2.5 Flash via Google API",
    "local-fallback":          "Local rule-based analysis — AI engines unavailable",
    "local-multidoc":          "Local rule-based analysis — AI engines unavailable",
    "local":                   "Local rule-based analysis — AI engines unavailable",
}


def _build_engine_notes(engines_used: dict = None) -> dict:
    """Build section 12 AI engine notes from per-task engine tracking dict."""
    eu = engines_used or {}
    er  = eu.get("entity_resolution",  "local-fallback")
    ba  = eu.get("behavioral_analysis","local-fallback")
    rw  = eu.get("report_writing",     "local-fallback")
    ra  = eu.get("risk_agent",         "local-fallback")
    ns  = eu.get("next_step_agent",    "local-fallback")

    def _display(engine_id: str) -> str:
        return _ENGINE_DISPLAY_MAP.get(engine_id, engine_id or "Unknown")

    lines = [
        f"Entity Resolution:    {_display(er)}",
        f"Behavioral Analysis:  {_display(ba)}",
        f"Report Writing:       {_display(rw)}",
        f"Risk Assessment:      {_display(ra)}",
        f"Next Steps:           {_display(ns)}",
    ]

    all_vals = list(eu.values())
    if any("bedrock" in v for v in all_vals):
        overall     = "claude-sonnet-4-bedrock"
        overall_str = "Claude Sonnet 4 via AWS Bedrock ap-south-1 Mumbai"
    elif any("gemini" in v for v in all_vals):
        overall     = "gemini"
        overall_str = "Gemini 2.5 Flash via Google API"
    else:
        overall     = "local-fallback"
        overall_str = "Local rule-based analysis"

    lines.append(f"Model: {overall}")
    return {
        "content": f"[AI ANALYSIS] Primary engine: {overall_str}",
        "model":   overall,
        "items":   lines,
    }


def _fmt_usernames(usernames) -> str:
    """Format the usernames field cleanly — never emit raw Python dicts."""
    if isinstance(usernames, dict):
        return ", ".join(usernames.keys()) if usernames else "None"
    if isinstance(usernames, (list, set)):
        return ", ".join(str(u) for u in usernames) if usernames else "None"
    return str(usernames) if usernames else "None"


def _build_location_lines(locations: list) -> list:
    """
    Return deduplicated, filtered location lines for §04.
    Removes bare city/country names, short strings, and duplicates.
    Caps at 8 meaningful entries.
    """
    _BARE_NAMES = {
        "mumbai", "delhi", "pune", "bengaluru", "bangalore", "hyderabad",
        "gurugram", "gurgaon", "nashik", "nagpur", "ahmedabad", "surat",
        "jaipur", "lucknow", "kolkata", "chennai", "noida", "thane",
        "india", "dubai", "uae", "uk", "usa", "pakistan", "bangladesh",
        "singapore", "not found", "unknown",
    }
    seen: set  = set()
    lines: list = []
    for loc in locations:
        loc_str = str(loc).strip()
        if len(loc_str) < 6:
            continue
        key = loc_str.lower()
        if key in _BARE_NAMES or key in seen:
            continue
        seen.add(key)
        lines.append(f"{loc_str} — Source: stated in documents")
        if len(lines) >= 8:
            break
    # Fallback: if all were filtered, show first 3 unique entries as-is
    if not lines and locations:
        seen2: set = set()
        for loc in locations:
            loc_str = str(loc).strip()
            if loc_str and loc_str.lower() not in seen2:
                seen2.add(loc_str.lower())
                lines.append(f"{loc_str} — Source: document mention")
                if len(lines) >= 3:
                    break
    return lines


def _build_timeline_intelligence_section(
    timeline_events: list,
    contradictions:  list,
    gaps:            list,
    narrative:       dict = None,
) -> dict:
    """Build Section 06B — Timeline Intelligence Analysis for PDF."""
    from modules.timeline import score_event_strength

    lines = [
        "TIMELINE ANALYSIS — For use as investigative reference. "
        "Source citations included. Suitable for annexure to charge sheet.",
        "",
    ]

    # ── Subsection A — Evidence Strength ─────────────────────────────────────
    lines.append("SUBSECTION A — EVIDENCE STRENGTH:")
    if timeline_events:
        for ev in timeline_events[:30]:
            strength_data = ev.get("evidence_strength") or score_event_strength(ev, timeline_events)
            strength  = strength_data.get("strength", "UNVERIFIED")
            date_str  = ev.get("date", ev.get("normalized", ""))
            time_str  = ev.get("time", "")
            desc      = ev.get("description", ev.get("context", ""))[:70]
            src       = ev.get("source", "")[:50]
            ts        = f"{date_str} {time_str}".strip()
            lines.append(f"  [{strength}] {ts} — {desc} — Source: {src}")
    else:
        lines.append("  No timeline events to score.")

    # ── Subsection B — Contradictions ─────────────────────────────────────────
    lines.append("")
    if contradictions:
        lines.append(
            f"SUBSECTION B — CONTRADICTIONS DETECTED: {len(contradictions)}"
        )
        for c in contradictions:
            sev      = c.get("severity", "")
            ts       = c.get("timestamp", "")
            conflict = c.get("conflict", "")
            sources  = " | ".join(c.get("sources", []))
            court    = c.get("court_note", "")
            lines.append(f"  [{sev}] {ts}")
            lines.append(f"    CONFLICT: {conflict}")
            lines.append(f"    SOURCES: {sources}")
            lines.append(f"    COURT NOTE: {court}")
    else:
        lines.append("SUBSECTION B — CONTRADICTIONS: No timeline contradictions detected.")

    # ── Subsection C — Evidence Gaps ─────────────────────────────────────────
    lines.append("")
    if gaps:
        lines.append(f"SUBSECTION C — EVIDENCE GAPS: {len(gaps)} gap(s) found")
        for g in gaps:
            start    = g.get("gap_start", "")
            end      = g.get("gap_end", "")
            days     = g.get("gap_days", 0)
            sev      = g.get("severity", "")
            evidence = ", ".join(g.get("evidence_needed", [])[:3])
            court    = g.get("court_note", "")
            lines.append(f"  [{sev}] {start} to {end} ({days} days)")
            lines.append(f"    Evidence needed: {evidence}")
            lines.append(f"    Court note: {court}")
    else:
        lines.append("SUBSECTION C — EVIDENCE GAPS: No significant gaps detected.")

    # ── AI Narrative ─────────────────────────────────────────────────────────
    if narrative and narrative.get("narrative"):
        lines.append("")
        lines.append("SUBSECTION D — AI TIMELINE NARRATIVE (TimelineAgent):")
        lines.append(f"  {narrative.get('narrative', '')}")
        pattern = narrative.get("pattern_summary", "")
        if pattern:
            lines.append(f"  Pattern: {pattern}")
        focus = narrative.get("investigator_focus", "")
        if focus:
            lines.append(f"  Investigator priority: {focus}")
        for cm in narrative.get("critical_moments", [])[:5]:
            lines.append(f"  Critical moment: {cm}")

    total_flags = len(contradictions) + len(gaps)
    return {
        "content": (
            f"[VERIFIED DATA] Timeline intelligence: "
            f"{len(contradictions)} contradiction(s) detected, "
            f"{len(gaps)} gap(s) found, "
            f"{len(timeline_events)} events scored."
        ),
        "confidence": 90,
        "items": lines,
    }


def _build_sections_local(
    person:          dict,
    search_results:  dict,
    graph_data:      dict,
    timeline_data:   dict,
    behavioral_data: dict,
    agent_results:   dict = None,
    raw_documents:   list = None,
    engines_used:    dict = None,
) -> dict:
    results = (search_results or {}).get("results", [])
    name    = person.get("confirmed_name", "Not found")
    tl      = timeline_data or {}
    bd      = (behavioral_data or {}).get("assessment", {})
    gd      = graph_data or {}

    def _src_list():
        return list({r.get("url", "") for r in results if r.get("url", "")})

    locations  = person.get("location_stated", [])
    events     = [
        f"{e['normalized']}: {e.get('context', e.get('description', ''))[:60]} — Source: {e['source']}"
        for e in tl.get("events", [])[:20]
    ]

    behavior_content = (
        "[AI ANALYSIS] " + (bd.get("analyst_notes") or "Not determined.") +
        (f" Activity pattern: {bd['activity_pattern']}." if bd.get("activity_pattern") else "")
    ) if bd else "[AI ANALYSIS] Not available — no behavioral data."

    # ── Section 08: Key Associations — person/org/alias nodes only, subject excluded
    # Use pre-filtered top_associations from graph_summary when available;
    # fall back to filtering top_nodes by node_type for older graph_data payloads.
    summary = gd.get("summary", {})
    raw_associations = list(summary.get("top_associations") or [])
    if not raw_associations:
        # Fallback: filter top_nodes to person/org/alias, exclude subject
        subject_lbl = name.lower()
        raw_associations = [
            n for n in summary.get("top_nodes", [])
            if n.get("node_type", "unknown") in ("person", "org", "alias")
            and n.get("label", "").lower() != subject_lbl
        ]

    # ── Supplement thin associations with available intelligence signals ────────
    # When fewer than 3 named associates exist, add platform, location, and phone
    # signals so §08 is never empty for CDR/financial-only investigations.
    if len(raw_associations) < 3:
        _assoc_seen = {a.get("label", "").lower() for a in raw_associations}
        # Platforms confirmed on
        for plat in person.get("platforms_confirmed", []):
            if plat.lower() not in _assoc_seen and len(raw_associations) < 6:
                raw_associations.append({"label": plat, "centrality": 0.1, "node_type": "platform"})
                _assoc_seen.add(plat.lower())
        # Location nodes from graph
        for n in summary.get("top_nodes", []):
            if (n.get("node_type") == "location"
                    and n.get("label", "").lower() not in _assoc_seen
                    and len(raw_associations) < 6):
                raw_associations.append(n)
                _assoc_seen.add(n.get("label", "").lower())
        # Key phone numbers as contact signals (max 2)
        _ph_added = 0
        for ph in person.get("phones_found", []):
            ph_str = ph if isinstance(ph, str) else str(ph)
            if ph_str and ph_str.lower() not in _assoc_seen and _ph_added < 2 and len(raw_associations) < 6:
                raw_associations.append({"label": ph_str, "centrality": 0.05, "node_type": "contact"})
                _assoc_seen.add(ph_str.lower())
                _ph_added += 1

    def _assoc_label(n: dict) -> str:
        lbl   = n.get("label", "")
        ntype = n.get("node_type", "")
        cent  = n.get("centrality", 0)
        if ntype == "platform":
            return f"{lbl} (confirmed platform)"
        if ntype == "location":
            return f"{lbl} (location)"
        if ntype == "contact":
            return f"{lbl} (contact number)"
        return f"{lbl} (centrality: {cent})"

    conns = [_assoc_label(n) for n in raw_associations]

    # ── Collect anomalies from ALL sources ───────────────────────────────────
    _anom_seen: set = set()

    def _add_anomaly(entry: str):
        key = entry.lower()[:80]
        if key not in _anom_seen and entry.strip():
            _anom_seen.add(key)
            anomalies.append(entry)

    anomalies: list = []
    # Source 1: timeline anomalies
    for a in tl.get("anomalies", []):
        _add_anomaly(f"{a.get('flag','?')}: {a.get('detail','')}")
    # Source 2: behavioral analysis — rule anomalies, AI anomalies, and behavioral flags.
    # behavioral_flags are included here with a minimum-length guard (>20 chars) to
    # filter trivial entries while surfacing meaningful flags (deletions, VPN, CERT-In, etc.).
    if bd:
        for ra in bd.get("rule_anomalies", []):
            _add_anomaly(f"{ra.get('flag','?')}: {ra.get('detail','')}")
        # bd["anomalies"] = AI-detected structural anomalies distinct from flags
        for ba in bd.get("anomalies", []):
            _add_anomaly(str(ba))
        # bd["behavioral_flags"] = meaningful activity flags from behavioral analysis
        for bf in bd.get("behavioral_flags", []):
            bf_str = str(bf).strip()
            if bf_str and len(bf_str) > 20:
                _add_anomaly(bf_str)
    # Source 3: account creation pattern flags
    for f in person.get("account_creation_flags", []):
        if isinstance(f, dict):
            _add_anomaly(f"[{f.get('severity','?')}] {f.get('detail', f.get('flag',''))}")
        else:
            _add_anomaly(str(f))
    # Source 4: ingestion-level anomaly flags (may be dicts or strings)
    for f in person.get("anomaly_flags", []):
        if isinstance(f, dict):
            _add_anomaly(f.get("flag") or f.get("detail") or str(f))
        else:
            _add_anomaly(str(f))
    # Source 5: conflict detection results
    for c in person.get("conflicts", []):
        if isinstance(c, dict):
            _add_anomaly(c.get("flag") or c.get("detail") or str(c))
        else:
            _add_anomaly(str(c))
    # NOTE: risk_factors from agent_results are intentionally NOT added here.
    # They are fully rendered in §16 Risk Assessment (_build_risk_section).
    # Adding them here as [RISK FLAG] entries caused every factor to appear
    # twice in the report (audit problem #4).
    # Source 6: raw document keyword scan — CERT-In, IT Act, DPDP, etc.
    # These flags are added here so they are present in anomalies_and_flags.flags
    # and therefore visible to run_next_step_agent when it builds investigative steps.
    if raw_documents:
        _DOC_KEYWORD_FLAGS = [
            (("CERT-IN", "CERT-In", "CERTIN", "COMPUTER EMERGENCY RESPONSE TEAM"),
             "CERT-In inquiry confirmed"),
            (("IT ACT", "INFORMATION TECHNOLOGY ACT", "SECTION 43", "SECTION 66", "SECTION 69"),
             "IT Act violation flagged"),
            (("DPDP", "DATA PROTECTION AND PRIVACY", "DATA PROTECTION ACT"),
             "DPDP Act breach suspected"),
            (("DELETED", "DELETION", "REPO_DELETE", "POST_DELETE", "MODEL_DELETE"),
             "Evidence deletion confirmed"),
            (("UNAUTHORISED ACCESS", "UNAUTHORIZED ACCESS"),
             "Unauthorised access flagged"),
            (("SCRAPING", "SCRAPED", "DATA SCRAPE"),
             "Unauthorised data scraping flagged"),
            (("DEPLOYED", "DEPLOYMENT", "MALWARE", "EXPLOIT"),
             "Malicious deployment / exploit activity flagged"),
        ]
        for _doc in raw_documents:
            _doc_text = str(_doc.get("full_text", "") or _doc.get("raw_text", "")).upper()
            for _kws, _flag_label in _DOC_KEYWORD_FLAGS:
                if any(_kw.upper() in _doc_text for _kw in _kws):
                    _add_anomaly(_flag_label)

    # ── Consolidate repetitive "Cluster" burst-activity entries (Issue 3) ──────
    # Timeline may produce one "Cluster: N events on DATE" line per burst date.
    # Collapse them into a single summary to prevent them filling the top-15 cap.
    _cluster_lines = [a for a in anomalies if a.lower().startswith("cluster:")]
    _other_lines   = [a for a in anomalies if not a.lower().startswith("cluster:")]
    if len(_cluster_lines) > 2:
        _other_lines.extend(_cluster_lines[:2])
        _other_lines.append(
            f"Burst activity: {len(_cluster_lines)} dates with elevated communication volume detected"
        )
    else:
        _other_lines.extend(_cluster_lines)
    anomalies = _other_lines

    gaps     = person.get("data_gaps", [])
    all_urls = _src_list()

    # ── Fix 1E: build platform presence from all sources ─────────────────────
    plat_map = build_platform_presence(person)
    plat_content = (
        f"[VERIFIED DATA] Confirmed on {len(plat_map)} platform(s): "
        f"{', '.join(plat_map.keys()) or 'None'}."
    )
    plat_dict = {
        p: f"{v.get('url','Not found')} | @{v.get('username','?')} | {v.get('status','?')}"
        for p, v in plat_map.items()
    }

    # ── Fix 1D: build source log from uploaded documents ─────────────────────
    source_log_lines = build_source_log(raw_documents or [], search_results)

    return {
        "subject_identity": {
            "content": (
                f"[VERIFIED DATA] Name: {name}. "
                f"Variants: {', '.join(person.get('name_variants', [])) or 'None'}. "
                f"Usernames: {_fmt_usernames(person.get('usernames', {}))}."
            ),
            "confidence": person.get("confidence_score", 0),
            "verified_items": [f"{name} — Source: {s}" for s in person.get("data_sources", [])],
        },
        "platform_presence": {
            "content": plat_content,
            "confidence": min(len(plat_map) * 20, 90),
            "platforms": plat_dict,
        },
        "public_location_data": {
            "content": f"[VERIFIED DATA] Stated locations: {', '.join(locations) or 'Not found'}.",
            "confidence": 60 if locations else 0,
            "locations": _build_location_lines(locations),
        },
        "network_map_summary": {
            "content": (
                f"[VERIFIED DATA] Graph: {gd.get('summary', {}).get('nodes', 0)} nodes, "
                f"{gd.get('summary', {}).get('edges', 0)} edges."
            ),
            "confidence": 50 if conns else 10,
            "connections": conns or ["No confirmed connections mapped."],
        },
        "timeline_of_activity": {
            "content": f"[VERIFIED DATA] {tl.get('count', 0)} temporal data points extracted.",
            "confidence": min(tl.get("count", 0) * 5, 80),
            "events": events or ["No dated events found."],
        },
        "behavioral_patterns": {
            "content": behavior_content,
            "confidence": bd.get("timezone_confidence", 0) if bd else 0,
            "flags": bd.get("behavioral_flags", []) if bd else [],
        },
        "key_associations": {
            "content": f"[VERIFIED DATA] {len(conns)} confirmed network associations.",
            "confidence": 40 if conns else 0,
            "associations": conns or ["None confirmed."],
        },
        "anomalies_and_flags": {
            "content": f"{len(anomalies)} anomaly/flag(s) detected." if anomalies else "No anomalies detected.",
            "flags": anomalies or [],
        },
        "data_gaps": {"items": gaps or ["No gaps identified."]},
        "source_log": {"items": source_log_lines},
        "ai_engine_notes": _build_engine_notes(engines_used),
        "overall_confidence": person.get("confidence_score", 0),
        "confidence_breakdown": person.get("confidence_breakdown", {}),
        "confidence_explanation": person.get("confidence_explanation", ""),
        "linked_profiles":        _build_linked_profiles_section(person),
        "extracted_intelligence": _build_extracted_intelligence_section(person),
        "account_timeline":       _build_account_timeline_section(person),
        # ── Sections 16 + 17 — always present ────────────────────────────────
        "risk_assessment": _build_risk_section(person, agent_results, raw_documents),
        "next_steps":      _build_next_steps_section(agent_results, person),
    }


# ══════════════════════════════════════════════════════════════════════════════
# SECTIONS -> PDF DATA ADAPTER
# ══════════════════════════════════════════════════════════════════════════════

def _sections_to_pdf_data(sections: dict) -> dict:
    """
    Translate internal sections dict (Gemini or local format) into the flat
    report_data dict expected by generate_pdf().
    """
    s1  = sections.get("subject_identity",    {})
    s3  = sections.get("platform_presence",   {})
    s4  = sections.get("public_location_data",{})
    s5  = sections.get("network_map_summary", {})
    s6  = sections.get("timeline_of_activity",{})
    s7  = sections.get("behavioral_patterns", {})
    s8  = sections.get("key_associations",    {})
    s9  = sections.get("anomalies_and_flags", {})
    s10 = sections.get("data_gaps",           {})
    s11 = sections.get("source_log",          {})
    s12 = sections.get("ai_engine_notes",     {})
    oc  = sections.get("overall_confidence",  0)

    def _flatten(section, items_key):
        """Return content string + bullet list as a single list."""
        content = section.get("content", "")
        items   = section.get(items_key, [])
        out     = []
        if content:
            out.append(content)
        for item in (items or [])[:30]:
            out.append(f"• {item}")
        return out or ["Not found."]

    # Behavioral flags separate
    behav_flags = s7.get("flags", [])
    behav_lines = [s7.get("content", "Not available.")]
    if behav_flags:
        behav_lines.append("Behavioral flags:")
        for f in behav_flags[:10]:
            behav_lines.append(f"⚑ {f}")

    # Anomalies — capped at 15 entries; behavioral_flags excluded (in §07)
    anom_content = s9.get("content", "")
    anom_flags   = s9.get("flags", [])
    anom_lines   = [anom_content] if anom_content else []
    for f in anom_flags[:15]:
        anom_lines.append(f"⚑ {f}")
    if len(anom_flags) > 15:
        anom_lines.append(f"  … and {len(anom_flags) - 15} additional flag(s) — see full audit log.")

    # AI notes
    ai_content = s12.get("content", "Not available.")
    ai_model   = s12.get("model", "")
    ai_lines   = [ai_content]
    if ai_model:
        ai_lines.append(f"Model: {ai_model}")

    s06b = sections.get("timeline_intelligence", {})
    s13 = sections.get("linked_profiles",         {})
    s14 = sections.get("extracted_intelligence",  {})
    s15 = sections.get("account_timeline",        {})
    s16 = sections.get("risk_assessment",         {})
    s17 = sections.get("next_steps",              {})
    s18 = sections.get("tactical_plan",           {})

    # Section 16 — Risk Assessment
    risk_lines = _flatten(s16, "items") if s16 else ["Risk assessment not available."]

    # Section 17 — Next Steps
    ns_lines = _flatten(s17, "items") if s17 else ["Investigative next steps not available."]

    # Section 06B — Timeline Intelligence
    ti_lines = _flatten(s06b, "items") if s06b else [
        "Timeline intelligence analysis not available."
    ]

    result = {
        "subject_identity":       _flatten(s1, "verified_items"),
        "confidence_score":       f"{oc}/100",
        "confidence_explanation": sections.get("confidence_explanation", ""),
        "platform_presence":      s3.get("platforms", {}) or s3.get("content", "Not found."),
        "location_data":          _flatten(s4, "locations"),
        "network_summary":        _flatten(s5, "connections"),
        "timeline":               _flatten(s6, "events"),
        "timeline_intelligence":  ti_lines,
        "behavioral_patterns":    behav_lines,
        "associations":           _flatten(s8, "associations"),
        "anomalies":              anom_lines or ["None detected."],
        "data_gaps":              s10.get("items", ["None identified."]),
        "source_log":             s11.get("items", s11.get("urls", ["No sources logged."])),
        "ai_notes":               ai_lines,
        "linked_profiles":        s13.get("items", ["No linked profiles data."]),
        "extracted_intelligence": s14.get("items", ["No extracted intelligence."]),
        "account_timeline":       s15.get("items", ["No account timeline data."]),
        "risk_assessment":        risk_lines,
        "next_steps":             ns_lines,
    }
    # Section 18 — pass full tactical_plan dict through for rich PDF rendering
    if isinstance(s18, dict) and s18.get("actions"):
        result["tactical_plan"] = s18
    return result


# ══════════════════════════════════════════════════════════════════════════════
# AUDIT LOG
# ══════════════════════════════════════════════════════════════════════════════

def _log_report(user_id: str, subject: str, pdf_path: str, gemini_used: bool):
    try:
        conn   = sqlite3.connect(str(config.DATABASE_PATH))
        now    = datetime.datetime.utcnow().isoformat()
        detail = json.dumps({"subject": subject, "pdf": pdf_path, "gemini": gemini_used})
        conn.execute(
            "INSERT INTO audit_log (event, username, detail, timestamp) VALUES (?,?,?,?)",
            ("REPORT_GENERATED", user_id, detail, now),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

@defensive(fallback={
    "full_report": "Report generation failed. Check error log.",
    "sections": {},
    "error": True,
    "generated_at": datetime.datetime.now().isoformat(),
    "pdf_bytes": b"",
    "pdf_path": "",
    "pdf_filename": "",
    "gemini_used": False,
})
def generate_report(
    person:          dict,
    search_results:  dict  = None,
    graph_data:      dict  = None,
    timeline_data:   dict  = None,
    behavioral_data: dict  = None,
    ip_results:      list  = None,
    user_id:         str   = "system",
    mode:            str   = "OSINT",
    agent_results:   dict  = None,
    raw_documents:   list  = None,
    assets_data:     list  = None,
) -> dict:
    """
    Build a full intelligence report.
    Returns dict with: sections, pdf_bytes, pdf_path, pdf_filename,
                       generated_at, gemini_used, bedrock_used, subject, mode, user_id.
    """
    import traceback as _tb
    generated_at = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    subject      = (person or {}).get("confirmed_name", "Unknown")
    print(f"[REPORT] generate_report() starting for: {subject}")
    try:
        return _generate_report_inner(
            person=person, search_results=search_results, graph_data=graph_data,
            timeline_data=timeline_data, behavioral_data=behavioral_data,
            ip_results=ip_results, user_id=user_id, mode=mode,
            agent_results=agent_results, raw_documents=raw_documents,
            assets_data=assets_data, generated_at=generated_at,
        )
    except Exception as _e:
        print(f"[REPORT] FAILED: {_e}")
        _tb.print_exc()
        return {
            "sections": {
                "subject_identity": {
                    "content": subject,
                    "confidence": (person or {}).get("confidence_score", 0),
                    "verified_items": [],
                },
                "overall_confidence": (person or {}).get("confidence_score", 0),
                "error_note": str(_e),
            },
            "pdf_bytes": b"",
            "pdf_path": "",
            "pdf_filename": "",
            "generated_at": generated_at,
            "gemini_used": False,
            "subject": subject,
            "mode": mode,
            "user_id": user_id,
            "error": str(_e),
        }


def _generate_report_inner(
    person, search_results, graph_data, timeline_data, behavioral_data,
    ip_results, user_id, mode, agent_results, raw_documents, assets_data, generated_at,
) -> dict:
    from modules.ai_agents import LAST_ENGINE_USED

    # ── Force primary subject to the most-mentioned PERSON in the graph ──────
    # Runs before `subject` is assigned so every downstream reference
    # (PDF header, section content, confidence explanation) uses the
    # corrected name.  Only overrides when entity resolution did not
    # already produce a confirmed non-unknown name.
    person = dict(person or {})   # local shallow copy — don't mutate caller's dict

    # ── Inject keyword-derived flags from raw document text ───────────────────
    # Ensures risk agent and next-step agent always see critical flags even when
    # structured anomaly_flags are sparse (e.g. first-run before AI has run).
    _KEYWORD_FLAGS_INJECT = [
        (("CERT-IN", "CERT-In", "CERTIN", "COMPUTER EMERGENCY RESPONSE"),
         "CERT-In inquiry confirmed"),
        (("IT ACT", "INFORMATION TECHNOLOGY ACT", "SECTION 43", "SECTION 66", "SECTION 69"),
         "IT Act violation flagged"),
        (("DPDP", "DATA PROTECTION"),
         "DPDP Act breach suspected"),
        (("DELETED", "DELETION", "REPO_DELETE", "POST_DELETE", "MODEL_DELETE"),
         "Evidence deletion confirmed"),
        (("UNAUTHORISED", "UNAUTHORIZED"),
         "Unauthorised access flagged"),
        (("SCRAPING", "SCRAPED", "DATA SCRAPE"),
         "Unauthorised data scraping flagged"),
        (("DEPLOYED", "DEPLOYMENT", "MALWARE", "EXPLOIT"),
         "Malicious deployment / exploit activity flagged"),
        (("FEMA", "FOREIGN EXCHANGE", "USD", "INTERNATIONAL TRANSFER"),
         "International financial transfer — FEMA 1999 may apply"),
    ]
    _existing_flag_text = " ".join(
        (f.get("flag", str(f)) if isinstance(f, dict) else str(f)).upper()
        for f in person.get("anomaly_flags", [])
    )
    _injected_flags = list(person.get("anomaly_flags", []))
    for doc in (raw_documents or []):
        _doc_text = str(doc.get("full_text", "") or doc.get("raw_text", "")).upper()
        for _kws, _label in _KEYWORD_FLAGS_INJECT:
            if _label.upper() not in _existing_flag_text:
                if any(kw.upper() in _doc_text for kw in _kws):
                    _injected_flags.append(_label)
                    _existing_flag_text += " " + _label.upper()
    person["anomaly_flags"] = _injected_flags

    try:
        _raw_graph    = (graph_data or {}).get("graph")
        _all_entities = (graph_data or {}).get("entities", [])
        if _raw_graph is not None and len(_raw_graph.nodes) > 0:
            from modules.relationship_mapper import get_primary_subject as _gps
            primary_subject = _gps(_all_entities, _raw_graph)
            current_name    = person.get("confirmed_name", "") or ""
            if primary_subject and primary_subject != "Unknown Subject" and (
                not current_name or current_name == "Unknown Subject"
            ):
                person["confirmed_name"] = primary_subject
                person["name"]           = primary_subject   # alias for callers using this key
                print(f"[REPORT] Primary subject set from graph: {primary_subject!r}")
            else:
                # Always keep the alias in sync even when we don't override
                person["name"] = person.get("confirmed_name", current_name)
    except Exception as _gpe:
        print(f"[REPORT] get_primary_subject non-fatal: {_gpe}")

    subject      = (person or {}).get("confirmed_name", "Unknown")
    gemini_ok    = bool(config.GEMINI_API_KEY and config.GEMINI_API_KEY != "your_gemini_key_here")
    bedrock_ok   = bool(getattr(config, "bedrock_client", None) is not None)

    # Build payload for Gemini.
    # Normalise anomaly_flags to plain strings so Gemini receives a uniform
    # JSON array instead of a mixed list of objects and strings — avoids the
    # model echoing raw dict repr() text in report prose.
    def _normalise_flags(raw_flags: list) -> list:
        out = []
        for f in (raw_flags or []):
            if isinstance(f, dict):
                out.append(f.get("flag") or f.get("detail") or str(f))
            else:
                out.append(str(f))
        return out

    person_payload = dict(person)  # shallow copy — don't mutate caller's object
    person_payload["anomaly_flags"] = _normalise_flags(person.get("anomaly_flags", []))

    payload = {
        "person": person_payload,
        "graph": {
            "nodes":     (graph_data or {}).get("summary", {}).get("nodes", 0),
            "edges":     (graph_data or {}).get("summary", {}).get("edges", 0),
            "top_nodes": (graph_data or {}).get("summary", {}).get("top_nodes", []),
        },
        "timeline": {
            "event_count": (timeline_data or {}).get("count", 0),
            "events": [
                (e.get("normalized", ""), e.get("source", ""), e.get("context", "")[:60])
                for e in (timeline_data or {}).get("events", [])[:25]
            ],
            "gaps":      (timeline_data or {}).get("gaps", []),
            "anomalies": [
                (a.get("flag", ""), a.get("detail", ""))
                for a in (timeline_data or {}).get("anomalies", [])
            ],
        },
        "behavioral": (behavioral_data or {}).get("assessment", {}),
        "search_sources": list({
            r.get("platform", "") for r in (search_results or {}).get("results", [])
        }),
        "source_urls": [
            r.get("url", "")
            for r in (search_results or {}).get("results", [])
            if r.get("url", "") and r.get("confidence", 0) > 30
        ][:30],
    }
    payload_str = json.dumps(payload, ensure_ascii=False)
    if len(payload_str) > 12000:
        payload_str = payload_str[:12000] + "... [truncated]"

    # Build per-task engine tracking dict
    engines_used = {
        "entity_resolution":  "local-fallback",
        "behavioral_analysis": "local-fallback",
        "report_writing":     "local-fallback",
        "risk_agent":         "local-fallback",
        "next_step_agent":    "local-fallback",
    }
    # Detect resolution method from person object
    resolution_method = person.get("_resolution_method", "")
    if not resolution_method:
        # Infer from person data if the key is missing
        resolution_method = "local-fallback"
    engines_used["entity_resolution"] = resolution_method
    # Detect behavioral engine
    bd_method = (behavioral_data or {}).get("method", "")
    if bd_method:
        engines_used["behavioral_analysis"] = bd_method
    # Detect agent engines from results
    if agent_results:
        if agent_results.get("risk", {}).get("engine"):
            engines_used["risk_agent"] = agent_results["risk"]["engine"]
        if agent_results.get("next_steps", {}).get("engine"):
            engines_used["next_step_agent"] = agent_results["next_steps"]["engine"]
    # Override with LAST_ENGINE_USED from ai_agents module
    last_engine = LAST_ENGINE_USED or ""
    if "bedrock" in last_engine.lower() and engines_used["entity_resolution"] == "local-fallback":
        engines_used["entity_resolution"] = last_engine

    # Try Gemini; fall back to local
    sections    = None
    gemini_used = False
    if gemini_ok:
        sections = _call_gemini_report(payload_str)
        if sections:
            gemini_used = True
            engines_used["report_writing"] = "Gemini 2.5 Flash"
            # Rule 5: filter unsourced sentences in AI-generated content strings
            _ai_section_keys = [
                "subject_identity", "platform_presence", "public_location_data",
                "network_map_summary", "timeline_of_activity", "behavioral_patterns",
                "key_associations", "anomalies_and_flags", "ai_engine_notes",
            ]
            for _sk in _ai_section_keys:
                if isinstance(sections.get(_sk), dict):
                    _c = sections[_sk].get("content", "")
                    if _c:
                        sections[_sk]["content"] = filter_unsourced_sentences(_c)
    if not sections:
        sections = _build_sections_local(
            person, search_results, graph_data, timeline_data, behavioral_data,
            agent_results=agent_results, raw_documents=raw_documents,
            engines_used=engines_used,
        )
    elif "ai_engine_notes" not in sections:
        # Gemini wrote report but no engine notes section — inject one
        sections["ai_engine_notes"] = _build_engine_notes(engines_used)
    # ── Canonical confidence calculation (replaces raw confidence_score) ─────────
    # Runs for every path (Gemini, local, OSINT, Fusion) so the score in §02
    # and the overall_confidence banner are always derived from the same evidence
    # chain rather than whatever the AI model happened to emit.
    try:
        from modules.entity_resolution import calculate_stable_confidence
        _source_log  = raw_documents or []
        _phones      = person.get("phones_found", [])
        _tl_events   = (timeline_data or {}).get("events", [])
        _gaps        = person.get("data_gaps", [])
        _graph_nodes = (graph_data or {}).get("summary", {}).get("nodes", 0)

        _emails    = person.get("emails_found", [])
        _locations = person.get("locations_mentioned", [])
        confidence_result = calculate_stable_confidence(
            num_files       = len(_source_log),
            num_phones      = len(_phones),
            num_timeline    = len(_tl_events),
            num_graph_nodes = _graph_nodes,
            num_gaps        = len(_gaps),
            num_emails      = len(_emails),
            num_locations   = len(_locations),
        )

        overall_confidence   = confidence_result["confidence"]
        confidence_breakdown = confidence_result["breakdown"]

        sections["overall_confidence"]      = overall_confidence
        sections["confidence_breakdown"]    = confidence_breakdown
        sections["confidence_explanation"]  = confidence_breakdown
        print(f"[REPORT] Confidence recalculated: {overall_confidence}/100 "
              f"(files={len(_source_log)}, phones={len(_phones)}, "
              f"tl={len(_tl_events)}, gaps={len(_gaps)}, graph_nodes={_graph_nodes})")
    except Exception as _ce:
        print(f"[REPORT] Confidence recalc failed (non-fatal): {_ce}")
        if "overall_confidence" not in sections:
            sections["overall_confidence"] = person.get("confidence_score", 0)

    # ── Timeline Intelligence (06B) — contradictions, gaps, evidence strength ────
    try:
        from modules.timeline import (
            detect_timeline_contradictions,
            detect_timeline_gaps,
            score_event_strength,
        )
        _tl_events = list((timeline_data or {}).get("events", []))
        for _ev in _tl_events:
            _ev["evidence_strength"] = score_event_strength(_ev, _tl_events)
        _tl_contradictions = detect_timeline_contradictions(
            _tl_events, raw_documents or []
        )
        _tl_gaps = detect_timeline_gaps(_tl_events)

        # Pick up AI narrative if agent already ran
        _tl_narrative = (agent_results or {}).get("timeline_analysis")

        sections["timeline_contradictions"] = _tl_contradictions
        sections["timeline_gaps"]           = _tl_gaps
        sections["timeline_intelligence"]   = _build_timeline_intelligence_section(
            _tl_events, _tl_contradictions, _tl_gaps, _tl_narrative
        )
        print(
            f"[REPORT] Timeline intelligence: "
            f"{len(_tl_contradictions)} contradiction(s), "
            f"{len(_tl_gaps)} gap(s), "
            f"{len(_tl_events)} events scored."
        )
    except Exception as _tle:
        print(f"[REPORT] Timeline intelligence failed (non-fatal): {_tle}")

    # Always inject sections 13–17 (Gemini doesn't generate them)
    if "linked_profiles" not in sections:
        sections["linked_profiles"] = _build_linked_profiles_section(person)
    if "extracted_intelligence" not in sections:
        sections["extracted_intelligence"] = _build_extracted_intelligence_section(person)
    if "account_timeline" not in sections:
        sections["account_timeline"] = _build_account_timeline_section(person)
    # Sections 16 + 17 always injected regardless of Gemini
    sections["risk_assessment"] = _build_risk_section(person, agent_results, raw_documents)
    sections["next_steps"]      = _build_next_steps_section(agent_results, person)
    # Fix 1D: rebuild source log from actual documents
    if raw_documents:
        sections["source_log"] = {"items": build_source_log(raw_documents, search_results)}
    # Fix 1E: rebuild platform presence from all sources
    plat_map = build_platform_presence(person)
    if plat_map and (not sections.get("platform_presence") or not sections["platform_presence"].get("platforms")):
        sections["platform_presence"] = {
            "content": f"[VERIFIED DATA] Confirmed on {len(plat_map)} platform(s): {', '.join(plat_map.keys())}.",
            "confidence": min(len(plat_map) * 20, 90),
            "platforms": {p: f"{v.get('url','Not found')} | @{v.get('username','?')}" for p, v in plat_map.items()},
        }

    # Section 18 — Tactical Operation Plan (always runs — no assets gate)
    tactical_plan_result = (agent_results or {}).get("tactical_plan")
    if not (isinstance(tactical_plan_result, dict) and tactical_plan_result.get("actions")):
        try:
            from modules.ai_agents import run_tactical_plan_agent
            tp_anomalies = []
            for f in person.get("anomaly_flags", []) or []:
                tp_anomalies.append(f.get("flag", str(f)) if isinstance(f, dict) else str(f))
            for c in person.get("conflicts", []) or []:
                tp_anomalies.append(c.get("flag", str(c)) if isinstance(c, dict) else str(c))
            for bf in person.get("behavioral_flags", []) or []:
                tp_anomalies.append(str(bf))
            anom_sec = sections.get("anomalies_and_flags", {})
            for fl in (anom_sec.get("flags", []) or []):
                tp_anomalies.append(str(fl))
            tactical_plan_result = run_tactical_plan_agent(
                person,
                assets_data or [],
                {"anomalies": tp_anomalies, "person": person, "subject": subject},
                user_id,
            )
        except Exception as _tp_exc:
            print(f"[TACTICAL_PLAN] _generate_report_inner fallback failed: {_tp_exc}")
            tactical_plan_result = None
    if isinstance(tactical_plan_result, dict) and tactical_plan_result.get("actions"):
        sections["tactical_plan"] = tactical_plan_result

    # Convert sections to flat PDF data format
    pdf_data = _sections_to_pdf_data(sections)

    # Determine which engines were actually used
    all_engine_vals = list(engines_used.values())
    bedrock_used = any("bedrock" in v for v in all_engine_vals) or bedrock_ok

    # Generate PDF — background drawn first, content on top
    pdf_bytes = generate_pdf(
        report_data  = pdf_data,
        username     = subject,
        user_id      = user_id,
        mode         = mode,
        gemini_used  = gemini_used,
        bedrock_used = bedrock_used,
    )

    # Save to exports/
    try:
        pdf_path = Path(save_pdf_to_exports(pdf_bytes, subject, user_id))
        filename = pdf_path.name
    except Exception:
        safe_name = re.sub(r"[^\w\-]", "_", subject)[:40]
        ts_tag    = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        filename  = f"AETHERLENS_{safe_name}_{ts_tag}.pdf"
        pdf_path  = config.EXPORTS_DIR / filename
        try:
            pdf_path.write_bytes(pdf_bytes)
        except Exception:
            pdf_path = None

    _log_report(user_id, subject, str(pdf_path) if pdf_path else "", gemini_used)

    return {
        "sections":     sections,
        "pdf_bytes":    pdf_bytes,
        "pdf_path":     str(pdf_path) if pdf_path else "",
        "pdf_filename": filename,
        "generated_at": generated_at,
        "gemini_used":  gemini_used,
        "bedrock_used": bedrock_used,
        "subject":      subject,
        "mode":         mode,
        "user_id":      user_id,
    }
