"""
AetherLens — AI Agent Orchestration Layer (Hybrid v2)
Six intelligence agents (Bedrock primary / Gemini fallback):
  RiskAgent         — hybrid deterministic + LLM risk assessment
  PatternAgent      — hidden pattern & connection detection
  NextStepAgent     — lawful investigative guidance
  ComplianceAgent   — DPDP / IT Act compliance check
  TacticalPlanAgent — sequenced 6-action Tactical Operation Plan (replaces StrategyAgent)
  TimelineAgent     — narrative timeline analysis
AgentOrchestrator   — runs all six, merges outputs.
"""

import json
import re
import datetime
import sqlite3
import uuid
from typing import Any, Dict, List

try:
    import networkx as nx
    _NX_AVAILABLE = True
except ImportError:
    _NX_AVAILABLE = False

import config

# ══════════════════════════════════════════════════════════════════════════════
# SECTION A — SHARED HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _call_bedrock(prompt: str, max_tokens: int = 4096) -> str:
    """
    Claude Sonnet 4 on AWS Bedrock (ap-south-1 / Mumbai).
    Primary engine — data stays in India for DPDP compliance.
    Retries up to 3 times with exponential backoff before giving up.
    Returns raw text on success, empty string on all-retry failure.
    """
    from config import get_bedrock_client
    client, model_id = get_bedrock_client()
    if not client:
        return ""
    for attempt in range(3):
        try:
            body = json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": prompt}],
            })
            response = client.invoke_model(
                modelId     = model_id,
                body        = body,
                contentType = "application/json",
                accept      = "application/json",
            )
            result = json.loads(response["body"].read())
            return result["content"][0]["text"] or ""
        except Exception as e:
            if attempt == 2:
                try:
                    print(f"[BEDROCK] All 3 attempts failed: {e}")
                except Exception:
                    pass
                return ""
            import time
            time.sleep(2 ** attempt)   # 1s, 2s before retries 2 and 3
    return ""


def _call_gemini(prompt: str, max_tokens: int = 4096) -> str:
    import requests
    api_key = config.GEMINI_API_KEY
    if not api_key or api_key in ("", "your_gemini_key_here"):
        return ""
    url = f"{config.GEMINI_ENDPOINT}?key={api_key}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.1, "topP": 0.9, "maxOutputTokens": max_tokens},
        "safetySettings": [
            {"category": "HARM_CATEGORY_HARASSMENT",        "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH",       "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
        ],
    }
    try:
        resp = requests.post(url, json=payload, timeout=45)
        resp.raise_for_status()
        d          = resp.json()
        candidates = d.get("candidates", [])
        if not candidates:
            return ""
        content = candidates[0].get("content", {})
        parts   = content.get("parts", [])
        if not parts:
            return ""
        return parts[0].get("text", "")
    except Exception as e:
        try:
            print(f"[GEMINI] call failed: {e}")
        except Exception:
            pass
        return ""


# Tracks which engine served the most recent _call_ai() request.
# Read by UI/report code that wants to display the source.
LAST_ENGINE_USED = "local-fallback"


def _call_ai(prompt: str, max_tokens: int = 4096) -> str:
    """
    Engine priority:
      1. Claude Sonnet 4 on AWS Bedrock (ap-south-1, India)  — primary
      2. Gemini 2.5 Flash (Google)                            — fallback
    """
    global LAST_ENGINE_USED

    raw = _call_bedrock(prompt, max_tokens)
    if raw:
        LAST_ENGINE_USED = "claude-sonnet-4-bedrock"
        return raw

    raw = _call_gemini(prompt, max_tokens)
    if raw:
        LAST_ENGINE_USED = "gemini-fallback"
        return raw

    LAST_ENGINE_USED = "local-fallback"
    return ""


def _extract_json(text: str) -> dict | None:
    """Robust JSON extractor — strips markdown fences, then tries regex fallback."""
    text = re.sub(r"```(?:json)?|```", "", text).strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        try:
            return json.loads(m.group())
        except Exception:
            pass
    return None


def _log_agent_run(agent_name: str, result_summary: str, user_id: str = "system"):
    try:
        conn = sqlite3.connect(str(config.DATABASE_PATH))
        conn.execute(
            "CREATE TABLE IF NOT EXISTS agent_audit "
            "(id TEXT, agent TEXT, result TEXT, run_at TEXT, user_id TEXT)"
        )
        conn.execute(
            "INSERT INTO agent_audit VALUES (?,?,?,?,?)",
            (str(uuid.uuid4()), agent_name, result_summary[:500],
             datetime.datetime.utcnow().isoformat(), user_id),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def get_agent_activity_log(limit: int = 100) -> list:
    try:
        conn = sqlite3.connect(str(config.DATABASE_PATH))
        rows = conn.execute(
            "SELECT id, agent, result, run_at, user_id FROM agent_audit "
            "ORDER BY run_at DESC LIMIT ?", (limit,)
        ).fetchall()
        conn.close()
        return [
            {"id": r[0], "agent": r[1], "result": r[2], "run_at": r[3], "user_id": r[4]}
            for r in rows
        ]
    except Exception:
        return []


# ══════════════════════════════════════════════════════════════════════════════
# SECTION B — INTELLIGENCE EXPANSION GUARDRAILS
# ══════════════════════════════════════════════════════════════════════════════

# Rule 1 — Grounding constraint appended to agent prompts
GROUNDING_RULE = (
    "\n\nGROUNDING CONSTRAINT — CRITICAL:\n"
    "Your output MUST reference ONLY the confirmed entities listed below.\n"
    "Do NOT invent names, locations, organizations, or identifiers not in this list.\n"
    "Every suggested action must cite at least one confirmed entity by name.\n"
    "If data is insufficient to suggest a grounded action, state: "
    "'Insufficient confirmed data for this step'.\n\n"
    "CONFIRMED ENTITIES FROM SOURCE DOCUMENTS:\n{grounding_context}"
)


def build_grounding_context(person_object: dict, ontology_data: dict = None) -> str:
    """Return a compact block of confirmed entities for injecting into agent prompts."""
    po    = person_object or {}
    lines = []
    name  = po.get("confirmed_name", "")
    if name and name not in ("Unknown Subject", "Unknown", ""):
        lines.append(f"CONFIRMED SUBJECT: {name}")
    platforms = po.get("platforms_confirmed", [])
    if platforms:
        lines.append(f"CONFIRMED PLATFORMS: {', '.join(str(p) for p in platforms[:8])}")
    locations = po.get("location_stated", [])
    if locations:
        lines.append(f"CONFIRMED LOCATIONS: {', '.join(str(l) for l in locations[:5])}")
    phones = po.get("phones_found", [])
    if phones:
        lines.append(f"CONFIRMED PHONES: {', '.join(str(p) for p in phones[:3])}")
    emails = po.get("emails_found", [])
    if emails:
        lines.append(f"CONFIRMED EMAILS: {', '.join(str(e) for e in emails[:3])}")
    sources = po.get("data_sources", [])
    if sources:
        lines.append(f"DATA SOURCES: {', '.join(str(s) for s in sources[:5])}")
    if ontology_data:
        entities = ontology_data.get("entities", {})
        enames   = [v.get("name", k) for k, v in list(entities.items())[:10] if isinstance(v, dict)]
        if enames:
            lines.append(f"GRAPH ENTITIES: {', '.join(enames)}")
        rels     = ontology_data.get("relationships", [])
        rel_strs = [
            f"{r.get('from','?')} --[{r.get('type','?')}]--> {r.get('to','?')}"
            for r in rels[:5] if isinstance(r, dict)
        ]
        if rel_strs:
            lines.append(f"GRAPH RELATIONSHIPS: {'; '.join(rel_strs)}")
    return "\n".join(lines) if lines else "No confirmed entities in source documents."


# Rule 2 — Tag each output item as [SUPPORTED] or [UNSUPPORTED]
def validate_agent_output(agent_output: dict, confirmed_entities: list) -> dict:
    """
    Scan agent output and label each step/finding as [SUPPORTED] or [UNSUPPORTED]
    based on whether it references a confirmed entity from the source documents.
    """
    if not agent_output or not isinstance(agent_output, dict):
        return agent_output
    confirmed_lower = [str(e).lower() for e in (confirmed_entities or []) if e]

    def _grounded(text: str) -> bool:
        t = text.lower()
        return any(ent in t for ent in confirmed_lower) if confirmed_lower else False

    # next_steps (NextStepAgent)
    for step in agent_output.get("next_steps", []):
        if isinstance(step, dict):
            probe = step.get("action", "") + " " + step.get("data_gap_filled", "")
            if _grounded(probe):
                step["grounded"] = True
                step["label"]    = "[SUPPORTED]"
            else:
                step["grounded"] = False
                step["label"]    = "[UNSUPPORTED]"
                step.setdefault(
                    "warning",
                    "Action does not reference a confirmed entity from source documents",
                )

    # patterns_found (PatternAgent)
    for pat in agent_output.get("patterns_found", []):
        if isinstance(pat, dict):
            probe = pat.get("description", "") + " " + " ".join(
                str(e) for e in pat.get("entities_involved", [])
            )
            if _grounded(probe):
                pat["grounded"] = True
                pat["label"]    = "[SUPPORTED]"
            else:
                pat["grounded"] = False
                pat["label"]    = "[UNSUPPORTED]"
                pat.setdefault("warning", "Pattern does not reference a confirmed entity")

    # actions (TacticalPlanAgent)
    for action in agent_output.get("actions", []):
        if isinstance(action, dict):
            probe = action.get("description", "") + " " + action.get("title", "")
            if _grounded(probe):
                action["grounded"] = True
                action["label"]    = "[SUPPORTED]"
            else:
                action["grounded"] = False
                action["label"]    = "[UNSUPPORTED]"
                action.setdefault("warning", "Action does not reference a confirmed entity")

    return agent_output


# ══════════════════════════════════════════════════════════════════════════════
# AGENT 1 — RISK ASSESSMENT (Hybrid v2)
# Deterministic base_score + keyword weights → LLM only for explanation text.
# Hard-cap applied when data is sparse so we never overstate confidence.
# ══════════════════════════════════════════════════════════════════════════════

# Severity-weighted keywords spanning all Indian crime types
_RISK_HIGH_SEVERITY: list = [
    # Financial / PMLA / narcotics
    ("PMLA",                  18), ("NDPS",              18), ("NCB",             15),
    ("HAWALA",                15), ("CASH DEPOSIT",      12), ("FEMA",            15),
    ("ED ",                   12), ("MONEY LAUNDER",     18),
    # Cyber / IT Act
    ("IT ACT",                20), ("INFORMATION TECHNOLOGY", 18),
    ("SECTION 43",            18), ("SECTION 66",        18), ("SECTION 69",      18),
    ("VIOLATION FLAGGED",     20), ("DPDP",              15),
    ("BREACH SUSPECTED",      15), ("DATA PROTECTION",   12),
    # CERT-In / government inquiry
    ("CERT-IN",               20), ("CERTIN",            20),
    ("COMPUTER EMERGENCY",    18), ("INQUIRY CONFIRMED", 20),
    ("CYBER CELL",            15), ("FORMAL INQUIRY",    15),
    # Evidence destruction
    ("EVIDENCE DELETION",     18), ("DELETION CONFIRMED", 18),
    ("DELETION",              18), ("DELETED",           18),
    ("REPO_DELETE",           18), ("POST_DELETE",       18),
    ("MODEL_DELETE",          18), ("EVIDENCE",          12),
    # Malicious / unauthorised
    ("MALICIOUS DEPLOYMENT",  15), ("MALICIOUS",         15),
    ("EXPLOIT",               15), ("UNAUTHORISED",      12),
    ("UNAUTHORIZED",          12),
    # Legal status
    ("LOOKOUT",               15), ("CHARGESHEET",       15), ("ARREST",          12),
    ("SEIZED",                12), ("DEPLOYED",          15), ("DEPLOYMENT",      15),
]

_RISK_MEDIUM_SEVERITY: list = [
    ("TELEGRAM",  8), ("ENCRYPTED",   8), ("PROTONMAIL", 8),
    ("SIGNAL",    8), ("BURNER",     10), ("DUBAI",       8),
    ("UAE",       8), ("+971",        8), ("SCRAPING",    8),
    ("VPN",       6), ("NIGHT",       5), ("2AM",         6), ("1AM", 6),
]

_MITIGATION_BY_LEVEL = {
    "LOW":      "Continue routine monitoring. No immediate action required.",
    "MEDIUM":   "Increase monitoring frequency. Cross-reference all sources.",
    "HIGH":     "Escalate for senior analyst. Investigate all connections.",
    "CRITICAL": "Immediate escalation required. Multiple confirmed violations. Preserve evidence urgently.",
}


def run_risk_agent(
    person:    dict,
    anomalies  = None,
    graph      = None,
    user_id:   str = "system",
) -> dict:
    """
    Hybrid risk assessment — deterministic base score + LLM for explanation text.

    Backward-compatible:
      run_risk_agent(entity_data)                   — single dict, auto-extracts anomalies
      run_risk_agent(entity_data, user_id_string)   — old positional call
      run_risk_agent(person, anomalies, graph, uid) — new explicit call
    """
    # ── Backward-compat: (entity_data, user_id_string) old calling convention ──
    if isinstance(anomalies, str):
        user_id   = anomalies
        anomalies = None

    p = person or {}

    # Build anomalies list when caller didn't supply one
    if not isinstance(anomalies, list):
        anomalies = []
        for f in p.get("anomaly_flags", []) or []:
            anomalies.append(f.get("flag", str(f)) if isinstance(f, dict) else str(f))
        for c in p.get("conflicts", []) or []:
            anomalies.append(c.get("flag", str(c)) if isinstance(c, dict) else str(c))
        for bf in p.get("behavioral_flags", []) or []:
            anomalies.append(str(bf))

    # Normalise anomaly strings — extract text from dict wrappers
    flags = []
    for a in anomalies:
        if isinstance(a, dict):
            flags.append(str(a.get("flag") or a.get("detail") or a.get("type") or a))
        else:
            flags.append(str(a))
    flags = [f for f in flags if f.strip()]

    # ── STEP 1: DETERMINISTIC BASE SCORE ──────────────────────────────────────
    sources      = len(p.get("data_sources", []) or [])
    # n_anomalies: prefer explicit "anomalies" key (set by orchestrator write-back),
    # then anomaly_flags count, then the normalised flags list from the caller.
    n_anomalies  = len(p.get("anomalies", []) or p.get("anomaly_flags", []) or flags)
    has_assets   = bool(p.get("assets_data"))
    entity_count = len(str(p))

    # Calibrated scoring — keeps heavy cases (7 src / 8 flags) in 72–85 band.
    # sources*5 + anomalies*6 → 7*5 + 8*6 = 83 (8 flags); 101 → 85 (11 flags).
    # Hard ceiling of 85 prevents 100/100 inflation on every multi-flag case.
    base_score = (sources * 5) + (n_anomalies * 6) + (18 if has_assets else 0)
    if entity_count < 5000:
        base_score -= 12
    if sources <= 4:
        base_score -= 10

    # FINAL HARD CAP — ceiling 85, floor 0 (only the most extreme cases hit 85)
    base_score = max(0, min(85, base_score))

    keyword_factors: list = []

    # Deterministic level (always derived from score — LLM cannot override)
    if base_score >= 75:   level = "CRITICAL"
    elif base_score >= 55: level = "HIGH"
    elif base_score >= 35: level = "MEDIUM"
    else:                  level = "LOW"

    # Deterministic confidence from source richness
    confidence = min(85, 30 + (sources * 12))

    # ── STEP 2: LLM FOR EXPLANATION AND KEY FACTORS ────────────────────────────
    evidence_lines = []
    if flags:
        evidence_lines.append("CONFIRMED FLAGS:\n" + "\n".join(f"- {f[:120]}" for f in flags[:15]))
    if p.get("confirmed_name"):
        evidence_lines.append(f"SUBJECT: {p['confirmed_name']}")
    phones = p.get("phones_found", [])
    if phones:
        evidence_lines.append(f"PHONES: {', '.join(str(x) for x in phones[:5])}")
    locs = p.get("location_stated", [])
    if locs:
        evidence_lines.append(f"LOCATIONS: {', '.join(str(x) for x in locs[:4])}")
    evidence_text = "\n".join(evidence_lines) or "No specific evidence provided."

    llm_prompt = f"""You are a conservative Indian law enforcement risk analyst.

The deterministic risk score for this subject is already fixed: {base_score}/100 (Level: {level}).
Do NOT change the score. Your job is only to explain WHY this score is justified.

Evidence:
{evidence_text}

Return ONLY valid JSON (no markdown, no code blocks):
{{
  "key_factors": ["3-4 specific factors that drove this score"],
  "explanation": "one-sentence explanation referencing the actual evidence",
  "recommendation": "one-sentence next investigative step",
  "mitigation_notes": "brief mitigation or escalation guidance"
}}"""

    llm_result: dict = {}
    try:
        raw = _call_ai(llm_prompt, max_tokens=600)
        if raw:
            clean = re.sub(r"```(?:json)?|```", "", raw).strip()
            m = re.search(r"\{.*\}", clean, re.DOTALL)
            if m:
                llm_result = json.loads(m.group())
    except Exception as e:
        print(f"[RISK] LLM explanation failed: {e}")

    # ── STEP 3: MERGE — deterministic values always win ────────────────────────
    key_factors = llm_result.get("key_factors") or [
        f.get("factor", "") for f in keyword_factors[:4]
    ] or [f"Anomalies detected: {n_anomalies}", f"Data sources: {sources}"]

    risk_factors = (
        [{"factor": kf, "weight": 10, "evidence": kf, "source": "hybrid"} for kf in key_factors[:5]]
        if key_factors else keyword_factors[:5]
    )

    result = {
        "risk_score":       base_score,
        "risk_level":       level,
        "confidence":       confidence,
        "explanation":      (
            llm_result.get("explanation")
            or f"Risk Score {base_score}/100 ({level}) — hybrid scoring from {n_anomalies} flags."
        ),
        "recommendation":   llm_result.get("recommendation", ""),
        "mitigation_notes": (
            llm_result.get("mitigation_notes")
            or _MITIGATION_BY_LEVEL[level]
        ),
        "key_factors":      key_factors,
        "risk_factors":     risk_factors,
        "agent":            "RiskAgent v2 — Hybrid",
        "generated_by":     "RiskAgent v2 — Hybrid",
        "method":           f"hybrid ({LAST_ENGINE_USED})",
        "timestamp":        datetime.datetime.utcnow().isoformat(),
        "generated_at":     datetime.datetime.utcnow().isoformat(),
    }

    _log_agent_run(
        "RiskAgent",
        f"score={base_score} level={level} flags={n_anomalies} sources={sources}",
        user_id,
    )
    return result


# ══════════════════════════════════════════════════════════════════════════════
# AGENT 2 — PATTERN DETECTION
# ══════════════════════════════════════════════════════════════════════════════

_PATTERN_PROMPT = (
    "You are PatternAgent, a pattern detection AI for an OSINT intelligence platform.\n"
    "Analyze relationships between all entities in the dataset.\n"
    "Find non-obvious connections. Identify behavioral patterns. Flag anomalies.\n"
    "Every finding MUST cite exact evidence from the data.\n\n"
    "Return ONLY this JSON (no markdown, no commentary):\n"
    '{{\n  "patterns_found": [\n'
    '    {{"pattern_type": "", "entities_involved": [], "description": "", "evidence": [], "significance": "LOW", "confidence": 0}}\n'
    '  ],\n  "hidden_connections": [],\n  "anomalies": [],\n'
    '  "agent": "PatternAgent",\n  "generated_at": ""\n}}\n\n'
    "Ontology data:\n{ontology_json}"
)


def run_pattern_agent(ontology_data: dict, user_id: str = "system") -> dict:
    ont_json = json.dumps(ontology_data, indent=2, ensure_ascii=False)
    if len(ont_json) > 10000:
        ont_json = ont_json[:10000] + "... [truncated]"
    raw    = _call_ai(_PATTERN_PROMPT.format(ontology_json=ont_json))
    result = _extract_json(raw) if raw else None

    if result and "patterns_found" in result:
        result.setdefault("agent", "PatternAgent")
        result.setdefault("generated_at", datetime.datetime.utcnow().isoformat())
        result["method"] = "ai"
    else:
        entities  = ontology_data.get("entities", {})
        patterns  = []
        persons   = [e for e in entities.values() if e.get("entity_type") == "PERSON"]
        events    = [e for e in entities.values() if e.get("entity_type") == "EVENT"]
        locations = [e for e in entities.values() if e.get("entity_type") == "LOCATION"]
        if len(persons) > 1:
            patterns.append({
                "pattern_type": "MULTI_IDENTITY",
                "entities_involved": [p.get("id", "") for p in persons[:5]],
                "description": f"{len(persons)} person entities — possible cross-platform identity",
                "evidence": ["Multiple PersonEntity nodes"], "significance": "MEDIUM", "confidence": 75,
            })
        if events:
            patterns.append({
                "pattern_type": "ACTIVITY_CLUSTER",
                "entities_involved": [e.get("id", "") for e in events[:5]],
                "description": f"{len(events)} activity events recorded",
                "evidence": ["EventEntity nodes present"], "significance": "LOW", "confidence": 65,
            })
        if locations:
            patterns.append({
                "pattern_type": "LOCATION_PATTERN",
                "entities_involved": [l.get("id", "") for l in locations[:5]],
                "description": f"{len(locations)} location(s) linked to subject",
                "evidence": ["LocationEntity nodes present"], "significance": "LOW", "confidence": 60,
            })
        result = {
            "patterns_found": patterns, "hidden_connections": [], "anomalies": [],
            "agent": "PatternAgent", "generated_at": datetime.datetime.utcnow().isoformat(),
            "method": "local",
        }

    entities  = ontology_data.get("entities", {})
    confirmed = [
        v.get("name", k) for k, v in entities.items()
        if isinstance(v, dict) and v.get("name")
    ]
    result = validate_agent_output(result, confirmed)
    result["engine"] = LAST_ENGINE_USED
    _log_agent_run("PatternAgent", f"patterns={len(result.get('patterns_found', []))}", user_id)
    return result


# ══════════════════════════════════════════════════════════════════════════════
# AGENT 3 — INVESTIGATIVE NEXT STEPS
# ══════════════════════════════════════════════════════════════════════════════

_NEXT_STEP_PROMPT = (
    "You are NextStepAgent, an investigative guidance AI.\n"
    "Based on the intelligence report, suggest the next 5 lawful investigative steps.\n"
    "Each step must be achievable through LEGAL means only.\n"
    "Do NOT suggest illegal, coercive, or privacy-invasive methods.\n\n"
    "Return ONLY this JSON (no markdown, no commentary):\n"
    '{{\n  "next_steps": [\n'
    '    {{"step_number": 1, "action": "", "legal_basis": "", "authorization_required": "", "data_gap_filled": "", "estimated_value": "HIGH", "priority": 1}}\n'
    '  ],\n  "agent": "NextStepAgent",\n  "generated_at": ""\n}}\n\n'
    "Report:\n{report_json}"
)


def run_next_step_agent(report: dict, user_id: str = "system") -> dict:
    """
    Generate 5 case-specific investigative next steps using Claude AI.
    Steps are tailored to the actual crime type, anomalies, and legal context.
    Falls back to rule-based steps if AI is unavailable.
    """
    person = report.get("person", {}) if isinstance(report, dict) else {}

    anomalies: list = list(
        report.get("anomalies")
        or report.get("sections", {}).get("anomalies_and_flags", {}).get("flags", [])
        or []
    )
    for c in person.get("conflicts", []):
        anomalies.append(c.get("flag", str(c)) if isinstance(c, dict) else str(c))
    for f in person.get("anomaly_flags", []):
        anomalies.append(f.get("flag", str(f)) if isinstance(f, dict) else str(f))
    for f in person.get("behavioral_flags", []):
        anomalies.append(str(f))

    person_name = person.get("confirmed_name", "Unknown Subject")
    locations   = ", ".join(person.get("location_stated", []) or [])

    flag_list = [
        str(f).strip()
        for f in (anomalies or [])
        if str(f).strip() and len(str(f).strip()) > 10
    ]
    flag_text = "\n".join(f"- {f[:120]}" for f in flag_list[:12]) or "No specific flags"

    has_financial = any(
        any(x in str(f).upper() for x in
            ["FEMA", "USD", "INTERNATIONAL", "DEBIT", "OPENAI", "ANTHROPIC"])
        for f in flag_list
    )
    has_certin   = any("CERT" in str(f).upper() for f in flag_list)
    has_deletion = any(
        any(x in str(f).upper() for x in ["DELETION", "DELETED", "REPO_DELETE"])
        for f in flag_list
    )

    print(f"[NEXTSTEP] flags={len(flag_list)} financial={has_financial} certin={has_certin}")

    extra = []
    if has_financial:
        extra.append(
            "International USD payments confirmed to foreign tech companies. "
            "FEMA 1999 applies. Include bank records subpoena."
        )
    if has_certin:
        extra.append(
            "Active CERT-In inquiry open. Reference case in steps. "
            "Include server log preservation."
        )
    if has_deletion:
        extra.append(
            "Evidence deletion confirmed. Device seizure urgent. "
            "Include forensic preservation."
        )
    extra_str = "\n".join(extra)

    prompt = (
        "You are a senior legal analyst."
        " Generate exactly 5 specific investigative next steps for this case.\n\n"
        f"Subject: {person_name}\n"
        f"Locations: {locations}\n\n"
        "CONFIRMED FLAGS:\n"
        f"{flag_text}\n\n"
        "IMPORTANT CONTEXT:\n"
        f"{extra_str}\n\n"
        "Rules:\n"
        "- Each step must address a specific flag above\n"
        "- Cite exact Indian law section\n"
        "- State required authorisation\n"
        "- If FEMA: include bank subpoena\n"
        "- If CERT-In: reference case\n"
        "- If deletion: device seizure\n\n"
        "Return JSON only:\n"
        '{"steps": [{'
        '"step_number": 1,'
        '"action": "...",'
        '"legal_basis": "...",'
        '"authorization": "...",'
        '"priority": "HIGH",'
        '"fills_gap": "..."'
        "}]}"
    )

    try:
        result_text = _call_ai(prompt, max_tokens=1500)
        clean = re.sub(r"```(?:json)?|```", "", result_text).strip()
        m = re.search(r"\{.*\}", clean, re.DOTALL)
        if m:
            parsed   = json.loads(m.group())
            ai_steps = parsed.get("steps", [])
            if ai_steps:
                normalised = []
                for idx, s in enumerate(ai_steps[:5], 1):
                    normalised.append({
                        "step":                   s.get("action", ""),
                        "action":                 s.get("action", ""),
                        "step_number":            s.get("step_number", idx),
                        "legal_basis":            s.get("legal_basis", ""),
                        "authorization":          s.get("authorization", ""),
                        "authorization_required": s.get("authorization", ""),
                        "priority":               s.get("step_number", idx),
                        "value":                  s.get("priority", "HIGH"),
                        "fills_gap":              s.get("fills_gap", ""),
                    })
                result = {
                    "next_steps":   normalised,
                    "generated_by": "NextStepAgent - AI",
                    "agent":        "NextStepAgent",
                    "generated_at": datetime.datetime.utcnow().isoformat(),
                    "method":       "ai-bedrock",
                }
                _log_agent_run("NextStepAgent",
                               f"steps={len(normalised)} method=ai-bedrock", user_id)
                return result
    except Exception as e:
        print(f"[NEXT_STEP] AI failed: {e} -> rule-based fallback")

    # ── Rule-based fallback ────────────────────────────────────────────────────
    steps      = []
    anom_upper = " ".join(str(a).upper() for a in anomalies)

    if "PMLA" in anom_upper or " ED " in anom_upper:
        steps.append({
            "step":        "Obtain PMLA case number and ED file status via CERSAI / ED portal",
            "legal_basis": "Prevention of Money Laundering Act 2002 — Section 5",
            "priority":    1, "value": "HIGH",
            "fills_gap":   "Financial crime case details",
        })
    if "+971" in anom_upper or "UAE" in anom_upper or "DUBAI" in anom_upper:
        steps.append({
            "step":        "Request international CDR and subscriber details for UAE numbers via MLAT",
            "legal_basis": "Mutual Legal Assistance Treaty (MLAT) with UAE",
            "priority":    2, "value": "HIGH",
            "fills_gap":   "Foreign contact verification",
        })
    if "TELEGRAM" in anom_upper or "ENCRYPTED" in anom_upper or "SIGNAL" in anom_upper:
        steps.append({
            "step":        "Obtain Telegram/Signal subscriber data and message logs via legal process server",
            "legal_basis": "IT Act 2000 — Section 69 + BNSS Section 94",
            "priority":    3, "value": "HIGH",
            "fills_gap":   "Encrypted communication evidence",
        })
    if ("IT ACT" in anom_upper or "DPDP" in anom_upper or "CERT" in anom_upper
            or "DELETION" in anom_upper or "SCRAPING" in anom_upper):
        steps.append({
            "step":        "File formal complaint with CERT-In and request preservation of server logs",
            "legal_basis": "IT Act 2000 — Section 43/66 + DPDP Act 2023",
            "priority":    4, "value": "HIGH",
            "fills_gap":   "Cyber offence documentation and log preservation",
        })
    if "LOCATION CONFLICT" in anom_upper or "MULTIPLE LOCATION" in anom_upper:
        steps.append({
            "step":        "Conduct tower-dump / IMEI location analysis to confirm current residence",
            "legal_basis": "TRAI regulations / BNSS Section 94",
            "priority":    5, "value": "MEDIUM",
            "fills_gap":   "Confirmed residential address",
        })
    if "NAME CONFLICT" in anom_upper or "ALIAS" in anom_upper:
        steps.append({
            "step":        "Cross-verify identity documents (Aadhaar / PAN / Passport) via UIDAI and Income Tax portal",
            "legal_basis": "IT Act 2000 / Aadhaar Act 2016",
            "priority":    len(steps) + 1, "value": "HIGH",
            "fills_gap":   "Identity verification across name variants",
        })

    if not steps:
        steps = [
            {
                "step":        "Submit formal request for device forensics and account preservation",
                "legal_basis": "BNSS Section 94 — search and seizure",
                "priority":    1, "value": "HIGH",
                "fills_gap":   "Digital evidence preservation",
            },
            {
                "step":        "Request platform subscriber data via nodal officer legal process",
                "legal_basis": "IT Act 2000 — Section 69B + IT Rules 2021",
                "priority":    2, "value": "MEDIUM",
                "fills_gap":   "Platform identity and activity confirmation",
            },
        ]

    _PAD_STEPS = [
        {
            "step":        "File formal complaint with CERT-In and request server log preservation",
            "legal_basis": "IT Act 2000 — Section 43/66 + DPDP Act 2023",
            "priority":    len(steps) + 1, "value": "HIGH",
            "fills_gap":   "Cyber offence documentation",
        },
        {
            "step":        "Obtain CDR (Call Detail Records) and tower-dump from telecom provider",
            "legal_basis": "TRAI regulations / BNSS Section 94 / IT Act Section 69",
            "priority":    len(steps) + 2, "value": "HIGH",
            "fills_gap":   "Communication pattern and movement trail",
        },
        {
            "step":        "Apply for Mutual Legal Assistance Treaty (MLAT) request for foreign server data",
            "legal_basis": "MLAT / IT Act 2000 — Section 69 / CrPC Section 166A",
            "priority":    len(steps) + 3, "value": "MEDIUM",
            "fills_gap":   "Cross-border digital evidence",
        },
        {
            "step":        "Conduct physical surveillance and field verification of stated address(es)",
            "legal_basis": "CrPC Section 41/41A / BNSS Section 35",
            "priority":    len(steps) + 4, "value": "MEDIUM",
            "fills_gap":   "Confirmed residential / operational location",
        },
        {
            "step":        "Preserve and forensically image all seized devices under chain-of-custody",
            "legal_basis": "IT Act 2000 — Section 65B / BNSS Section 94",
            "priority":    len(steps) + 5, "value": "HIGH",
            "fills_gap":   "Admissible digital forensic evidence",
        },
    ]
    pad_idx = 0
    while len(steps) < 5 and pad_idx < len(_PAD_STEPS):
        candidate    = _PAD_STEPS[pad_idx]
        pad_idx     += 1
        existing_txt = " ".join(s.get("step", "") for s in steps).upper()
        if not any(kw in existing_txt for kw in candidate["step"].upper().split()[:4]):
            steps.append(candidate)

    result = {
        "next_steps":   steps[:5],
        "generated_by": "NextStepAgent - Rule-Based",
        "agent":        "NextStepAgent",
        "generated_at": datetime.datetime.utcnow().isoformat(),
        "method":       "rule-based",
    }
    _log_agent_run("NextStepAgent",
                   f"steps={len(result['next_steps'])} method=rule-based", user_id)
    return result


# ══════════════════════════════════════════════════════════════════════════════
# AGENT 4 — COMPLIANCE
# ══════════════════════════════════════════════════════════════════════════════

_COMPLIANCE_PROMPT = (
    "You are ComplianceAgent, a legal compliance AI for an intelligence platform.\n"
    "Review this report for DPDP Act 2023 and IT Act 2000 compliance.\n"
    "Check: data minimization, purpose limitation, lawful basis, privacy risks.\n\n"
    "Return ONLY this JSON (no markdown, no commentary):\n"
    '{{\n  "compliant": true,\n  "compliance_score": 85,\n'
    '  "flags": [{{"concern": "", "section": "", "recommendation": ""}}],\n'
    '  "cleared_for_export": true,\n  "agent": "ComplianceAgent",\n  "generated_at": ""\n}}\n\n'
    "Report:\n{report_json}"
)


def run_compliance_agent(report_data: dict, user_id: str = "system") -> dict:
    report_json = json.dumps(report_data, indent=2, ensure_ascii=False)
    if len(report_json) > 8000:
        report_json = report_json[:8000] + "... [truncated]"
    raw    = _call_ai(_COMPLIANCE_PROMPT.format(report_json=report_json))
    result = _extract_json(raw) if raw else None

    if result and "compliance_score" in result:
        result.setdefault("agent", "ComplianceAgent")
        result.setdefault("generated_at", datetime.datetime.utcnow().isoformat())
        result["method"]           = "ai"
        result["compliance_score"] = max(0, min(100, int(result.get("compliance_score", 0))))
    else:
        flags  = []
        person = report_data.get("person", {}) if isinstance(report_data, dict) else {}
        if isinstance(person, dict):
            if person.get("phones_found") or person.get("emails_found"):
                flags.append({
                    "concern": "Personal contact data in report",
                    "section": "DPDP Act 2023 §4 — Data Minimization",
                    "recommendation": "Verify lawful basis for retaining contact details",
                })
            if len(person.get("location_stated", [])) > 3:
                flags.append({
                    "concern": "Multiple location data points may exceed necessity",
                    "section": "DPDP Act 2023 §5 — Purpose Limitation",
                    "recommendation": "Retain only locations necessary for stated purpose",
                })
        score  = max(0, 100 - len(flags) * 15)
        result = {
            "compliant":          len(flags) == 0,
            "compliance_score":   score,
            "flags":              flags,
            "cleared_for_export": len(flags) <= 1,
            "agent":              "ComplianceAgent",
            "generated_at":       datetime.datetime.utcnow().isoformat(),
            "method":             "local",
        }

    _log_agent_run("ComplianceAgent",
                   f"compliant={result.get('compliant')} score={result.get('compliance_score')}",
                   user_id)
    return result


# ══════════════════════════════════════════════════════════════════════════════
# AGENT 5 — TACTICAL OPERATION PLAN (Hybrid v2)
# Deterministic sequencing skeleton built first. LLM enhances wording only.
# Structural fields (id, agency, dependencies, time_window, priority) are
# never overridden by LLM output — they come from deterministic logic.
# ══════════════════════════════════════════════════════════════════════════════

_VALID_AGENCIES     = {"Cyber Cell", "CERT-In", "ED", "NCB", "Local Police", "Court", "SFIO"}
_VALID_TIME_WINDOWS = {"0-24 hours", "24-72 hours", "72hrs-7days", "7-30 days"}
_VALID_LEVELS       = {"CRITICAL", "HIGH", "MEDIUM", "LOW"}

# Keys that form the required action schema
_REQUIRED_ACTION_KEYS = {
    "id", "title", "description", "legal_basis", "authority_required",
    "agency", "time_window", "time_sensitivity", "priority",
    "depends_on", "blocks", "parallel_with",
    "risk_if_delayed", "risk_if_reversed", "reward",
}

# Structural keys owned by deterministic logic — LLM may not change these
_STRUCTURAL_KEYS = {
    "id", "agency", "time_window", "time_sensitivity", "priority",
    "depends_on", "blocks", "parallel_with",
    "risk_if_delayed", "risk_if_reversed",
}


def _tactical_plan_fallback(person_object: dict, anomalies: list, assets_data: list) -> dict:
    """
    Generalized 6-action Tactical Operation Plan that works for ANY case type.

    Detects case type dynamically from anomaly text.  Action 2 branches on:
      financial/FEMA/PMLA  → FREEZE BANK ACCOUNTS UNDER PMLA
      cyber/IT Act/comms   → APPLY FOR JUDICIAL INTERCEPTION ORDER
      else (general OSINT) → PRESERVE ALL PLATFORM & CLOUD DATA
    Returns a complete dict (case_summary, critical_warning, actions).
    """
    person_name  = person_object.get("confirmed_name", "Unknown Subject")
    anomaly_text = " ".join(str(a).lower() for a in (anomalies or []))

    has_financial = any(k in anomaly_text for k in [
        "fema", "pmla", "bank", "financial", "international transfer", "ed indicator",
        "hawala", "money launder", "cash deposit", "usd",
    ])
    has_cyber = any(k in anomaly_text for k in [
        "it act", "cyber", "platform", "digital evidence", "cert-in",
        "section 66", "section 43", "malicious", "deletion", "scraping",
        "unauthori", "dpdp",
    ])
    has_drug = any(k in anomaly_text for k in [
        "ndps", "drug", "narcotic", "psychotropic", "ncb",
    ])

    # ── Action 2: branches on detected case type ───────────────────────────────
    if has_financial:
        action2 = {
            "id": 2,
            "title": "FREEZE BANK ACCOUNTS UNDER PMLA",
            "priority": "CRITICAL",
            "time_window": "0-24 hours",
            "time_sensitivity": "CRITICAL",
            "description": (
                f"Apply for provisional attachment order under PMLA for all accounts linked to "
                f"{person_name}. File with ED simultaneously. Do not notify subject before freeze."
            ),
            "legal_basis": "PMLA 2002 — Section 5 (Provisional Attachment) / FEMA 1999 Section 37A",
            "authority_required": "ED Deputy Director / Designated PMLA Authority",
            "agency": "ED",
            "depends_on": [],
            "blocks": [5],
            "parallel_with": [1],
            "risk_if_delayed": "HIGH",
            "risk_if_reversed": "HIGH",
            "reward": "Prevents asset dissipation; freezes proceeds of crime before subject is aware",
        }
    elif has_cyber:
        action2 = {
            "id": 2,
            "title": "APPLY FOR JUDICIAL INTERCEPTION ORDER",
            "priority": "CRITICAL",
            "time_window": "0-24 hours",
            "time_sensitivity": "CRITICAL",
            "description": (
                f"Apply for lawful interception and monitoring order for all confirmed "
                f"communication platforms and phone numbers linked to {person_name}."
            ),
            "legal_basis": "IT Act 2000 — Section 69 / Telegraph Act 1885 — Section 5(2)",
            "authority_required": "Home Secretary (State) / Secretary MHA (Central)",
            "agency": "Cyber Cell",
            "depends_on": [],
            "blocks": [],
            "parallel_with": [1],
            "risk_if_delayed": "MEDIUM",
            "risk_if_reversed": "LOW",
            "reward": "Enables real-time intelligence collection on ongoing communications",
        }
    else:
        action2 = {
            "id": 2,
            "title": "PRESERVE ALL PLATFORM & CLOUD DATA",
            "priority": "HIGH",
            "time_window": "0-24 hours",
            "time_sensitivity": "HIGH",
            "description": (
                f"Issue preservation notices to all platforms and cloud providers where "
                f"{person_name} has confirmed presence. Secure server logs, account metadata, "
                "and storage. Do not notify subject."
            ),
            "legal_basis": "IT Act 2000 — Section 69 / BNSS Section 94",
            "authority_required": "SP (Cyber) or designated nodal officer",
            "agency": "Cyber Cell",
            "depends_on": [],
            "blocks": [],
            "parallel_with": [1],
            "risk_if_delayed": "HIGH",
            "risk_if_reversed": "HIGH",
            "reward": "Secures digital evidence before subject can delete or alter it",
        }

    actions = [
        # ── Action 1 — Digital preservation: always first, always CRITICAL ─────
        {
            "id": 1,
            "title": "PRESERVE DIGITAL EVIDENCE IMMEDIATELY",
            "priority": "CRITICAL",
            "time_window": "0-24 hours",
            "time_sensitivity": "CRITICAL",
            "description": (
                f"Issue preservation notice to all platforms where {person_name} has confirmed "
                "presence. Preserve server logs, account metadata, transaction records, and "
                "cloud storage. Do not notify subject."
            ),
            "legal_basis": (
                "IT Act 2000 — Section 69 / BNSS Section 94 / "
                "IT (Procedure and Safeguards) Rules 2009"
            ),
            "authority_required": "Superintendent of Police (Cyber) or designated nodal officer",
            "agency": "Cyber Cell",
            "depends_on": [],
            "blocks": [],
            "parallel_with": [2],
            "risk_if_delayed": "HIGH",
            "risk_if_reversed": "HIGH",
            "reward": (
                "Prevents evidence deletion; secures platform metadata and activity logs "
                "before subject is alerted"
            ),
        },
        # ── Action 2 — Case-adaptive parallel action ───────────────────────────
        action2,
        # ── Action 3 — Search warrant (depends on 1+2 for financial; else 1) ──
        {
            "id": 3,
            "title": "OBTAIN SEARCH WARRANT FOR PHYSICAL PREMISES",
            "priority": "HIGH",
            "time_window": "24-72 hours",
            "time_sensitivity": "HIGH",
            "description": (
                f"Apply for search warrant covering all known addresses of {person_name}. "
                "Warrant to cover electronic devices, storage media, financial documents, "
                "and communications equipment."
            ),
            "legal_basis": "BNSS Section 94 / IT Act 2000 — Section 80 (warrant for search)",
            "authority_required": "Judicial Magistrate First Class (JMFC)",
            "agency": "Local Police",
            "depends_on": [1, 2] if has_financial else [1],
            "blocks": [4],
            "parallel_with": [],
            "risk_if_delayed": "MEDIUM",
            "risk_if_reversed": "MEDIUM",
            "reward": "Legal authorization for physical search; device and document seizure",
        },
        # ── Action 4 — Records subpoena ────────────────────────────────────────
        {
            "id": 4,
            "title": "SUBPOENA FINANCIAL AND TELECOM RECORDS",
            "priority": "HIGH",
            "time_window": "24-72 hours",
            "time_sensitivity": "HIGH",
            "description": (
                f"Issue production orders to telecom providers for CDR/tower dump for "
                f"{person_name}'s confirmed numbers. Issue simultaneous bank record subpoena "
                "for all linked accounts. Minimum 12-month lookback period."
            ),
            "legal_basis": (
                "BNSS Section 94 / TRAI Regulations / "
                "PMLA 2002 — Section 50 (summons for records)"
            ),
            "authority_required": "SP or above / ED Deputy Director",
            "agency": "Cyber Cell",
            "depends_on": [1],
            "blocks": [6],
            "parallel_with": [3],
            "risk_if_delayed": "MEDIUM",
            "risk_if_reversed": "LOW",
            "reward": "Call pattern evidence; financial transaction trail; movement history",
        },
        # ── Action 5 — Subject notice: only after digital + financial secured ─
        {
            "id": 5,
            "title": "ISSUE FORMAL NOTICE TO SUBJECT",
            "priority": "HIGH",
            "time_window": "72hrs-7days",
            "time_sensitivity": "MEDIUM",
            "description": (
                f"After digital evidence is preserved and accounts frozen (or interception "
                f"active), issue formal notice to {person_name} under BNSS. Do NOT contact "
                "subject before Actions 1-3 are complete."
            ),
            "legal_basis": (
                "BNSS Section 35 / Section 179 (examination of person) / "
                "IT Act 2000 — Section 67C"
            ),
            "authority_required": "SP / Additional SP",
            "agency": "Local Police",
            "depends_on": [1, 2, 3],
            "blocks": [],
            "parallel_with": [4],
            "risk_if_delayed": "LOW",
            "risk_if_reversed": "MEDIUM",
            "reward": "Subject's recorded statement; opportunity to identify co-conspirators",
        },
        # ── Action 6 — Chargesheet: all prior actions must complete first ──────
        {
            "id": 6,
            "title": "FILE CHARGESHEET AND PROSECUTION REPORT",
            "priority": "MEDIUM",
            "time_window": "7-30 days",
            "time_sensitivity": "MEDIUM",
            "description": (
                "Compile all collected evidence into formal chargesheet. File prosecution "
                "complaint with designated court. Include Section 65B IT Act certificates "
                "for all digital evidence. Brief public prosecutor."
            ),
            "legal_basis": (
                "BNSS Section 193 (chargesheet) / "
                "IT Act 2000 — Section 65B / CrPC Section 173"
            ),
            "authority_required": "SP / DCP (minimum) to authorize chargesheet",
            "agency": "Court",
            "depends_on": [1, 2, 3, 4, 5],
            "blocks": [],
            "parallel_with": [],
            "risk_if_delayed": "MEDIUM",
            "risk_if_reversed": "LOW",
            "reward": (
                "Formal prosecution; prevents subject from claiming lack of notice; "
                "initiates judicial process"
            ),
        },
    ]

    case_type = (
        "Financial Crime" if has_financial
        else ("Cyber Crime" if has_cyber
              else ("Drug/Narcotics" if has_drug
                    else "General OSINT"))
    )
    return {
        "case_summary": (
            f"Tactical operation plan for {person_name} — "
            f"{len(anomalies or [])} confirmed evidence flag(s) across {case_type} case type."
        ),
        "critical_warning": (
            "Digital preservation must precede all physical actions. "
            "Evidence loss is irreversible."
        ),
        "actions": actions,
        "method": "rule-based-fallback (generalized for all case types)",
    }


def run_tactical_plan_agent(
    person_object: dict,
    assets_data:   list,
    report_data:   dict,
    user_id:       str = "system",
) -> dict:
    """
    Hybrid Tactical Operation Plan generator — generalized for ANY case type.

    Pulls anomalies from report_data (keys: "anomalies" or "anomaly_flags").
    Step 1: Try LLM for a full 6-action plan.
    Step 2: If LLM fails or returns != 6 actions, fall back to the deterministic
            _tactical_plan_fallback which auto-detects case type from anomaly text.
    """
    anomalies = report_data.get("anomalies", []) or report_data.get("anomaly_flags", [])
    grounding = build_grounding_context(person_object)

    prompt = f"""You are a senior Indian law enforcement tactical planner.
Subject: {person_object.get('confirmed_name')}
Anomalies/Flags detected: {len(anomalies)}
Key indicators: {', '.join(str(a)[:80] for a in anomalies[:6]) if anomalies else 'General OSINT'}

Create a professional 6-action Tactical Operation Plan with strict Indian legal sequencing.
Return ONLY valid JSON."""

    raw    = _call_ai(prompt, 2200)
    result = _extract_json(raw) or {}

    if not result.get("actions") or len(result.get("actions", [])) != 6:
        result = _tactical_plan_fallback(person_object, anomalies, assets_data)

    result["method"]       = f"hybrid ({LAST_ENGINE_USED})" if raw else "rule-based-fallback"
    result["agent"]        = "TacticalPlanAgent"
    result["generated_at"] = datetime.datetime.utcnow().isoformat()

    _log_agent_run(
        "TacticalPlanAgent",
        f"actions=6 method={result['method']}",
        user_id or "system",
    )
    return result


# ══════════════════════════════════════════════════════════════════════════════
# AGENT 6 — TIMELINE ANALYSIS
# ══════════════════════════════════════════════════════════════════════════════

def run_timeline_analysis_agent(
    timeline_events: list,
    contradictions:  list,
    gaps:            list,
    person_name:     str,
) -> dict:
    """
    Generates a narrative analysis of the timeline — pattern, critical moments,
    investigator focus.
    """
    if not timeline_events:
        return {
            "narrative":          "",
            "critical_moments":   [],
            "pattern_summary":    "",
            "investigator_focus": "",
            "agent":              "TimelineAgent",
        }

    event_summary = "\n".join([
        f"{e.get('date', e.get('normalized',''))} "
        f"{e.get('time','')}: "
        f"{e.get('description', e.get('context',''))[:80]} "
        f"[{e.get('source','')[:30]}]"
        for e in timeline_events[:30]
    ])
    contradiction_summary = "\n".join([
        f"CONTRADICTION: {c.get('conflict','')} at {c.get('timestamp','')}"
        for c in (contradictions or [])
    ]) or "None detected"
    gap_summary = "\n".join([
        f"GAP: {g.get('gap_days',0)} days "
        f"({g.get('gap_start','')} to {g.get('gap_end','')})"
        for g in (gaps or [])
    ]) or "No significant gaps"

    prompt = f"""You are a senior intelligence analyst reviewing a timeline for subject: {person_name}

Timeline events (chronological):
{event_summary}

Contradictions detected:
{contradiction_summary}

Timeline gaps:
{gap_summary}

Provide a concise analytical narrative covering:
1. What the timeline reveals about the subject's pattern of activity
2. The most critical moments in the timeline
3. Whether the timeline supports or undermines any narrative
4. What an investigator should focus on first

Return JSON only. No markdown.
{{
  "narrative": "2-3 sentence summary",
  "critical_moments": ["moment 1 description", "moment 2 description"],
  "pattern_summary": "1 sentence",
  "investigator_focus": "what to prioritize"
}}"""

    try:
        raw   = _call_ai(prompt, max_tokens=800)
        clean = re.sub(r"```(?:json)?|```", "", raw).strip()
        m     = re.search(r"\{.*\}", clean, re.DOTALL)
        if m:
            parsed = json.loads(m.group())
            parsed["agent"]  = "TimelineAgent"
            parsed["engine"] = LAST_ENGINE_USED
            return parsed
    except Exception as e:
        print(f"[TIMELINE AGENT] Failed: {e}")

    return {
        "narrative":          "Timeline analysis unavailable.",
        "critical_moments":   [],
        "pattern_summary":    "",
        "investigator_focus": "",
        "agent":              "TimelineAgent",
        "engine":             "local-fallback",
    }


# ══════════════════════════════════════════════════════════════════════════════
# AGENT ORCHESTRATOR
# ══════════════════════════════════════════════════════════════════════════════

class AgentOrchestrator:
    _DISPATCH = {
        "RiskAgent":         run_risk_agent,
        "PatternAgent":      run_pattern_agent,
        "NextStepAgent":     run_next_step_agent,
        "ComplianceAgent":   run_compliance_agent,
        "TacticalPlanAgent": run_tactical_plan_agent,
    }

    def run_agent(self, agent_name: str, data: dict, user_id: str = "system") -> dict:
        fn = self._DISPATCH.get(agent_name)
        if not fn:
            return {"error": f"Unknown agent: {agent_name}", "agent": agent_name}
        return fn(data, user_id)

    def run_all_agents(
        self,
        ontology:      dict,
        report:        dict,
        mode:          str  = "OSINT",
        user_id:       str  = "system",
        assets_data:   list = None,
        raw_documents: list = None,
    ) -> dict:
        person_data = {}
        if isinstance(report, dict):
            person_data = dict(report.get("person", {}) or {})

        # ── PRE-AGENT: unify anomalies from ALL sources before any agent runs ──
        # Sources:
        #   1. person_data.anomaly_flags  (document-level flags from ingest)
        #   2. report["anomalies"]        (rule_anomalies injected by caller)
        #   3. person_data.conflicts / behavioral_flags
        _seen_flags: set = set()
        unified_anomalies: list = []

        def _add_flag(raw):
            text = raw.get("flag", str(raw)) if isinstance(raw, dict) else str(raw)
            text = text.strip()
            if text and text.lower() not in _seen_flags:
                _seen_flags.add(text.lower())
                unified_anomalies.append(text)

        for f in person_data.get("anomaly_flags",   []) or []: _add_flag(f)
        for f in report.get("anomalies",            []) or []: _add_flag(f)
        for f in report.get("anomaly_flags",         []) or []: _add_flag(f)
        for c in person_data.get("conflicts",        []) or []: _add_flag(c)
        for b in person_data.get("behavioral_flags", []) or []: _add_flag(b)

        # Inject unified list back into person_data so risk/next-step agents see it
        if unified_anomalies:
            _unified_flag_dicts = [
                {"flag": a, "source": "pipeline-unified", "severity": "MEDIUM"}
                for a in unified_anomalies
            ]
            person_data["anomaly_flags"] = _unified_flag_dicts
            person_data["anomalies"]     = unified_anomalies

            # Write-back: mutate the ORIGINAL person dict inside report so that
            # subsequent generate_report() calls (with the same person object) also
            # see the enriched anomaly data without re-running the orchestrator.
            _orig_person = report.get("person")
            if isinstance(_orig_person, dict):
                _orig_person["anomaly_flags"] = _unified_flag_dicts
                _orig_person["anomalies"]     = unified_anomalies

        # Rebuild report with enriched person_data and explicit anomalies key
        enriched_report = {**report, "person": person_data, "anomalies": unified_anomalies}

        print(f"[ORCHESTRATOR] unified_anomalies={len(unified_anomalies)} "
              f"sources={len(person_data.get('data_sources', []))}")

        agents_run = ["RiskAgent", "PatternAgent", "NextStepAgent", "ComplianceAgent"]

        results = {
            "risk":       run_risk_agent(person_data, user_id=user_id),
            "patterns":   run_pattern_agent(ontology,  user_id),
            "next_steps": run_next_step_agent(enriched_report, user_id),
            "compliance": run_compliance_agent(enriched_report, user_id),
        }

        # TacticalPlanAgent — always runs on every case (no assets gate)
        try:
            results["tactical_plan"] = run_tactical_plan_agent(
                person_data,
                assets_data or [],
                {"anomalies": unified_anomalies, "person": person_data},
                user_id,
            )
            agents_run.append("TacticalPlanAgent")
        except Exception as exc:
            results["tactical_plan"] = {
                "error":        str(exc),
                "agent":        "TacticalPlanAgent",
                "generated_at": datetime.datetime.utcnow().isoformat(),
            }

        # TimelineAgent — runs after contradiction and gap detection
        try:
            from modules.timeline import (
                detect_timeline_contradictions,
                detect_timeline_gaps,
            )
            timeline_events   = person_data.get("timeline_events", [])
            tl_contradictions = detect_timeline_contradictions(
                timeline_events, raw_documents or []
            )
            tl_gaps = detect_timeline_gaps(timeline_events)

            results["timeline_analysis"]      = run_timeline_analysis_agent(
                timeline_events,
                tl_contradictions,
                tl_gaps,
                person_data.get("confirmed_name", "Unknown"),
            )
            results["timeline_contradictions"] = tl_contradictions
            results["timeline_gaps"]           = tl_gaps
            agents_run.append("TimelineAgent")
        except Exception as exc:
            results["timeline_analysis"] = {
                "error":        str(exc),
                "agent":        "TimelineAgent",
                "generated_at": datetime.datetime.utcnow().isoformat(),
            }

        results["orchestrator"] = {
            "run_at":     datetime.datetime.utcnow().isoformat(),
            "mode":       mode,
            "agents_run": agents_run,
            "user_id":    user_id,
        }
        return results


orchestrator = AgentOrchestrator()
