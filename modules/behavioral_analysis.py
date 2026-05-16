"""
AetherLens — Behavioral Analysis Module
Bedrock/Gemini powered intelligence assessment of structured subject data.
"""

import json
import re
import requests

import config
from modules.sanitizer import defensive, safe_list, safe_str


def deep_flatten(val):
    if val is None:
        return ""
    if isinstance(val, str):
        return val
    if isinstance(val, (int, float, bool)):
        return str(val)
    if isinstance(val, dict):
        return " ".join(
            deep_flatten(v) for v in val.values() if v is not None
        )
    if isinstance(val, (list, tuple)):
        return " ".join(
            deep_flatten(item) for item in val if item is not None
        )
    try:
        return str(val)
    except Exception:
        return ""

EMPTY_ASSESSMENT = {
    "timezone_probable":        "",
    "timezone_confidence":      0,
    "activity_pattern":         "",
    "behavioral_flags":         [],
    "language_indicators":      [],
    "interest_clusters":        [],
    "network_influence_score":  0,
    "analyst_notes":            "",
    "data_limitations":         [],
    # Timeline intelligence fields
    "digital_identity_age":     "",
    "first_platform":           "",
    "platform_adoption_pattern": "",
    "account_flags":            [],
    "estimated_age_indicators": [],
    "sophistication_level":     "",
}

ANALYST_PROMPT_TEMPLATE = """You are an intelligence analyst performing behavioral analysis on structured public OSINT data about a subject.

STRICT RULES:
1. Analyze ONLY what is present in the provided data. Do not infer, invent, or extrapolate beyond the data.
2. Cite the specific data source for every observation (e.g., "GitHub profile states...", "Google News article dated...").
3. Return ONLY a valid JSON object matching the exact schema below. No markdown, no code fences, no commentary outside the JSON.
4. If a field cannot be determined from the data, use "" for strings, 0 for numbers, [] for arrays.
5. behavioral_flags must only contain observations directly supported by data — list the supporting source after each flag.
6. network_influence_score: integer 0-100 based on follower/mention data if present, else 0.
7. Format as professional intelligence assessment — precise, neutral, factual.
8. For timeline fields: use account_timeline and account_creation_flags from the subject data if present.
9. sophistication_level: "LOW" / "MEDIUM" / "HIGH" / "EXPERT" based on platform diversity, early adoption, and account age.

REQUIRED JSON SCHEMA:
{{
  "timezone_probable": "region/timezone string or empty",
  "timezone_confidence": 0,
  "activity_pattern": "description of observed activity patterns or empty",
  "behavioral_flags": ["flag with source citation"],
  "language_indicators": ["language or writing style observations"],
  "interest_clusters": ["topic/interest cluster observed"],
  "network_influence_score": 0,
  "analyst_notes": "summary assessment string",
  "data_limitations": ["limitation or gap in available data"],
  "digital_identity_age": "e.g. 15 years (since 2009) or empty",
  "first_platform": "platform name or empty",
  "platform_adoption_pattern": "description of how platforms were adopted over time or empty",
  "account_flags": ["concise flag from timeline analysis"],
  "estimated_age_indicators": ["any age indicators from join dates or content"],
  "sophistication_level": "LOW/MEDIUM/HIGH/EXPERT or empty"
}}

SUBJECT DATA:
{payload}

Return the JSON assessment now:"""



def _extract_json(text: str) -> dict | None:
    """Extract and parse the first JSON object from a text string."""
    text = re.sub(r"```(?:json)?", "", text).strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except Exception:
            pass
    return None


def _local_fallback(subject_data: dict) -> dict:
    """
    Rule-based fallback when AI is unavailable.
    Produces a meaningful behavioral summary from confirmed anomaly flags,
    phone data, and location data — never returns "Not determined" when
    intelligence data is present.
    """
    assessment = json.loads(json.dumps(EMPTY_ASSESSMENT))
    person     = subject_data.get("person", subject_data)
    limitations = []

    # ── Anomaly flag driven patterns (primary signal for fusion subjects) ──────
    flags     = safe_list(person.get("anomaly_flags", []))
    phones    = safe_list(person.get("phones_found", []))
    locations = safe_list(person.get("location_stated", []))
    patterns  = []

    # International contact pattern
    intl = [p for p in phones if any(
        p.startswith(cc) for cc in ("+971", "+92", "+65", "+44", "+1", "+60")
    )]
    if intl:
        patterns.append(
            f"International contacts confirmed: {len(intl)} foreign number(s) including "
            f"{', '.join(intl[:3])}"
        )
        assessment["behavioral_flags"].append(
            f"International contact detected: {len(intl)} foreign numbers"
        )

    # Legal/financial risk signals — only genuine threat indicators, not passive noise.
    # Spam-received flags (offers@, newsletter@, alerts@) are explicitly excluded here;
    # analyze_behavioral_patterns() handles the spam-vs-signal distinction below.
    _SPAM_SKIP = ("offers@", "newsletter@", "alerts@", "spam -", "unsubscribe")
    for flag in flags:
        # Normalise dict flags to plain text before keyword matching and storage.
        # anomaly_flags contains {"flag":..., "source":..., "severity":...} dicts from
        # document_flags; str(dict) would produce Python repr artifacts in behavioral_flags.
        flag_text = (
            flag.get("flag") or flag.get("detail") or str(flag)
            if isinstance(flag, dict) else str(flag)
        )
        f_lower = flag_text.lower()
        # Skip inbound spam noise
        if any(sk in f_lower for sk in _SPAM_SKIP):
            continue
        if any(k in f_lower for k in ["pmla", "prevention of money laundering"]):
            patterns.append("Active PMLA proceedings — financial crime indicator")
            assessment["behavioral_flags"].append(flag_text[:100])
        elif any(k in f_lower for k in ["ed history", "ed inquiry", "enforcement direct"]):
            patterns.append("Enforcement Directorate history — prior financial investigations")
            assessment["behavioral_flags"].append(flag_text[:100])
        elif any(k in f_lower for k in ["telegram", "encrypted", "protonmail", "signal"]):
            patterns.append("Encrypted communication platforms confirmed — counter-surveillance awareness")
            assessment["behavioral_flags"].append(flag_text[:100])
        elif any(k in f_lower for k in ["night", "2am", "1am", "00:", "01:"]):
            patterns.append("Nocturnal activity pattern confirmed — late-night communications detected")
            assessment["behavioral_flags"].append(flag_text[:100])

    # ── Clean signal pass via analyze_behavioral_patterns ────────────────────
    # Pull timeline, phones, and emails from the person dict and run the
    # noise-aware function.  Merge any additional flags it finds that aren't
    # already in the assessment.
    _timeline = (
        safe_list(person.get("account_timeline"))
        or safe_list(person.get("timeline"))
        or safe_list(person.get("communication_log"))
    )
    _phones = phones   # already extracted above
    _emails = safe_list(person.get("emails_found", []))
    _abp = analyze_behavioral_patterns(_timeline, _phones, _emails, subject_data)
    _existing_flags = {f.lower() for f in assessment["behavioral_flags"]}
    for _f in _abp.get("behavioral_flags", []):
        if _f.lower() not in _existing_flags:
            assessment["behavioral_flags"].append(_f)
            _existing_flags.add(_f.lower())
    # Expose night_activity_score and spam_exposure_level on the assessment
    assessment["night_activity_score"] = _abp.get("night_activity_score", 0)
    assessment["spam_exposure_level"]  = _abp.get("spam_exposure_level", "moderate")

    # Geographic pattern
    if len(locations) >= 2:
        patterns.append(
            f"Geographic mobility across {len(locations)} confirmed locations: "
            f"{', '.join(str(l) for l in locations[:4])}"
        )

    # Timezone inference from Mumbai/India locations
    india_locs = [l for l in locations if any(
        city in str(l).lower() for city in ("mumbai", "delhi", "pune", "bangalore", "hyderabad")
    )]
    if india_locs:
        assessment["timezone_probable"] = "Asia/Kolkata (UTC+5:30)"

    # ── Digital presence signals (secondary — social media / GitHub subjects) ──
    gh = person.get("github_data", {})
    if gh.get("joined"):
        patterns.append(f"GitHub account created {gh['joined']}")
        assessment["activity_pattern"] = f"GitHub account created {gh['joined']}. "

    followers = person.get("follower_counts", {})
    total_followers = sum(c for c in followers.values() if isinstance(c, int))
    if total_followers > 100000:
        assessment["network_influence_score"] = 85
    elif total_followers > 10000:
        assessment["network_influence_score"] = 60
    elif total_followers > 1000:
        assessment["network_influence_score"] = 35
    elif total_followers > 0:
        assessment["network_influence_score"] = 15

    bios     = person.get("bio_data", {})
    bio_text = " ".join(deep_flatten(v) for v in bios.values()).lower()
    tech_keywords = ["software", "developer", "engineer", "linux", "open source",
                     "python", "code", "programming", "tech"]
    if any(k in bio_text for k in tech_keywords):
        assessment["interest_clusters"].append("Technology / Software Development")
    if bio_text:
        assessment["language_indicators"].append("English language content detected in bio data")

    platforms = person.get("platforms_confirmed", [])
    timeline  = person.get("account_timeline", [])
    digital_age   = person.get("digital_age_years", 0)
    oldest        = person.get("oldest_account", {})
    timeline_flags = person.get("account_creation_flags", [])

    if oldest:
        assessment["first_platform"] = oldest.get("platform", "")
        assessment["digital_identity_age"] = (
            f"{digital_age} years (since {oldest.get('join_year', '')})" if digital_age else ""
        )
    if timeline:
        plat_names = [e.get("platform", "") for e in timeline if e.get("platform")]
        assessment["platform_adoption_pattern"] = (
            f"Account creation sequence: {' -> '.join(plat_names)}" if plat_names else ""
        )
    if timeline_flags:
        assessment["account_flags"] = [
            f.get("detail", f.get("flag", "")) for f in timeline_flags
            if f.get("flag") not in ("OLDEST_ACCOUNT", "NO_DATE_DATA")
        ]

    n_platforms = len(platforms)
    early_adopter = any(f.get("flag") == "EARLY_ADOPTER" for f in timeline_flags) if timeline_flags else False
    if digital_age >= 10 and n_platforms >= 4 and early_adopter:
        assessment["sophistication_level"] = "EXPERT"
    elif digital_age >= 7 and n_platforms >= 3:
        assessment["sophistication_level"] = "HIGH"
    elif digital_age >= 3 and n_platforms >= 2:
        assessment["sophistication_level"] = "MEDIUM"
    elif n_platforms >= 1:
        assessment["sophistication_level"] = "LOW"

    # ── Compose activity pattern summary ──────────────────────────────────────
    if patterns:
        assessment["activity_pattern"] = ". ".join(patterns) + "."
    elif not assessment.get("activity_pattern"):
        assessment["activity_pattern"] = "Insufficient data for behavioral pattern analysis."

    # ── Analyst notes — synthesize from available signals ─────────────────────
    subject_name = person.get("confirmed_name", "Subject")
    if platforms:
        assessment["analyst_notes"] = (
            f"Subject has confirmed presence on {len(platforms)} platform(s): "
            f"{', '.join(platforms)}. Rule-based analysis from confirmed data."
        )
    else:
        # Build a meaningful summary from whatever evidence IS available
        _signals = []
        if phones:
            _signals.append(f"{len(phones)} phone number(s) on record")
        if locations:
            _signals.append(f"{len(locations)} location(s) identified")
        if patterns:
            _signals.append(f"{len(patterns)} behavioural pattern(s) detected")
        if _signals:
            _pat_str = (
                f" Identified patterns: {assessment['activity_pattern']}"
                if assessment.get("activity_pattern") and
                   assessment["activity_pattern"] != "Insufficient data for behavioral pattern analysis."
                else ""
            )
            assessment["analyst_notes"] = (
                f"Intelligence assessment for {subject_name}: {'; '.join(_signals)}.{_pat_str}"
            )
        else:
            assessment["analyst_notes"] = (
                f"Insufficient digital footprint data for {subject_name}. "
                "No confirmed social media platforms or communications data identified. "
                "Physical surveillance and financial record analysis recommended."
            )

    limitations.append("Rule-based analysis active — AI deep-analysis returned no parseable response.")
    if not followers:
        limitations.append("No follower/engagement metrics available.")
    if not bios:
        limitations.append("No bio data available for content analysis.")
    if not timeline:
        limitations.append("No account creation timeline data available.")

    assessment["data_limitations"] = limitations
    assessment["timezone_confidence"] = 0
    return assessment


def detect_rule_based_anomalies(structured_rows: list) -> list:
    """
    Scan structured data rows for hard-coded anomaly patterns.
    Returns list of {"flag": str, "detail": str} dicts.
    """
    anomalies = []
    _INTL_PREFIX = re.compile(r"^\+(?!91\b)\d")  # international but not India +91

    def _col(row: dict, *keys) -> str:
        for k in keys:
            if k in row:
                return str(row[k]).strip()
        return ""

    short_calls: list  = []
    self_calls: list   = []
    late_night: list   = []
    intl_contacts: set = set()
    coloc_check: dict  = {}    # (location, date) -> set of subjects

    for row in structured_rows:
        caller   = _col(row, "caller_name", "caller", "from_name", "from")
        receiver = _col(row, "receiver_name", "receiver", "to_name", "to")
        dur_raw  = _col(row, "duration_seconds", "duration", "call_duration", "duration_sec")
        date_val = _col(row, "date", "date_time", "call_date", "timestamp", "time")
        location = _col(row, "location", "city", "city_state", "place", "area")
        subj     = _col(row, "subject", "name", "person") or caller

        # Short call detection (< 5 seconds)
        try:
            dur = float(dur_raw)
            if 0 < dur < 5:
                short_calls.append(
                    f"{caller} -> {receiver} ({dur:.0f}s) on {date_val[:20]}"
                )
        except (ValueError, TypeError):
            pass

        # Self-call
        if caller and receiver and caller.strip() == receiver.strip():
            self_calls.append(f"{caller} called self on {date_val[:20]}")

        # Late night (00:00 – 04:00)
        try:
            time_part = date_val.strip()
            # Try to extract HH:MM
            m = re.search(r"\b(\d{1,2}):(\d{2})", time_part)
            if m:
                hour = int(m.group(1))
                if 0 <= hour < 4:
                    late_night.append(f"{subj or 'Unknown'} active at {m.group(0)} on {date_val[:20]}")
        except Exception:
            pass

        # International numbers
        for phone_field in ("receiver_number", "caller_number", "phone_number", "contact_number", "number"):
            ph = _col(row, phone_field)
            if ph and _INTL_PREFIX.match(ph):
                intl_contacts.add(ph[:20])

        # Also check receiver_name for international format hints in number columns
        for ph in [caller, receiver]:
            if ph and _INTL_PREFIX.match(ph):
                intl_contacts.add(ph[:20])

        # Co-location
        if subj and location and date_val:
            ck = (location.strip()[:40], date_val.strip()[:10])
            coloc_check.setdefault(ck, set()).add(subj.strip())

    # Emit anomaly entries
    for sc in short_calls[:5]:
        anomalies.append({"flag": "Suspicious short call", "detail": sc})

    for sc in self_calls[:3]:
        anomalies.append({"flag": "Self-call detected", "detail": sc})

    for ln in late_night[:5]:
        anomalies.append({"flag": "Late night activity", "detail": ln})

    if intl_contacts:
        anomalies.append({
            "flag":   "International contact detected",
            "detail": f"International numbers: {', '.join(list(intl_contacts)[:5])}",
        })

    for (loc, date), subjects in coloc_check.items():
        if len(subjects) >= 2:
            anomalies.append({
                "flag":   "Co-location detected",
                "detail": f"Multiple subjects at '{loc}' on {date}: {', '.join(list(subjects)[:4])}",
            })

    return anomalies


def analyze_behavioral_patterns(
    timeline: list,
    phones: list,
    emails: list,
    report_data: dict,
) -> dict:
    """
    Clean signal-vs-noise behavioral analysis.

    Separates *genuine* behavioral signals (night-burst activity, burner-phone
    diversity, VPN use) from *passive noise* (receiving marketing spam,
    newsletter/alert emails that the subject did not send).

    Args:
        timeline:    List of timeline event strings or dicts (account_timeline,
                     communication logs, etc.)
        phones:      List of phone number strings found for the subject.
        emails:      List of email address strings found for the subject.
        report_data: Top-level report dict — checked for vpn_usage_detected.

    Returns:
        {
            "behavioral_flags":    [str, ...],   # only genuine signals
            "night_activity_score": int,          # count of night-burst events
            "spam_exposure_level":  str,          # "high" | "moderate"
        }
    """
    flags: list = []
    night_bursts: int = 0
    spam_received_count: int = 0

    for event in timeline:
        text = str(event).lower()

        # Signal: subject was *active* during late-night/early-morning hours
        if any(kw in text for kw in ("23:", "00:", "01:", "02:", "03:", "04:")):
            night_bursts += 1

        # Noise: inbound spam / marketing / automated alerts — sender-side tokens,
        # not subject-initiated activity.  Do NOT count as a threat flag on its own.
        if any(kw in text for kw in ("spam -", "offers@", "alerts@", "newsletter@")):
            spam_received_count += 1

    # ── Genuine signals ───────────────────────────────────────────────────────
    if night_bursts >= 5:
        flags.append(
            "Late night / night-time burst activity detected across multiple sources"
        )

    # Multiple distinct phones → possible burner/secondary number strategy
    unique_phones = list({str(p).strip() for p in phones if p})
    if len(unique_phones) >= 5:
        flags.append(
            "Multiple phone numbers in use (possible burner/secondary numbers)"
        )

    # Spam exposure only flagged when *extreme* volume co-occurs with night ops —
    # correlation is meaningful, isolated spam receipt is not.
    if spam_received_count > 15 and night_bursts >= 4:
        flags.append(
            "High volume of spam exposure — possible operational correlation"
        )

    # VPN / anonymisation — check both top-level and nested person dict
    person_sub = report_data.get("person", {}) if isinstance(report_data, dict) else {}
    vpn_detected = (
        report_data.get("vpn_usage_detected")
        or person_sub.get("vpn_usage_detected")
        if isinstance(report_data, dict) else False
    )
    if vpn_detected:
        flags.append(
            "VPN / anonymisation tool usage detected during activity windows"
        )

    return {
        "behavioral_flags":     flags,
        "night_activity_score": night_bursts,
        "spam_exposure_level":  "high" if spam_received_count > 12 else "moderate",
    }


@defensive(fallback=(
    {
        "timezone_probable": "Not determined", "timezone_confidence": 0,
        "activity_pattern": "Analysis unavailable", "behavioral_flags": [],
        "analyst_notes": "Analysis failed — fallback active",
        "data_limitations": ["Behavioral analysis error"],
        "network_influence_score": 0,
    },
    "error_fallback",
))
def analyze(subject_data: dict, structured_rows: list = None) -> tuple[dict, str]:
    """
    Run behavioral analysis on a subject data payload.
    subject_data should contain 'person' (Person Object) and 'search_results'.
    structured_rows: optional list of dicts from CSV/Excel for rule-based anomaly detection.
    Returns (assessment_dict, method_used).
    method_used is 'bedrock', 'gemini-fallback', or 'local'.
    """
    try:
        return _analyze_inner(subject_data, structured_rows)
    except Exception as e:
        import traceback
        print(f"[BEHAVIORAL] Failed: {e}")
        traceback.print_exc()
        return {
            "timezone_probable": "Not determined",
            "timezone_confidence": 0,
            "activity_pattern": f"Analysis failed: {e}",
            "behavioral_flags": [],
            "interest_clusters": [],
            "analyst_notes": "Behavioral analysis encountered an error. Report generated from available data.",
            "data_limitations": [str(e)],
            "network_influence_score": 0,
            "rule_anomalies": [],
            "error": str(e),
        }, "error_fallback"


def _analyze_inner(subject_data: dict, structured_rows: list = None) -> tuple[dict, str]:
    # Always run rule-based anomaly detection first
    rule_anomalies = []
    if structured_rows:
        try:
            rule_anomalies = detect_rule_based_anomalies(structured_rows)
        except Exception:
            pass

    from modules.ai_agents import _call_ai

    payload_str = json.dumps(subject_data, indent=2, ensure_ascii=False)
    # Truncate large payloads — Fusion jobs with 6+ documents can produce 50K+
    # character payloads that cause Bedrock ValidationException or silent timeouts,
    # causing the entire AI path to fail and the fallback message to appear.
    # 12,000 chars is consistent with the truncation used by other agents.
    if len(payload_str) > 12000:
        payload_str = payload_str[:12000] + "\n... [payload truncated for AI context limit]"
    prompt      = ANALYST_PROMPT_TEMPLATE.format(payload=payload_str)

    # ── Vehicle intelligence block ─────────────────────────────────────
    challan_data = (
        subject_data.get("challan_data")
        or subject_data.get("person", {}).get("challan_data")
    )
    anpr_data = (
        subject_data.get("anpr_data")
        or subject_data.get("person", {}).get("anpr_data")
    )
    vehicle_block = ""
    if challan_data:
        vehicle_block += (
            f"\n\nTRAFFIC CHALLAN INTELLIGENCE:\n"
            f"Total challans: {challan_data.get('count', 0)}\n"
            f"Vehicles: {challan_data.get('vehicles_found', [])}\n"
            f"Night violations: {challan_data.get('night_count', 0)}\n"
            f"Unpaid fines: {challan_data.get('unpaid_count', 0)}\n"
            f"Location cluster: {challan_data.get('location_frequency', [])}\n"
        )
    if anpr_data:
        vehicle_block += (
            f"\n\nANPR CAMERA INTELLIGENCE:\n"
            f"Total captures: {anpr_data.get('count', 0)}\n"
            f"Night captures: {anpr_data.get('night_count', 0)}\n"
            f"High speed (>120kmph): {anpr_data.get('high_speed_count', 0)}\n"
            f"Route pattern: {anpr_data.get('route_pattern', 'Not determined')}\n"
        )
    if vehicle_block:
        prompt += vehicle_block + (
            "\n\nAnalyse vehicle data for:\n"
            "1. Route predictability (frequent locations = pattern)\n"
            "2. Operational timing (night violations = night ops)\n"
            "3. Financial stress indicators (unpaid fines = vulnerability)\n"
            "4. Geographic territory (challan location cluster)\n"
            "5. High speed = evasion/urgency\n"
            "6. Interdiction opportunities (predictable route + time)\n\n"
            "Label vehicle findings:\n"
            "[VERIFIED] from challan/ANPR data\n"
            "[ANALYTICAL] from your inference\n"
        )

    # ── Engine priority: Bedrock fusion path → _call_ai fallback chain ──────────
    # Try _call_bedrock_for_fusion first — it creates its own boto3 client and is
    # proven to work even when config.bedrock_client is None at module init time.
    raw        = ""
    engine_tag = "local"
    try:
        from modules.entity_resolution import _call_bedrock_for_fusion, _extract_json as _ej
        bedrock_raw = _call_bedrock_for_fusion(prompt)
        if bedrock_raw and len(bedrock_raw.strip()) > 50:
            raw        = bedrock_raw
            engine_tag = "claude-sonnet-4-bedrock"
            print(f"[BEHAVIORAL] Bedrock responded ({len(raw)} chars)")
    except Exception as be:
        print(f"[BEHAVIORAL] Bedrock fusion path error: {be}")

    # If Bedrock didn't answer, try the _call_ai chain (Gemini fallback)
    if not raw:
        from modules.ai_agents import _call_ai, LAST_ENGINE_USED
        raw = _call_ai(prompt, max_tokens=4000)
        if raw:
            engine_tag = LAST_ENGINE_USED or "ai-fallback"
            print(f"[BEHAVIORAL] _call_ai responded via {engine_tag}")

    if raw:
        from modules.ai_agents import _extract_json as _aj
        parsed = _aj(raw)
        if not parsed:
            # Try the entity_resolution extractor as backup
            try:
                from modules.entity_resolution import _extract_json as _ej2
                parsed = _ej2(raw)
            except Exception:
                parsed = None
        if parsed:
            # Backfill missing keys with defaults
            for key, default in EMPTY_ASSESSMENT.items():
                if key not in parsed:
                    parsed[key] = json.loads(json.dumps(default))
            # Clamp scores
            score = parsed.get("network_influence_score", 0)
            parsed["network_influence_score"] = max(0, min(100, int(score) if isinstance(score, (int, float)) else 0))
            tz_conf = parsed.get("timezone_confidence", 0)
            parsed["timezone_confidence"] = max(0, min(100, int(tz_conf) if isinstance(tz_conf, (int, float)) else 0))
            # Prepend rule-based anomaly flags
            if rule_anomalies:
                rule_strs = [f"{a['flag']}: {a['detail']}" for a in rule_anomalies]
                existing  = parsed.get("behavioral_flags", [])
                parsed["behavioral_flags"] = rule_strs + [f for f in existing if f not in rule_strs]
            # ── Clean signal pass: remove spam-noise, add genuine signals ────
            # Strip AI-generated flags that are clearly spam-received noise
            _SPAM_NOISE_KW = (
                "spam received", "marketing email", "newsletter received",
                "promotional email", "received spam", "spam exposure",
                "offers@", "newsletter@", "alerts@",
            )
            parsed["behavioral_flags"] = [
                bf for bf in parsed.get("behavioral_flags", [])
                if not any(kw in str(bf).lower() for kw in _SPAM_NOISE_KW)
            ]
            # Augment with clean signals from analyze_behavioral_patterns
            _person = subject_data.get("person", subject_data)
            _timeline = (
                safe_list(_person.get("account_timeline"))
                or safe_list(_person.get("timeline"))
                or safe_list(_person.get("communication_log"))
            )
            _phones = safe_list(_person.get("phones_found", []))
            _emails = safe_list(_person.get("emails_found", []))
            _abp = analyze_behavioral_patterns(_timeline, _phones, _emails, subject_data)
            _existing = {f.lower() for f in parsed["behavioral_flags"]}
            for _f in _abp.get("behavioral_flags", []):
                if _f.lower() not in _existing:
                    parsed["behavioral_flags"].append(_f)
                    _existing.add(_f.lower())
            parsed["night_activity_score"] = _abp.get("night_activity_score", 0)
            parsed["spam_exposure_level"]  = _abp.get("spam_exposure_level", "moderate")
            parsed["rule_anomalies"] = rule_anomalies
            return parsed, engine_tag

    # All AI paths exhausted — rule-based only
    print("[BEHAVIORAL] All AI engines returned empty — using rule-based analysis")
    result = _local_fallback(subject_data)
    if rule_anomalies:
        rule_strs = [f"{a['flag']}: {a['detail']}" for a in rule_anomalies]
        existing  = result.get("behavioral_flags", [])
        result["behavioral_flags"] = rule_strs + [f for f in existing if f not in rule_strs]
    result["rule_anomalies"] = rule_anomalies
    return result, "local"
