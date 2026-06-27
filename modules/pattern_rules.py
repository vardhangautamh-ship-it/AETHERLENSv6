"""
AETHERLENS — Pattern Inference Library (deterministic).

This module is the *intelligence* of the Pattern Analysis section. Every
conclusion it produces is rule-based: the SAME input yields the SAME output on
every run, with no randomness, no clock reads, and no LLM involvement. An LLM
may later rephrase these conclusions for readability (see report Step 5), but it
may never originate one. If the LLM is switched off, every conclusion below
still stands — just in plainer language.

DESIGN ORDER (intentional): the rules are written FIRST; the ontology
(modules/ontology.py) is then shaped to provide exactly what these rules read.
Nothing here is speculative — every entity attribute referenced below earns its
place by being needed by a rule.

────────────────────────────────────────────────────────────────────────────
ONTOLOGY CONTRACT (what a rule may read off the `onto` object)
────────────────────────────────────────────────────────────────────────────
`onto` is duck-typed; Step 2's `Ontology` dataclass satisfies this contract.
Each collection below is a list (possibly empty). Attributes are read with
getattr + safe defaults, so partially-populated ontologies never raise.

  onto.subject_name : str                  # the resolved primary subject
  onto.subject      : Person | None        # the is_subject Person (if typed)
  onto.flags        : list[str]            # raw anomaly-flag texts (§09 input)
  onto.graph        : networkx.Graph       # the EXISTING relationship graph

  onto.persons          : [Person]         # name, role, is_subject, source
  onto.phones           : [PhoneNumber]    # number, type, country, source
  onto.organizations    : [Organization]   # name, type, jurisdiction, offshore
  onto.transactions     : [Transaction]    # date, direction, amount,
                                           #   cross_border, counterparty,
                                           #   structured, source
  onto.properties       : [Property]       # jurisdiction, type, foreign, source
  onto.comm_channels    : [CommChannel]    # type, encrypted, foreign_exit, src
  onto.legal_proceedings: [LegalProceeding]# agency, status, date, case_ref,
                                           #   kind, source
  onto.deletion_events  : [DeletionEvent]  # timestamp, target, source
  onto.timeline_events  : [TimelineEvent]  # date, significance, source, descr

Entity attribute vocabularies (deterministic enums, lower-case compared):
  PhoneNumber.type   : "domestic" | "international" | "burner"
  Organization.type  : "shell" | "front" | "legitimate"
  Transaction.direction : "in" | "out"
  CommChannel.type   : "protonmail" | "telegram" | "signal" | "vpn" | "email" | ...
  LegalProceeding.kind  : "loc" | "enforcement" | "inquiry" | "notice"
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

import networkx as nx


# ══════════════════════════════════════════════════════════════════════════════
# Result type
# ══════════════════════════════════════════════════════════════════════════════
@dataclass
class PatternMatch:
    """A single deterministic pattern conclusion. Fully explainable: the facts
    that fired it are carried in `triggers_met`, and `plain_explanation` is a
    rule-generated sentence (never LLM text)."""
    pattern_id: str
    pattern_name: str
    case_type: str                       # "financial" | "cyber" | "general"
    confidence: str                      # "STRONG" | "MODERATE" | "WEAK"
    triggers_met: list[str]
    plain_explanation: str
    supporting_sources: list[str] = field(default_factory=list)


# Case-type constants
FINANCIAL = "financial"
CYBER = "cyber"
GENERAL = "general"


# ══════════════════════════════════════════════════════════════════════════════
# Small deterministic helpers
# ══════════════════════════════════════════════════════════════════════════════
def _norm(s) -> str:
    return str(s or "").strip().lower()


def _attr(obj, name, default=None):
    return getattr(obj, name, default)


def _parse_date(value):
    """Tolerant date parser → datetime | None. Deterministic (no clock)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    s = str(value).strip()
    if not s:
        return None
    s = s.replace("/", "-")
    # take just the date portion if a timestamp was supplied
    s_date = s.split("T")[0].split(" ")[0]
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%m-%d-%Y"):
        try:
            return datetime.strptime(s_date, fmt)
        except ValueError:
            continue
    return None


def _sources(*entities) -> list[str]:
    """Collect unique, sorted, non-empty `.source` values for explainability."""
    out = set()
    for e in entities:
        if isinstance(e, (list, tuple, set)):
            for sub in e:
                src = _norm(_attr(sub, "source", ""))
                if src:
                    out.add(str(_attr(sub, "source")))
        else:
            src = _norm(_attr(e, "source", ""))
            if src:
                out.add(str(_attr(e, "source")))
    return sorted(out)


# Domestic / offshore jurisdiction sense (kept tiny and explicit)
_DOMESTIC_TOKENS = ("india", "indian", "in", "domestic")
_OFFSHORE_TOKENS = (
    "uae", "dubai", "singapore", "switzerland", "swiss", "cayman", "bvi",
    "british virgin", "mauritius", "panama", "cyprus", "hong kong", "hongkong",
    "seychelles", "malta", "luxembourg", "offshore", "foreign",
)
_ENCRYPTED_CHANNEL_TYPES = ("protonmail", "proton", "telegram", "signal", "wickr", "threema")
_ENFORCEMENT_AGENCIES = ("dri", "ncb", "ed", "sfio", "cbi", "serious fraud")


def _is_offshore_jurisdiction(j) -> bool:
    j = _norm(j)
    if not j:
        return False
    if any(t in j for t in _DOMESTIC_TOKENS) and not any(t in j for t in _OFFSHORE_TOKENS):
        return False
    return any(t in j for t in _OFFSHORE_TOKENS)


def _is_foreign(j) -> bool:
    """A jurisdiction that is not domestic India."""
    j = _norm(j)
    if not j:
        return False
    return not any(t in j for t in _DOMESTIC_TOKENS)


# Reporting thresholds for structuring detection (INR). A deposit sitting in the
# 80–100% band just under a threshold is the classic sub-threshold structuring
# signature.
_STRUCTURING_THRESHOLDS = (50_000, 200_000, 1_000_000)


def _is_structured_deposit(t) -> bool:
    if _norm(_attr(t, "direction")) != "in":
        return False
    if bool(_attr(t, "structured", False)):
        return True
    amt = _attr(t, "amount", 0) or 0
    try:
        amt = float(amt)
    except (TypeError, ValueError):
        return False
    for thr in _STRUCTURING_THRESHOLDS:
        if thr * 0.8 <= amt < thr:
            return True
    return False


# ══════════════════════════════════════════════════════════════════════════════
# FINANCIAL-CRIME PATTERNS (1–5)
# ══════════════════════════════════════════════════════════════════════════════
def rule_layering_structure(onto) -> PatternMatch | None:
    """LAYERING_STRUCTURE — structured deposits → cross-border wires → shell."""
    txns = _attr(onto, "transactions", []) or []
    orgs = _attr(onto, "organizations", []) or []

    structured = [t for t in txns if _is_structured_deposit(t)]
    wires = [t for t in txns
             if _norm(_attr(t, "direction")) == "out" and bool(_attr(t, "cross_border", False))]
    shells = [o for o in orgs if _norm(_attr(o, "type")) in ("shell", "front")]

    if not (structured and wires and shells):
        return None

    entity = str(_attr(shells[0], "name", "an unnamed entity"))
    triggers = [
        f"{len(structured)} structured cash deposit(s) detected",
        f"{len(wires)} cross-border wire transfer(s)",
        f"shell/front entity present: {entity}",
    ]
    confidence = "STRONG" if (len(structured) >= 3 and len(wires) >= 2) else "MODERATE"
    explanation = (
        f"Structured cash deposits feeding cross-border wires through {entity} — "
        f"a layering pattern consistent with trade-based money laundering."
    )
    return PatternMatch(
        pattern_id="LAYERING_STRUCTURE",
        pattern_name="Layering Structure",
        case_type=FINANCIAL,
        confidence=confidence,
        triggers_met=triggers,
        plain_explanation=explanation,
        supporting_sources=_sources(structured, wires, shells),
    )


def rule_offshore_flight_risk(onto) -> PatternMatch | None:
    """OFFSHORE_FLIGHT_RISK — active LOC + offshore assets + international reach."""
    legals = _attr(onto, "legal_proceedings", []) or []
    props = _attr(onto, "properties", []) or []
    phones = _attr(onto, "phones", []) or []
    flags = _attr(onto, "flags", []) or []

    active_loc = [lp for lp in legals
                  if _norm(_attr(lp, "kind")) == "loc" and _norm(_attr(lp, "status")) == "active"]
    if not active_loc:
        # flag-text fallback: an active lookout circular noted in §09 flags
        if any("lookout" in _norm(f) or "loc " in _norm(f) or _norm(f).endswith(" loc")
               for f in flags):
            active_loc = ["<flag:lookout-circular>"]

    foreign_props = [p for p in props
                     if bool(_attr(p, "foreign", False)) or _is_foreign(_attr(p, "jurisdiction"))]
    intl_contacts = [ph for ph in phones if _norm(_attr(ph, "type")) == "international"]
    intl_flag = any("internation" in _norm(f) or "travel" in _norm(f) or "foreign" in _norm(f)
                    for f in flags)

    if not (active_loc and foreign_props and (intl_contacts or intl_flag)):
        return None

    triggers = [
        "active lookout circular (LOC)",
        f"{len(foreign_props)} offshore/foreign property holding(s)",
        (f"{len(intl_contacts)} international contact line(s)"
         if intl_contacts else "international travel/contact indicator in flags"),
    ]
    confidence = "STRONG" if (intl_contacts and len(foreign_props) >= 1) else "MODERATE"
    explanation = (
        "Active LOC combined with confirmed offshore assets and international "
        "contacts indicates elevated flight risk."
    )
    return PatternMatch(
        pattern_id="OFFSHORE_FLIGHT_RISK",
        pattern_name="Offshore Flight Risk",
        case_type=FINANCIAL,
        confidence=confidence,
        triggers_met=triggers,
        plain_explanation=explanation,
        supporting_sources=_sources(foreign_props, intl_contacts),
    )


def rule_operational_security(onto) -> PatternMatch | None:
    """OPERATIONAL_SECURITY — multiple lines + encrypted channels (+ VPN)."""
    phones = _attr(onto, "phones", []) or []
    channels = _attr(onto, "comm_channels", []) or []

    burners = [ph for ph in phones if _norm(_attr(ph, "type")) == "burner"]
    encrypted = [c for c in channels
                 if bool(_attr(c, "encrypted", False))
                 or _norm(_attr(c, "type")) in _ENCRYPTED_CHANNEL_TYPES]
    foreign_vpn = [c for c in channels
                   if _norm(_attr(c, "type")) == "vpn" and bool(_attr(c, "foreign_exit", False))]

    if not (len(phones) >= 3 and encrypted):
        return None

    triggers = [f"{len(phones)} distinct phone line(s) ({len(burners)} flagged as burner)",
                f"{len(encrypted)} encrypted messaging channel(s)"]
    if foreign_vpn:
        triggers.append(f"{len(foreign_vpn)} foreign VPN/exit node(s)")
    confidence = "STRONG" if (len(burners) >= 2 and foreign_vpn) else "MODERATE"
    explanation = (
        "Multiple burner lines plus encrypted channels indicate deliberate "
        "communication compartmentalisation."
    )
    return PatternMatch(
        pattern_id="OPERATIONAL_SECURITY",
        pattern_name="Operational Security",
        case_type=FINANCIAL,
        confidence=confidence,
        triggers_met=triggers,
        plain_explanation=explanation,
        supporting_sources=_sources(phones, encrypted, foreign_vpn),
    )


def rule_shell_layering_network(onto) -> PatternMatch | None:
    """SHELL_LAYERING_NETWORK — shell + 2+ associates routed through it + offshore."""
    orgs = _attr(onto, "organizations", []) or []
    graph = _attr(onto, "graph", None)

    offshore_shells = [o for o in orgs
                       if _norm(_attr(o, "type")) in ("shell", "front")
                       and (bool(_attr(o, "offshore", False))
                            or _is_offshore_jurisdiction(_attr(o, "jurisdiction")))]
    if not offshore_shells:
        return None

    # Count associates linked to the shell entity in the EXISTING graph.
    best_org = None
    best_assoc = 0
    if isinstance(graph, nx.Graph):
        for o in offshore_shells:
            name = str(_attr(o, "name", ""))
            if name and graph.has_node(name):
                assoc = sum(1 for nb in graph.neighbors(name)
                            if _norm(graph.nodes[nb].get("type", "")) in ("", "person"))
                if assoc > best_assoc:
                    best_assoc, best_org = assoc, o
    if best_org is None:
        best_org = offshore_shells[0]

    if best_assoc < 2:
        return None

    entity = str(_attr(best_org, "name", "an offshore entity"))
    triggers = [
        f"shell/front entity in offshore jurisdiction: {entity}",
        f"{best_assoc} associates routed through the same entity",
    ]
    confidence = "STRONG" if best_assoc >= 3 else "MODERATE"
    explanation = (
        f"Multiple associates routed through a single offshore entity ({entity}) — "
        f"indicative of a layering network."
    )
    return PatternMatch(
        pattern_id="SHELL_LAYERING_NETWORK",
        pattern_name="Shell Layering Network",
        case_type=FINANCIAL,
        confidence=confidence,
        triggers_met=triggers,
        plain_explanation=explanation,
        supporting_sources=_sources(offshore_shells),
    )


def rule_enforcement_history_escalation(onto) -> PatternMatch | None:
    """ENFORCEMENT_HISTORY_ESCALATION — 3+ enforcement actions across years."""
    legals = _attr(onto, "legal_proceedings", []) or []

    actions = [lp for lp in legals
               if _norm(_attr(lp, "kind")) == "enforcement"
               or _norm(_attr(lp, "agency")) in _ENFORCEMENT_AGENCIES]
    if len(actions) < 3:
        return None

    years = sorted({d.year for d in (_parse_date(_attr(a, "date")) for a in actions) if d})
    if len(years) < 2:
        return None

    agencies = sorted({str(_attr(a, "agency")).upper() for a in actions if _attr(a, "agency")})
    triggers = [
        f"{len(actions)} enforcement actions on record",
        f"agencies involved: {', '.join(agencies) if agencies else 'multiple'}",
        f"spanning {years[0]}–{years[-1]} ({len(years)} distinct years)",
    ]
    confidence = "STRONG" if (len(actions) >= 4 and len(years) >= 3) else "MODERATE"
    explanation = (
        "A sustained pattern of enforcement actions across multiple agencies and "
        "years indicates a persistent subject of interest."
    )
    return PatternMatch(
        pattern_id="ENFORCEMENT_HISTORY_ESCALATION",
        pattern_name="Enforcement History Escalation",
        case_type=FINANCIAL,
        confidence=confidence,
        triggers_met=triggers,
        plain_explanation=explanation,
        supporting_sources=_sources(actions),
    )


# ══════════════════════════════════════════════════════════════════════════════
# CYBER-CRIME PATTERNS (6–8)
# ══════════════════════════════════════════════════════════════════════════════
_BENIGN_PURPOSE_TOKENS = ("research", "student", "academic", "personal", "hobby",
                          "learning", "study", "education")
_DATA_VOLUME_TOKENS = ("data egress", "data volume", "exfil", " gb", " tb",
                       "gigabyte", "terabyte", "upload volume")
# Operational-scale spend threshold (INR) — large enough to contradict a claimed
# hobby/research footprint.
_OPERATIONAL_SPEND_INR = 500_000


def rule_operational_scale_mismatch(onto) -> PatternMatch | None:
    """OPERATIONAL_SCALE_MISMATCH — spend/usage vs a claimed benign purpose."""
    txns = _attr(onto, "transactions", []) or []
    flags = _attr(onto, "flags", []) or []

    claimed_benign = any(any(tok in _norm(f) for tok in _BENIGN_PURPOSE_TOKENS) for f in flags)
    if not claimed_benign:
        return None

    total_out = 0.0
    for t in txns:
        if _norm(_attr(t, "direction")) == "out":
            try:
                total_out += float(_attr(t, "amount", 0) or 0)
            except (TypeError, ValueError):
                pass
    big_spend = total_out >= _OPERATIONAL_SPEND_INR
    big_data = any(any(tok in _norm(f) for tok in _DATA_VOLUME_TOKENS) for f in flags)

    if not (big_spend or big_data):
        return None

    triggers = ["a benign purpose is claimed in the record"]
    if big_spend:
        triggers.append(f"outbound spend of ~INR {int(total_out):,} at operational scale")
    if big_data:
        triggers.append("high data egress/volume noted in flags")
    confidence = "STRONG" if (big_spend and big_data) else "MODERATE"
    explanation = (
        "Expenditure/usage at operational scale is inconsistent with the stated "
        "benign purpose."
    )
    return PatternMatch(
        pattern_id="OPERATIONAL_SCALE_MISMATCH",
        pattern_name="Operational Scale Mismatch",
        case_type=CYBER,
        confidence=confidence,
        triggers_met=triggers,
        plain_explanation=explanation,
        supporting_sources=_sources(txns),
    )


def rule_anti_forensic_behaviour(onto) -> PatternMatch | None:
    """ANTI_FORENSIC_BEHAVIOUR — deletions timed to an inquiry/notice date."""
    deletions = _attr(onto, "deletion_events", []) or []
    legals = _attr(onto, "legal_proceedings", []) or []
    if not deletions:
        return None

    inquiry_dates = [d for d in (_parse_date(_attr(lp, "date")) for lp in legals
                                 if _norm(_attr(lp, "kind")) in ("inquiry", "notice")) if d]
    if not inquiry_dates:
        return None

    # Correlate: a deletion that occurs on/after an inquiry date within 30 days.
    best_gap = None
    for de in deletions:
        dt = _parse_date(_attr(de, "timestamp"))
        if not dt:
            continue
        for idate in inquiry_dates:
            gap = (dt - idate).days
            if 0 <= gap <= 30 and (best_gap is None or gap < best_gap):
                best_gap = gap
    if best_gap is None:
        return None

    triggers = [
        f"{len(deletions)} deletion event(s) recorded",
        f"earliest deletion within {best_gap} day(s) of an inquiry/notice date",
    ]
    confidence = "STRONG" if best_gap <= 7 else "MODERATE"
    explanation = (
        "Deletion activity timed to an inquiry date suggests evidence awareness."
    )
    return PatternMatch(
        pattern_id="ANTI_FORENSIC_BEHAVIOUR",
        pattern_name="Anti-Forensic Behaviour",
        case_type=CYBER,
        confidence=confidence,
        triggers_met=triggers,
        plain_explanation=explanation,
        supporting_sources=_sources(deletions),
    )


def rule_counter_surveillance(onto) -> PatternMatch | None:
    """COUNTER_SURVEILLANCE — VPN + encryption + compartmentalised platforms."""
    channels = _attr(onto, "comm_channels", []) or []

    vpn = [c for c in channels if _norm(_attr(c, "type")) == "vpn"]
    foreign_vpn = [c for c in vpn if bool(_attr(c, "foreign_exit", False))]
    encrypted = [c for c in channels
                 if bool(_attr(c, "encrypted", False))
                 or _norm(_attr(c, "type")) in _ENCRYPTED_CHANNEL_TYPES]
    distinct_platforms = sorted({_norm(_attr(c, "type")) for c in channels if _norm(_attr(c, "type"))})

    if not (vpn and encrypted and len(distinct_platforms) >= 2):
        return None

    triggers = [
        f"{len(vpn)} VPN channel(s)" + (f" ({len(foreign_vpn)} foreign exit)" if foreign_vpn else ""),
        f"{len(encrypted)} encrypted messaging channel(s)",
        f"{len(distinct_platforms)} distinct platforms in use (compartmentalised)",
    ]
    confidence = "STRONG" if (foreign_vpn and len(distinct_platforms) >= 3) else "MODERATE"
    explanation = (
        "VPN use plus encrypted, compartmentalised communications indicate "
        "counter-surveillance awareness."
    )
    return PatternMatch(
        pattern_id="COUNTER_SURVEILLANCE",
        pattern_name="Counter-Surveillance",
        case_type=CYBER,
        confidence=confidence,
        triggers_met=triggers,
        plain_explanation=explanation,
        supporting_sources=_sources(channels),
    )


# ══════════════════════════════════════════════════════════════════════════════
# GENERAL PATTERNS (9–10)
# ══════════════════════════════════════════════════════════════════════════════
def rule_network_hub(onto) -> PatternMatch | None:
    """NETWORK_HUB — subject is the central connector of separate associates.

    Uses real betweenness centrality on the EXISTING NetworkX graph and confirms
    the subject is an articulation point whose removal splits the associates into
    2+ otherwise-separate groups. Nothing fabricated.
    """
    graph = _attr(onto, "graph", None)
    subject = str(_attr(onto, "subject_name", "") or "")
    if not isinstance(graph, nx.Graph) or not subject or not graph.has_node(subject):
        return None
    if graph.number_of_nodes() < 4 or graph.degree(subject) < 2:
        return None

    bc = nx.betweenness_centrality(graph)          # deterministic for a fixed graph
    subj_bc = bc.get(subject, 0.0)
    if subj_bc <= 0:
        return None
    if subj_bc < max(bc.values()):                 # must be THE central connector
        return None

    # How many otherwise-separate groups does the subject bridge?
    neighbours = list(graph.neighbors(subject))
    reduced = graph.copy()
    reduced.remove_node(subject)
    components = [c for c in nx.connected_components(reduced)
                 if any(nb in c for nb in neighbours)]
    separated = len(components)
    if separated < 2:
        return None

    triggers = [
        f"subject holds the highest betweenness centrality ({subj_bc:.3f})",
        f"subject directly links {len(neighbours)} associates",
        f"removing the subject splits them into {separated} otherwise-separate groups",
    ]
    confidence = "STRONG" if separated >= 3 else "MODERATE"
    explanation = (
        f"The subject is the central connector linking {len(neighbours)} "
        f"otherwise-separate associates."
    )
    return PatternMatch(
        pattern_id="NETWORK_HUB",
        pattern_name="Network Hub",
        case_type=GENERAL,
        confidence=confidence,
        triggers_met=triggers,
        plain_explanation=explanation,
        supporting_sources=[],
    )


# Timeline-cluster tuning
_CLUSTER_WINDOW_DAYS = 14
_CLUSTER_MIN_EVENTS = 3
_HIGH_SIGNIFICANCE = ("high", "critical")


def _is_high_sig(ev) -> bool:
    sig = _attr(ev, "significance", "")
    if isinstance(sig, (int, float)):
        return sig >= 7
    return _norm(sig) in _HIGH_SIGNIFICANCE


def rule_timeline_cluster(onto) -> PatternMatch | None:
    """TIMELINE_CLUSTER — a burst of high-significance events in a short window."""
    events = _attr(onto, "timeline_events", []) or []
    dated = sorted(
        ((_parse_date(_attr(ev, "date")), ev) for ev in events if _is_high_sig(ev)),
        key=lambda pair: (pair[0] is None, pair[0] or datetime.min),
    )
    dated = [(d, ev) for d, ev in dated if d is not None]
    if len(dated) < _CLUSTER_MIN_EVENTS:
        return None

    # Sliding window over sorted dates: largest count within the window.
    best_count, best_start, best_end = 0, None, None
    for i in range(len(dated)):
        j = i
        while j < len(dated) and (dated[j][0] - dated[i][0]).days <= _CLUSTER_WINDOW_DAYS:
            j += 1
        count = j - i
        if count > best_count:
            best_count, best_start, best_end = count, dated[i][0], dated[j - 1][0]

    if best_count < _CLUSTER_MIN_EVENTS:
        return None

    window = (f"{best_start.date()}" if best_start == best_end
              else f"{best_start.date()} to {best_end.date()}")
    triggers = [
        f"{best_count} high-significance events within {_CLUSTER_WINDOW_DAYS} days",
        f"window: {window}",
    ]
    span_days = (best_end - best_start).days
    confidence = "STRONG" if (best_count >= 5 and span_days <= 7) else "MODERATE"
    explanation = (
        f"A concentration of significant activity around {window} indicates a "
        f"period of elevated operational tempo."
    )
    return PatternMatch(
        pattern_id="TIMELINE_CLUSTER",
        pattern_name="Timeline Cluster",
        case_type=GENERAL,
        confidence=confidence,
        triggers_met=triggers,
        plain_explanation=explanation,
        supporting_sources=_sources([ev for _, ev in dated]),
    )


# ══════════════════════════════════════════════════════════════════════════════
# Registry — deterministic order (financial → cyber → general)
# ══════════════════════════════════════════════════════════════════════════════
ALL_RULES = [
    rule_layering_structure,
    rule_offshore_flight_risk,
    rule_operational_security,
    rule_shell_layering_network,
    rule_enforcement_history_escalation,
    rule_operational_scale_mismatch,
    rule_anti_forensic_behaviour,
    rule_counter_surveillance,
    rule_network_hub,
    rule_timeline_cluster,
]

RULES_BY_ID = {
    "LAYERING_STRUCTURE": rule_layering_structure,
    "OFFSHORE_FLIGHT_RISK": rule_offshore_flight_risk,
    "OPERATIONAL_SECURITY": rule_operational_security,
    "SHELL_LAYERING_NETWORK": rule_shell_layering_network,
    "ENFORCEMENT_HISTORY_ESCALATION": rule_enforcement_history_escalation,
    "OPERATIONAL_SCALE_MISMATCH": rule_operational_scale_mismatch,
    "ANTI_FORENSIC_BEHAVIOUR": rule_anti_forensic_behaviour,
    "COUNTER_SURVEILLANCE": rule_counter_surveillance,
    "NETWORK_HUB": rule_network_hub,
    "TIMELINE_CLUSTER": rule_timeline_cluster,
}
