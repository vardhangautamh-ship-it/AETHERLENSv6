"""
AetherLens — Entity Resolution Module
Gemini-powered cross-source identity consolidation.
Builds a structured Person Object from raw OSINT search results.
"""

import json
import re
import datetime
import requests

import config
from modules.sanitizer import (
    safe_str, safe_list, safe_int, safe_phone, defensive,
    most_common_by_key, normalize_name_key,
)

# ── Person Object schema ───────────────────────────────────────────────────────

EMPTY_PERSON = {
    "confirmed_name":       "",
    "name_variants":        [],
    "usernames":            {},
    "platforms_confirmed":  [],
    "profile_urls":         {},
    "bio_data":             {},
    "location_stated":      [],
    "join_dates":           {},
    "follower_counts":      {},
    "post_counts":          {},
    "web_mentions":         [],
    "news_appearances":     [],
    "github_data":          {},
    "confidence_score":     0,
    "data_sources":         [],
    "data_gaps":            [],
    # Cross-platform discovery fields
    "confirmed_linked_profiles": [],
    "potential_linked_profiles": [],
    "emails_found":              [],
    "phones_found":              [],
    "websites_found":            [],
    "linkedin_intelligence":     {},
    "cross_platform_summary": {
        "total_confirmed":   0,
        "total_potential":   0,
        "platforms_present": [],
        "discovery_method":  [],
    },
    # Account timeline fields
    "account_timeline":       [],
    "oldest_account":         {},
    "newest_account":         {},
    "account_creation_flags": [],
    "digital_age_years":      0,
}


def _new_person() -> dict:
    return json.loads(json.dumps(EMPTY_PERSON))


# ── Gemini API call ────────────────────────────────────────────────────────────

def _call_gemini(prompt: str) -> str:
    """
    Send a prompt to Gemini and return the text response.
    Returns empty string if API key not configured or request fails.
    """
    api_key = config.GEMINI_API_KEY
    if not api_key or api_key == "your_gemini_key_here":
        return ""

    url = f"{config.GEMINI_ENDPOINT}?key={api_key}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature":     0.1,
            "topP":            0.9,
            "maxOutputTokens": 4096,
        },
        "safetySettings": [
            {"category": "HARM_CATEGORY_HARASSMENT",        "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH",       "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
        ],
    }
    try:
        resp = requests.post(url, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        candidates = data.get("candidates", [])
        if candidates:
            parts = candidates[0].get("content", {}).get("parts", [])
            if parts:
                return parts[0].get("text", "")
    except Exception:
        pass
    return ""


# ── Prompt builder ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are an expert OSINT analyst performing entity resolution.
You receive raw search results about a person from multiple online sources.
Your task is to determine if multiple records refer to the same individual,
extract all available structured data, and return ONLY a valid JSON object.

STRICT RULES:
1. Return ONLY the JSON object — no markdown, no code fences, no explanation.
2. NEVER hallucinate or invent information not present in the source data.
3. If a field cannot be determined from the data, set it to "Not found" (string) or [] (list) or {} (dict).
4. Flag any inconsistencies between sources in the "data_gaps" array.
5. confidence_score must be an integer 0-100 based on evidence strength.
6. "platforms_confirmed" must only list platforms with actual evidence.
7. Use exact text from source data — no paraphrasing of uncertain facts.

Return this exact JSON structure (no extra keys):
{
  "confirmed_name": "string or Not found",
  "name_variants": ["list of alternative names/aliases found"],
  "usernames": {"platform": "username"},
  "platforms_confirmed": ["list of platforms with evidence"],
  "profile_urls": {"platform": "url"},
  "bio_data": {"source": "bio text"},
  "location_stated": ["list of locations mentioned"],
  "join_dates": {"platform": "date string"},
  "follower_counts": {"platform": integer_or_Not found},
  "post_counts": {"platform": integer_or_Not found},
  "web_mentions": ["list of notable web mentions with URL"],
  "news_appearances": ["list of news titles with URL"],
  "github_data": {"repos": N, "followers": N, "bio": "text", "joined": "date"},
  "confidence_score": integer_0_to_100,
  "data_sources": ["list of sources used"],
  "data_gaps": ["list of missing or inconsistent fields"]
}"""


def _build_prompt(query: str, search_results: dict) -> str:
    results_json = json.dumps(search_results, indent=2, ensure_ascii=False)
    return f"""{SYSTEM_PROMPT}

TARGET QUERY: "{query}"

RAW SEARCH DATA:
{results_json}

Now return the JSON Person Object:"""


# ── JSON extractor ─────────────────────────────────────────────────────────────

def _extract_json(text: str) -> dict | None:
    """Extract first valid JSON object from Gemini response text."""
    # Strip markdown fences if present
    text = re.sub(r"```(?:json)?", "", text).strip()
    # Try direct parse
    try:
        return json.loads(text)
    except Exception:
        pass
    # Try to find first {...} block
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except Exception:
            pass
    return None


# ── Local fallback resolver ────────────────────────────────────────────────────

def _local_resolve(query: str, search_results: dict) -> dict:
    """
    Rule-based fallback when Gemini is unavailable.
    Extracts structured data directly from search result fields.
    """
    person = _new_person()
    results = search_results.get("results", [])

    if not results:
        person["data_gaps"].append("No search results available")
        return person

    person["confirmed_name"] = query if not is_bad_subject_name(query) else "Unknown Subject"
    sources_seen = set()
    urls = {}
    bios = {}
    github_found = {}

    for r in results:
        platform = r.get("platform", "Unknown")
        url      = r.get("url", "")
        snippet  = r.get("snippet", "")
        name     = r.get("full_name", "")
        raw      = r.get("raw", {})

        if r.get("confidence", 0) == 0:
            continue

        sources_seen.add(platform)
        if url:
            urls[platform] = url
        if snippet:
            bios[platform] = snippet[:200]

        # Name variants
        if name and name.lower() != query.lower() and name not in person["name_variants"]:
            person["name_variants"].append(name)

        # Web / news mentions
        if platform == "Google News" and url:
            person["news_appearances"].append(f"{name} — {url}")
        elif url and platform not in ("GitHub", "Reddit"):
            person["web_mentions"].append(f"{name} — {url}")

        # GitHub specific
        if platform == "GitHub" and raw:
            github_found = {
                "repos":     raw.get("public_repos", "Not found"),
                "followers": raw.get("followers", "Not found"),
                "bio":       raw.get("bio") or "Not found",
                "joined":    raw.get("created_at", "Not found")[:10] if raw.get("created_at") else "Not found",
            }
            person["follower_counts"]["GitHub"] = raw.get("followers", "Not found")
            person["post_counts"]["GitHub"]     = raw.get("public_repos", "Not found")
            uname = raw.get("login", "")
            if uname:
                person["usernames"]["GitHub"] = uname

        # Reddit specific
        if platform == "Reddit" and raw:
            person["follower_counts"]["Reddit"] = raw.get("total_karma", "Not found")
            uname = raw.get("name", "")
            if uname:
                person["usernames"]["Reddit"] = uname

        # Join date extraction from result metadata
        jd = r.get("join_date", "")
        if jd and platform not in person["join_dates"]:
            person["join_dates"][platform] = {
                "join_date":         jd,
                "join_year":         r.get("join_year", 0),
                "join_month":        r.get("join_month", ""),
                "join_timestamp":    r.get("join_timestamp", ""),
                "account_age_years": r.get("account_age_years", 0),
                "account_age_days":  r.get("account_age_days", 0),
                "last_active":       r.get("last_active", ""),
                "date_confidence":   r.get("date_confidence", ""),
                "date_source":       r.get("date_source", ""),
            }

    person["platforms_confirmed"] = sorted(sources_seen)
    person["profile_urls"]        = urls
    person["bio_data"]            = bios
    person["data_sources"]        = sorted(sources_seen)
    if github_found:
        person["github_data"] = github_found

    # Confidence: based on number of sources with valid results
    n = len(sources_seen)
    if n >= 4:
        person["confidence_score"] = 80
    elif n == 3:
        person["confidence_score"] = 65
    elif n == 2:
        person["confidence_score"] = 45
    elif n == 1:
        person["confidence_score"] = 30
    else:
        person["confidence_score"] = 10

    # Data gaps
    if not person["location_stated"]:
        person["data_gaps"].append("Location: Not found in any source")
    if not person["join_dates"]:
        person["data_gaps"].append("Join dates: Not found")
    if not person["github_data"]:
        person["data_gaps"].append("GitHub profile: Not searched or not found")
    if not person["news_appearances"]:
        person["data_gaps"].append("News appearances: None found")

    return person


# ── Fusion document resolution ─────────────────────────────────────────────────

FUSION_PROMPT = """You are an entity resolution engine.
Given this raw structured data extracted from uploaded documents,
identify the primary subject.
Extract and structure all information about this person.

Rules:
- Primary subject = person appearing most frequently in the data
- Never use filenames or sheet names as person names
- Extract all phone numbers
- Extract all locations
- Extract all dates and events
- Extract all relationships
- Link all records referring to same person into one entity
- Return structured Person Object JSON matching the exact schema

Raw data: {data_json}

Return this exact JSON structure (no markdown, no code fences):
{{
  "confirmed_name": "primary subject full name",
  "name_variants": [],
  "usernames": {{}},
  "platforms_confirmed": [],
  "profile_urls": {{}},
  "bio_data": {{}},
  "location_stated": [],
  "join_dates": {{}},
  "follower_counts": {{}},
  "post_counts": {{}},
  "web_mentions": [],
  "news_appearances": [],
  "github_data": {{}},
  "confidence_score": 0,
  "data_sources": [],
  "data_gaps": []
}}"""


def _call_bedrock_for_fusion(prompt: str) -> str:
    """
    Call Claude Opus 4 on AWS Bedrock (ap-south-1) for fusion entity resolution.
    Primary engine — data stays in India (DPDP compliance).
    """
    try:
        if getattr(config, "bedrock_client", None) is None:
            return ""
        body = json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 2048,
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
            print(f"[BEDROCK] Entity resolution failed: {e}")
        except Exception:
            pass
        return ""




# ── Filename / document-artifact detection ───────────────────────────────────
FILENAME_SKIP_PATTERNS = [
    # Test and validation prefixes
    "test", "retest", "val",
    "val0", "val1", "val2",
    # Empty / null indicators
    "empty", "empty_data", "empty_doc",
    "null", "zero_data", "zero",
    "no_data", "blank",
    # Source-file indicators
    "source_a", "source_b", "source_c", "source_d",
    # Generic document names
    "profile", "document", "report", "file", "data",
    "background", "field_note", "observation", "log",
    "records", "transactions", "surveillance", "calls",
    "financial", "bank", "telecom", "challans",
    "anpr", "timeline",
    # AETHERLENS artifacts
    "aetherlens", "restricted", "intelligence",
    # Location/infrastructure strings that are never valid person names
    "point", "link", "bridge", "sea", "worli", "bandra", "nariman",
    "tower", "plaza", "mall", "junction", "station", "airport",
    "highway", "flyover", "naka", "garden", "sector",
    # Legal/academic subject-category strings that are never person names
    "procedure", "jurisprudence", "legislation", "ordinance",
    "constitution", "amendment", "tribunal", "jurisdiction",
]


_NAME_SUFFIX_WORDS = {
    "alias", "aka", "residence", "address", "home", "office",
    "case", "file", "ref", "reference", "id", "no", "number",
    "ltd", "pvt", "inc", "llp", "llc", "co",
    "mr", "mrs", "ms", "dr", "prof",
    "son", "daughter", "wife", "husband", "father", "mother",
    "profile", "record", "report", "note", "log", "unit", "section", "details",
    "1", "2", "3", "a", "b",
}

# Canonical anchored person-name matcher for STRUCTURED single-cell values
# (CSV/Excel cells, resolved subject strings). Anchored ^...$ so it validates a
# whole stripped cell as 2–4 Titlecase words; \s+ is safe here because a single
# cell never spans columns or lines. Single source of truth — imported by
# data_ingestion.extract_primary_subject_from_bytes and
# relationship_mapper so the two never drift apart.
RE_PERSON_NAME_CELL = re.compile(r"^([A-Z][a-z]{1,20}(?:\s+[A-Z][a-z]{1,20}){1,3})$")

# Social-platform tokens that can never appear in a real human name.
# Shared by _is_platform_suffix(), is_bad_subject_name(), and detect_all_conflicts().
_PLATFORM_TOKEN_SET = {
    "instagram", "telegram", "twitter", "github", "linkedin", "youtube",
    "facebook", "huggingface", "hugging_face", "reddit", "discord",
    "signal", "whatsapp", "tiktok", "snapchat", "threads", "x.com",
    # additional tokens kept for coverage
    "account", "user", "profile", "handle", "page",
    "channel", "group", "official", "verified",
    "post", "story", "reel", "dm", "message",
}


def _is_name_with_suffix(primary: str, variant: str) -> bool:
    """Return True if variant is primary name plus trailing descriptor words (not a real alias)."""
    p = primary.lower().strip()
    v = variant.lower().strip()
    if not v.startswith(p):
        return False
    suffix = v[len(p):].strip()
    if not suffix:
        return False
    return all(w in _NAME_SUFFIX_WORDS for w in suffix.split())


# Words that can NEVER appear in a real human name.
# If any individual word in a candidate matches one of these, the candidate
# is a document/category label, not a person.  Keep entries lowercase.
_IMPOSSIBLE_NAME_WORDS = {
    # Time / calendar
    "year", "years", "month", "months", "week", "weeks",
    "semester", "quarter", "term", "session", "annual", "quarterly",
    "monthly", "weekly", "daily", "period", "duration", "date", "time",
    # Academic / institutional
    "academic", "course", "subject", "class", "grade", "curriculum",
    "syllabus", "module", "lesson", "chapter", "unit", "section",
    "department", "faculty", "division", "programme", "program",
    "college", "school", "university", "institute", "board",
    # Role / relationship labels — form-field captions ("Student Name: …",
    # "Account Holder: …") never the person, only the label preceding the name.
    "student", "pupil", "candidate", "applicant", "enrollee", "trainee",
    "intern", "employee", "employer", "member", "customer", "client",
    "patient", "tenant", "resident", "holder", "cardholder", "guest",
    "nominee", "beneficiary", "guardian", "applicant", "subscriber",
    # Banking / transaction-statement noise ("Current"/"Savings" account,
    # "Wallet Recharge", "Big Billion Days" sale lines).
    "current", "savings", "wallet", "recharge", "cashback", "balance",
    "autopay", "mandate", "billion",
    # Legal / procedural
    "procedure", "proceedings", "act", "code", "statute",
    "regulation", "ordinance", "amendment", "clause", "article",
    "jurisprudence", "legislation", "jurisdiction",
    "tribunal", "constitution",
    # Investigation / inquiry labels — never a person's name
    "cyber", "incident", "inquiry", "investigation", "operation",
    # Document / data labels
    "report", "profile", "document", "file", "record", "log",
    "data", "dataset", "entry", "form", "sheet", "table",
    "summary", "overview", "details", "information", "info",
    "type", "category", "level", "status", "mode", "format",
    # Miscellaneous non-name nouns
    "policy", "procedure", "process", "method", "system",
    "plan", "scheme", "project", "case", "matter", "issue",
    "number", "no", "id", "ref", "reference", "code",
    # Transactional / vendor email-sender labels — a billing-receipt "From" name
    # like "Anthropic Billing" is an organisation label, never the subject.
    "billing", "invoice", "invoices", "receipt", "receipts",
    "payment", "payments", "payout", "refund", "refunds",
    "subscription", "order", "orders", "purchase", "purchases",
    "txn", "transaction", "checkout", "cart",
    "support", "helpdesk", "noreply", "notification", "notifications",
    "newsletter", "alert", "alerts", "unsubscribe", "mailer",
    # Email greeting / sign-off fragments mis-extracted as names ("Dear Sir").
    "dear", "sir", "madam", "regards", "sincerely", "greetings",
}


# Noise tokens that appear in spam-SMS labels, campus/location noise,
# and financial-offer message subjects that must never become the main subject.
_NOISE_SUBJECT_TOKENS = {
    "manesar", "campus", "gurugram", "gurgaon", "noida", "chandigarh",
    "spam", "credit", "loan", "insurance", "emi",
    "job", "delivery", "otp", "fraud", "offer",
    "mutual", "fund", "electricity", "personal", "car",
    "notification", "attempt", "reels", "apply", "winner",
    "cashback", "reward", "prize", "discount", "voucher",
    # Phase 0 additions: financial/marketing noise tokens from GhostWire stress test
    "bank", "alert", "newsletter", "promo", "marketing",
    # Investigation / operation titles that pollute subject name selection.
    # NOTE: generic words only — operation CODENAMES (GhostWire, Jupiter, Trident,
    # …) are caught structurally via "operation X" here + the filename-stem match
    # (Check 10), not by hardcoding individual codenames.
    "cyber", "incident", "inquiry", "sector",
    "operation", "case", "document", "investigation",
}


# Org / role "function" words that a real human name never ENDS in. Used by a
# structural rule (not a brand blocklist): a multi-token candidate whose final
# token is one of these is a "Brand + Function" sender label — e.g. "Acme
# Billing", "Globex Notifications", "Initech Helpdesk" — regardless of the brand,
# so it generalises to vendors never seen before.
_ORG_FUNCTION_SUFFIXES = frozenset({
    "billing", "invoice", "invoices", "receipt", "receipts",
    "payment", "payments", "payout", "payouts", "refund", "refunds",
    "subscription", "subscriptions", "order", "orders", "purchase", "purchases",
    "support", "helpdesk", "notification", "notifications",
    "alert", "alerts", "update", "updates", "newsletter",
    "admin", "accounts", "mailer", "noreply", "donotreply",
    "reminders", "reminder", "unsubscribe", "team", "teams",
})
# NOTE: deliberately excludes real-surname collisions (Sales, Service, Care, Bell,
# News) — the goal is brand-sender labels, not to reject people.


# Transactional / spam-category words that mean a "location" string is really a
# spam-SMS label, not a place. Deliberately EXCLUDES real place names (a city such
# as "Gurugram" must survive) — that is why this is separate from the name-noise
# token sets, which DO contain city names for subject-name rejection.
_NOISE_LOCATION_TOKENS = frozenset({
    "spam", "otp", "fraud", "scam", "phishing", "offer", "offers",
    "credit", "loan", "insurance", "mutual", "fund", "electricity",
    "delivery", "notification", "cashback", "reward", "prize", "winner",
    "voucher", "discount", "emi", "job", "lottery", "kyc", "recharge",
    "subscription", "invoice", "billing", "bill",
})


def _is_noise_location(value) -> bool:
    """True if a location string is actually a spam/transactional label."""
    s = safe_str(value).strip()
    if not s or len(s) < 2:
        return True
    sl = s.lower()
    if "spam" in sl:
        return True
    words = [w.strip(".:,()[]-") for w in re.split(r"[\s\-]+", sl)]
    return any(w in _NOISE_LOCATION_TOKENS for w in words)


def is_bad_subject_name(candidate, raw_documents=None) -> bool:
    """
    Return True if `candidate` is clearly a filename, test artifact,
    platform artifact, location label, spam/noise token, or document-title
    that should never become the main subject.

    Checks (in order):
      1. Null / too-short / multiline
      2. Pure digits / symbols
      3. Noise subject tokens (spam labels, campus/location names, financial offers)
      4. Exact-match known-bad literals (@spam, reels, manesar campus, etc.)
      5. Platform-suffix / doubled-name artifact
      6. Any token is a known social-platform word
      7. Impossible-name words (Academic, Report, Procedure, etc.)
      8. Role-title prefix (Officer, Inspector, etc.)
      9. Filename skip-pattern blocklist
     10. Matches an uploaded filename stem
    """
    if not candidate:
        return True
    s = str(candidate).strip()
    if "\n" in s or "\r" in s or len(s) < 3:
        return True

    if re.match(r'^[\d\s\-_]+$', s):
        return True

    sl = s.lower()

    # ── Check 3: noise subject tokens ────────────────────────────────────────
    # Single-token match is enough — "Manesar Campus Gurugram" is clearly noise.
    words = [w.strip(".:,()[]") for w in s.split()]
    if any(w.lower() in _NOISE_SUBJECT_TOKENS for w in words):
        return True

    # ── Check 4: exact-match known-bad literals ───────────────────────────────
    _KNOWN_BAD = {
        "@spam", "reels", "unknown", "student name", "manesar campus",
        "spam message", "delivery attempt", "otp verification",
    }
    if sl in _KNOWN_BAD:
        return True
    if sl.startswith("spam") or sl.endswith("campus") or sl.endswith("gurugram"):
        return True

    # ── Check 5: platform-suffix / doubled-name artifact ─────────────────────
    if _is_name_with_suffix(s, sl) or _is_platform_suffix(s, sl):
        return True

    # ── Check 6: any token is a social-platform word ─────────────────────────
    if any(w.lower() in _PLATFORM_TOKEN_SET for w in words):
        return True

    # ── Check 7: impossible-name words ───────────────────────────────────────
    if any(w.lower() in _IMPOSSIBLE_NAME_WORDS for w in words):
        return True

    # ── Check 7b: "Brand + Function" sender label (structural, brand-agnostic) ─
    # A real person's name never ends in an org/role function word. This catches
    # vendor sender labels for brands we have never enumerated — "Acme Billing",
    # "Globex Notifications", "Initech Helpdesk" — without a per-brand blocklist.
    if len(words) >= 2 and words[-1].lower().rstrip(".:,") in _ORG_FUNCTION_SUFFIXES:
        return True

    # ── Check 8: role-title prefix ────────────────────────────────────────────
    _ROLE_TITLES = {
        "officer", "constable", "inspector", "sub-inspector", "sub_inspector",
        "si", "dsp", "sp", "ips", "asi", "pi", "psi",
        "head", "superintendent", "investigating", "io",
    }
    if words and words[0].lower().rstrip(".:,") in _ROLE_TITLES:
        return True

    # ── Check 9: filename skip-pattern blocklist ──────────────────────────────
    # Match on TOKEN boundaries, never naked substrings. A candidate is a
    # filename/artifact only when one of its delimited tokens IS a skip pattern
    # (e.g. "anpr_log", "test_data", "val1_record"). Naked substring matching
    # wrongly rejected real human names: 'val' in 'Torvalds'/'Sandoval', 'park'
    # in the surname 'Park', 'naka' in 'Tanaka', 'sea' in 'Sean', 'bridge' in
    # 'Bridges'/'Bridget'. Multi-segment patterns (those containing '_') keep
    # substring matching, since they can never be a substring of a real name.
    c_lower     = sl.replace(" ", "_").replace("-", "_")
    name_tokens = set(re.split(r"[_\s\-.]+", c_lower))
    for pattern in FILENAME_SKIP_PATTERNS:
        if "_" in pattern:
            if pattern in c_lower:
                return True
        elif pattern in name_tokens:
            return True

    # ── Check 10: matches an uploaded filename stem ───────────────────────────
    for doc in (raw_documents or []):
        fname = (doc.get("filename", "") or doc.get("name", "")).lower()
        for ext in (".csv", ".pdf", ".txt", ".xlsx", ".xls", ".json"):
            fname = fname.replace(ext, "")
        fname = fname.replace(" ", "_").replace("-", "_").strip()
        if not fname:
            continue
        if c_lower == fname or (len(fname) > 4 and fname in c_lower):
            return True

    return False


def resolve_primary_subject(entities: list, person_object: dict) -> dict:
    """
    Strong primary subject name selection with aggressive noise rejection.

    Priority order:
      1. confirmed_name already present in person_object (trust the caller)
      2. PersonEntity names extracted from documents
      3. Last-resort: first entity name that clears a lightweight check

    Returns a dict with confirmed_name, name_variants, resolution_method.
    """
    _BAD_WORDS = {"incident", "inquiry", "cyber", "case", "file", "document",
                  "operation", "sector", "investigation"}

    candidates: list[tuple[str, int]] = []

    # 1. Honour confirmed_name already set by caller
    if person_object.get("confirmed_name"):
        name = person_object["confirmed_name"].strip()
        if not is_bad_subject_name(name):
            candidates.append((name, 100))

    # 2. PersonEntity names extracted from documents
    for entity in entities:
        if entity.get("type") == "PersonEntity" and entity.get("name"):
            name = entity["name"].strip()
            if not is_bad_subject_name(name):
                candidates.append((name, 80))

    # 3. Fallback: first entity name that passes a lightweight word-level check
    if not candidates:
        for entity in entities:
            if entity.get("type") == "PersonEntity" and entity.get("name"):
                name = entity["name"].strip()
                words_lc = {w.lower() for w in name.split()}
                if len(name) > 4 and not words_lc & _BAD_WORDS:
                    candidates.append((name, 60))
                    break

    if candidates:
        candidates.sort(key=lambda x: x[1], reverse=True)
        best_name = candidates[0][0]
    else:
        best_name = "Unknown Subject"

    return {
        "confirmed_name": best_name,
        "name_variants": [best_name],
        "resolution_method": "entity_resolution_v2",
    }


# ── Handle noise tokens (never valid social-media handles) ───────────────────
_NOISE_HANDLE_TOKENS: frozenset = frozenset({
    "spam", "reels", "offers", "alerts", "newsletter", "promo",
    "marketing", "notification", "otp", "fraud", "credit", "loan",
    "insurance", "winner", "cashback", "reward", "prize", "discount",
    "voucher", "delivery", "apply", "emi", "bill",
    "not found", "not_found", "unknown", "none", "null",
})


def clean_person_object(person: dict) -> dict:
    """
    Centralised, aggressive sanitisation of a person dict — call at every
    pipeline exit point so noise can NEVER reach report generation.

    Mutates and returns the same dict.

    Cleans:
      • confirmed_name / name    — rejected via is_bad_subject_name()
      • usernames dict           — noise handles removed
      • platforms_confirmed list — platforms whose handle was noise removed
      • confirmed_linked_profiles — profiles with noise handles removed
    """
    if not isinstance(person, dict):
        return person

    # ── confirmed_name / name ─────────────────────────────────────────────────
    cn = (person.get("confirmed_name") or "").replace("\n", " ").strip()
    if cn and is_bad_subject_name(cn):
        print(f"[CLEAN_PERSON] Rejected noise name: {cn!r}")
        person["confirmed_name"] = "Unknown Subject"
        person["name"]           = "Unknown Subject"

    # ── usernames dict ────────────────────────────────────────────────────────
    noisy_platforms: set = set()
    clean_unames: dict   = {}
    for plat, handle in list((person.get("usernames") or {}).items()):
        h = str(handle).lstrip("@").lower().strip()
        if not h or h in _NOISE_HANDLE_TOKENS or any(t in h for t in _NOISE_HANDLE_TOKENS if t):
            noisy_platforms.add(plat.lower())
            print(f"[CLEAN_PERSON] Rejected handle {handle!r} for {plat}")
        else:
            clean_unames[plat] = handle
    person["usernames"] = clean_unames

    # ── platforms_confirmed: drop platforms whose handle was noise ────────────
    if noisy_platforms:
        person["platforms_confirmed"] = [
            p for p in (person.get("platforms_confirmed") or [])
            if p.lower() not in noisy_platforms
        ]

    # ── confirmed_linked_profiles ─────────────────────────────────────────────
    clean_profiles = []
    for profile in (person.get("confirmed_linked_profiles") or []):
        h = str(profile.get("username", "")).lstrip("@").lower().strip()
        if h and (h in _NOISE_HANDLE_TOKENS or any(t in h for t in _NOISE_HANDLE_TOKENS if t)):
            continue
        clean_profiles.append(profile)
    person["confirmed_linked_profiles"] = clean_profiles

    # ── location fields: strip spam/transactional labels (keep real places) ───
    for _loc_key in ("location_stated", "locations_mentioned"):
        locs = person.get(_loc_key)
        if isinstance(locs, list) and locs:
            kept = [l for l in locs if not _is_noise_location(l)]
            if len(kept) != len(locs):
                dropped = [l for l in locs if _is_noise_location(l)]
                print(f"[CLEAN_PERSON] Dropped {len(dropped)} noise location(s) from {_loc_key}: {dropped[:5]}")
            person[_loc_key] = kept

    return person


# ── Deterministic platform extraction (Fix 1) ────────────────────────────────
# Column-name vocabularies for structured-row platform detection.  Lower-cased,
# matched case-insensitively against each row's keys.
_PLATFORM_COL_NAMES = frozenset({"platform", "site", "service", "network", "social", "social_media"})
_HANDLE_COL_NAMES   = frozenset({"username", "handle", "user", "account", "screen_name",
                                  "userid", "user_id", "user_name", "account_name"})
_URL_COL_NAMES      = frozenset({"url", "link", "profile", "profile_url", "profile_link"})
_STATUS_COL_NAMES   = frozenset({"status", "state", "verification", "verified", "confirmed"})
# Status values that mean "this is a real, confirmed account of the subject".
_CONFIRMED_STATUS_VALUES = frozenset({"confirmed", "verified", "active", "ok", "true", "yes", "valid"})


def extract_platforms_from_rows(raw_documents: list) -> dict:
    """
    Deterministic, AI-free platform / username extraction from structured rows.

    Reads any row that carries a platform column plus a username/handle column
    (e.g. social-media CSV exports).  A row is kept only when:
      • a status column, if present, is one of _CONFIRMED_STATUS_VALUES, AND
      • the handle is not a noise token (_NOISE_HANDLE_TOKENS).

    This guarantees that §03 (Platform Presence) and the subject's handles are
    populated with or without the LLM — fulfilling the "keep AI + add rules"
    decision.  Pure and reproducible: same rows → same output every run.

    Returns:
        {
          "platforms_confirmed": [sorted platform names],
          "usernames":           {platform: handle},
          "confirmed_linked_profiles": [{platform, username, url}, ...],
        }
    """
    platforms: dict = {}   # platform_display -> {"handle": str, "url": str}

    for doc in safe_list(raw_documents):
        if not isinstance(doc, dict):
            continue
        for row in safe_list(doc.get("structured_rows", [])):
            if not isinstance(row, dict):
                continue
            lc = {str(k).lower().strip(): k for k in row.keys()}

            def _col(names):
                for n in names:
                    if n in lc:
                        return safe_str(row.get(lc[n], ""))
                return ""

            plat   = _col(_PLATFORM_COL_NAMES).strip()
            handle = _col(_HANDLE_COL_NAMES).strip()
            if not plat or not handle:
                continue

            status = _col(_STATUS_COL_NAMES).strip().lower()
            if status and status not in _CONFIRMED_STATUS_VALUES:
                continue   # explicitly non-confirmed (e.g. SPAM) → skip

            h = handle.lstrip("@").lower().strip()
            if not h or h in _NOISE_HANDLE_TOKENS or any(t in h for t in _NOISE_HANDLE_TOKENS if t):
                continue   # noise handle (@reels, @spam, …) → skip

            url = _col(_URL_COL_NAMES).strip()
            if plat.lower() not in {p.lower() for p in platforms}:
                platforms[plat] = {"handle": handle.lstrip("@"), "url": url}

    plat_list = sorted(platforms.keys())
    usernames = {p: platforms[p]["handle"] for p in plat_list}
    linked = [
        {"platform": p, "username": platforms[p]["handle"], "url": platforms[p]["url"]}
        for p in plat_list if platforms[p]["url"]
    ]
    return {
        "platforms_confirmed":       plat_list,
        "usernames":                 usernames,
        "confirmed_linked_profiles": linked,
    }


_ENTITY_SKIP = [
    "field officer report", "field intelligence note", "intelligence report",
    "background profile document", "surveillance log", "activity log",
    "case file", "subject file", "aetherlens", "restricted", "classification",
    "authorized", "data completeness warning", "warning", "field officer",
    "field officer unit", "observer", "section", "page", "not found",
    "unknown", "confirmed", "unconfirmed", "case ref", "source",
    "ed mum", "ncb ggn", "ncb mum",
    # Operation / investigation title noise — generic phrases only (no hardcoded
    # codenames; "operation <codename>" is caught by the generic 'operation' word).
    "in cyber incident inquiry", "cyber incident inquiry", "cyber incident",
]


# ── Permanent phone extraction pipeline ──────────────────────────────────────

PHONE_REGEX = re.compile(
    r"(?<!\d)"                              # no digit immediately before
    r"("
    r"\+91[-]?\d{5}[-]?\d{5}"              # +91-XXXXX-XXXXX  (India — no space inside)
    r"|\+92[-]?\d{3}[-]?\d{7}"             # +92-XXX-XXXXXXX  (Pakistan)
    r"|\+971[-]?\d{2}[-]?\d{7}"            # +971-XX-XXXXXXX  (UAE)
    r"|\+65[-]?\d{4}[-]?\d{4}"             # +65-XXXX-XXXX    (Singapore)
    r"|\+\d{1,3}[-]?\d{6,12}"              # generic international (no space — stops at space)
    r"|\b91[6-9]\d{9}\b"                   # 91XXXXXXXXXX — CDR no-prefix format
    r"|\b0[6-9]\d{9}\b"                    # 0XXXXXXXXXX  — leading-zero format
    r"|\b[6-9]\d{9}\b"                     # plain 10-digit Indian mobile
    r")"
    r"(?!\d)"                              # no digit immediately after
)


def extract_all_emails(raw_documents: list) -> list:
    """
    Extract unique email addresses from structured rows and raw text.
    Skips service/billing domains so only the subject's own emails are returned.
    """
    EMAIL_RE = re.compile(
        r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
        re.IGNORECASE,
    )
    SKIP_DOMAINS = {
        "openai.com", "anthropic.com", "x.ai", "google.com",
        "billing.anthropic.com", "console.x.ai", "cloud.google.com",
        "noreply", "example.com", "mailer.com", "no-reply",
        "amazonaws.com", "sendgrid.net", "mailchimp.com",
    }
    found: set = set()
    for doc in (raw_documents or []):
        # Structured rows
        for row in doc.get("structured_rows", []):
            for val in row.values():
                for m in EMAIL_RE.findall(str(val)):
                    domain = m.split("@")[-1].lower()
                    if not any(sd in domain for sd in SKIP_DOMAINS):
                        found.add(m.lower())
        # Raw text
        for m in EMAIL_RE.findall(str(doc.get("raw_text", ""))):
            domain = m.split("@")[-1].lower()
            if not any(sd in domain for sd in SKIP_DOMAINS):
                found.add(m.lower())
    return list(found)


# ── Single phone validator — one source of truth for every phone path ─────────
def is_valid_phone(p: str) -> bool:
    """
    Rejects order IDs, CDR fragments, IP addresses, ISP data-volume figures, and
    other non-phone numbers; accepts real Indian mobile / international numbers.
    Called by extract_all_phones, build_phone_source_map, and
    data_ingestion._extract_phones so phone validation is identical everywhere.
    """
    if not p:
        return False
    cleaned = str(p).strip()

    # Reject leading small-number + space fragments (ISP data columns)
    # e.g. "1 1221774321", "128 9876543210", "1240 1221774321"
    if re.match(r"^\d{1,4}\s+\d+$", cleaned):
        return False

    # Reject IP addresses (standalone or embedded in a bled-together cell)
    if re.search(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", cleaned):
        return False

    # Reject ANPR/CCTV gate codes and similar short hyphenated ID codes — including
    # when a gate code bleeds together with an adjacent column. e.g. gate 'G3-2302'
    # + IP '10.44.21.8' collapses to '3-2302 1044218', whose 12 combined digits
    # otherwise pass the length/domestic checks. A real phone never carries a
    # 1–2-digit group before a hyphen (real groups are 3+ digits: '022-...',
    # '+91-98201-...'), so this never rejects a genuine number.
    if any(re.match(r"^\d{1,2}-\d{2,5}$", t) for t in cleaned.split()):
        return False

    # Reject order / invoice / receipt IDs
    # e.g. "RCP-ANT-231122-8821", "INV-OAI-2310-447821"
    if re.match(r"^[A-Z]{2,}-[A-Z]{2,}-", cleaned):
        return False
    # e.g. "INV-2310", "RCP-8821"
    if re.match(r"^[A-Z]{2,}-\d+", cleaned):
        return False
    # e.g. "2311-447822" (YYMM-XXXXXX order ref), "23-44782" (short order)
    if re.match(r"^\d{2,4}-\d{5,7}$", cleaned):
        return False
    # e.g. "447822-2311" (reversed order ref)
    if re.match(r"^\d{6}-\d{4}$", cleaned):
        return False
    # e.g. "TXN231016HDFC8821"
    if re.match(r"^TXN\d+", cleaned):
        return False

    # Reject date-format false positives (YYYY-MM-DD)
    if re.match(r"^\d{4}[-/]\d{2}[-/]\d{2}", cleaned):
        return False

    # Reject CDR row fragments with date tokens
    tokens = cleaned.split()
    if len(tokens) >= 2:
        if any(re.match(r"\d{4}-\d{2}-\d{2}", t) for t in tokens):
            return False

    # Reject CDR row fragments with trailing call-duration padding
    # e.g. "+91-98201-44109              320"
    if re.search(r"\s{2,}\d{1,4}\s*$", cleaned):
        return False

    # Reject ISP account-ID patterns  e.g. "1234/JIO/2022/00123"
    if re.match(r"^\d{4}/\w+/\d{4}/\d+$", cleaned):
        return False
    # Reject enrollment formats  e.g. "ALG/LLB/2022/001"
    if re.match(r"^[A-Za-z]+/[A-Za-z]+/\d{4}/", cleaned):
        return False

    # Strip to digits only for length and prefix checks
    digits = re.sub(r"[^\d]", "", cleaned)

    # Must be 7–15 digits
    if not (7 <= len(digits) <= 15):
        return False

    # Reject known ISP data-volume prefixes
    _DATA_PREFIXES = ("128", "256", "512", "1024", "2048", "4096", "3185", "1852")
    for _pfx in _DATA_PREFIXES:
        if digits.startswith(_pfx) and len(digits) > 8:
            return False

    # Domestic number validation (no + or 00 prefix)
    if not cleaned.startswith("+") and not cleaned.startswith("00"):
        if len(digits) == 10:
            if digits[0] not in "6789":
                return False
        elif len(digits) == 12 and digits[:2] == "91":
            if digits[2] not in "6789":
                return False
        elif len(digits) < 10:
            return False

    # International +91 double-check
    if "+91" in cleaned:
        local = digits[-10:]
        if len(local) == 10 and local[0] not in "6789":
            return False

    return True


def extract_all_phones(raw_documents: list) -> list:
    """
    Permanent comprehensive phone extractor.
    Scans ingestion results, structured rows, and raw text from every document.
    Works on any file format, any phone format.
    """
    phones: set = set()

    for doc in safe_list(raw_documents):
        # Source 1: ingestion entities result
        entities = safe_list(doc.get("entities", {}).get("phones", [])) if isinstance(doc.get("entities"), dict) else []
        for p in entities:
            val = p.get("value", "") if isinstance(p, dict) else str(p)
            clean = safe_phone(safe_str(val))
            if clean:
                phones.add(clean)

        # Source 2: structured rows (CDR / CSV columns)
        for row in safe_list(doc.get("structured_rows", [])):
            if not isinstance(row, dict):
                continue
            for val in row.values():
                text = safe_str(val)
                for m in PHONE_REGEX.findall(text):
                    clean = safe_phone(m)
                    if clean:
                        phones.add(clean)

        # Source 3: full document text — prefer full_text (unlimited) over raw_text (5000-char preview)
        text = safe_str(doc.get("full_text", "") or doc.get("raw_text", ""))
        for m in PHONE_REGEX.findall(text):
            clean = safe_phone(m)
            if clean:
                phones.add(clean)

    # ── Post-extraction validation (uses module-level is_valid_phone) ─────────
    # Filter 1: use is_valid_phone validator
    # Filter 2: also check date-format false positives
    _DATE_RE = re.compile(r"^\d{4}[-/]\d{2}[-/]\d{2}")
    filtered: set = set()
    for p in phones:
        if not is_valid_phone(p):
            continue
        if _DATE_RE.match(p.strip()):
            continue
        filtered.add(p)

    # Deduplicate: normalize to last-10-digits key so +91-9876543210
    # and 9876543210 are treated as the same subscriber number.
    # Preference order: +country format > bare 10-digit > other
    seen_digits: dict = {}   # last-10-digit key -> canonical representation
    for p in filtered:
        digits = re.sub(r"\D", "", p)
        # Use last 10 digits as the dedup key (handles 91XXXXXXXXXX, 0XXXXXXXXXX, +91...)
        key = digits[-10:] if len(digits) >= 10 else digits
        existing = seen_digits.get(key)
        if existing is None:
            seen_digits[key] = p
        else:
            # Prefer the version with a + prefix (international canonical form)
            if p.startswith("+") and not existing.startswith("+"):
                seen_digits[key] = p

    return list(seen_digits.values())


def build_phone_source_map(raw_documents: list) -> dict:
    """
    Returns {canonical_phone: [filename, ...]} mapping each confirmed phone
    number to the document(s) it was extracted from.
    Used for per-source attribution in §14 of the PDF report.
    """
    raw_map: dict = {}   # phone -> set of filenames (before dedup)

    for doc in safe_list(raw_documents):
        fname = safe_str(doc.get("filename", "")) or "unknown file"

        # Entities list
        entities = safe_list(doc.get("entities", {}).get("phones", [])) if isinstance(doc.get("entities"), dict) else []
        for p in entities:
            val = p.get("value", "") if isinstance(p, dict) else str(p)
            clean = safe_phone(safe_str(val))
            if clean and is_valid_phone(clean):
                raw_map.setdefault(clean, set()).add(fname)

        # Structured rows
        for row in safe_list(doc.get("structured_rows", [])):
            if not isinstance(row, dict):
                continue
            for val in row.values():
                for m in PHONE_REGEX.findall(safe_str(val)):
                    clean = safe_phone(m)
                    if clean and is_valid_phone(clean):
                        raw_map.setdefault(clean, set()).add(fname)

        # Raw text
        text = safe_str(doc.get("full_text", "") or doc.get("raw_text", ""))
        for m in PHONE_REGEX.findall(text):
            clean = safe_phone(m)
            if clean and is_valid_phone(clean):
                raw_map.setdefault(clean, set()).add(fname)

    # Normalise keys to last-10-digit canonical form (same dedup as extract_all_phones)
    seen_digits: dict = {}   # last-10-digit -> (canonical_phone, set_of_sources)
    for phone, sources in raw_map.items():
        digits = re.sub(r"\D", "", phone)
        key = digits[-10:] if len(digits) >= 10 else digits
        existing = seen_digits.get(key)
        if existing is None:
            seen_digits[key] = (phone, sources)
        else:
            canon, existing_sources = existing
            existing_sources.update(sources)
            # Prefer + prefix canonical form
            if phone.startswith("+") and not canon.startswith("+"):
                seen_digits[key] = (phone, existing_sources)
            else:
                seen_digits[key] = (canon, existing_sources)

    return {phone: sorted(sources) for phone, sources in seen_digits.values()}


def _local_resolve_from_rows(subject_name: str, structured_rows: list, filename: str,
                              document_flags: list = None, doc_locations: list = None) -> dict:
    """Build minimal person dict from structured rows when all AI calls fail."""
    person = _new_person()
    # Strip newline artifacts from text extraction (e.g. "Zafar Ahmed Khan\nCase")
    clean_subject = (subject_name or "").replace("\n", " ").replace("\r", " ").strip()
    person["confirmed_name"]   = (clean_subject if clean_subject and not is_bad_subject_name(clean_subject) else "Unknown")
    person["data_sources"]     = [filename] if filename else []
    person["confidence_score"] = 30 if subject_name else 5

    phones_seen: set = set()
    locs_seen: set   = set()
    for row in structured_rows[:500]:
        for k, v in row.items():
            v = str(v).strip()
            if not v:
                continue
            kl = k.lower()
            if any(x in kl for x in ("phone", "number", "contact")):
                if re.match(r"[\d\+][\d\s\-]{6,}", v):
                    phones_seen.add(v)
            if any(x in kl for x in ("location", "city", "place", "area")):
                if len(v) > 2 and v not in ("nan", "None"):
                    locs_seen.add(v)

    person["phones_found"]    = list(phones_seen)[:20]
    person["location_stated"] = list(locs_seen)[:10]

    # Merge locations from PDF text extraction
    if doc_locations:
        existing = set(person["location_stated"])
        for loc in doc_locations:
            if loc not in existing:
                person["location_stated"].append(loc)
                existing.add(loc)
        person["location_stated"] = person["location_stated"][:20]

    # Collect document flags as anomaly_flags
    if document_flags:
        person["anomaly_flags"] = [
            {"flag": f.get("flag", str(f)), "source": f.get("source", ""), "severity": "MEDIUM"}
            if isinstance(f, dict) else {"flag": str(f), "source": "", "severity": "MEDIUM"}
            for f in document_flags
        ]

    # Gap detection only — confidence scoring happens in the caller after
    # AI overlay and multi-doc raw_documents are available (line 763-764).
    # Calling calculate_confidence here (no raw_documents) always caps at 5
    # and is immediately overwritten — skip to avoid confusing log noise.
    person["data_gaps"] = detect_data_gaps(person)

    return person


@defensive(fallback=(
    {
        "confirmed_name": "Unknown Subject", "confidence_score": 0,
        "data_gaps": ["Resolution failed"], "phones_found": [],
        "emails_found": [], "location_stated": [], "platforms_confirmed": [],
        "data_sources": [], "anomaly_flags": [], "conflicts": [],
    },
    "error_fallback",
))
def resolve_entity_from_multiple_docs(raw_documents: list) -> tuple[dict, str]:
    """
    Resolve primary subject from a list of ingest result dicts.
    Uses primary_subject from each doc first, then frequency analysis.
    Collects anomaly_flags and locations from all docs.
    Returns (person_dict, method_used).
    """
    from collections import Counter

    # PRIORITY 1 — Use primary_subject from any document
    primary_name = None
    for doc in (raw_documents or []):
        ps = doc.get("primary_subject", "").replace("\n", " ").replace("\r", " ").strip()
        if ps and ps.lower() not in _ENTITY_SKIP and len(ps) > 4 \
                and not is_bad_subject_name(ps, raw_documents):
            primary_name = ps
            print("[RESOLVE] Using primary_subject:", primary_name)
            break

    # PRIORITY 2 — Frequency analysis across all name lists
    if not primary_name:
        all_names = []
        for doc in (raw_documents or []):
            ents = doc.get("entities", {})
            for n in ents.get("names", []):
                val = n.get("value", "") if isinstance(n, dict) else str(n)
                val = val.replace("\n", " ").replace("\r", " ").strip()
                # Strip trailing suffix words so "Zafar Ahmed Khan Case" → "Zafar Ahmed Khan"
                parts = val.split()
                while parts and parts[-1].lower() in _NAME_SUFFIX_WORDS:
                    parts = parts[:-1]
                val = " ".join(parts)
                if val:
                    all_names.append(val)
        if all_names:
            # Aggregate by normalized key (case + whitespace) so variants of one
            # name vote together instead of splitting the count (Fix 3).
            for name, _ in most_common_by_key(all_names):
                if not any(s in name.lower() for s in _ENTITY_SKIP) and len(name) > 5 \
                        and not is_bad_subject_name(name, raw_documents):
                    primary_name = name
                    break

    # FINAL GUARD — even if something slipped through, kill filename/test artifacts
    if primary_name and is_bad_subject_name(primary_name, raw_documents):
        print(f"[RESOLVE] Rejecting filename-as-subject: '{primary_name}'")
        primary_name = None

    if not primary_name:
        primary_name = "Unknown Subject"

    print("[RESOLVE] Final subject:", primary_name)

    # Collect all structured_rows, flags, and locations across docs
    all_rows = []
    all_flags = []
    all_locs = []
    all_sources = []
    for doc in (raw_documents or []):
        all_rows.extend(doc.get("structured_rows", []))
        all_flags.extend(doc.get("document_flags", []))
        all_locs.extend(doc.get("locations", []))
        fname = doc.get("filename", doc.get("name", ""))
        if fname:
            all_sources.append(fname)

    # ── Try AI engines in priority order: Bedrock → Gemini ──────────────────
    ai_person = None
    ai_method = None
    if primary_name != "Unknown Subject":
        sample_rows = all_rows[:200] if all_rows else []
        data_payload = {
            "subject_hint": primary_name,
            "source_file":  ", ".join(all_sources),
            "rows":         sample_rows,
        }
        prompt = FUSION_PROMPT.format(
            data_json=json.dumps(data_payload, indent=2, ensure_ascii=False)
        )

        print("[RESOLVE] Trying Bedrock (Claude Sonnet 4 · ap-south-1)...")
        raw = _call_bedrock_for_fusion(prompt)
        print(f"[RESOLVE] Bedrock returned {len(raw) if raw else 0} chars")
        if raw:
            parsed = _extract_json(raw)
            if parsed and parsed.get("confirmed_name"):
                ai_person, ai_method = parsed, "claude-sonnet-4-bedrock"

        if not ai_person:
            print("[RESOLVE] Bedrock empty/parse-failed -> trying Gemini")
            gk = config.GEMINI_API_KEY
            if gk and gk not in ("", "your_gemini_key_here"):
                raw = _call_gemini(prompt)
                print(f"[RESOLVE] Gemini returned {len(raw) if raw else 0} chars")
                if raw:
                    parsed = _extract_json(raw)
                    if parsed and parsed.get("confirmed_name"):
                        ai_person, ai_method = parsed, "gemini"

    # ── Build base person from rows, then overlay AI result if we got one ───
    person = _local_resolve_from_rows(
        primary_name, all_rows,
        ", ".join(all_sources) if all_sources else "",
        document_flags=all_flags,
        doc_locations=all_locs,
    )
    person["data_sources"] = all_sources

    method_used = "local-multidoc"
    if ai_person:
        # Overlay AI-extracted fields (but keep row-derived safety fields).
        # Skip confirmed_name and usernames — both are handled explicitly below.
        _SKIP_IN_OVERLAY = {"confirmed_name", "usernames"}
        for key, default in EMPTY_PERSON.items():
            if key in _SKIP_IN_OVERLAY:
                continue
            if key in ai_person and ai_person[key]:
                person[key] = ai_person[key]
        # confirmed_name: noise-checked write
        cn = ai_person.get("confirmed_name", "").replace("\n", " ").replace("\r", " ").strip()
        if cn and not is_bad_subject_name(cn, raw_documents):
            person["confirmed_name"] = cn
        # usernames: strip noise handles before merging
        ai_unames = ai_person.get("usernames") or {}
        for plat, handle in ai_unames.items():
            h = str(handle).lstrip("@").lower().strip()
            if h and h not in _NOISE_HANDLE_TOKENS and not any(t in h for t in _NOISE_HANDLE_TOKENS if t):
                person["usernames"][plat] = handle
        person["data_sources"] = all_sources
        method_used = ai_method
        print(f"[RESOLVE] AI engine accepted: {method_used}")

    # ── Deterministic platform/username extraction (Fix 1) ───────────────────
    # Runs with or without the AI; unions into (never overwrites) AI-found data
    # so §03 and the subject's handles are populated even when the LLM is off.
    det_plat = extract_platforms_from_rows(raw_documents)
    if det_plat["platforms_confirmed"]:
        existing_plats = {p.lower(): p for p in safe_list(person.get("platforms_confirmed", []))}
        for p in det_plat["platforms_confirmed"]:
            existing_plats.setdefault(p.lower(), p)
        person["platforms_confirmed"] = sorted(existing_plats.values())
    person.setdefault("usernames", {})
    for plat, handle in det_plat["usernames"].items():
        if not any(ep.lower() == plat.lower() for ep in person["usernames"]):
            person["usernames"][plat] = handle
    person.setdefault("confirmed_linked_profiles", [])
    _seen_links = {
        (safe_str(l.get("platform", "")).lower(), safe_str(l.get("username", "")).lower())
        for l in person["confirmed_linked_profiles"] if isinstance(l, dict)
    }
    for l in det_plat["confirmed_linked_profiles"]:
        _k = (l["platform"].lower(), l["username"].lower())
        if _k not in _seen_links:
            person["confirmed_linked_profiles"].append(l)
            _seen_links.add(_k)
    if det_plat["platforms_confirmed"]:
        print(f"[RESOLVE] Deterministic platforms: {det_plat['platforms_confirmed']}")

    # Permanent: comprehensive phone extraction across all sources
    all_phones = extract_all_phones(raw_documents)
    if all_phones:
        person["phones_found"] = all_phones
    # Per-file source attribution for §14 report rendering
    person["phone_sources"] = build_phone_source_map(raw_documents)

    # Permanent: email extraction across all sources
    all_emails = extract_all_emails(raw_documents)
    if all_emails:
        existing = set(safe_list(person.get("emails_found", [])))
        for e in all_emails:
            if e not in existing:
                existing.add(e)
        person["emails_found"] = list(existing)

    # Permanent: conflict detection across all sources
    if primary_name != "Unknown Subject":
        detect_all_conflicts(raw_documents, primary_name, person)

    person["data_gaps"] = detect_data_gaps(person, raw_documents)

    # Primary confidence score — rule-based ceiling applied over any AI score
    ai_score   = int(person.get("confidence_score") or 0) if ai_person else 0
    rule_score = calculate_confidence(person, raw_documents)
    person["confidence_score"] = min(ai_score, rule_score) if ai_person else rule_score

    # Evidence-based breakdown for the report PDF (§02)
    num_timeline = len(safe_list(person.get("timeline_events", [])))
    conf_result  = calculate_stable_confidence(
        num_files       = len(raw_documents),
        num_phones      = len(safe_list(person.get("phones_found", []))),
        num_timeline    = num_timeline,
        num_graph_nodes = 0,   # graph not built yet at this stage; updated in app.py
        num_gaps        = len(safe_list(person.get("data_gaps", []))),
        num_emails      = len(safe_list(person.get("emails_found", []))),
        num_locations   = len(safe_list(person.get("locations_mentioned", []))),
    )
    person["confidence_breakdown"]  = conf_result["breakdown"]
    person["confidence_explanation"] = conf_result["breakdown"]
    print(f"[CONFIDENCE] Breakdown: {conf_result['breakdown']}")

    return person, method_used


def calculate_confidence(person_object: dict, raw_documents: list = None) -> int:
    """
    Evidence-chain confidence scorer. Every point added has a logged reason.
    Every cap has a logged reason. Fully debuggable.
    """
    raw_documents    = safe_list(raw_documents)
    score            = 0
    evidence_chain   = []

    def add(points: int, reason: str):
        nonlocal score
        score += points
        evidence_chain.append(f"+{points}: {reason}")

    def cap(max_score: int, reason: str):
        nonlocal score
        if score > max_score:
            old   = score
            score = max_score
            evidence_chain.append(f"CAP {old}->{max_score}: {reason}")

    name = safe_str(person_object.get("confirmed_name", ""))
    if name and name not in ("Unknown Subject", "Unknown", "", "None"):
        add(20, "Name confirmed")

    phones = safe_list(person_object.get("phones_found", []))
    if len(phones) >= 5:
        add(20, f"{len(phones)} phones")
    elif len(phones) >= 3:
        add(15, f"{len(phones)} phones")
    elif len(phones) >= 1:
        add(10, f"{len(phones)} phone")

    emails = safe_list(person_object.get("emails_found", []))
    if emails:
        add(10, f"{len(emails)} emails")

    locs = safe_list(person_object.get("location_stated", []))
    if locs:
        add(8, f"{len(locs)} locations")

    platforms = safe_list(person_object.get("platforms_confirmed", []))
    if len(platforms) >= 3:
        add(15, "3+ platforms")
    elif len(platforms) >= 1:
        add(8, f"{len(platforms)} platform")

    doc_count = len(raw_documents)
    if doc_count >= 6:
        add(15, f"{doc_count} files")
    elif doc_count >= 4:
        add(12, f"{doc_count} files")
    elif doc_count >= 3:
        add(8, f"{doc_count} files")
    elif doc_count >= 2:
        add(5, f"{doc_count} files")

    assoc = safe_list(person_object.get("data_sources", []))
    if len(assoc) >= 4:
        add(10, "rich associations")
    elif len(assoc) >= 2:
        add(5, "some associations")

    gaps    = safe_list(person_object.get("data_gaps", []))
    penalty = min(len(gaps) * 2, 15)
    if penalty:
        score -= penalty
        evidence_chain.append(f"-{penalty}: {len(gaps)} gaps")

    # Hard caps — each with documented reason
    has_contact = bool(phones or emails or platforms)
    if not has_contact:
        cap(40, "no contact data confirmed")

    if doc_count == 1:
        cap(40, "single source only")
    if doc_count == 0:
        cap(5, "no documents")

    # Multi-source documents (≥3 files) count as a confirmed data type —
    # CDR/document-only investigations have no social platforms by design.
    confirmed_types = sum([
        1 if phones else 0,
        1 if emails else 0,
        1 if locs else 0,
        1 if platforms else 0,
        1 if doc_count >= 3 else 0,   # rich document corpus = evidence type
    ])
    if confirmed_types == 0:
        cap(20, "no data types")
    elif confirmed_types < 2:
        cap(40, f"only {confirmed_types} data type(s)")

    # Relax the platform cap for document-rich investigations
    if not platforms and doc_count < 3:
        cap(55, "no confirmed platforms and sparse docs")
    if doc_count <= 2:
        cap(60, f"only {doc_count} document(s)")

    final = max(safe_int(score), 0)

    # Store evidence chain in person object for debugging
    person_object["confidence_evidence"] = evidence_chain
    print(f"[CONFIDENCE] {final}/100 — {evidence_chain}")

    return final


def calculate_stable_confidence(
    num_files: int,
    num_phones: int,
    num_timeline: int,
    num_graph_nodes: int,
    num_gaps: int,
    num_emails: int = 0,
    num_locations: int = 0,
    num_platforms: int = 0,
) -> dict:
    """
    Dynamic evidence-based confidence engine. Starts at 0, builds from evidence.
    Capped at 95. Rounded to nearest 2.
    Returns {"confidence": int, "breakdown": str}.

    `num_platforms` (confirmed online presence) lets OSINT / live-search subjects
    score on their actual evidence. The model is otherwise document-centric, so
    without it a fully-resolved web subject (e.g. a confirmed GitHub account) with
    zero uploaded files scored 0 — falsely implying "no evidence". Defaults to 0
    so every document-mode caller is unaffected.
    """
    score = 0

    # Sources bonus (tiered)
    if num_files >= 5:
        src_bonus = 48
    elif num_files >= 3:
        src_bonus = 35
    elif num_files >= 2:
        src_bonus = 20
    elif num_files >= 1:
        src_bonus = 10
    else:
        src_bonus = 0
    score += src_bonus

    # Phone bonus (tiered)
    if num_phones >= 3:
        phone_bonus = 14
    elif num_phones >= 2:
        phone_bonus = 10
    elif num_phones >= 1:
        phone_bonus = 5
    else:
        phone_bonus = 0
    score += phone_bonus

    # Email bonus
    email_bonus = 8 if num_emails >= 1 else 0
    score += email_bonus

    # Location bonus (tiered)
    if num_locations >= 3:
        loc_bonus = 8
    elif num_locations >= 1:
        loc_bonus = 4
    else:
        loc_bonus = 0
    score += loc_bonus

    # Platform bonus (tiered) — confirmed online presence (OSINT evidence)
    if num_platforms >= 4:
        platform_bonus = 28
    elif num_platforms >= 3:
        platform_bonus = 22
    elif num_platforms >= 2:
        platform_bonus = 14
    elif num_platforms >= 1:
        platform_bonus = 8
    else:
        platform_bonus = 0
    score += platform_bonus

    # Timeline bonus (tiered)
    if num_timeline >= 20:
        timeline_bonus = 14
    elif num_timeline >= 10:
        timeline_bonus = 8
    elif num_timeline >= 1:
        timeline_bonus = 4
    else:
        timeline_bonus = 0
    score += timeline_bonus

    # Graph bonus (tiered)
    if num_graph_nodes >= 10:
        graph_bonus = 10
    elif num_graph_nodes >= 5:
        graph_bonus = 6
    elif num_graph_nodes >= 1:
        graph_bonus = 3
    else:
        graph_bonus = 0
    score += graph_bonus

    # Gap penalty: 2 pts each, max 15
    gap_penalty = min(num_gaps * 2, 15)
    score -= gap_penalty

    final_score = max(0, min(95, score))
    final_score = round(final_score / 2) * 2

    breakdown = (
        f"{num_files} source file(s) [+{src_bonus}], "
        f"{num_phones} phone(s) [+{phone_bonus}], "
        f"{num_emails} email(s) [+{email_bonus}], "
        f"{num_locations} location(s) [+{loc_bonus}], "
        f"{num_platforms} platform(s) [+{platform_bonus}], "
        f"{num_timeline} timeline event(s) [+{timeline_bonus}], "
        f"{num_graph_nodes} graph node(s) [+{graph_bonus}], "
        f"{num_gaps} data gap(s) [-{gap_penalty}]"
    )

    return {"confidence": final_score, "breakdown": breakdown}


def calculate_evidence_based_confidence(
    num_files: int,
    num_entities: int = 0,
    num_phones: int = 0,
    num_timeline_events: int = 0,
    num_gaps: int = 0,
    contradiction_count: int = 0,
    graph_node_count: int = 0,
) -> dict:
    """
    Backward-compatibility shim — delegates to calculate_stable_confidence.
    Old callers reading 'identity_confidence' or 'explanation' still work.
    """
    result = calculate_stable_confidence(
        num_files       = num_files,
        num_phones      = num_phones,
        num_timeline    = num_timeline_events,
        num_graph_nodes = graph_node_count,
        num_gaps        = num_gaps,
    )
    score = result["confidence"]
    return {
        "identity_confidence": score,
        "confidence":          score,
        "breakdown":           result["breakdown"],
        "explanation":         result["breakdown"],
    }


def detect_data_gaps(person_object: dict, raw_documents: list = None) -> list:
    """
    Comprehensive data gap detection: checks all expected fields AND
    extracts explicitly stated gaps from document text.
    """
    import re as _re
    raw_documents = raw_documents or []
    gaps = []
    seen = set()

    def _add_gap(g: str):
        g = g.strip()
        if g and 3 < len(g) < 80 and g.lower() not in seen:
            seen.add(g.lower())
            gaps.append(g)

    EXPECTED_FIELDS = [
        ("phones_found",              "No verified phone number on record"),
        ("emails_found",              "No email address identified in documents"),
        ("platforms_confirmed",       "Social media presence unconfirmed — manual platform check recommended"),
        ("location_stated",           "No confirmed residential or operational address"),
        ("confirmed_linked_profiles", "Online identity unverified — cross-platform search pending"),
        ("join_dates",                "Platform account creation dates unknown"),
        ("github_data",               "Technical/developer profile (GitHub) not investigated"),
        ("news_appearances",          "No news or media appearances found — expand search to media databases"),
    ]
    for field, label in EXPECTED_FIELDS:
        val = person_object.get(field)
        if not val or val == [] or val == {} or val == "":
            _add_gap(label)

    # NOTE: Free-text extraction from document body is intentionally NOT done here.
    # Document body contains arbitrary content (surveillance logs, CDR rows, financial data)
    # that would inject raw fragments like "tenant" or "m Dubai Large cabin bag" as gap labels.
    # Gaps are derived ONLY from structured field presence checks above.

    return gaps if gaps else ["No specific gaps identified"]


# ── Permanent conflict detector ───────────────────────────────────────────────

_MAJOR_CITIES = {
    "mumbai", "delhi", "pune", "bengaluru", "hyderabad", "chandigarh",
    "kolkata", "chennai", "gurugram", "noida", "ahmedabad", "surat",
    "lucknow", "jaipur",
}

_DOB_CONTEXT_RE = re.compile(
    r"(?:dob|date.of.birth|born)[:\s]+([^\n,;]{5,30})",
    re.IGNORECASE,
)


def _is_platform_suffix(primary: str, variant: str) -> bool:
    """
    Returns True when the variant is just the primary name concatenated with
    social-platform words (or vice-versa), or the primary name repeated.
    Catches NER artefacts such as:
        "Harshvardhan Instagram"           → True  (exact platform suffix)
        "Harshvardhan Harshvardhan"        → True  (doubled name)
        "Harshvardhan Telegram Profile"    → True  (multi-word platform suffix)
        "Instagram Harshvardhan"           → True  (platform prefix)
        "Harshvardhan Gautam Instagram"    → True  (multi-token primary + platform)
    """
    p = primary.lower().strip()
    v = variant.lower().strip()

    if not p or not v:
        return False

    p_tokens = p.split()
    v_tokens = v.split()

    # 1. Exact doubled primary: "Harshvardhan Harshvardhan"
    if v == f"{p} {p}":
        return True
    # Multi-token doubled: ["a","b","a","b"]
    if (len(v_tokens) == 2 * len(p_tokens)
            and v_tokens[:len(p_tokens)] == p_tokens
            and v_tokens[len(p_tokens):] == p_tokens):
        return True

    # 2. Primary is a prefix of variant and every suffix token is a platform word
    if v.startswith(p + " "):
        suffix_tokens = v[len(p):].strip().split()
        if suffix_tokens and all(t in _PLATFORM_TOKEN_SET for t in suffix_tokens):
            return True

    # 3. Variant starts with a platform word, rest matches primary
    if v_tokens and v_tokens[0] in _PLATFORM_TOKEN_SET:
        rest = " ".join(v_tokens[1:])
        if rest == p:
            return True

    # 4. Remove ALL platform tokens from variant — if what remains equals primary.
    # Guard: only fire when at least one platform token was actually stripped;
    # otherwise good names ("Arjun Mehta" vs "arjun mehta") would always match.
    non_platform = [t for t in v_tokens if t not in _PLATFORM_TOKEN_SET]
    if len(non_platform) < len(v_tokens) and non_platform and " ".join(non_platform) == p:
        return True

    # 5. Legacy exact-match check for single platform word either side
    for platform in _PLATFORM_TOKEN_SET:
        if v == f"{p} {platform}" or v == f"{platform} {p}":
            return True

    return False


def detect_all_conflicts(
    raw_documents: list,
    primary_name: str,
    person_object: dict,
) -> list:
    """
    Permanent pipeline step — runs on every fusion job.
    Detects name variants, location conflicts, and DOB conflicts across sources.
    Injects findings directly into person_object["anomaly_flags"] and ["conflicts"].
    """
    conflicts: list = []

    # ── NAME CONFLICTS ────────────────────────────────────────────────────────
    primary_parts = set(safe_str(primary_name).lower().split())
    names_by_source: dict = {}
    for doc in safe_list(raw_documents):
        fname = safe_str(doc.get("filename", ""))
        ents  = doc.get("entities", {})
        names = safe_list(ents.get("names", [])) if isinstance(ents, dict) else []
        parsed = [
            safe_str(n.get("value", n) if isinstance(n, dict) else n)
            for n in names
        ]
        if parsed:
            names_by_source[fname] = parsed

    seen_variants: set = set()
    _primary_key = normalize_name_key(primary_name)
    for source, names in names_by_source.items():
        for name in names:
            # Case/whitespace-insensitive self-exclusion: "ARJUN MEHTA" is the
            # SAME identity as "Arjun Mehta" and must never raise a false
            # NAME_CONFLICT against the primary (Fix 4).
            if normalize_name_key(name) == _primary_key \
                    or normalize_name_key(name) in seen_variants:
                continue
            name_parts = set(name.lower().split())
            overlap    = primary_parts & name_parts
            # Minimum overlap threshold: a single shared surname (e.g., "Khan")
            # is not enough to declare a NAME_CONFLICT when the primary has
            # 2+ tokens.  Require ≥2 shared tokens for multi-word primaries so
            # that "Khan Ali" does not conflict with "Zafar Ahmed Khan".
            min_overlap = 2 if len(primary_parts) >= 2 else 1
            if overlap and len(overlap) >= min_overlap and len(name) > 4:
                # Suppress platform-name artefacts and double-name artefacts
                if _is_name_with_suffix(primary_name, name):
                    continue
                if _is_platform_suffix(primary_name, name):
                    continue

                # Explicit inline guards — belt-and-suspenders on top of helpers
                _primary_lower = primary_name.lower().strip()
                _variant_lower = name.lower().strip()
                _PLATFORM_NAMES = [
                    "instagram", "telegram", "twitter", "github",
                    "hugging face", "linkedin", "facebook", "youtube",
                    "reddit", "discord", "whatsapp", "signal",
                    "tiktok", "x.com",
                ]
                _skip = False
                for _p in _PLATFORM_NAMES:
                    if _variant_lower == _primary_lower + " " + _p:
                        _skip = True
                        break
                    if _variant_lower == _p + " " + _primary_lower:
                        _skip = True
                        break
                # Guard: doubled name e.g. "Harshvardhan Harshvardhan"
                if _variant_lower == _primary_lower + " " + _primary_lower:
                    _skip = True
                if _skip:
                    continue

                seen_variants.add(normalize_name_key(name))
                conflicts.append({
                    "type":     "NAME_CONFLICT",
                    "flag":     (
                        f"NAME CONFLICT: Primary='{primary_name}' vs Variant='{name}'"
                        f" — shared tokens: {overlap} — Source: {source}"
                    ),
                    "severity": "HIGH",
                })

    # ── LOCATION CONFLICTS ────────────────────────────────────────────────────
    cities_found: dict = {}
    for doc in safe_list(raw_documents):
        fname = safe_str(doc.get("filename", ""))
        locs  = safe_list(doc.get("locations", []))
        for loc in locs:
            loc_lower = safe_str(loc).lower()
            for city in _MAJOR_CITIES:
                if city in loc_lower:
                    cities_found.setdefault(city, []).append(fname)

    if len(cities_found) > 1:
        city_list = list(cities_found.keys())
        conflicts.append({
            "type":     "LOCATION_CONFLICT",
            "flag":     (
                f"LOCATION CONFLICT: Subject linked to multiple cities: "
                f"{', '.join(city_list)} -- verify current residence"
            ),
            "severity": "MEDIUM",
        })

    # ── DOB CONFLICTS ─────────────────────────────────────────────────────────
    dobs_by_source: dict = {}
    for doc in safe_list(raw_documents):
        fname   = safe_str(doc.get("filename", ""))
        text    = safe_str(doc.get("raw_text", "") or doc.get("full_text", ""))
        matches = _DOB_CONTEXT_RE.findall(text)
        if matches:
            dobs_by_source[fname] = matches

    if len(dobs_by_source) > 1:
        all_dobs = [
            f"{d.strip()} ({src})"
            for src, dobs in dobs_by_source.items()
            for d in dobs
        ]
        unique_dob_values = set(d.split(" (")[0].strip() for d in all_dobs)
        if len(unique_dob_values) > 1:
            conflicts.append({
                "type":     "DOB_CONFLICT",
                "flag":     (
                    f"DOB CONFLICT: Multiple dates of birth found: "
                    f"{'; '.join(all_dobs)}"
                ),
                "severity": "HIGH",
            })

    # Inject into person object
    person_object["conflicts"] = conflicts
    existing = safe_list(person_object.get("anomaly_flags", []))
    for c in conflicts:
        existing.append(c.get("flag", ""))
    person_object["anomaly_flags"] = existing

    if conflicts:
        print(f"[CONFLICTS] {len(conflicts)} found: {[c['type'] for c in conflicts]}")

    return conflicts


def resolve_entity_from_documents(
    subject_name: str,
    structured_rows: list,
    filename: str = "",
) -> tuple:
    """
    Build a Person Object from structured document rows (CSV/Excel).
    Tries Bedrock first, then Gemini, then local rule-based fallback.
    Returns (person_dict, method_used).
    """
    # Shared prompt builder
    sample = structured_rows[:200] if structured_rows else []
    data_payload = {
        "subject_hint": subject_name,
        "source_file":  filename,
        "rows":         sample,
    }
    prompt = FUSION_PROMPT.format(
        data_json=json.dumps(data_payload, indent=2, ensure_ascii=False)
    )

    def _parse_ai_response(raw_text: str, model_name: str):
        if not raw_text:
            return None, None
        parsed = _extract_json(raw_text)
        if not parsed:
            return None, None
        # Ensure confirmed_name is populated — only fall back to subject_name
        # if it is a real person name (not a legal category, filename, etc.)
        if not parsed.get("confirmed_name") or parsed["confirmed_name"] in ("", "Not found"):
            if subject_name and not is_bad_subject_name(subject_name):
                parsed["confirmed_name"] = subject_name
            else:
                parsed["confirmed_name"] = "Unknown Subject"
        for key, default in EMPTY_PERSON.items():
            if key not in parsed:
                parsed[key] = json.loads(json.dumps(default))
        if not isinstance(parsed.get("confidence_score"), (int, float)):
            parsed["confidence_score"] = 0

        # ── Set data_sources BEFORE confidence scoring so +10 bonus applies ──
        parsed["data_sources"] = parsed.get("data_sources") or ([filename] if filename else [])

        # ── Merge phones from structured_rows BEFORE confidence scoring ───────
        # AI may return sparse phones; structured_rows has every raw number.
        _doc_stub = [{"filename": filename, "structured_rows": structured_rows}] if structured_rows else (
            [{"filename": filename}] if filename else []
        )
        if structured_rows:
            row_phones = extract_all_phones(_doc_stub)
            if row_phones:
                existing_phones = set(safe_list(parsed.get("phones_found", [])))
                for p in row_phones:
                    if p not in existing_phones:
                        existing_phones.add(p)
                parsed["phones_found"] = list(existing_phones)

        # ── Apply hard caps — AI often inflates scores on sparse data ─────────
        ai_score = max(0, min(100, int(parsed["confidence_score"])))
        capped   = calculate_confidence(parsed, _doc_stub)
        parsed["confidence_score"] = min(ai_score, capped)

        # Ensure comprehensive gap detection
        if not parsed.get("data_gaps"):
            parsed["data_gaps"] = detect_data_gaps(parsed)
        return parsed, model_name

    # ── Try Bedrock (Claude Sonnet 4 · ap-south-1 · India) — PRIMARY ─────────
    if getattr(config, "bedrock_client", None) is not None:
        raw = _call_bedrock_for_fusion(prompt)
        person, method = _parse_ai_response(raw, "claude-sonnet-4-bedrock")
        if person:
            return person, method

    # ── Try Gemini ────────────────────────────────────────────────────────────
    gemini_key = config.GEMINI_API_KEY
    if gemini_key and gemini_key not in ("", "your_gemini_key_here"):
        raw = _call_gemini(prompt)
        person, method = _parse_ai_response(raw, "gemini")
        if person:
            return person, method

    # ── Local rule-based fallback ─────────────────────────────────────────────
    return _local_resolve_from_rows(subject_name, structured_rows, filename), "local-fallback"


# ── Main resolution entry point ────────────────────────────────────────────────

def resolve_entity(query: str, search_results: dict) -> dict:
    """
    Build a Person Object from raw search results.
    Uses Gemini if API key is available; falls back to local rule-based resolver.
    Returns (person_dict, method_used).
    """
    gemini_key = config.GEMINI_API_KEY
    use_gemini = gemini_key and gemini_key != "your_gemini_key_here"

    if use_gemini:
        prompt   = _build_prompt(query, search_results)
        raw_text = _call_gemini(prompt)
        if raw_text:
            parsed = _extract_json(raw_text)
            if parsed:
                # Ensure all required keys are present (backfill with defaults)
                for key, default in EMPTY_PERSON.items():
                    if key not in parsed:
                        parsed[key] = json.loads(json.dumps(default))
                # Enforce types
                if not isinstance(parsed.get("confidence_score"), (int, float)):
                    parsed["confidence_score"] = 0
                parsed["confidence_score"] = max(0, min(100, int(parsed["confidence_score"])))
                return parsed, "gemini"

    # Fallback
    person = _local_resolve(query, search_results)
    return person, "local"


# ── Convenience wrapper ────────────────────────────────────────────────────────

def build_person_profile(query: str, search_results: dict) -> tuple[dict, str]:
    """
    Public API: build and return (person_object, resolution_method).
    After initial resolution, automatically runs cross-platform discovery:
      - find_linked_profiles()          -> confirmed + potential linked accounts
      - extract_linkedin_intelligence() -> emails, phones, social links from LinkedIn
    All results are merged into the Person Object.
    """
    person, method = resolve_entity(query, search_results)

    # ── Cross-platform discovery ───────────────────────────────────────────────
    try:
        from modules.search import find_linked_profiles, extract_linkedin_intelligence

        linked = find_linked_profiles(person)
        person["confirmed_linked_profiles"] = linked.get("confirmed_linked", [])
        person["potential_linked_profiles"] = linked.get("potential_linked", [])

        disc = linked.get("discovery_summary", {})
        person["cross_platform_summary"] = {
            "total_confirmed":   len(person["confirmed_linked_profiles"]),
            "total_potential":   len(person["potential_linked_profiles"]),
            "platforms_present": list(
                set(person.get("platforms_confirmed", []))
                | {c["platform"] for c in person["confirmed_linked_profiles"]}
            ),
            "discovery_method": [
                m for m in ["username_propagation", "name_search", "bio_crossref"]
                if disc.get("platforms_checked") or disc.get("platforms_confirmed")
            ],
        }

        # ── LinkedIn intelligence ──────────────────────────────────────────────
        li_url  = person.get("profile_urls", {}).get("LinkedIn", "")
        li_user = person.get("usernames", {}).get("LinkedIn", "")
        if not li_url and li_user:
            li_url = f"https://www.linkedin.com/in/{li_user}/"

        if li_url:
            m = __import__("re").search(r"linkedin\.com/in/([^/?#\s]+)", li_url)
            if m:
                li_intel = extract_linkedin_intelligence(m.group(1).rstrip("/"))
                person["linkedin_intelligence"] = li_intel
                # Merge emails, phones, websites
                for email in li_intel.get("emails_found", []):
                    if email not in person["emails_found"]:
                        person["emails_found"].append(email)
                for phone in li_intel.get("phones_found", []):
                    if phone not in person["phones_found"]:
                        person["phones_found"].append(phone)
                if li_intel.get("website_found"):
                    w = li_intel["website_found"]
                    if w not in person["websites_found"]:
                        person["websites_found"].append(w)

    except Exception:
        pass  # Discovery is non-fatal — profile is still valid without it

    # ── Account timeline analysis ──────────────────────────────────────────────
    try:
        from modules.account_timeline import build_account_timeline
        timeline_result = build_account_timeline(person)
        person["account_timeline"]       = timeline_result.get("timeline", [])
        person["oldest_account"]         = timeline_result.get("oldest_account", {})
        person["newest_account"]         = timeline_result.get("newest_account", {})
        person["account_creation_flags"] = timeline_result.get("flags", [])
        person["digital_age_years"]      = timeline_result.get("digital_age_years", 0)
    except Exception:
        pass  # Timeline is non-fatal

    return person, method
