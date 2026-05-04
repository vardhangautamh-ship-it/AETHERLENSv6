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
# Must run before any os.getenv() calls so cloud secrets win over stale values.
def load_cloud_secrets():
    """
    Inject Streamlit secrets into os.environ.
    Handles both flat  (AWS_ACCESS_KEY_ID = "...")
    and nested         ([secrets] / AWS_ACCESS_KEY_ID = "...") TOML layouts.
    """
    try:
        import streamlit as st
        secrets = dict(st.secrets)
        for k, v in secrets.items():
            if isinstance(v, str):
                os.environ[k] = v
            else:
                # Nested section — flatten one level deep
                try:
                    for nk, nv in dict(v).items():
                        if isinstance(nv, str):
                            os.environ[nk] = nv
                except Exception:
                    pass
    except Exception:
        pass

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
GROK_API_KEY   = os.getenv("GROK_API_KEY", "")

# ── API Endpoints ─────────────────────────────────────────────────────────────
GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
GROK_ENDPOINT   = "https://api.x.ai/v1/chat/completions"
GROK_API_BASE   = "https://api.x.ai/v1"
GROK_MODEL      = "grok-4"
GROK_MAX_TOKENS = 8000
GROK_TEMPERATURE = 0.1

# ── Grok 4 OpenAI-compatible client ───────────────────────────────────────────
try:
    from openai import OpenAI as _OpenAI
    grok_client = _OpenAI(
        api_key=GROK_API_KEY or "placeholder",
        base_url=GROK_API_BASE,
    )
except Exception:
    grok_client = None

def test_grok_connection():
    """Test Grok 4 connectivity. Returns (success: bool, message: str)."""
    if not GROK_API_KEY or GROK_API_KEY in ("", "your_grok_key_here"):
        return False, "GROK_API_KEY not set"
    if grok_client is None:
        return False, "openai package not installed"
    try:
        response = grok_client.chat.completions.create(
            model=GROK_MODEL,
            max_tokens=10,
            messages=[{"role": "user", "content": "Respond exactly: GROK4 ONLINE"}],
        )
        msg = response.choices[0].message.content
        return True, msg
    except Exception as e:
        return False, str(e)

# ── BEDROCK CLIENT (Claude Sonnet 4 · ap-south-1 · data stays in India) ──────

def get_bedrock_client():
    """
    Load secrets, then initialise the Bedrock runtime client.
    Returns (client, model_id). Called at module load and can be
    re-called if credentials arrive late (e.g. Streamlit secrets).
    """
    load_cloud_secrets()           # attempt env injection from st.secrets

    key    = os.getenv("AWS_ACCESS_KEY_ID",     "")
    secret = os.getenv("AWS_SECRET_ACCESS_KEY", "")
    region = os.getenv("AWS_REGION",            "ap-south-1")
    model  = os.getenv(
        "BEDROCK_MODEL_ID",
        "apac.anthropic.claude-sonnet-4-20250514-v1:0",
    )

    # Direct st.secrets fallback — works at runtime even if the import-time
    # load_cloud_secrets() call above failed (Streamlit context not yet ready).
    # Also handles nested TOML sections, e.g. [secrets] / AWS_ACCESS_KEY_ID = "..."
    if not key or not secret:
        try:
            import streamlit as st
            def _get_secret(name, default=""):
                # Try top-level first
                val = st.secrets.get(name, "")
                if val:
                    return str(val)
                # Try one level of nesting
                for section_val in st.secrets.values():
                    try:
                        nested = dict(section_val)
                        if name in nested and nested[name]:
                            return str(nested[name])
                    except Exception:
                        pass
                return default
            key    = _get_secret("AWS_ACCESS_KEY_ID",     key    or "")
            secret = _get_secret("AWS_SECRET_ACCESS_KEY", secret or "")
            region = _get_secret("AWS_REGION",            region)
            model  = _get_secret("BEDROCK_MODEL_ID",      model)
        except Exception:
            pass

    if not key or not secret:
        return None, model
    try:
        import boto3
        client = boto3.client(
            service_name          = "bedrock-runtime",
            region_name           = region,
            aws_access_key_id     = key,
            aws_secret_access_key = secret,
        )
        return client, model
    except Exception as e:
        logger.warning(f"[BEDROCK] Init failed: {e}")
        return None, model

bedrock_client, BEDROCK_MODEL_ID = get_bedrock_client()
AWS_REGION = os.getenv("AWS_REGION", "ap-south-1")


def test_bedrock_connection():
    """Test Bedrock connectivity. Returns (success: bool, message: str)."""
    # Use module-level vars (may have been refreshed by lazy re-init in app.py)
    import sys
    _mod = sys.modules[__name__]
    _client = getattr(_mod, "bedrock_client", None)
    _model  = getattr(_mod, "BEDROCK_MODEL_ID", BEDROCK_MODEL_ID)
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
