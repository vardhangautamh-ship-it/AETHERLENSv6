"""
AetherLens — Phone Enrichment

Turns a bare phone string into structured intelligence: country, region/circle,
original carrier, line type (mobile / landline / VoIP), and timezone.

Engine: Google's libphonenumber (the `phonenumbers` package) when available —
fully offline, no API key, no network. If the library is missing, a deterministic
heuristic fallback covers India and common country codes so the pipeline never
crashes and always returns a consistent shape.

Note on carrier: libphonenumber reports the ORIGINAL allocatee of a number range.
Indian mobile number portability (MNP) means the *current* operator may differ —
the result is labelled accordingly so analysts don't over-trust it.

Public API:
    enrich_phone(raw, default_region="IN") -> dict
    enrich_phones(phones, default_region="IN") -> list[dict]
    format_enrichment_line(info) -> str
"""
from __future__ import annotations

import re

try:
    import phonenumbers
    from phonenumbers import (
        carrier as _carrier,
        geocoder as _geocoder,
        timezone as _timezone,
        PhoneNumberType,
        NumberParseException,
    )
    _HAS_LIB = True
except Exception:  # pragma: no cover - exercised only when lib absent
    _HAS_LIB = False


# ── Country-code → name (used by both engines for a friendly label) ──────────
_CC_NAME = {
    "1": "United States / Canada", "44": "United Kingdom", "61": "Australia",
    "65": "Singapore", "971": "United Arab Emirates", "966": "Saudi Arabia",
    "91": "India", "92": "Pakistan", "880": "Bangladesh", "977": "Nepal",
    "94": "Sri Lanka", "93": "Afghanistan", "86": "China", "81": "Japan",
    "49": "Germany", "33": "France", "7": "Russia / Kazakhstan", "60": "Malaysia",
    "62": "Indonesia", "63": "Philippines", "234": "Nigeria", "27": "South Africa",
}

_CC_REGION = {  # region code for friendly country name (fallback engine)
    "1": "US", "44": "GB", "61": "AU", "65": "SG", "971": "AE", "966": "SA",
    "91": "IN", "92": "PK", "880": "BD", "977": "NP", "94": "LK", "93": "AF",
    "86": "CN", "81": "JP", "49": "DE", "33": "FR", "7": "RU", "60": "MY",
    "62": "ID", "63": "PH", "234": "NG", "27": "ZA",
}

# India mobile first-digit → original operator family (approximate; pre-MNP).
# Indian mobile numbers are 10 digits starting 6/7/8/9. Fine-grained operator
# mapping needs the full 5-digit series block, which libphonenumber handles; this
# coarse map is only the heuristic fallback.
_IN_MOBILE_LEADS = {"6", "7", "8", "9"}


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def _line_type_label(num) -> str:
    """Map a phonenumbers PhoneNumberType to a human label."""
    t = phonenumbers.number_type(num)
    return {
        PhoneNumberType.MOBILE:               "mobile",
        PhoneNumberType.FIXED_LINE:           "landline",
        PhoneNumberType.FIXED_LINE_OR_MOBILE: "fixed-or-mobile",
        PhoneNumberType.VOIP:                 "voip",
        PhoneNumberType.TOLL_FREE:            "toll-free",
        PhoneNumberType.PREMIUM_RATE:         "premium-rate",
        PhoneNumberType.SHARED_COST:          "shared-cost",
        PhoneNumberType.PERSONAL_NUMBER:      "personal",
        PhoneNumberType.PAGER:                "pager",
        PhoneNumberType.UAN:                  "uan",
        PhoneNumberType.VOICEMAIL:            "voicemail",
    }.get(t, "unknown")


def _blank(raw: str) -> dict:
    """Consistent empty/invalid result shape."""
    return {
        "input":       raw,
        "valid":       False,
        "possible":    False,
        "e164":        "",
        "national":    "",
        "country_code": "",
        "country":     "",
        "region":      "",
        "carrier":     "",
        "line_type":   "unknown",
        "is_mobile":   False,
        "timezones":   [],
        "engine":      "phonenumbers" if _HAS_LIB else "heuristic",
        "notes":       [],
    }


def _enrich_with_lib(raw: str, default_region: str) -> dict:
    out = _blank(raw)
    try:
        num = phonenumbers.parse(raw, default_region)
    except NumberParseException as e:
        out["notes"].append(f"parse failed: {e}")
        return out

    out["possible"] = phonenumbers.is_possible_number(num)
    out["valid"]    = phonenumbers.is_valid_number(num)
    out["country_code"] = f"+{num.country_code}"
    out["e164"]     = phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.E164)
    out["national"] = phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.NATIONAL)

    region_code = phonenumbers.region_code_for_number(num) or ""
    out["country"] = _CC_NAME.get(str(num.country_code), region_code or "")
    out["region"]  = _geocoder.description_for_number(num, "en") or ""
    out["carrier"] = _carrier.name_for_number(num, "en") or ""
    out["line_type"] = _line_type_label(num)
    out["is_mobile"] = out["line_type"] in ("mobile", "fixed-or-mobile")
    out["timezones"] = list(_timezone.time_zones_for_number(num) or [])

    if out["carrier"]:
        out["notes"].append("carrier = original allocatee; may differ after porting (MNP)")
    if out["possible"] and not out["valid"]:
        out["notes"].append("possible but not a valid number for its region")
    return out


def _enrich_heuristic(raw: str, default_region: str) -> dict:
    """Deterministic fallback when the phonenumbers library is unavailable."""
    out = _blank(raw)
    d = _digits(raw)
    if not d:
        out["notes"].append("no digits")
        return out

    has_plus = raw.strip().startswith("+") or raw.strip().startswith("00")
    cc = ""
    national = d
    if has_plus:
        d2 = d[2:] if raw.strip().startswith("00") else d
        for code in sorted(_CC_NAME, key=len, reverse=True):
            if d2.startswith(code):
                cc, national = code, d2[len(code):]
                break
    else:
        # No explicit country code — assume the default region.
        cc = {"IN": "91", "PK": "92", "US": "1", "GB": "44", "AE": "971"}.get(
            default_region.upper(), "")
        # Indian numbers often arrive as 91XXXXXXXXXX, 0XXXXXXXXXX, or 10-digit.
        if cc == "91":
            if national.startswith("91") and len(national) == 12:
                national = national[2:]
            elif national.startswith("0") and len(national) == 11:
                national = national[1:]

    out["country_code"] = f"+{cc}" if cc else ""
    out["country"]      = _CC_NAME.get(cc, "")
    out["national"]     = national

    # India-specific line typing (the most common case for this tool).
    if cc == "91":
        if len(national) == 10 and national[0] in _IN_MOBILE_LEADS:
            out["valid"] = out["possible"] = out["is_mobile"] = True
            out["line_type"] = "mobile"
            out["e164"] = f"+91{national}"
            out["notes"].append("India mobile (heuristic: 10-digit, leads 6-9)")
        elif 8 <= len(national) <= 11:
            out["possible"] = True
            out["line_type"] = "landline"
            out["e164"] = f"+91{national}"
            out["notes"].append("India landline (heuristic)")
        else:
            out["notes"].append("unrecognised India format (heuristic)")
    else:
        # Generic: treat a plausible 7-15 digit number as possible.
        if 7 <= len(national) <= 15:
            out["possible"] = True
            out["e164"] = f"+{cc}{national}" if cc else ""
        out["notes"].append("non-India number; install 'phonenumbers' for full detail")

    return out


def enrich_phone(raw, default_region: str = "IN") -> dict:
    """
    Enrich a single phone string. Never raises.

    Returns a dict with: input, valid, possible, e164, national, country_code,
    country, region, carrier, line_type, is_mobile, timezones, engine, notes.
    """
    raw = "" if raw is None else str(raw).strip()
    if not raw:
        return _blank(raw)
    try:
        return _enrich_with_lib(raw, default_region) if _HAS_LIB \
            else _enrich_heuristic(raw, default_region)
    except Exception as e:  # pragma: no cover - defensive
        out = _blank(raw)
        out["notes"].append(f"enrichment error: {e}")
        return out


def enrich_phones(phones, default_region: str = "IN") -> list:
    """Enrich a list of phone strings, de-duplicated by E.164 (then raw)."""
    seen, results = set(), []
    for p in (phones or []):
        info = enrich_phone(p, default_region)
        key = info.get("e164") or _digits(info.get("input", ""))
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        results.append(info)
    return results


def format_enrichment_line(info: dict) -> str:
    """One-line human summary, e.g. 'mobile · India · Karnataka · Airtel'."""
    if not info or (not info.get("valid") and not info.get("possible")):
        return "unverified / unrecognised number"
    parts   = [info.get("line_type", "unknown")]
    country = info.get("country", "")
    region  = info.get("region", "")
    if country:
        parts.append(country)
    # Skip the region when it just repeats the country (libphonenumber returns a
    # country-level description for mobiles, e.g. region == "India").
    if region and region.lower() not in country.lower():
        parts.append(region)
    if info.get("carrier"):
        parts.append(info["carrier"])
    return " · ".join(p for p in parts if p)
