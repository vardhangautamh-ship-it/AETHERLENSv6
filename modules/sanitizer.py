"""
AetherLens — Universal Input Sanitizer & Defensive Decorator
Imported by every module. Makes every input safe before processing.
"""

import functools
import traceback
import logging
import json
import re
from pathlib import Path

# ── Logging setup ──────────────────────────────────────────────────────────────
Path("logs").mkdir(exist_ok=True)

_formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
_file_handler   = logging.FileHandler("logs/aetherlens.log", encoding="utf-8")
_file_handler.setFormatter(_formatter)
_stream_handler = logging.StreamHandler()
_stream_handler.setFormatter(_formatter)
_stream_handler.setLevel(logging.ERROR)   # only errors to terminal from here

_sanitizer_logger = logging.getLogger("aetherlens.sanitizer")
_sanitizer_logger.setLevel(logging.ERROR)
if not _sanitizer_logger.handlers:
    _sanitizer_logger.addHandler(_file_handler)
    _sanitizer_logger.addHandler(_stream_handler)


# ══════════════════════════════════════════════════════════════════════════════
# PRIMITIVE COERCERS
# ══════════════════════════════════════════════════════════════════════════════

def safe_str(val, default: str = "") -> str:
    """Convert any value to a stripped string safely."""
    if val is None:
        return default
    if isinstance(val, (dict, list)):
        try:
            return json.dumps(val, default=str)
        except Exception:
            return str(val)
    try:
        return str(val).strip()
    except Exception:
        return default


def safe_int(val, default: int = 0) -> int:
    """Convert any value to int safely."""
    try:
        return int(float(str(val)))
    except Exception:
        return default


def safe_float(val, default: float = 0.0) -> float:
    """Convert any value to float safely."""
    try:
        return float(str(val))
    except Exception:
        return default


def safe_list(val, default=None) -> list:
    """Convert any value to a list safely."""
    if default is None:
        default = []
    if val is None:
        return default
    if isinstance(val, list):
        return val
    if isinstance(val, (str, int, float)):
        return [val]
    if isinstance(val, dict):
        return list(val.values())
    try:
        return list(val)
    except Exception:
        return default


def safe_dict(val, default=None) -> dict:
    """Convert any value to a dict safely."""
    if default is None:
        default = {}
    if val is None:
        return default
    if isinstance(val, dict):
        return val
    try:
        return json.loads(str(val))
    except Exception:
        return default


# ══════════════════════════════════════════════════════════════════════════════
# DOMAIN-SPECIFIC COERCERS
# ══════════════════════════════════════════════════════════════════════════════

_BAD_NAMES = {
    "zero data", "empty file", "no data", "unknown", "none", "null", "nan",
    "not found", "n/a", "test file", "sample", "field officer report",
    "intelligence report", "background profile", "restricted", "aetherlens",
    "document", "file", "source", "report",
}


def safe_name(val) -> str:
    """Return a clean subject name, or 'Unknown Subject' for garbage values."""
    name = safe_str(val)
    if not name:
        return "Unknown Subject"
    if name.lower().strip() in _BAD_NAMES:
        return "Unknown Subject"
    if len(name.strip()) < 3:
        return "Unknown Subject"
    return name.strip()


def safe_phone(val) -> str | None:
    """Clean a phone string. Returns None if result is too short to be a phone."""
    phone   = safe_str(val)
    cleaned = re.sub(r"[^\d\+\-\s]", "", phone)
    if len(re.sub(r"\D", "", cleaned)) < 8:
        return None
    return cleaned.strip() or None


def safe_confidence(val, min_val: int = 0, max_val: int = 100) -> int:
    """Clamp a confidence score to [min_val, max_val]."""
    score = safe_int(val, 0)
    return max(min_val, min(max_val, score))


def flatten_to_str(val) -> str:
    """Recursively flatten any nested structure to a single space-joined string."""
    if val is None:
        return ""
    if isinstance(val, str):
        return val
    if isinstance(val, list):
        return " ".join(flatten_to_str(i) for i in val if i is not None)
    if isinstance(val, dict):
        return " ".join(flatten_to_str(v) for v in val.values() if v is not None)
    return str(val)


# ══════════════════════════════════════════════════════════════════════════════
# DEFENSIVE DECORATOR
# ══════════════════════════════════════════════════════════════════════════════

def defensive(fallback=None):
    """
    Wrap any function so that on any unhandled exception:
      1. The full traceback is logged to logs/aetherlens.log
      2. The error is printed to terminal (prefixed)
      3. The fallback value (or callable result) is returned

    Usage:
        @defensive(fallback={"key": "default"})
        def my_function(...):
            ...
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as exc:
                module = getattr(func, "__module__", "?")
                name   = getattr(func, "__name__",   "?")
                tb     = traceback.format_exc()
                msg    = f"[{module}.{name}] FAILED: {exc}\n{tb}"
                _sanitizer_logger.error(msg)
                print(msg)
                if callable(fallback):
                    return fallback()
                return fallback
        return wrapper
    return decorator
