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

# ── BEDROCK CLIENT (Claude Opus 4 · ap-south-1 · data stays in India) ────────
bedrock_client    = None
BEDROCK_MODEL_ID  = os.getenv("BEDROCK_MODEL_ID", "apac.anthropic.claude-sonnet-4-20250514-v1:0")
AWS_REGION        = os.getenv("AWS_REGION", "ap-south-1")

try:
    import boto3
    from botocore.exceptions import ClientError, NoCredentialsError
    _aws_key    = os.getenv("AWS_ACCESS_KEY_ID", "")
    _aws_secret = os.getenv("AWS_SECRET_ACCESS_KEY", "")
    if _aws_key and _aws_secret:
        bedrock_client = boto3.client(
            service_name          = "bedrock-runtime",
            region_name           = AWS_REGION,
            aws_access_key_id     = _aws_key,
            aws_secret_access_key = _aws_secret,
        )
        try:
            print(f"[BEDROCK] Client initialized — region: {AWS_REGION}")
        except Exception:
            pass
    else:
        try:
            print("[BEDROCK] AWS credentials not set in .env — skipping init")
        except Exception:
            pass
except Exception as e:
    try:
        print(f"[BEDROCK] Init failed: {e}")
    except Exception:
        pass
    bedrock_client = None


def test_bedrock_connection():
    """Test Bedrock connectivity. Returns (success: bool, message: str)."""
    if bedrock_client is None:
        return False, "not_initialized"
    try:
        import json as _json
        body = _json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 10,
            "messages": [{"role": "user", "content": "Say: ONLINE"}],
        })
        response = bedrock_client.invoke_model(
            modelId     = BEDROCK_MODEL_ID,
            body        = body,
            contentType = "application/json",
            accept      = "application/json",
        )
        result = _json.loads(response["body"].read())
        text   = result["content"][0]["text"]
        try:
            print(f"[BEDROCK] Test response: {text}")
        except Exception:
            pass
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
