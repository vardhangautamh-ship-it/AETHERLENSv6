"""
AetherLens — Data Ingestion Module
Upload, parse, and normalize CSV / Excel / PDF / plain-text documents.
Mandatory lawful authorization declaration logged on every upload.
"""

import re
import io
import json
import sqlite3
import datetime
from pathlib import Path

import pandas as pd
import pdfplumber

import config
from modules.sanitizer import most_common_by_key

# ── File-type guard ────────────────────────────────────────────────────────────

SKIP_EXTENSIONS = [
    ".json",
    ".log",
    ".md",
    ".py",
    ".js",
    ".html",
    ".xml",
    ".zip",
    ".rar",
    ".tar",
    ".gz",
]


def should_skip_file(filename: str) -> bool:
    """
    Return True for file types that are never valid intelligence documents.
    Prevents JSON configs, log dumps, source code, and archives from
    accidentally entering the ingestion pipeline.
    """
    return Path(filename).suffix.lower() in SKIP_EXTENSIONS


# ── Encoding helpers ───────────────────────────────────────────────────────────

def clean_text_encoding(text) -> str:
    """
    Sanitise any string (or bytes) so it survives a cp1252 / ascii write.
    Converts bytes -> str, then round-trips through utf-8 with 'replace' so
    every codepoint is valid UTF-8.  Returns '' for None / non-string input.
    """
    if text is None:
        return ""
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    if not isinstance(text, str):
        try:
            text = str(text)
        except Exception:
            return ""
    # Round-trip: strips/replaces anything that can't be encoded cleanly
    return text.encode("utf-8", errors="replace").decode("utf-8", errors="replace")


def read_csv_safe(filepath_or_buffer, **kwargs):
    """
    Drop-in replacement for pd.read_csv() that survives cp1252 / utf-8 / latin-1
    mismatches.  Tries encodings in order; last resort uses utf-8 with replacement.

    Works with both file paths (str/Path) and file-like objects (e.g.
    Streamlit UploadedFile / io.BytesIO).
    """
    encodings = ["utf-8", "utf-8-sig", "latin-1", "cp1252", "iso-8859-1"]

    # For file-like objects we must re-wrap bytes each attempt because
    # pd.read_csv advances the stream pointer on failure.
    is_bytes_io = hasattr(filepath_or_buffer, "read")
    raw_bytes = None
    if is_bytes_io:
        raw_bytes = filepath_or_buffer.read()
        # reset in case caller re-uses the buffer
        if hasattr(filepath_or_buffer, "seek"):
            try:
                filepath_or_buffer.seek(0)
            except Exception:
                pass

    for enc in encodings:
        try:
            if raw_bytes is not None:
                return pd.read_csv(io.BytesIO(raw_bytes), encoding=enc, **kwargs)
            else:
                return pd.read_csv(filepath_or_buffer, encoding=enc, **kwargs)
        except (UnicodeDecodeError, UnicodeError):
            continue
        except Exception:
            raise  # non-encoding errors bubble up immediately

    # Last resort — replace bad bytes
    if raw_bytes is not None:
        clean = raw_bytes.decode("utf-8", errors="replace")
        return pd.read_csv(io.StringIO(clean), **kwargs)
    else:
        with open(filepath_or_buffer, "rb") as fh:
            clean = fh.read().decode("utf-8", errors="replace")
        return pd.read_csv(io.StringIO(clean), **kwargs)


# ── Regex extraction patterns ──────────────────────────────────────────────────

# Phone patterns — ordered from most specific to most general
# Covers: +91-XXXXX-XXXXX (India), +92-XXX-XXXXXXX (Pakistan),
#          +country-subscriber, plain 10-digit Indian mobiles
_PHONE_PATTERNS = [
    re.compile(r"\+91[-\s]?\d{5}[-\s]?\d{5}\b"),           # India: +91-97420-11834
    re.compile(r"\+92[-\s]?\d{3}[-\s]?\d{7}\b"),            # Pakistan: +92-300-4471829
    re.compile(r"\+\d{1,3}[-\s]?\(?\d{1,4}\)?[-\s]?\d{3,5}[-\s]?\d{4,6}\b"),  # Intl generic
    re.compile(r"\b[6-9]\d{9}\b"),                           # Indian mobile: 10-digit
    re.compile(r"\b\d{3,5}[-\s]\d{6,8}\b"),                 # local formats
]

RE_PHONE = re.compile(
    r"(?:\+?\d[\d\s\-().]{7,}\d)",
    re.IGNORECASE,
)

RE_EMAIL = re.compile(
    r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b",
)

RE_DATE = re.compile(
    r"\b(?:\d{4}[-/]\d{1,2}[-/]\d{1,2}"
    r"|\d{1,2}[-/]\d{1,2}[-/]\d{4}"
    r"|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}"
    r"|\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{4}"
    r")\b",
    re.IGNORECASE,
)

RE_ADDRESS = re.compile(
    r"\b\d{1,5}\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3}"
    r"(?:\s+(?:St|Street|Ave|Avenue|Rd|Road|Blvd|Boulevard|Lane|Ln|Dr|Drive|Court|Ct|Place|Pl|Way)\.?)?"
    r"(?:,?\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)?"
    r"(?:,?\s+[A-Z]{2}\s+\d{5}(?:-\d{4})?)?",
    re.IGNORECASE,
)

# Canonical free-text name scanner. \s+ would let a newline bridge two words,
# producing "Zafar Ahmed Khan\nCase"; [ \t]+ restricts the inter-word gap to
# space/tab so a match never crosses a line boundary. Single source of truth —
# also used by extract_primary_subject_from_text's frequency pass below.
RE_NAME = re.compile(
    r"\b([A-Z][a-z]{1,20}(?:[ \t]+[A-Z][a-z]{1,20}){1,3})\b"
)

# Common words to exclude from name candidates
NAME_STOPWORDS = {
    "January", "February", "March", "April", "June", "July", "August",
    "September", "October", "November", "December", "Monday", "Tuesday",
    "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
    "North", "South", "East", "West", "New", "United", "States", "Street",
    "Avenue", "Road", "Boulevard", "Drive", "Lane", "Court", "Place",
    "Inc", "Ltd", "Corp", "Company", "University", "College", "School",
    "Department", "Ministry", "The", "And", "Or", "In", "At", "Of",
    "To", "For", "With", "This", "That", "From", "By", "On",
    # Location / infrastructure words that look like names in Indian documents
    # but are never part of a person's name — prevents "Bank Nariman Point",
    # "Bandra Worli Sea Link", "Andheri Station" etc. from being extracted as subjects.
    "Bank", "Branch", "Point", "Link", "Bridge", "Sea", "Bay", "Port",
    "Station", "Airport", "Highway", "Flyover", "Junction", "Naka",
    "Tower", "Plaza", "Mall", "Complex", "Centre", "Center",
    "Park", "Garden", "Market", "Masjid", "Mandir", "Chowk",
    "Marg", "Bandra", "Worli", "Nariman", "Andheri", "Borivali",
    "Colaba", "Kurla", "Dharavi", "Dadar", "Fort", "Linking",
    "Juhu", "Kandivali", "Malad", "Goregaon", "Mulund", "Thane",
    "Navi", "Powai", "Vikhroli", "Ghatkopar", "Sion", "Wadala",
    "Connaught", "Lodhi", "Karol", "Nehru", "Rajiv", "Indira",
    "Sector", "Phase", "Block", "Zone", "Wing", "Building", "Floor",
    # Major Indian city names — never appear as components of a person's name.
    # "Mumbai Speeding", "Delhi Police" etc. in challan/CDR text would otherwise
    # pass the regex filter and be mis-typed as person nodes in the graph.
    "Mumbai", "Delhi", "Chennai", "Kolkata", "Hyderabad", "Bengaluru",
    "Pune", "Nashik", "Nagpur", "Ahmedabad", "Surat", "Jaipur", "Lucknow",
    # Traffic / legal violation terms that appear capitalised in challan CSVs
    # and match the name regex (e.g. "Speeding", "Overloading")
    "Speeding", "Overspeed", "Overspeeding", "Overloading", "Violation",
    "Challan", "Jumping", "Parking",
    # Operation / investigation title words — never part of a human name
    "Cyber", "Incident", "Inquiry", "Investigation", "Operation",
    "Ghostwire", "Jupiter", "Sector", "Case",
}


def _normalize_name_match(raw: str) -> str | None:
    """Clean a single RE_NAME capture before it becomes an entity or subject.

    RE_NAME must keep its [ \\t]+ inter-word gap (single-spacing would drop the
    legitimate double-spaced PDF artifact 'Arjun  Mehta'), so it cannot, by
    itself, stop an adjacent CSV column from bleeding into the capture. This is
    the single place every RE_NAME match is cleaned, fixing two confirmed
    column-bleed artifacts:

      * violation/label column bleed  ->  'Abbas Qureshi Overspeeding'
      * doubled-name (column restart) ->  'Farhan Abbas Qureshi Farhan'

    Strips trailing noise tokens (NAME_STOPWORDS — cities, violations, labels)
    and collapses a trailing token that merely repeats an earlier one. Returns
    the cleaned 2-4 word name, or None if nothing valid survives.
    """
    words = raw.split()
    # Drop trailing noise tokens (e.g. a violation column that bled onto the end).
    while len(words) > 2 and words[-1] in NAME_STOPWORDS:
        words.pop()
    # Collapse a doubled name: a trailing token repeating an earlier token
    # (case-insensitive) is a column-restart artifact, not part of the name.
    while len(words) > 2 and words[-1].lower() in {w.lower() for w in words[:-1]}:
        words.pop()
    if len(words) < 2:
        return None
    # Any remaining stopword (interior bleed) means this was never a clean name.
    if any(w in NAME_STOPWORDS for w in words):
        return None
    return " ".join(words)

# Values that look like names but are actually sheet/column/metadata labels
FUSION_NAME_SKIPLIST = {
    "Location Timeline", "Date Time", "City State", "Activity Type",
    "Work Entry", "NexaTech", "Not Found", "Not found", "Unknown",
    "HIGH", "MEDIUM", "LOW", "Call Records", "Call Duration",
    "Caller Name", "Receiver Name", "Full Name", "Subject Name",
    "Sheet Name", "Column Header", "Data Entry", "Log Entry",
    "Entry Type", "Record Type", "File Name",
}

# Columns that contain real person names in structured data
NAME_COLUMNS = {
    "name", "subject", "person", "caller_name", "receiver_name",
    "full_name", "contact_name", "person_name", "subject_name",
    "caller", "receiver", "contact",
    "associate", "known_contact", "relative_name", "relative",
}

# ── Subject / flag / location extractors ──────────────────────────────────────

from collections import Counter

DOCUMENT_SKIP_LIST = [
    "field officer report", "field intelligence note", "intelligence report",
    "background profile document", "surveillance log", "activity log",
    "case file", "subject file", "aetherlens", "restricted", "classification",
    "authorized", "data completeness warning", "warning", "field officer",
    "field officer unit", "observer", "section", "page", "not found",
    "unknown", "confirmed", "unconfirmed", "case ref", "source",
    "ed mum", "ncb ggn", "ncb mum",
    # Operation / case title words — never part of a person's name
    "cyber", "incident", "inquiry", "investigation", "operation",
    "ghostwire", "jupiter", "sector", "document",
]

PLACE_SKIP_LIST = [
    "road", "street", "nagar", "colony", "marg", "lane", "chowk", "bazaar",
    "masjid", "mandir", "station", "airport", "market", "area", "building",
    "flat", "plot", "sector", "phase", "midc", "chs", "society",
    "mumbai", "delhi", "gurugram", "pune", "nashik", "bengaluru", "hyderabad",
    "mohammed ali", "minara", "dadar", "kurla", "dharavi",
    "cyber city", "cyber hub", "connaught", "lodhi",
    # Additional location/infrastructure words — prevents "Bank Nariman Point",
    # "Bandra Worli Sea Link" etc. from being picked as the primary subject
    "bank", "branch", "point", "link", "bridge", "sea", "bay", "port",
    "bandra", "worli", "nariman", "andheri", "borivali", "colaba",
    "tower", "plaza", "mall", "complex", "centre", "center",
    "park", "garden", "junction", "highway", "flyover", "naka",
    "fort", "linking", "juhu", "kandivali", "malad", "goregaon",
    "mulund", "thane", "powai", "vikhroli", "ghatkopar", "sion", "wadala",
]

_LABEL_PATTERNS = [
    re.compile(r'(?:Full[ \t]+)?Name[ \t]*[:\|][ \t]*([A-Z][a-z]{1,20}(?:[ \t]+[A-Z][a-z]{1,20})+)'),
    re.compile(r'Subject[ \t]*[A-Z]?[ \t]*[:\|—\-][ \t]*(?:PRIMARY[ \t]*)?([A-Z][a-z]{1,20}(?:[ \t]+[A-Z][a-z]{1,20})+)'),
    re.compile(r'PRIMARY[ \t]+SUBJECT[ \t]*[:\|][ \t]*([A-Z][a-z]{1,20}(?:[ \t]+[A-Z][a-z]{1,20})+)'),
    re.compile(r'SUBJECT[ \t]+(?:NAME[ \t]*)?[:\|][ \t]*([A-Z][a-z]{1,20}(?:[ \t]+[A-Z][a-z]{1,20})+)', re.IGNORECASE),
    re.compile(r'Suspect[ \t]*[:\|][ \t]*([A-Z][a-z]{1,20}(?:[ \t]+[A-Z][a-z]{1,20})+)'),
    re.compile(r'Target[ \t]*[:\|][ \t]*([A-Z][a-z]{1,20}(?:[ \t]+[A-Z][a-z]{1,20})+)'),
]

_LOCATION_PATTERNS = [
    re.compile(
        r'[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\s+'
        r'(?:Road|Street|Marg|Lane|Chowk|Bazaar|Area|Market|Masjid|Station|Airport)\s*'
        r'(?:[,]\s*(?:Mumbai|Delhi|Gurugram))?',
        re.IGNORECASE,
    ),
    re.compile(
        r'\b(?:Mohammed Ali Road|Minara Masjid|Dharavi|Kurla|Dadar|Turbhe MIDC|'
        r'Cyber City|Cyber Hub|Khan Market|Lodhi Road|NH-48|Khopoli)\b',
        re.IGNORECASE,
    ),
    re.compile(
        r'(?:Mumbai|Delhi|Gurugram|Pune|Nashik|Navi Mumbai|Bengaluru|Hyderabad)',
        re.IGNORECASE,
    ),
]

_FLAG_RE = re.compile(r'\[FLAG\]\s*(.+?)(?:\n|$)', re.IGNORECASE)


def extract_subject_name(text: str) -> str | None:
    """
    Extract primary subject name from document free text.
    Priority: explicit label patterns first, then frequency analysis.
    Returns None if nothing passes validation.
    """
    # PRIORITY 1 — Explicit labels
    for pat in _LABEL_PATTERNS:
        m = pat.search(text)
        if m:
            candidate = m.group(1).strip()
            cl = candidate.lower()
            if not any(s in cl for s in DOCUMENT_SKIP_LIST + PLACE_SKIP_LIST) and len(candidate) > 4:
                print("[ENTITY] Subject via label:", candidate)
                return candidate

    # PRIORITY 2 — Frequency analysis
    # Skip form-field captions ("Student Name:", "Account Holder:") — a capitalised
    # phrase immediately followed by ':' or '|' is a label, not the subject.
    candidates = []
    for m in RE_NAME.finditer(text):
        if text[m.end():m.end() + 4].lstrip()[:1] in (":", "|"):
            continue
        name = _normalize_name_match(m.group(1))
        if name:
            candidates.append(name)
    filtered = [
        n for n in candidates
        if not any(s in n.lower() for s in DOCUMENT_SKIP_LIST)
        and not any(p in n.lower() for p in PLACE_SKIP_LIST)
        and 5 < len(n) < 45
        and "\n" not in n and "\r" not in n   # belt-and-suspenders: reject any cross-line artifact
    ]
    if not filtered:
        return None

    # Normalize (case + whitespace) BEFORE counting so variants of one name
    # ("Arjun Mehta" / "ARJUN MEHTA" / "Arjun  Mehta") vote together (Fix 3).
    top = most_common_by_key(filtered)[:5]
    print("[ENTITY] Top name candidates:", top)
    best = top[0][0] if top else None
    if best:
        best = best.replace("\n", " ").replace("\r", " ").strip()
    return best or None


def extract_flags_from_text(text: str, source: str) -> list[dict]:
    """Extract [FLAG] tagged lines from document text."""
    flags = []
    for m in _FLAG_RE.finditer(str(text)):
        flag_text = m.group(1).strip()
        if flag_text:
            flags.append({"flag": flag_text, "source": source, "type": "DOCUMENT_FLAG"})
    return flags


def extract_locations_from_text(text: str) -> list[str]:
    """Extract location mentions from document text."""
    locations = []
    for pat in _LOCATION_PATTERNS:
        for m in pat.findall(str(text)):
            loc = m.strip() if isinstance(m, str) else str(m).strip()
            if len(loc) > 3:
                locations.append(loc)
    return list(set(locations))


def extract_primary_subject_from_bytes(file_bytes: bytes, suffix: str) -> str:
    """
    Parse Excel/CSV as structured data and find the most frequently
    appearing real person name from actual data rows (never from sheet
    names or column headers).
    Returns the primary subject name, or empty string if not found.
    """
    try:
        dfs = []
        if suffix in (".xlsx", ".xls"):
            xl = pd.ExcelFile(io.BytesIO(file_bytes))
            for sheet in xl.sheet_names:
                dfs.append(xl.parse(sheet, dtype=str, keep_default_na=False))
        elif suffix == ".csv":
            dfs.append(read_csv_safe(io.BytesIO(file_bytes), dtype=str, keep_default_na=False))
        else:
            return ""

        name_values: list = []
        # Canonical anchored single-cell matcher (single source of truth shared
        # with relationship_mapper); imported locally to avoid a top-level cycle.
        from modules.entity_resolution import RE_PERSON_NAME_CELL as name_re

        for df in dfs:
            # Normalise column names for matching
            col_map = {c: c.lower().strip().replace(" ", "_") for c in df.columns}
            for col in df.columns:
                norm_col = col_map[col]
                if norm_col not in NAME_COLUMNS:
                    continue
                for val in df[col].dropna():
                    val = str(val).strip()
                    if not val or val in FUSION_NAME_SKIPLIST:
                        continue
                    # Must match two-or-more capitalised words
                    if not name_re.match(val):
                        continue
                    words = val.split()
                    if any(w in NAME_STOPWORDS for w in words):
                        continue
                    name_values.append(val)

        # Aggregate by normalized key (case + whitespace) before picking the
        # most frequent name so variants do not split the vote (Fix 3).
        ranked = most_common_by_key(name_values)
        if not ranked:
            return ""
        best = ranked[0][0]
        return best.replace("\n", " ").replace("\r", " ").strip()
    except Exception:
        return ""

RELATIONSHIP_KEYWORDS = [
    "married to", "works for", "works with", "employed by", "related to",
    "associated with", "connected to", "partner of", "brother of", "sister of",
    "daughter of", "son of", "mother of", "father of", "friend of",
    "colleague of", "lives with", "resides with", "director of", "ceo of",
    "owner of", "member of", "reported to", "supervised by",
]

RE_RELATIONSHIP = re.compile(
    r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s+(?:" +
    "|".join(re.escape(k) for k in RELATIONSHIP_KEYWORDS) +
    r")\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)",
    re.IGNORECASE,
)

COUNTRIES = {
    "Afghanistan", "Albania", "Algeria", "Argentina", "Australia", "Austria",
    "Belgium", "Bolivia", "Brazil", "Canada", "Chile", "China", "Colombia",
    "Croatia", "Czech Republic", "Denmark", "Egypt", "Finland", "France",
    "Germany", "Ghana", "Greece", "Hungary", "India", "Indonesia", "Iran",
    "Iraq", "Ireland", "Israel", "Italy", "Japan", "Jordan", "Kenya",
    "Mexico", "Morocco", "Netherlands", "New Zealand", "Nigeria", "Norway",
    "Pakistan", "Peru", "Philippines", "Poland", "Portugal", "Romania",
    "Russia", "Saudi Arabia", "South Africa", "South Korea", "Spain",
    "Sweden", "Switzerland", "Syria", "Thailand", "Turkey", "Ukraine",
    "United Kingdom", "United States", "Venezuela", "Vietnam",
}

RE_LOCATION = re.compile(
    r"\b(" + "|".join(re.escape(c) for c in COUNTRIES) + r")\b",
    re.IGNORECASE,
)

US_STATES = {
    "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado",
    "Connecticut", "Delaware", "Florida", "Georgia", "Hawaii", "Idaho",
    "Illinois", "Indiana", "Iowa", "Kansas", "Kentucky", "Louisiana",
    "Maine", "Maryland", "Massachusetts", "Michigan", "Minnesota",
    "Mississippi", "Missouri", "Montana", "Nebraska", "Nevada",
    "New Hampshire", "New Jersey", "New Mexico", "New York",
    "North Carolina", "North Dakota", "Ohio", "Oklahoma", "Oregon",
    "Pennsylvania", "Rhode Island", "South Carolina", "South Dakota",
    "Tennessee", "Texas", "Utah", "Vermont", "Virginia", "Washington",
    "West Virginia", "Wisconsin", "Wyoming",
}

RE_US_STATE = re.compile(
    r"\b(" + "|".join(re.escape(s) for s in US_STATES) + r")\b"
)


# ── Text extractors ────────────────────────────────────────────────────────────

def _extract_text_from_csv(file_bytes: bytes, filename: str) -> str:
    try:
        df = read_csv_safe(io.BytesIO(file_bytes), dtype=str, keep_default_na=False)
        return df.to_string(index=False)
    except Exception as e:
        return f"CSV parse error: {e}"


def _extract_text_from_excel(file_bytes: bytes, filename: str) -> str:
    try:
        xl = pd.ExcelFile(io.BytesIO(file_bytes))
        parts = []
        for sheet_name in xl.sheet_names:
            df = xl.parse(sheet_name, dtype=str, keep_default_na=False)
            parts.append(f"--- Sheet: {sheet_name} ---\n{df.to_string(index=False)}")
        return "\n\n".join(parts)
    except Exception as e:
        return f"Excel parse error: {e}"


def _extract_text_from_pdf(file_bytes: bytes, filename: str) -> str:
    try:
        text_parts = []
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            for i, page in enumerate(pdf.pages, 1):
                text = page.extract_text() or ""
                text = clean_text_encoding(text)   # strip/replace non-utf-8 chars
                if text.strip():
                    text_parts.append(f"--- Page {i} ---\n{text}")
        return "\n\n".join(text_parts) if text_parts else "No text extracted from PDF."
    except Exception as e:
        return f"PDF parse error: {e}"


def _extract_text_from_txt(file_bytes: bytes, filename: str) -> str:
    for enc in ("utf-8", "utf-16", "latin-1", "cp1252"):
        try:
            return file_bytes.decode(enc)
        except Exception:
            continue
    return file_bytes.decode("utf-8", errors="replace")


FILE_EXTRACTORS = {
    ".csv":  _extract_text_from_csv,
    ".xlsx": _extract_text_from_excel,
    ".xls":  _extract_text_from_excel,
    ".pdf":  _extract_text_from_pdf,
    ".txt":  _extract_text_from_txt,
    ".text": _extract_text_from_txt,
}

SUPPORTED_TYPES = list(FILE_EXTRACTORS.keys())


# ── Entity extractors ──────────────────────────────────────────────────────────

def _extract_phones(text: str) -> list[dict]:
    """
    Extract phone numbers using specific + generic patterns.
    Specific patterns (India/Pakistan) take priority; generic catches the rest.
    """
    # Single phone validator — see entity_resolution.is_valid_phone
    from modules.entity_resolution import is_valid_phone

    found = []
    seen  = set()

    def _add(m, pattern_name: str = "generic"):
        raw    = m.group().strip()
        digits = re.sub(r"[^\d]", "", raw)
        if len(digits) < 7 or len(digits) > 15:
            return
        # Reject order IDs, IP fragments, ISP data-volume figures, CDR fragments
        if not is_valid_phone(raw):
            return
        norm = digits  # normalised key to prevent duplicates across patterns
        if norm in seen:
            return
        seen.add(norm)
        found.append({
            "value":    raw,
            "type":     "phone",
            "ambiguous": len(digits) < 10,
            "context":  text[max(0, m.start()-30):m.end()+30].strip(),
            "source_pattern": pattern_name,
        })

    # Run specific patterns first
    for pat in _PHONE_PATTERNS:
        for m in pat.finditer(text):
            _add(m, pat.pattern[:20])

    # Generic fallback for anything the specific patterns missed
    for m in RE_PHONE.finditer(text):
        _add(m, "generic")

    return found


def _extract_emails(text: str) -> list[dict]:
    found = []
    seen  = set()
    for m in RE_EMAIL.finditer(text):
        raw = m.group()
        if raw in seen:
            continue
        seen.add(raw)
        found.append({
            "value":    raw,
            "type":     "email",
            "ambiguous": False,
            "context":  text[max(0, m.start()-30):m.end()+30].strip(),
        })
    return found


def _extract_dates(text: str) -> list[dict]:
    found = []
    seen  = set()
    for m in RE_DATE.finditer(text):
        raw = m.group().strip()
        if raw in seen:
            continue
        seen.add(raw)
        found.append({
            "value":    raw,
            "type":     "date",
            "ambiguous": False,
            "context":  text[max(0, m.start()-40):m.end()+40].strip(),
        })
    return found


def _extract_addresses(text: str) -> list[dict]:
    found = []
    seen  = set()
    for m in RE_ADDRESS.finditer(text):
        raw = m.group().strip()
        if len(raw) < 10 or raw in seen:
            continue
        seen.add(raw)
        found.append({
            "value":    raw,
            "type":     "address",
            "ambiguous": True,
            "context":  text[max(0, m.start()-20):m.end()+40].strip(),
        })
    return found[:20]  # cap to avoid noise


def _extract_names(text: str) -> list[dict]:
    found = []
    seen  = set()
    for m in RE_NAME.finditer(text):
        raw   = m.group().strip()
        # Structural caption guard: a capitalised phrase immediately followed by
        # ':' or '|' is a FORM-FIELD CAPTION ("Student Name:", "Account Holder:",
        # "Father's Name:", "Nominee:") — never the person. The actual value
        # follows the caption and is captured as its own match, so dropping the
        # caption keeps the name. This removes the whole class of label noise
        # without enumerating individual label words.
        if text[m.end():m.end() + 4].lstrip()[:1] in (":", "|"):
            continue
        # Strip column-bleed (violation/label tails, doubled names) and filter
        # stopwords / short single words via the single canonical normalizer.
        name = _normalize_name_match(raw)
        if not name or name in seen:
            continue
        seen.add(name)
        found.append({
            "value":    name,
            "type":     "name",
            "ambiguous": len(name.split()) == 2,
            "context":  text[max(0, m.start()-30):m.end()+30].strip(),
        })
    return found


def _extract_locations(text: str) -> list[dict]:
    found = []
    seen  = set()
    for m in list(RE_LOCATION.finditer(text)) + list(RE_US_STATE.finditer(text)):
        raw = m.group().strip()
        if raw in seen:
            continue
        seen.add(raw)
        found.append({
            "value":    raw,
            "type":     "location",
            "ambiguous": False,
            "context":  text[max(0, m.start()-30):m.end()+30].strip(),
        })
    return found


# ══════════════════════════════════════════════════════════════════════════════
# SPECIALISED PARSERS — Traffic Challans & ANPR Logs
# ══════════════════════════════════════════════════════════════════════════════

def _find_col(columns, targets: list) -> str | None:
    """Return the first matching column name from a list of candidate names."""
    cols_lower = {c.lower().strip(): c for c in columns}
    for t in targets:
        if t in cols_lower:
            return cols_lower[t]
    return None


def parse_traffic_challans(df, filename: str) -> dict:
    """
    Parse traffic challan records.
    Expected columns (flexible naming): vehicle_number, challan_date,
    challan_time, location, violation_type, fine_amount, status, owner_name.
    """
    from collections import Counter

    CHALLAN_COL_MAP = {
        "vehicle_number": ["vehicle_number", "vehicle", "plate", "registration", "reg_number", "veh_no"],
        "challan_date":   ["challan_date", "date", "violation_date", "offence_date"],
        "challan_time":   ["challan_time", "time", "violation_time"],
        "location":       ["location", "place", "address", "spot", "challan_location"],
        "violation_type": ["violation_type", "violation", "offence", "offence_type", "type"],
        "fine_amount":    ["fine_amount", "fine", "amount", "penalty"],
        "status":         ["status", "payment_status", "paid_status"],
        "owner_name":     ["owner_name", "owner", "name", "driver_name"],
    }

    records          = []
    night_violations = []
    unpaid           = []
    vehicles:  set   = set()
    locations: set   = set()

    for _, row in df.iterrows():
        record: dict = {}
        for field, alts in CHALLAN_COL_MAP.items():
            col = _find_col(df.columns, alts)
            if col:
                record[field] = str(row[col]).strip()

        if not record.get("vehicle_number"):
            continue

        records.append(record)

        veh = record.get("vehicle_number", "")
        if veh:
            vehicles.add(veh)

        loc = record.get("location", "")
        if loc:
            locations.add(loc)

        # Night violation (22:00 – 05:00)
        time_str  = record.get("challan_time", "")
        hour_m    = re.search(r"(\d{1,2}):", time_str)
        if hour_m:
            hour = int(hour_m.group(1))
            if hour >= 22 or hour <= 5:
                night_violations.append(record)

        # Unpaid fines
        status = record.get("status", "").lower()
        if any(k in status for k in ("unpaid", "pending", "due")):
            unpaid.append(record)

    loc_freq = Counter(
        r.get("location", "") for r in records if r.get("location")
    ).most_common(5)

    return {
        "type":               "traffic_challans",
        "records":            records,
        "count":              len(records),
        "vehicles_found":     list(vehicles),
        "locations_found":    list(locations),
        "night_violations":   night_violations,
        "night_count":        len(night_violations),
        "unpaid":             unpaid,
        "unpaid_count":       len(unpaid),
        "location_frequency": loc_freq,
        "filename":           filename,
    }


def parse_anpr_logs(df, filename: str) -> dict:
    """
    Parse ANPR / traffic camera logs.
    Expected columns (flexible naming): vehicle_number, capture_date,
    capture_time, location, location_gps, speed_kmph, direction,
    owner_name, owner_mobile, violation_type, rc_status.
    """
    from collections import Counter

    ANPR_COL_MAP = {
        "vehicle_number": ["vehicle_number", "vehicle", "plate", "registration", "reg_no"],
        "capture_date":   ["capture_date", "date", "log_date", "timestamp"],
        "capture_time":   ["capture_time", "time", "log_time"],
        "location":       ["location", "camera_location", "place", "checkpoint"],
        "location_gps":   ["location_gps", "gps", "coordinates", "lat_long"],
        "speed_kmph":     ["speed_kmph", "speed", "speed_kmh", "kmph"],
        "direction":      ["direction", "heading", "lane_direction"],
        "owner_name":     ["owner_name", "owner", "registered_owner"],
        "owner_mobile":   ["owner_mobile", "mobile", "phone", "owner_phone"],
        "violation_type": ["violation_type", "violation", "offence"],
        "rc_status":      ["rc_status", "registration_status", "status"],
    }

    records         = []
    night_captures  = []
    high_speed      = []
    vehicles: set   = set()

    for _, row in df.iterrows():
        record: dict = {}
        for field, alts in ANPR_COL_MAP.items():
            col = _find_col(df.columns, alts)
            if col:
                record[field] = str(row[col]).strip()

        if not record.get("vehicle_number"):
            continue

        records.append(record)

        veh = record.get("vehicle_number", "")
        if veh:
            vehicles.add(veh)

        # Night capture (22:00 – 05:00)
        time_str = record.get("capture_time", "")
        hour_m   = re.search(r"(\d{1,2}):", time_str)
        if hour_m:
            hour = int(hour_m.group(1))
            if hour >= 22 or hour <= 5:
                night_captures.append(record)

        # High speed (> 120 kmph)
        speed_str = record.get("speed_kmph", "0")
        try:
            speed = float(re.sub(r"[^\d.]", "", speed_str) or 0)
            if speed > 120:
                high_speed.append(record)
        except Exception:
            pass

    locs          = [r.get("location", "") for r in records if r.get("location")]
    route         = Counter(locs).most_common(3)
    route_pattern = " -> ".join(loc for loc, _ in route)

    return {
        "type":                 "anpr_logs",
        "records":              records,
        "count":                len(records),
        "vehicles_found":       list(vehicles),
        "night_captures":       night_captures,
        "night_count":          len(night_captures),
        "high_speed_captures":  high_speed,
        "high_speed_count":     len(high_speed),
        "frequent_locations":   route,
        "route_pattern":        route_pattern,
        "filename":             filename,
    }


def detect_file_subtype(df, filename: str) -> str:
    """
    Detect whether a CSV/Excel file is a challan log, ANPR log, or generic.
    Checks filename keywords and column names.
    Returns: 'challan' | 'anpr' | 'generic'
    """
    fn_lower   = filename.lower()
    cols_lower = [c.lower().strip() for c in df.columns]

    challan_kw = ["challan", "violation", "fine_amount", "fine", "offence"]
    if any(kw in fn_lower or any(kw in c for c in cols_lower) for kw in challan_kw):
        return "challan"

    anpr_kw = ["anpr", "camera", "speed_kmph", "capture", "checkpoint", "kmph"]
    if any(kw in fn_lower or any(kw in c for c in cols_lower) for kw in anpr_kw):
        return "anpr"

    return "generic"


def _extract_relationships(text: str) -> list[dict]:
    found = []
    for m in RE_RELATIONSHIP.finditer(text):
        entity_a = m.group(1).strip()
        entity_b = m.group(2).strip()
        rel_type = m.group(0).replace(entity_a, "").replace(entity_b, "").strip()
        found.append({
            "value":    f"{entity_a} -> {rel_type} -> {entity_b}",
            "entity_a": entity_a,
            "entity_b": entity_b,
            "rel_type": rel_type,
            "type":     "relationship",
            "ambiguous": False,
            "context":  text[max(0, m.start()-20):m.end()+20].strip(),
        })
    return found


def normalize_entities(text: str, source_label: str) -> dict:
    """
    Run all extractors on text and return categorised entity dict.
    Every item is tagged with its source document.
    """
    return {
        "source":        source_label,
        "names":         _extract_names(text),
        "phones":        _extract_phones(text),
        "emails":        _extract_emails(text),
        "dates":         _extract_dates(text),
        "addresses":     _extract_addresses(text),
        "locations":     _extract_locations(text),
        "relationships": _extract_relationships(text),
    }


# ── Audit log ──────────────────────────────────────────────────────────────────

def _log_upload(user_id: str, filename: str, file_type: str, declared: bool, items_count: int):
    try:
        conn = sqlite3.connect(str(config.DATABASE_PATH))
        now  = datetime.datetime.utcnow().isoformat()
        detail = json.dumps({
            "filename":    filename,
            "type":        file_type,
            "declaration": declared,
            "items":       items_count,
        })
        conn.execute(
            "INSERT INTO audit_log (event, username, detail, timestamp) VALUES (?, ?, ?, ?)",
            ("FILE_UPLOAD", user_id, detail, now),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


# ── Main ingestion pipeline ────────────────────────────────────────────────────

def ingest_file(
    file_bytes: bytes,
    filename: str,
    user_id: str,
    declared: bool,
) -> dict:
    """
    Full ingestion pipeline.
    If declared is False, returns an error immediately — upload is blocked.
    """
    if not declared:
        return {
            "success":   False,
            "error":     "Upload blocked: lawful authorization declaration required.",
            "filename":  filename,
            "entities":  None,
        }

    # ── File-type guard — block non-data files before any processing ──────────
    if should_skip_file(filename):
        print(f"[INGEST] Skipping non-data file: {filename}")
        return {
            "success":  False,
            "error":    f"File type not accepted: {Path(filename).suffix.lower()}. "
                        f"Only CSV, XLSX, PDF, and TXT files are processed.",
            "filename": filename,
            "entities": None,
        }

    suffix = Path(filename).suffix.lower()
    extractor = FILE_EXTRACTORS.get(suffix)

    if extractor is None:
        return {
            "success":   False,
            "error":     f"Unsupported file type: {suffix}. Supported: {', '.join(SUPPORTED_TYPES)}",
            "filename":  filename,
            "entities":  None,
        }

    # Extract text
    raw_text = extractor(file_bytes, filename)

    if "parse error" in raw_text.lower():
        _log_upload(user_id, filename, suffix, declared, 0)
        return {
            "success":   False,
            "error":     raw_text,
            "filename":  filename,
            "entities":  None,
        }

    # Extract structured rows for relationship/anomaly analysis
    structured_rows: list[dict] = []
    primary_subject: str = ""
    try:
        primary_subject = extract_primary_subject_from_bytes(file_bytes, suffix)
        if suffix in (".xlsx", ".xls"):
            xl = pd.ExcelFile(io.BytesIO(file_bytes))
            for sheet in xl.sheet_names:
                df = xl.parse(sheet, dtype=str, keep_default_na=False)
                df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]
                structured_rows.extend(df.to_dict("records"))
        elif suffix == ".csv":
            df = read_csv_safe(io.BytesIO(file_bytes), dtype=str, keep_default_na=False)
            df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]
            structured_rows.extend(df.to_dict("records"))
            # CSV: check explicit subject/person name columns first
            if not primary_subject:
                _subject_cols = {"subject_name", "person_name", "caller_name", "subject", "person"}
                for row in structured_rows[:5]:
                    for col, val in row.items():
                        if col in _subject_cols:
                            val_str = str(val).strip()
                            if val_str and val_str not in ("", "nan", "None", "Not found"):
                                primary_subject = val_str
                                break
                    if primary_subject:
                        break
    except Exception:
        pass

    # ── Specialised sub-type detection for CSV / Excel ────────────────────────
    _challan_data = None
    _anpr_data    = None
    if suffix in (".csv", ".xlsx", ".xls") and structured_rows:
        try:
            if suffix == ".csv":
                _df_sub = read_csv_safe(io.BytesIO(file_bytes), dtype=str, keep_default_na=False)
            else:
                _df_sub = pd.read_excel(io.BytesIO(file_bytes), dtype=str, keep_default_na=False)
            _subtype = detect_file_subtype(_df_sub, filename)
            if _subtype == "challan":
                _challan_data = parse_traffic_challans(_df_sub, filename)
                print(f"[INGEST] Challan file: {_challan_data['count']} records")
            elif _subtype == "anpr":
                _anpr_data = parse_anpr_logs(_df_sub, filename)
                print(f"[INGEST] ANPR file: {_anpr_data['count']} records")
        except Exception:
            pass

    # For PDF/TXT: extract subject name from raw text
    if not primary_subject and suffix in (".pdf", ".txt"):
        primary_subject = (extract_subject_name(raw_text) or "").replace("\n", " ").replace("\r", " ").strip()

    # Normalise before any further use — strip newlines / CR introduced by PDF/text extraction
    primary_subject = primary_subject.replace("\n", " ").replace("\r", " ").strip()

    # Guard: reject legal/procedural category strings, filenames, and artifacts
    # that the LLM sometimes misidentifies as a person name.
    if primary_subject:
        from modules.entity_resolution import is_bad_subject_name as _is_bad_ps
        if _is_bad_ps(primary_subject):
            print(f"[INGEST] Rejecting bad primary_subject: {primary_subject!r}")
            primary_subject = ""

    if primary_subject:
        print(f"[INGEST] Primary subject: {primary_subject}")
    else:
        print("[INGEST] No subject name found in document")

    # Extract [FLAG] tagged entries from text
    document_flags = extract_flags_from_text(raw_text, filename)
    print(f"[INGEST] Flags found: {len(document_flags)}")

    # Extract locations from text (PDF/TXT gets enhanced extraction)
    if suffix in (".pdf", ".txt"):
        doc_locations = extract_locations_from_text(raw_text)
    else:
        doc_locations = []

    # Normalize entities
    entities = normalize_entities(raw_text, filename)

    # Prepend primary_subject into names list so it ranks first
    if primary_subject:
        existing_names = [n for n in entities.get("names", []) if n.get("value") != primary_subject]
        entities["names"] = [{"value": primary_subject, "type": "name", "ambiguous": False, "context": "primary_subject"}] + existing_names

    total_items = sum(
        len(v) for k, v in entities.items()
        if isinstance(v, list)
    )

    _log_upload(user_id, filename, suffix, declared, total_items)

    # Build relationship graph inputs from extracted entities
    graph_entities = []
    graph_rels     = []
    # Only add names that pass the person-name sanity check so that
    # location strings like "Bank Nariman Point" or "Bandra Worli Sea Link"
    # are never typed as "person" nodes in the relationship graph.
    _loc_words = {
        "bank", "branch", "point", "link", "bridge", "sea", "bay", "port",
        "station", "airport", "highway", "flyover", "junction", "naka",
        "tower", "plaza", "mall", "complex", "centre", "center",
        "park", "garden", "market", "masjid", "mandir", "chowk",
        "marg", "road", "street", "lane", "avenue", "boulevard",
        "bandra", "worli", "nariman", "sector", "phase", "block", "zone",
        # Major Indian cities — never a person-name word (fixes "Mumbai Speeding" node)
        "mumbai", "delhi", "chennai", "kolkata", "hyderabad", "bengaluru",
        "pune", "nashik", "nagpur", "ahmedabad", "surat", "jaipur", "lucknow",
        # Traffic violation terms that look like proper nouns in challan CSVs
        "speeding", "overspeed", "overloading", "violation", "challan",
    }
    for name_item in entities.get("names", [])[:30]:
        nm = name_item["value"]
        # Skip names that contain any location indicator word
        if any(w in _loc_words for w in nm.lower().split()):
            continue
        nid = f"ingested:{nm}"
        graph_entities.append({
            "id":    nid,
            "label": nm,
            "type":  "person",
        })
    for rel in entities.get("relationships", []):
        graph_rels.append({
            "source": f"ingested:{rel['entity_a']}",
            "target": f"ingested:{rel['entity_b']}",
            "type":   "mentioned_with",
            "weight": 1,
            "detail": rel.get("rel_type", ""),
        })

    result = {
        "success":          True,
        "filename":         filename,
        "file_type":        suffix,
        "raw_text":         raw_text[:5000],  # preview only
        "full_text":        raw_text,
        "entities":         entities,
        "total_items":      total_items,
        "graph_entities":   graph_entities,
        "graph_rels":       graph_rels,
        "declared":         declared,
        "user_id":          user_id,
        "structured_rows":  structured_rows,
        "primary_subject":  primary_subject,
        "document_flags":   document_flags,
        "locations":        doc_locations,
    }
    if _challan_data:
        result["challan_data"] = _challan_data
        result["type"]         = "traffic_challans"
    if _anpr_data:
        result["anpr_data"] = _anpr_data
        result["type"]      = "anpr_logs"
    return result
