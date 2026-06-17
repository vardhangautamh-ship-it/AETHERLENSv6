"""
Regression suite for the live OSINT search path (Phase OSINT — Deliverable 1).

Locks in:
  (1) A rate-limited / errored platform lookup is reported as "lookup_failed",
      NOT "not_found". (Empirically: an unauthenticated GitHub call on a shared
      host returns 403, and the old code labelled torvalds as "Profile not found".)
  (2) A genuine 404 is still reported as "not_found".
  (3) A successful 200 still yields a full-confidence hit.
  (4) GitHub calls attach an Authorization header when GITHUB_TOKEN is configured,
      and attach none when it is not (lifts the 60 -> 5,000 req/hr anonymous cap).

Fully offline: requests + feedparser + ddgs are stubbed. No network, no AI.
Run: python3 test_search_robustness.py
"""
import sys, types, pathlib

# ─── stub heavy/optional deps so the module imports anywhere ─────────────────
for _name in ("feedparser",):
    if _name not in sys.modules:
        sys.modules[_name] = types.ModuleType(_name)
if "ddgs" not in sys.modules and "duckduckgo_search" not in sys.modules:
    _d = types.ModuleType("ddgs")
    _d.DDGS = object
    sys.modules["ddgs"] = _d

# real config is fine (no network at import) — we mutate GITHUB_TOKEN per-test
import config
import modules.search as S

results = []
def check(label, ok):
    results.append(bool(ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")


class _FakeResp:
    """Minimal stand-in for requests.Response."""
    def __init__(self, status_code, json_data=None, headers=None):
        self.status_code = status_code
        self._json = json_data if json_data is not None else {}
        self.headers = headers or {}
    def json(self):
        return self._json


_captured_headers = []

def _make_fake_get(status_code, json_data=None, headers=None):
    """Return a requests.get replacement that records the headers it was called with."""
    def _fake_get(url, *a, **kw):
        _captured_headers.append(kw.get("headers", {}))
        return _FakeResp(status_code, json_data, headers)
    return _fake_get


# ══════════════════════════════════════════════════════════════════════════════
print("=" * 72)
print("PART 1 — error vs. not-found distinction (the core bug)")
print("=" * 72)

# ── GitHub 404 → genuinely absent ────────────────────────────────────────────
S.requests.get = _make_fake_get(404)
r = S.lookup_github("definitely_not_a_real_user_zzz")
print(f"\n[404] status={r.get('status')!r} snippet={r.get('snippet')!r}")
check("GitHub 404 → status 'not_found'", r.get("status") == "not_found")
check("GitHub 404 snippet says 'not found'", "not found" in r.get("snippet", "").lower())

# ── GitHub 403 rate-limit → lookup_failed (NOT not_found) ─────────────────────
S.requests.get = _make_fake_get(403, headers={"X-RateLimit-Remaining": "0"})
r = S.lookup_github("torvalds")
print(f"[403] status={r.get('status')!r} snippet={r.get('snippet')!r}")
check("GitHub 403 → status 'lookup_failed'", r.get("status") == "lookup_failed")
check("GitHub 403 is NOT reported as not_found", r.get("status") != "not_found")
check("GitHub 403 snippet mentions rate-limit", "rate-limit" in r.get("snippet", "").lower())

# ── GitHub 500 → lookup_failed ───────────────────────────────────────────────
S.requests.get = _make_fake_get(500)
r = S.lookup_github("torvalds")
print(f"[500] status={r.get('status')!r}")
check("GitHub 500 → status 'lookup_failed'", r.get("status") == "lookup_failed")

# ── GitHub 200 → real hit ────────────────────────────────────────────────────
S.requests.get = _make_fake_get(200, json_data={
    "login": "torvalds", "name": "Linus Torvalds", "public_repos": 8,
    "followers": 200000, "following": 0, "created_at": "2011-09-03T15:26:22Z",
    "updated_at": "2024-01-01T00:00:00Z", "bio": "", "location": "", "company": "",
    "blog": "", "email": "", "avatar_url": "",
})
r = S.lookup_github("torvalds")
print(f"[200] confidence={r.get('confidence')} name={r.get('full_name')!r}")
check("GitHub 200 → confidence 100", r.get("confidence") == 100)
check("GitHub 200 → real name resolved", r.get("full_name") == "Linus Torvalds")
check("GitHub 200 is NOT flagged lookup_failed", r.get("status") != "lookup_failed")

# ── Reddit 403 → lookup_failed ───────────────────────────────────────────────
S.requests.get = _make_fake_get(403)
r = S.lookup_reddit("spez")
print(f"[reddit 403] status={r.get('status')!r}")
check("Reddit 403 → status 'lookup_failed'", r.get("status") == "lookup_failed")

# ── Reddit 404 → not_found ───────────────────────────────────────────────────
S.requests.get = _make_fake_get(404)
r = S.lookup_reddit("nope_zzz")
check("Reddit 404 → status 'not_found'", r.get("status") == "not_found")

# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 72)
print("PART 2 — GitHub token enrichment (rate-limit fix)")
print("=" * 72)

# ── token present → Authorization header attached ────────────────────────────
_captured_headers.clear()
config.GITHUB_TOKEN = "ghp_FAKE_TEST_TOKEN_123"
S.requests.get = _make_fake_get(200, json_data={"login": "x", "created_at": ""})
S.lookup_github("x")
hdr = _captured_headers[-1] if _captured_headers else {}
print(f"\n[token set] Authorization present: {'Authorization' in hdr}")
check("token set → Authorization header attached", hdr.get("Authorization") == "Bearer ghp_FAKE_TEST_TOKEN_123")

# ── token absent → no Authorization header ───────────────────────────────────
_captured_headers.clear()
config.GITHUB_TOKEN = ""
S.requests.get = _make_fake_get(200, json_data={"login": "x", "created_at": ""})
S.lookup_github("x")
hdr = _captured_headers[-1] if _captured_headers else {}
print(f"[token empty] Authorization present: {'Authorization' in hdr}")
check("token empty → no Authorization header", "Authorization" not in hdr)
check("GitHub Accept header still set", "vnd.github" in hdr.get("Accept", ""))

# ══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 72)
passed, total = sum(results), len(results)
print(f"SUMMARY: {passed}/{total} checks passed")
print("ALL SEARCH-ROBUSTNESS CHECKS PASSED" if passed == total else "SOME CHECKS FAILED")
sys.exit(0 if passed == total else 1)
