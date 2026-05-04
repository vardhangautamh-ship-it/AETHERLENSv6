"""
AetherLens — AI Agent Orchestration Layer
Four intelligence agents (Gemini + Grok 4 fallback):
  RiskAgent       — comprehensive risk assessment
  PatternAgent    — hidden pattern & connection detection
  NextStepAgent   — lawful investigative guidance
  ComplianceAgent — DPDP / IT Act compliance check
AgentOrchestrator — runs all four, merges outputs.
"""

import json
import re
import datetime
import sqlite3
import uuid

import config

# ══════════════════════════════════════════════════════════════════════════════
# SHARED HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _call_bedrock(prompt: str, max_tokens: int = 4096) -> str:
    """
    Claude Opus 4 on AWS Bedrock (ap-south-1 / Mumbai).
    Primary engine — data stays in India for DPDP compliance.
    Returns raw text on success, empty string on failure.
    """
    # Lazy init: config.bedrock_client is None when config was imported
    # before Streamlit finished loading (st.secrets not yet available).
    # Re-call get_bedrock_client() now that the app is fully running.
    if getattr(config, "bedrock_client", None) is None:
        try:
            _client, _model = config.get_bedrock_client()
            if _client:
                config.bedrock_client   = _client
                config.BEDROCK_MODEL_ID = _model
        except Exception:
            pass
    if getattr(config, "bedrock_client", None) is None:
        return ""
    try:
        body = json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        })
        response = config.bedrock_client.invoke_model(
            modelId     = config.BEDROCK_MODEL_ID,
            body        = body,
            contentType = "application/json",
            accept      = "application/json",
        )
        result = json.loads(response["body"].read())
        return result["content"][0]["text"] or ""
    except Exception as e:
        try:
            print(f"[BEDROCK] Call failed: {e}")
        except Exception:
            pass
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
        d = resp.json()
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


def _call_grok(prompt: str, max_tokens: int = 4096) -> str:
    if not config.grok_client:
        return ""
    if not config.GROK_API_KEY or config.GROK_API_KEY in ("", "your_grok_key_here"):
        return ""
    try:
        response = config.grok_client.chat.completions.create(
            model=config.GROK_MODEL,
            max_tokens=max_tokens,
            temperature=config.GROK_TEMPERATURE,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.choices[0].message.content or ""
    except Exception as e:
        try:
            print(f"[GROK] call failed: {e}")
        except Exception:
            pass
        return ""


# Tracks which engine served the most recent _call_ai() request.
# Read by UI/report code that wants to display the source.
LAST_ENGINE_USED = "local-fallback"


def _call_ai(prompt: str, max_tokens: int = 4096) -> str:
    """
    Engine priority:
      1. Claude Opus 4 on AWS Bedrock (ap-south-1, India)    — primary
      2. Grok 4 (xAI)                                         — fallback
      3. Gemini 2.5 Flash (Google)                            — last resort
    """
    global LAST_ENGINE_USED

    raw = _call_bedrock(prompt, max_tokens)
    if raw:
        LAST_ENGINE_USED = "claude-sonnet-4-bedrock"
        try:
            print(f"[AGENTS] Engine: {LAST_ENGINE_USED}")
        except Exception:
            pass
        return raw

    raw = _call_grok(prompt, max_tokens)
    if raw:
        LAST_ENGINE_USED = "grok-4"
        try:
            print(f"[AGENTS] Engine: {LAST_ENGINE_USED}")
        except Exception:
            pass
        return raw

    raw = _call_gemini(prompt, max_tokens)
    if raw:
        LAST_ENGINE_USED = "gemini-fallback"
        try:
            print(f"[AGENTS] Engine: {LAST_ENGINE_USED}")
        except Exception:
            pass
        return raw

    LAST_ENGINE_USED = "local-fallback"
    return ""


def _extract_json(text: str) -> dict | None:
    text = re.sub(r"```(?:json)?", "", text).strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"\{.*\}", text, re.DOTALL)
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
        return [{"id": r[0], "agent": r[1], "result": r[2], "run_at": r[3], "user_id": r[4]} for r in rows]
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
        enames = [v.get("name", k) for k, v in list(entities.items())[:10] if isinstance(v, dict)]
        if enames:
            lines.append(f"GRAPH ENTITIES: {', '.join(enames)}")
        rels = ontology_data.get("relationships", [])
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

    # operational_phases (StrategyAgent)
    for phase in agent_output.get("operational_phases", []):
        if isinstance(phase, dict):
            probe = phase.get("objective", "") + " " + " ".join(phase.get("actions", []))
            if _grounded(probe):
                phase["grounded"] = True
                phase["label"]    = "[SUPPORTED]"
            else:
                phase["grounded"] = False
                phase["label"]    = "[UNSUPPORTED]"
                phase.setdefault("warning", "Phase does not reference a confirmed entity")

    return agent_output


# ══════════════════════════════════════════════════════════════════════════════
# AGENT 1 — RISK ASSESSMENT
# ══════════════════════════════════════════════════════════════════════════════

_RISK_PROMPT = (
    "You are RiskAgent, a risk assessment AI for an OSINT intelligence platform.\n"
    "Analyze ONLY the provided entity data. Calculate a risk score 0-100.\n"
    "Justify every point with exact evidence from the data.\n"
    "Never score a factor without evidence.\n"
    "Risk levels: 0-25=LOW, 26-50=MEDIUM, 51-75=HIGH, 76-100=CRITICAL\n\n"
    "Return ONLY this JSON (no markdown, no commentary):\n"
    '{{\n  "risk_score": 0,\n  "risk_level": "LOW",\n'
    '  "risk_factors": [{{"factor": "", "evidence": "", "weight": 0, "source": ""}}],\n'
    '  "mitigation_notes": "",\n  "confidence": 0,\n'
    '  "agent": "RiskAgent",\n  "generated_at": ""\n}}\n\n'
    "Entity data:\n{entity_json}"
)


def calculate_risk_score(person: dict, anomalies: list, graph=None) -> dict:
    """
    AI-powered risk scoring via Claude Sonnet 4 — Bedrock Mumbai.
    Works for ANY crime type (financial, cyber, narcotics, terrorism,
    IT Act, DPDP, fraud, etc.).
    Falls back to weighted evidence scoring if AI is unavailable.
    """
    import datetime, json, re

    p     = person or {}
    name  = p.get("confirmed_name", "Unknown")
    phones    = p.get("phones_found",         [])
    locations = p.get("location_stated",      [])
    platforms = p.get("platforms_confirmed",  [])
    flags     = [str(a) for a in (anomalies or []) if str(a).strip()]

    # ── BUILD EVIDENCE SUMMARY ──────────────────────────────────────────────
    evidence_lines = []
    if flags:
        evidence_lines.append(
            "CONFIRMED FLAGS:\n" + "\n".join(f"- {f}" for f in flags[:20])
        )
    if phones:
        evidence_lines.append(f"PHONES FOUND: {', '.join(phones[:10])}")
    if locations:
        evidence_lines.append(f"LOCATIONS: {', '.join(locations[:5])}")
    if platforms:
        evidence_lines.append(f"PLATFORMS: {', '.join(platforms[:5])}")
    for field in ["criminal_history", "legal_proceedings", "occupation",
                  "employer", "known_associates"]:
        val = p.get(field)
        if val:
            evidence_lines.append(f"{field.upper()}: {str(val)[:200]}")
    evidence_text = "\n".join(evidence_lines) or "No specific evidence flags."

    # ── AI SCORING PROMPT ───────────────────────────────────────────────────
    prompt = f"""You are a senior intelligence analyst scoring subject risk for an official intelligence report.

Subject: {name}

Evidence summary:
{evidence_text}

Score this subject's risk level based on the evidence above.
Consider ALL crime types — financial, cyber, narcotics, terrorism financing,
fraud, IT Act violations, DPDP breaches, or any other relevant offence.

Return ONLY valid JSON. No explanation outside JSON. No markdown. No code blocks.

Required format:
{{
  "risk_score": <integer 28-92>,
  "risk_level": "<LOW|MEDIUM|HIGH|CRITICAL>",
  "risk_factors": [
    {{
      "factor": "<what was found>",
      "weight": <integer>,
      "evidence": "<specific evidence>"
    }}
  ],
  "mitigation_notes": "<1-2 sentences>",
  "confidence": <integer 35-92>,
  "explanation": "<1 sentence summary>"
}}

Scoring guide:
28-44: LOW      — minor flags, routine monitoring
45-64: MEDIUM   — significant flags, increase watch
65-81: HIGH     — serious violations, escalate
82-92: CRITICAL — multiple confirmed violations, immediate action required

Risk factors must cite specific evidence from the summary above.
Minimum 3 factors if evidence exists.
Every factor must have a weight proportional to its severity."""

    # ── CALL AI (primary path) ──────────────────────────────────────────────
    try:
        result_text = _call_ai(prompt, max_tokens=1000)

        clean = re.sub(r"```(?:json)?|```", "", result_text).strip()
        m = re.search(r"\{.*\}", clean, re.DOTALL)
        if m:
            result = json.loads(m.group())

            score      = max(28, min(92, int(result.get("risk_score",  50))))
            confidence = max(35, min(92, int(result.get("confidence",  55))))
            factors    = result.get("risk_factors",    [])
            mitigation = result.get("mitigation_notes", "")
            explanation = result.get("explanation",    "")

            # Re-derive level from score so it always matches the number
            if score >= 82:   level = "CRITICAL"
            elif score >= 65: level = "HIGH"
            elif score >= 45: level = "MEDIUM"
            else:             level = "LOW"

            return {
                "risk_score":       score,
                "risk_level":       level,
                "risk_factors":     factors,
                "mitigation_notes": mitigation,
                "confidence":       confidence,
                "explanation":      explanation,
                "agent":            "RiskAgent",
                "method":           "ai-bedrock",
                "generated_at":     datetime.datetime.utcnow().isoformat(),
            }

    except Exception as e:
        print(f"[RISK] AI scoring failed: {e} -> weighted fallback")

    # ── WEIGHTED FALLBACK (AI offline) ──────────────────────────────────────
    # Generic severity-weighted keywords that span all crime types.
    score   = 30
    factors = []
    flag_text = " ".join(str(f).upper() for f in flags)

    HIGH_SEVERITY = [
        # Financial / narcotics
        ("PMLA",                  18), ("NDPS",             18), ("NCB",            15),
        ("HAWALA",                15), ("CASH DEPOSIT",     12),
        # Cyber / IT Act — multiple alias forms for robustness
        ("IT ACT",                20), ("INFORMATION TECHNOLOGY", 18),
        ("SECTION 43",            18), ("SECTION 66",       18), ("SECTION 69",     18),
        ("DPDP",                  15),
        # CERT-In / government inquiry — all alias forms
        ("CERT-IN",               20), ("CERT-In",          20), ("CERTIN",         20),
        ("CERT ",                 18), ("COMPUTER EMERGENCY", 18),
        ("CYBER CELL",            15), ("FORMAL INQUIRY",   15),
        # Evidence actions
        ("DELETION",              18), ("DELETED",          18), ("REPO_DELETE",    18),
        ("POST_DELETE",           18), ("MODEL_DELETE",     18), ("EVIDENCE",       12),
        # Legal status
        ("LOOKOUT",               15), ("CHARGESHEET",      15), ("ARREST",         12),
        ("SEIZED",                12), ("DEPLOYED",         15), ("DEPLOYMENT",     15),
    ]
    MEDIUM_SEVERITY = [
        ("TELEGRAM",      8),  ("ENCRYPTED",    8),  ("PROTONMAIL",    8),
        ("SIGNAL",        8),  ("BURNER",       10), ("DUBAI",          8),
        ("UAE",           8),  ("+971",          8),  ("SCRAPING",      8),
        ("UNAUTHORISED",  10), ("VPN",           6),  ("NIGHT",         5),
        ("2AM",           6),  ("1AM",           6),
    ]

    for keyword, weight in HIGH_SEVERITY + MEDIUM_SEVERITY:
        if keyword in flag_text:
            score += weight
            factors.append({
                "factor":   keyword.title() + " indicator",
                "weight":   weight,
                "evidence": f"{keyword} detected in anomaly flags",
                "source":   "anomaly_flags",
            })

    score = max(28, min(92, score))

    if score >= 82:   level = "CRITICAL"
    elif score >= 65: level = "HIGH"
    elif score >= 45: level = "MEDIUM"
    else:             level = "LOW"

    MITIGATION = {
        "LOW":      "Continue routine monitoring. No immediate action required.",
        "MEDIUM":   "Increase monitoring frequency. Cross-reference all sources.",
        "HIGH":     "Escalate for senior analyst. Investigate all connections.",
        "CRITICAL": "Immediate escalation required. Multiple confirmed violations. Preserve evidence urgently.",
    }

    # Dynamic confidence from data richness
    data_pts   = sum([bool(p.get("confirmed_name")), bool(phones),
                      bool(locations), bool(platforms), bool(flags)])
    confidence = max(35, min(88, 40 + (data_pts * 5) + (len(factors) * 4)))

    return {
        "risk_score":       score,
        "risk_level":       level,
        "risk_factors":     factors[:8],
        "mitigation_notes": MITIGATION[level],
        "confidence":       confidence,
        "explanation":      (
            f"Risk Score {score}/100 ({level}) — weighted fallback "
            f"scoring from confirmed evidence flags."
        ),
        "agent":            "RiskAgent",
        "method":           "weighted-fallback",
        "generated_at":     datetime.datetime.utcnow().isoformat(),
    }


def _rule_based_risk(entity_data: dict) -> dict:
    """
    Thin wrapper: builds the anomalies list from entity_data then delegates
    to calculate_risk_score for scoring logic.
    """
    # Collect anomaly strings from all known person-dict fields
    anomalies: list = []
    for f in entity_data.get("anomaly_flags", []) or []:
        anomalies.append(f.get("flag", str(f)) if isinstance(f, dict) else str(f))
    for c in entity_data.get("conflicts", []) or []:
        anomalies.append(c.get("flag", str(c)) if isinstance(c, dict) else str(c))
    for bf in entity_data.get("behavioral_flags", []) or []:
        anomalies.append(str(bf))

    result = calculate_risk_score(entity_data, anomalies)
    # Keep uppercase aliases for any callers that read RISK_SCORE / RISK_LEVEL
    result["RISK_SCORE"] = result["risk_score"]
    result["RISK_LEVEL"] = result["risk_level"]
    result["method"]     = "rule-based-fallback"
    return result


def run_risk_agent(
    person: dict,
    anomalies=None,
    graph=None,
    user_id: str = "system",
) -> dict:
    """
    Risk assessment with proper evidence-based scoring.

    Signature: run_risk_agent(person, anomalies=None, graph=None, user_id="system")

    Backward-compatible:
      - Old callers that pass a single entity_data dict work unchanged.
      - Old callers that pass (entity_data, user_id_string) also work:
        the string is detected and shifted to user_id automatically.
      - New callers can pass anomalies explicitly for full control.
    """
    # ── Backward-compat: handle old-style calls ────────────────────────────────
    # orchestrator calls fn(data, user_id) → anomalies receives a string
    # _build_risk_section calls run_risk_agent(person) → anomalies is None
    if isinstance(anomalies, str):
        # Caller passed user_id as the second positional arg (old signature)
        user_id   = anomalies
        anomalies = None

    # Build anomalies list from person dict when caller didn't supply one
    if not isinstance(anomalies, list):
        anomalies = []
        for f in (person or {}).get("anomaly_flags", []) or []:
            anomalies.append(f.get("flag", str(f)) if isinstance(f, dict) else str(f))
        for c in (person or {}).get("conflicts", []) or []:
            anomalies.append(c.get("flag", str(c)) if isinstance(c, dict) else str(c))
        for bf in (person or {}).get("behavioral_flags", []) or []:
            anomalies.append(str(bf))

    # ── Core scoring (user-specified logic) ───────────────────────────────────
    risk_result = calculate_risk_score(person, anomalies, graph)

    result = {
        "risk_score":       risk_result["risk_score"],
        "risk_level":       risk_result["risk_level"],
        "confidence":       risk_result["confidence"],
        "explanation":      risk_result["explanation"],
        # Top anomaly strings shown as evidence bullets in §16 of the PDF report.
        # _build_risk_section handles both plain strings and structured dicts.
        # Prefer structured risk_factors from AI path; fall back to raw anomaly strings.
        "risk_factors":     risk_result.get("risk_factors") or (anomalies[:5] if anomalies else []),
        # Kept for _build_risk_section compatibility (mitigation line in §16)
        "mitigation_notes": risk_result.get("mitigation_notes", ""),
        # Kept for orchestrator callers and test assertions
        "agent":            "RiskAgent v2 - Evidence Based",
        "generated_by":     "RiskAgent v2 - Evidence Based",
        "timestamp":        datetime.datetime.utcnow().isoformat(),
        "generated_at":     datetime.datetime.utcnow().isoformat(),
        # Pass through actual method so report shows ai-bedrock vs weighted-fallback
        "method":           risk_result.get("method", "evidence-based"),
    }

    _log_agent_run(
        "RiskAgent",
        "score=" + str(result["risk_score"]) + " level=" + result["risk_level"]
        + " anomalies=" + str(len(anomalies)),
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
            patterns.append({"pattern_type": "MULTI_IDENTITY", "entities_involved": [p.get("id","") for p in persons[:5]],
                              "description": f"{len(persons)} person entities — possible cross-platform identity",
                              "evidence": ["Multiple PersonEntity nodes"], "significance": "MEDIUM", "confidence": 75})
        if events:
            patterns.append({"pattern_type": "ACTIVITY_CLUSTER", "entities_involved": [e.get("id","") for e in events[:5]],
                              "description": f"{len(events)} activity events recorded",
                              "evidence": ["EventEntity nodes present"], "significance": "LOW", "confidence": 65})
        if locations:
            patterns.append({"pattern_type": "LOCATION_PATTERN", "entities_involved": [l.get("id","") for l in locations[:5]],
                              "description": f"{len(locations)} location(s) linked to subject",
                              "evidence": ["LocationEntity nodes present"], "significance": "LOW", "confidence": 60})
        result = {"patterns_found": patterns, "hidden_connections": [], "anomalies": [],
                  "agent": "PatternAgent", "generated_at": datetime.datetime.utcnow().isoformat(), "method": "local"}

    # Rule 2: tag each pattern as [SUPPORTED] or [UNSUPPORTED]
    entities = ontology_data.get("entities", {})
    confirmed = [
        v.get("name", k) for k, v in entities.items()
        if isinstance(v, dict) and v.get("name")
    ]
    result = validate_agent_output(result, confirmed)

    result["engine"] = LAST_ENGINE_USED
    _log_agent_run("PatternAgent", f"patterns={len(result.get('patterns_found',[]))}", user_id)
    return result


# ══════════════════════════════════════════════════════════════════════════════
# AGENT 3 — INVESTIGATIVE STEPS
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
    import json, re

    person = report.get("person", {}) if isinstance(report, dict) else {}

    # Collect all anomaly/flag strings from every available source
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

    # Build context fields for the prompt
    subject_name   = person.get("confirmed_name", "Unknown Subject")
    locations      = ", ".join(person.get("location_stated", [])[:4]) or "Unknown"
    platforms      = ", ".join(person.get("platforms_confirmed", [])
                               or person.get("platforms", []))[:200] or "None confirmed"
    legal_flags    = [str(a) for a in anomalies
                      if any(k in str(a).upper() for k in
                             ["PMLA", "NDPS", "NCB", "IT ACT", "DPDP", "CERT",
                              "ARREST", "CHARGESHEET", "LOOKOUT", "ED ", "SEIZED"])]
    anomaly_text   = "\n".join(f"- {a}" for a in anomalies[:25]) or "None recorded."
    legal_text     = "\n".join(f"- {f}" for f in legal_flags[:10]) or "None detected."

    # ── AI PATH ────────────────────────────────────────────────────────────────
    prompt = f"""You are a senior legal analyst generating investigative next steps for an official intelligence report.

Subject: {subject_name}
Location(s): {locations}
Platforms found: {platforms}

All anomaly and crime indicators:
{anomaly_text}

Confirmed legal proceedings / high-severity flags:
{legal_text}

Generate exactly 5 specific, lawful investigative next steps for THIS specific case.

Rules:
- Each step must be specific to the evidence listed above — no generic steps
- Each step must cite the exact Indian law section (IT Act, BNSS, PMLA, DPDP, etc.)
- Each step must state the authorisation required (court order, CERT-In, MLAT, etc.)
- Each step must state what evidence gap it fills
- If evidence shows IT Act violations → cite IT Act Section 43/66/69
- If evidence shows DPDP violations → cite DPDP Act 2023
- If evidence shows platform metadata → cite platform legal process
- If evidence shows device activity → cite BNSS search warrant
- If evidence shows encrypted comms → cite IT Act Section 69
- If evidence shows CERT-In inquiry → reference the existing case number
- DO NOT suggest electoral rolls or MCA21 filings unless business crime is indicated
- DO NOT suggest steps that duplicate evidence already confirmed above

Return ONLY valid JSON. No markdown. No explanation outside JSON.

{{
  "steps": [
    {{
      "step_number": 1,
      "action": "<specific action>",
      "legal_basis": "<exact law + section>",
      "authorization": "<what is needed>",
      "priority": "<HIGH|MEDIUM|LOW>",
      "fills_gap": "<what evidence gap this addresses>"
    }}
  ]
}}"""

    try:
        result_text = _call_ai(prompt, max_tokens=1200)
        clean = re.sub(r"```(?:json)?|```", "", result_text).strip()
        m = re.search(r"\{.*\}", clean, re.DOTALL)
        if m:
            parsed = json.loads(m.group())
            ai_steps = parsed.get("steps", [])
            if ai_steps:
                # Normalise to the key schema expected by the report renderer.
                # Keys used by _build_next_steps_section:
                #   step / action, step_number, legal_basis,
                #   authorization_required, priority, value, fills_gap
                normalised = []
                for idx, s in enumerate(ai_steps[:5], 1):
                    normalised.append({
                        "step":                  s.get("action", ""),
                        "action":                s.get("action", ""),   # dual-key compat
                        "step_number":           s.get("step_number", idx),
                        "legal_basis":           s.get("legal_basis", ""),
                        "authorization":         s.get("authorization", ""),
                        "authorization_required": s.get("authorization", ""),
                        "priority":              s.get("step_number", idx),
                        "value":                 s.get("priority", "HIGH"),
                        "fills_gap":             s.get("fills_gap", ""),
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

    # ── RULE-BASED FALLBACK ────────────────────────────────────────────────────
    steps = []
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
    if "IT ACT" in anom_upper or "DPDP" in anom_upper or "CERT" in anom_upper \
            or "DELETION" in anom_upper or "SCRAPING" in anom_upper:
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

    # Absolute fallback — only if nothing matched at all
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

    # Pad to minimum 5 steps so Section 17 always has substantive content.
    _PAD_STEPS = [
        {
            "step":        "File formal complaint with CERT-In and request server log preservation",
            "legal_basis": "IT Act 2000 — Section 43/66 + DPDP Act 2023",
            "priority":    len(steps) + 1, "value": "HIGH",
            "fills_gap":   "Cyber offence documentation and log preservation",
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
    _pad_idx = 0
    while len(steps) < 5 and _pad_idx < len(_PAD_STEPS):
        _candidate = _PAD_STEPS[_pad_idx]
        _pad_idx += 1
        # Don't duplicate a step that's semantically already in the list
        _existing_text = " ".join(s.get("step","") for s in steps).upper()
        if not any(kw in _existing_text for kw in
                   _candidate["step"].upper().split()[:4]):
            steps.append(_candidate)

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
        result["method"] = "ai"
        result["compliance_score"] = max(0, min(100, int(result.get("compliance_score", 0))))
    else:
        flags = []
        person = report_data.get("person", {}) if isinstance(report_data, dict) else {}
        if isinstance(person, dict):
            if person.get("phones_found") or person.get("emails_found"):
                flags.append({"concern": "Personal contact data in report",
                               "section": "DPDP Act 2023 §4 — Data Minimization",
                               "recommendation": "Verify lawful basis for retaining contact details"})
            if len(person.get("location_stated", [])) > 3:
                flags.append({"concern": "Multiple location data points may exceed necessity",
                               "section": "DPDP Act 2023 §5 — Purpose Limitation",
                               "recommendation": "Retain only locations necessary for stated purpose"})
        score = max(0, 100 - len(flags) * 15)
        result = {
            "compliant": len(flags) == 0, "compliance_score": score, "flags": flags,
            "cleared_for_export": len(flags) <= 1, "agent": "ComplianceAgent",
            "generated_at": datetime.datetime.utcnow().isoformat(), "method": "local",
        }

    _log_agent_run("ComplianceAgent",
                   f"compliant={result.get('compliant')} score={result.get('compliance_score')}", user_id)
    return result


# ══════════════════════════════════════════════════════════════════════════════
# AGENT 5 — OPERATIONAL STRATEGY
# ══════════════════════════════════════════════════════════════════════════════

_STRATEGY_PROMPT = (
    "You are StrategyAgent, an operational intelligence planner for a lawful OSINT platform.\n"
    "Design a phased operational strategy based on the subject profile, asset inventory, and report data.\n"
    "Every phase MUST cite a legal basis under Indian law (DPDP Act 2023, CrPC, IT Act 2000).\n"
    "Include clear abort conditions for each phase.\n"
    "Do NOT suggest illegal, coercive, or surveillance methods.\n\n"
    "Return ONLY this JSON (no markdown, no commentary):\n"
    '{{\n  "operational_phases": [\n'
    '    {{\n      "phase": 1,\n      "name": "",\n      "objective": "",\n'
    '      "actions": [],\n      "legal_basis": "",\n'
    '      "resources_required": [],\n      "success_criteria": "",\n'
    '      "abort_condition": ""\n    }}\n  ],\n'
    '  "risk_mitigations": [],\n  "priority_targets": [],\n'
    '  "estimated_timeline": "",\n  "classification": "RESTRICTED",\n'
    '  "agent": "StrategyAgent",\n  "generated_at": ""\n}}\n\n'
    "Subject profile:\n{person_json}\n\n"
    "Assets data:\n{assets_json}\n\n"
    "Report summary:\n{report_json}"
)


def run_strategy_agent(
    person_object: dict,
    assets_data:   list,
    report_data:   dict,
    user_id:       str = "system",
) -> dict:
    """Generate an operational strategy based on subject, assets, and report data."""
    person_json = json.dumps(person_object or {}, indent=2, ensure_ascii=False)
    if len(person_json) > 4000:
        person_json = person_json[:4000] + "... [truncated]"

    assets_json = json.dumps(assets_data or [], indent=2, ensure_ascii=False)
    if len(assets_json) > 3000:
        assets_json = assets_json[:3000] + "... [truncated]"

    report_json = json.dumps(report_data or {}, indent=2, ensure_ascii=False)
    if len(report_json) > 3000:
        report_json = report_json[:3000] + "... [truncated]"

    # Rule 1: build grounding context and append to prompt
    grounding = build_grounding_context(person_object or {})
    strategy_prompt = _STRATEGY_PROMPT + GROUNDING_RULE.format(grounding_context=grounding)

    raw    = _call_ai(strategy_prompt.format(
        person_json=person_json,
        assets_json=assets_json,
        report_json=report_json,
    ))
    result = _extract_json(raw) if raw else None

    if result and "operational_phases" in result:
        result.setdefault("agent", "StrategyAgent")
        result.setdefault("generated_at", datetime.datetime.utcnow().isoformat())
        result["method"] = "ai"
    else:
        phases = []
        if assets_data:
            phases.append({
                "phase":              1,
                "name":               "Asset Verification",
                "objective":          f"Verify and legally document {len(assets_data)} identified asset(s)",
                "actions":            [
                    "Cross-reference asset identifiers with public registries",
                    "Confirm ownership via MCA/VAHAN/RERA public portals",
                    "Document chain-of-custody for each asset record",
                ],
                "legal_basis":        "IT Act 2000 §69B — Monitoring for intelligence purposes; public records access",
                "resources_required": ["Digital forensics analyst", "Public registry access"],
                "success_criteria":   "All assets verified with legal documentation trail",
                "abort_condition":    "Assets confirmed legally unrelated to subject of interest",
            })
        phases.append({
            "phase":              len(phases) + 1,
            "name":               "Digital Footprint Mapping",
            "objective":          "Compile complete open-source digital profile",
            "actions":            [
                "OSINT collection from publicly accessible platforms",
                "Username correlation across platforms",
                "Communication pattern analysis from public posts",
            ],
            "legal_basis":        "DPDP Act 2023 §7 — Processing for legitimate purposes with safeguards",
            "resources_required": ["OSINT analyst", "AetherLens platform"],
            "success_criteria":   "Complete digital profile with confidence ≥ 70%",
            "abort_condition":    "Evidence of coordinated OPSEC indicating counter-surveillance",
        })
        phases.append({
            "phase":              len(phases) + 1,
            "name":               "Network & Association Analysis",
            "objective":          "Map known associates and organizational affiliations",
            "actions":            [
                "Graph analysis of relationship network",
                "Cross-platform identity correlation",
                "Corporate/NGO affiliation search via MCA public portal",
            ],
            "legal_basis":        "CrPC §91 — Production and inspection of public documents",
            "resources_required": ["Intelligence analyst", "Network analysis tools"],
            "success_criteria":   "Network map with all first-degree connections documented",
            "abort_condition":    "Network connections confirmed as coincidental with no operational relevance",
        })
        phases.append({
            "phase":              len(phases) + 1,
            "name":               "Compliance Review & Report",
            "objective":          "Ensure all collected intelligence meets DPDP Act 2023 compliance",
            "actions":            [
                "Audit data collection methods for lawful basis",
                "Apply data minimization — purge non-essential records",
                "Generate final intelligence report with legal citations",
            ],
            "legal_basis":        "DPDP Act 2023 §4–§9 — Data minimization, purpose limitation, lawful basis",
            "resources_required": ["Legal compliance officer", "AetherLens PDF export"],
            "success_criteria":   "Compliance score ≥ 85%, report cleared for authorized distribution",
            "abort_condition":    "Compliance violations detected that cannot be remediated",
        })
        result = {
            "operational_phases":  phases,
            "risk_mitigations": [
                "Maintain strict chain of custody for all digital evidence",
                "All collection methods must comply with DPDP Act 2023 §7",
                "Document legal authorization before each phase commences",
                "Store all collected data with AES-256 encryption at rest",
            ],
            "priority_targets":    [str((person_object or {}).get("confirmed_name", "Primary Subject"))],
            "estimated_timeline":  "2–4 weeks",
            "classification":      "RESTRICTED",
            "agent":               "StrategyAgent",
            "generated_at":        datetime.datetime.utcnow().isoformat(),
            "method":              "local",
        }

    # Rule 2: tag each phase as [SUPPORTED] or [UNSUPPORTED]
    po = person_object or {}
    confirmed = (
        [po.get("confirmed_name", "")]
        + list(po.get("platforms_confirmed", []))
        + list(po.get("location_stated", []))
    )
    result = validate_agent_output(result, confirmed)

    _log_agent_run("StrategyAgent", f"phases={len(result.get('operational_phases', []))}", user_id)
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
    Generates a narrative analysis of the timeline — what story does it tell,
    critical moments, what an investigator should focus on first.
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
        result = _call_ai(prompt, max_tokens=800)
        clean  = re.sub(r"```(?:json)?|```", "", result).strip()
        m      = re.search(r"\{.*\}", clean, re.DOTALL)
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
        "RiskAgent":       run_risk_agent,
        "PatternAgent":    run_pattern_agent,
        "NextStepAgent":   run_next_step_agent,
        "ComplianceAgent": run_compliance_agent,
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
            person_data = report.get("person", {}) or {}

        agents_run = ["RiskAgent", "PatternAgent", "NextStepAgent", "ComplianceAgent"]

        results = {
            "risk":       self.run_agent("RiskAgent",       person_data, user_id),
            "patterns":   self.run_agent("PatternAgent",    ontology,    user_id),
            "next_steps": self.run_agent("NextStepAgent",   report,      user_id),
            "compliance": self.run_agent("ComplianceAgent", report,      user_id),
        }

        # StrategyAgent — only when assets data is provided
        if assets_data:
            try:
                strat = run_strategy_agent(person_data, assets_data, report, user_id)
                results["strategy"] = strat
                agents_run.append("StrategyAgent")
            except Exception as exc:
                results["strategy"] = {
                    "error": str(exc), "agent": "StrategyAgent",
                    "generated_at": datetime.datetime.utcnow().isoformat(),
                }

        # TimelineAgent — runs after contradiction and gap detection
        try:
            from modules.timeline import (
                detect_timeline_contradictions,
                detect_timeline_gaps,
            )
            timeline_events = person_data.get("timeline_events", [])
            tl_contradictions = detect_timeline_contradictions(
                timeline_events, raw_documents or []
            )
            tl_gaps = detect_timeline_gaps(timeline_events)

            results["timeline_analysis"] = run_timeline_analysis_agent(
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
                "error": str(exc), "agent": "TimelineAgent",
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
