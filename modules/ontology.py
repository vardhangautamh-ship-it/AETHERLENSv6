"""
AetherLens — Ontology Layer  (Phase 0.5 — Upgraded)
=====================================================
Central structured knowledge backbone for all AetherLens modules.

Entity types (17)
-----------------
Person, Phone, Email, BankAccount, Document, PlatformAccount,
Location, Event, Network, NetworkCluster, Device, Vehicle,
Asset, IPAddress, ImmigrationFlag, BorderCrossing, ImmigrationStatus

Relationship types (35)
-----------------------
See RELATIONSHIP_TYPES below.

Key capabilities added in Phase 0.5
-------------------------------------
* First-class Phone / Email / PlatformAccount / Document / Vehicle /
  IPAddress / Immigration entity nodes (previously only flattened strings).
* Inference rule engine: derives SHARES_PHONE_WITH, SHARES_EMAIL_WITH,
  risk propagation, frequent-contact links, contamination flags.
* Inverted property index: O(1) lookup "which entities have this phone?".
* Type index: O(1) find_by_type().
* validate(): self-loops, orphaned nodes, circular SAME_PERSON_AS.
* merge_persons(): absorb duplicate PersonEntity objects.
* get_centrality() / get_clusters(): graph-analytics layer.
* add_relationship_idempotent(): dedup-safe edge insertion.
* run_inference(): one-call rule engine execution.

Backward Compatibility
-----------------------
All Phase-0 / v5 callers continue to work unchanged:
  build_digital_twin()         same signature and return type
  OntologyGraph.add_entity()   same
  OntologyGraph.add_relationship() same
  OntologyGraph.query()        same
  OntologyGraph.find_hidden_connections() same
  OntologyGraph.get_entity_summary()      same
  OntologyGraph.export_graph_json()       same
  OntologyGraph.import_graph_json()       same
  OntologyGraph.to_plotly_figure()        same
  OntologyGraph.save_to_db()              same
  OntologyGraph.load_from_db()            same
  PersonEntity, LocationEntity, EventEntity,
  NetworkEntity, DeviceEntity, AssetEntity — all original fields kept
  calculate_risk_score()                  same signature
"""

import uuid
import datetime
import json
import dataclasses
import re
from dataclasses import dataclass, field
from typing import Any, Optional, List, Dict
import networkx as nx
import sqlite3

import config


# ── Entity type registry ───────────────────────────────────────────────────────

ENTITY_TYPES: frozenset = frozenset({
    "PERSON",
    "PHONE",
    "EMAIL",
    "BANK_ACCOUNT",
    "DOCUMENT",
    "PLATFORM_ACCOUNT",
    "LOCATION",
    "EVENT",
    "NETWORK",
    "NETWORK_CLUSTER",
    "DEVICE",
    "VEHICLE",
    "ASSET",
    "IP_ADDRESS",
    "IMMIGRATION_FLAG",
    "BORDER_CROSSING",
    "IMMIGRATION_STATUS",
})


# ── Relationship type registry ─────────────────────────────────────────────────

RELATIONSHIP_TYPES: set = {
    # ── Original 15 (Phase 0 / v5 — preserved exactly) ───────────────────────
    "KNOWS",
    "CONTACTED",
    "LOCATED_AT",
    "OWNS_DEVICE",
    "MEMBER_OF",
    "PARTICIPATED_IN",
    "ASSOCIATED_WITH",
    "SAME_PERSON_AS",
    "FAMILY_OF",
    "WORKS_WITH",
    "FOLLOWS",
    "OWNS_ASSET",
    "CONTROLS",
    "SUPPLIES_TO",
    "RECEIVES_FROM",
    # ── Phase 0.5 additions ───────────────────────────────────────────────────
    # Identifier ownership
    "HAS_PHONE",
    "HAS_EMAIL",
    "HAS_BANK_ACCOUNT",
    "HAS_DOCUMENT",
    "HAS_IP_ADDRESS",
    "HAS_PLATFORM_ACCOUNT",
    "OWNS_VEHICLE",
    # Derived / inferred identity links
    "SHARES_PHONE_WITH",
    "SHARES_EMAIL_WITH",
    "LINKED_TO",
    # Communication
    "CALLED",
    "MESSAGED",
    # Financial
    "TRANSFERRED_TO",
    "RECEIVED_FROM",
    "REMITTANCE_TO",
    # Physical / geographic
    "CO_LOCATED_WITH",
    # Immigration
    "TRAVELLED_TO",
    "ENTERED_VIA",
    "EXITED_VIA",
    # Enforcement
    "FLAGGED_FOR",
}


# ── Normalisation helpers ──────────────────────────────────────────────────────

def normalize_phone(raw: str) -> str:
    """
    Normalise a raw phone string to a clean, consistent representation.

    Handles Indian (E.164 +91), UAE (+971), UK (+44), US (+1), Singapore (+65),
    Pakistan (+92) prefixes. Falls back to stripped digits for unknowns.
    Returns empty string for invalid / unrecognisable input.
    """
    if not raw:
        return ""
    # Strip everything except digits and leading +
    cleaned = re.sub(r"[^\d+]", "", raw.strip())
    if not cleaned:
        return ""

    # Already E.164 with country code
    if cleaned.startswith("+"):
        return cleaned

    digits = cleaned.lstrip("0")

    # Indian: 91 + 10 digits = 12 digits
    if re.match(r"^91[6-9]\d{9}$", digits):
        return "+" + digits

    # Plain 10-digit Indian mobile (starts with 6–9)
    if re.match(r"^[6-9]\d{9}$", digits):
        return "+91" + digits

    # 0XXXXXXXXXX Indian with leading zero
    if re.match(r"^0[6-9]\d{9}$", cleaned):
        return "+91" + cleaned[1:]

    # UAE: 971 + 9 digits = 12 digits
    if re.match(r"^971\d{9}$", digits):
        return "+" + digits

    # Pakistan: 92 + 10 digits = 12 digits
    if re.match(r"^92\d{10}$", digits):
        return "+" + digits

    # UK: 44 + 10 digits = 12 digits
    if re.match(r"^44\d{10}$", digits):
        return "+" + digits

    # US/CA: 1 + 10 digits = 11 digits
    if re.match(r"^1\d{10}$", digits):
        return "+" + digits

    # Singapore: 65 + 8 digits = 10 digits
    if re.match(r"^65\d{8}$", digits):
        return "+" + digits

    # Minimum 7 digits to be a plausible number
    if len(digits) >= 7:
        return digits

    return ""


# Module-level constant — NOT a dataclass field (avoids frozenset serialisation issues)
_EMAIL_SERVICE_LOCAL_PARTS: frozenset = frozenset({
    "noreply", "no-reply", "donotreply", "mailer", "bounce",
    "alerts", "notification", "notifications", "newsletter", "promo", "marketing",
    "support", "help", "info", "offers", "updates", "billing", "admin",
})

# Ordered list of known country code prefixes (longer first to avoid greedy-match errors)
_COUNTRY_CODE_PREFIXES: tuple = (
    "+971", "+972", "+973", "+974", "+975", "+976", "+977",   # Gulf, Israel, Nepal …
    "+880", "+886", "+852", "+853", "+855", "+856",            # BD, TW, HK, MO, KH, LA
    "+44", "+65", "+91", "+92", "+93", "+94", "+95", "+96",   # UK, SG, IN, PK, AF, LK, MM, IR
    "+1",                                                      # US/CA
)


def _extract_country_code(norm: str) -> str:
    """Return the country code prefix (e.g. '+91') from an already-normalised phone number."""
    if not norm.startswith("+"):
        return ""
    for prefix in _COUNTRY_CODE_PREFIXES:
        if norm.startswith(prefix):
            return prefix
    # Fallback: take 1 digit after +
    m = re.match(r"^\+(\d{1,3})", norm)
    return "+" + m.group(1) if m else ""


def normalize_email(raw: str) -> str:
    """
    Normalise an email address: strip whitespace, lowercase.
    Returns empty string for clearly invalid input.
    """
    if not raw:
        return ""
    norm = raw.strip().lower()
    # Basic structural check: must have exactly one @ with something either side
    if norm.count("@") != 1:
        return ""
    local, domain = norm.split("@")
    if not local or not domain or "." not in domain:
        return ""
    return norm


# ── Entity Dataclasses ────────────────────────────────────────────────────────
#
# Convention
#   - Every entity exposes .id (UUID str) and .entity_type (UPPER_SNAKE str).
#   - Every entity implements to_dict() → plain dict (JSON-safe via asdict).
#   - Existing field names are NEVER removed (backward compat).
#   - New fields appended after existing ones.


@dataclass
class PersonEntity:
    """Primary subject — a human individual under investigation."""
    # Original fields (Phase 0 / v5 — preserved)
    id: str                    = field(default_factory=lambda: str(uuid.uuid4()))
    name: str                  = ""
    name_variants: list        = field(default_factory=list)
    usernames: dict            = field(default_factory=dict)
    emails: list               = field(default_factory=list)
    phones: list               = field(default_factory=list)
    locations: list            = field(default_factory=list)
    age_indicators: list       = field(default_factory=list)
    platforms: dict            = field(default_factory=dict)
    accounts: list             = field(default_factory=list)
    relationships: list        = field(default_factory=list)
    risk_score: float          = 0.0
    confidence: float          = 0.0
    tags: list                 = field(default_factory=list)
    created_at: str            = field(default_factory=lambda: datetime.datetime.utcnow().isoformat())
    updated_at: str            = field(default_factory=lambda: datetime.datetime.utcnow().isoformat())
    data_sources: list         = field(default_factory=list)
    classification: str        = "UNCLASSIFIED"
    entity_type: str           = "PERSON"
    # Phase 0.5 additions
    dob: str                   = ""        # date of birth if known
    nationality: str           = ""
    id_documents: list         = field(default_factory=list)   # passport, aadhar, PAN numbers
    immigration_flags: list    = field(default_factory=list)   # ImmigrationFlagEntity ids
    targeting_priority: str    = "NONE"    # NONE | LOW | MEDIUM | HIGH | CRITICAL

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclass
class PhoneEntity:
    """A telephone number — first-class graph node for CDR and contact analysis."""
    id: str             = field(default_factory=lambda: str(uuid.uuid4()))
    number: str         = ""    # normalised (E.164-like)
    raw: str            = ""    # original string as ingested
    country_code: str   = ""    # e.g. "+91", "+971"
    carrier: str        = ""    # telecom carrier if known
    number_type: str    = ""    # mobile | landline | voip | unknown
    is_verified: bool   = False
    owners_linked: list = field(default_factory=list)   # PersonEntity ids
    confidence: float   = 0.0
    data_sources: list  = field(default_factory=list)
    tags: list          = field(default_factory=list)
    created_at: str     = field(default_factory=lambda: datetime.datetime.utcnow().isoformat())
    entity_type: str    = "PHONE"

    @classmethod
    def from_raw(cls, raw: str, **kwargs) -> "PhoneEntity":
        """Construct and normalise in one call."""
        norm = normalize_phone(raw)
        cc = _extract_country_code(norm)
        return cls(number=norm, raw=raw, country_code=cc, **kwargs)

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclass
class EmailEntity:
    """An email address — first-class graph node for comms and identity analysis."""
    id: str             = field(default_factory=lambda: str(uuid.uuid4()))
    address: str        = ""    # normalised (lowercase)
    raw: str            = ""    # original string as ingested
    domain: str         = ""    # extracted domain part
    is_personal: bool   = True  # False for noreply@, service@, alerts@ etc.
    owners_linked: list = field(default_factory=list)
    confidence: float   = 0.0
    data_sources: list  = field(default_factory=list)
    tags: list          = field(default_factory=list)
    created_at: str     = field(default_factory=lambda: datetime.datetime.utcnow().isoformat())
    entity_type: str    = "EMAIL"

    @classmethod
    def from_raw(cls, raw: str, **kwargs) -> "EmailEntity":
        """Construct and normalise in one call. Detects service/automated senders."""
        norm = normalize_email(raw)
        domain = norm.split("@")[1] if "@" in norm else ""
        local_part = norm.split("@")[0] if "@" in norm else ""
        is_personal = not any(h in local_part for h in _EMAIL_SERVICE_LOCAL_PARTS)
        return cls(address=norm, raw=raw, domain=domain, is_personal=is_personal, **kwargs)

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclass
class BankAccountEntity:
    """A bank account — financial graph node."""
    id: str             = field(default_factory=lambda: str(uuid.uuid4()))
    account_number: str = ""
    ifsc_code: str      = ""
    bank_name: str      = ""
    account_type: str   = ""    # savings | current | nri | unknown
    holder_name: str    = ""
    branch: str         = ""
    owners_linked: list = field(default_factory=list)
    confidence: float   = 0.0
    data_sources: list  = field(default_factory=list)
    tags: list          = field(default_factory=list)
    created_at: str     = field(default_factory=lambda: datetime.datetime.utcnow().isoformat())
    entity_type: str    = "BANK_ACCOUNT"

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclass
class DocumentEntity:
    """An ingested file — tracks provenance and case prefix for contamination detection."""
    id: str             = field(default_factory=lambda: str(uuid.uuid4()))
    filename: str       = ""
    file_type: str      = ""    # csv | pdf | txt | xlsx | json
    case_prefix: str    = ""    # e.g. GHOSTWIRE, JUPITER (from filename)
    ingested_at: str    = field(default_factory=lambda: datetime.datetime.utcnow().isoformat())
    page_count: int     = 0
    row_count: int      = 0
    data_hash: str      = ""    # SHA-256 of file bytes for dedup
    subjects_linked: list = field(default_factory=list)   # PersonEntity ids
    confidence: float   = 0.0
    data_sources: list  = field(default_factory=list)
    tags: list          = field(default_factory=list)
    entity_type: str    = "DOCUMENT"

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclass
class PlatformAccountEntity:
    """A social-media or online platform account — cross-platform linking node."""
    id: str              = field(default_factory=lambda: str(uuid.uuid4()))
    platform: str        = ""    # Instagram | Telegram | GitHub | LinkedIn | …
    handle: str          = ""    # @username or screen name
    url: str             = ""    # profile URL
    follower_count: int  = 0
    following_count: int = 0
    join_date: str       = ""
    bio: str             = ""
    is_verified: bool    = False
    owners_linked: list  = field(default_factory=list)
    confidence: float    = 0.0
    data_sources: list   = field(default_factory=list)
    tags: list           = field(default_factory=list)
    created_at: str      = field(default_factory=lambda: datetime.datetime.utcnow().isoformat())
    entity_type: str     = "PLATFORM_ACCOUNT"

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclass
class LocationEntity:
    """A geographic location — stated, inferred, or captured."""
    # Original fields (Phase 0 / v5 — preserved)
    id: str              = field(default_factory=lambda: str(uuid.uuid4()))
    name: str            = ""
    type: str            = ""    # city | district | state | country | address | poi
    coordinates: dict    = field(default_factory=dict)    # {"lat": ..., "lon": ...}
    confidence: float    = 0.0
    persons_linked: list = field(default_factory=list)
    events_linked: list  = field(default_factory=list)
    data_sources: list   = field(default_factory=list)
    entity_type: str     = "LOCATION"
    # Phase 0.5 additions
    country: str         = ""
    state: str           = ""
    pin_code: str        = ""
    source_type: str     = ""    # stated | inferred | anpr | cdr_tower | challan

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclass
class EventEntity:
    """A discrete timestamped event — communication, movement, financial transaction."""
    # Original fields (Phase 0 / v5 — preserved)
    id: str                   = field(default_factory=lambda: str(uuid.uuid4()))
    type: str                 = ""
    timestamp: str            = ""
    description: str          = ""
    persons_involved: list    = field(default_factory=list)
    locations_involved: list  = field(default_factory=list)
    platforms_involved: list  = field(default_factory=list)
    significance: float       = 0.5
    data_sources: list        = field(default_factory=list)
    entity_type: str          = "EVENT"
    # Phase 0.5 additions
    event_category: str       = ""   # communication | financial | travel | social | legal
    severity: str             = ""   # LOW | MEDIUM | HIGH | CRITICAL
    source_document: str      = ""   # DocumentEntity id

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclass
class NetworkEntity:
    """A social / operational network cluster (original Phase 0 entity — preserved)."""
    id: str             = field(default_factory=lambda: str(uuid.uuid4()))
    type: str           = ""
    members: list       = field(default_factory=list)
    connections: list   = field(default_factory=list)
    strength: float     = 0.0
    cluster_id: str     = ""
    data_sources: list  = field(default_factory=list)
    entity_type: str    = "NETWORK"

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclass
class NetworkClusterEntity:
    """
    Enhanced network cluster with graph-analytic metadata.
    Use this for Phase 0.5+ cluster detection results.
    """
    id: str             = field(default_factory=lambda: str(uuid.uuid4()))
    cluster_id: str     = ""
    algorithm: str      = ""     # louvain | greedy_modularity | manual
    members: list       = field(default_factory=list)    # entity ids in cluster
    density: float      = 0.0    # edge density within cluster
    hub_entity_id: str  = ""     # most central member
    risk_score: float   = 0.0    # max risk across members
    data_sources: list  = field(default_factory=list)
    tags: list          = field(default_factory=list)
    created_at: str     = field(default_factory=lambda: datetime.datetime.utcnow().isoformat())
    entity_type: str    = "NETWORK_CLUSTER"

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclass
class DeviceEntity:
    """A digital device — phone handset, laptop, router, etc."""
    # Original fields (Phase 0 / v5 — preserved)
    id: str              = field(default_factory=lambda: str(uuid.uuid4()))
    type: str            = ""
    identifier: str      = ""
    owner_linked: list   = field(default_factory=list)
    locations_linked: list = field(default_factory=list)
    events_linked: list  = field(default_factory=list)
    data_sources: list   = field(default_factory=list)
    entity_type: str     = "DEVICE"
    # Phase 0.5 additions
    imei: str            = ""
    imsi: str            = ""
    make: str            = ""
    model: str           = ""

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclass
class VehicleEntity:
    """A motor vehicle — separate from generic AssetEntity for ANPR / challan work."""
    id: str              = field(default_factory=lambda: str(uuid.uuid4()))
    plate_number: str    = ""    # registration plate / number
    make: str            = ""    # e.g. Maruti, Honda
    model: str           = ""    # e.g. Swift, City
    colour: str          = ""
    year: int            = 0
    registration_state: str = ""
    chassis_number: str  = ""
    engine_number: str   = ""
    owner_linked: list   = field(default_factory=list)
    anpr_hit_count: int  = 0
    challan_count: int   = 0
    locations_seen: list = field(default_factory=list)
    confidence: float    = 0.0
    data_sources: list   = field(default_factory=list)
    tags: list           = field(default_factory=list)
    created_at: str      = field(default_factory=lambda: datetime.datetime.utcnow().isoformat())
    entity_type: str     = "VEHICLE"

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclass
class AssetEntity:
    """Generic asset — property, financial instrument, equipment (original Phase 0 entity)."""
    # Original fields (Phase 0 / v5 — preserved)
    id: str              = field(default_factory=lambda: str(uuid.uuid4()))
    name: str            = ""
    asset_type: str      = ""    # vehicle | personnel | equipment | property | financial
    identifier: str      = ""
    description: str     = ""
    owner_linked: list   = field(default_factory=list)
    locations_linked: list = field(default_factory=list)
    value_estimate: str  = ""
    confidence: float    = 0.0
    data_sources: list   = field(default_factory=list)
    tags: list           = field(default_factory=list)
    entity_type: str     = "ASSET"

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclass
class IPAddressEntity:
    """An IP address — from login logs, CDR records, or VPN detection."""
    id: str             = field(default_factory=lambda: str(uuid.uuid4()))
    ip: str             = ""
    asn: str            = ""       # Autonomous System Number
    org: str            = ""       # Organisation / ISP
    country: str        = ""
    city: str           = ""
    is_vpn: bool        = False
    is_proxy: bool      = False
    is_tor: bool        = False
    owners_linked: list = field(default_factory=list)
    seen_at: list       = field(default_factory=list)    # timestamps
    confidence: float   = 0.0
    data_sources: list  = field(default_factory=list)
    tags: list          = field(default_factory=list)
    created_at: str     = field(default_factory=lambda: datetime.datetime.utcnow().isoformat())
    entity_type: str    = "IP_ADDRESS"

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclass
class ImmigrationFlagEntity:
    """An enforcement / immigration watchlist flag on a person."""
    id: str              = field(default_factory=lambda: str(uuid.uuid4()))
    flag_type: str       = ""    # LOC | LOOKOUT | OVERSTAY | VISA_VIOLATION | DEPORTED | …
    severity: str        = ""    # LOW | MEDIUM | HIGH | CRITICAL
    authority: str       = ""    # issuing authority (e.g. FRRO, BEU, Customs)
    reference_number: str = ""
    issued_on: str       = ""
    valid_until: str     = ""
    notes: str           = ""
    subject_linked: str  = ""    # PersonEntity id
    confidence: float    = 0.0
    data_sources: list   = field(default_factory=list)
    tags: list           = field(default_factory=list)
    created_at: str      = field(default_factory=lambda: datetime.datetime.utcnow().isoformat())
    entity_type: str     = "IMMIGRATION_FLAG"

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclass
class BorderCrossingEvent:
    """A recorded border / immigration checkpoint crossing event."""
    id: str               = field(default_factory=lambda: str(uuid.uuid4()))
    direction: str        = ""    # ENTRY | EXIT
    port_of_entry: str    = ""    # airport / land border name
    crossing_date: str    = ""    # ISO date string
    mode: str             = ""    # air | land | sea
    travel_document: str  = ""    # passport number used
    visa_type: str        = ""    # tourist | business | student | work | transit
    flight_number: str    = ""
    origin_country: str   = ""
    destination_country: str = ""
    subject_linked: str   = ""    # PersonEntity id
    confidence: float     = 0.0
    data_sources: list    = field(default_factory=list)
    tags: list            = field(default_factory=list)
    created_at: str       = field(default_factory=lambda: datetime.datetime.utcnow().isoformat())
    entity_type: str      = "BORDER_CROSSING"

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclass
class ImmigrationStatus:
    """Current or historical immigration / visa status for a person."""
    id: str              = field(default_factory=lambda: str(uuid.uuid4()))
    status_type: str     = ""    # LEGAL_RESIDENT | VISA_HOLDER | OVERSTAY | UNDOCUMENTED | DEPORTED
    country: str         = ""    # country the status applies to
    valid_from: str      = ""
    valid_until: str     = ""
    is_overstay: bool    = False
    overstay_days: int   = 0
    visa_category: str   = ""    # e.g. B1/B2, F1, H1B, OCI, PIO
    issuing_authority: str = ""
    subject_linked: str  = ""    # PersonEntity id
    confidence: float    = 0.0
    data_sources: list   = field(default_factory=list)
    tags: list           = field(default_factory=list)
    created_at: str      = field(default_factory=lambda: datetime.datetime.utcnow().isoformat())
    entity_type: str     = "IMMIGRATION_STATUS"

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


# ── Entity type → class mapping (used by import_graph_json and inference) ─────

_ENTITY_MAP: dict = {
    "PERSON":            PersonEntity,
    "PHONE":             PhoneEntity,
    "EMAIL":             EmailEntity,
    "BANK_ACCOUNT":      BankAccountEntity,
    "DOCUMENT":          DocumentEntity,
    "PLATFORM_ACCOUNT":  PlatformAccountEntity,
    "LOCATION":          LocationEntity,
    "EVENT":             EventEntity,
    "NETWORK":           NetworkEntity,
    "NETWORK_CLUSTER":   NetworkClusterEntity,
    "DEVICE":            DeviceEntity,
    "VEHICLE":           VehicleEntity,
    "ASSET":             AssetEntity,
    "IP_ADDRESS":        IPAddressEntity,
    "IMMIGRATION_FLAG":  ImmigrationFlagEntity,
    "BORDER_CROSSING":   BorderCrossingEvent,
    "IMMIGRATION_STATUS": ImmigrationStatus,
}

# Property fields to index per entity type (drives find_by_property O(1) lookup)
_INDEX_PROPS: dict = {
    "PERSON":            ["name"],
    "PHONE":             ["number"],
    "EMAIL":             ["address"],
    "BANK_ACCOUNT":      ["account_number"],
    "DOCUMENT":          ["filename"],
    "PLATFORM_ACCOUNT":  ["handle", "platform"],
    "LOCATION":          ["name"],
    "EVENT":             ["type"],
    "NETWORK":           ["cluster_id"],
    "NETWORK_CLUSTER":   ["cluster_id"],
    "DEVICE":            ["identifier", "imei"],
    "VEHICLE":           ["plate_number"],
    "ASSET":             ["identifier"],
    "IP_ADDRESS":        ["ip"],
    "IMMIGRATION_FLAG":  ["flag_type", "reference_number"],
    "BORDER_CROSSING":   ["port_of_entry", "travel_document"],
    "IMMIGRATION_STATUS": ["status_type", "country"],
}


# ── Risk Scoring Engine ───────────────────────────────────────────────────────

RISK_FACTORS: dict = {
    "multiple_name_variants": {
        "weight": 10,
        "desc":   "Multiple name variants suggest identity complexity",
    },
    "recently_created_accounts": {
        "weight": 15,
        "desc":   "Accounts created < 6 months ago",
    },
    "inconsistent_locations": {
        "weight": 12,
        "desc":   "Conflicting location data across platforms",
    },
    "rapid_network_growth": {
        "weight": 18,
        "desc":   "Unusually rapid follower/connection growth",
    },
    "flagged_connections": {
        "weight": 25,
        "desc":   "Connections to previously flagged entities",
    },
    "behavioral_anomalies": {
        "weight": 20,
        "desc":   "Behavioral anomalies detected in activity",
    },
    "data_gaps": {
        "weight": 8,
        "desc":   "Significant data gaps in expected fields",
    },
    "vpn_proxy_indicators": {
        "weight": 15,
        "desc":   "VPN or proxy use indicators from IP data",
    },
    # Phase 0.5 additions
    "immigration_flag_active": {
        "weight": 30,
        "desc":   "Active immigration lookout / enforcement flag",
    },
    "visa_overstay": {
        "weight": 22,
        "desc":   "Confirmed or suspected visa overstay",
    },
    "shared_identifier": {
        "weight": 20,
        "desc":   "Phone or email shared with another subject",
    },
}


def calculate_risk_score(
    person_entity: PersonEntity,
    ontology_graph: Any = None,
    extra_data: Optional[dict] = None,
) -> dict:
    """
    Calculate a composite risk score for a PersonEntity.

    FALLBACK ONLY: the live risk authority is ai_agents.run_risk_agent (saturating
    deterministic score + LLM explanation). This rule-based scorer is invoked solely
    by report_generator._build_risk_section when RiskAgent is unavailable; do not
    use it as the primary path.

    Returns:
        {
            "risk_score":       float 0-100,
            "risk_level":       "LOW" | "MEDIUM" | "HIGH" | "CRITICAL",
            "risk_factors":     [breakdown dicts],
            "mitigation_notes": str,
            "confidence":       float 0-1,
        }

    Signature unchanged from Phase 0 — all callers continue to work.
    """
    try:
        extra_data = extra_data or {}
        score = 0.0
        breakdown = []

        now = datetime.datetime.utcnow()
        six_months_ago = now - datetime.timedelta(days=182)

        # ── multiple_name_variants ────────────────────────────────────────────
        if len(person_entity.name_variants) >= 3:
            factor = RISK_FACTORS["multiple_name_variants"]
            score += factor["weight"]
            breakdown.append({
                "factor":   "multiple_name_variants",
                "evidence": f"{len(person_entity.name_variants)} name variants detected",
                "weight":   factor["weight"],
                "source":   "entity_data",
            })

        # ── recently_created_accounts ─────────────────────────────────────────
        try:
            recent_accounts = []
            for account in person_entity.accounts:
                if isinstance(account, dict):
                    join_year = account.get("join_year")
                    join_date_str = account.get("join_date", "")
                    if join_year is not None:
                        try:
                            if datetime.datetime(int(join_year), 1, 1) >= six_months_ago:
                                recent_accounts.append(account)
                        except (ValueError, TypeError):
                            pass
                    elif join_date_str:
                        try:
                            if datetime.datetime.fromisoformat(str(join_date_str)) >= six_months_ago:
                                recent_accounts.append(account)
                        except (ValueError, TypeError):
                            pass
            if recent_accounts:
                factor = RISK_FACTORS["recently_created_accounts"]
                score += factor["weight"]
                breakdown.append({
                    "factor":   "recently_created_accounts",
                    "evidence": f"{len(recent_accounts)} account(s) created within the last 6 months",
                    "weight":   factor["weight"],
                    "source":   "account_data",
                })
        except Exception:
            pass

        # ── inconsistent_locations ────────────────────────────────────────────
        try:
            unique_locations = set(person_entity.locations)
            if len(unique_locations) >= 3:
                factor = RISK_FACTORS["inconsistent_locations"]
                score += factor["weight"]
                breakdown.append({
                    "factor":   "inconsistent_locations",
                    "evidence": (
                        f"{len(unique_locations)} distinct locations recorded: "
                        f"{', '.join(list(unique_locations)[:5])}"
                    ),
                    "weight":   factor["weight"],
                    "source":   "location_data",
                })
        except Exception:
            pass

        # ── rapid_network_growth ──────────────────────────────────────────────
        if extra_data.get("rapid_growth", False):
            factor = RISK_FACTORS["rapid_network_growth"]
            score += factor["weight"]
            breakdown.append({
                "factor":   "rapid_network_growth",
                "evidence": "Rapid network growth flag set in supplemental data",
                "weight":   factor["weight"],
                "source":   "behavioral_data",
            })

        # ── flagged_connections ───────────────────────────────────────────────
        if ontology_graph is not None:
            try:
                flagged_neighbors = []
                if person_entity.id in ontology_graph.graph:
                    for neighbor_id in ontology_graph.graph.successors(person_entity.id):
                        neighbor_entity = ontology_graph._entities.get(neighbor_id)
                        if neighbor_entity is not None:
                            if getattr(neighbor_entity, "risk_score", 0.0) > 70:
                                flagged_neighbors.append(
                                    getattr(neighbor_entity, "name", neighbor_id)
                                )
                    for neighbor_id in ontology_graph.graph.predecessors(person_entity.id):
                        neighbor_entity = ontology_graph._entities.get(neighbor_id)
                        if neighbor_entity is not None:
                            nb_label = getattr(neighbor_entity, "name", neighbor_id)
                            if (getattr(neighbor_entity, "risk_score", 0.0) > 70
                                    and nb_label not in flagged_neighbors):
                                flagged_neighbors.append(nb_label)
                if flagged_neighbors:
                    factor = RISK_FACTORS["flagged_connections"]
                    score += factor["weight"]
                    breakdown.append({
                        "factor":   "flagged_connections",
                        "evidence": (
                            f"Connected to {len(flagged_neighbors)} high-risk entity(ies): "
                            f"{', '.join(str(n) for n in flagged_neighbors[:5])}"
                        ),
                        "weight":   factor["weight"],
                        "source":   "graph_analysis",
                    })
            except Exception:
                pass

        # ── behavioral_anomalies ──────────────────────────────────────────────
        behavioral_flags = extra_data.get("behavioral_flags", [])
        if behavioral_flags:
            factor = RISK_FACTORS["behavioral_anomalies"]
            score += factor["weight"]
            breakdown.append({
                "factor":   "behavioral_anomalies",
                "evidence": (
                    f"{len(behavioral_flags)} behavioral flag(s): "
                    f"{', '.join(str(f) for f in behavioral_flags[:5])}"
                ),
                "weight":   factor["weight"],
                "source":   "behavioral_analysis",
            })

        # ── data_gaps ─────────────────────────────────────────────────────────
        if not person_entity.data_sources or len(person_entity.data_sources) < 2:
            factor = RISK_FACTORS["data_gaps"]
            score += factor["weight"]
            breakdown.append({
                "factor":   "data_gaps",
                "evidence": (
                    f"Only {len(person_entity.data_sources)} data source(s); "
                    "cross-validation not possible"
                ),
                "weight":   factor["weight"],
                "source":   "data_quality",
            })

        # ── vpn_proxy_indicators ──────────────────────────────────────────────
        if extra_data.get("vpn_detected", False):
            factor = RISK_FACTORS["vpn_proxy_indicators"]
            score += factor["weight"]
            breakdown.append({
                "factor":   "vpn_proxy_indicators",
                "evidence": "VPN or proxy use detected in IP analysis",
                "weight":   factor["weight"],
                "source":   "ip_analysis",
            })

        # ── immigration_flag_active (Phase 0.5) ───────────────────────────────
        if getattr(person_entity, "immigration_flags", []):
            factor = RISK_FACTORS["immigration_flag_active"]
            score += factor["weight"]
            breakdown.append({
                "factor":   "immigration_flag_active",
                "evidence": (
                    f"{len(person_entity.immigration_flags)} active immigration flag(s)"
                ),
                "weight":   factor["weight"],
                "source":   "immigration_data",
            })

        # ── shared_identifier (Phase 0.5) — set by inference engine ──────────
        if extra_data.get("shared_identifier", False):
            factor = RISK_FACTORS["shared_identifier"]
            score += factor["weight"]
            breakdown.append({
                "factor":   "shared_identifier",
                "evidence": "Phone or email shared with another subject in the graph",
                "weight":   factor["weight"],
                "source":   "inference_engine",
            })

        # Clamp to 0-100
        score = max(0.0, min(100.0, score))

        if score <= 25:
            risk_level = "LOW"
        elif score <= 50:
            risk_level = "MEDIUM"
        elif score <= 75:
            risk_level = "HIGH"
        else:
            risk_level = "CRITICAL"

        mitigation_map = {
            "LOW":      "No immediate action required. Continue routine monitoring.",
            "MEDIUM":   "Increase monitoring frequency. Cross-reference sources to resolve gaps.",
            "HIGH":     "Escalate for analyst review. Investigate flagged connections and anomalies.",
            "CRITICAL": "CRITICAL — Immediate escalation required. Full investigation warranted.",
        }

        confidence = min(1.0, len(person_entity.data_sources) / 5.0)

        return {
            "risk_score":       round(score, 2),
            "risk_level":       risk_level,
            "risk_factors":     breakdown,
            "mitigation_notes": mitigation_map[risk_level],
            "confidence":       round(confidence, 2),
        }

    except Exception as exc:
        return {
            "risk_score":       0.0,
            "risk_level":       "LOW",
            "risk_factors":     [],
            "mitigation_notes": f"Risk scoring failed: {exc}",
            "confidence":       0.0,
        }


# ── Inference Engine ──────────────────────────────────────────────────────────

@dataclass
class InferenceFact:
    """A single derived fact produced by an inference rule."""
    rule_name:          str
    source_entity_id:   str
    target_entity_id:   str
    relationship_type:  str
    strength:           float
    evidence:           list
    derived_at: str     = field(default_factory=lambda: datetime.datetime.utcnow().isoformat())

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


class InferenceRule:
    """Abstract base — subclass and implement apply()."""
    name: str        = "base_rule"
    description: str = ""

    def apply(self, graph: "OntologyGraph") -> list:
        """
        Scan the graph and return a list of InferenceFact objects.
        Called by OntologyGraph.run_inference().
        Must not mutate the graph directly — facts are applied by the caller.
        """
        return []


class PhoneShareInferenceRule(InferenceRule):
    """
    If two PersonEntity nodes both have HAS_PHONE edges to the same PhoneEntity,
    derive a SHARES_PHONE_WITH relationship between the two persons.
    """
    name        = "phone_share"
    description = "Shared phone number → SHARES_PHONE_WITH"

    def apply(self, graph: "OntologyGraph") -> list:
        facts = []
        phone_ids = [
            eid for eid, e in graph._entities.items()
            if getattr(e, "entity_type", "") == "PHONE"
        ]
        for phone_id in phone_ids:
            # Persons that point TO this phone via HAS_PHONE
            persons = [
                u for u, v, data in graph.graph.in_edges(phone_id, data=True)
                if data.get("type") == "HAS_PHONE"
                and getattr(graph._entities.get(u), "entity_type", "") == "PERSON"
            ]
            for i in range(len(persons)):
                for j in range(i + 1, len(persons)):
                    phone_ent = graph._entities.get(phone_id)
                    phone_num = getattr(phone_ent, "number", phone_id)
                    # Both directions
                    for src, tgt in [(persons[i], persons[j]), (persons[j], persons[i])]:
                        facts.append(InferenceFact(
                            rule_name         = self.name,
                            source_entity_id  = src,
                            target_entity_id  = tgt,
                            relationship_type = "SHARES_PHONE_WITH",
                            strength          = 0.9,
                            evidence          = [f"Shared phone: {phone_num}"],
                        ))
        return facts


class EmailShareInferenceRule(InferenceRule):
    """
    If two PersonEntity nodes both have HAS_EMAIL edges to the same EmailEntity,
    derive a SHARES_EMAIL_WITH relationship between the two persons.
    """
    name        = "email_share"
    description = "Shared email address → SHARES_EMAIL_WITH"

    def apply(self, graph: "OntologyGraph") -> list:
        facts = []
        email_ids = [
            eid for eid, e in graph._entities.items()
            if getattr(e, "entity_type", "") == "EMAIL"
        ]
        for email_id in email_ids:
            persons = [
                u for u, v, data in graph.graph.in_edges(email_id, data=True)
                if data.get("type") == "HAS_EMAIL"
                and getattr(graph._entities.get(u), "entity_type", "") == "PERSON"
            ]
            for i in range(len(persons)):
                for j in range(i + 1, len(persons)):
                    email_ent = graph._entities.get(email_id)
                    addr = getattr(email_ent, "address", email_id)
                    for src, tgt in [(persons[i], persons[j]), (persons[j], persons[i])]:
                        facts.append(InferenceFact(
                            rule_name         = self.name,
                            source_entity_id  = src,
                            target_entity_id  = tgt,
                            relationship_type = "SHARES_EMAIL_WITH",
                            strength          = 0.85,
                            evidence          = [f"Shared email: {addr}"],
                        ))
        return facts


class RiskPropagationRule(InferenceRule):
    """
    If a PersonEntity is directly linked to a high-risk entity (risk_score > 70),
    add a LINKED_TO edge with elevated strength to surface the association.
    Does not modify risk_score directly — that is re-calculated by the caller.
    """
    name        = "risk_propagation"
    description = "Proximity to high-risk entity → LINKED_TO (elevated)"

    def apply(self, graph: "OntologyGraph") -> list:
        facts = []
        person_ids = [
            eid for eid, e in graph._entities.items()
            if getattr(e, "entity_type", "") == "PERSON"
        ]
        for pid in person_ids:
            if pid not in graph.graph:
                continue
            neighbors = set(graph.graph.successors(pid)) | set(graph.graph.predecessors(pid))
            for nid in neighbors:
                neighbor = graph._entities.get(nid)
                if neighbor is None:
                    continue
                nb_risk = getattr(neighbor, "risk_score", 0.0)
                if nb_risk > 70 and nid != pid:
                    nb_label = getattr(neighbor, "name", nid)
                    facts.append(InferenceFact(
                        rule_name         = self.name,
                        source_entity_id  = pid,
                        target_entity_id  = nid,
                        relationship_type = "LINKED_TO",
                        strength          = min(0.95, nb_risk / 100),
                        evidence          = [
                            f"Adjacent to high-risk entity '{nb_label}' "
                            f"(risk={nb_risk:.0f})"
                        ],
                    ))
        return facts


class FrequentContactRule(InferenceRule):
    """
    If more than 5 CALLED or MESSAGED edges exist between the same pair of persons,
    derive a KNOWS relationship with high strength (repeated contact = known contact).
    """
    name        = "frequent_contact"
    description = "≥5 CALLED/MESSAGED edges between persons → KNOWS (high strength)"
    THRESHOLD   = 5

    def apply(self, graph: "OntologyGraph") -> list:
        facts = []
        # Count edges per (source, target) pair
        pair_counts: dict = {}
        for u, v, data in graph.graph.edges(data=True):
            if data.get("type") in ("CALLED", "MESSAGED"):
                key = (u, v)
                pair_counts[key] = pair_counts.get(key, 0) + 1

        for (src, tgt), count in pair_counts.items():
            if count >= self.THRESHOLD:
                src_e = graph._entities.get(src)
                tgt_e = graph._entities.get(tgt)
                if (src_e and tgt_e
                        and getattr(src_e, "entity_type", "") == "PERSON"
                        and getattr(tgt_e, "entity_type", "") == "PERSON"):
                    facts.append(InferenceFact(
                        rule_name         = self.name,
                        source_entity_id  = src,
                        target_entity_id  = tgt,
                        relationship_type = "KNOWS",
                        strength          = min(0.98, 0.6 + count * 0.04),
                        evidence          = [
                            f"{count} CALLED/MESSAGED events between "
                            f"'{getattr(src_e,'name',src)}' and "
                            f"'{getattr(tgt_e,'name',tgt)}'"
                        ],
                    ))
        return facts


class ContaminationDetectionRule(InferenceRule):
    """
    If DocumentEntity nodes from two or more distinct case prefixes exist in
    the same graph, flag the primary PersonEntity with a contamination tag.
    This mirrors the app-level detect_file_contamination() check inside the
    knowledge graph so analysts can see it in the graph view too.
    """
    name        = "contamination_detection"
    description = "Mixed case files in graph → contamination flag on person node"

    def apply(self, graph: "OntologyGraph") -> list:
        facts = []
        prefixes = set()
        for eid, e in graph._entities.items():
            if getattr(e, "entity_type", "") == "DOCUMENT":
                cp = getattr(e, "case_prefix", "").upper().strip()
                if cp:
                    prefixes.add(cp)

        if len(prefixes) < 2:
            return facts

        # Tag all persons in the graph
        person_ids = [
            eid for eid, e in graph._entities.items()
            if getattr(e, "entity_type", "") == "PERSON"
        ]
        prefix_str = ", ".join(sorted(prefixes))
        for pid in person_ids:
            entity = graph._entities.get(pid)
            if entity and hasattr(entity, "tags"):
                tag = f"CONTAMINATION:{prefix_str}"
                if tag not in entity.tags:
                    entity.tags.append(tag)
        return facts   # no edges derived — modification is tag-based


# Default rule set applied by run_inference()
ALL_RULES: list = [
    PhoneShareInferenceRule(),
    EmailShareInferenceRule(),
    RiskPropagationRule(),
    FrequentContactRule(),
    ContaminationDetectionRule(),
]


# ── Ontology Graph ────────────────────────────────────────────────────────────

class OntologyGraph:
    """
    Core semantic knowledge graph for AetherLens OSINT intelligence.

    Backed by a NetworkX MultiDiGraph.
    Persists to SQLite via config.DATABASE_PATH.

    Internal indexes
    ----------------
    _entities    : {entity_id: entity_obj}
    _type_index  : {entity_type: set(entity_ids)}
    _prop_index  : {(prop_name, value_lower): set(entity_ids)}

    All three are kept in sync by _index_entity() / _deindex_entity().
    """

    def __init__(self):
        self.graph: nx.MultiDiGraph = nx.MultiDiGraph()
        self._entities: dict = {}
        self._type_index: dict = {}
        self._prop_index: dict = {}

    # ── Internal indexing ─────────────────────────────────────────────────────

    def _index_entity(self, entity: Any) -> None:
        """Update type index and property index for entity."""
        eid   = entity.id
        etype = entity.entity_type
        self._type_index.setdefault(etype, set()).add(eid)
        for prop in _INDEX_PROPS.get(etype, ["name"]):
            val = getattr(entity, prop, None)
            if val:
                key = (prop, str(val).lower().strip())
                self._prop_index.setdefault(key, set()).add(eid)

    def _deindex_entity(self, entity: Any) -> None:
        """Remove entity from type index and property index."""
        eid   = entity.id
        etype = entity.entity_type
        if etype in self._type_index:
            self._type_index[etype].discard(eid)
        for prop in _INDEX_PROPS.get(etype, ["name"]):
            val = getattr(entity, prop, None)
            if val:
                key = (prop, str(val).lower().strip())
                if key in self._prop_index:
                    self._prop_index[key].discard(eid)

    # ── Entity management ─────────────────────────────────────────────────────

    def add_entity(self, entity: Any) -> None:
        """
        Add any entity dataclass to the graph.
        Silently overwrites on duplicate id.
        Updates type and property indexes automatically.
        """
        try:
            entity_id = entity.id
            self._entities[entity_id] = entity
            self._index_entity(entity)

            attrs = entity.to_dict()
            attrs["node_type"] = entity.entity_type

            etype = entity.entity_type
            if etype == "DEVICE":
                attrs["label"] = getattr(entity, "identifier", entity_id)
            elif etype == "ASSET":
                attrs["label"] = (getattr(entity, "name", None)
                                  or getattr(entity, "identifier", entity_id))
            elif etype == "PHONE":
                attrs["label"] = getattr(entity, "number", entity_id)
            elif etype == "EMAIL":
                attrs["label"] = getattr(entity, "address", entity_id)
            elif etype == "VEHICLE":
                attrs["label"] = getattr(entity, "plate_number", entity_id)
            elif etype == "IP_ADDRESS":
                attrs["label"] = getattr(entity, "ip", entity_id)
            else:
                attrs["label"] = getattr(entity, "name", entity_id)

            self.graph.add_node(entity_id, **attrs)
        except Exception:
            pass

    def remove_entity(self, entity_id: str) -> None:
        """Remove an entity and all its edges from the graph."""
        entity = self._entities.pop(entity_id, None)
        if entity:
            self._deindex_entity(entity)
        if entity_id in self.graph:
            self.graph.remove_node(entity_id)

    # ── Relationship management ───────────────────────────────────────────────

    def add_relationship(
        self,
        entity_a_id: str,
        entity_b_id: str,
        relationship_type: str,
        strength: float = 0.5,
        evidence: Optional[list] = None,
        data_source: str = "",
    ) -> None:
        """
        Add a directed edge between two entity nodes.
        Unknown relationship types fall back to ASSOCIATED_WITH with a warning.
        Signature unchanged from Phase 0.
        """
        try:
            if relationship_type not in RELATIONSHIP_TYPES:
                print(f"[ONTOLOGY] Unknown relationship type '{relationship_type}' "
                      f"— falling back to ASSOCIATED_WITH")
                relationship_type = "ASSOCIATED_WITH"

            edge_attrs = {
                "type":       relationship_type,
                "strength":   float(strength),
                "evidence":   evidence if isinstance(evidence, list) else [],
                "created_at": datetime.datetime.utcnow().isoformat(),
                "data_source": str(data_source),
            }

            if entity_a_id not in self.graph:
                self.graph.add_node(entity_a_id)
            if entity_b_id not in self.graph:
                self.graph.add_node(entity_b_id)

            self.graph.add_edge(entity_a_id, entity_b_id, **edge_attrs)
        except Exception:
            pass

    def add_relationship_idempotent(
        self,
        entity_a_id: str,
        entity_b_id: str,
        relationship_type: str,
        strength: float = 0.5,
        evidence: Optional[list] = None,
        data_source: str = "",
    ) -> None:
        """
        Dedup-safe edge insertion.
        If a relationship of the same type already exists between these two nodes,
        updates strength (taking the higher value) and merges evidence.
        Otherwise delegates to add_relationship().
        """
        try:
            for u, v, data in self.graph.out_edges(entity_a_id, data=True):
                if v == entity_b_id and data.get("type") == relationship_type:
                    # Update in place
                    if strength > data.get("strength", 0.0):
                        data["strength"] = float(strength)
                    existing_ev = data.get("evidence", [])
                    for e in (evidence or []):
                        if e not in existing_ev:
                            existing_ev.append(e)
                    return
        except Exception:
            pass
        self.add_relationship(entity_a_id, entity_b_id, relationship_type,
                              strength, evidence, data_source)

    # ── Query API ─────────────────────────────────────────────────────────────

    def query(
        self,
        entity_type: Optional[str] = None,
        relationship_type: Optional[str] = None,
        min_strength: float = 0.0,
        tags: Optional[list] = None,
    ) -> dict:
        """
        Filter entities and relationships by type, tags, and edge strength.
        Returns {"entities": [...dicts], "relationships": [...dicts]}.
        Signature unchanged from Phase 0.
        """
        try:
            matched = list(self._entities.values())

            if entity_type is not None:
                matched = [e for e in matched
                           if getattr(e, "entity_type", "") == entity_type]

            if tags:
                matched = [e for e in matched
                           if any(t in getattr(e, "tags", []) for t in tags)]

            entity_dicts = [e.to_dict() for e in matched]

            edge_list = []
            for u, v, key, data in self.graph.edges(data=True, keys=True):
                if relationship_type is not None and data.get("type") != relationship_type:
                    continue
                if data.get("strength", 0.0) < min_strength:
                    continue
                edge_list.append({"from": u, "to": v, "key": key, **data})

            return {"entities": entity_dicts, "relationships": edge_list}

        except Exception:
            return {"entities": [], "relationships": []}

    def find_by_type(self, entity_type: str) -> list:
        """
        Return all entity objects of the given type.  O(1) via type index.
        Returns a list of entity dataclass instances (not dicts).
        """
        ids = self._type_index.get(entity_type, set())
        return [self._entities[eid] for eid in ids if eid in self._entities]

    def find_by_property(self, prop: str, value: str) -> list:
        """
        Return all entity objects where entity.<prop> == value (case-insensitive).
        O(1) via property index.
        Returns a list of entity dataclass instances (not dicts).

        Examples
        --------
        graph.find_by_property("number", "+919999999999")  # PhoneEntity
        graph.find_by_property("plate_number", "MH01AB1234")  # VehicleEntity
        graph.find_by_property("address", "user@example.com")  # EmailEntity
        """
        key = (prop, str(value).lower().strip())
        ids = self._prop_index.get(key, set())
        return [self._entities[eid] for eid in ids if eid in self._entities]

    def find_related(
        self,
        entity_id: str,
        relationship_type: Optional[str] = None,
        direction: str = "both",
    ) -> list:
        """
        Return entities directly connected to entity_id.

        Args:
            entity_id:         Node to start from.
            relationship_type: Filter by edge type (None = all).
            direction:         "out" | "in" | "both"

        Returns:
            list of dicts: {
                "entity":       entity dataclass instance,
                "rel_type":     str,
                "strength":     float,
                "direction":    "outgoing" | "incoming",
                "evidence":     list,
            }
        """
        results = []
        if entity_id not in self.graph:
            return results

        if direction in ("out", "both"):
            for u, v, data in self.graph.out_edges(entity_id, data=True):
                rel = data.get("type", "")
                if relationship_type and rel != relationship_type:
                    continue
                ent = self._entities.get(v)
                if ent:
                    results.append({
                        "entity":    ent,
                        "rel_type":  rel,
                        "strength":  data.get("strength", 0.0),
                        "direction": "outgoing",
                        "evidence":  data.get("evidence", []),
                    })

        if direction in ("in", "both"):
            for u, v, data in self.graph.in_edges(entity_id, data=True):
                rel = data.get("type", "")
                if relationship_type and rel != relationship_type:
                    continue
                ent = self._entities.get(u)
                if ent:
                    results.append({
                        "entity":    ent,
                        "rel_type":  rel,
                        "strength":  data.get("strength", 0.0),
                        "direction": "incoming",
                        "evidence":  data.get("evidence", []),
                    })

        return results

    # ── Path finding ──────────────────────────────────────────────────────────

    def find_hidden_connections(self, entity_a_id: str, entity_b_id: str) -> dict:
        """
        Discover indirect connections up to 3 hops.
        Returns shortest_path, all_paths (≤10), degrees, found.
        Signature unchanged from Phase 0.
        """
        try:
            all_paths = list(
                nx.all_simple_paths(self.graph, entity_a_id, entity_b_id, cutoff=3)
            )[:10]

            try:
                shortest = nx.shortest_path(self.graph, entity_a_id, entity_b_id)
            except (nx.NetworkXNoPath, nx.NodeNotFound):
                shortest = []

            degrees = len(shortest) - 1 if len(shortest) > 1 else 0
            return {
                "shortest_path": shortest,
                "all_paths":     all_paths,
                "degrees":       degrees,
                "found":         len(shortest) > 0,
            }
        except Exception:
            return {"found": False, "shortest_path": [], "all_paths": [], "degrees": 0}

    # ── Entity summary ────────────────────────────────────────────────────────

    def get_entity_summary(self, entity_id: str) -> dict:
        """
        Full summary for a single entity: data, connected entities,
        all relationships, risk_score, confidence.
        Signature unchanged from Phase 0.
        """
        try:
            entity     = self._entities.get(entity_id)
            entity_dict = entity.to_dict() if entity else {}

            connected_ids = set()
            if entity_id in self.graph:
                connected_ids.update(self.graph.successors(entity_id))
                connected_ids.update(self.graph.predecessors(entity_id))

            connected_entities = []
            for cid in connected_ids:
                nb = self._entities.get(cid)
                connected_entities.append(nb.to_dict() if nb else {"id": cid})

            relationships = []
            if entity_id in self.graph:
                for u, v, key, data in self.graph.edges(entity_id, data=True, keys=True):
                    relationships.append({"from": u, "to": v, "key": key, **data})
                for u, v, key, data in self.graph.in_edges(entity_id, data=True, keys=True):
                    relationships.append({"from": u, "to": v, "key": key, **data})

            seen = set()
            deduped = []
            for rel in relationships:
                fp = (rel.get("from"), rel.get("to"), rel.get("key"))
                if fp not in seen:
                    seen.add(fp)
                    deduped.append(rel)

            return {
                "entity":            entity_dict,
                "connected_entities": connected_entities,
                "relationships":     deduped,
                "risk_score":        entity_dict.get("risk_score", 0.0),
                "confidence":        entity_dict.get("confidence", 0.0),
            }
        except Exception:
            return {
                "entity":            {},
                "connected_entities": [],
                "relationships":     [],
                "risk_score":        0.0,
                "confidence":        0.0,
            }

    # ── Inference ─────────────────────────────────────────────────────────────

    def run_inference(self, rules: Optional[list] = None) -> list:
        """
        Apply inference rules and insert derived relationships into the graph.

        Args:
            rules: list of InferenceRule instances.
                   Defaults to ALL_RULES if None.

        Returns:
            list of InferenceFact dicts describing every derived relationship.
        """
        active_rules = rules if rules is not None else ALL_RULES
        all_facts = []
        for rule in active_rules:
            try:
                facts = rule.apply(self)
                for fact in facts:
                    self.add_relationship_idempotent(
                        fact.source_entity_id,
                        fact.target_entity_id,
                        fact.relationship_type,
                        fact.strength,
                        fact.evidence,
                        f"inference:{fact.rule_name}",
                    )
                    all_facts.append(fact.to_dict())
            except Exception as e:
                print(f"[ONTOLOGY] Inference rule '{rule.name}' failed: {e}")
        if all_facts:
            print(f"[ONTOLOGY] Inference: {len(all_facts)} facts derived "
                  f"from {len(active_rules)} rule(s)")
        return all_facts

    # ── Validation ────────────────────────────────────────────────────────────

    def validate(self) -> list:
        """
        Run consistency checks on the graph.

        Checks:
          1. Self-loops on SAME_PERSON_AS (logical impossibility).
          2. Orphaned nodes (no edges — may indicate ingestion error).
          3. Nodes present in graph but missing entity object.
          4. Circular SAME_PERSON_AS chains (creates merge paradox).
          5. Invalid relationship types on edges.
          6. Entities with empty / placeholder ids.

        Returns:
            list of issue strings. Empty list means the graph is consistent.
        """
        issues = []

        # 1. Self-loops on SAME_PERSON_AS
        for u, v, data in self.graph.edges(data=True):
            if u == v and data.get("type") == "SAME_PERSON_AS":
                ent   = self._entities.get(u)
                label = getattr(ent, "name", u) if ent else u
                issues.append(f"SELF_LOOP: SAME_PERSON_AS on entity '{label}' ({u})")

        # 2. Orphaned nodes
        for nid in self.graph.nodes():
            if self.graph.degree(nid) == 0:
                ent   = self._entities.get(nid)
                label = getattr(ent, "name", nid) if ent else nid
                etype = getattr(ent, "entity_type", "?") if ent else "?"
                issues.append(f"ORPHAN: node '{label}' [{etype}] ({nid}) has no edges")

        # 3. Node in graph but no entity object
        for nid in self.graph.nodes():
            if nid not in self._entities:
                issues.append(f"MISSING_ENTITY: node {nid} in graph but no entity object")

        # 4. Circular SAME_PERSON_AS chains
        spa_edges = [
            (u, v) for u, v, d in self.graph.edges(data=True)
            if d.get("type") == "SAME_PERSON_AS"
        ]
        if spa_edges:
            try:
                spa_graph = nx.DiGraph()
                spa_graph.add_edges_from(spa_edges)
                cycle = nx.find_cycle(spa_graph)
                issues.append(
                    f"CIRCULAR_MERGE: SAME_PERSON_AS cycle detected: "
                    f"{' -> '.join(f'{u}->{v}' for u, v in cycle)}"
                )
            except nx.NetworkXNoCycle:
                pass
            except Exception:
                pass

        # 5. Invalid relationship types
        for u, v, data in self.graph.edges(data=True):
            rtype = data.get("type", "")
            if rtype and rtype not in RELATIONSHIP_TYPES:
                issues.append(f"INVALID_REL_TYPE: '{rtype}' on edge {u} -> {v}")

        # 6. Blank entity ids
        for eid, ent in self._entities.items():
            if not eid or eid.strip() == "":
                issues.append(f"BLANK_ID: entity with empty id: {ent!r}")

        return issues

    # ── Graph analytics ───────────────────────────────────────────────────────

    def get_centrality(self) -> dict:
        """
        Compute node centrality metrics.

        Returns:
            {
                "betweenness": {entity_id: float},
                "degree":      {entity_id: int},
                "pagerank":    {entity_id: float},
            }
        """
        try:
            if len(self.graph.nodes) == 0:
                return {"betweenness": {}, "degree": {}, "pagerank": {}}
            undirected = self.graph.to_undirected()
            betweenness = nx.betweenness_centrality(undirected, normalized=True)
            degree      = dict(self.graph.degree())
            try:
                pagerank = nx.pagerank(self.graph, max_iter=200, tol=1e-4)
            except Exception:
                pagerank = {}
            return {
                "betweenness": betweenness,
                "degree":      degree,
                "pagerank":    pagerank,
            }
        except Exception:
            return {"betweenness": {}, "degree": {}, "pagerank": {}}

    def get_clusters(self) -> list:
        """
        Detect communities using greedy modularity (NetworkX).

        Returns:
            list of {"cluster_id": int, "size": int, "members": [{"id", "label"}]}
        """
        try:
            if len(self.graph.nodes) < 2:
                return []
            undirected = self.graph.to_undirected()
            from networkx.algorithms.community import greedy_modularity_communities
            communities = list(greedy_modularity_communities(undirected))
            result = []
            for i, community in enumerate(communities):
                members = []
                for nid in community:
                    ent   = self._entities.get(nid)
                    label = getattr(ent, "name", nid) if ent else nid
                    members.append({"id": nid, "label": label})
                result.append({"cluster_id": i, "size": len(community), "members": members})
            return result
        except Exception:
            return []

    # ── Person merge ──────────────────────────────────────────────────────────

    def merge_persons(self, id_a: str, id_b: str) -> Optional[PersonEntity]:
        """
        Merge PersonEntity B into PersonEntity A (A absorbs B).

        Actions:
          - Adds SAME_PERSON_AS edge (A → B).
          - Transfers all B's graph edges to A.
          - Merges name_variants, phones, emails, locations, data_sources,
            usernames, platforms, tags.
          - Updates node attributes for A in the graph.
          - Returns the merged PersonEntity (A).

        Does not remove B from the graph — preserves the SAME_PERSON_AS link
        so the merge history is auditable.
        """
        entity_a = self._entities.get(id_a)
        entity_b = self._entities.get(id_b)
        if not entity_a or not entity_b:
            return None
        if not isinstance(entity_a, PersonEntity) or not isinstance(entity_b, PersonEntity):
            return None

        # Add SAME_PERSON_AS link
        self.add_relationship_idempotent(
            id_a, id_b, "SAME_PERSON_AS",
            strength=1.0, evidence=["Programmatic merge"],
            data_source="merge_operation",
        )

        # Merge collections
        def _merge_list(src, dst):
            for item in src:
                if item not in dst:
                    dst.append(item)

        if entity_b.name and entity_b.name not in entity_a.name_variants:
            entity_a.name_variants.append(entity_b.name)
        _merge_list(entity_b.name_variants, entity_a.name_variants)
        _merge_list(entity_b.phones, entity_a.phones)
        _merge_list(entity_b.emails, entity_a.emails)
        _merge_list(entity_b.locations, entity_a.locations)
        _merge_list(entity_b.data_sources, entity_a.data_sources)
        _merge_list(entity_b.tags, entity_a.tags)
        entity_a.usernames.update(entity_b.usernames)
        entity_a.platforms.update(entity_b.platforms)
        entity_a.risk_score  = max(entity_a.risk_score, entity_b.risk_score)
        entity_a.confidence  = max(entity_a.confidence, entity_b.confidence)
        entity_a.updated_at  = datetime.datetime.utcnow().isoformat()

        # Re-sync node attributes
        if id_a in self.graph:
            for k, v in entity_a.to_dict().items():
                self.graph.nodes[id_a][k] = v

        return entity_a

    # ── Import / Export ───────────────────────────────────────────────────────

    def export_graph_json(self) -> dict:
        """
        Return a JSON-serialisable dict representing the entire graph.
        Signature unchanged from Phase 0.
        """
        try:
            entities_out = {eid: e.to_dict() for eid, e in self._entities.items()}
            edges_out = [
                {"from": u, "to": v, "key": key, **data}
                for u, v, key, data in self.graph.edges(data=True, keys=True)
            ]
            return {
                "entities":   entities_out,
                "edges":      edges_out,
                "node_count": self.graph.number_of_nodes(),
                "edge_count": self.graph.number_of_edges(),
                "exported_at": datetime.datetime.utcnow().isoformat(),
            }
        except Exception:
            return {
                "entities": {}, "edges": [],
                "node_count": 0, "edge_count": 0,
                "exported_at": datetime.datetime.utcnow().isoformat(),
            }

    def import_graph_json(self, data: dict) -> None:
        """
        Reconstruct the graph from an export_graph_json payload.
        Handles all Phase 0 and Phase 0.5 entity types.
        Signature unchanged from Phase 0.
        """
        try:
            for eid, edata in data.get("entities", {}).items():
                try:
                    etype = edata.get("entity_type", "PERSON")
                    cls   = _ENTITY_MAP.get(etype, PersonEntity)
                    valid_fields = {f.name for f in dataclasses.fields(cls)}
                    filtered = {k: v for k, v in edata.items() if k in valid_fields}
                    entity_obj = cls(**filtered)
                    self.add_entity(entity_obj)
                except Exception:
                    continue

            for edge in data.get("edges", []):
                try:
                    self.add_relationship(
                        edge.get("from", ""),
                        edge.get("to", ""),
                        edge.get("type", "ASSOCIATED_WITH"),
                        edge.get("strength", 0.5),
                        edge.get("evidence", []),
                        edge.get("data_source", ""),
                    )
                except Exception:
                    continue
        except Exception:
            pass

    # ── Plotly Visualisation ──────────────────────────────────────────────────

    def to_plotly_figure(self, filter_type: Optional[str] = None, min_risk: float = 0.0):
        """
        Render the knowledge graph as a Plotly Figure.
        Nodes are colour-coded by entity_type; size reflects risk_score.
        Signature unchanged from Phase 0.
        """
        import plotly.graph_objects as go

        _TYPE_COLOURS = {
            "PERSON":            "#FF6B6B",
            "PHONE":             "#FFA07A",
            "EMAIL":             "#FFD700",
            "BANK_ACCOUNT":      "#98FB98",
            "DOCUMENT":          "#DDA0DD",
            "PLATFORM_ACCOUNT":  "#87CEEB",
            "LOCATION":          "#4ECDC4",
            "EVENT":             "#FFE66D",
            "NETWORK":           "#A8E6CF",
            "NETWORK_CLUSTER":   "#90EE90",
            "DEVICE":            "#C9B1FF",
            "VEHICLE":           "#F4A460",
            "ASSET":             "#FFB347",
            "IP_ADDRESS":        "#B0C4DE",
            "IMMIGRATION_FLAG":  "#FF6347",
            "BORDER_CROSSING":   "#DC143C",
            "IMMIGRATION_STATUS": "#FF8C00",
        }

        visible_ids = set()
        for eid, entity in self._entities.items():
            etype = getattr(entity, "entity_type", "PERSON")
            erisk = getattr(entity, "risk_score", 0.0)
            if filter_type and etype != filter_type:
                continue
            if erisk < min_risk:
                continue
            visible_ids.add(eid)

        subgraph = self.graph.subgraph(visible_ids) if visible_ids else self.graph
        if len(subgraph.nodes) == 0:
            fig = go.Figure()
            fig.update_layout(title="Knowledge Graph (empty)")
            return fig

        try:
            pos = nx.spring_layout(subgraph, seed=42)
        except Exception:
            pos = {n: (i, 0) for i, n in enumerate(subgraph.nodes)}

        edge_x, edge_y = [], []
        for u, v in subgraph.edges():
            x0, y0 = pos.get(u, (0, 0))
            x1, y1 = pos.get(v, (0, 0))
            edge_x += [x0, x1, None]
            edge_y += [y0, y1, None]

        edge_trace = go.Scatter(
            x=edge_x, y=edge_y,
            mode="lines",
            line=dict(width=0.8, color="#555"),
            hoverinfo="none",
            name="relationships",
        )

        node_traces = []
        grouped: dict = {}
        for nid in subgraph.nodes():
            entity = self._entities.get(nid)
            etype  = getattr(entity, "entity_type", "PERSON") if entity else "PERSON"
            grouped.setdefault(etype, []).append(nid)

        for etype, nids in grouped.items():
            nx_list, ny_list, labels, sizes = [], [], [], []
            for nid in nids:
                x, y  = pos.get(nid, (0, 0))
                nx_list.append(x)
                ny_list.append(y)
                entity = self._entities.get(nid)
                label  = getattr(entity, "name", nid) if entity else nid
                risk   = getattr(entity, "risk_score", 0.0) if entity else 0.0
                labels.append(f"{label}<br>Risk: {risk:.0f}")
                sizes.append(max(8, min(30, 8 + risk / 5)))

            node_traces.append(go.Scatter(
                x=nx_list, y=ny_list,
                mode="markers+text",
                marker=dict(
                    size=sizes,
                    color=_TYPE_COLOURS.get(etype, "#AAAAAA"),
                    line=dict(width=1, color="#222"),
                ),
                text=[
                    getattr(self._entities.get(n), "name", n)
                    if self._entities.get(n) else n
                    for n in nids
                ],
                textposition="top center",
                hovertext=labels,
                hoverinfo="text",
                name=etype,
            ))

        fig = go.Figure(data=[edge_trace] + node_traces)
        fig.update_layout(
            title="AetherLens Knowledge Graph",
            showlegend=True,
            hovermode="closest",
            margin=dict(b=20, l=5, r=5, t=40),
            xaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
            yaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
            paper_bgcolor="#0E1117",
            plot_bgcolor="#0E1117",
            font=dict(color="#FAFAFA"),
        )
        return fig

    # ── Persistence ───────────────────────────────────────────────────────────

    def save_to_db(self) -> bool:
        """
        Persist the serialised graph to SQLite table 'ontology_graph'.
        Signature unchanged from Phase 0.
        """
        try:
            db_path  = str(config.DATABASE_PATH)
            payload  = self.export_graph_json()
            json_str = json.dumps(payload, default=str)
            saved_at = datetime.datetime.utcnow().isoformat()

            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS ontology_graph (
                        id       TEXT PRIMARY KEY,
                        data     TEXT,
                        saved_at TEXT
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO ontology_graph (id, data, saved_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(id) DO UPDATE
                      SET data=excluded.data, saved_at=excluded.saved_at
                    """,
                    ("latest", json_str, saved_at),
                )
                conn.commit()
            return True
        except Exception:
            return False

    def load_from_db(self) -> bool:
        """
        Load the most recent graph snapshot from SQLite.
        Signature unchanged from Phase 0.
        """
        try:
            db_path = str(config.DATABASE_PATH)
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS ontology_graph (
                        id       TEXT PRIMARY KEY,
                        data     TEXT,
                        saved_at TEXT
                    )
                    """
                )
                cursor = conn.execute(
                    "SELECT data FROM ontology_graph ORDER BY saved_at DESC LIMIT 1"
                )
                row = cursor.fetchone()

            if row is None:
                return False

            self.import_graph_json(json.loads(row[0]))
            return True
        except Exception:
            return False


# ── Digital Twin Builder ──────────────────────────────────────────────────────

def build_digital_twin(all_data_sources: dict, raw_documents: list = None) -> OntologyGraph:
    """
    Build a complete OntologyGraph from all ingested data sources.

    Phase 0.5 enhancements (backward compatible):
      - Creates first-class PhoneEntity / EmailEntity nodes and HAS_PHONE /
        HAS_EMAIL edges from person.phones_found / emails_found.
      - Creates PlatformAccountEntity nodes for each confirmed platform.
      - Creates DocumentEntity nodes from raw_documents.
      - Runs the inference engine at the end to derive shared-identifier
        links and risk-propagation relationships.
      - All Phase 0 entity creation (Location, Event, Network, Asset) is
        preserved exactly.

    Args:
        all_data_sources: dict with keys:
            "person"          — resolved Person Object dict
            "search_results"  — raw search result dict
            "timeline_data"   — timeline dict with "events" list
            "behavioral_data" — behavioral assessment dict
        raw_documents: list of ingest_file() result dicts

    Returns:
        OntologyGraph — fully populated, inference applied.
    """
    graph = OntologyGraph()

    # ── Asset type hints (unchanged from Phase 0) ─────────────────────────────
    _ASSET_TYPE_HINTS = {
        "vehicle":   ["vehicle", "car", "truck", "bike", "registration", "reg_no",
                      "chassis", "engine_no", "make", "model"],
        "property":  ["property", "plot", "flat", "house", "address", "land", "survey"],
        "financial": ["account", "bank", "ifsc", "pan", "credit", "debit", "wallet"],
        "equipment": ["equipment", "device", "laptop", "phone", "imei", "serial"],
        "personnel": ["employee", "personnel", "staff", "designation", "department"],
    }

    try:
        person = all_data_sources.get("person", {}) or {}
        tl     = all_data_sources.get("timeline_data", {}) or {}
        behav  = all_data_sources.get("behavioral_data", {}) or {}

        # ── Primary PersonEntity ──────────────────────────────────────────────
        _raw_name   = person.get("confirmed_name", "Unknown") or "Unknown"
        _clean_name = _raw_name.replace("\n", " ").replace("\r", " ").strip() or "Unknown"

        pe = PersonEntity(
            name           = _clean_name,
            name_variants  = person.get("name_variants", []),
            usernames      = person.get("usernames", {}),
            emails         = person.get("emails_found", []),
            phones         = person.get("phones_found", []),
            locations      = person.get("location_stated", []),
            platforms      = person.get("profile_urls", {}),
            accounts       = list(person.get("join_dates", {}).keys()),
            confidence     = float(person.get("confidence_score", 0)),
            data_sources   = person.get("data_sources", []),
            classification = "RESTRICTED",
        )

        extra = {
            "behavioral_flags": (
                (behav or {}).get("assessment", {}).get("behavioral_flags", [])
            ),
        }
        risk_result  = calculate_risk_score(pe, graph, extra)
        pe.risk_score = risk_result["risk_score"]
        graph.add_entity(pe)

        # ── PhoneEntity nodes (Phase 0.5) ─────────────────────────────────────
        _phone_id_map: dict = {}   # raw number → PhoneEntity.id (for dedup)
        for raw_phone in person.get("phones_found", []):
            try:
                norm = normalize_phone(raw_phone)
                dedup_key = norm or raw_phone
                if dedup_key in _phone_id_map:
                    phone_eid = _phone_id_map[dedup_key]
                else:
                    ph_ent = PhoneEntity.from_raw(raw_phone, confidence=0.8,
                                                  data_sources=pe.data_sources[:])
                    ph_ent.owners_linked.append(pe.id)
                    graph.add_entity(ph_ent)
                    _phone_id_map[dedup_key] = ph_ent.id
                    phone_eid = ph_ent.id
                graph.add_relationship_idempotent(
                    pe.id, phone_eid, "HAS_PHONE",
                    strength=0.9,
                    evidence=[f"Phone extracted from person profile"],
                    data_source="entity_resolution",
                )
            except Exception:
                continue

        # ── EmailEntity nodes (Phase 0.5) ─────────────────────────────────────
        _email_id_map: dict = {}
        for raw_email in person.get("emails_found", []):
            try:
                norm = normalize_email(raw_email)
                dedup_key = norm or raw_email.lower().strip()
                if dedup_key in _email_id_map:
                    email_eid = _email_id_map[dedup_key]
                else:
                    em_ent = EmailEntity.from_raw(raw_email, confidence=0.8,
                                                  data_sources=pe.data_sources[:])
                    em_ent.owners_linked.append(pe.id)
                    graph.add_entity(em_ent)
                    _email_id_map[dedup_key] = em_ent.id
                    email_eid = em_ent.id
                graph.add_relationship_idempotent(
                    pe.id, email_eid, "HAS_EMAIL",
                    strength=0.9,
                    evidence=["Email extracted from person profile"],
                    data_source="entity_resolution",
                )
            except Exception:
                continue

        # ── PlatformAccountEntity nodes (Phase 0.5) ───────────────────────────
        # Handles that are noise / spam tokens are skipped entirely.
        _HANDLE_NOISE = {
            "spam", "reels", "offers", "alerts", "newsletter", "promo",
            "marketing", "notification", "otp", "fraud", "credit", "loan",
            "insurance", "winner", "cashback", "reward", "prize", "discount",
            "voucher", "delivery", "apply", "emi", "bill",
        }
        profile_urls = person.get("profile_urls", {})
        usernames    = person.get("usernames", {})
        for platform, url in profile_urls.items():
            try:
                handle = usernames.get(platform, "")
                handle_lc = str(handle).lstrip("@").lower()
                if handle_lc in _HANDLE_NOISE or any(tok in handle_lc for tok in _HANDLE_NOISE):
                    continue
                pa_ent = PlatformAccountEntity(
                    platform       = str(platform),
                    handle         = str(handle),
                    url            = str(url),
                    owners_linked  = [pe.id],
                    confidence     = 0.75,
                    data_sources   = pe.data_sources[:],
                )
                graph.add_entity(pa_ent)
                graph.add_relationship_idempotent(
                    pe.id, pa_ent.id, "HAS_PLATFORM_ACCOUNT",
                    strength=0.8,
                    evidence=[f"Confirmed {platform} account"],
                    data_source="entity_resolution",
                )
            except Exception:
                continue

        # ── DocumentEntity nodes (Phase 0.5) ──────────────────────────────────
        for doc in (raw_documents or []):
            try:
                fname = doc.get("filename", doc.get("name", ""))
                if not fname:
                    continue
                # Derive case prefix from filename
                fname_lower = fname.lower()
                if fname_lower.startswith("ghostwire"):
                    case_prefix = "GHOSTWIRE"
                elif fname_lower.startswith("jup"):
                    case_prefix = "JUPITER"
                else:
                    case_prefix = fname.split("_")[0].upper()[:12]

                doc_ent = DocumentEntity(
                    filename        = fname,
                    file_type       = fname.rsplit(".", 1)[-1].lower() if "." in fname else "",
                    case_prefix     = case_prefix,
                    row_count       = len(doc.get("structured_rows", [])),
                    subjects_linked = [pe.id],
                    confidence      = 0.9,
                    data_sources    = [fname],
                )
                graph.add_entity(doc_ent)
                graph.add_relationship_idempotent(
                    pe.id, doc_ent.id, "HAS_DOCUMENT",
                    strength=1.0,
                    evidence=[f"Document uploaded for this subject"],
                    data_source=fname,
                )
            except Exception:
                continue

        # ── LocationEntity nodes (unchanged from Phase 0) ─────────────────────
        for loc in person.get("location_stated", []):
            try:
                le = LocationEntity(
                    name           = str(loc),
                    type           = "region",
                    confidence     = 0.6,
                    persons_linked = [pe.id],
                )
                graph.add_entity(le)
                graph.add_relationship(
                    pe.id, le.id, "LOCATED_AT",
                    strength=0.6,
                    evidence=[f"Stated location: {loc}"],
                    data_source="profile_data",
                )
            except Exception:
                continue

        # ── EventEntity nodes (unchanged from Phase 0) ────────────────────────
        for ev in (tl or {}).get("events", [])[:20]:
            try:
                ee = EventEntity(
                    type               = "activity",
                    timestamp          = ev.get("normalized", ""),
                    description        = str(ev.get("context", ""))[:200],
                    persons_involved   = [pe.id],
                    platforms_involved = [ev.get("source", "")],
                    significance       = 0.5,
                    data_sources       = [ev.get("source", "")],
                )
                graph.add_entity(ee)
                graph.add_relationship(
                    pe.id, ee.id, "PARTICIPATED_IN",
                    strength=0.7,
                    evidence=[str(ev.get("context", ""))[:100]],
                    data_source=ev.get("source", ""),
                )
            except Exception:
                continue

        # ── NetworkEntity (unchanged from Phase 0) ────────────────────────────
        linked = person.get("confirmed_linked_profiles", [])
        if linked:
            try:
                ne = NetworkEntity(
                    type        = "social",
                    members     = [pe.id] + [c.get("url", "") for c in linked],
                    connections = [c.get("url", "") for c in linked],
                    strength    = 0.9,
                    cluster_id  = f"cluster_{pe.id[:8]}",
                    data_sources = ["cross_platform_discovery"],
                )
                graph.add_entity(ne)
                graph.add_relationship(
                    pe.id, ne.id, "MEMBER_OF",
                    strength=0.9,
                    evidence=["Confirmed linked accounts"],
                    data_source="cross_platform_discovery",
                )
            except Exception:
                pass

        # ── AssetEntity from raw_documents (unchanged from Phase 0) ───────────
        for doc in (raw_documents or []):
            doc_filename = doc.get("filename", doc.get("name", "document"))
            for row in doc.get("structured_rows", []):
                if not isinstance(row, dict):
                    continue
                row_lower = {k.lower(): v for k, v in row.items()}

                detected_type = ""
                for atype, hints in _ASSET_TYPE_HINTS.items():
                    if any(h in k for h in hints for k in row_lower):
                        detected_type = atype
                        break
                if not detected_type:
                    continue

                name_val  = (row_lower.get("name") or row_lower.get("vehicle_name")
                             or row_lower.get("asset_name") or row_lower.get("model", ""))
                ident_val = (row_lower.get("registration") or row_lower.get("reg_no")
                             or row_lower.get("identifier") or row_lower.get("serial_no")
                             or row_lower.get("account_no") or row_lower.get("imei", ""))
                desc_parts = [
                    f"{k}: {v}" for k, v in list(row.items())[:6]
                    if v and str(v) not in ("", "None", "nan")
                ]

                if not name_val and not ident_val:
                    continue

                try:
                    ae = AssetEntity(
                        name          = str(name_val or ident_val or detected_type.title()),
                        asset_type    = detected_type,
                        identifier    = str(ident_val),
                        description   = " | ".join(desc_parts)[:300],
                        owner_linked  = [pe.id],
                        confidence    = 0.7,
                        data_sources  = [doc_filename],
                        tags          = [detected_type],
                    )
                    graph.add_entity(ae)
                    graph.add_relationship(
                        pe.id, ae.id, "OWNS_ASSET",
                        strength=0.7,
                        evidence=[f"Asset found in document: {doc_filename}"],
                        data_source=doc_filename,
                    )
                except Exception:
                    continue

    except Exception as exc:
        print(f"[ONTOLOGY] build_digital_twin error: {exc}")

    # ── Run inference engine ──────────────────────────────────────────────────
    try:
        graph.run_inference()
    except Exception as exc:
        print(f"[ONTOLOGY] Inference failed (non-fatal): {exc}")

    return graph


# ══════════════════════════════════════════════════════════════════════════════
# TYPED CASE ONTOLOGY  (Phase 0.5 backbone)
# ══════════════════════════════════════════════════════════════════════════════
# A LIGHTWEIGHT, typed projection of the resolved case. build_ontology() below
# constructs it ONCE per report (report_generator._generate_report_inner keeps
# it as `_onto`), and it is the SINGLE source for:
#   * §09B pattern analysis  — pattern_engine.analyze_ontology(_onto) runs the
#     deterministic IF-THEN rules in pattern_rules.py over it;
#   * §05 Network Map / §08 Key Associations — named persons/organizations;
#   * §03 Platform Presence — PlatformAccount entities (platform-typed source
#     columns, per-file cited);
#   * §04 Location Data — Location entities (location-typed columns / labelled
#     fields, per-file cited).
#
# This is intentionally SEPARATE in purpose from the digital-twin Entities above
# (PersonEntity, PhoneEntity, …), which are heavyweight graph nodes carrying ids,
# confidence, and DB persistence, and which serve the app's digital-twin views
# (app.py build_digital_twin) and §16 risk scoring — NOT the report sections
# listed above. The typed layer is an in-memory, read-only *view*: each entity
# exposes exactly — and only — the attributes a consumer reads (pattern rules'
# contract is documented at the top of pattern_rules.py). They live in this one
# ontology module (no second ontology file); the names do not collide with the
# *Entity classes. Every field below earns its place by being read by a rule or
# a report section — nothing speculative.
#
# The graph relationships used by the rules (e.g. NETWORK_HUB, SHELL_LAYERING)
# reuse the EXISTING NetworkX graph from relationship_mapper — no parallel graph.

import re as _pa_re


@dataclass
class Person:
    """A human in the case. (rules: NETWORK_HUB via subject_name)"""
    name: str = ""
    role: str = ""
    is_subject: bool = False
    source: str = ""


@dataclass
class PhoneNumber:
    """A phone line. type ∈ domestic|international|burner.
    (rules: OPERATIONAL_SECURITY, OFFSHORE_FLIGHT_RISK)"""
    number: str = ""
    type: str = "domestic"
    country: str = ""
    source: str = ""


@dataclass
class Organization:
    """A company/entity. type ∈ shell|front|legitimate.
    (rules: LAYERING_STRUCTURE, SHELL_LAYERING_NETWORK)"""
    name: str = ""
    type: str = "legitimate"
    jurisdiction: str = ""
    offshore: bool = False
    source: str = ""


@dataclass
class Transaction:
    """A financial movement. direction ∈ in|out.
    (rules: LAYERING_STRUCTURE, OPERATIONAL_SCALE_MISMATCH)"""
    date: str = ""
    direction: str = "in"
    amount: float = 0.0
    cross_border: bool = False
    counterparty: str = ""
    structured: bool = False
    source: str = ""


@dataclass
class Property:
    """A real-asset holding. (rules: OFFSHORE_FLIGHT_RISK)"""
    jurisdiction: str = ""
    type: str = ""
    foreign: bool = False
    source: str = ""


@dataclass
class CommChannel:
    """A communications channel. type ∈ protonmail|telegram|signal|vpn|email|…
    (rules: OPERATIONAL_SECURITY, COUNTER_SURVEILLANCE)"""
    type: str = ""
    encrypted: bool = False
    foreign_exit: bool = False
    source: str = ""


@dataclass
class Location:
    """A place tied to the case by location-typed evidence: a location-named
    column on a source row, or a location-labelled line in document text.
    Unlabelled lines (headers, letterheads, classification stamps) carry no
    label and can never enter. Consumed by §04 and, since Phase 1, by the
    BORDER_MOVEMENT_CLUSTER immigration rule."""
    name: str = ""
    kind: str = "stated"          # stated | tower | address — from the evidence
    source: str = ""              # the actual source file(s)


@dataclass
class PlatformAccount:
    """An online platform account attested by a platform-named column on a
    source row. The handle is kept ONLY when a username-bearing column supplied
    it and it passes the schema-label test — a column label ("subscriber",
    "encryption") is never a handle. Consumed by §03 — no pattern rule reads it."""
    platform: str = ""
    handle: str = ""
    url: str = ""
    source: str = ""              # the actual source file(s)


@dataclass
class LegalProceeding:
    """A legal/enforcement event. kind ∈ loc|enforcement|inquiry|notice.
    (rules: OFFSHORE_FLIGHT_RISK, ENFORCEMENT_HISTORY_ESCALATION,
    ANTI_FORENSIC_BEHAVIOUR)"""
    agency: str = ""
    status: str = ""
    date: str = ""
    case_ref: str = ""
    kind: str = ""
    source: str = ""


@dataclass
class DeletionEvent:
    """An evidence-deletion event. (rules: ANTI_FORENSIC_BEHAVIOUR)"""
    timestamp: str = ""
    target: str = ""
    source: str = ""


@dataclass
class TimelineEvent:
    """A dated event with significance. (rules: TIMELINE_CLUSTER)"""
    date: str = ""
    significance: str = "LOW"
    source: str = ""
    description: str = ""


@dataclass
class Ontology:
    """Typed, structured view of one case that the pattern rules run over.
    The single output of build_ontology(); the rules read only from here."""
    subject_name: str = ""
    subject: Optional[Person] = None
    flags: list = field(default_factory=list)            # list[str]
    graph: Any = None                                    # existing NetworkX graph
    persons: list = field(default_factory=list)
    phones: list = field(default_factory=list)
    organizations: list = field(default_factory=list)
    transactions: list = field(default_factory=list)
    properties: list = field(default_factory=list)
    comm_channels: list = field(default_factory=list)
    legal_proceedings: list = field(default_factory=list)
    deletion_events: list = field(default_factory=list)
    timeline_events: list = field(default_factory=list)
    locations: list = field(default_factory=list)           # §04 (no rule reads)
    platform_accounts: list = field(default_factory=list)   # §03 (no rule reads)

    def counts(self) -> dict:
        """Population summary — handy for tests and the report header."""
        return {
            "persons": len(self.persons), "phones": len(self.phones),
            "organizations": len(self.organizations), "transactions": len(self.transactions),
            "properties": len(self.properties), "comm_channels": len(self.comm_channels),
            "legal_proceedings": len(self.legal_proceedings),
            "deletion_events": len(self.deletion_events),
            "timeline_events": len(self.timeline_events), "flags": len(self.flags),
            "locations": len(self.locations),
            "platform_accounts": len(self.platform_accounts),
        }


# ── builder helpers (defensive: tolerate dicts OR objects, missing keys) ──────
def _pa_get(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _pa_norm(s) -> str:
    return str(s or "").strip().lower()


def _pa_listify(x) -> list:
    if x is None:
        return []
    if isinstance(x, dict):
        return [x]
    if isinstance(x, (list, tuple, set)):
        return list(x)
    return [x]


def _pa_flatten_flags(*sources) -> list:
    """Normalise assorted flag containers (str / {'flag':..} / objects) → list[str]."""
    out = []
    for src in sources:
        for f in _pa_listify(src):
            if isinstance(f, dict):
                txt = f.get("flag") or f.get("text") or f.get("description") or ""
            else:
                txt = str(f)
            txt = str(txt).strip()
            if txt:
                out.append(txt)
    # de-dup preserving order (deterministic)
    seen, deduped = set(), []
    for t in out:
        k = t.lower()
        if k not in seen:
            seen.add(k)
            deduped.append(t)
    return deduped


_PA_OFFSHORE = ("uae", "dubai", "singapore", "switzerland", "swiss", "cayman", "bvi",
                "british virgin", "mauritius", "panama", "cyprus", "hong kong", "hongkong",
                "seychelles", "malta", "luxembourg", "offshore", "foreign")
_PA_DOMESTIC = ("india", "indian", " in ", "domestic")
_PA_DATE_RE = _pa_re.compile(r"\b(\d{4}-\d{1,2}-\d{1,2})\b")

# ── GENERALITY VOCABULARIES ───────────────────────────────────────────────────
# Every mapping decision below matches against one of these SETS, never a single
# literal. To support a new case's wording, add ONE alias to the relevant set —
# no logic change. All matching is case- and punctuation-insensitive (compare the
# _pa_token() of both sides), so "Real Estate", "real-estate", "REAL_ESTATE" are
# equivalent. These describe GENERIC shapes — no case/operation/subject names.

# Graph-node `type` aliases → which typed bucket a flat node belongs to (HOP 1).
_PA_NODETYPE_ORG = {"organization", "organisation", "org", "company", "entity",
                    "business", "corp", "corporation", "firm", "ngo", "trust",
                    "llp", "fze", "pte", "shell", "front", "vendor", "employer"}
_PA_NODETYPE_PERSON = {"person", "individual", "subject", "name", "people",
                       "human", "associate", "contact", "alias"}
_PA_NODETYPE_PROPERTY = {"property", "asset", "realestate", "holding", "land",
                         "estate", "apartment", "villa", "plot"}

# Org name/notes tokens that signal a shell/front entity (HOP 1/2). Includes
# corporate-form abbreviations common to offshore shells and money-mixing terms.
_PA_SHELL_TOKENS = {"shell", "front", "paper company", "fze", "pte", "holdings",
                    "ventures", "llp", "llc", "ltd", "offshore", "mixer",
                    "tumbler", "obfuscation", "nominee", "trading", "general trading"}

# ── HOP 2 — transaction column + value vocabularies ───────────────────────────
# COLUMN-NAME aliases: which raw CSV column supplies each Transaction field. A
# column matches if its normalised name equals an alias OR contains one (len>=4).
# Extend by adding one alias token to the relevant set.
_PA_COL_AMOUNT = {"amount", "amountinr", "amountinrequiv", "amountusd", "amounteur",
                  "value", "sum", "sumpaid", "total", "totalamount", "transactionamount",
                  "txnamount", "paid", "amountpaid", "debitamount", "creditamount"}
_PA_COL_TYPE = {"transactiontype", "txntype", "type", "flow", "txtype", "entrytype",
                "movement", "drcr", "category", "nature"}
_PA_COL_DIRECTION = {"direction", "drcr", "debitcredit", "inout"}
_PA_COL_COUNTERPARTY = {"counterparty", "beneficiary", "recipient", "payee",
                        "contactname", "remitter", "sender", "party", "destination",
                        "towhom", "paidto", "receivedfrom"}
_PA_COL_NOTES = {"notes", "note", "remark", "remarks", "description", "details",
                 "narration", "purpose", "memo", "comment"}
_PA_COL_DATE = {"date", "txndate", "transactiondate", "valuedate", "entrydate",
                "timestamp", "postingdate", "datetime", "normalized", "eventdate"}
# Financial-CONTEXT column vocabulary — a deposit/investment ledger is
# unambiguously financial even when a row carries no explicit transaction-type or
# direction column (its COLUMN NAMES are the signal: investor_ref, promised_return,
# ledger_code, principal, redemption…). Used by the strict `records` gate so such a
# ledger deposit is admitted as an inbound transaction, while call / movement /
# receipt rows (no such columns) stay skipped. Extend by adding one column-name token.
_PA_COL_FINCTX = {"investor", "investorref", "depositor", "deposit", "ledger",
                  "ledgercode", "principal", "maturity", "redemption", "promisedreturn",
                  "returnpct", "scheme", "unitholder", "subscriber", "contribution",
                  "premium", "instalment", "installment", "folio", "portfolio"}
# Party-side column vocabulary — the columns that name WHO transacts on the
# subject side of a money row (the named subject, the account/wallet holder —
# a front entity or the subject personally). The money-routing graph enrichment
# links each such named party to the organization the row's money moved through.
# Extend by adding one column-name token.
_PA_COL_PARTY = {"subjectname", "accountholder", "walletholder", "acctholder",
                 "accountname", "holder", "originator", "orderedby"}

# VALUE vocabularies: classify a field's CONTENT by keyword, never by equality.
# Short tokens (<=3 chars, e.g. "in"/"out") match only as whole words.
_PA_DIR_OUT = {"out", "wire", "transfer", "remit", "remittance", "withdrawal",
               "payout", "offshore", "sent", "debit", "payment", "mixer", "split",
               "disburse", "outflow", "spend", "purchase", "wireout", "paidout"}
_PA_DIR_IN = {"in", "inflow", "deposit", "receipt", "credit", "collection",
              "received", "incoming", "fee", "ransom", "investor", "subscription",
              "inward", "topup", "funding", "contribution"}
# Foreign / cross-border indicators (reuses the offshore set, adds reach signals).
_PA_FOREIGN_TOKENS = set(_PA_OFFSHORE) | {"overseas", "abroad", "gulf", "+971",
                       "+65", "+44", "+880", "+966", "+974", "qatar", "saudi",
                       "oman", "bahrain", "kuwait", "doha", "abu dhabi", "sharjah",
                       "international", "cross-border", "crossborder"}
# Structuring / smurfing indicators (sub-threshold splitting of deposits).
_PA_STRUCTURING_TOKENS = {"split", "structur", "layering", "smurf", "below threshold",
                          "sub-threshold", "subthreshold", "tranche", "instalment",
                          "installment", "partial", "broken", "multiple deposit",
                          "broken up", "just under"}

# ── HOP 3 — dated events from source rows + encrypted-app recognition ──────────
# Secure / encrypted messaging + anonymisation apps. Any match → an ENCRYPTED
# CommChannel. Extend with one app name as new tools appear.
_PA_ENCRYPTED_APPS = {"protonmail", "proton", "tutanota", "telegram", "signal",
                      "session", "threema", "wickr", "briar", "silentcircle",
                      "silent circle", "pgp", "gpg", "element", "olvid", "encrypted"}
# Canonical names so "proton"/"protonmail" don't count as two platforms.
_PA_APP_CANON = {"proton": "protonmail", "silent circle": "silentcircle"}
# VPN / anonymisation indicators (in a value), plus column names that denote a
# VPN/foreign exit when populated (exit_node, vpn_exit, …).
_PA_VPN_TOKENS = {"vpn", "tor", "tor browser", "onion", "proxy", "exit node",
                  "anonymis", "anonymiz", "nordvpn", "expressvpn", "protonvpn",
                  "mullvad"}
_PA_COL_EXITNODE = {"exitnode", "vpnexit", "exitcountry", "exitlocation"}
# Evidence-destruction indicators.
_PA_DELETION_TOKENS = {"deletion", "deleted", "delete", "wiped", "wipe", "formatted",
                       "format", "purged", "purge", "anti-forensic", "anti forensic",
                       "antiforensic", "destroyed", "erased", "scrubbed",
                       "visibility_change", "logs purged", "account_delete"}
# Legal-event indicators → kind. Specific kinds (loc/notice/inquiry) before the
# generic enforcement-agency scan so a notice isn't mislabelled enforcement.
_PA_LOC_TOKENS = {"lookout", "look out circular", "look-out", "loc issued"}
_PA_NOTICE_TOKENS = {"notice", "summons", "show cause", "show-cause", "production order"}
_PA_INQUIRY_TOKENS = {"inquiry", "enquiry", "cert-in", "certin", "probe", "fir",
                      "ecir", "case registered", "investigation opened"}
_PA_ENFORCEMENT_AGENCIES = {"dri", "ncb", "ed", "sfio", "cbi", "sebi", "eow",
                            "income tax", "customs", "enforcement directorate",
                            "serious fraud", "cyber cell"}
# Broader enforcement-ACTION indicators (agency names + case/charge markers). Any
# match → a kind="enforcement" proceeding (the rule counts these toward escalation).
_PA_ENFORCEMENT_INDICATORS = _PA_ENFORCEMENT_AGENCIES | {
    "ahtu", "fir", "ecir", "charge sheet", "chargesheet", "prosecution",
    "passport act", "pmla", "fema", "ndps", "it act", "case registered",
    "case ref", "raid", "seizure", "seized", "arrest", "complaint registered"}
# Person-name-ish columns used to build the relationship graph from records.
_PA_COL_CONTACT = {"contactname", "contact", "peer", "associate", "counterpart",
                   "party", "peername", "alias", "handler", "supplier"}

# Location-typed evidence vocabulary — §04 admits ONLY values whose column name
# (or text-line label) contains one of these WORDS (word-level match: the header
# is split on separators, so "tower_location" matches via "location" while
# "allocation" does not). Extend with one word to support a new header.
_PA_COL_LOCATION = {"location", "address", "city", "residence", "residency",
                    "home", "hometown", "place", "district", "region", "town",
                    "village", "locality", "state"}

# Generic data-schema label words. A value whose words ALL come from this set is
# a column label / record descriptor leaking through as data ("subscriber",
# "encryption", "platform_name") — never a real handle, platform, or place.
# Structural test, not a blocklist of observed artifacts: any label-shaped
# value is rejected, including combinations ("user id", "account status").
_PA_SCHEMA_LABEL_TOKENS = {
    "platform", "username", "handle", "user", "userid", "subscriber",
    "subscription", "encryption", "encrypted", "account", "status", "action",
    "device", "metadata", "record", "records", "data", "notes",
    "note", "name", "type", "id", "code", "ref", "line", "column", "field",
    "value", "unknown", "anonymous", "confirmed", "pending", "verified",
    "date", "time", "source", "restricted", "classified", "confidential",
    "profile", "exit", "node", "log", "entry"}


def _pa_is_schema_label(value) -> bool:
    """True when EVERY word of `value` is a generic schema/label word — the
    structural test that keeps column labels ('subscriber', 'platform_name')
    from surfacing as usernames, platforms, or locations."""
    words = [w for w in _pa_re.split(r"[^a-z0-9]+", _pa_norm(value)) if w]
    return bool(words) and all(w in _PA_SCHEMA_LABEL_TOKENS for w in words)
_PA_YEAR_RE = _pa_re.compile(r"\b(19|20)\d{2}\b")


def _pa_year_date(text) -> str:
    """Synthesise a YYYY-01-01 date from the first 4-digit year in text (e.g. a
    year embedded in an enforcement reference 'SEBI/WTM/2019/2207'). Returns ''
    when no year is present — never invents one."""
    m = _PA_YEAR_RE.search(str(text or ""))
    return f"{m.group(0)}-01-01" if m else ""


# High-significance event vocabulary (drives TimelineEvent.significance when the
# source provides none). Generic operational/enforcement/financial signals.
_PA_HIGH_SIG_TOKENS = {"delet", "wiped", "wipe", "purge", "anti-forensic", "wire",
                       "transfer", "offshore", "cross-border", "crossborder", "ransom",
                       "extortion", "payout", "mixer", "layering", "enforcement",
                       "raid", "seizure", "seized", "arrest", "summons", "notice",
                       "lookout", "fir", "ecir", "transit", "smuggl", "forged",
                       "forgery", "trafficking", "deployed", "exfil", "breach",
                       "scrape", "unauthor", "cert-in", "certin", "inflow"}


def _pa_canon_app(app) -> str:
    a = _pa_norm(app)
    return _PA_APP_CANON.get(a, a)


def _pa_high_significance(text) -> bool:
    """True if event text names a materially significant act. Used only to fill a
    missing significance — never overrides a significance the source already gave."""
    return _pa_any_token(text, _PA_HIGH_SIG_TOKENS)


def _pa_events_from_pairs(pairs) -> tuple:
    """HOP 3 — derive DATED, typed deletion / legal / comm events from (text, date)
    pairs sourced from structured ROWS *and* timeline entries (raw-text lines keep
    their date + context). The date travels WITH the semantic, so it is never lost
    to a flattened, dateless flag label. Fully general — each pair is classified by
    keyword over its text against the vocabularies above; a pair with no matching
    signal contributes nothing (never a fabricated date/event)."""
    dels, legals, chans, seen_chan = [], [], [], set()
    for blob, rdate, exit_val in pairs:
        if not blob:
            continue
        if _pa_any_token(blob, _PA_DELETION_TOKENS):
            dels.append(DeletionEvent(timestamp=rdate, target="", source="record"))
        if _pa_any_token(blob, _PA_LOC_TOKENS):
            legals.append(LegalProceeding(status="active", date=rdate, kind="loc", source="record"))
        if _pa_any_token(blob, _PA_NOTICE_TOKENS):
            legals.append(LegalProceeding(date=rdate, kind="notice", source="record"))
        if _pa_any_token(blob, _PA_INQUIRY_TOKENS):
            legals.append(LegalProceeding(date=rdate, kind="inquiry", source="record"))
        for ind in _PA_ENFORCEMENT_INDICATORS:
            if _pa_any_token(blob, (ind,)):
                agency = ind.upper() if ind in _PA_ENFORCEMENT_AGENCIES else ""
                # year embedded in a reference (e.g. 'PPT/DEL/2017/0094') when the
                # line carries no full date — escalation counts distinct YEARS.
                legals.append(LegalProceeding(agency=agency, date=rdate or _pa_year_date(blob),
                                              kind="enforcement", source="record"))
                break
        for app in _PA_ENCRYPTED_APPS:
            if _pa_any_token(blob, (app,)):
                canon = _pa_canon_app(app)
                if canon not in seen_chan:
                    seen_chan.add(canon)
                    chans.append(CommChannel(type=canon, encrypted=True, source="record"))
        if (_pa_any_token(blob, _PA_VPN_TOKENS) or str(exit_val or "").strip()) and "vpn" not in seen_chan:
            seen_chan.add("vpn")
            foreign = _pa_is_foreign(exit_val) or _pa_any_token(blob, _PA_FOREIGN_TOKENS)
            chans.append(CommChannel(type="vpn", encrypted=False,
                                     foreign_exit=bool(foreign), source="record"))
    return dels, legals, chans


# Tokens that mark a string as an org/role/description, never a personal name.
_PA_NON_PERSON_TOKENS = _PA_SHELL_TOKENS | {
    "desk", "wallet", "collection", "redemption", "conversion", "payment", "fee",
    "account", "exchange", "deposit", "transfer", "service", "cash", "recruitment",
    "solutions", "capital", "advisory", "manpower", "pooled", "investor", "victim",
    "withdrawal", "counter", "agency", "landline", "office", "bank", "company"}


def _pa_personish(name) -> bool:
    """A general, structural 'looks like a real personal name' test: 2-4 words, each
    Capitalised and letters-only, and free of org/role/description tokens. No name
    lists — purely structural; used only to seed relationship-graph nodes."""
    n = str(name or "").strip()
    words = n.split()
    if not (4 <= len(n) <= 50) or not (2 <= len(words) <= 4):
        return False
    if not all(w[:1].isupper() and _pa_re.match(r"^[A-Za-z.''\-]+$", w) for w in words):
        return False
    return not _pa_any_token(n, _PA_NON_PERSON_TOKENS)


def _pa_graph_from_records(subject, records, transactions):
    """Build a relationship graph from structured source rows when no usable graph
    was supplied upstream. Edges are REAL and subject-centric only: subject↔each
    communication contact (a person-name column) and subject↔each person who is a
    transaction counterparty. No inferred associate-to-associate edges are invented
    (no fabrication). Returns an undirected nx.Graph (empty if no subject)."""
    G = nx.Graph()
    s = str(subject or "").strip()
    if not s:
        return G
    G.add_node(s, type="person")
    for row in records:
        if not isinstance(row, dict):
            continue
        for col, val in row.items():
            if _pa_token(col) in _PA_COL_CONTACT:
                nm = str(val or "").strip()
                if _pa_personish(nm) and _pa_norm(nm) != _pa_norm(s):
                    G.add_node(nm, type="person")
                    G.add_edge(s, nm)
    for t in (transactions or []):
        cp = (getattr(t, "counterparty", "") or "").strip()
        if not cp:
            continue
        # a person named inside a counterparty label, e.g. "Mule wallet (Farida Sheikh)"
        m = _pa_re.search(r"\(([^)]+)\)", cp)
        nm = (m.group(1) if m else cp).strip()
        if _pa_personish(nm) and _pa_norm(nm) != _pa_norm(s):
            G.add_node(nm, type="person")
            G.add_edge(s, nm)
    return G


def _pa_token(s) -> str:
    """Normalise to a comparable token: lower-case, strip all non-alphanumerics.
    Makes column/value/type matching case- and punctuation-insensitive."""
    return _pa_re.sub(r"[^a-z0-9]", "", _pa_norm(s))


def _pa_any_token(text, vocab) -> bool:
    """True if any vocab phrase appears in `text` (both normalised, space-kept).
    Substring/keyword match over a known vocabulary — not equality to a literal.
    Short tokens (<=3 chars) match only as whole words to avoid spurious hits."""
    t = " " + _pa_norm(text) + " "
    if not t.strip():
        return False
    words = {w for w in _pa_re.split(r"[^a-z0-9+]+", t) if w}
    for v in vocab:
        vn = _pa_norm(v)
        if not vn:
            continue
        if len(vn) <= 3:
            if vn in words:
                return True
        elif vn in t:
            return True
    return False


def _pa_pick(row, col_vocab):
    """HOP 2 — return the value of the first column whose normalised name matches
    `col_vocab` (exact alias, or contains an alias of length>=4). General column
    resolution: a new header is supported by adding one alias, not by branching."""
    if not isinstance(row, dict):
        return None
    norm = {_pa_token(k): v for k, v in row.items()}
    for k, v in norm.items():            # exact-alias pass first (most precise)
        if k in col_vocab:
            return v
    for k, v in norm.items():            # then substring pass for compound headers
        if any(len(m) >= 4 and m in k for m in col_vocab):
            return v
    return None


def _pa_has_col(row, col_vocab) -> bool:
    """True if `row` has ANY column whose normalised name matches `col_vocab`
    (exact alias, or contains an alias of length>=4). Column-name presence test —
    used to recognise a financial/deposit ledger by its headers, not its values."""
    if not isinstance(row, dict):
        return False
    for k in row:
        kt = _pa_token(k)
        if kt in col_vocab or any(len(m) >= 4 and m in kt for m in col_vocab):
            return True
    return False


def _pa_classify_direction(*texts) -> str:
    """Classify money-flow direction from a transaction-type / notes value by
    keyword vote over the IN/OUT vocabularies. Returns 'in', 'out', or '' (undecided
    — the caller defaults undecided deposits to 'in', never fabricating an 'out')."""
    blob = " " + " ".join(_pa_norm(t) for t in texts) + " "
    words = {w for w in _pa_re.split(r"[^a-z0-9]+", blob) if w}

    def _score(vocab):
        n = 0
        for v in vocab:
            vn = _pa_norm(v)
            if len(vn) <= 3:
                if vn in words:
                    n += 1
            elif vn in blob:
                n += 1
        return n

    out_n, in_n = _score(_PA_DIR_OUT), _score(_PA_DIR_IN)
    if out_n > in_n:
        return "out"
    if in_n > out_n:
        return "in"
    return ""


def _pa_is_offshore(j) -> bool:
    j = _pa_norm(j)
    return bool(j) and any(t in j for t in _PA_OFFSHORE)


def _pa_is_foreign(j) -> bool:
    j = _pa_norm(j)
    return bool(j) and not any(t.strip() in j for t in _PA_DOMESTIC)


def _pa_classify_phone(number, tags) -> str:
    low = [_pa_norm(t) for t in _pa_listify(tags)]
    if any("burner" in t or "prepaid" in t for t in low):
        return "burner"
    digits = _pa_re.sub(r"[^\d+]", "", str(number or ""))
    if digits.startswith("+") and not digits.startswith("+91"):
        return "international"
    if digits.startswith("00") and not digits.startswith("0091"):
        return "international"
    return "domestic"


def _pa_first_date(text) -> str:
    m = _PA_DATE_RE.search(str(text or ""))
    return m.group(1) if m else ""


def _pa_build_transaction(t, strict=False):
    """Map ONE raw row → a Transaction via the shared HOP-2 column vocabularies.

    This is the single transaction mapper used for BOTH the dedicated
    `financial_data` bucket and the generic structured `records` (where
    document-panel financial CSVs actually land). Field resolution
    (amount / direction / counterparty / cross_border / structured) reuses the
    same synonym & keyword sets — no parallel mapper, no case-specific columns.

    `strict` (used for `records`, which may contain NON-financial rows — call
    logs, movement/ANPR, ledgers, receipts) accepts a row ONLY when it carries
    genuine transaction semantics: a positive money amount AND a resolvable
    money-flow direction (explicit direction column, or a transaction-type/notes
    value the direction classifier recognises). A row lacking either is returned
    as None and skipped, never fabricated into a bogus transaction. In permissive
    mode (the dedicated financial bucket) every row maps, preserving prior
    behaviour. Returns a Transaction, or None to skip.
    """
    type_val = _pa_get(t, "type") or _pa_pick(t, _PA_COL_TYPE) or ""
    notes_val = _pa_get(t, "notes") or _pa_pick(t, _PA_COL_NOTES) or ""
    cp_val = (_pa_get(t, "counterparty") or _pa_pick(t, _PA_COL_COUNTERPARTY) or "")

    # direction: an explicit direction value wins; else classify the type/notes.
    explicit_dir = _pa_norm(_pa_get(t, "direction") or _pa_pick(t, _PA_COL_DIRECTION))
    if explicit_dir in ("credit", "deposit", "inward", "in", "inflow", "received", "cr"):
        direction, dir_signal = "in", True
    elif explicit_dir in ("debit", "withdrawal", "outward", "out", "wire", "sent", "dr"):
        direction, dir_signal = "out", True
    else:
        # The transaction-TYPE value is authoritative for direction; fall back to
        # notes only when the type is blank/ambiguous, then default to inbound.
        classified = _pa_classify_direction(type_val) or _pa_classify_direction(notes_val)
        dir_signal = bool(classified)
        direction = classified or "in"

    # amount: explicit numeric key, else first amount-synonym column.
    raw_amt = _pa_get(t, "amount")
    if raw_amt in (None, "", 0, 0.0):
        raw_amt = _pa_pick(t, _PA_COL_AMOUNT)
    try:
        amount = float(str(raw_amt).replace(",", "")) if raw_amt not in (None, "") else 0.0
    except (TypeError, ValueError):
        amount = 0.0

    # STRICT — a `records` row becomes a transaction only with genuine financial
    # semantics: a positive money amount AND a money-flow signal. The signal is
    # either a resolved direction (explicit column / transaction-type / notes) OR a
    # financial-context ledger header (investor_ref, promised_return, ledger_code…),
    # in which case a bare deposit row defaults to inbound. Rows with neither —
    # call logs, movement/ANPR, plain receipts — are skipped, never fabricated.
    fin_ctx = _pa_has_col(t, _PA_COL_FINCTX)
    if strict and not (amount > 0 and (dir_signal or fin_ctx)):
        return None

    # cross_border / structured: explicit flag OR keyword over counterparty+notes+type.
    cross_border = bool(_pa_get(t, "cross_border", False)) or \
        _pa_any_token(f"{cp_val} {notes_val} {type_val}", _PA_FOREIGN_TOKENS)
    structured = bool(_pa_get(t, "structured", False)) or \
        _pa_any_token(f"{type_val} {notes_val}", _PA_STRUCTURING_TOKENS)

    return Transaction(
        date=str(_pa_get(t, "date") or _pa_pick(t, _PA_COL_DATE) or ""),
        direction=direction, amount=amount, cross_border=cross_border,
        counterparty=str(cp_val or ""), structured=structured,
        source=str(_pa_get(t, "source") or _pa_get(t, "source_file") or ""))


def _pa_channels_from_flags(flags) -> list:
    """Derive CommChannel entries from §09 flag text when not explicitly typed.
    Uses the shared encrypted-app vocabulary (_PA_ENCRYPTED_APPS) + VPN tokens, so
    any known secure app named in a flag yields an encrypted channel."""
    chans, seen = [], set()
    for f in flags:
        fl = _pa_norm(f)
        for app in _PA_ENCRYPTED_APPS:
            if _pa_any_token(fl, (app,)):
                canon = _pa_canon_app(app)
                if canon not in seen:
                    seen.add(canon)
                    chans.append(CommChannel(type=canon, encrypted=True, source="flag"))
        if _pa_any_token(fl, _PA_VPN_TOKENS) and "vpn" not in seen:
            seen.add("vpn")
            foreign_exit = _pa_any_token(fl, _PA_FOREIGN_TOKENS) or "exit" in fl
            chans.append(CommChannel(type="vpn", encrypted=False,
                                     foreign_exit=foreign_exit, source="flag"))
    return chans


def _pa_legal_from_flags(flags) -> list:
    """Derive LegalProceeding entries from flag text (LOC / enforcement / inquiry)."""
    out = []
    for f in flags:
        fl = _pa_norm(f)
        date = _pa_first_date(f)
        if "lookout" in fl or fl.endswith(" loc") or " loc " in fl or "look out circular" in fl:
            out.append(LegalProceeding(agency="", status="active", date=date,
                                       kind="loc", source="flag"))
        for ag in ("dri", "ncb", "ed", "sfio", "cbi"):
            if _pa_re.search(rf"\b{ag}\b", fl):
                out.append(LegalProceeding(agency=ag.upper(), status="", date=date,
                                           kind="enforcement", source="flag"))
        if any(k in fl for k in ("inquiry", "summons", "notice", "cert-in", "certin")):
            kind = "notice" if "notice" in fl else "inquiry"
            out.append(LegalProceeding(agency="", status="", date=date,
                                       kind=kind, source="flag"))
    return out


def _pa_deletions_from_flags(flags) -> list:
    out = []
    for f in flags:
        fl = _pa_norm(f)
        if any(k in fl for k in ("deletion", "deleted", "wiped", "formatted",
                                 "anti-forensic", "anti forensic", "data destroyed")):
            out.append(DeletionEvent(timestamp=_pa_first_date(f), target="", source="flag"))
    return out


def _pa_normalize_entities(entities) -> dict:
    """HOP 1 — accept EITHER shape and return the dict the rest of the builder reads.

    The live pipeline passes a FLAT LIST of graph nodes ([{id,label,type}, …]);
    the unit tests pass a DICT of typed lists ({organizations:[...], persons:[...],
    …}). A dict is returned unchanged (tests/other callers unaffected). A list is
    bucketed by a TYPE-SYNONYM set (case/punctuation-insensitive), never by literal
    equality, so any node-type wording sorts into the right typed object.

    Nodes whose type is unknown/location are skipped — never fabricated into a
    typed object. A node with no explicit/blank type is treated as a person, which
    matches relationship_mapper's default node type.
    """
    if isinstance(entities, dict):
        return entities
    nodes = _pa_listify(entities)
    if not nodes:
        return {}
    buckets = {"organizations": [], "persons": [], "properties": []}
    for nd in nodes:
        name = str(_pa_get(nd, "label", "") or _pa_get(nd, "name", "")
                   or _pa_get(nd, "id", "") or "").strip()
        if not name:
            continue
        tok = _pa_token(_pa_get(nd, "type", ""))
        tags = _pa_listify(_pa_get(nd, "tags"))
        src = str(_pa_get(nd, "source", "") or "graph")
        if tok in _PA_NODETYPE_ORG or any(k in tok for k in ("org", "compan", "shell", "front")):
            # Preserve an upstream shell/front type signal; else leave blank for the
            # org loop to classify from name/tags against _PA_SHELL_TOKENS.
            otype = tok if tok in ("shell", "front") else ""
            buckets["organizations"].append(
                {"name": name, "type": otype, "tags": tags, "source": src})
        elif tok in _PA_NODETYPE_PROPERTY or "propert" in tok or "asset" in tok:
            buckets["properties"].append({"jurisdiction": name, "source": src})
        elif tok in _PA_NODETYPE_PERSON or tok == "" or "person" in tok:
            buckets["persons"].append({"name": name, "source": src})
        # else: location / unknown / infrastructure node → intentionally skipped
    return buckets


def build_ontology(person, entities=None, flags=None, timeline=None,
                   graph=None, phones=None, financial_data=None, records=None,
                   texts=None, documents=None) -> Ontology:
    """THE consolidation point: raw entity-resolution output → typed Ontology.

    Defensive by design — every argument is optional and each field is parsed
    with safe fallbacks, so a partially-populated case never raises. The pattern
    rules then consume the returned Ontology, never the raw dicts.

    Expected (flexible) input shapes:
      person         : resolved Person Object dict — confirmed_name/name,
                       anomaly_flags, phones_found, role
      entities       : dict of typed lists (organizations, properties,
                       comm_channels, legal_proceedings, deletion_events, persons)
                       OR a flat list of {id,label,type} graph nodes (HOP 1)
      flags          : list[str | {'flag':..}] — §09 anomaly flags
      timeline       : {'events':[...]} or list of event dicts
      graph          : the EXISTING NetworkX graph (relationship_mapper)
      phones         : list[str | dict] of phone lines (else person.phones_found)
      financial_data : {'transactions':[...], 'properties':[...]} or list of txns
      records        : flat list of structured source rows (HOP 3) — the dated
                       origin for deletion/legal/comm events. When a date column
                       and the semantic share a row, the date is preserved; flag
                       text (which is dateless) is only a last-resort fallback.
      documents      : list of ingest-result dicts (filename + structured_rows +
                       raw text). The per-FILE origin for the typed §03 platform
                       accounts and §04 locations, so provenance (the actual
                       source file) travels with each value. Optional — rule
                       inputs above are unaffected when omitted.
    """
    entities = _pa_normalize_entities(entities)   # HOP 1: list-or-dict → dict
    person = person or {}
    # The graph rules (NETWORK_HUB betweenness / articulation, SHELL_LAYERING
    # neighbour count) reason about CONNECTION, not direction; connected_components
    # is undirected-only. Normalise a directed graph to an undirected view so those
    # rules work regardless of how the upstream graph was built. No rule change.
    if isinstance(graph, nx.DiGraph):
        graph = graph.to_undirected(as_view=False)
    onto = Ontology(graph=graph)

    # ── subject + persons ────────────────────────────────────────────────────
    subject_name = (_pa_get(person, "confirmed_name") or _pa_get(person, "name")
                    or _pa_get(person, "primary_subject") or "")
    onto.subject_name = str(subject_name).strip()
    subj = Person(name=onto.subject_name, role=str(_pa_get(person, "role", "") or ""),
                  is_subject=True, source="resolution")
    onto.subject = subj
    onto.persons.append(subj)
    for p in _pa_listify(_pa_get(entities, "persons")):
        nm = str(_pa_get(p, "name", "") or "").strip()
        if nm and nm != onto.subject_name:
            onto.persons.append(Person(name=nm, role=str(_pa_get(p, "role", "") or ""),
                                       is_subject=False, source=str(_pa_get(p, "source", "") or "")))

    # Contacts named on structured rows (call records' contact_name, handler,
    # supplier, …) become typed Persons — the humans §08 cites. Same contact-column
    # vocabulary and structural name test _pa_graph_from_records uses; dedup by
    # token; the supplied graph is NOT modified (graph rules see no new nodes).
    _seen_person = {_pa_token(p.name) for p in onto.persons}
    for row in _pa_listify(records):
        if not isinstance(row, dict):
            continue
        for col, val in row.items():
            if _pa_token(col) not in _PA_COL_CONTACT:
                continue
            nm = str(val or "").strip()
            if _pa_personish(nm) and _pa_token(nm) not in _seen_person:
                onto.persons.append(Person(name=nm, is_subject=False, source="record contact"))
                _seen_person.add(_pa_token(nm))

    # ── flags (arg + person.anomaly_flags + behavioral_flags) ────────────────
    onto.flags = _pa_flatten_flags(flags, _pa_get(person, "anomaly_flags"),
                                   _pa_get(person, "behavioral_flags"),
                                   _pa_get(person, "conflicts"))

    # ── phones (arg, else person.phones_found) ───────────────────────────────
    raw_phones = _pa_listify(phones) or _pa_listify(_pa_get(person, "phones_found"))
    for ph in raw_phones:
        if isinstance(ph, dict):
            num = ph.get("number") or ph.get("phone") or ph.get("value") or ""
            tags = ph.get("tags") or []
            ptype = ph.get("type") or _pa_classify_phone(num, tags)
            onto.phones.append(PhoneNumber(number=str(num), type=ptype,
                                           country=str(ph.get("country", "") or ""),
                                           source=str(ph.get("source", "") or "")))
        else:
            num = str(ph)
            onto.phones.append(PhoneNumber(number=num, type=_pa_classify_phone(num, []),
                                           source=""))

    # ── organizations ────────────────────────────────────────────────────────
    for o in _pa_listify(_pa_get(entities, "organizations") or _pa_get(entities, "orgs")
                         or _pa_get(entities, "companies")):
        name = str(_pa_get(o, "name", "") or "").strip()
        if not name:
            continue
        otype = _pa_norm(_pa_get(o, "type"))
        if otype not in ("shell", "front", "legitimate"):
            # Classify from name + tags against the shell/front vocabulary (keyword
            # match over a vocab, not equality to one literal). "front" wins ties.
            blob = name + " " + " ".join(str(t) for t in _pa_listify(_pa_get(o, "tags")))
            otype = ("front" if _pa_any_token(blob, ("front", "paper company", "nominee"))
                     else "shell" if _pa_any_token(blob, _PA_SHELL_TOKENS)
                     else "legitimate")
        juris = str(_pa_get(o, "jurisdiction", "") or "")
        onto.organizations.append(Organization(
            name=name, type=otype, jurisdiction=juris,
            offshore=bool(_pa_get(o, "offshore", False)) or _pa_is_offshore(juris),
            source=str(_pa_get(o, "source", "") or "")))

    # ── transactions — HOP 2 general column mapping, from BOTH buckets ─────────
    # Transactions are drawn from the dedicated `financial_data` bucket AND from
    # the generic structured `records` — because the document-panel financial CSVs
    # (crypto flows, bank statements, investor ledgers) land in `records`, while
    # `financial_data` is fed only by the separate asset-upload widget. One shared
    # mapper (_pa_build_transaction) serves both; `records` is read in STRICT mode
    # so non-financial rows (call logs, movement/ANPR, receipts) are skipped, not
    # fabricated. Deduplicated by (date, amount, counterparty, direction) so a row
    # present in both buckets is counted once.
    fin = financial_data or {}
    _seen_txn = set()

    def _txn_key(tx):
        return (_pa_token(tx.date), round(float(tx.amount or 0), 2),
                _pa_token(tx.counterparty), _pa_norm(tx.direction))

    # 1) dedicated financial bucket — permissive (every row maps, as before).
    for t in _pa_listify(_pa_get(fin, "transactions") if isinstance(fin, dict) else fin):
        tx = _pa_build_transaction(t, strict=False)
        if tx is None:
            continue
        onto.transactions.append(tx)
        _seen_txn.add(_txn_key(tx))

    # 2) generic records — STRICT (skip non-financial rows), dedup vs bucket + self.
    for r in _pa_listify(records):
        tx = _pa_build_transaction(r, strict=True)
        if tx is None:
            continue
        k = _txn_key(tx)
        if k in _seen_txn:
            continue
        _seen_txn.add(k)
        onto.transactions.append(tx)

    # ── counterparty → Organization enrichment (HOP 2) ─────────────────────────
    # The entity money is routed THROUGH (mixer, offshore exchange, shell company)
    # often appears only as a transaction counterparty, never as a graph node. Add
    # it as a typed Organization when its name matches the shell/front or foreign
    # vocabularies — keyword-driven, no case-specific names, no fabrication of a
    # company where the counterparty text gives no such signal.
    _seen_orgs = {_pa_token(o.name) for o in onto.organizations}
    for t in onto.transactions:
        cp = (t.counterparty or "").strip()
        if not cp or _pa_token(cp) in _seen_orgs:
            continue
        is_shell = _pa_any_token(cp, _PA_SHELL_TOKENS)
        is_foreign = _pa_any_token(cp, _PA_FOREIGN_TOKENS)
        if not (is_shell or is_foreign):
            continue
        otype = ("front" if _pa_any_token(cp, ("front", "paper company", "nominee"))
                 else "shell" if is_shell else "legitimate")
        onto.organizations.append(Organization(
            name=cp, type=otype, jurisdiction="", offshore=is_foreign, source="counterparty"))
        _seen_orgs.add(_pa_token(cp))

    # ── properties (financial_data or entities) ───────────────────────────────
    prop_src = (_pa_get(fin, "properties") if isinstance(fin, dict) else None) \
        or _pa_get(entities, "properties") or _pa_get(entities, "assets")
    for p in _pa_listify(prop_src):
        juris = str(_pa_get(p, "jurisdiction", "") or _pa_get(p, "location", "") or "")
        onto.properties.append(Property(
            jurisdiction=juris, type=str(_pa_get(p, "type", "") or ""),
            foreign=bool(_pa_get(p, "foreign", False)) or _pa_is_foreign(juris),
            source=str(_pa_get(p, "source", "") or "")))

    # ── Relationship graph: build from records when none supplied upstream ─────
    # The graph rules (NETWORK_HUB, SHELL_LAYERING_NETWORK) need a populated graph;
    # if the upstream graph is empty (e.g. the CDR builder didn't recognise this
    # file's columns), assemble a subject-centric one from the source rows. Real
    # edges only — no invented associate-to-associate links.
    if onto.graph is None or onto.graph.number_of_nodes() == 0:
        _built = _pa_graph_from_records(onto.subject_name, _pa_listify(records), onto.transactions)
        if _built.number_of_nodes() > 1:
            onto.graph = _built

    # ── money-routing graph enrichment — orgs the money moved through ─────────
    # SHELL_LAYERING_NETWORK is a GRAPH rule: it needs the offshore shell to be a
    # graph node whose neighbours are the parties routing money through it. Those
    # parties are genuinely named ON the transaction rows themselves — the
    # counterparty column names the org, and the party-side columns (subject_name,
    # account_holder, wallet_holder…) name who moved the money. Add one node per
    # organization that appears as a row's counterparty, and one edge per named
    # party on that row. Row-grounded edges only — no associate-to-associate links
    # are invented, and no edge exists without a source row naming both ends.
    # Node kind is stored under `node_type`, the relationship_mapper convention
    # used by the live graph (its nodes carry `node_type`, not `type`).
    _org_by_token = {_pa_token(o.name): o for o in onto.organizations if o.name}
    if _org_by_token:
        _g = onto.graph if isinstance(onto.graph, nx.Graph) else nx.Graph()
        _node_by_token = {_pa_token(n): n for n in _g.nodes}
        _money_rows = list(_pa_listify(records)) + \
            _pa_listify(_pa_get(fin, "transactions") if isinstance(fin, dict) else fin)
        _added_edge = False
        for row in _money_rows:
            if not isinstance(row, dict):
                continue
            cp = str(_pa_get(row, "counterparty")
                     or _pa_pick(row, _PA_COL_COUNTERPARTY) or "").strip()
            org = _org_by_token.get(_pa_token(cp)) if cp else None
            if org is None:
                continue
            org_node = _node_by_token.get(_pa_token(org.name))
            if org_node is None:
                org_node = org.name
                _g.add_node(org_node, node_type="organization")
                _node_by_token[_pa_token(org.name)] = org_node
            for col, val in row.items():
                if _pa_token(col) not in _PA_COL_PARTY:
                    continue
                party = str(val or "").strip()
                if not party or _pa_token(party) == _pa_token(org.name):
                    continue
                pnode = _node_by_token.get(_pa_token(party))
                if pnode is None:
                    pnode = party
                    _g.add_node(pnode, node_type=(
                        "person" if _pa_personish(party) else "entity"))
                    _node_by_token[_pa_token(party)] = pnode
                _g.add_edge(pnode, org_node)
                _added_edge = True
        if _added_edge and (onto.graph is None or onto.graph.number_of_nodes() == 0):
            onto.graph = _g

    # ── HOP 3: dated comm/legal/deletion events from SOURCE ROWS + TIMELINE ────
    # Priority order for each category: explicit typed entities → records/timeline
    # (dated, rich) → flag text (dateless, last resort). The date travels with the
    # semantic so date-correlated rules (e.g. ANTI_FORENSIC) keep working.
    _evpairs = []
    for row in _pa_listify(records):
        if not isinstance(row, dict):
            continue
        vals = [str(v) for v in row.values() if str(v).strip() not in ("", "None", "nan")]
        blob = " ".join(vals)
        rdate = _pa_first_date(str(_pa_pick(row, _PA_COL_DATE) or "")) or _pa_first_date(blob)
        _evpairs.append((blob, rdate, _pa_pick(row, _PA_COL_EXITNODE)))
    _tl_raw = _pa_get(timeline, "events") if isinstance(timeline, dict) else timeline
    for ev in _pa_listify(_tl_raw):
        desc = (_pa_get(ev, "description") or _pa_get(ev, "context")
                or _pa_get(ev, "event") or "")
        edate = _pa_first_date(str(_pa_get(ev, "date") or _pa_get(ev, "normalized")
                                   or _pa_pick(ev, _PA_COL_DATE) or ""))
        if desc:
            _evpairs.append((str(desc), edate, None))
    # Raw narrative text (case notes, surveillance logs): scan LINE BY LINE so an
    # app name / enforcement reference keeps any date (or embedded year) on its line.
    for txt in _pa_listify(texts):
        for line in str(txt or "").splitlines():
            line = line.strip()
            if line:
                _evpairs.append((line, _pa_first_date(line), None))
    rec_dels, rec_legals, rec_chans = _pa_events_from_pairs(_evpairs)

    # ── comm channels (explicit entities → records → flags) ───────────────────
    explicit_chans = _pa_listify(_pa_get(entities, "comm_channels") or _pa_get(entities, "channels"))
    for c in explicit_chans:
        onto.comm_channels.append(CommChannel(
            type=_pa_norm(_pa_get(c, "type")), encrypted=bool(_pa_get(c, "encrypted", False)),
            foreign_exit=bool(_pa_get(c, "foreign_exit", False)),
            source=str(_pa_get(c, "source", "") or "")))
    _have = {c.type for c in onto.comm_channels}
    for c in rec_chans:
        if c.type not in _have:
            _have.add(c.type)
            onto.comm_channels.append(c)
    if not onto.comm_channels:
        onto.comm_channels = _pa_channels_from_flags(onto.flags)

    # ── legal proceedings (explicit → records → flags) ────────────────────────
    explicit_legal = _pa_listify(_pa_get(entities, "legal_proceedings")
                                 or _pa_get(entities, "proceedings"))
    for lp in explicit_legal:
        onto.legal_proceedings.append(LegalProceeding(
            agency=str(_pa_get(lp, "agency", "") or ""), status=_pa_norm(_pa_get(lp, "status")),
            date=str(_pa_get(lp, "date", "") or ""), case_ref=str(_pa_get(lp, "case_ref", "") or ""),
            kind=_pa_norm(_pa_get(lp, "kind")), source=str(_pa_get(lp, "source", "") or "")))
    onto.legal_proceedings.extend(rec_legals)
    if not onto.legal_proceedings:
        onto.legal_proceedings = _pa_legal_from_flags(onto.flags)

    # ── deletion events (explicit → records → flags) ──────────────────────────
    explicit_del = _pa_listify(_pa_get(entities, "deletion_events"))
    for de in explicit_del:
        onto.deletion_events.append(DeletionEvent(
            timestamp=str(_pa_get(de, "timestamp", "") or _pa_get(de, "date", "") or ""),
            target=str(_pa_get(de, "target", "") or ""), source=str(_pa_get(de, "source", "") or "")))
    onto.deletion_events.extend(rec_dels)
    if not onto.deletion_events:
        onto.deletion_events = _pa_deletions_from_flags(onto.flags)

    # ── timeline events (general field resolution: normalized/context/…) ───────
    tl_events = _pa_get(timeline, "events") if isinstance(timeline, dict) else timeline
    for ev in _pa_listify(tl_events):
        edate = (_pa_get(ev, "date") or _pa_get(ev, "normalized")
                 or _pa_pick(ev, _PA_COL_DATE) or "")
        edesc = (_pa_get(ev, "description") or _pa_get(ev, "event")
                 or _pa_get(ev, "context") or "")
        esig = (_pa_get(ev, "significance") or _pa_get(ev, "importance")
                or _pa_get(ev, "severity") or "")
        # Fill a MISSING significance by keyword (never overrides a supplied one),
        # so timeline-cluster detection works on sources that don't pre-score events.
        if not str(esig).strip():
            esig = "HIGH" if _pa_high_significance(edesc) else "LOW"
        onto.timeline_events.append(TimelineEvent(
            date=str(edate or ""), significance=str(esig or "LOW"),
            source=str(_pa_get(ev, "source", "") or ""), description=str(edesc or "")))

    # ── typed §03/§04 evidence — platform accounts + locations (Phase 0.5 Step 3)
    # Harvested per DOCUMENT so provenance (the actual source file) travels with
    # every value. Admission is structural: a platform must come from a
    # platform-named column; a handle only from a username-bearing column; a
    # location only from a location-named column or a location-labelled text
    # line. Unlabelled lines (headers, letterheads) carry no label and can never
    # become locations; label-shaped values ("subscriber", "platform_name") are
    # rejected by _pa_is_schema_label. Column vocabularies are shared with
    # entity_resolution — ONE vocabulary, two readers. No pattern rule reads
    # either list, so §09B is provably unaffected.
    try:
        from modules.entity_resolution import (
            _PLATFORM_COL_NAMES as _er_plat, _HANDLE_COL_NAMES as _er_handle,
            _URL_COL_NAMES as _er_url, _STATUS_COL_NAMES as _er_status,
            _CONFIRMED_STATUS_VALUES as _er_confirmed,
            _NOISE_HANDLE_TOKENS as _er_noise)
    except Exception:                        # keep this module importable alone
        _er_plat = {"platform", "site", "service", "network", "social"}
        _er_handle = {"username", "handle", "user", "account", "screen_name"}
        _er_url = {"url", "link", "profile", "profile_url"}
        _er_status = {"status", "state", "verification", "verified", "confirmed"}
        _er_confirmed = {"confirmed", "verified", "active", "ok", "true", "yes", "valid"}
        _er_noise = set()
    _plat_cols = {_pa_token(c) for c in _er_plat}
    _handle_cols = {_pa_token(c) for c in _er_handle}
    _url_cols = {_pa_token(c) for c in _er_url}
    _status_cols = {_pa_token(c) for c in _er_status}
    _exit_cols = {_pa_token(c) for c in _PA_COL_EXITNODE}

    def _noise_handle(h) -> bool:
        lc = str(h or "").lstrip("@").lower().strip()
        return (not lc or _pa_is_schema_label(lc)
                or lc in _er_noise or any(t and t in lc for t in _er_noise))

    def _loc_col(header) -> bool:
        """Word-level match: 'tower_location' → yes via 'location';
        'allocation' → no; VPN-exit columns are channels, not places."""
        if _pa_token(header) in _exit_cols:
            return False
        words = [w for w in _pa_re.split(r"[^a-z0-9]+", _pa_norm(header)) if w]
        return any(w in _PA_COL_LOCATION for w in words)

    _loc_by_token, _acct_by_token = {}, {}

    def _add_location(val, kind, fname):
        v = str(val or "").strip()
        if len(v) < 3 or _pa_is_schema_label(v) or _PA_DATE_RE.search(v):
            return
        if sum(ch.isdigit() for ch in v) > len(v) // 2:   # codes/IPs, not places
            return
        k = _pa_token(v)
        if not k:
            return
        ex = _loc_by_token.get(k)
        if ex is None:
            loc = Location(name=v, kind=kind, source=fname)
            _loc_by_token[k] = loc
            onto.locations.append(loc)
        elif fname not in ex.source and len(ex.source) < 120:
            ex.source += f", {fname}"

    for doc in _pa_listify(documents):
        if not isinstance(doc, dict):
            continue
        fname = (str(doc.get("filename") or doc.get("name") or "").strip()
                 or "uploaded document")
        for row in _pa_listify(doc.get("structured_rows")):
            if not isinstance(row, dict):
                continue
            plat = handle = url = status = ""
            for col, val in row.items():
                ct, v = _pa_token(col), str(val or "").strip()
                if not v or v.lower() in ("none", "nan"):
                    continue
                if ct in _plat_cols and not plat:
                    plat = v
                elif ct in _handle_cols and not handle:
                    handle = v
                elif ct in _url_cols and not url:
                    url = v
                elif ct in _status_cols and not status:
                    status = v.lower()
                if _loc_col(col):
                    _kind = ("tower" if "tower" in ct else "address"
                             if _pa_any_token(col, ("address", "home", "residence"))
                             else "stated")
                    _add_location(v, _kind, fname)
            if not plat or _pa_is_schema_label(plat):
                continue
            if status and status not in _er_confirmed:
                continue                       # explicitly non-confirmed → skip
            if _noise_handle(handle):
                handle = ""
            pk = _pa_token(plat)
            ex = _acct_by_token.get(pk)
            if ex is None:
                acct = PlatformAccount(platform=plat, handle=handle.lstrip("@"),
                                       url=url, source=fname)
                _acct_by_token[pk] = acct
                onto.platform_accounts.append(acct)
            else:
                if handle and not ex.handle:   # a later row supplies the handle
                    ex.handle = handle.lstrip("@")
                if url and not ex.url:
                    ex.url = url
                if fname not in ex.source and len(ex.source) < 120:
                    ex.source += f", {fname}"
        # Location-LABELLED text lines ("Home address: Salt Lake…") — the label
        # IS the typed evidence; a bare header line has no label, so it never
        # matches. Long prefixes are prose, not labels.
        for line in str(doc.get("raw_text") or doc.get("full_text")
                        or doc.get("text") or "").splitlines():
            label, sep, val = line.partition(":")
            if not sep or len(label.strip()) > 30:
                continue
            if _loc_col(label):
                _add_location(val, "address" if _pa_any_token(
                    label, ("address", "home", "residence")) else "stated", fname)

    return onto
