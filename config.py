import os
import sys
import io
import logging
from dotenv import load_dotenv
from pathlib import Path

_ON_LINUX = os.path.exists("/tmp")

def safe_print(*args, **kwargs):
    """
    Print that never crashes on Windows cp1252 stdout.
    Replaces any unencodable character with ? instead of raising
    UnicodeEncodeError.
    """
    try:
        print(*args, **kwargs)
    except UnicodeEncodeError:
        safe_args = []
        for arg in args:
            try:
                text  = str(arg)
                codec = getattr(sys.stdout, 'encoding', 'utf-8') or 'utf-8'
                safe_args.append(
                    text.encode(codec, errors='replace').decode(codec, errors='replace')
                )
            except Exception:
                safe_args.append(repr(arg))
        print(*safe_args, **kwargs)


# Load environment variables from .env
BASE_DIR = Path(__file__).parent.resolve()
load_dotenv(BASE_DIR / ".env")

# ── Streamlit Cloud secrets → os.environ ─────────────────────────────────────
def load_cloud_secrets():
    loaded = []
    try:
        import streamlit as st
        secret_keys = list(st.secrets.keys())
        for k in secret_keys:
            v = st.secrets[k]
            if isinstance(v, str):
                os.environ[k] = v
                loaded.append(k)
        if loaded:
            print(f"[SECRETS] Loaded {len(loaded)} keys: {loaded}")
        else:
            print("[SECRETS] No string secrets found")
    except Exception as e:
        print(f"[SECRETS] Failed: {e}")
    return loaded

load_cloud_secrets()

# ── Centralised logging ────────────────────────────────────────────────────────
_LOG_DIR = Path("/tmp/logs") if _ON_LINUX else Path("logs")
_LOG_DIR.mkdir(parents=True, exist_ok=True)

_log_formatter  = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
_file_handler   = logging.FileHandler(str(_LOG_DIR / "aetherlens.log"), encoding="utf-8")
_file_handler.setFormatter(_log_formatter)
_stream_handler = logging.StreamHandler()
_stream_handler.setFormatter(_log_formatter)
_stream_handler.setLevel(logging.WARNING)

logger = logging.getLogger("aetherlens")
logger.setLevel(logging.INFO)
if not logger.handlers:
    logger.addHandler(_file_handler)
    logger.addHandler(_stream_handler)

# ── API Keys ──────────────────────────────────────────────────────────────────
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# ── API Endpoints ─────────────────────────────────────────────────────────────
GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"

# ── BEDROCK CLIENT (Claude Sonnet 4 · ap-south-1 · data stays in India) ──────
AWS_REGION = os.getenv("AWS_REGION", "ap-south-1")


def get_bedrock_client():
    # Always reload secrets first
    load_cloud_secrets()

    key    = os.environ.get("AWS_ACCESS_KEY_ID",     "").strip()
    secret = os.environ.get("AWS_SECRET_ACCESS_KEY", "").strip()
    region = os.environ.get("AWS_REGION",            "ap-south-1").strip()
    model  = os.environ.get(
        "BEDROCK_MODEL_ID",
        "apac.anthropic.claude-sonnet-4-20250514-v1:0",
    ).strip()

    print(f"[BEDROCK] Key present: {bool(key)} len={len(key)}")

    if not key or not secret:
        print("[BEDROCK] MISSING CREDS")
        return None, model

    try:
        import boto3
        client = boto3.client(
            service_name          = "bedrock-runtime",
            region_name           = region,
            aws_access_key_id     = key,
            aws_secret_access_key = secret,
        )
        print(f"[BEDROCK] Client ready region={region} model={model}")
        return client, model
    except Exception as e:
        print(f"[BEDROCK] Init error: {e}")
        return None, model


# Initialize at module level
bedrock_client, BEDROCK_MODEL_ID = get_bedrock_client()


def test_bedrock_connection():
    """Test Bedrock connectivity. Returns (success: bool, message: str)."""
    _client, _model = get_bedrock_client()
    if _client is None:
        return False, "not_initialized"
    try:
        import json as _json
        body = _json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 10,
            "messages": [{"role": "user", "content": "Say: ONLINE"}],
        })
        response = _client.invoke_model(
            modelId     = _model,
            body        = body,
            contentType = "application/json",
            accept      = "application/json",
        )
        result = _json.loads(response["body"].read())
        text   = result["content"][0]["text"]
        return True, text
    except Exception as e:
        try:
            code = e.response["Error"]["Code"]  # type: ignore[attr-defined]
            return False, code
        except Exception:
            return False, str(e)


def test_gemini_connection():
    """Test Gemini API connectivity. Returns (success: bool, message: str)."""
    if not GEMINI_API_KEY or GEMINI_API_KEY in ("", "your_gemini_key_here"):
        return False, "GEMINI_API_KEY not set"
    try:
        import requests as _req
        url  = f"{GEMINI_ENDPOINT}?key={GEMINI_API_KEY}"
        body = {
            "contents": [{"parts": [{"text": "Respond exactly: GEMINI ONLINE"}]}],
            "generationConfig": {"temperature": 0.0, "maxOutputTokens": 16},
        }
        resp = _req.post(url, json=body, timeout=15)
        resp.raise_for_status()
        text = resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
        if "GEMINI" in text or "ONLINE" in text:
            return True, text
        return True, text
    except Exception as e:
        return False, str(e)


# ── Database ──────────────────────────────────────────────────────────────────
if _ON_LINUX:
    DATABASE_DIR  = Path("/tmp")
    DATABASE_PATH = Path("/tmp/aetherlens.db")
else:
    DATABASE_DIR  = BASE_DIR / "database"
    DATABASE_PATH = DATABASE_DIR / "aetherlens.db"
DATABASE_URL  = f"sqlite:///{DATABASE_PATH}"

# ── JWT ───────────────────────────────────────────────────────────────────────
JWT_SECRET     = os.getenv("JWT_SECRET", "AetherLens@X9#vK2mP5nQ8wR")
JWT_ALGORITHM  = "HS256"
JWT_EXPIRY_MIN = 30  # minutes

# ── Admin Defaults ────────────────────────────────────────────────────────────
ADMIN_PIN      = os.getenv("ADMIN_PIN", "747291")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "AetherLens@2024")

# ── Security ──────────────────────────────────────────────────────────────────
MAX_PIN_ATTEMPTS  = 3
LOCKOUT_MINUTES   = 10
BCRYPT_ROUNDS     = 12

# ── Roles ─────────────────────────────────────────────────────────────────────
ROLE_ADMIN   = "ADMIN"
ROLE_ANALYST = "ANALYST"
ROLE_VIEWER  = "VIEWER"
ALL_ROLES    = [ROLE_ADMIN, ROLE_ANALYST, ROLE_VIEWER]

# ── App Metadata ──────────────────────────────────────────────────────────────
APP_NAME    = "AetherLens"
APP_VERSION = "2.0.0"
APP_TAGLINE = "Intelligence Operating System"

# ── Exports ───────────────────────────────────────────────────────────────────
EXPORTS_DIR = Path("/tmp/exports") if _ON_LINUX else BASE_DIR / "exports"

# ── Assets ────────────────────────────────────────────────────────────────────
ASSETS_DIR = BASE_DIR / "assets"
CSS_PATH   = ASSETS_DIR / "styles.css"

# ── Ensure directories exist ──────────────────────────────────────────────────
try:
    DATABASE_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    pass
try:
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    pass
