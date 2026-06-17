"""
AetherLens — Search Module
Direct platform profile lookup (handle/platform) + multi-source name search.

Handle query format:  {handle}/{platform}
  Examples:
    elonmusk/twitter     -> fetch https://x.com/elonmusk
    nasa/ig              -> fetch https://www.instagram.com/nasa/
    torvalds/github      -> https://api.github.com/users/torvalds
    spez/reddit          -> https://www.reddit.com/user/spez/about.json
    johndoe/linkedin     -> fetch https://www.linkedin.com/in/johndoe/
    mkbhd/yt             -> fetch https://www.youtube.com/@mkbhd

Plain name query: DuckDuckGo + Wikipedia + Google News parallel search.
"""

import re
import json
import time
import sqlite3
import datetime
import threading
import requests
import feedparser

try:
    from ddgs import DDGS
except ImportError:
    from duckduckgo_search import DDGS

import config


# ── Browser-like headers for HTML scraping ─────────────────────────────────────

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Cache-Control": "max-age=0",
}

_API_HEADERS = {
    "User-Agent": "AetherLens/1.0 OSINT Research Tool",
    "Accept": "application/json",
}


# ── Audit log ──────────────────────────────────────────────────────────────────

def log_search(user_id: str, query: str, results_count: int, search_type: str = "name"):
    try:
        conn = sqlite3.connect(str(config.DATABASE_PATH))
        now  = datetime.datetime.utcnow().isoformat()
        conn.execute(
            "INSERT INTO audit_log (event, username, detail, timestamp) VALUES (?,?,?,?)",
            ("SEARCH", user_id, json.dumps({"query": query, "type": search_type, "results": results_count}), now),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


# ── Confidence scoring ─────────────────────────────────────────────────────────

def _score(query: str, title: str, snippet: str) -> int:
    q_terms = set(query.lower().split())
    text    = (title + " " + snippet).lower()
    matched = sum(1 for t in q_terms if t in text)
    base    = int((matched / max(len(q_terms), 1)) * 70)
    if query.lower() in text:
        base = min(base + 25, 100)
    return max(base, 5)


# ══════════════════════════════════════════════════════════════════════════════
# HTML META SCRAPER (for platforms without public JSON APIs)
# ══════════════════════════════════════════════════════════════════════════════

def _scrape_meta(url: str) -> dict:
    """
    Fetch a public profile URL and extract structured data from meta tags and JSON-LD.
    Returns a dict. Never raises.
    """
    out = {
        "display_name": "",
        "bio":          "",
        "image_url":    "",
        "extra":        {},
        "ok":           False,
        "status_code":  0,
        "error":        "",
        "raw_html":     "",
    }
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=15, allow_redirects=True)
        out["status_code"] = resp.status_code
        if resp.status_code not in (200, 301, 302):
            out["error"] = f"HTTP {resp.status_code}"
            return out
        html = resp.text
        out["ok"]       = True
        out["raw_html"] = html[:50000]

        def _meta(attr_name: str, attr_type: str = "name") -> str:
            for tmpl in [
                rf'<meta\s+{attr_type}="{re.escape(attr_name)}"\s+content="([^"]*)"',
                rf'<meta\s+content="([^"]*)"\s+{attr_type}="{re.escape(attr_name)}"',
                rf"<meta\s+{attr_type}='{re.escape(attr_name)}'\s+content='([^']*)'",
                rf"<meta\s+content='([^']*)'\s+{attr_type}='{re.escape(attr_name)}'",
            ]:
                m = re.search(tmpl, html, re.I)
                if m:
                    return m.group(1).strip()
            return ""

        raw_title = (
            _meta("og:title",          "property") or
            _meta("twitter:title",     "name")     or
            ""
        )
        raw_desc = (
            _meta("og:description",    "property") or
            _meta("twitter:description", "name")   or
            _meta("description",       "name")     or
            ""
        )
        if not raw_title:
            m = re.search(r"<title[^>]*>([^<]+)</title>", html, re.I)
            if m:
                raw_title = m.group(1).strip()

        out["image_url"] = (
            _meta("og:image",       "property") or
            _meta("twitter:image",  "name")     or
            ""
        )

        # Decode HTML entities
        def _clean(s: str) -> str:
            return (s
                .replace("&amp;",  "&").replace("&lt;",   "<")
                .replace("&gt;",   ">").replace("&quot;", '"')
                .replace("&#39;",  "'").replace("&nbsp;", " ")
                .replace("&#x27;", "'")
            )
        out["display_name"] = _clean(raw_title)
        out["bio"]          = _clean(raw_desc)

        # JSON-LD structured data
        for jld_raw in re.findall(
            r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
            html, re.S | re.I
        ):
            try:
                jld = json.loads(jld_raw)
                if isinstance(jld, dict):
                    out["extra"]["json_ld"] = jld
                    break
                if isinstance(jld, list) and jld:
                    out["extra"]["json_ld"] = jld[0]
                    break
            except Exception:
                pass

        # YouTube channel subscriber count (embedded in ytInitialData)
        if "youtube.com" in url:
            m = re.search(r'"subscriberCountText"\s*:\s*\{"simpleText"\s*:\s*"([^"]+)"', html)
            if m:
                out["extra"]["subscribers"] = m.group(1)
            # Channel name from ytInitialData
            m2 = re.search(r'"channelMetadataRenderer"\s*:\s*\{[^}]*"title"\s*:\s*"([^"]+)"', html)
            if m2:
                out["display_name"] = out["display_name"] or m2.group(1)

        # Instagram follower stats from meta description
        # Instagram meta desc format: "X Followers, Y Following, Z Posts"
        if "instagram.com" in url and out["bio"]:
            m = re.search(r"([\d,Kk.]+)\s+Followers?,\s*([\d,Kk.]+)\s+Following", out["bio"])
            if m:
                out["extra"]["followers"] = m.group(1)
                out["extra"]["following"] = m.group(2)

    except requests.exceptions.ConnectionError as e:
        out["error"] = f"Connection failed: {e}"
    except requests.exceptions.Timeout:
        out["error"] = "Request timed out"
    except Exception as e:
        out["error"] = str(e)

    return out


# ── Join date helper ───────────────────────────────────────────────────────────

def _join_meta(
    join_dt,          # datetime.datetime or None
    confidence: str,  # "EXACT" | "APPROXIMATE" | "NOT AVAILABLE"
    source:     str,
    last_active: str = "",
) -> dict:
    """
    Build a standardised join-date metadata dict.
    `join_dt` may be datetime.datetime, datetime.date, or None.
    """
    today = datetime.datetime.utcnow().date()
    if join_dt is not None:
        jd = join_dt.date() if hasattr(join_dt, "date") else join_dt
        age_days  = max(0, (today - jd).days)
        age_years = age_days // 365
        return {
            "join_date":         jd.strftime("%B %d, %Y"),
            "join_year":         jd.year,
            "join_month":        jd.strftime("%B"),
            "join_timestamp":    join_dt.isoformat() if hasattr(join_dt, "isoformat") else str(join_dt),
            "account_age_years": age_years,
            "account_age_days":  age_days,
            "last_active":       last_active,
            "date_confidence":   confidence,
            "date_source":       source,
        }
    return {
        "join_date":         "",
        "join_year":         0,
        "join_month":        "",
        "join_timestamp":    "",
        "account_age_years": 0,
        "account_age_days":  0,
        "last_active":       last_active,
        "date_confidence":   confidence,
        "date_source":       source,
    }


def _parse_join_str(text: str):
    """
    Try to parse a human join-date string (e.g. 'March 2009', 'Apr 6, 2006')
    into a datetime.datetime. Returns None on failure.
    """
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%B %Y", "%b %Y", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(text.strip(), fmt)
        except ValueError:
            pass
    # Try year only
    m = re.search(r"\b(20\d{2}|19\d{2})\b", text)
    if m:
        try:
            return datetime.datetime(int(m.group(1)), 1, 1)
        except ValueError:
            pass
    return None


def _not_found_result(platform_label: str, handle: str, url: str) -> dict:
    """The platform confirmed this handle does NOT exist (HTTP 404)."""
    return {
        "full_name":  handle,
        "platform":   platform_label,
        "snippet":    f"Profile not found at {url}",
        "url":        url,
        "confidence": 0,
        "status":     "not_found",
    }


def _lookup_failed_result(platform_label: str, handle: str, url: str, reason: str) -> dict:
    """
    The lookup could NOT be completed (rate limit, 5xx, timeout, network error).

    This is deliberately distinct from `_not_found_result`: a failed check means
    "we don't know whether this account exists", which is very different from
    "this account does not exist". Conflating the two led the analyst to conclude
    a subject had no presence on a platform when the request was merely throttled.
    """
    return {
        "full_name":  handle,
        "platform":   platform_label,
        "snippet":    f"Lookup unavailable ({reason}) — could not confirm {url}",
        "url":        url,
        "confidence": 0,
        "status":     "lookup_failed",
        "error":      reason,
    }


def _github_headers() -> dict:
    """
    Headers for GitHub API calls. Attaches a bearer token when GITHUB_TOKEN is
    configured, lifting the anonymous 60 req/hr cap to 5,000 req/hr. The token is
    read at call time (not import time) so Streamlit-secrets injection is honoured.
    """
    headers = {**_API_HEADERS, "Accept": "application/vnd.github.v3+json"}
    try:
        token = (getattr(config, "GITHUB_TOKEN", "") or "").strip()
    except Exception:
        token = ""
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _github_rate_limited(resp) -> bool:
    """True if a GitHub response is a rate-limit rejection (403/429 + remaining 0)."""
    if resp.status_code not in (403, 429):
        return False
    remaining = resp.headers.get("X-RateLimit-Remaining")
    # If the header is absent we still treat a 403/429 as throttling rather than
    # a genuine "not found".
    return remaining is None or remaining == "0"


# ══════════════════════════════════════════════════════════════════════════════
# PLATFORM HANDLE PATTERNS — format: {handle}/{platform}
# ══════════════════════════════════════════════════════════════════════════════

_HANDLE_PATTERNS = {
    "twitter":   re.compile(r"^(.+)/twitter$",  re.IGNORECASE),
    "instagram": re.compile(r"^(.+)/ig$",        re.IGNORECASE),
    "github":    re.compile(r"^(.+)/github$",    re.IGNORECASE),
    "reddit":    re.compile(r"^(.+)/reddit$",    re.IGNORECASE),
    "linkedin":  re.compile(r"^(.+)/linkedin$",  re.IGNORECASE),
    "youtube":   re.compile(r"^(.+)/yt$",        re.IGNORECASE),
}

_PLATFORM_LABELS = {
    "twitter":   "X / Twitter",
    "instagram": "Instagram",
    "github":    "GitHub",
    "reddit":    "Reddit",
    "linkedin":  "LinkedIn",
    "youtube":   "YouTube",
}

_PLATFORM_BASE_URLS = {
    "twitter":   "https://x.com/{handle}",
    "instagram": "https://www.instagram.com/{handle}/",
    "github":    "https://github.com/{handle}",
    "reddit":    "https://www.reddit.com/user/{handle}",
    "linkedin":  "https://www.linkedin.com/in/{handle}/",
    "youtube":   "https://www.youtube.com/@{handle}",
}


def parse_handle_query(query: str) -> dict | None:
    """
    Detect if query is a handle query ({handle}/{platform}).
    Returns {"platform": str, "handle": str} or None.
    """
    q = query.strip()
    for platform, pattern in _HANDLE_PATTERNS.items():
        m = pattern.match(q)
        if m:
            return {"platform": platform, "handle": m.group(1).strip()}
    return None


# ══════════════════════════════════════════════════════════════════════════════
# DIRECT PLATFORM LOOKUPS — confidence 100
# ══════════════════════════════════════════════════════════════════════════════

def lookup_github(handle: str) -> dict:
    """Direct GitHub API lookup — returns single result dict."""
    url = _PLATFORM_BASE_URLS["github"].format(handle=handle)
    try:
        resp = requests.get(
            f"https://api.github.com/users/{handle}",
            headers=_github_headers(),
            timeout=12,
        )
        if resp.status_code == 404:
            return _not_found_result("GitHub", handle, url)
        if _github_rate_limited(resp):
            return _lookup_failed_result(
                "GitHub", handle, url,
                "HTTP 403 rate-limited — set GITHUB_TOKEN to raise the API limit")
        if resp.status_code != 200:
            return _lookup_failed_result("GitHub", handle, url, f"HTTP {resp.status_code}")

        d         = resp.json()
        name      = d.get("name") or handle
        bio       = d.get("bio") or ""
        repos     = d.get("public_repos", 0)
        follows   = d.get("followers", 0)
        fwing     = d.get("following", 0)
        loc       = d.get("location") or ""
        company   = d.get("company") or ""
        created   = d.get("created_at", "")
        updated   = d.get("updated_at", "")
        blog      = d.get("blog") or ""
        email     = d.get("email") or ""
        avatar    = d.get("avatar_url") or ""

        # ── Join date (EXACT from API) ────────────────────────────────────────
        jdt = None
        if created:
            try:
                jdt = datetime.datetime.fromisoformat(created.replace("Z", ""))
            except Exception:
                pass
        last_active_str = updated[:10] if updated else ""
        join_info = _join_meta(jdt, "EXACT", "GitHub API created_at", last_active=last_active_str)

        joined_display = created[:10] if created else ""
        parts = [f"Name: {name}"]
        if bio:             parts.append(f"Bio: {bio}")
        parts.append(f"Repos: {repos}  |  Followers: {follows}  |  Following: {fwing}")
        if loc:             parts.append(f"Location: {loc}")
        if company:         parts.append(f"Company: {company}")
        if join_info["join_date"]: parts.append(f"Joined: {join_info['join_date']}")
        if email:           parts.append(f"Email: {email}")
        if blog:            parts.append(f"Website: {blog}")

        result = {
            "full_name":    name,
            "platform":     "GitHub",
            "snippet":      " | ".join(parts)[:400],
            "url":          d.get("html_url", url),
            "confidence":   100,
            "raw":          d,
            "display_name": name,
            "bio":          bio,
            "location":     loc,
            "image_url":    avatar,
            "extra": {
                "repos": repos, "followers": follows, "following": fwing,
                "joined": joined_display, "company": company, "blog": blog, "email": email,
            },
        }
        result.update(join_info)
        return result
    except Exception as e:
        return {
            "full_name":  handle,
            "platform":   "GitHub",
            "snippet":    f"Error fetching GitHub profile: {e}",
            "url":        url,
            "confidence": 0,
        }


def lookup_reddit(handle: str) -> dict:
    """Direct Reddit JSON API lookup — returns single result dict."""
    url = _PLATFORM_BASE_URLS["reddit"].format(handle=handle)
    try:
        resp = requests.get(
            f"https://www.reddit.com/user/{handle}/about.json",
            headers=_API_HEADERS,
            timeout=12,
        )
        if resp.status_code == 404:
            return _not_found_result("Reddit", handle, url)
        if resp.status_code in (403, 429):
            return _lookup_failed_result(
                "Reddit", handle, url, f"HTTP {resp.status_code} rate-limited")
        if resp.status_code != 200:
            return _lookup_failed_result("Reddit", handle, url, f"HTTP {resp.status_code}")

        d = resp.json().get("data", {})
        name          = d.get("name", handle)
        karma_post    = d.get("link_karma", 0)
        karma_comment = d.get("comment_karma", 0)
        karma_total   = karma_post + karma_comment
        icon          = d.get("icon_img") or ""
        created_utc   = d.get("created_utc", 0)
        verified      = d.get("verified", False)
        premium       = d.get("is_gold", False)

        # ── Join date (EXACT from API unix timestamp) ─────────────────────────
        jdt = datetime.datetime.utcfromtimestamp(created_utc) if created_utc else None
        join_info = _join_meta(jdt, "EXACT", "Reddit API created_utc")
        joined_display = join_info["join_date"]

        parts = [f"u/{name}"]
        parts.append(f"Post karma: {karma_post:,}  |  Comment karma: {karma_comment:,}  |  Total: {karma_total:,}")
        if joined_display: parts.append(f"Joined: {joined_display}")
        if verified:       parts.append("Email verified")
        if premium:        parts.append("Reddit Premium")

        result = {
            "full_name":    name,
            "platform":     "Reddit",
            "snippet":      " | ".join(parts)[:400],
            "url":          url,
            "confidence":   100,
            "raw":          d,
            "display_name": name,
            "bio":          "",
            "image_url":    icon,
            "extra": {
                "karma_post": karma_post, "karma_comment": karma_comment,
                "joined": joined_display, "verified": verified,
            },
        }
        result.update(join_info)
        return result
    except Exception as e:
        return {
            "full_name":  handle,
            "platform":   "Reddit",
            "snippet":    f"Error fetching Reddit profile: {e}",
            "url":        url,
            "confidence": 0,
        }


def lookup_twitter(handle: str) -> dict:
    """
    Fetch Twitter/X public profile page and extract available meta data.
    Confidence: 100 (exact URL constructed — profile exists or not).
    """
    url   = _PLATFORM_BASE_URLS["twitter"].format(handle=handle)
    label = "X / Twitter"

    meta = _scrape_meta(url)

    if not meta["ok"]:
        # If 404, profile does not exist
        if meta["status_code"] == 404:
            return _not_found_result(label, handle, url)
        # Other error — still return with URL, note the issue
        return {
            "full_name":  handle,
            "platform":   label,
            "snippet":    f"Profile URL: {url} | Note: {meta['error'] or 'Page unavailable — may require login or JS'}",
            "url":        url,
            "confidence": 100,
            "display_name": handle,
            "bio":        "",
            "image_url":  "",
        }

    display = meta["display_name"] or handle
    bio     = meta["bio"] or "Profile exists — bio requires JS rendering"

    # Twitter/X OG title usually is "Display Name (@handle)"
    display = re.sub(r"\s*\(@?[^\)]+\)\s*(/\s*X)?$", "", display).strip() or display

    # ── Join date: look for "Joined Month Year" in raw HTML ───────────────────
    html_raw = meta.get("raw_html", "")
    join_info = _join_meta(None, "NOT AVAILABLE", "Twitter/X page HTML")
    for pattern in [
        r'Joined\s+(\w+\s+\d{4})',
        r'"joinedDate"\s*:\s*"([^"]+)"',
        r'joinDate.*?(\w+\s+\d{4})',
    ]:
        m = re.search(pattern, html_raw, re.I)
        if m:
            jdt = _parse_join_str(m.group(1))
            if jdt:
                join_info = _join_meta(jdt, "APPROXIMATE", "Twitter/X page HTML")
                break
    # Also check OG description
    if not join_info["join_year"] and bio:
        m = re.search(r'Joined\s+(\w+\s+\d{4})', bio, re.I)
        if m:
            jdt = _parse_join_str(m.group(1))
            if jdt:
                join_info = _join_meta(jdt, "APPROXIMATE", "Twitter OG description")

    parts = [f"@{handle}"]
    if display and display.lower() != handle.lower():
        parts.insert(0, display)
    if bio:
        parts.append(bio[:200])
    if join_info["join_date"]:
        parts.append(f"Joined: {join_info['join_date']}")
    parts.append(f"Profile: {url}")

    result = {
        "full_name":    display,
        "platform":     label,
        "snippet":      " | ".join(parts)[:400],
        "url":          url,
        "confidence":   100,
        "display_name": display,
        "bio":          bio,
        "image_url":    meta["image_url"],
        "extra":        meta["extra"],
    }
    result.update(join_info)
    return result


def lookup_instagram(handle: str) -> dict:
    """
    Fetch Instagram public profile page and extract available meta data.
    Instagram's meta description often contains follower/following/post counts.
    Confidence: 100 (exact URL).
    """
    url   = _PLATFORM_BASE_URLS["instagram"].format(handle=handle)
    label = "Instagram"

    meta = _scrape_meta(url)

    if not meta["ok"]:
        if meta["status_code"] == 404:
            return _not_found_result(label, handle, url)
        return {
            "full_name":  handle,
            "platform":   label,
            "snippet":    f"Profile URL: {url} | Note: {meta['error'] or 'Page requires JS — verify manually'}",
            "url":        url,
            "confidence": 100,
            "display_name": handle,
            "bio":        "",
            "image_url":  "",
        }

    display = meta["display_name"] or handle
    bio     = meta["bio"] or ""

    display = re.sub(r"\s*\(@?[^\)]+\)\s*[•·].*$", "", display).strip() or display
    display = re.sub(r"\s*[•·]\s*Instagram.*$", "", display).strip() or display

    # ── Join date: not publicly available on Instagram ─────────────────────
    join_info = _join_meta(None, "NOT AVAILABLE", "Instagram - not publicly available")

    parts = [f"@{handle}"]
    if display and display.lower() != handle.lower():
        parts.insert(0, display)

    extra  = meta.get("extra", {})
    if extra.get("followers"):
        parts.append(f"Followers: {extra['followers']}")
    if extra.get("following"):
        parts.append(f"Following: {extra['following']}")
    if bio:
        parts.append(bio[:200])
    parts.append(f"Profile: {url}")

    result = {
        "full_name":    display,
        "platform":     label,
        "snippet":      " | ".join(parts)[:400],
        "url":          url,
        "confidence":   100,
        "display_name": display,
        "bio":          bio,
        "image_url":    meta["image_url"],
        "extra":        extra,
    }
    result.update(join_info)
    return result


def lookup_linkedin(handle: str) -> dict:
    """
    Construct LinkedIn public profile URL and attempt to extract available data.
    LinkedIn restricts most content without login.
    Confidence: 100 (exact URL).
    """
    url   = _PLATFORM_BASE_URLS["linkedin"].format(handle=handle)
    label = "LinkedIn"

    meta = _scrape_meta(url)

    display = meta["display_name"] or handle
    bio     = meta["bio"] or ""

    display = re.sub(r"\s*\|\s*LinkedIn.*$", "", display).strip() or display
    bio     = re.sub(r"See.+LinkedIn.*$", "", bio).strip()

    if not bio:
        bio = "LinkedIn profile — login required to view full details"

    # ── Join date: look for "Member since" in raw HTML ──────────────────────
    html_raw  = meta.get("raw_html", "")
    join_info = _join_meta(None, "NOT AVAILABLE", "LinkedIn public profile")
    for pattern in [
        r'Member\s+since\s+(\w+\s+\d{4}|\d{4})',
        r'"memberSince"\s*:\s*"([^"]+)"',
        r'joinedAt.*?(\d{4})',
    ]:
        m = re.search(pattern, html_raw, re.I)
        if m:
            jdt = _parse_join_str(m.group(1))
            if jdt:
                join_info = _join_meta(jdt, "APPROXIMATE", "LinkedIn public profile HTML")
                break

    parts = [display] if display and display != handle else [handle]
    parts.append(bio[:200])
    if join_info["join_date"]:
        parts.append(f"Member since: {join_info['join_date']}")
    parts.append(f"Profile: {url}")

    result = {
        "full_name":    display or handle,
        "platform":     label,
        "snippet":      " | ".join(parts)[:400],
        "url":          url,
        "confidence":   100,
        "display_name": display or handle,
        "bio":          bio,
        "image_url":    meta["image_url"],
        "extra":        meta.get("extra", {}),
    }
    result.update(join_info)
    return result


def lookup_youtube(handle: str) -> dict:
    """
    Fetch YouTube channel page (@handle) and extract available meta data.
    YouTube has good meta tags and sometimes JSON-LD.
    Confidence: 100 (exact URL).
    """
    url   = _PLATFORM_BASE_URLS["youtube"].format(handle=handle)
    label = "YouTube"

    meta = _scrape_meta(url)

    if not meta["ok"]:
        if meta["status_code"] == 404:
            return _not_found_result(label, handle, url)
        return {
            "full_name":  handle,
            "platform":   label,
            "snippet":    f"Channel URL: {url} | Note: {meta['error'] or 'Unavailable'}",
            "url":        url,
            "confidence": 100,
            "display_name": handle,
            "bio":        "",
            "image_url":  "",
        }

    display = meta["display_name"] or handle
    bio     = meta["bio"] or ""
    extra   = meta.get("extra", {})

    display = re.sub(r"\s*-\s*YouTube$", "", display).strip() or display

    # ── Join date: look for "Joined Month DD, YYYY" in raw HTML ──────────────
    html_raw  = meta.get("raw_html", "")
    join_info = _join_meta(None, "NOT AVAILABLE", "YouTube channel page")
    for pattern in [
        r'Joined\s+(\w+\s+\d+,\s+\d{4})',   # "Joined Apr 6, 2006"
        r'Joined\s+(\w+\s+\d{4})',            # "Joined April 2006"
        r'"joinedDateText"[^"]*"([^"]+)"',    # ytInitialData
        r'"publishedAt"\s*:\s*"(\d{4}-\d{2}-\d{2})',  # ISO in ytInitialData
    ]:
        m = re.search(pattern, html_raw, re.I)
        if m:
            jdt = _parse_join_str(m.group(1))
            if jdt:
                conf = "EXACT" if "publishedAt" in pattern else "APPROXIMATE"
                join_info = _join_meta(jdt, conf, "YouTube channel page HTML")
                break

    parts = [display]
    if extra.get("subscribers"):
        parts.append(f"Subscribers: {extra['subscribers']}")
    if bio:
        parts.append(bio[:200])
    if join_info["join_date"]:
        parts.append(f"Joined: {join_info['join_date']}")
    parts.append(f"Channel: {url}")

    result = {
        "full_name":    display,
        "platform":     label,
        "snippet":      " | ".join(parts)[:400],
        "url":          url,
        "confidence":   100,
        "display_name": display,
        "bio":          bio,
        "image_url":    meta["image_url"],
        "extra":        extra,
    }
    result.update(join_info)
    return result


# ── Handle lookup dispatcher ───────────────────────────────────────────────────

def search_by_handle(query: str, user_id: str = "system") -> dict:
    """
    Entry point for handle queries ({handle}/{platform}).
    Dispatches to the correct direct lookup. Confidence always 100.
    """
    parsed = parse_handle_query(query)
    if not parsed:
        return {"error": "Not a valid handle query", "query": query, "results": []}

    platform = parsed["platform"]
    handle   = parsed["handle"]

    dispatch = {
        "github":    lookup_github,
        "reddit":    lookup_reddit,
        "twitter":   lookup_twitter,
        "instagram": lookup_instagram,
        "linkedin":  lookup_linkedin,
        "youtube":   lookup_youtube,
    }

    fn     = dispatch.get(platform)
    result = fn(handle) if fn else _not_found_result(
        _PLATFORM_LABELS.get(platform, platform), handle,
        _PLATFORM_BASE_URLS.get(platform, "").format(handle=handle)
    )

    # Wrap single result in standard list
    results = [result]
    log_search(user_id, query, len(results), f"handle:{platform}")

    return {
        "query":    query,
        "platform": platform,
        "handle":   handle,
        "total":    len(results),
        "results":  results,
        "errors":   {},
        "mode":     "handle",
    }


# ══════════════════════════════════════════════════════════════════════════════
# NAME SEARCH — DuckDuckGo + Wikipedia + Google News (parallel)
# ══════════════════════════════════════════════════════════════════════════════

def search_duckduckgo(query: str, max_results: int = 8) -> list[dict]:
    results = []
    try:
        with DDGS() as ddgs:
            hits = list(ddgs.text(query, max_results=max_results))
        for h in hits:
            title  = h.get("title", "")
            body   = h.get("body", "")
            url    = h.get("href", "")
            results.append({
                "full_name":  title,
                "platform":   "DuckDuckGo",
                "snippet":    body[:300],
                "url":        url,
                "confidence": _score(query, title, body),
            })
    except Exception as e:
        results.append({
            "full_name": "DuckDuckGo Error",
            "platform":  "DuckDuckGo",
            "snippet":   str(e)[:200],
            "url":       "",
            "confidence": 0,
        })
    return results


def search_google_news(query: str, max_results: int = 8) -> list[dict]:
    results = []
    try:
        url  = f"https://news.google.com/rss/search?q={requests.utils.quote(query)}&hl=en-US&gl=US&ceid=US:en"
        feed = feedparser.parse(url)
        for entry in feed.entries[:max_results]:
            title   = entry.get("title", "")
            summary = re.sub(r"<[^>]+>", "", entry.get("summary", ""))
            link    = entry.get("link", "")
            results.append({
                "full_name":  title,
                "platform":   "Google News",
                "snippet":    summary[:300],
                "url":        link,
                "confidence": _score(query, title, summary),
            })
    except Exception as e:
        results.append({
            "full_name": "Google News Error",
            "platform":  "Google News",
            "snippet":   str(e)[:200],
            "url":       "",
            "confidence": 0,
        })
    return results


def search_wikipedia(query: str, max_results: int = 5) -> list[dict]:
    results = []
    try:
        resp = requests.get(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action": "query", "list": "search", "srsearch": query,
                "srlimit": max_results, "format": "json", "utf8": 1,
            },
            headers=_API_HEADERS,
            timeout=10,
        )
        data = resp.json()
        for item in data.get("query", {}).get("search", []):
            title   = item.get("title", "")
            snippet = re.sub(r"<[^>]+>", "", item.get("snippet", ""))
            url     = f"https://en.wikipedia.org/wiki/{requests.utils.quote(title.replace(' ', '_'))}"
            results.append({
                "full_name":  title,
                "platform":   "Wikipedia",
                "snippet":    snippet[:300],
                "url":        url,
                "confidence": _score(query, title, snippet),
            })
    except Exception as e:
        results.append({
            "full_name": "Wikipedia Error",
            "platform":  "Wikipedia",
            "snippet":   str(e)[:200],
            "url":       "",
            "confidence": 0,
        })
    return results


def search_all_sources(query: str, user_id: str = "system") -> dict:
    """
    Run DuckDuckGo + Wikipedia + Google News in parallel.
    Returns top 5 results ranked by confidence. All results have real URLs.
    """
    buckets: dict[str, list] = {
        "duckduckgo":  [],
        "wikipedia":   [],
        "google_news": [],
    }
    errors: dict[str, str] = {}

    def run(name, fn, *args):
        try:
            buckets[name] = fn(*args)
        except Exception as e:
            errors[name] = str(e)

    threads = [
        threading.Thread(target=run, args=("duckduckgo",  search_duckduckgo,  query)),
        threading.Thread(target=run, args=("wikipedia",   search_wikipedia,   query)),
        threading.Thread(target=run, args=("google_news", search_google_news, query)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)

    all_results = []
    for items in buckets.values():
        all_results.extend(items)

    # Filter errors, sort by confidence, take top 5 with real URLs
    valid = [
        r for r in all_results
        if r["confidence"] > 0
        and "Error" not in r.get("full_name", "")
        and r.get("url", "")
    ]
    valid.sort(key=lambda x: x["confidence"], reverse=True)
    top5 = valid[:5]

    log_search(user_id, query, len(top5), "name")

    return {
        "query":   query,
        "total":   len(top5),
        "sources": buckets,
        "results": top5,
        "errors":  errors,
        "mode":    "name",
    }


# ══════════════════════════════════════════════════════════════════════════════
# CROSS-PLATFORM DISCOVERY — extended platform URL map
# ══════════════════════════════════════════════════════════════════════════════

_ALL_PLATFORM_URLS = {
    "twitter":   "https://x.com/{handle}",
    "instagram": "https://www.instagram.com/{handle}/",
    "github":    "https://github.com/{handle}",
    "reddit":    "https://www.reddit.com/user/{handle}",
    "linkedin":  "https://www.linkedin.com/in/{handle}/",
    "youtube":   "https://www.youtube.com/@{handle}",
    "tiktok":    "https://www.tiktok.com/@{handle}",
    "facebook":  "https://www.facebook.com/{handle}",
    "pinterest": "https://www.pinterest.com/{handle}/",
    "snapchat":  "https://www.snapchat.com/add/{handle}",
}

_ALL_PLATFORM_DISPLAY = {
    "twitter":   "X / Twitter",
    "instagram": "Instagram",
    "github":    "GitHub",
    "reddit":    "Reddit",
    "linkedin":  "LinkedIn",
    "youtube":   "YouTube",
    "tiktok":    "TikTok",
    "facebook":  "Facebook",
    "pinterest": "Pinterest",
    "snapchat":  "Snapchat",
}

# Normalise various display names back to internal keys
_PLATFORM_NORM = {
    "x / twitter": "twitter", "twitter": "twitter",
    "instagram":   "instagram",
    "github":      "github",
    "reddit":      "reddit",
    "linkedin":    "linkedin",
    "youtube":     "youtube",
    "tiktok":      "tiktok",
    "facebook":    "facebook",
    "pinterest":   "pinterest",
    "snapchat":    "snapchat",
}


def _check_platform_exists(platform: str, handle: str) -> tuple[bool, dict, int]:
    """
    Check whether `handle` exists on `platform`.
    Returns (exists, public_data, http_status).
    Uses the relevant API for GitHub/Reddit; HEAD then GET for others.
    Never raises.
    """
    url = _ALL_PLATFORM_URLS.get(platform, "").format(handle=handle)
    if not url:
        return False, {}, 0
    try:
        if platform == "github":
            resp = requests.get(
                f"https://api.github.com/users/{handle}",
                headers=_github_headers(),
                timeout=10,
            )
            exists = resp.status_code == 200
            pub = {}
            if exists:
                d = resp.json()
                jdt = None
                if d.get("created_at"):
                    try:
                        jdt = datetime.datetime.fromisoformat(d["created_at"].replace("Z", ""))
                    except Exception:
                        pass
                jm = _join_meta(jdt, "EXACT", "GitHub API created_at",
                                last_active=(d.get("updated_at","")[:10]))
                pub = {
                    "name":      d.get("name", ""),
                    "bio":       (d.get("bio") or "")[:120],
                    "followers": d.get("followers", 0),
                    "repos":     d.get("public_repos", 0),
                    **jm,
                }
            return exists, pub, resp.status_code

        if platform == "reddit":
            resp = requests.get(
                f"https://www.reddit.com/user/{handle}/about.json",
                headers=_API_HEADERS,
                timeout=10,
            )
            exists = resp.status_code == 200
            pub = {}
            if exists:
                d = resp.json().get("data", {})
                utc = d.get("created_utc", 0)
                jdt = datetime.datetime.utcfromtimestamp(utc) if utc else None
                jm  = _join_meta(jdt, "EXACT", "Reddit API created_utc")
                pub = {
                    "name":  d.get("name", handle),
                    "karma": d.get("link_karma", 0) + d.get("comment_karma", 0),
                    **jm,
                }
            return exists, pub, resp.status_code

        # All other platforms — HEAD first (fast), then GET for meta if 200
        try:
            head = requests.head(url, headers=_HEADERS, timeout=8, allow_redirects=True)
            status = head.status_code
        except Exception:
            status = 0

        if status == 404:
            return False, {}, 404
        if status in (200, 301, 302):
            pub = {}
            try:
                get_resp = requests.get(url, headers=_HEADERS, timeout=10, allow_redirects=True)
                if get_resp.status_code == 200:
                    h = get_resp.text[:15000]

                    def _qm(attr, at="property"):
                        m = re.search(
                            rf'<meta\s+{at}="{re.escape(attr)}"\s+content="([^"]*)"', h, re.I
                        )
                        return m.group(1).strip() if m else ""

                    pub = {
                        "name": (_qm("og:title") or _qm("twitter:title", "name"))[:100],
                        "bio":  (_qm("og:description", "property") or _qm("description", "name"))[:200],
                    }

                    # Join date extraction — platform-specific HTML patterns
                    jdt = None
                    jconf = "NOT AVAILABLE"
                    jsrc  = f"{platform} HTML"
                    _join_patterns = [
                        r'"joinedDate"\s*:\s*"([^"]+)"',
                        r'"createdAt"\s*:\s*"([^"]+)"',
                        r'Joined\s+([A-Z][a-z]+\s+\d{1,2},\s+\d{4})',
                        r'Joined\s+([A-Z][a-z]+\s+\d{4})',
                        r'"dateCreated"\s*:\s*"([^"]+)"',
                        r'"startDate"\s*:\s*"([^"]+)"',
                        r'"memberSince"\s*:\s*"([^"]+)"',
                        r'Member\s+since\s+(\w+\s+\d{4}|\d{4})',
                    ]
                    for pat in _join_patterns:
                        m2 = re.search(pat, h, re.I)
                        if m2:
                            jdt = _parse_join_str(m2.group(1))
                            if jdt:
                                jconf = "APPROXIMATE"
                                break
                    jm = _join_meta(jdt, jconf, jsrc)
                    pub.update(jm)
            except Exception:
                pass
            return True, pub, 200

        return False, {}, status

    except Exception:
        return False, {}, 0


def _name_similarity(name1: str, name2: str) -> int:
    """Return 0–100 name similarity score."""
    if not name1 or not name2:
        return 0
    n1 = re.sub(r"[^\w\s]", "", name1.lower()).strip()
    n2 = re.sub(r"[^\w\s]", "", name2.lower()).strip()
    if n1 == n2:
        return 100
    w1, w2 = set(n1.split()), set(n2.split())
    if not w1 or not w2:
        return 0
    shorter = w1 if len(w1) <= len(w2) else w2
    longer  = w2 if len(w1) <= len(w2) else w1
    if shorter.issubset(longer):
        return 85
    overlap = len(shorter & longer)
    ratio   = overlap / len(shorter)
    if ratio >= 0.8:
        return 75
    if ratio >= 0.5:
        return 55
    if ratio > 0:
        return int(ratio * 50)
    return 0


def _bio_overlap_bonus(bio1: str, bio2: str) -> int:
    """Return 0–20 bonus score for shared significant words in two bios."""
    if not bio1 or not bio2:
        return 0
    _stop = {
        "the","and","for","are","with","this","that","from","have","will",
        "your","their","about","more","some","than","also","into","over",
        "then","them","they","been","its","not","but","was","were","has",
    }
    def _sig(text):
        return {w for w in re.findall(r"\b[a-zA-Z]{4,}\b", text.lower()) if w not in _stop}
    shared = len(_sig(bio1) & _sig(bio2))
    return min(20, shared * 5)


def _search_name_github(name: str, known_bio: str = "") -> list[dict]:
    """Search GitHub users by name. Returns POTENTIAL matches."""
    out = []
    try:
        resp = requests.get(
            "https://api.github.com/search/users",
            params={"q": f"{name} in:name", "per_page": 5},
            headers=_github_headers(),
            timeout=12,
        )
        if resp.status_code != 200:
            return out
        for item in resp.json().get("items", [])[:5]:
            login = item.get("login", "")
            if not login:
                continue
            gh_name, bio = login, ""
            try:
                full = requests.get(
                    f"https://api.github.com/users/{login}",
                    headers=_github_headers(),
                    timeout=8,
                )
                if full.status_code == 200:
                    d = full.json()
                    gh_name = d.get("name", "") or login
                    bio = (d.get("bio") or "")[:200]
            except Exception:
                pass
            ns = _name_similarity(name, gh_name)
            bb = _bio_overlap_bonus(known_bio, bio)
            conf = min(99, ns + bb)
            if conf >= 70:
                reason = "name match"
                if bb > 0:
                    reason += " + bio overlap"
                out.append({
                    "platform":    "GitHub",
                    "username":    login,
                    "url":         f"https://github.com/{login}",
                    "confidence":  conf,
                    "match_reason": reason,
                    "public_data": {"name": gh_name, "bio": bio},
                })
    except Exception:
        pass
    return out


def _search_name_ddg_site(name: str, site: str, platform: str, known_bio: str = "") -> list[dict]:
    """DuckDuckGo site: search for a name on a specific platform."""
    out = []
    try:
        with DDGS() as ddgs:
            hits = list(ddgs.text(f'site:{site} "{name}"', max_results=5))
        for h in hits:
            url    = h.get("href", "")
            title  = h.get("title", "")
            body   = h.get("body", "")
            if not url or site not in url:
                continue
            # Extract handle from URL
            username = ""
            for pat in [
                r"(?:twitter|x)\.com/([^/?#\s]+)",
                r"instagram\.com/([^/?#\s]+)",
                r"linkedin\.com/in/([^/?#\s]+)",
                r"github\.com/([^/?#\s]+)",
                r"youtube\.com/(?:@|c/|user/)([^/?#\s]+)",
                r"tiktok\.com/@([^/?#\s]+)",
                r"facebook\.com/([^/?#\s]+)",
                r"pinterest\.com/([^/?#\s]+)",
            ]:
                m = re.search(pat, url, re.I)
                if m:
                    username = m.group(1).rstrip("/")
                    break
            if not username:
                continue
            ns   = _name_similarity(name, title)
            bb   = _bio_overlap_bonus(known_bio, body)
            conf = min(99, ns + bb)
            if conf >= 70:
                reason = "name search"
                if bb > 0:
                    reason += " + bio overlap"
                out.append({
                    "platform":    _ALL_PLATFORM_DISPLAY.get(platform, platform),
                    "username":    username,
                    "url":         url,
                    "confidence":  conf,
                    "match_reason": reason,
                    "public_data": {"snippet": body[:200]},
                })
    except Exception:
        pass
    return out


# ══════════════════════════════════════════════════════════════════════════════
# FEATURE 2 — LINKEDIN INTELLIGENCE EXTRACTOR
# ══════════════════════════════════════════════════════════════════════════════

def extract_linkedin_intelligence(username: str) -> dict:
    """
    Fetch a LinkedIn public profile and extract all publicly visible data.
    No login required. If blocked, returns partial data with blocked fields
    marked as 'Requires direct visit'.
    """
    url = f"https://www.linkedin.com/in/{username.strip('/')}/"
    result = {
        "linkedin_url":    url,
        "name":            "",
        "headline":        "",
        "location":        "",
        "about":           "",
        "company":         "",
        "education":       [],
        "emails_found":    [],
        "phones_found":    [],
        "twitter_found":   "",
        "instagram_found": "",
        "github_found":    "",
        "website_found":   "",
        "other_socials":   [],
        "confidence":      0,
        "data_source":     "linkedin_public",
    }
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=15, allow_redirects=True)
        if resp.status_code not in (200, 301, 302):
            result["name"] = "Requires direct visit"
            return result

        html = resp.text
        conf  = 10

        # ── Meta-tag helpers ──────────────────────────────────────────────────
        def _meta(attr_name: str, attr_type: str = "name") -> str:
            for tmpl in [
                rf'<meta\s+{attr_type}="{re.escape(attr_name)}"\s+content="([^"]*)"',
                rf'<meta\s+content="([^"]*)"\s+{attr_type}="{re.escape(attr_name)}"',
            ]:
                m = re.search(tmpl, html, re.I)
                if m:
                    return m.group(1).strip()
            return ""

        og_title = _meta("og:title", "property") or _meta("twitter:title")
        if og_title:
            name = re.sub(r"\s*[\|\-–]\s*LinkedIn.*$", "", og_title, flags=re.I).strip()
            result["name"] = name[:120]
            conf = max(conf, 40)

        og_desc = _meta("og:description", "property") or _meta("description")
        if og_desc:
            desc = re.sub(r"\s*See [^\|]+LinkedIn.*$", "", og_desc, flags=re.I).strip()
            desc = re.sub(r"\s*[\|·]\s*LinkedIn.*$", "", desc, flags=re.I).strip()
            result["headline"] = desc[:300]
            conf = max(conf, 50)

        # Page title fallback
        if not result["name"]:
            m = re.search(r"<title[^>]*>([^<]+)</title>", html, re.I)
            if m:
                raw = re.sub(r"\s*[\|\-–]\s*LinkedIn.*$", "", m.group(1), flags=re.I).strip()
                result["name"] = raw[:120]

        # ── JSON-LD ────────────────────────────────────────────────────────────
        for jld_raw in re.findall(
            r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
            html, re.S | re.I,
        ):
            try:
                jld   = json.loads(jld_raw)
                items = jld if isinstance(jld, list) else [jld]
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    t = item.get("@type", "")
                    if t in ("Person", "ProfilePage"):
                        if item.get("name"):
                            result["name"] = result["name"] or item["name"][:120]
                        if item.get("description"):
                            result["about"] = item["description"][:600]
                        addr = item.get("address")
                        if isinstance(addr, dict):
                            city    = addr.get("addressLocality", "")
                            country = addr.get("addressCountry", "")
                            result["location"] = ", ".join(p for p in [city, country] if p)
                        org = item.get("worksFor")
                        if isinstance(org, dict):
                            result["company"] = org.get("name", "")
                        elif isinstance(org, str):
                            result["company"] = org
                        edu_list = item.get("alumniOf", [])
                        if isinstance(edu_list, dict):
                            edu_list = [edu_list]
                        for e in (edu_list or []):
                            if isinstance(e, dict) and e.get("name"):
                                result["education"].append(e["name"])
                        conf = max(conf, 70)
            except Exception:
                pass

        # ── Email regex ────────────────────────────────────────────────────────
        for email in re.findall(
            r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", html
        ):
            if "linkedin" not in email.lower() and email not in result["emails_found"]:
                result["emails_found"].append(email)
                conf = max(conf, 75)

        # ── Phone regex ────────────────────────────────────────────────────────
        for phone in re.findall(
            r"[\+]?[(]?[0-9]{3}[)]?[-\s\.]?[0-9]{3}[-\s\.]?[0-9]{4,6}", html
        ):
            if phone not in result["phones_found"]:
                result["phones_found"].append(phone)

        # ── Social link extraction ─────────────────────────────────────────────
        # Twitter/X
        m = re.search(
            r'(?:href|content)=["\']https?://(?:www\.)?(?:twitter|x)\.com/([^/"\'/?#\s]+)["\']',
            html, re.I,
        )
        if m:
            h = m.group(1)
            if h.lower() not in ("intent","share","hashtag","status","home","i","search"):
                result["twitter_found"] = h
                conf = max(conf, 75)

        # Instagram
        m = re.search(
            r'(?:href|content)=["\']https?://(?:www\.)?instagram\.com/([^/"\'/?#\s]+)["\']',
            html, re.I,
        )
        if m:
            result["instagram_found"] = m.group(1)
            conf = max(conf, 75)

        # GitHub
        m = re.search(
            r'(?:href|content)=["\']https?://(?:www\.)?github\.com/([^/"\'/?#\s]+)["\']',
            html, re.I,
        )
        if m:
            h = m.group(1)
            _gh_skip = {
                "features","pricing","about","login","join","topics","collections",
                "trending","marketplace","sponsors","orgs","site","contact",
            }
            if h.lower() not in _gh_skip:
                result["github_found"] = h
                conf = max(conf, 75)

        # External website
        for ext_m in re.finditer(
            r'href=["\']https?://([^"\']{10,80})["\']', html, re.I
        ):
            raw = "https://" + ext_m.group(1)
            if not any(skip in raw for skip in (
                "linkedin","google","microsoft","apple","facebook","twitter",
                "instagram","github","youtube","tiktok","pinterest","snapchat",
                "cloudflare","w3.org","schema.org","akamai","jquery",
            )):
                result["website_found"] = raw[:200]
                conf = max(conf, 60)
                break

        # YouTube / TikTok / Pinterest (other socials)
        for pat, label in [
            (r'href=["\']https?://(?:www\.)?youtube\.com/(?:@|c/|user/)?([^"\'/?#\s]+)["\']', "YouTube"),
            (r'href=["\']https?://(?:www\.)?tiktok\.com/@([^"\'/?#\s]+)["\']', "TikTok"),
            (r'href=["\']https?://(?:www\.)?pinterest\.com/([^"\'/?#\s]+)["\']', "Pinterest"),
        ]:
            sm = re.search(pat, html, re.I)
            if sm:
                entry = f"{label}: {sm.group(1)}"
                if entry not in result["other_socials"]:
                    result["other_socials"].append(entry)

        result["confidence"] = conf

        # Mark login-wall blocked fields
        if "authwall" in html.lower() or "Join to see" in html or "join-linkedin" in html:
            if not result["name"]:
                result["name"] = "Requires direct visit"

    except requests.exceptions.Timeout:
        result["name"] = "Requires direct visit"
    except requests.exceptions.ConnectionError:
        result["name"] = "Requires direct visit"
    except Exception:
        if not result["name"]:
            result["name"] = "Requires direct visit"

    return result


# ══════════════════════════════════════════════════════════════════════════════
# FEATURE 1 — LINKED PROFILES DISCOVERY ENGINE
# ══════════════════════════════════════════════════════════════════════════════

def find_linked_profiles(confirmed_target: dict) -> dict:
    """
    Attempt to find all other accounts belonging to the same person.

    Steps:
      1. Username propagation — try same handle on every unconfirmed platform
      2. Name-based search   — GitHub API + DuckDuckGo site: operators
      3. Bio cross-reference — search distinctive bio phrases
      4. LinkedIn intel      — use any social links found on LinkedIn profile

    Returns:
      {
        "confirmed_linked": [...],
        "potential_linked": [...],
        "discovery_summary": { platforms_checked, _confirmed, _potential, _not_found, total }
      }
    """
    confirmed_linked: list[dict] = []
    potential_linked: list[dict] = []
    platforms_checked:   list[str] = []
    platforms_confirmed: list[str] = []
    platforms_potential: list[str] = []
    platforms_not_found: list[str] = []

    # ── Normalise known platforms from Person Object ──────────────────────────
    raw_plats = confirmed_target.get("platforms_confirmed", [])
    known_keys: set[str] = set()
    for p in raw_plats:
        k = _PLATFORM_NORM.get(p.lower(), p.lower())
        known_keys.add(k)

    # Extract username -> platform map from Person Object
    known_usernames: dict[str, str] = {}  # platform_key -> handle
    for plat_label, handle in confirmed_target.get("usernames", {}).items():
        k = _PLATFORM_NORM.get(plat_label.lower(), plat_label.lower())
        if handle:
            known_usernames[k] = handle

    # Also parse handles from profile_urls when not in usernames dict
    for plat_label, purl in confirmed_target.get("profile_urls", {}).items():
        k = _PLATFORM_NORM.get(plat_label.lower(), plat_label.lower())
        if k not in known_usernames and purl:
            m = re.search(
                r"(?:github\.com|x\.com|twitter\.com|instagram\.com|"
                r"reddit\.com/user|linkedin\.com/in|youtube\.com/@|"
                r"tiktok\.com/@|facebook\.com|pinterest\.com|snapchat\.com/add)"
                r"/([^/?#\s]+)",
                purl,
            )
            if m:
                known_usernames[k] = m.group(1).rstrip("/")
                known_keys.add(k)

    unique_handles: set[str] = set(h for h in known_usernames.values() if h)

    # ── Step 1: Username propagation ─────────────────────────────────────────
    _lock = threading.Lock()

    def _check(platform: str, handle: str):
        if platform in known_keys:
            return
        url = _ALL_PLATFORM_URLS.get(platform, "").format(handle=handle)
        if not url:
            return
        with _lock:
            if platform not in platforms_checked:
                platforms_checked.append(platform)
        exists, pub, _ = _check_platform_exists(platform, handle)
        with _lock:
            if exists:
                confirmed_linked.append({
                    "platform":     _ALL_PLATFORM_DISPLAY.get(platform, platform),
                    "username":     handle,
                    "url":          url,
                    "confidence":   100,
                    "match_reason": "same username",
                    "public_data":  pub,
                })
                if platform not in platforms_confirmed:
                    platforms_confirmed.append(platform)
            else:
                if platform not in platforms_not_found:
                    platforms_not_found.append(platform)

    threads: list[threading.Thread] = []
    for handle in unique_handles:
        if not handle or len(handle) < 2:
            continue
        for plat in _ALL_PLATFORM_URLS:
            if plat not in known_keys:
                t = threading.Thread(target=_check, args=(plat, handle), daemon=True)
                threads.append(t)

    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    # ── Step 2: Name-based search ─────────────────────────────────────────────
    display_name = confirmed_target.get("confirmed_name", "")
    bio_data     = confirmed_target.get("bio_data", {})
    combined_bio = " ".join(str(v) for v in bio_data.values())[:600]

    if display_name and len(display_name) > 2:
        already_usernames = {c["username"] for c in confirmed_linked}

        # GitHub API search
        try:
            for r in _search_name_github(display_name, combined_bio):
                if r["username"] not in already_usernames:
                    potential_linked.append(r)
                    if "github" not in platforms_potential:
                        platforms_potential.append("github")
        except Exception:
            pass

        # DuckDuckGo site: searches for 3 social platforms
        for plat_key, site in [
            ("twitter",   "twitter.com"),
            ("instagram", "instagram.com"),
            ("linkedin",  "linkedin.com/in"),
        ]:
            if plat_key not in known_keys:
                try:
                    for r in _search_name_ddg_site(display_name, site, plat_key, combined_bio):
                        if r["username"] not in already_usernames:
                            potential_linked.append(r)
                            if plat_key not in platforms_potential:
                                platforms_potential.append(plat_key)
                except Exception:
                    pass

    # ── Step 3: Bio cross-reference ───────────────────────────────────────────
    if combined_bio and len(combined_bio) > 30:
        _stop = {
            "this","that","with","from","have","will","your","their","about",
            "more","some","than","also","into","over","then","them","they",
        }
        words = [
            w for w in re.findall(r"\b[A-Za-z]{5,}\b", combined_bio)
            if w.lower() not in _stop
        ][:8]

        if len(words) >= 3:
            bio_phrase = " ".join(words[:3])
            already_usernames = {c["username"] for c in confirmed_linked} | \
                                {p["username"] for p in potential_linked}
            for plat_key, site in [("github", "github.com"), ("twitter", "twitter.com")]:
                if plat_key not in known_keys:
                    try:
                        for r in _search_name_ddg_site(bio_phrase, site, plat_key, combined_bio)[:2]:
                            if r["username"] not in already_usernames:
                                r["match_reason"] = "bio phrase match"
                                r["confidence"]   = min(r["confidence"], 75)
                                potential_linked.append(r)
                    except Exception:
                        pass

    # ── Step 4: LinkedIn intelligence -> confirm extra accounts ───────────────
    li_url  = confirmed_target.get("profile_urls", {}).get("LinkedIn", "")
    li_user = known_usernames.get("linkedin", "")
    if not li_url and li_user:
        li_url = f"https://www.linkedin.com/in/{li_user}/"

    if li_url:
        m = re.search(r"linkedin\.com/in/([^/?#\s]+)", li_url)
        if m:
            li_handle = m.group(1).rstrip("/")
            try:
                li_intel = extract_linkedin_intelligence(li_handle)
                already  = {c["username"] for c in confirmed_linked}

                def _add_confirmed(plat_display: str, handle: str, link_url: str):
                    if handle and handle not in already:
                        confirmed_linked.append({
                            "platform":     plat_display,
                            "username":     handle,
                            "url":          link_url,
                            "confidence":   100,
                            "match_reason": "linked from LinkedIn profile",
                            "public_data":  {},
                        })
                        already.add(handle)

                if li_intel.get("twitter_found"):
                    h = li_intel["twitter_found"]
                    _add_confirmed("X / Twitter", h, f"https://x.com/{h}")
                if li_intel.get("github_found"):
                    h = li_intel["github_found"]
                    _add_confirmed("GitHub", h, f"https://github.com/{h}")
                if li_intel.get("instagram_found"):
                    h = li_intel["instagram_found"]
                    _add_confirmed("Instagram", h, f"https://www.instagram.com/{h}/")
                for other in li_intel.get("other_socials", []):
                    if ":" in other:
                        lbl, h = other.split(":", 1)
                        h = h.strip()
                        _add_confirmed(lbl.strip(), h, "")
            except Exception:
                pass

    # ── De-duplicate potential list ───────────────────────────────────────────
    conf_usernames = {c["username"] for c in confirmed_linked}
    seen_pot: set[tuple] = set()
    deduped_potential: list[dict] = []
    for p in potential_linked:
        key = (p["platform"], p["username"])
        if p["username"] not in conf_usernames and key not in seen_pot:
            seen_pot.add(key)
            deduped_potential.append(p)

    total = len(confirmed_linked) + len(deduped_potential)

    return {
        "confirmed_linked": confirmed_linked,
        "potential_linked": deduped_potential,
        "discovery_summary": {
            "platforms_checked":   sorted(set(platforms_checked)),
            "platforms_confirmed": sorted(set(platforms_confirmed)),
            "platforms_potential": sorted(set(platforms_potential)),
            "platforms_not_found": sorted(set(platforms_not_found)),
            "total_accounts_found": total,
        },
    }


# ── Unified entry point ────────────────────────────────────────────────────────

def run_search(query: str, user_id: str = "system") -> dict:
    """
    Auto-detect query type:
      - {handle}/{platform}  -> direct platform lookup, confidence 100
      - plain name           -> multi-source search, top 5 by confidence
    """
    query = query.strip()
    if not query:
        return {"query": query, "total": 0, "results": [], "errors": {}}

    if parse_handle_query(query):
        return search_by_handle(query, user_id)
    else:
        return search_all_sources(query, user_id)
