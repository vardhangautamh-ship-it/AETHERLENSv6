"""
AetherLens Authentication Module
Handles PIN gate, user login, JWT sessions, and audit logging.
"""

import sqlite3
import bcrypt
import jwt
import datetime
import streamlit as st
from contextlib import contextmanager

import config


# ── Database helpers ──────────────────────────────────────────────────────────

@contextmanager
def get_db():
    conn = sqlite3.connect(str(config.DATABASE_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """Create all tables and seed the default admin on first launch."""
    with get_db() as conn:
        cur = conn.cursor()

        # PIN settings table (single row)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS pin_config (
                id              INTEGER PRIMARY KEY DEFAULT 1,
                pin_hash        TEXT NOT NULL,
                failed_attempts INTEGER NOT NULL DEFAULT 0,
                locked_until    TEXT
            )
        """)

        # Users table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                username      TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role          TEXT NOT NULL DEFAULT 'VIEWER',
                created_at    TEXT NOT NULL,
                is_active     INTEGER NOT NULL DEFAULT 1
            )
        """)

        # Audit log table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                event      TEXT NOT NULL,
                username   TEXT,
                detail     TEXT,
                ip         TEXT,
                timestamp  TEXT NOT NULL
            )
        """)

        # Seed PIN if not present
        existing_pin = cur.execute("SELECT id FROM pin_config WHERE id = 1").fetchone()
        if not existing_pin:
            raw_pin = str(config.ADMIN_PIN).zfill(6)[:6]
            pin_hash = bcrypt.hashpw(raw_pin.encode(), bcrypt.gensalt(rounds=config.BCRYPT_ROUNDS)).decode()
            cur.execute(
                "INSERT OR IGNORE INTO pin_config (id, pin_hash, failed_attempts) VALUES (1, ?, 0)",
                (pin_hash,)
            )

        # Seed default admin if not present
        existing_admin = cur.execute(
            "SELECT id FROM users WHERE username = ?", (config.ADMIN_USERNAME,)
        ).fetchone()
        if not existing_admin:
            pw_hash = bcrypt.hashpw(
                config.ADMIN_PASSWORD.encode(),
                bcrypt.gensalt(rounds=config.BCRYPT_ROUNDS)
            ).decode()
            now = datetime.datetime.utcnow().isoformat()
            cur.execute(
                "INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
                (config.ADMIN_USERNAME, pw_hash, config.ROLE_ADMIN, now)
            )
            _write_audit(cur, "ADMIN_CREATED", config.ADMIN_USERNAME, "Default admin seeded on first launch")


def _write_audit(cur, event: str, username: str = None, detail: str = None, ip: str = None):
    """Internal: write an audit entry using an existing cursor."""
    now = datetime.datetime.utcnow().isoformat()
    cur.execute(
        "INSERT INTO audit_log (event, username, detail, ip, timestamp) VALUES (?, ?, ?, ?, ?)",
        (event, username, detail, ip, now)
    )


def write_audit(event: str, username: str = None, detail: str = None, ip: str = None):
    """Public: open connection, write audit entry, close."""
    with get_db() as conn:
        _write_audit(conn.cursor(), event, username, detail, ip)


# ── PIN Gate ──────────────────────────────────────────────────────────────────

def get_pin_state() -> dict:
    """Return the current PIN row as a plain dict."""
    with get_db() as conn:
        row = conn.execute("SELECT * FROM pin_config WHERE id = 1").fetchone()
        if row:
            return dict(row)
    return {}


def is_pin_locked() -> tuple[bool, int]:
    """
    Returns (locked: bool, seconds_remaining: int).
    seconds_remaining is 0 when not locked.
    """
    state = get_pin_state()
    if not state or not state.get("locked_until"):
        return False, 0
    locked_until = datetime.datetime.fromisoformat(state["locked_until"])
    now = datetime.datetime.utcnow()
    if now < locked_until:
        remaining = int((locked_until - now).total_seconds())
        return True, remaining
    # Lockout expired — clear it
    with get_db() as conn:
        conn.execute(
            "UPDATE pin_config SET failed_attempts = 0, locked_until = NULL WHERE id = 1"
        )
    return False, 0


def verify_pin(entered_pin: str) -> bool:
    """
    Verify the 6-digit PIN.
    Tracks failures and applies lockout after MAX_PIN_ATTEMPTS.
    Returns True on success, False on failure.
    Raises RuntimeError if currently locked out.
    """
    locked, secs = is_pin_locked()
    if locked:
        raise RuntimeError(f"PIN locked. Try again in {secs} seconds.")

    state = get_pin_state()
    pin_hash = state["pin_hash"].encode()
    correct = bcrypt.checkpw(entered_pin.encode(), pin_hash)

    with get_db() as conn:
        cur = conn.cursor()
        if correct:
            cur.execute(
                "UPDATE pin_config SET failed_attempts = 0, locked_until = NULL WHERE id = 1"
            )
            _write_audit(cur, "PIN_SUCCESS")
            return True
        else:
            new_attempts = state["failed_attempts"] + 1
            if new_attempts >= config.MAX_PIN_ATTEMPTS:
                unlock_at = (
                    datetime.datetime.utcnow() + datetime.timedelta(minutes=config.LOCKOUT_MINUTES)
                ).isoformat()
                cur.execute(
                    "UPDATE pin_config SET failed_attempts = ?, locked_until = ? WHERE id = 1",
                    (new_attempts, unlock_at)
                )
                _write_audit(cur, "PIN_LOCKED", detail=f"Locked until {unlock_at}")
            else:
                cur.execute(
                    "UPDATE pin_config SET failed_attempts = ? WHERE id = 1",
                    (new_attempts,)
                )
                _write_audit(cur, "PIN_FAILURE", detail=f"Attempt {new_attempts}/{config.MAX_PIN_ATTEMPTS}")
            return False


# ── Login / JWT ───────────────────────────────────────────────────────────────

def verify_login(username: str, password: str) -> dict | None:
    """
    Verify username + password.
    Returns user dict on success, None on failure.
    """
    with get_db() as conn:
        cur = conn.cursor()
        row = cur.execute(
            "SELECT * FROM users WHERE username = ? AND is_active = 1", (username,)
        ).fetchone()
        if not row:
            _write_audit(cur, "LOGIN_FAILURE", username, "User not found")
            return None
        user = dict(row)
        if bcrypt.checkpw(password.encode(), user["password_hash"].encode()):
            _write_audit(cur, "LOGIN_SUCCESS", username, f"Role: {user['role']}")
            return user
        else:
            _write_audit(cur, "LOGIN_FAILURE", username, "Wrong password")
            return None


def create_token(user: dict) -> str:
    """Generate a signed JWT for the authenticated user."""
    expiry = datetime.datetime.utcnow() + datetime.timedelta(minutes=config.JWT_EXPIRY_MIN)
    payload = {
        "sub": user["username"],
        "role": user["role"],
        "exp": expiry,
        "iat": datetime.datetime.utcnow(),
    }
    return jwt.encode(payload, config.JWT_SECRET, algorithm=config.JWT_ALGORITHM)


def decode_token(token: str) -> dict | None:
    """
    Decode and validate JWT.
    Returns payload dict, or None if expired / invalid.
    """
    try:
        return jwt.decode(token, config.JWT_SECRET, algorithms=[config.JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


# ── Streamlit Session Helpers ─────────────────────────────────────────────────

def session_init():
    """Initialise all auth-related session state keys if not present."""
    defaults = {
        "pin_verified":  False,
        "jwt_token":     None,
        "current_user":  None,
        "current_role":  None,
        "auth_error":    None,
        "pin_attempts":  0,
    }
    for key, val in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = val


def session_login(token: str, user: dict):
    """Store token and user info in session state."""
    st.session_state.jwt_token    = token
    st.session_state.current_user = user["username"]
    st.session_state.current_role = user["role"]
    st.session_state.auth_error   = None


def session_logout():
    """Clear all session auth state and audit the logout."""
    username = st.session_state.get("current_user")
    if username:
        write_audit("LOGOUT", username)
    for key in ["pin_verified", "jwt_token", "current_user", "current_role", "auth_error", "pin_attempts"]:
        st.session_state[key] = None if key not in ("pin_verified",) else False
    st.session_state.pin_verified = False
    st.session_state.pin_attempts = 0


def validate_session() -> bool:
    """
    Called on every page load.
    Returns True if the session token is still valid.
    Clears session and returns False if token is missing or expired.
    """
    token = st.session_state.get("jwt_token")
    if not token:
        return False
    payload = decode_token(token)
    if payload is None:
        write_audit("SESSION_EXPIRED", st.session_state.get("current_user"))
        session_logout()
        return False
    # Refresh cached role/user from token payload (defensive)
    st.session_state.current_user = payload["sub"]
    st.session_state.current_role = payload["role"]
    return True


# ══════════════════════════════════════════════════════════════════════════════
# ADMIN FUNCTIONS
# ══════════════════════════════════════════════════════════════════════════════

def get_all_users() -> list[dict]:
    """Return all users with last-login derived from audit_log."""
    with get_db() as conn:
        users = [dict(r) for r in conn.execute(
            "SELECT id, username, role, created_at, is_active FROM users ORDER BY id"
        ).fetchall()]
        for u in users:
            row = conn.execute(
                "SELECT timestamp FROM audit_log WHERE event='LOGIN_SUCCESS' AND username=? ORDER BY id DESC LIMIT 1",
                (u["username"],)
            ).fetchone()
            u["last_login"] = row["timestamp"] if row else "Never"
            u.pop("id", None)
        return users


def admin_create_user(username: str, password: str, role: str, created_by: str) -> tuple[bool, str]:
    """Create a new user. Returns (success, message)."""
    if role not in config.ALL_ROLES:
        return False, f"Invalid role: {role}"
    username = username.strip().lower()
    if not username or len(username) < 3:
        return False, "Username must be at least 3 characters."
    try:
        pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=config.BCRYPT_ROUNDS)).decode()
        now     = datetime.datetime.utcnow().isoformat()
        with get_db() as conn:
            existing = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
            if existing:
                return False, f"Username '{username}' already exists."
            conn.execute(
                "INSERT INTO users (username, password_hash, role, created_at) VALUES (?,?,?,?)",
                (username, pw_hash, role, now)
            )
            _write_audit(conn.cursor(), "ADMIN_USER_CREATED", created_by,
                         f"Created user: {username} role: {role}")
        return True, f"User '{username}' created with role {role}."
    except Exception as e:
        return False, str(e)


def admin_delete_user(username: str, deleted_by: str) -> tuple[bool, str]:
    """Delete a user. Cannot delete yourself or the last admin."""
    if username == deleted_by:
        return False, "Cannot delete your own account."
    try:
        with get_db() as conn:
            row = conn.execute("SELECT role FROM users WHERE username=?", (username,)).fetchone()
            if not row:
                return False, f"User '{username}' not found."
            if row["role"] == config.ROLE_ADMIN:
                count = conn.execute(
                    "SELECT count(*) FROM users WHERE role=? AND is_active=1", (config.ROLE_ADMIN,)
                ).fetchone()[0]
                if count <= 1:
                    return False, "Cannot delete the last admin account."
            conn.execute("DELETE FROM users WHERE username=?", (username,))
            _write_audit(conn.cursor(), "ADMIN_USER_DELETED", deleted_by, f"Deleted: {username}")
        return True, f"User '{username}' deleted."
    except Exception as e:
        return False, str(e)


def admin_change_role(username: str, new_role: str, changed_by: str) -> tuple[bool, str]:
    """Change a user's role."""
    if new_role not in config.ALL_ROLES:
        return False, f"Invalid role: {new_role}"
    try:
        with get_db() as conn:
            conn.execute("UPDATE users SET role=? WHERE username=?", (new_role, username))
            _write_audit(conn.cursor(), "ADMIN_ROLE_CHANGE", changed_by,
                         f"{username} -> {new_role}")
        return True, f"Role updated to {new_role}."
    except Exception as e:
        return False, str(e)


def admin_reset_password(username: str, new_password: str, reset_by: str) -> tuple[bool, str]:
    """Reset a user's password."""
    try:
        pw_hash = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt(rounds=config.BCRYPT_ROUNDS)).decode()
        with get_db() as conn:
            conn.execute("UPDATE users SET password_hash=? WHERE username=?", (pw_hash, username))
            _write_audit(conn.cursor(), "ADMIN_PW_RESET", reset_by, f"Reset password for: {username}")
        return True, "Password reset."
    except Exception as e:
        return False, str(e)


def admin_toggle_lock(username: str, lock: bool, changed_by: str) -> tuple[bool, str]:
    """Lock or unlock a user account (is_active 0=locked 1=active)."""
    if username == changed_by and lock:
        return False, "Cannot lock your own account."
    try:
        with get_db() as conn:
            conn.execute("UPDATE users SET is_active=? WHERE username=?",
                         (0 if lock else 1, username))
            action = "ADMIN_USER_LOCKED" if lock else "ADMIN_USER_UNLOCKED"
            _write_audit(conn.cursor(), action, changed_by, f"Target: {username}")
        return True, ("Account locked." if lock else "Account unlocked.")
    except Exception as e:
        return False, str(e)


def get_audit_log(
    limit: int = 200,
    username_filter: str = None,
    action_filter: str = None,
    date_from: str = None,
    date_to: str = None,
) -> list[dict]:
    """Query audit log with optional filters."""
    clauses = []
    params  = []
    if username_filter and username_filter != "All":
        clauses.append("username = ?")
        params.append(username_filter)
    if action_filter and action_filter != "All":
        clauses.append("event = ?")
        params.append(action_filter)
    if date_from:
        clauses.append("timestamp >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("timestamp <= ?")
        params.append(date_to + "T23:59:59")

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    query = f"SELECT * FROM audit_log {where} ORDER BY id DESC LIMIT ?"
    params.append(limit)

    with get_db() as conn:
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def get_system_stats() -> dict:
    """Return live system statistics."""
    try:
        with get_db() as conn:
            n_users    = conn.execute("SELECT count(*) FROM users WHERE is_active=1").fetchone()[0]
            n_searches = conn.execute("SELECT count(*) FROM audit_log WHERE event='SEARCH'").fetchone()[0]
            n_reports  = conn.execute("SELECT count(*) FROM audit_log WHERE event='REPORT_GENERATED'").fetchone()[0]
            n_uploads  = conn.execute("SELECT count(*) FROM audit_log WHERE event='FILE_UPLOAD'").fetchone()[0]
            last_row   = conn.execute("SELECT timestamp, username FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
            last_ts    = last_row["timestamp"] if last_row else "Never"
            last_user  = last_row["username"]  if last_row else ""
        return {
            "users":       n_users,
            "searches":    n_searches,
            "reports":     n_reports,
            "uploads":     n_uploads,
            "last_active": last_ts,
            "last_user":   last_user,
            "db_ok":       True,
        }
    except Exception as e:
        return {"db_ok": False, "error": str(e), "users": 0, "searches": 0,
                "reports": 0, "uploads": 0, "last_active": "Error", "last_user": ""}
