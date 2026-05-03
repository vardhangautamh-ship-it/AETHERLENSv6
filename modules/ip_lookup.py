"""
AetherLens — IP Lookup Module
City-level geolocation via ip-api.com (no key required).
Always displays accuracy disclaimer.
"""

import re
import requests

DISCLAIMER = "Location accuracy: city-level only. Exact address requires court order."

EMPTY_RESULT = {
    "ip":         "",
    "city":       "",
    "region":     "",
    "country":    "",
    "isp":        "",
    "timezone":   "",
    "mobile":     False,
    "proxy":      False,
    "confidence": "city-level only",
    "disclaimer": DISCLAIMER,
    "error":      None,
}


def _is_valid_ip(ip: str) -> bool:
    ip = ip.strip()
    # IPv4
    ipv4 = re.match(r"^(\d{1,3}\.){3}\d{1,3}$", ip)
    if ipv4:
        parts = ip.split(".")
        return all(0 <= int(p) <= 255 for p in parts)
    # IPv6 (basic)
    ipv6 = re.match(r"^[0-9a-fA-F:]+$", ip) and ":" in ip
    return bool(ipv6)


def lookup_ip(ip: str) -> dict:
    """
    Query ip-api.com for city-level geolocation of the given IP address.
    Returns a structured dict. Always includes the accuracy disclaimer.
    """
    result = dict(EMPTY_RESULT)
    result["ip"] = ip.strip()

    if not _is_valid_ip(ip.strip()):
        result["error"] = f"Invalid IP address format: {ip}"
        return result

    try:
        resp = requests.get(
            f"http://ip-api.com/json/{ip.strip()}",
            params={"fields": "status,message,country,regionName,city,isp,timezone,mobile,proxy,query"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("status") == "success":
            result["ip"]       = data.get("query", ip.strip())
            result["city"]     = data.get("city", "")
            result["region"]   = data.get("regionName", "")
            result["country"]  = data.get("country", "")
            result["isp"]      = data.get("isp", "")
            result["timezone"] = data.get("timezone", "")
            result["mobile"]   = bool(data.get("mobile", False))
            result["proxy"]    = bool(data.get("proxy", False))
        else:
            result["error"] = data.get("message", "IP lookup failed")

    except requests.exceptions.Timeout:
        result["error"] = "Request timed out (ip-api.com)"
    except requests.exceptions.ConnectionError:
        result["error"] = "Cannot reach ip-api.com"
    except Exception as e:
        result["error"] = str(e)

    return result


def lookup_multiple(ip_list: list[str]) -> list[dict]:
    """Look up a list of IP addresses and return list of results."""
    return [lookup_ip(ip) for ip in ip_list if ip.strip()]


def format_result(r: dict) -> str:
    """Return a single-line human-readable summary."""
    if r.get("error"):
        return f"{r['ip']} — Error: {r['error']}"
    parts = [r["ip"]]
    if r["city"]:    parts.append(r["city"])
    if r["region"]:  parts.append(r["region"])
    if r["country"]: parts.append(r["country"])
    if r["isp"]:     parts.append(f"ISP: {r['isp']}")
    if r["proxy"]:   parts.append("PROXY DETECTED")
    if r["mobile"]:  parts.append("Mobile network")
    return " | ".join(parts)
