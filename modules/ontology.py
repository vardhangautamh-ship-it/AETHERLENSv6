"""
AetherLens — Ontology Layer
Semantic knowledge graph: typed, connected, queryable OSINT objects.
"""

import uuid
import datetime
import json
import hashlib
import dataclasses
from dataclasses import dataclass, field, asdict
from typing import Any, Optional
import networkx as nx
import sqlite3
from pathlib import Path
import re

import config

# ── Relationship Types ────────────────────────────────────────────────────────

RELATIONSHIP_TYPES = {
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
}

# ── Entity Dataclasses ────────────────────────────────────────────────────────

@dataclass
class PersonEntity:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    name_variants: list = field(default_factory=list)
    usernames: dict = field(default_factory=dict)
    emails: list = field(default_factory=list)
    phones: list = field(default_factory=list)
    locations: list = field(default_factory=list)
    age_indicators: list = field(default_factory=list)
    platforms: dict = field(default_factory=dict)
    accounts: list = field(default_factory=list)
    relationships: list = field(default_factory=list)
    risk_score: float = 0.0
    confidence: float = 0.0
    tags: list = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.datetime.utcnow().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.datetime.utcnow().isoformat())
    data_sources: list = field(default_factory=list)
    classification: str = "UNCLASSIFIED"
    entity_type: str = "PERSON"

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclass
class LocationEntity:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    type: str = ""
    coordinates: dict = field(default_factory=dict)
    confidence: float = 0.0
    persons_linked: list = field(default_factory=list)
    events_linked: list = field(default_factory=list)
    data_sources: list = field(default_factory=list)
    entity_type: str = "LOCATION"

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclass
class EventEntity:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    type: str = ""
    timestamp: str = ""
    description: str = ""
    persons_involved: list = field(default_factory=list)
    locations_involved: list = field(default_factory=list)
    platforms_involved: list = field(default_factory=list)
    significance: float = 0.0
    data_sources: list = field(default_factory=list)
    entity_type: str = "EVENT"

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclass
class NetworkEntity:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    type: str = ""
    members: list = field(default_factory=list)
    connections: list = field(default_factory=list)
    strength: float = 0.0
    cluster_id: str = ""
    data_sources: list = field(default_factory=list)
    entity_type: str = "NETWORK"

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclass
class DeviceEntity:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    type: str = ""
    identifier: str = ""
    owner_linked: list = field(default_factory=list)
    locations_linked: list = field(default_factory=list)
    events_linked: list = field(default_factory=list)
    data_sources: list = field(default_factory=list)
    entity_type: str = "DEVICE"

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclass
class AssetEntity:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    asset_type: str = ""        # vehicle | personnel | equipment | property | financial
    identifier: str = ""        # registration no., serial no., account no., etc.
    description: str = ""
    owner_linked: list = field(default_factory=list)
    locations_linked: list = field(default_factory=list)
    value_estimate: str = ""
    confidence: float = 0.0
    data_sources: list = field(default_factory=list)
    tags: list = field(default_factory=list)
    entity_type: str = "ASSET"

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


# ── Risk Scoring Engine ───────────────────────────────────────────────────────

RISK_FACTORS = {
    "multiple_name_variants": {
        "weight": 10,
        "desc": "Multiple name variants suggest identity complexity",
    },
    "recently_created_accounts": {
        "weight": 15,
        "desc": "Accounts created < 6 months ago",
    },
    "inconsistent_locations": {
        "weight": 12,
        "desc": "Conflicting location data across platforms",
    },
    "rapid_network_growth": {
        "weight": 18,
        "desc": "Unusually rapid follower/connection growth",
    },
    "flagged_connections": {
        "weight": 25,
        "desc": "Connections to previously flagged entities",
    },
    "behavioral_anomalies": {
        "weight": 20,
        "desc": "Behavioral anomalies detected in activity",
    },
    "data_gaps": {
        "weight": 8,
        "desc": "Significant data gaps in expected fields",
    },
    "vpn_proxy_indicators": {
        "weight": 15,
        "desc": "VPN or proxy use indicators from IP data",
    },
}


def calculate_risk_score(
    person_entity: PersonEntity,
    ontology_graph: Any = None,
    extra_data: Optional[dict] = None,
) -> dict:
    """
    Calculate a composite risk score for a PersonEntity.

    Returns a dict with risk_score, risk_level, risk_factors breakdown,
    mitigation_notes, and confidence. Does not raise; errors return score=0.
    """
    try:
        extra_data = extra_data or {}
        score = 0.0
        breakdown = []

        now = datetime.datetime.utcnow()
        six_months_ago = now - datetime.timedelta(days=182)

        # multiple_name_variants
        if len(person_entity.name_variants) >= 3:
            factor = RISK_FACTORS["multiple_name_variants"]
            score += factor["weight"]
            breakdown.append({
                "factor": "multiple_name_variants",
                "evidence": f"{len(person_entity.name_variants)} name variants detected",
                "weight": factor["weight"],
                "source": "entity_data",
            })

        # recently_created_accounts
        try:
            recent_accounts = []
            for account in person_entity.accounts:
                # accounts may be plain strings (platform names) or dicts with join info
                if isinstance(account, dict):
                    join_year = account.get("join_year")
                    join_date_str = account.get("join_date", "")
                    if join_year is not None:
                        try:
                            join_dt = datetime.datetime(int(join_year), 1, 1)
                            if join_dt >= six_months_ago:
                                recent_accounts.append(account)
                        except (ValueError, TypeError):
                            pass
                    elif join_date_str:
                        try:
                            join_dt = datetime.datetime.fromisoformat(str(join_date_str))
                            if join_dt >= six_months_ago:
                                recent_accounts.append(account)
                        except (ValueError, TypeError):
                            pass
            if recent_accounts:
                factor = RISK_FACTORS["recently_created_accounts"]
                score += factor["weight"]
                breakdown.append({
                    "factor": "recently_created_accounts",
                    "evidence": f"{len(recent_accounts)} account(s) created within the last 6 months",
                    "weight": factor["weight"],
                    "source": "account_data",
                })
        except Exception:
            pass

        # inconsistent_locations
        try:
            unique_locations = set(person_entity.locations)
            if len(unique_locations) >= 3:
                factor = RISK_FACTORS["inconsistent_locations"]
                score += factor["weight"]
                breakdown.append({
                    "factor": "inconsistent_locations",
                    "evidence": f"{len(unique_locations)} distinct locations recorded: {', '.join(list(unique_locations)[:5])}",
                    "weight": factor["weight"],
                    "source": "location_data",
                })
        except Exception:
            pass

        # rapid_network_growth
        if extra_data.get("rapid_growth", False):
            factor = RISK_FACTORS["rapid_network_growth"]
            score += factor["weight"]
            breakdown.append({
                "factor": "rapid_network_growth",
                "evidence": "Rapid network growth flag set in supplemental data",
                "weight": factor["weight"],
                "source": "behavioral_data",
            })

        # flagged_connections
        if ontology_graph is not None:
            try:
                flagged_neighbors = []
                if person_entity.id in ontology_graph.graph:
                    for neighbor_id in ontology_graph.graph.successors(person_entity.id):
                        neighbor_entity = ontology_graph._entities.get(neighbor_id)
                        if neighbor_entity is not None:
                            neighbor_risk = getattr(neighbor_entity, "risk_score", 0.0)
                            if neighbor_risk > 70:
                                neighbor_label = getattr(neighbor_entity, "name", neighbor_id)
                                flagged_neighbors.append(neighbor_label)
                    for neighbor_id in ontology_graph.graph.predecessors(person_entity.id):
                        neighbor_entity = ontology_graph._entities.get(neighbor_id)
                        if neighbor_entity is not None:
                            neighbor_risk = getattr(neighbor_entity, "risk_score", 0.0)
                            if neighbor_risk > 70:
                                neighbor_label = getattr(neighbor_entity, "name", neighbor_id)
                                if neighbor_label not in flagged_neighbors:
                                    flagged_neighbors.append(neighbor_label)
                if flagged_neighbors:
                    factor = RISK_FACTORS["flagged_connections"]
                    score += factor["weight"]
                    breakdown.append({
                        "factor": "flagged_connections",
                        "evidence": f"Connected to {len(flagged_neighbors)} high-risk entity(ies): {', '.join(str(n) for n in flagged_neighbors[:5])}",
                        "weight": factor["weight"],
                        "source": "graph_analysis",
                    })
            except Exception:
                pass

        # behavioral_anomalies
        behavioral_flags = extra_data.get("behavioral_flags", [])
        if behavioral_flags:
            factor = RISK_FACTORS["behavioral_anomalies"]
            score += factor["weight"]
            breakdown.append({
                "factor": "behavioral_anomalies",
                "evidence": f"{len(behavioral_flags)} behavioral flag(s): {', '.join(str(f) for f in behavioral_flags[:5])}",
                "weight": factor["weight"],
                "source": "behavioral_analysis",
            })

        # data_gaps
        if not person_entity.data_sources or len(person_entity.data_sources) < 2:
            factor = RISK_FACTORS["data_gaps"]
            score += factor["weight"]
            breakdown.append({
                "factor": "data_gaps",
                "evidence": f"Only {len(person_entity.data_sources)} data source(s) available; cross-validation not possible",
                "weight": factor["weight"],
                "source": "data_quality",
            })

        # vpn_proxy_indicators
        if extra_data.get("vpn_detected", False):
            factor = RISK_FACTORS["vpn_proxy_indicators"]
            score += factor["weight"]
            breakdown.append({
                "factor": "vpn_proxy_indicators",
                "evidence": "VPN or proxy use detected in IP analysis",
                "weight": factor["weight"],
                "source": "ip_analysis",
            })

        # Clamp score to 0-100
        score = max(0.0, min(100.0, score))

        # Risk level thresholds
        if score <= 25:
            risk_level = "LOW"
        elif score <= 50:
            risk_level = "MEDIUM"
        elif score <= 75:
            risk_level = "HIGH"
        else:
            risk_level = "CRITICAL"

        # Mitigation notes
        if risk_level == "LOW":
            mitigation_notes = "No immediate action required. Continue routine monitoring."
        elif risk_level == "MEDIUM":
            mitigation_notes = "Increase monitoring frequency. Cross-reference data sources to resolve gaps."
        elif risk_level == "HIGH":
            mitigation_notes = "Escalate for analyst review. Investigate flagged connections and behavioral anomalies."
        else:
            mitigation_notes = "CRITICAL — Immediate escalation required. Full investigation warranted."

        # Confidence based on number of sources
        confidence = min(1.0, len(person_entity.data_sources) / 5.0)

        return {
            "risk_score": round(score, 2),
            "risk_level": risk_level,
            "risk_factors": breakdown,
            "mitigation_notes": mitigation_notes,
            "confidence": round(confidence, 2),
        }

    except Exception as exc:
        return {
            "risk_score": 0.0,
            "risk_level": "LOW",
            "risk_factors": [],
            "mitigation_notes": f"Risk scoring failed: {exc}",
            "confidence": 0.0,
        }


# ── Ontology Graph ────────────────────────────────────────────────────────────

class OntologyGraph:
    """
    Core semantic knowledge graph for AetherLens OSINT intelligence.
    Backed by a NetworkX MultiDiGraph; persists to SQLite via config.DATABASE_PATH.
    """

    def __init__(self):
        self.graph: nx.MultiDiGraph = nx.MultiDiGraph()
        self._entities: dict = {}

    # ── Entity management ─────────────────────────────────────────────────────

    def add_entity(self, entity: Any) -> None:
        """Add any entity dataclass to the graph. Silently overwrites on duplicate id."""
        try:
            entity_id = entity.id
            self._entities[entity_id] = entity

            # Build node attribute dict from entity fields
            attrs = entity.to_dict()
            attrs["node_type"] = entity.entity_type

            # Use 'identifier' as label for DeviceEntity/AssetEntity (when name absent), 'name' for others
            if entity.entity_type == "DEVICE":
                attrs["label"] = getattr(entity, "identifier", entity_id)
            elif entity.entity_type == "ASSET":
                attrs["label"] = getattr(entity, "name", None) or getattr(entity, "identifier", entity_id)
            else:
                attrs["label"] = getattr(entity, "name", entity_id)

            self.graph.add_node(entity_id, **attrs)
        except Exception:
            pass

    def add_relationship(
        self,
        entity_a_id: str,
        entity_b_id: str,
        relationship_type: str,
        strength: float = 0.5,
        evidence: Optional[list] = None,
        data_source: str = "",
    ) -> None:
        """Add a directed edge between two entity nodes."""
        try:
            if relationship_type not in RELATIONSHIP_TYPES:
                relationship_type = "ASSOCIATED_WITH"

            edge_attrs = {
                "type": relationship_type,
                "strength": float(strength),
                "evidence": evidence if isinstance(evidence, list) else [],
                "created_at": datetime.datetime.utcnow().isoformat(),
                "data_source": str(data_source),
            }

            # Ensure both nodes exist before adding edge
            if entity_a_id not in self.graph:
                self.graph.add_node(entity_a_id)
            if entity_b_id not in self.graph:
                self.graph.add_node(entity_b_id)

            self.graph.add_edge(entity_a_id, entity_b_id, **edge_attrs)
        except Exception:
            pass

    # ── Query ─────────────────────────────────────────────────────────────────

    def query(
        self,
        entity_type: Optional[str] = None,
        relationship_type: Optional[str] = None,
        min_strength: float = 0.0,
        tags: Optional[list] = None,
    ) -> dict:
        """
        Filter entities and relationships by type, tags, and edge strength.
        Returns {"entities": [...], "relationships": [...]}.
        """
        try:
            # Filter entities
            matched_entities = list(self._entities.values())

            if entity_type is not None:
                matched_entities = [
                    e for e in matched_entities
                    if getattr(e, "entity_type", "") == entity_type
                ]

            if tags:
                matched_entities = [
                    e for e in matched_entities
                    if any(t in getattr(e, "tags", []) for t in tags)
                ]

            entity_dicts = [e.to_dict() for e in matched_entities]

            # Filter edges
            edge_list = []
            for u, v, key, data in self.graph.edges(data=True, keys=True):
                edge_strength = data.get("strength", 0.0)
                edge_rel_type = data.get("type", "")

                if relationship_type is not None and edge_rel_type != relationship_type:
                    continue
                if edge_strength < min_strength:
                    continue

                edge_list.append({
                    "from": u,
                    "to": v,
                    "key": key,
                    **data,
                })

            return {"entities": entity_dicts, "relationships": edge_list}

        except Exception:
            return {"entities": [], "relationships": []}

    # ── Path finding ──────────────────────────────────────────────────────────

    def find_hidden_connections(self, entity_a_id: str, entity_b_id: str) -> dict:
        """
        Discover indirect connections between two entities up to 3 hops.
        Returns shortest_path, all_paths (up to 10), degrees, and found flag.
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
            found = len(shortest) > 0

            return {
                "shortest_path": shortest,
                "all_paths": all_paths,
                "degrees": degrees,
                "found": found,
            }
        except Exception:
            return {
                "found": False,
                "shortest_path": [],
                "all_paths": [],
                "degrees": 0,
            }

    # ── Entity summary ────────────────────────────────────────────────────────

    def get_entity_summary(self, entity_id: str) -> dict:
        """
        Return a full summary for a single entity: its data, connected entities,
        all relationships, risk_score, and confidence.
        """
        try:
            entity = self._entities.get(entity_id)
            entity_dict = entity.to_dict() if entity else {}

            # Connected neighbors (successors + predecessors)
            connected_ids = set()
            if entity_id in self.graph:
                connected_ids.update(self.graph.successors(entity_id))
                connected_ids.update(self.graph.predecessors(entity_id))

            connected_entities = []
            for cid in connected_ids:
                neighbour = self._entities.get(cid)
                if neighbour is not None:
                    connected_entities.append(neighbour.to_dict())
                else:
                    # Node exists in graph but no entity object
                    connected_entities.append({"id": cid})

            # All edges touching this node
            relationships = []
            if entity_id in self.graph:
                for u, v, key, data in self.graph.edges(entity_id, data=True, keys=True):
                    relationships.append({"from": u, "to": v, "key": key, **data})
                for u, v, key, data in self.graph.in_edges(entity_id, data=True, keys=True):
                    relationships.append({"from": u, "to": v, "key": key, **data})

            # Deduplicate edges by converting to frozenset of items
            seen = set()
            deduped = []
            for rel in relationships:
                fingerprint = (rel.get("from"), rel.get("to"), rel.get("key"))
                if fingerprint not in seen:
                    seen.add(fingerprint)
                    deduped.append(rel)

            risk_score = entity_dict.get("risk_score", 0.0)
            confidence = entity_dict.get("confidence", 0.0)

            return {
                "entity": entity_dict,
                "connected_entities": connected_entities,
                "relationships": deduped,
                "risk_score": risk_score,
                "confidence": confidence,
            }
        except Exception:
            return {
                "entity": {},
                "connected_entities": [],
                "relationships": [],
                "risk_score": 0.0,
                "confidence": 0.0,
            }

    # ── Import / Export ───────────────────────────────────────────────────────

    def export_graph_json(self) -> dict:
        """
        Return a JSON-serializable dict representing the entire graph.
        """
        try:
            entities_out = {}
            for eid, entity in self._entities.items():
                entities_out[eid] = entity.to_dict()

            edges_out = []
            for u, v, key, data in self.graph.edges(data=True, keys=True):
                edges_out.append({"from": u, "to": v, "key": key, **data})

            return {
                "entities": entities_out,
                "edges": edges_out,
                "node_count": self.graph.number_of_nodes(),
                "edge_count": self.graph.number_of_edges(),
                "exported_at": datetime.datetime.utcnow().isoformat(),
            }
        except Exception:
            return {"entities": {}, "edges": [], "node_count": 0, "edge_count": 0, "exported_at": datetime.datetime.utcnow().isoformat()}

    def import_graph_json(self, data: dict) -> None:
        """
        Reconstruct the graph from an export_graph_json payload.
        Detects entity_type and instantiates the correct dataclass.
        """
        _ENTITY_MAP = {
            "PERSON":   PersonEntity,
            "LOCATION": LocationEntity,
            "EVENT":    EventEntity,
            "NETWORK":  NetworkEntity,
            "DEVICE":   DeviceEntity,
            "ASSET":    AssetEntity,
        }

        try:
            entities_raw = data.get("entities", {})
            for eid, edata in entities_raw.items():
                try:
                    etype = edata.get("entity_type", "PERSON")
                    cls = _ENTITY_MAP.get(etype, PersonEntity)
                    # Only pass fields that the dataclass accepts
                    valid_fields = {f.name for f in dataclasses.fields(cls)}
                    filtered = {k: v for k, v in edata.items() if k in valid_fields}
                    entity_obj = cls(**filtered)
                    self.add_entity(entity_obj)
                except Exception:
                    continue

            for edge in data.get("edges", []):
                try:
                    from_id = edge.get("from", "")
                    to_id = edge.get("to", "")
                    rel_type = edge.get("type", "ASSOCIATED_WITH")
                    strength = edge.get("strength", 0.5)
                    evidence = edge.get("evidence", [])
                    data_source = edge.get("data_source", "")
                    self.add_relationship(from_id, to_id, rel_type, strength, evidence, data_source)
                except Exception:
                    continue
        except Exception:
            pass

    # ── Plotly Visualisation ──────────────────────────────────────────────────

    def to_plotly_figure(self, filter_type: Optional[str] = None, min_risk: float = 0.0):
        """
        Render the knowledge graph as a Plotly Figure.
        Nodes are colour-coded by entity_type; size reflects risk_score.
        Returns a plotly.graph_objects.Figure.
        """
        import plotly.graph_objects as go

        _TYPE_COLOURS = {
            "PERSON":   "#FF6B6B",
            "LOCATION": "#4ECDC4",
            "EVENT":    "#FFE66D",
            "NETWORK":  "#A8E6CF",
            "DEVICE":   "#C9B1FF",
            "ASSET":    "#FFB347",
        }

        # Filter entities
        visible_ids = set()
        for eid, entity in self._entities.items():
            etype = getattr(entity, "entity_type", "PERSON")
            erisk = getattr(entity, "risk_score", 0.0)
            if filter_type and etype != filter_type:
                continue
            if erisk < min_risk:
                continue
            visible_ids.add(eid)

        # Layout
        subgraph = self.graph.subgraph(visible_ids) if visible_ids else self.graph
        if len(subgraph.nodes) == 0:
            fig = go.Figure()
            fig.update_layout(title="Knowledge Graph (empty)")
            return fig

        try:
            pos = nx.spring_layout(subgraph, seed=42)
        except Exception:
            pos = {n: (i, 0) for i, n in enumerate(subgraph.nodes)}

        # Edge traces
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

        # Node traces grouped by entity_type
        node_traces = []
        grouped: dict = {}
        for nid in subgraph.nodes():
            entity = self._entities.get(nid)
            etype = getattr(entity, "entity_type", "PERSON") if entity else "PERSON"
            grouped.setdefault(etype, []).append(nid)

        for etype, nids in grouped.items():
            nx_list, ny_list, labels, sizes = [], [], [], []
            for nid in nids:
                x, y = pos.get(nid, (0, 0))
                nx_list.append(x)
                ny_list.append(y)
                entity = self._entities.get(nid)
                label = getattr(entity, "name", nid) if entity else nid
                risk  = getattr(entity, "risk_score", 0.0) if entity else 0.0
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
                text=[getattr(self._entities.get(n), "name", n) if self._entities.get(n) else n
                      for n in nids],
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
        Creates the table if it does not exist. Returns True on success.
        """
        try:
            db_path = str(config.DATABASE_PATH)
            payload = self.export_graph_json()
            json_str = json.dumps(payload, default=str)
            saved_at = datetime.datetime.utcnow().isoformat()

            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS ontology_graph (
                        id      TEXT PRIMARY KEY,
                        data    TEXT,
                        saved_at TEXT
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO ontology_graph (id, data, saved_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET data=excluded.data, saved_at=excluded.saved_at
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
        Returns True on success, False on failure.
        """
        try:
            db_path = str(config.DATABASE_PATH)
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS ontology_graph (
                        id      TEXT PRIMARY KEY,
                        data    TEXT,
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

            payload = json.loads(row[0])
            self.import_graph_json(payload)
            return True
        except Exception:
            return False


# ── Digital Twin Builder ──────────────────────────────────────────────────────

def build_digital_twin(all_data_sources: dict, raw_documents: list = None) -> OntologyGraph:
    """
    Takes all ingested data (person, search_results, graph_data, timeline_data,
    behavioral_data) plus optional raw_documents list from Fusion mode.
    Creates unified entity objects, resolves same-person matches,
    builds relationship graph, calculates risk scores, and returns a complete
    OntologyGraph.

    raw_documents: list of dicts from ingest_file() — each may contain asset rows
                   with asset_type, name, identifier, description fields.

    Does not raise; errors in individual sub-steps are caught and skipped.
    """
    graph = OntologyGraph()

    try:
        person = all_data_sources.get("person", {}) or {}
        search = all_data_sources.get("search_results", {}) or {}
        tl     = all_data_sources.get("timeline_data", {}) or {}
        behav  = all_data_sources.get("behavioral_data", {}) or {}

        # ── Primary PersonEntity ──────────────────────────────────────────────
        # Strip newline artifacts before storing — e.g. "Zafar Ahmed Khan\nCase"
        # from PDF/txt text extraction can reach confirmed_name on local-fallback runs.
        _raw_name = person.get("confirmed_name", "Unknown") or "Unknown"
        _clean_name = _raw_name.replace("\n", " ").replace("\r", " ").strip() or "Unknown"
        pe = PersonEntity(
            name=_clean_name,
            name_variants=person.get("name_variants", []),
            usernames=person.get("usernames", {}),
            emails=person.get("emails_found", []),
            phones=person.get("phones_found", []),
            locations=person.get("location_stated", []),
            platforms=person.get("profile_urls", {}),
            accounts=list(person.get("join_dates", {}).keys()),
            confidence=float(person.get("confidence_score", 0)),
            data_sources=person.get("data_sources", []),
            classification="RESTRICTED",
        )

        extra = {
            "behavioral_flags": (behav or {}).get("assessment", {}).get("behavioral_flags", []),
        }
        risk_result = calculate_risk_score(pe, graph, extra)
        pe.risk_score = risk_result["risk_score"]
        graph.add_entity(pe)

        # ── LocationEntity for each stated location ───────────────────────────
        for loc in person.get("location_stated", []):
            try:
                le = LocationEntity(
                    name=str(loc),
                    type="region",
                    confidence=0.6,
                    persons_linked=[pe.id],
                )
                graph.add_entity(le)
                graph.add_relationship(
                    pe.id,
                    le.id,
                    "LOCATED_AT",
                    strength=0.6,
                    evidence=[f"Stated location: {loc}"],
                    data_source="profile_data",
                )
            except Exception:
                continue

        # ── EventEntity for timeline events ──────────────────────────────────
        for ev in (tl or {}).get("events", [])[:20]:
            try:
                ee = EventEntity(
                    type="activity",
                    timestamp=ev.get("normalized", ""),
                    description=str(ev.get("context", ""))[:200],
                    persons_involved=[pe.id],
                    locations_involved=[],
                    platforms_involved=[ev.get("source", "")],
                    significance=0.5,
                    data_sources=[ev.get("source", "")],
                )
                graph.add_entity(ee)
                graph.add_relationship(
                    pe.id,
                    ee.id,
                    "PARTICIPATED_IN",
                    strength=0.7,
                    evidence=[str(ev.get("context", ""))[:100]],
                    data_source=ev.get("source", ""),
                )
            except Exception:
                continue

        # ── NetworkEntity for confirmed linked profiles ───────────────────────
        linked = person.get("confirmed_linked_profiles", [])
        if linked:
            try:
                ne = NetworkEntity(
                    type="social",
                    members=[pe.id] + [c.get("url", "") for c in linked],
                    connections=[c.get("url", "") for c in linked],
                    strength=0.9,
                    cluster_id=f"cluster_{pe.id[:8]}",
                    data_sources=["cross_platform_discovery"],
                )
                graph.add_entity(ne)
                graph.add_relationship(
                    pe.id,
                    ne.id,
                    "MEMBER_OF",
                    strength=0.9,
                    evidence=["Confirmed linked accounts"],
                    data_source="cross_platform_discovery",
                )
            except Exception:
                pass

        # ── AssetEntity from raw_documents ────────────────────────────────────
        _ASSET_TYPE_HINTS = {
            "vehicle":    ["vehicle", "car", "truck", "bike", "registration", "reg_no",
                           "chassis", "engine_no", "make", "model"],
            "property":   ["property", "plot", "flat", "house", "address", "land", "survey"],
            "financial":  ["account", "bank", "ifsc", "pan", "credit", "debit", "wallet"],
            "equipment":  ["equipment", "device", "laptop", "phone", "imei", "serial"],
            "personnel":  ["employee", "personnel", "staff", "designation", "department"],
        }

        for doc in (raw_documents or []):
            doc_filename = doc.get("filename", doc.get("name", "document"))
            for row in doc.get("structured_rows", []):
                if not isinstance(row, dict):
                    continue
                row_lower = {k.lower(): v for k, v in row.items()}

                # Detect asset type from column names
                detected_type = ""
                for atype, hints in _ASSET_TYPE_HINTS.items():
                    if any(h in k for h in hints for k in row_lower):
                        detected_type = atype
                        break
                if not detected_type:
                    continue

                # Build AssetEntity from row fields
                name_val   = (row_lower.get("name") or row_lower.get("vehicle_name")
                              or row_lower.get("asset_name") or row_lower.get("model", ""))
                ident_val  = (row_lower.get("registration") or row_lower.get("reg_no")
                              or row_lower.get("identifier") or row_lower.get("serial_no")
                              or row_lower.get("account_no") or row_lower.get("imei", ""))
                desc_parts = [f"{k}: {v}" for k, v in list(row.items())[:6] if v and str(v) not in ("", "None", "nan")]
                desc_val   = " | ".join(desc_parts)[:300]

                if not name_val and not ident_val:
                    continue

                try:
                    ae = AssetEntity(
                        name=str(name_val or ident_val or detected_type.title()),
                        asset_type=detected_type,
                        identifier=str(ident_val),
                        description=desc_val,
                        owner_linked=[pe.id],
                        confidence=0.7,
                        data_sources=[doc_filename],
                        tags=[detected_type],
                    )
                    graph.add_entity(ae)
                    graph.add_relationship(
                        pe.id,
                        ae.id,
                        "OWNS_ASSET",
                        strength=0.7,
                        evidence=[f"Asset found in document: {doc_filename}"],
                        data_source=doc_filename,
                    )
                except Exception:
                    continue

    except Exception:
        pass

    return graph
