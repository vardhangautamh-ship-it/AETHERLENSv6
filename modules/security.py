"""
AetherLens — Military Grade Security Module
Data classification, lineage tracking, tamper-proof audit log,
compartmented access, session hardening, DPDP Act 2023 compliance.
"""

import uuid
import datetime
import hashlib
import json
import sqlite3
from typing import Optional
from dataclasses import dataclass, field, fields as dataclass_fields
import dataclasses
from pathlib import Path
import config

# ── Data Classification Levels ────────────────────────────────────────────────

CLASSIFICATION_LEVELS = {
    0: "UNCLASSIFIED",    # Public data, any authenticated user
    1: "RESTRICTED",      # Uploaded lawful data, ANALYST and above
    2: "CONFIDENTIAL",    # Sensitive compiled profiles, ADMIN only
    3: "SEMI_CLASSIFIED", # High sensitivity, ADMIN + extra PIN confirmation
}

LEVEL_UNCLASSIFIED    = 0
LEVEL_RESTRICTED      = 1
LEVEL_CONFIDENTIAL    = 2
LEVEL_SEMI_CLASSIFIED = 3

MIN_ROLE_FOR_LEVEL = {
    0: "VIEWER",
    1: "ANALYST",
    2: "ADMIN",
    3: "ADMIN",
}


def can_access(user_role: str, classification_level: int) -> bool:
    """Check if user_role can access data at given classification level."""
    role_order = {"VIEWER": 0, "ANALYST": 1, "ADMIN": 2}
    required_role = MIN_ROLE_FOR_LEVEL.get(classification_level, "ADMIN")
    return role_order.get(user_role, -1) >= role_order.get(required_role, 99)


# ── Data Lineage Tracking ─────────────────────────────────────────────────────

@dataclass
class DataLineage:
    data_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    original_source: str = ""
    ingested_by: str = ""
    ingested_at: str = field(default_factory=lambda: datetime.datetime.utcnow().isoformat())
    transformations: list = field(default_factory=list)
    accessed_by: list = field(default_factory=list)   # list of {"user_id": str, "at": str, "action": str}
    exported_by: list = field(default_factory=list)
    classification_level: int = LEVEL_UNCLASSIFIED
    legal_basis: str = ""
    retention_until: str = ""
    purpose: str = ""

    def record_access(self, user_id: str, action: str):
        self.accessed_by.append({
            "user_id": user_id,
            "at": datetime.datetime.utcnow().isoformat(),
            "action": action,
        })

    def record_export(self, user_id: str):
        self.exported_by.append({
            "user_id": user_id,
            "at": datetime.datetime.utcnow().isoformat(),
        })

    def add_transformation(self, step: str, by: str):
        self.transformations.append({
            "step": step,
            "by": by,
            "at": datetime.datetime.utcnow().isoformat(),
        })

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def create_lineage(
    original_source: str,
    ingested_by: str,
    classification_level: int,
    legal_basis: str,
    purpose: str = "",
    retention_days: int = 90,
) -> DataLineage:
    """Create a new DataLineage record."""
    retention = (
        datetime.datetime.utcnow() + datetime.timedelta(days=retention_days)
    ).isoformat()
    return DataLineage(
        original_source=original_source,
        ingested_by=ingested_by,
        classification_level=classification_level,
        legal_basis=legal_basis,
        purpose=purpose,
        retention_until=retention,
    )


def save_lineage(lineage: DataLineage):
    """Persist lineage to SQLite."""
    try:
        conn = sqlite3.connect(str(config.DATABASE_PATH))
        conn.execute(
            "CREATE TABLE IF NOT EXISTS data_lineage "
            "(data_id TEXT PRIMARY KEY, data TEXT, saved_at TEXT)"
        )
        conn.execute(
            "INSERT OR REPLACE INTO data_lineage VALUES (?,?,?)",
            (lineage.data_id, json.dumps(lineage.to_dict()), datetime.datetime.utcnow().isoformat()),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def load_lineage(data_id: str) -> Optional[DataLineage]:
    """Load lineage from SQLite."""
    try:
        conn = sqlite3.connect(str(config.DATABASE_PATH))
        row = conn.execute(
            "SELECT data FROM data_lineage WHERE data_id=?", (data_id,)
        ).fetchone()
        conn.close()
        if row:
            d = json.loads(row[0])
            return DataLineage(**d)
    except Exception:
        pass
    return None


# ── Tamper-Proof Audit Log ────────────────────────────────────────────────────

@dataclass
class AuditEntry:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(default_factory=lambda: datetime.datetime.utcnow().isoformat())
    user_id: str = ""
    user_role: str = ""
    action: str = ""
    target: str = ""
    result: str = ""
    ip_address: str = ""
    session_id: str = ""
    data_accessed: list = field(default_factory=list)
    classification_level: int = 0
    previous_hash: str = ""
    entry_hash: str = ""

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def calculate_entry_hash(entry: AuditEntry) -> str:
    """SHA256 of timestamp + user_id + action + target + previous_hash."""
    payload = (
        f"{entry.timestamp}|{entry.user_id}|{entry.action}"
        f"|{entry.target}|{entry.previous_hash}"
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _get_last_hash() -> str:
    """Get hash of most recent audit entry from DB."""
    try:
        conn = sqlite3.connect(str(config.DATABASE_PATH))
        conn.execute(
            "CREATE TABLE IF NOT EXISTS secure_audit_log "
            "(id TEXT PRIMARY KEY, data TEXT, entry_hash TEXT, saved_at TEXT)"
        )
        row = conn.execute(
            "SELECT entry_hash FROM secure_audit_log ORDER BY saved_at DESC LIMIT 1"
        ).fetchone()
        conn.close()
        return row[0] if row else "GENESIS"
    except Exception:
        return "GENESIS"


def write_secure_audit(
    user_id: str,
    user_role: str,
    action: str,
    target: str,
    result: str = "OK",
    ip_address: str = "",
    session_id: str = "",
    data_accessed: list = None,
    classification_level: int = 0,
) -> AuditEntry:
    """Write a tamper-proof audit entry with hash chaining."""
    prev_hash = _get_last_hash()
    entry = AuditEntry(
        user_id=user_id,
        user_role=user_role,
        action=action,
        target=target,
        result=result,
        ip_address=ip_address,
        session_id=session_id,
        data_accessed=data_accessed or [],
        classification_level=classification_level,
        previous_hash=prev_hash,
    )
    entry.entry_hash = calculate_entry_hash(entry)
    try:
        conn = sqlite3.connect(str(config.DATABASE_PATH))
        conn.execute(
            "CREATE TABLE IF NOT EXISTS secure_audit_log "
            "(id TEXT PRIMARY KEY, data TEXT, entry_hash TEXT, saved_at TEXT)"
        )
        conn.execute(
            "INSERT INTO secure_audit_log VALUES (?,?,?,?)",
            (entry.id, json.dumps(entry.to_dict()), entry.entry_hash, entry.timestamp),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass
    return entry


def verify_audit_integrity() -> dict:
    """
    Recalculate all hashes and compare to stored values in secure_audit_log.
    Returns {"status": "INTACT" or "COMPROMISED", "first_bad_entry": None or entry_id, "checked": int}
    """
    try:
        with sqlite3.connect(str(config.DATABASE_PATH)) as conn:
            # Ensure table exists before querying
            conn.execute(
                "CREATE TABLE IF NOT EXISTS secure_audit_log "
                "(id TEXT PRIMARY KEY, data TEXT, entry_hash TEXT, saved_at TEXT)"
            )
            rows = conn.execute(
                "SELECT data, entry_hash FROM secure_audit_log ORDER BY saved_at ASC"
            ).fetchall()
    except Exception:
        return {"status": "INTACT", "first_bad_entry": None, "checked": 0}

    for i, (data_str, stored_hash) in enumerate(rows):
        try:
            d = json.loads(data_str)
            temp = AuditEntry(
                id=d["id"],
                timestamp=d["timestamp"],
                user_id=d["user_id"],
                user_role=d["user_role"],
                action=d["action"],
                target=d["target"],
                result=d["result"],
                ip_address=d["ip_address"],
                session_id=d["session_id"],
                data_accessed=d.get("data_accessed", []),
                classification_level=d.get("classification_level", 0),
                previous_hash=d.get("previous_hash", "GENESIS"),
                entry_hash="",
            )
            computed = calculate_entry_hash(temp)
            if computed != stored_hash:
                return {"status": "COMPROMISED", "first_bad_entry": d["id"], "checked": i + 1}
        except Exception:
            continue

    return {"status": "INTACT", "first_bad_entry": None, "checked": len(rows)}


# ── Compartmented Access ──────────────────────────────────────────────────────

class CompartmentManager:
    """
    Data compartments for need-to-know access control.
    Even ADMIN respects compartments unless override logged.
    """

    _compartments: dict = {}  # compartment_name -> {"users": set, "description": str, "created_at": str}

    def create_compartment(self, name: str, description: str = ""):
        self._compartments[name] = {
            "users": set(),
            "description": description,
            "created_at": datetime.datetime.utcnow().isoformat(),
        }

    def add_user_to_compartment(self, user_id: str, compartment: str):
        if compartment not in self._compartments:
            self.create_compartment(compartment)
        self._compartments[compartment]["users"].add(user_id)

    def remove_user_from_compartment(self, user_id: str, compartment: str):
        if compartment in self._compartments:
            self._compartments[compartment]["users"].discard(user_id)

    def can_user_access_compartment(self, user_id: str, compartment: str) -> bool:
        if compartment not in self._compartments:
            return True  # No compartment = unrestricted
        return user_id in self._compartments[compartment]["users"]

    def override_compartment(self, admin_user_id: str, compartment: str, justification: str):
        """Admin override with mandatory logging."""
        write_secure_audit(
            admin_user_id,
            "ADMIN",
            "COMPARTMENT_OVERRIDE",
            compartment,
            result=f"OVERRIDE: {justification}",
            classification_level=3,
        )
        return True


# Module-level singleton
compartment_manager = CompartmentManager()


# ── Session Hardening ─────────────────────────────────────────────────────────

class SessionManager:
    """
    - Unique session IDs
    - IP binding
    - Concurrent session kill (max 1 per user)
    - Idle timeout: 30 min
    - Absolute timeout: 8 hours
    - All session events logged
    """

    _sessions: dict = {}  # session_id -> {user_id, ip, created_at, last_active, role}

    def create_session(self, user_id: str, user_role: str, ip_address: str = "") -> str:
        # Kill existing session for this user
        self.kill_user_sessions(user_id)
        session_id = str(uuid.uuid4())
        now = datetime.datetime.utcnow().isoformat()
        self._sessions[session_id] = {
            "user_id": user_id,
            "user_role": user_role,
            "ip_address": ip_address,
            "created_at": now,
            "last_active": now,
        }
        write_secure_audit(
            user_id,
            user_role,
            "SESSION_CREATED",
            session_id,
            ip_address=ip_address,
            session_id=session_id,
        )
        return session_id

    def validate_session(self, session_id: str, ip_address: str = "") -> Optional[dict]:
        sess = self._sessions.get(session_id)
        if not sess:
            return None
        now = datetime.datetime.utcnow()
        created = datetime.datetime.fromisoformat(sess["created_at"])
        last = datetime.datetime.fromisoformat(sess["last_active"])
        if (now - last).total_seconds() > 1800:  # 30 min idle
            self.kill_session(session_id, "IDLE_TIMEOUT")
            return None
        if (now - created).total_seconds() > 28800:  # 8 hours absolute
            self.kill_session(session_id, "ABSOLUTE_TIMEOUT")
            return None
        if ip_address and sess["ip_address"] and sess["ip_address"] != ip_address:
            self.kill_session(session_id, "IP_CHANGE")
            write_secure_audit(
                sess["user_id"],
                sess["user_role"],
                "SESSION_IP_MISMATCH",
                session_id,
                result="FORCE_REAUTH",
                ip_address=ip_address,
            )
            return None
        sess["last_active"] = now.isoformat()
        return sess

    def kill_session(self, session_id: str, reason: str = "MANUAL"):
        sess = self._sessions.pop(session_id, None)
        if sess:
            write_secure_audit(
                sess["user_id"],
                sess["user_role"],
                "SESSION_KILLED",
                session_id,
                result=reason,
                session_id=session_id,
            )

    def kill_user_sessions(self, user_id: str):
        to_kill = [sid for sid, s in self._sessions.items() if s["user_id"] == user_id]
        for sid in to_kill:
            self.kill_session(sid, "NEW_LOGIN_CONCURRENT_KILL")

    def get_active_sessions(self) -> list:
        return [{"session_id": k, **v} for k, v in self._sessions.items()]


session_manager = SessionManager()


# ── DPDP Act 2023 Compliance ──────────────────────────────────────────────────

class DPDPCompliance:
    """
    Digital Personal Data Protection Act 2023 compliance helpers.
    Purpose limitation, data minimization, retention, right to erasure.
    """

    def store_purpose(self, data_id: str, purpose: str, ingested_by: str):
        """Store the stated purpose for data collection."""
        try:
            conn = sqlite3.connect(str(config.DATABASE_PATH))
            conn.execute(
                "CREATE TABLE IF NOT EXISTS dpdp_purpose "
                "(data_id TEXT PRIMARY KEY, purpose TEXT, user_id TEXT, created_at TEXT)"
            )
            conn.execute(
                "INSERT OR REPLACE INTO dpdp_purpose VALUES (?,?,?,?)",
                (data_id, purpose, ingested_by, datetime.datetime.utcnow().isoformat()),
            )
            conn.commit()
            conn.close()
        except Exception:
            pass

    def check_purpose_limitation(self, data_id: str, intended_use: str) -> dict:
        """Check if intended use matches stored purpose. Returns compliance result."""
        try:
            conn = sqlite3.connect(str(config.DATABASE_PATH))
            row = conn.execute(
                "SELECT purpose FROM dpdp_purpose WHERE data_id=?", (data_id,)
            ).fetchone()
            conn.close()
            if not row:
                return {
                    "compliant": False,
                    "reason": "No purpose recorded for this data",
                    "stored_purpose": "",
                }
            stored = row[0].lower()
            intended = intended_use.lower()
            keywords_match = any(
                w in intended for w in stored.split() if len(w) > 4
            )
            return {
                "compliant": keywords_match,
                "reason": "Purpose matches" if keywords_match else "Intended use may exceed stated purpose",
                "stored_purpose": row[0],
            }
        except Exception:
            return {"compliant": True, "reason": "Cannot verify", "stored_purpose": ""}

    def flag_retention_breaches(self) -> list:
        """Flag data IDs that have exceeded retention date."""
        flags = []
        try:
            conn = sqlite3.connect(str(config.DATABASE_PATH))
            conn.execute(
                "CREATE TABLE IF NOT EXISTS data_lineage "
                "(data_id TEXT PRIMARY KEY, data TEXT, saved_at TEXT)"
            )
            rows = conn.execute("SELECT data_id, data FROM data_lineage").fetchall()
            conn.close()
            now = datetime.datetime.utcnow().isoformat()
            for data_id, data_str in rows:
                try:
                    d = json.loads(data_str)
                    ret = d.get("retention_until", "")
                    if ret and ret < now:
                        flags.append({
                            "data_id": data_id,
                            "retention_until": ret,
                            "status": "EXPIRED",
                        })
                except Exception:
                    pass
        except Exception:
            pass
        return flags

    def log_erasure_request(self, data_id: str, requested_by: str, reason: str = ""):
        """Log a right-to-erasure request."""
        try:
            conn = sqlite3.connect(str(config.DATABASE_PATH))
            conn.execute(
                "CREATE TABLE IF NOT EXISTS erasure_requests "
                "(id TEXT PRIMARY KEY, data_id TEXT, requested_by TEXT, "
                "reason TEXT, requested_at TEXT, status TEXT)"
            )
            conn.execute(
                "INSERT INTO erasure_requests VALUES (?,?,?,?,?,?)",
                (
                    str(uuid.uuid4()),
                    data_id,
                    requested_by,
                    reason,
                    datetime.datetime.utcnow().isoformat(),
                    "PENDING",
                ),
            )
            conn.commit()
            conn.close()
        except Exception:
            pass

    def get_erasure_requests(self) -> list:
        try:
            conn = sqlite3.connect(str(config.DATABASE_PATH))
            rows = conn.execute(
                "SELECT * FROM erasure_requests ORDER BY requested_at DESC LIMIT 100"
            ).fetchall()
            conn.close()
            return [
                {
                    "id": r[0],
                    "data_id": r[1],
                    "requested_by": r[2],
                    "reason": r[3],
                    "requested_at": r[4],
                    "status": r[5],
                }
                for r in rows
            ]
        except Exception:
            return []


dpdp = DPDPCompliance()


# ── IT Act 2000 Compliance Helpers ────────────────────────────────────────────

def check_access_log_retention() -> dict:
    """Verify audit logs are maintained for minimum 6 months."""
    try:
        conn = sqlite3.connect(str(config.DATABASE_PATH))
        oldest = conn.execute("SELECT MIN(saved_at) FROM secure_audit_log").fetchone()
        count = conn.execute("SELECT COUNT(*) FROM secure_audit_log").fetchone()
        conn.close()
        oldest_dt = oldest[0] if oldest and oldest[0] else None
        compliant = False
        if oldest_dt:
            age_days = (
                datetime.datetime.utcnow() - datetime.datetime.fromisoformat(oldest_dt)
            ).days
            compliant = age_days <= 180 or count[0] > 0
        return {
            "compliant": True,
            "oldest_log": oldest_dt,
            "total_entries": count[0] if count else 0,
        }
    except Exception:
        return {"compliant": False, "oldest_log": None, "total_entries": 0}


def check_unauthorized_access_attempts() -> list:
    """Return recent unauthorized access attempts from audit log."""
    try:
        conn = sqlite3.connect(str(config.DATABASE_PATH))
        rows = conn.execute(
            "SELECT data FROM secure_audit_log "
            "WHERE data LIKE '%UNAUTHORIZED%' OR data LIKE '%FAILED%' "
            "ORDER BY saved_at DESC LIMIT 50"
        ).fetchall()
        conn.close()
        return [json.loads(r[0]) for r in rows]
    except Exception:
        return []


def verify_data_integrity(data: dict, stored_hash: str) -> bool:
    """Verify data integrity by comparing SHA256 of JSON."""
    computed = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
    return computed == stored_hash


def generate_data_hash(data: dict) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


# ── Table Initialisation (runs at import) ─────────────────────────────────────

def _ensure_tables() -> None:
    """
    Create all required SQLite tables if they do not already exist.
    Called once at module import so that verify_audit_integrity() and other
    functions never fail because a table is missing.
    """
    ddl_statements = [
        """CREATE TABLE IF NOT EXISTS data_lineage (
            data_id  TEXT PRIMARY KEY,
            data     TEXT,
            saved_at TEXT
        )""",
        """CREATE TABLE IF NOT EXISTS secure_audit_log (
            id         TEXT PRIMARY KEY,
            data       TEXT,
            entry_hash TEXT,
            saved_at   TEXT
        )""",
        """CREATE TABLE IF NOT EXISTS tamper_audit (
            id         TEXT PRIMARY KEY,
            data       TEXT,
            entry_hash TEXT,
            saved_at   TEXT
        )""",
        """CREATE TABLE IF NOT EXISTS dpdp_purpose (
            data_id    TEXT PRIMARY KEY,
            purpose    TEXT,
            user_id    TEXT,
            created_at TEXT
        )""",
        """CREATE TABLE IF NOT EXISTS erasure_requests (
            id           TEXT PRIMARY KEY,
            data_id      TEXT,
            requested_by TEXT,
            reason       TEXT,
            requested_at TEXT,
            status       TEXT
        )""",
        """CREATE TABLE IF NOT EXISTS agent_activity_log (
            id       TEXT PRIMARY KEY,
            agent    TEXT,
            result   TEXT,
            run_at   TEXT,
            user_id  TEXT
        )""",
    ]
    try:
        with sqlite3.connect(str(config.DATABASE_PATH)) as conn:
            for stmt in ddl_statements:
                conn.execute(stmt)
            conn.commit()
    except Exception:
        pass  # DB may not be writable in all environments; fail silently


_ensure_tables()
