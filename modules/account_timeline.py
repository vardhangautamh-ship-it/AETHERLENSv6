"""
AetherLens — Account Timeline Module
Builds a chronological account creation history from Person Object join dates.
Performs 6 pattern checks: rapid creation, recent creation, dormant->active,
oldest account, platform gaps, and platform order / early-adopter detection.
"""

import datetime
import re

# ── Platform launch dates (for early-adopter detection) ───────────────────────

_PLATFORM_LAUNCH = {
    "GitHub":    datetime.date(2008, 4, 10),
    "Twitter":   datetime.date(2006, 7, 15),
    "Reddit":    datetime.date(2005, 6, 23),
    "YouTube":   datetime.date(2005, 2, 14),
    "LinkedIn":  datetime.date(2003, 5, 5),
    "Instagram": datetime.date(2010, 10, 6),
    "TikTok":    datetime.date(2018, 9, 12),
    "Facebook":  datetime.date(2004, 2, 4),
    "Pinterest": datetime.date(2010, 3, 1),
    "Snapchat":  datetime.date(2011, 7, 8),
}

# Confidence weights for sorting (EXACT > APPROXIMATE > NOT AVAILABLE)
_CONF_WEIGHT = {"EXACT": 2, "APPROXIMATE": 1, "NOT AVAILABLE": 0, "": 0}


# ── Helpers ────────────────────────────────────────────────────────────────────

def _to_date(join_info: dict):
    """
    Convert join_info dict to a datetime.date, or None if unavailable.
    Tries join_timestamp first (most precise), then reconstructs from year/month.
    """
    ts = join_info.get("join_timestamp", "")
    if ts:
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
            try:
                return datetime.datetime.strptime(ts[:19], fmt).date()
            except ValueError:
                pass

    year = join_info.get("join_year", 0)
    month_str = join_info.get("join_month", "")
    if year and year > 1990:
        month = 1
        if month_str:
            try:
                month = datetime.datetime.strptime(month_str[:3], "%b").month
            except ValueError:
                try:
                    month = datetime.datetime.strptime(month_str, "%B").month
                except ValueError:
                    pass
        try:
            return datetime.date(int(year), month, 1)
        except ValueError:
            pass
    return None


def _collect_join_dates(person: dict) -> list[dict]:
    """
    Gather join dates from:
      1. person["join_dates"]  — main platform lookups
      2. confirmed_linked_profiles[].public_data — linked accounts
      3. potential_linked_profiles[].public_data — potential accounts (lower weight)
    Returns a list of dicts with: platform, date, confidence, source, profile_type
    """
    entries = []

    # Main join dates
    for platform, jinfo in person.get("join_dates", {}).items():
        if not isinstance(jinfo, dict):
            continue
        d = _to_date(jinfo)
        if d:
            entries.append({
                "platform":    platform,
                "date":        d,
                "join_year":   jinfo.get("join_year", d.year),
                "join_month":  jinfo.get("join_month", ""),
                "join_date_str": jinfo.get("join_date", str(d)),
                "confidence":  jinfo.get("date_confidence", ""),
                "source":      jinfo.get("date_source", ""),
                "age_years":   jinfo.get("account_age_years", 0),
                "last_active": jinfo.get("last_active", ""),
                "profile_type": "primary",
            })

    # Confirmed linked profiles
    for lp in person.get("confirmed_linked_profiles", []):
        pub = lp.get("public_data", {})
        platform = lp.get("platform", "Unknown")
        d = _to_date(pub)
        if d:
            entries.append({
                "platform":    platform,
                "date":        d,
                "join_year":   pub.get("join_year", d.year),
                "join_month":  pub.get("join_month", ""),
                "join_date_str": pub.get("join_date", str(d)),
                "confidence":  pub.get("date_confidence", "APPROXIMATE"),
                "source":      pub.get("date_source", f"{platform} linked profile"),
                "age_years":   pub.get("account_age_years", 0),
                "last_active": pub.get("last_active", ""),
                "profile_type": "confirmed_linked",
            })

    # Potential linked profiles (lower confidence)
    for lp in person.get("potential_linked_profiles", []):
        pub = lp.get("public_data", {})
        platform = lp.get("platform", "Unknown")
        d = _to_date(pub)
        if d:
            entries.append({
                "platform":    platform,
                "date":        d,
                "join_year":   pub.get("join_year", d.year),
                "join_month":  pub.get("join_month", ""),
                "join_date_str": pub.get("join_date", str(d)),
                "confidence":  "APPROXIMATE",
                "source":      pub.get("date_source", f"{platform} potential profile"),
                "age_years":   pub.get("account_age_years", 0),
                "last_active": pub.get("last_active", ""),
                "profile_type": "potential_linked",
            })

    # Sort by date ascending
    entries.sort(key=lambda e: e["date"])
    return entries


# ── Pattern analysis ───────────────────────────────────────────────────────────

def _analyse_patterns(entries: list[dict]) -> list[dict]:
    """
    Run 6 pattern checks on sorted account creation entries.
    Returns a list of flag dicts: {flag, severity, detail}
    Severity: "HIGH", "MEDIUM", "LOW", "INFO"
    """
    flags = []
    if not entries:
        return flags

    today = datetime.date.today()
    dates = [e["date"] for e in entries]

    # ── 1. Rapid creation ─────────────────────────────────────────────────────
    # Multiple accounts created within 90 days of each other
    for i, e1 in enumerate(entries):
        for e2 in entries[i + 1:]:
            delta = abs((e2["date"] - e1["date"]).days)
            if delta <= 90:
                flags.append({
                    "flag": "RAPID_ACCOUNT_CREATION",
                    "severity": "HIGH",
                    "detail": (
                        f"{e1['platform']} and {e2['platform']} created within "
                        f"{delta} days of each other "
                        f"({e1['join_date_str']} / {e2['join_date_str']}). "
                        "May indicate coordinated account setup or identity migration."
                    ),
                })
                break
        else:
            continue
        break

    # ── 2. Recent creation ────────────────────────────────────────────────────
    # Any account created within the last 180 days
    for e in entries:
        age_days = (today - e["date"]).days
        if 0 <= age_days <= 180:
            flags.append({
                "flag": "RECENTLY_CREATED_ACCOUNT",
                "severity": "MEDIUM",
                "detail": (
                    f"{e['platform']} account created {age_days} days ago "
                    f"({e['join_date_str']}). New account — limited history available."
                ),
            })

    # ── 3. Dormant then active ────────────────────────────────────────────────
    # Account created 3+ years ago with no recent activity indication
    for e in entries:
        age_years = e.get("age_years", 0)
        last = e.get("last_active", "")
        if age_years >= 3 and not last:
            flags.append({
                "flag": "DORMANT_ACCOUNT",
                "severity": "LOW",
                "detail": (
                    f"{e['platform']} account is {age_years}+ years old "
                    f"(joined {e['join_date_str']}) with no last-active date recorded. "
                    "Could be dormant or abandoned."
                ),
            })

    # ── 4. Oldest account ─────────────────────────────────────────────────────
    oldest = entries[0]
    flags.append({
        "flag": "OLDEST_ACCOUNT",
        "severity": "INFO",
        "detail": (
            f"Oldest account: {oldest['platform']} joined {oldest['join_date_str']} "
            f"({oldest.get('age_years', 0)} years ago). "
            f"Confidence: {oldest['confidence']}."
        ),
    })

    # ── 5. Platform gap ───────────────────────────────────────────────────────
    # Largest gap between consecutive account creations
    if len(dates) >= 2:
        gaps = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]
        max_gap = max(gaps)
        max_idx = gaps.index(max_gap)
        if max_gap > 730:  # > 2 years gap
            flags.append({
                "flag": "LARGE_PLATFORM_GAP",
                "severity": "LOW",
                "detail": (
                    f"{max_gap // 365:.1f}-year gap between "
                    f"{entries[max_idx]['platform']} ({entries[max_idx]['join_date_str']}) "
                    f"and {entries[max_idx + 1]['platform']} "
                    f"({entries[max_idx + 1]['join_date_str']}). "
                    "May indicate a period of reduced online presence."
                ),
            })

    # ── 6. Early adopter / platform order ─────────────────────────────────────
    for e in entries:
        launch = _PLATFORM_LAUNCH.get(e["platform"])
        if launch:
            days_after_launch = (e["date"] - launch).days
            if 0 <= days_after_launch <= 365:
                flags.append({
                    "flag": "EARLY_ADOPTER",
                    "severity": "INFO",
                    "detail": (
                        f"Joined {e['platform']} within {days_after_launch} days of its launch "
                        f"({e['join_date_str']} vs. platform launch {launch.strftime('%B %d, %Y')}). "
                        "Suggests tech-savvy early adopter."
                    ),
                })

    return flags


# ── Main entry point ───────────────────────────────────────────────────────────

def build_account_timeline(person: dict) -> dict:
    """
    Build a chronological account creation timeline from a Person Object.

    Returns dict:
      {
        "timeline":          [...],   # sorted list of account entries
        "oldest_account":    {...},   # earliest entry
        "newest_account":    {...},   # most recent entry
        "flags":             [...],   # pattern analysis flags
        "digital_age_years": int,     # years since oldest account
        "platforms_with_dates": int,  # count of platforms with date data
      }
    """
    entries = _collect_join_dates(person)

    if not entries:
        return {
            "timeline":             [],
            "oldest_account":       {},
            "newest_account":       {},
            "flags":                [{"flag": "NO_DATE_DATA", "severity": "INFO",
                                      "detail": "No account creation dates available."}],
            "digital_age_years":    0,
            "platforms_with_dates": 0,
        }

    flags = _analyse_patterns(entries)

    oldest = entries[0]
    newest = entries[-1]
    today  = datetime.date.today()
    digital_age = max(0, (today - oldest["date"]).days) // 365

    # Serialise dates to strings for JSON safety
    serialised = []
    for e in entries:
        row = dict(e)
        row["date"] = e["date"].isoformat()
        serialised.append(row)

    return {
        "timeline":             serialised,
        "oldest_account":       {k: (v.isoformat() if isinstance(v, datetime.date) else v)
                                 for k, v in oldest.items()},
        "newest_account":       {k: (v.isoformat() if isinstance(v, datetime.date) else v)
                                 for k, v in newest.items()},
        "flags":                flags,
        "digital_age_years":    digital_age,
        "platforms_with_dates": len(entries),
    }


# ── Plotly chart builder ───────────────────────────────────────────────────────

def build_timeline_chart(timeline: list[dict]) -> "go.Figure | None":
    """
    Build a horizontal scatter Plotly figure from timeline entries.
    X = year (float for month precision), Y = platform name.
    Color: EXACT = deep purple, APPROXIMATE = dusty purple, NOT AVAILABLE = grey.

    Returns None if Plotly is unavailable or timeline is empty.
    """
    if not timeline:
        return None
    try:
        import plotly.graph_objects as go
    except ImportError:
        return None

    _COLORS = {
        "EXACT":         "#7B2FBE",
        "APPROXIMATE":   "#B08FD4",
        "NOT AVAILABLE": "#888888",
        "":              "#888888",
    }

    x_vals, y_vals, colors, hover_texts, sizes = [], [], [], [], []

    for e in timeline:
        platform   = e.get("platform", "Unknown")
        confidence = e.get("confidence", "")
        date_str   = e.get("join_date_str", e.get("date", ""))
        age        = e.get("age_years", 0)
        prof_type  = e.get("profile_type", "primary")
        source     = e.get("source", "")

        # Convert date string to fractional year for X axis
        try:
            d = datetime.date.fromisoformat(e["date"])
            x = d.year + (d.month - 1) / 12
        except Exception:
            x = e.get("join_year", 0) or 0
        if not x:
            continue

        marker_size = 14 if confidence == "EXACT" else 10
        hover = (
            f"<b>{platform}</b><br>"
            f"Joined: {date_str}<br>"
            f"Confidence: {confidence}<br>"
            f"Account age: {age} years<br>"
            f"Profile type: {prof_type}<br>"
            f"Source: {source}"
        )

        x_vals.append(x)
        y_vals.append(platform)
        colors.append(_COLORS.get(confidence, "#888888"))
        hover_texts.append(hover)
        sizes.append(marker_size)

    if not x_vals:
        return None

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=x_vals,
        y=y_vals,
        mode="markers",
        marker=dict(
            color=colors,
            size=sizes,
            symbol="circle",
            line=dict(color="white", width=1),
        ),
        hovertemplate="%{customdata}<extra></extra>",
        customdata=hover_texts,
        name="Account Creation",
    ))

    # Legend annotations for confidence levels
    for conf, col in _COLORS.items():
        if conf:
            fig.add_trace(go.Scatter(
                x=[None], y=[None],
                mode="markers",
                marker=dict(color=col, size=10, symbol="circle"),
                name=conf,
                showlegend=True,
            ))

    fig.update_layout(
        title=dict(text="Digital Identity Timeline — Account Creation Dates",
                   font=dict(color="#E0D6F5", size=16)),
        paper_bgcolor="#1A1A2E",
        plot_bgcolor="#16213E",
        font=dict(color="#E0D6F5"),
        xaxis=dict(
            title="Year",
            tickformat=".0f",
            gridcolor="#2A2A4A",
            color="#E0D6F5",
        ),
        yaxis=dict(
            title="Platform",
            gridcolor="#2A2A4A",
            color="#E0D6F5",
            automargin=True,
        ),
        legend=dict(
            title="Date Confidence",
            bgcolor="rgba(0,0,0,0)",
            bordercolor="#7B2FBE",
            borderwidth=1,
            font=dict(color="#E0D6F5"),
        ),
        height=max(300, 60 * len(set(y_vals)) + 100),
        margin=dict(l=120, r=40, t=60, b=60),
        hovermode="closest",
    )

    return fig
