"""
AetherLens — Timeline Module
Extract, normalize, sort, and visualize temporal data from OSINT sources.
"""

import re
import json
from datetime import datetime, date
from dateutil import parser as dateutil_parser
import plotly.graph_objects as go

from modules.sanitizer import safe_str, defensive

# ── Date extraction patterns ───────────────────────────────────────────────────

DATE_PATTERNS = [
    # ISO 8601: 2023-04-15 or 2023/04/15
    r"\b(\d{4}[-/]\d{1,2}[-/]\d{1,2})\b",
    # Written: April 15, 2023 | 15 April 2023
    r"\b(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{4})\b",
    r"\b((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4})\b",
    # Compact: 15/04/2023 or 04/15/2023
    r"\b(\d{1,2}/\d{1,2}/\d{4})\b",
    r"\b(\d{1,2}-\d{1,2}-\d{4})\b",
    # Year + month only: March 2022 | 2022-03
    r"\b((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{4})\b",
    r"\b(\d{4}-\d{2})\b",
    # Year only (as fallback)
    r"\b((?:19|20)\d{2})\b",
]

COMPILED_PATTERNS = [re.compile(p, re.IGNORECASE) for p in DATE_PATTERNS]


# ── Date precision (CHIMERA fix) ──────────────────────────────────────────────
# A source that supplies only a year ("2002") or a year-month ("January 2023")
# must never gain a fabricated day/month — least of all one derived from
# datetime.now(), which made rendered timeline dates track the report
# generation date. Every parsed date carries the precision the SOURCE actually
# provided; partial-precision dates get a deterministic anchor (Jan 1 / day 1)
# for internal ordering ONLY, and are rendered at source precision.

PRECISION_YEAR       = "YEAR"
PRECISION_YEAR_MONTH = "YEAR_MONTH"
PRECISION_FULL       = "FULL_DATE"

# Two fixed sentinel defaults for dateutil: any field on which the two parses
# disagree was NOT supplied by the source (dateutil filled it from `default`).
_SENTINEL_A = datetime(2001, 1, 1)
_SENTINEL_B = datetime(2002, 2, 2)


def _parse_date_precision(
    raw: str, dayfirst: bool = False
) -> tuple[datetime | None, str | None]:
    """Parse a raw date string into (datetime, precision).

    precision is the granularity the SOURCE supplied: FULL_DATE, YEAR_MONTH,
    or YEAR. Partial dates are anchored deterministically (Jan 1 / day 1) so
    they can be ordered; the anchor digits must never be displayed.
    Returns (None, None) if unparseable or if the source omits the year —
    a year is never imputed.
    """
    raw = raw.strip()
    # Year-only
    if re.fullmatch(r"(?:19|20)\d{2}", raw):
        try:
            return datetime(int(raw), 1, 1), PRECISION_YEAR
        except Exception:
            return None, None
    # Year-month numeric (2022-03)
    ym = re.fullmatch(r"(\d{4})-(\d{1,2})", raw)
    if ym:
        try:
            return datetime(int(ym.group(1)), int(ym.group(2)), 1), PRECISION_YEAR_MONTH
        except Exception:
            return None, None
    # General case: double-parse with two sentinel defaults. Fields that
    # differ between the two results came from the default, not the source.
    for fuzzy in (False, True):
        try:
            a = dateutil_parser.parse(raw, fuzzy=fuzzy, dayfirst=dayfirst,
                                      default=_SENTINEL_A)
            b = dateutil_parser.parse(raw, fuzzy=fuzzy, dayfirst=dayfirst,
                                      default=_SENTINEL_B)
        except Exception:
            continue
        if a.year != b.year:
            return None, None  # source gave no year — reject, never impute
        if a.month != b.month:
            return datetime(a.year, 1, 1), PRECISION_YEAR
        if a.day != b.day:
            return datetime(a.year, a.month, 1), PRECISION_YEAR_MONTH
        return a, PRECISION_FULL
    return None, None


def _normalize_for_precision(dt: datetime, precision: str) -> str:
    """Sortable string at source precision: '2002' / '2023-01' / '2023-04-18'."""
    if precision == PRECISION_YEAR:
        return dt.strftime("%Y")
    if precision == PRECISION_YEAR_MONTH:
        return dt.strftime("%Y-%m")
    return dt.strftime("%Y-%m-%d")


def _display_for_precision(dt: datetime, precision: str) -> str:
    """Human form at source precision: '2002' / 'January 2023' / '2023-04-18'."""
    if precision == PRECISION_YEAR:
        return dt.strftime("%Y")
    if precision == PRECISION_YEAR_MONTH:
        return dt.strftime("%B %Y")
    return dt.strftime("%Y-%m-%d")


def _event_date_fields(dt: datetime, precision: str) -> dict:
    """The date-bearing fields every timeline event carries."""
    return {
        "normalized":     _normalize_for_precision(dt, precision),
        "datetime_obj":   dt,
        "date_precision": precision,
        "display_date":   _display_for_precision(dt, precision),
        "ambiguous":      precision != PRECISION_FULL,
    }


_MONETARY_CONTEXT_RE = re.compile(
    r"(?:inr|rs\.?|rupees?|usd|\$|eur|aed|sgd|fine|amount|penalty|fee|charge|paid|balance|due|total)"
    r"\s*[\d,]*",
    re.IGNORECASE,
)

_DURATION_CONTEXT_RE = re.compile(
    r"(?:seconds?|secs?|minutes?|mins?|hours?|duration|length|time)\s*[\d,]*",
    re.IGNORECASE,
)


def _is_monetary_year(year_str: str, context: str) -> bool:
    """
    Return True if this 4-digit year appears right after a monetary/amount keyword,
    meaning it is a currency amount (e.g. 'INR 2000', 'Rs. 500') not a date year.
    """
    ctx = context.lower()
    # Check for monetary keyword immediately before the year
    # e.g. "INR 2000", "Rs. 500", "fine_amount: 2000", "$2000"
    before = ctx[:ctx.find(year_str)].rstrip() if year_str in ctx else ""
    if _MONETARY_CONTEXT_RE.search(before[-30:]):
        return True
    # Check patterns like "fine_amount: INR 2000" or "amount 500"
    if re.search(
        r"(?:inr|rs\.?|rupees?|usd|\$|eur|aed|fine|amount|penalty|fee|balance)\s*" + year_str,
        ctx,
    ):
        return True
    return False


def extract_dates_from_text(text: str, source_label: str = "Unknown") -> list[dict]:
    """
    Extract all date-like strings from a text block.
    Returns list of {raw, normalized, datetime_obj, source, ambiguous}.
    Filters out: monetary amounts (INR 2000), call durations, out-of-range years.
    """
    found = []
    seen  = set()

    for pattern in COMPILED_PATTERNS:
        for match in pattern.finditer(text):
            raw = match.group(1).strip()
            if raw in seen:
                continue
            seen.add(raw)

            # ── Year-only guard ───────────────────────────────────────────────
            if re.fullmatch(r"(?:19|20)\d{2}", raw):
                year_int = int(raw)
                # Reject out-of-range years
                if not (1950 <= year_int <= 2030):
                    continue
                # Reject years that appear in monetary context
                ctx = text[max(0, match.start() - 60):match.end() + 20]
                if _is_monetary_year(raw, ctx):
                    continue
                # Reject if immediately preceded by non-date tokens
                # (e.g. a column value that is just a bare number like "2000")
                # Check 5 chars before match — if alphanumeric or digit, skip
                pre = text[max(0, match.start() - 5):match.start()]
                if re.search(r"\d", pre):
                    continue  # digits immediately before — part of a larger number

            dt, precision = _parse_date_precision(raw)
            if dt:
                found.append({
                    "raw":     raw,
                    **_event_date_fields(dt, precision),
                    "source":  source_label,
                    "context": text[max(0, match.start()-40):match.end()+40].strip(),
                })

    return found


def extract_dates_from_person(person: dict, search_results: dict) -> list[dict]:
    """
    Extract all temporal data from a Person Object and its raw search results.
    """
    all_events = []

    # Join dates
    for platform, date_str in person.get("join_dates", {}).items():
        if date_str and date_str != "Not found":
            dt, precision = _parse_date_precision(date_str)
            if dt:
                all_events.append({
                    "raw":        date_str,
                    **_event_date_fields(dt, precision),
                    "source":     f"{platform} — Account Created",
                    "context":    f"Account created on {platform}",
                    "event_type": "account_creation",
                })

    # GitHub joined
    gh = person.get("github_data", {})
    gh_joined = gh.get("joined", "")
    if gh_joined and gh_joined != "Not found":
        dt, precision = _parse_date_precision(gh_joined)
        if dt:
            all_events.append({
                "raw":        gh_joined,
                **_event_date_fields(dt, precision),
                "source":     "GitHub — Account Created",
                "context":    "GitHub account creation date",
                "event_type": "account_creation",
            })

    # Search results text
    for result in search_results.get("results", []):
        snippet = result.get("snippet", "")
        name    = result.get("full_name", "")
        source  = result.get("platform", "Unknown")
        label   = f"{source} — {name[:40]}"
        if snippet:
            events = extract_dates_from_text(snippet, label)
            for e in events:
                e["event_type"] = "mention"
            all_events.extend(events)

    # News appearances
    for news_item in person.get("news_appearances", []):
        events = extract_dates_from_text(news_item, "News")
        for e in events:
            e["event_type"] = "news_mention"
        all_events.extend(events)

    # Deduplicate by (normalized_date, context[:80]) — same approach as deduplicate_events
    seen_keys = set()
    unique = []
    for e in all_events:
        ctx_clean = re.sub(r"\s+", " ", safe_str(e.get("context", ""))).strip().lower()[:80]
        key = (e["normalized"], ctx_clean)
        if key not in seen_keys:
            seen_keys.add(key)
            unique.append(e)

    # Sort chronologically
    unique.sort(key=lambda x: x["datetime_obj"])
    return unique


def identify_gaps(events: list[dict], gap_threshold_days: int = 180) -> list[dict]:
    """
    Identify periods of inactivity (gaps) between consecutive events.
    Returns list of gap dicts with start, end, duration_days.

    Only FULL_DATE events participate: a day-count computed from the
    ordering anchor of a year-only or month-only event would fabricate
    precision the source never provided.
    """
    events = [
        e for e in events
        if e.get("date_precision", PRECISION_FULL) == PRECISION_FULL
    ]
    if len(events) < 2:
        return []

    gaps = []
    for i in range(len(events) - 1):
        dt_a = events[i]["datetime_obj"]
        dt_b = events[i + 1]["datetime_obj"]
        delta = (dt_b - dt_a).days
        if delta >= gap_threshold_days:
            gaps.append({
                "start":         events[i]["normalized"],
                "end":           events[i + 1]["normalized"],
                "duration_days": delta,
                "label":         f"Gap: {delta} days",
            })
    return gaps


def flag_anomalies(events: list[dict]) -> list[dict]:
    """
    Flag temporal anomalies:
    - Events in the future
    - Events before plausible birth year (pre-1900)
    - Duplicate events on same day from different sources
    """
    anomalies = []
    now = datetime.now()
    date_map: dict[str, list] = {}

    for e in events:
        dt = e["datetime_obj"]

        if dt > now:
            anomalies.append({
                "event":  e,
                "flag":   "Future date",
                "detail": f"Date {e['normalized']} is in the future",
            })

        if dt.year < 1900:
            anomalies.append({
                "event":  e,
                "flag":   "Implausible date",
                "detail": f"Date {e['normalized']} pre-dates plausible range",
            })

        key = e["normalized"]
        date_map.setdefault(key, []).append(e)

    for day, same_day_events in date_map.items():
        if len(same_day_events) >= 3:
            anomalies.append({
                "event":  same_day_events[0],
                "flag":   "Cluster",
                "detail": f"{len(same_day_events)} events on {day} — possible burst activity",
            })

    return anomalies


# ── Plotly timeline ─────────────────────────────────────────────────────────────

EVENT_TYPE_COLORS = {
    "account_creation": "#2563EB",   # --info
    "news_mention":     "#D97706",   # --warn
    "mention":          "#9D4EDD",   # --p500
    "gap":              "#2A2438",   # --faint
    "anomaly":          "#DC2626",   # --crit
}


def render_timeline(
    events: list[dict],
    gaps: list[dict],
    anomalies: list[dict],
    subject_name: str = "Subject",
) -> go.Figure:
    """
    Build an interactive Plotly scatter timeline.
    """
    fig = go.Figure()

    if not events:
        fig.update_layout(
            paper_bgcolor="#000000",
            plot_bgcolor="#05000D",
            font=dict(color="#F0EAD6", family="Arial, sans-serif"),
            title=dict(text="No temporal data found",
                       font=dict(color="#7B2FBE", family="Arial, sans-serif")),
            height=400,
        )
        return fig

    anomaly_dates = {a["event"]["normalized"] for a in anomalies}

    # Group events by type for legend
    event_groups: dict[str, list] = {}
    for e in events:
        etype = e.get("event_type", "mention")
        if e["normalized"] in anomaly_dates:
            etype = "anomaly"
        event_groups.setdefault(etype, []).append(e)

    y_labels = {
        "account_creation": 1.0,
        "news_mention":     0.7,
        "mention":          0.4,
        "anomaly":          1.3,
    }

    for etype, evts in event_groups.items():
        color = EVENT_TYPE_COLORS.get(etype, "#9D4EDD")
        y_val = y_labels.get(etype, 0.4)
        xs, ys, hovers = [], [], []

        for e in evts:
            xs.append(e["datetime_obj"])
            ys.append(y_val)
            ambig = {
                PRECISION_YEAR:       " (year precision)",
                PRECISION_YEAR_MONTH: " (month precision)",
            }.get(e.get("date_precision", PRECISION_FULL), "")
            hover = (
                f"<b>{e['normalized']}{ambig}</b><br>"
                f"Source: {e['source']}<br>"
                f"Context: {e.get('context', '')[:100]}"
            )
            hovers.append(hover)

        marker_symbol = "diamond" if etype == "anomaly" else "circle"
        marker_size   = 14 if etype == "anomaly" else 10

        fig.add_trace(go.Scatter(
            x=xs,
            y=ys,
            mode="markers",
            marker=dict(
                color=color,
                size=marker_size,
                symbol=marker_symbol,
                line=dict(color="#000000", width=1),
                opacity=0.9,
            ),
            name=etype.replace("_", " ").title(),
            hovertext=hovers,
            hoverinfo="text",
        ))

    # Gap shading
    for gap in gaps:
        try:
            x0 = dateutil_parser.parse(gap["start"])
            x1 = dateutil_parser.parse(gap["end"])
        except Exception:
            continue
        fig.add_vrect(
            x0=x0, x1=x1,
            fillcolor="#1a0a2e",
            opacity=0.3,
            layer="below",
            line_width=0,
            annotation_text=f"Gap {gap['duration_days']}d",
            annotation_position="top left",
            annotation_font=dict(color="#555", size=9),
        )

    # Timeline baseline
    if events:
        fig.add_hline(
            y=0.0, line_dash="dot",
            line_color="#1a1a1a", line_width=1,
        )

    # NOTE: Plotly renders text in SVG/canvas — web fonts loaded via CSS @import
    # (Rajdhani, JetBrains Mono) are NOT available there and produce white rectangles.
    # All Plotly font families must be system-safe: Arial, Courier New, etc.
    _SYS      = "Arial, sans-serif"
    _SYS_MONO = "Courier New, monospace"

    fig.update_layout(
        title=dict(
            text=f"Timeline — {subject_name}",
            font=dict(color="#9D4EDD", size=14, family=_SYS),
            x=0.01,
        ),
        paper_bgcolor="#000000",   # --void
        plot_bgcolor="#05000D",    # --abyss
        font=dict(color="#F0EAD6", family=_SYS),
        hovermode="closest",
        height=420,
        showlegend=True,
        legend=dict(
            bgcolor="#0A0015",                    # --deep
            bordercolor="rgba(123,47,190,0.28)",  # --border
            font=dict(color="#F0EAD6", size=10, family=_SYS),
            orientation="h",
            y=-0.15,
        ),
        xaxis=dict(
            gridcolor="#1E0040",               # --mid
            zerolinecolor="rgba(123,47,190,0.28)",
            tickfont=dict(color="#9CA3AF", size=9, family=_SYS_MONO),
            title=None,
            rangeslider=dict(visible=True, bgcolor="#05000D", thickness=0.06),
        ),
        yaxis=dict(
            showgrid=False,
            zeroline=False,
            showticklabels=False,
            range=[-0.3, 1.8],
            visible=False,
        ),
        margin=dict(l=10, r=10, t=40, b=40),
        dragmode="pan",
    )
    fig.update_traces(
        hoverlabel=dict(
            bgcolor="#0A0015",              # --deep
            bordercolor="#7B2FBE",          # --p600
            font=dict(color="#F0EAD6", size=11, family=_SYS),
        )
    )

    return fig


def build_timeline(person: dict, search_results: dict) -> dict:
    """
    Full pipeline: extract -> sort -> gap analysis -> anomaly detection -> figure.
    Returns dict with events, gaps, anomalies, figure.
    """
    events   = extract_dates_from_person(person, search_results)
    gaps     = identify_gaps(events)
    anomalies = flag_anomalies(events)
    subject  = person.get("confirmed_name", "Subject")
    fig      = render_timeline(events, gaps, anomalies, subject)

    return {
        "events":    events,
        "gaps":      gaps,
        "anomalies": anomalies,
        "figure":    fig,
        "count":     len(events),
    }


def deduplicate_events(events: list) -> list:
    """
    Two-pass content-aware deduplication.

    Pass 1 — primary key: (normalized_date, context_normalized[:80])
        Strips "Document: <file> [<col>]" prefixes so the same event ingested
        via both the raw-text scan and build_timeline_from_all_files collapses
        into one entry.

    Pass 2 — secondary key: (normalized_date, source_file_stem)
        Ensures the same file cannot contribute more than one event per date
        (catches cases where the context snippet differs across scans of the
        same file, making Pass 1 miss the duplicate).
    """
    # ── Pass 1: content-based dedup ───────────────────────────────────────────
    seen:   set  = set()
    pass1:  list = []
    for event in (events or []):
        date_str = safe_str(event.get("normalized", event.get("date", "")))
        desc     = safe_str(event.get("description", event.get("context", "")))
        # Strip "Document: <filename> [<col>]" and "Document: <filename>" prefixes
        desc_clean = re.sub(r"Document:\s*[^\[\n]*(?:\[[^\]]*\])?", "", desc)
        desc_clean = re.sub(r"\s+", " ", desc_clean).strip().lower()[:80]
        key = (date_str, desc_clean)
        if key not in seen:
            seen.add(key)
            pass1.append(event)

    # ── Pass 2: per-file per-date dedup ───────────────────────────────────────
    seen2:  set  = set()
    unique: list = []
    for event in pass1:
        date_str = safe_str(event.get("normalized", event.get("date", "")))
        source   = safe_str(event.get("source", ""))
        # Extract bare filename stem: "Document: foo.csv [challan_date]" → "foo.csv"
        stem = re.sub(r"Document:\s*", "", source).split("[")[0].strip().lower()[:50]
        key2 = (date_str, stem)
        if not stem or key2 not in seen2:
            if stem:
                seen2.add(key2)
            unique.append(event)

    return sorted(unique, key=lambda x: safe_str(x.get("normalized", x.get("date", ""))))


@defensive(fallback={"events": [], "count": 0, "gaps": [], "anomalies": [], "figure": None, "error": True})
def build_timeline_from_fusion(person_object: dict, raw_documents: list) -> dict:
    """
    Build timeline from Fusion mode raw documents.
    Extracts dates from raw text lines AND structured rows using DATE_PATTERNS.
    Returns same format as build_timeline().
    """
    events: list[dict] = []

    for doc in (raw_documents or []):
        filename = doc.get("filename", doc.get("name", "document"))

        # ── Raw text lines ─────────────────────────────────────────────────────
        text  = str(doc.get("raw_text", ""))
        lines = text.split("\n")
        for line in lines:
            line = line.strip()
            if not line:
                continue
            for pattern in COMPILED_PATTERNS:
                match = pattern.search(line)
                if match:
                    raw = match.group(1).strip()
                    # Skip monetary amounts parsed as year (e.g. "INR 2000")
                    if len(raw) == 4 and _is_monetary_year(raw, line):
                        break
                    dt, precision = _parse_date_precision(raw)
                    if dt:
                        events.append({
                            "raw":        raw,
                            **_event_date_fields(dt, precision),
                            "source":     f"Document: {filename}",
                            "context":    line[:120],
                            "event_type": "document_mention",
                        })
                    break  # one date match per line is sufficient

        # ── Structured rows ────────────────────────────────────────────────────
        rows = doc.get("structured_rows", [])
        for row in rows:
            if not isinstance(row, dict):
                continue
            for key, val in row.items():
                val_str = str(val).strip()
                if not val_str or val_str in ("None", "nan", ""):
                    continue
                for pattern in COMPILED_PATTERNS:
                    match = pattern.search(val_str)
                    if match:
                        raw = match.group(1).strip()
                        # Skip monetary amounts in column values
                        # e.g. fine_amount=2000, fee=1500, balance=2023
                        # Use column key + value as context so "fine_amount: 2000"
                        # is caught by _MONETARY_CONTEXT_RE ("fine" keyword).
                        if len(raw) == 4 and _is_monetary_year(raw, f"{key}: {val_str}"):
                            break
                        dt, precision = _parse_date_precision(raw)
                        if dt:
                            events.append({
                                "raw":        raw,
                                **_event_date_fields(dt, precision),
                                "source":     f"Document: {filename} [{key}]",
                                "context":    f"{key}: {val_str[:80]}",
                                "event_type": "structured_data",
                            })
                        break

    # ── Person object join dates ───────────────────────────────────────────────
    for platform, date_str in (person_object or {}).get("join_dates", {}).items():
        if date_str and date_str != "Not found":
            dt, precision = _parse_date_precision(date_str)
            if dt:
                events.append({
                    "raw":        date_str,
                    **_event_date_fields(dt, precision),
                    "source":     f"{platform} — Account Created",
                    "context":    f"Account created on {platform}",
                    "event_type": "account_creation",
                })

    # GitHub joined
    gh_joined = (person_object or {}).get("github_data", {}).get("joined", "")
    if gh_joined and gh_joined != "Not found":
        dt, precision = _parse_date_precision(gh_joined)
        if dt:
            events.append({
                "raw":        gh_joined,
                **_event_date_fields(dt, precision),
                "source":     "GitHub — Account Created",
                "context":    "GitHub account creation date",
                "event_type": "account_creation",
            })

    # ── Targeted date-column pass (supplements regex scan) ───────────────────
    # build_timeline_from_all_files uses _DATE_COLUMN_KEYS to extract events
    # from columns like challan_date / transaction_date / sighting_date that
    # may be missed by the generic regex scan above.
    targeted = build_timeline_from_all_files(raw_documents or [])
    events.extend(targeted)

    # ── Content-aware deduplication ───────────────────────────────────────────
    unique = deduplicate_events(events)
    unique.sort(key=lambda x: x.get("datetime_obj", datetime.min))

    gaps      = identify_gaps(unique)
    anomalies = flag_anomalies(unique)
    subject   = (person_object or {}).get("confirmed_name", "Subject")
    fig       = render_timeline(unique, gaps, anomalies, subject)

    return {
        "events":    unique,
        "gaps":      gaps,
        "anomalies": anomalies,
        "figure":    fig,
        "count":     len(unique),
    }


# ── Targeted date-column extractor (Audit fix #5) ────────────────────────────
# Recognises specific date-typed columns so bank statements, challans, FIR
# sighting logs, etc. all contribute timeline entries with per-file attribution
# rather than every event landing on whichever file was processed first.

_DATE_COLUMN_KEYS = {
    "date", "call_date", "transaction_date", "challan_date", "sighting_date",
    "issue_date", "report_date", "filing_date", "event_date", "hearing_date",
    "arrest_date", "seizure_date", "dispatch_date", "entry_date", "record_date",
}


# ── Timeline Intelligence Analysis ───────────────────────────────────────────

def detect_timeline_contradictions(
    timeline_events: list,
    raw_documents: list,
) -> list:
    """
    Cross-references timestamps across all sources to find impossibilities.
    Returns list of contradiction objects.
    """
    contradictions = []

    location_map = {}
    for event in timeline_events:
        date     = event.get("date", "")
        time     = event.get("time", "")
        location = event.get("location", "")
        source   = event.get("source", "")
        if date and location:
            key = f"{date} {time}".strip()
            if key not in location_map:
                location_map[key] = []
            location_map[key].append({
                "location": location,
                "source":   source,
                "event":    event.get("description", ""),
            })

    CITIES = {
        "mumbai":           "Mumbai",
        "delhi":            "Delhi",
        "gurugram":         "Gurugram",
        "bengaluru":        "Bengaluru",
        "bangalore":        "Bengaluru",
        "chennai":          "Chennai",
        "kolkata":          "Kolkata",
        "hyderabad":        "Hyderabad",
        "pune":             "Pune",
        "alibag":           "Alibag",
        "jnpt":             "Mumbai",
        "nhava sheva":      "Mumbai",
        "byculla":          "Mumbai",
        "juhu":             "Mumbai",
        "sector 14 gurugram": "Gurugram",
        "amity campus":     "Gurugram",
        "dalal street":     "Mumbai",
        "bandra":           "Mumbai",
        "nariman point":    "Mumbai",
        "zaveri bazaar":    "Mumbai",
    }

    def get_city(location_str):
        loc = location_str.lower()
        for key, city in CITIES.items():
            if key in loc:
                return city
        return location_str.split(" ")[0].title()

    for timestamp, locations in location_map.items():
        if len(locations) < 2:
            continue
        cities = [get_city(l["location"]) for l in locations]
        unique_cities = set(cities)
        if len(unique_cities) > 1:
            contradictions.append({
                "type":      "LOCATION_CONTRADICTION",
                "timestamp": timestamp,
                "conflict":  (
                    f"Subject placed in "
                    f"{' AND '.join(unique_cities)} simultaneously"
                ),
                "sources": [
                    f"{l['source']}: {l['location']}" for l in locations
                ],
                "severity":   "HIGH",
                "court_note": (
                    "Physical impossibility — subject cannot be in "
                    "multiple cities at once. Verify source accuracy."
                ),
            })

    call_events = [
        e for e in timeline_events
        if "call" in str(e.get("source", "")).lower()
        or "cdr"  in str(e.get("source", "")).lower()
    ]
    surveillance_events = [
        e for e in timeline_events
        if "surveillance" in str(e.get("source", "")).lower()
        or "anpr"         in str(e.get("source", "")).lower()
    ]

    for call in call_events:
        call_date = call.get("date", "")
        call_time = call.get("time", "")
        call_loc  = call.get("location", "")
        if not call_date or not call_loc:
            continue
        for surv in surveillance_events:
            surv_date = surv.get("date", "")
            if surv_date != call_date:
                continue
            surv_loc = surv.get("location", "")
            if not surv_loc:
                continue
            call_city = get_city(call_loc)
            surv_city = get_city(surv_loc)
            if (call_city != surv_city
                    and call_city != "Unknown"
                    and surv_city != "Unknown"):
                contradictions.append({
                    "type":      "CDR_SURVEILLANCE_MISMATCH",
                    "timestamp": f"{call_date} {call_time}",
                    "conflict":  (
                        f"CDR records call from {call_city} but "
                        f"surveillance places subject in {surv_city}"
                    ),
                    "sources": [
                        f"CDR: {call.get('source','')} — {call_city}",
                        f"Surveillance: {surv.get('source','')} — {surv_city}",
                    ],
                    "severity":   "CRITICAL",
                    "court_note": (
                        "CDR tower location contradicts physical surveillance. "
                        "One source may be fabricated or in error."
                    ),
                })

    seen   = set()
    unique = []
    for c in contradictions:
        key = c["conflict"][:50]
        if key not in seen:
            seen.add(key)
            unique.append(c)
    return unique


def detect_timeline_gaps(timeline_events: list) -> list:
    """
    Finds unexplained gaps in timeline and suggests what evidence would fill them.
    """
    if len(timeline_events) < 2:
        return []

    from datetime import datetime as _dt, timedelta  # noqa: F401

    gaps = []
    dated = []
    for e in timeline_events:
        date_str = e.get("date", "")
        if not date_str:
            continue
        try:
            dt = _dt.strptime(date_str[:10], "%Y-%m-%d")
            dated.append((dt, e))
        except Exception:
            continue

    if len(dated) < 2:
        return []

    dated.sort(key=lambda x: x[0])

    EVIDENCE_SUGGESTIONS = {
        (1,  3):   [
            "CDR records for this period",
            "Bank/ATM transactions",
        ],
        (3,  7):   [
            "CDR records for this period",
            "Bank/ATM transactions",
            "Travel records",
            "CCTV/ANPR if movement suspected",
        ],
        (7,  30):  [
            "CDR records — full period",
            "Bank statements",
            "Travel records — check airline/rail bookings",
            "CCTV retrieval",
            "Witness statements for this period",
            "Social media activity",
        ],
        (30, 999): [
            "CDR records — full period",
            "Bank statements",
            "Income tax returns",
            "Travel records",
            "Passport stamp verification",
            "Extended witness statements",
            "Business records",
        ],
    }

    for i in range(len(dated) - 1):
        dt1, event1 = dated[i]
        dt2, event2 = dated[i + 1]
        delta = (dt2 - dt1).days
        if delta <= 1:
            continue

        suggestions = ["CDR records for this period", "Bank transactions"]
        for (lo, hi), sugg in EVIDENCE_SUGGESTIONS.items():
            if lo <= delta < hi:
                suggestions = sugg
                break

        severity = (
            "HIGH"   if delta > 14 else
            "MEDIUM" if delta > 3  else
            "LOW"
        )

        gaps.append({
            "gap_start":      dt1.strftime("%Y-%m-%d"),
            "gap_end":        dt2.strftime("%Y-%m-%d"),
            "gap_days":       delta,
            "before_event":   event1.get("description", "")[:80],
            "after_event":    event2.get("description", "")[:80],
            "severity":       severity,
            "evidence_needed": suggestions,
            "court_note": (
                f"{delta}-day gap in verified activity. "
                f"Defence may exploit this. "
                f"Obtain evidence listed to close gap before charge sheet."
            ),
        })

    return gaps


def score_event_strength(event: dict, all_events: list) -> dict:
    """
    Scores each timeline event by evidence strength.
    Returns STRONG / MEDIUM / WEAK / UNVERIFIED with score and factors.
    """
    source      = str(event.get("source", "")).lower()
    score       = 0
    factors     = []

    SOURCE_WEIGHTS = {
        "cdr":          (25, "CDR records"),
        "call_records": (25, "CDR records"),
        "anpr":         (25, "ANPR camera"),
        "surveillance": (20, "Surveillance log"),
        "bank":         (20, "Banking records"),
        "financial":    (20, "Financial records"),
        "challan":      (15, "Traffic challan"),
        "platform":     (15, "Platform metadata"),
        "isp":          (15, "ISP records"),
        "certin":       (20, "CERT-In records"),
        "cert":         (20, "CERT-In records"),
        "pdf":          (10, "Document/report"),
        "witness":      (10, "Witness statement"),
        "statement":    (10, "Statement"),
    }

    for keyword, (weight, label) in SOURCE_WEIGHTS.items():
        if keyword in source:
            score += weight
            factors.append(label)
            break

    event_date = event.get("date", "")
    same_date_sources = set(
        e.get("source", "")
        for e in all_events
        if e.get("date", "") == event_date
        and e.get("source", "") != event.get("source", "")
    )
    if len(same_date_sources) >= 2:
        score += 20
        factors.append(f"Corroborated by {len(same_date_sources)} other sources")
    elif len(same_date_sources) == 1:
        score += 10
        factors.append("Corroborated by 1 other source")

    if any(x in source for x in ["cert", "court", "ed ", "ncb", "police", "fir"]):
        score += 15
        factors.append("Official record")

    if score >= 50:
        strength = "STRONG"
    elif score >= 30:
        strength = "MEDIUM"
    elif score >= 15:
        strength = "WEAK"
    else:
        strength = "UNVERIFIED"

    return {
        "strength": strength,
        "score":    score,
        "factors":  factors,
        "display_weight": (
            "bold"   if strength == "STRONG"  else
            "normal" if strength == "MEDIUM"  else
            "light"
        ),
    }


def build_timeline_from_all_files(all_files_data: list) -> list:
    """
    Extract timeline events from structured rows across ALL source files,
    preserving per-file source attribution.

    Each element of all_files_data must be a dict with:
        "filename" : str
        "rows"     : list[dict]   — OR —
        "structured_rows" : list[dict]   (either key accepted)

    Returns a deduplicated, sorted list of timeline event dicts compatible
    with build_timeline_from_fusion output (has "normalized", "source",
    "context", "event_type" keys).
    """
    raw_events: list = []

    for file_data in (all_files_data or []):
        filename = safe_str(file_data.get("filename", "unknown"))
        rows     = file_data.get("rows") or file_data.get("structured_rows") or []

        for row in rows:
            if not isinstance(row, dict):
                continue

            # Targeted pass: columns whose names signal a date value
            for col_key, val in row.items():
                if col_key.lower().strip() not in _DATE_COLUMN_KEYS:
                    continue
                val_str = str(val).strip()
                if not val_str or val_str in ("", "None", "nan", "NaT"):
                    continue
                dt, precision = _parse_date_precision(val_str)
                if not dt:
                    # Fallback for unusual formats: day-first reading, still
                    # precision-safe (never fills fields from the clock)
                    dt, precision = _parse_date_precision(val_str, dayfirst=True)
                if not dt:
                    continue
                # Build a description from neighbouring context columns
                desc_parts = []
                for ctx_col in ("description", "particulars", "narration",
                                "caller_name", "receiver_name", "location",
                                "challan_number", "case_id", "ref"):
                    ctx_val = safe_str(row.get(ctx_col, "")).strip()
                    if ctx_val and ctx_val not in ("None", "nan", ""):
                        desc_parts.append(f"{ctx_col}: {ctx_val[:60]}")
                description = " | ".join(desc_parts) or col_key

                raw_events.append({
                    "raw":          val_str,
                    **_event_date_fields(dt, precision),
                    "source":       f"Document: {filename} [{col_key}]",
                    "context":      description,
                    "event_type":   "structured_data",
                    "source_file":  filename,
                    "source_field": col_key,
                })

    # Deduplicate on (normalised_date, source_file) — same event across two
    # passes of the same row should not appear twice
    seen: set = set()
    unique: list = []
    for ev in raw_events:
        key = (ev["normalized"], ev["source_file"], ev.get("source_field", ""))
        if key not in seen:
            seen.add(key)
            unique.append(ev)

    unique.sort(key=lambda x: x.get("datetime_obj", datetime.min))
    return unique
