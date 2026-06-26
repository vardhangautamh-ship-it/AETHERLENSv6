"""
AetherLens — Relationship Mapper Module
NetworkX graph construction + Plotly interactive visualization.
"""

import json
import networkx as nx
import plotly.graph_objects as go

import re as _re
from modules.entity_resolution import _NAME_SUFFIX_WORDS, RE_PERSON_NAME_CELL

EDGE_TYPES = ["called", "located_near", "co_appears", "follows", "mentioned_with",
              "contacted", "co_located", "associated_with"]

EDGE_COLORS = {
    "called":          "#DC2626",   # --crit
    "located_near":    "#2563EB",   # --info
    "co_appears":      "#9D4EDD",   # --p500
    "follows":         "#16A34A",   # --online
    "mentioned_with":  "#D97706",   # --warn
    "contacted":       "#D97706",   # --warn
    "co_located":      "#0D9488",   # --teal
    "associated_with": "#C084FC",   # --p300
}

# Pattern to identify real person names in data rows. Single source of truth
# lives in entity_resolution (RE_PERSON_NAME_CELL); aliased here for callers.
_REAL_NAME_RE = RE_PERSON_NAME_CELL

_NAME_SKIP = {
    "Unknown", "Not found", "N/A", "None", "Null", "True", "False",
    "Location Timeline", "Date Time", "City State", "Activity Type",
    "Work Entry", "NexaTech", "HIGH", "MEDIUM", "LOW",
}

# Words that appear in Indian location/infrastructure names but never in person names.
# Any candidate name containing one of these tokens is rejected as a location string.
_LOCATION_INDICATOR_WORDS = {
    "bank", "branch", "point", "link", "bridge", "sea", "bay", "port",
    "station", "airport", "highway", "flyover", "junction", "naka",
    "tower", "plaza", "mall", "complex", "centre", "center",
    "park", "garden", "market", "masjid", "mandir", "chowk",
    "marg", "road", "street", "lane", "avenue", "boulevard",
    "bandra", "worli", "nariman", "andheri", "borivali",
    "colaba", "kurla", "dharavi", "dadar", "fort", "linking",
    "sector", "phase", "block", "zone", "wing", "floor",
    # Major Indian city names — never appear in a real person's name.
    # Prevents challan-data artifacts like "Mumbai Speeding" from passing
    # _is_real_name() and entering the relationship graph as person nodes.
    "mumbai", "delhi", "chennai", "kolkata", "hyderabad", "bengaluru",
    "pune", "nashik", "nagpur", "ahmedabad", "surat", "jaipur", "lucknow",
    # Traffic / legal violation terms that look like proper nouns in CSV data
    "speeding", "overspeed", "overloading", "violation", "challan",
}


def _is_real_name(val: str) -> bool:
    """Return True if val looks like a real person name (not a location or infrastructure label)."""
    val = str(val).strip()
    if not val or val in _NAME_SKIP or len(val) < 4:
        return False
    if not _REAL_NAME_RE.match(val):
        return False
    # Reject strings that contain any location/infrastructure indicator word
    val_lower = val.lower()
    for word in val_lower.split():
        if word in _LOCATION_INDICATOR_WORDS:
            return False
    return True


def extract_relationships_from_structured_rows(
    rows: list[dict],
    source_name: str = "document",
) -> tuple[list[dict], list[dict]]:
    """
    Scan structured data rows (from CSV/Excel) and extract entities + relationships.

    Detects:
    - caller_name -> CONTACTED -> receiver_name  (call records)
    - Two subjects at same location+date        (CO_LOCATED)
    - Notes containing "met", "with", "observed", "also present", "contact:", "associate:"
      -> ASSOCIATED_WITH

    Returns (entities, relationships) in the same format as build_graph().
    """
    entities:      list[dict] = []
    relationships: list[dict] = []
    seen_nodes:    set         = set()

    def _add_node(name: str, ntype: str = "person"):
        nid = f"person:{name}"
        if nid not in seen_nodes:
            entities.append({"id": nid, "label": name, "type": ntype})
            seen_nodes.add(nid)
        return nid

    # Column name normalisation helpers
    def _find_col(row: dict, *candidates) -> str:
        for c in candidates:
            if c in row:
                return str(row[c]).strip()
        return ""

    # ── Pass 1: call records ─────────────────────────────────────────────────
    # Collect per (caller, receiver) -> total duration, dates, locations
    call_agg: dict = {}   # (caller, receiver) -> {duration, dates, locs}

    for row in rows:
        caller   = _find_col(row, "caller_name", "caller", "from_name", "from")
        receiver = _find_col(row, "receiver_name", "receiver", "to_name", "to")
        if not (_is_real_name(caller) and _is_real_name(receiver)):
            continue
        if caller == receiver:
            continue  # self-call — flagged in anomalies, not as relationship

        key = (caller, receiver)
        if key not in call_agg:
            call_agg[key] = {"duration": 0.0, "dates": [], "locs": []}

        # Duration
        dur_raw = _find_col(row, "duration_seconds", "duration", "call_duration", "duration_sec")
        try:
            call_agg[key]["duration"] += float(dur_raw)
        except (ValueError, TypeError):
            call_agg[key]["duration"] += 1.0

        # Date evidence
        date_val = _find_col(row, "date", "date_time", "call_date", "timestamp", "time")
        if date_val and date_val not in call_agg[key]["dates"]:
            call_agg[key]["dates"].append(date_val[:20])

        # Location evidence
        loc_val = _find_col(row, "location", "city", "city_state", "place", "area")
        if loc_val and loc_val not in call_agg[key]["locs"]:
            call_agg[key]["locs"].append(loc_val[:30])

    for (caller, receiver), agg in call_agg.items():
        caller_id   = _add_node(caller)
        receiver_id = _add_node(receiver)
        weight = max(1, int(agg["duration"] / 60))
        evidence_str = ""
        if agg["dates"]:
            evidence_str += f"Dates: {', '.join(agg['dates'][:3])}"
        if agg["locs"]:
            evidence_str += f" | Locations: {', '.join(agg['locs'][:3])}"
        relationships.append({
            "source":  caller_id,
            "target":  receiver_id,
            "type":    "contacted",
            "weight":  weight,
            "detail":  evidence_str.strip() or f"Call record — {source_name}",
        })

    # ── Pass 2: co-location (same location + same date, different subjects) ──
    # Build (location, date) -> set of subjects
    coloc: dict = {}
    for row in rows:
        subj = _find_col(row, "subject", "name", "person", "subject_name")
        if not _is_real_name(subj):
            subj = _find_col(row, "caller_name", "caller")
        if not _is_real_name(subj):
            continue
        loc  = _find_col(row, "location", "city", "city_state", "place", "area")
        date = _find_col(row, "date", "date_time", "timestamp")[:10] if _find_col(row, "date", "date_time", "timestamp") else ""
        if loc and date:
            coloc_key = (loc.strip()[:40], date.strip()[:10])
            coloc.setdefault(coloc_key, set()).add(subj)

    for (loc, date), subjects in coloc.items():
        subj_list = list(subjects)
        if len(subj_list) < 2:
            continue
        for i in range(len(subj_list)):
            for j in range(i + 1, len(subj_list)):
                a_id = _add_node(subj_list[i])
                b_id = _add_node(subj_list[j])
                relationships.append({
                    "source": a_id,
                    "target": b_id,
                    "type":   "co_located",
                    "weight": 2,
                    "detail": f"Same location '{loc}' on {date}",
                })

    # ── Pass 3: notes / activity text ────────────────────────────────────────
    _ASSOC_PATTERNS = _re.compile(
        r"(?:also present|observed|met with|in contact with|associate:|contact:|"
        r"\bwith\b|accompanied by)",
        _re.IGNORECASE,
    )
    _MENTION_NAME_RE = _re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})\b")

    for row in rows:
        note = _find_col(row, "notes", "note", "activity", "activity_log",
                         "description", "remarks", "comments")
        if not note or not _ASSOC_PATTERNS.search(note):
            continue
        mentioned = [m.group(1) for m in _MENTION_NAME_RE.finditer(note)
                     if _is_real_name(m.group(1))]
        if len(mentioned) < 2:
            continue
        # First name is typically the subject, rest are associated
        subj_id = _add_node(mentioned[0])
        for assoc in mentioned[1:]:
            assoc_id = _add_node(assoc)
            relationships.append({
                "source": subj_id,
                "target": assoc_id,
                "type":   "associated_with",
                "weight": 1,
                "detail": note[:100],
            })

    return entities, relationships


# ── Graph building ─────────────────────────────────────────────────────────────

_SENTENCE_WORDS = [
    "subject encountered", "during surveillance", "is known hawala",
    "assessment subject", "field officer assessment", "no rows provided",
    "unable to identify", "surveillance was", "officer report",
    "intelligence note", "case ref", "data completeness",
]

INVALID_NODE_PATTERNS = [
    # Document heading artifacts
    "zero data", "zero data entry", "null entry", "empty",
    "empty data", "empty profile", "empty doc", "no data",
    "sample document", "field note", "intelligence file",
    "restricted", "aetherlens",
    # Test artifacts
    "test1", "test2", "test3", "retest",
    "val01", "val02", "val03", "val04",
    # Generic document words
    "document", "report", "profile", "background",
    "source a", "source b", "source c", "source d",
    "unknown subject", "unknown",
    # Academic course names — appear in student/university docs
    "constitutional law", "criminal procedure", "cyber law",
    "jurisprudence", "moot court", "law of contract",
    "administrative law", "legal drafting", "alternative dispute",
    "family law", "evidence act", "company law",
    # ISP / account identifiers
    "jiofiber", "jiofiber-ggn", "jio fiber",
    "alg/llb", "alg / llb",
    # Enrollment-number substrings
    "llb/2022", "llb/2023", "llb/2024",
    "lllb/2022", "lllb/2023",
    "ba.llb", "b.a.llb",
]

_FILE_EXTENSIONS = (".csv", ".pdf", ".txt", ".xlsx", ".xls", ".json", ".docx")


def is_valid_node_name(name: str) -> bool:
    """Return True only if name is a clean entity label (person/place/platform/org)."""
    if not name:
        return False
    s = str(name).strip()
    # Reject multi-line strings — text-extraction artifacts like "Zafar Ahmed Khan\nCase"
    if "\n" in s or "\r" in s:
        return False
    if len(s) > 60:
        return False
    if len(s) < 3:
        return False

    sl = s.lower()

    # Reject list objects that became strings — e.g. "['JIOFIBER', 'ALG']"
    if sl.startswith("[") or sl.startswith("{"):
        return False

    # Reject enrollment-number formats — e.g. "ALG/LLB/2022/001", "BA/LLB/2023"
    if _re.match(r'^[A-Za-z]+/[A-Za-z]+/\d{4}', s):
        return False

    # Reject ISP account-ID formats — e.g. "1234/JIO/2022/00123"
    if _re.match(r'^\d{4}/\w+/\d{4}/', s):
        return False

    # Reject pure-numeric strings
    if _re.match(r'^\d+$', sl):
        return False

    # Reject anything that looks like a filename
    if any(sl.endswith(ext) for ext in _FILE_EXTENSIONS):
        return False

    # Reject all-digits / digits+separators
    if _re.match(r'^[\d\s\-_]+$', sl):
        return False

    # Reject paragraph-text sentences
    for w in _SENTENCE_WORDS:
        if w in sl:
            return False

    # Reject known document-artifact and course-name patterns
    for pattern in INVALID_NODE_PATTERNS:
        if sl == pattern or pattern in sl:
            return False

    return True


def get_or_create_node(G: nx.DiGraph, name: str, node_type: str = "person") -> str:
    """
    Return node_id for an existing node with the same normalized name,
    or create a new one. Prevents duplicate nodes for the same real-world entity.
    Rejects paragraph text and document titles as node names.
    """
    if not is_valid_node_name(name):
        print(f"[GRAPH] Skipping invalid node: {repr(str(name)[:50])}")
        return ""          # caller must check for empty string
    # Strip trailing descriptor words ("Zafar Ahmed Khan Case" → "Zafar Ahmed Khan")
    parts = str(name).strip().split()
    while parts and parts[-1].lower() in _NAME_SUFFIX_WORDS:
        parts = parts[:-1]
    clean_name = " ".join(parts) if parts else str(name).strip()
    normalized = clean_name.lower()
    for node_id in G.nodes():
        existing = str(G.nodes[node_id].get("label", node_id)).strip().lower()
        if existing == normalized:
            # Increment mention counter each time this node is referenced
            G.nodes[node_id]["mention_count"] = G.nodes[node_id].get("mention_count", 0) + 1
            return node_id
    # New node — mention_count starts at 1
    G.add_node(clean_name, label=clean_name, node_type=node_type, mention_count=1)
    return clean_name


def normalize_name(name: str) -> str:
    """Normalise a name/label for deduplication comparison."""
    return str(name).strip().lower().replace("  ", " ")


def build_graph(entities: list[dict], relationships: list[dict]) -> nx.DiGraph:
    """
    Build a directed weighted graph.

    entities:      [{"id": "node_id", "label": "Display Name", "type": "person|org|location"}]
    relationships: [{"source": "id", "target": "id", "type": edge_type, "weight": int, "detail": "..."}]

    Uses get_or_create_node() to prevent duplicate nodes for the same real-world entity.
    Builds an id_to_node map so relationship source/target IDs (which may be like
    "person:Salim Qureshi") are always resolved to the canonical deduplicated node.
    """
    G = nx.DiGraph()

    # id_to_node: maps every known identifier (original id, label, resolved node_id)
    # -> the canonical node_id in G
    id_to_node: dict[str, str] = {}

    for e in entities:
        label   = e.get("label", "") or e.get("id", "")
        eid_raw = e.get("id", "")
        etype   = e.get("type", "person")

        # get_or_create deduplicates by normalised label; returns "" for invalid names
        node_id = get_or_create_node(G, label, etype)
        if not node_id:
            continue   # skip paragraph text / document titles

        # Register all ways this entity might be referenced in relationships
        id_to_node[eid_raw]  = node_id   # original id key  (e.g. "person:Salim Qureshi")
        id_to_node[label]    = node_id   # label key         (e.g. "Salim Qureshi")
        id_to_node[node_id]  = node_id   # resolved id (self)
        e["_resolved_id"]    = node_id

    def _resolve(raw: str) -> str:
        """Resolve a raw relationship endpoint to its canonical node_id."""
        if not raw:
            return raw
        if raw in id_to_node:
            return id_to_node[raw]
        # Last resort: get_or_create (creates a new node for unknown entities)
        node_id = get_or_create_node(G, raw, "unknown")
        id_to_node[raw] = node_id
        return node_id

    for r in relationships:
        src_raw = r.get("source", "")
        tgt_raw = r.get("target", "")
        etype   = r.get("type", "mentioned_with")
        weight  = r.get("weight", 1)
        detail  = r.get("detail", "")

        src = _resolve(src_raw)
        tgt = _resolve(tgt_raw)

        if not src or not tgt or src == tgt:
            continue

        if G.has_edge(src, tgt):
            G[src][tgt]["weight"]  += weight
            G[src][tgt]["details"].append(detail)
        else:
            G.add_edge(src, tgt, edge_type=etype, weight=weight, details=[detail])

    return G


def build_graph_from_person(person: dict, search_results: dict) -> tuple[nx.DiGraph, list, list]:
    """
    Auto-build graph entities and relationships from a Person Object + search results.
    Returns (graph, entities, relationships).
    """
    entities      = []
    relationships = []
    seen_nodes    = set()

    def add_node(node_id: str, label: str, node_type: str = "person"):
        if node_id not in seen_nodes:
            entities.append({"id": node_id, "label": label, "type": node_type})
            seen_nodes.add(node_id)

    # Primary subject
    subject_id   = person.get("confirmed_name", "Subject") or "Subject"
    subject_label = subject_id
    add_node(subject_id, subject_label, "person")

    # Platforms as org nodes + follows edges
    for platform in person.get("platforms_confirmed", []):
        plat_id = f"platform:{platform}"
        add_node(plat_id, platform, "platform")
        relationships.append({
            "source": subject_id,
            "target": plat_id,
            "type":   "follows",
            "weight": 1,
            "detail": f"{subject_label} has confirmed presence on {platform}",
        })

    # Usernames as alias nodes
    for plat, uname in person.get("usernames", {}).items():
        alias_id = f"alias:{uname}"
        add_node(alias_id, f"@{uname}", "alias")
        relationships.append({
            "source": subject_id,
            "target": alias_id,
            "type":   "mentioned_with",
            "weight": 2,
            "detail": f"Username on {plat}",
        })

    # Locations
    for loc in person.get("location_stated", []):
        if loc and loc != "Not found":
            loc_id = f"loc:{loc}"
            add_node(loc_id, loc, "location")
            relationships.append({
                "source": subject_id,
                "target": loc_id,
                "type":   "located_near",
                "weight": 1,
                "detail": f"Location stated: {loc}",
            })

    # Co-appearances from web mentions and news
    all_mentions = (
        person.get("web_mentions", []) +
        person.get("news_appearances", [])
    )
    co_appear_count = {}
    for mention in all_mentions:
        if " — " in mention:
            title = mention.split(" — ")[0]
            words = title.split()
            # Look for capitalized word pairs (potential co-appearing entities)
            for i in range(len(words) - 1):
                if (words[i][0].isupper() if words[i] else False) and \
                   (words[i+1][0].isupper() if words[i+1] else False):
                    pair = f"{words[i]} {words[i+1]}"
                    if pair.lower() != subject_label.lower() and len(pair) > 4:
                        co_appear_count[pair] = co_appear_count.get(pair, 0) + 1

    for entity_name, count in list(co_appear_count.items())[:8]:
        co_id = f"co:{entity_name}"
        add_node(co_id, entity_name, "entity")
        relationships.append({
            "source": subject_id,
            "target": co_id,
            "type":   "co_appears",
            "weight": count,
            "detail": f"Co-appears in {count} source(s)",
        })

    G = build_graph(entities, relationships)
    return G, entities, relationships


# ── Plotly visualization ───────────────────────────────────────────────────────

NODE_TYPE_COLORS = {
    "person":   "#7B2FBE",
    "platform": "#4B9EFF",
    "location": "#4BFF91",
    "alias":    "#FFD700",
    "entity":   "#9D4EDD",
    "unknown":  "#555555",
}

NODE_TYPE_SIZES = {
    "person":   22,
    "platform": 16,
    "location": 14,
    "alias":    12,
    "entity":   13,
    "unknown":  10,
}


def render_graph(
    G: nx.DiGraph,
    filter_edge_types: list[str] | None = None,
    title: str = "Relationship Map",
) -> go.Figure:
    """
    Render an interactive Plotly network graph.
    filter_edge_types: if provided, show only edges of those types.
    """
    if len(G.nodes) == 0:
        fig = go.Figure()
        fig.update_layout(
            paper_bgcolor="#000000",
            plot_bgcolor="#000000",
            font_color="#F0EAD6",
            title=dict(text="No entities to display", font=dict(color="#7B2FBE")),
        )
        return fig

    # Layout — spring_layout only (no scipy dependency)
    n = len(G.nodes)
    if n <= 2:
        pos = nx.spring_layout(G, seed=42)
    elif n <= 20:
        pos = nx.spring_layout(G, k=2.0, iterations=80, seed=42)
    else:
        # For large graphs use spring with tighter spacing and more iterations
        # (kamada_kawai requires scipy which may not be installed)
        pos = nx.spring_layout(G, k=1.5 / (n ** 0.5), iterations=120, seed=42)

    # Filter edges
    edges_to_draw = [
        (u, v, d) for u, v, d in G.edges(data=True)
        if filter_edge_types is None or d.get("edge_type", "mentioned_with") in filter_edge_types
    ]

    # Build edge traces (one per edge type for legend)
    edge_traces_by_type: dict[str, dict] = {}
    for u, v, data in edges_to_draw:
        etype  = data.get("edge_type", "mentioned_with")
        weight = data.get("weight", 1)
        detail = "; ".join(data.get("details", [])) or ""

        x0, y0 = pos[u]
        x1, y1 = pos[v]
        mx, my = (x0 + x1) / 2, (y0 + y1) / 2

        color = EDGE_COLORS.get(etype, "#9D4EDD")
        width = min(1 + weight * 0.5, 6)

        if etype not in edge_traces_by_type:
            edge_traces_by_type[etype] = {
                "x": [], "y": [], "color": color, "width": width,
                "texts": [], "name": etype,
            }

        t = edge_traces_by_type[etype]
        t["x"] += [x0, x1, None]
        t["y"] += [y0, y1, None]
        # Midpoint annotation for hover
        t["texts"].append((mx, my, f"{G.nodes[u].get('label', u)} -> {G.nodes[v].get('label', v)}<br>{etype}<br>{detail}"))

    edge_figure_traces = []
    for etype, t in edge_traces_by_type.items():
        edge_figure_traces.append(go.Scatter(
            x=t["x"], y=t["y"],
            mode="lines",
            line=dict(color=t["color"], width=1.5),
            hoverinfo="none",
            showlegend=True,
            name=etype,
            legendgroup=etype,
        ))
        # Invisible midpoint markers for hover
        if t["texts"]:
            mx_vals = [tx[0] for tx in t["texts"]]
            my_vals = [tx[1] for tx in t["texts"]]
            hover_texts = [tx[2] for tx in t["texts"]]
            edge_figure_traces.append(go.Scatter(
                x=mx_vals, y=my_vals,
                mode="markers",
                marker=dict(size=8, color="rgba(0,0,0,0)"),
                hovertext=hover_texts,
                hoverinfo="text",
                showlegend=False,
                legendgroup=etype,
            ))

    # Node traces grouped by type
    node_traces = []
    nodes_by_type: dict[str, list] = {}
    for node_id, node_data in G.nodes(data=True):
        ntype = node_data.get("node_type", "unknown")
        if ntype not in nodes_by_type:
            nodes_by_type[ntype] = []
        nodes_by_type[ntype].append((node_id, node_data))

    for ntype, node_list in nodes_by_type.items():
        color = NODE_TYPE_COLORS.get(ntype, "#7B2FBE")
        size  = NODE_TYPE_SIZES.get(ntype, 12)
        xs, ys, texts, hovers = [], [], [], []

        for node_id, node_data in node_list:
            x, y = pos[node_id]
            label = node_data.get("label", node_id)
            degree = G.degree(node_id)
            hover = (
                f"<b>{label}</b><br>"
                f"Type: {ntype}<br>"
                f"Connections: {degree}"
            )
            xs.append(x)
            ys.append(y)
            texts.append(label)
            hovers.append(hover)

        node_traces.append(go.Scatter(
            x=xs, y=ys,
            mode="markers+text",
            marker=dict(
                size=size,
                color=color,
                line=dict(color="#000000", width=1.5),
                opacity=0.9,
            ),
            text=texts,
            textposition="top center",
            textfont=dict(color="#F0EAD6", size=10, family="Arial, sans-serif"),
            hovertext=hovers,
            hoverinfo="text",
            name=ntype,
            showlegend=True,
        ))

    all_traces = edge_figure_traces + node_traces

    fig = go.Figure(data=all_traces)
    fig.update_layout(
        title=dict(
            text=title,
            font=dict(color="#9D4EDD", size=14, family="Arial, sans-serif"),
            x=0.01,
        ),
        paper_bgcolor="#000000",   # --void
        plot_bgcolor="#05000D",    # --abyss
        hovermode="closest",
        showlegend=True,
        legend=dict(
            bgcolor="#0A0015",              # --deep
            bordercolor="rgba(123,47,190,0.28)",  # --border
            font=dict(color="#F0EAD6", size=10, family="Courier New, monospace"),
        ),
        xaxis=dict(
            showgrid=False, zeroline=False, showticklabels=False,
            visible=False,
        ),
        yaxis=dict(
            showgrid=False, zeroline=False, showticklabels=False,
            visible=False,
        ),
        margin=dict(l=10, r=10, t=40, b=10),
        height=600,
        dragmode="pan",
    )
    fig.update_traces(
        hoverlabel=dict(
            bgcolor="#0A0015",              # --deep
            bordercolor="#7B2FBE",          # --p600
            font=dict(color="#F0EAD6", size=11, family="Courier New, monospace"),
        )
    )

    return fig


def get_key_associations(G: nx.DiGraph, subject_name: str, max_count: int = 5) -> list:
    """
    Return top associations excluding the subject and non-person/org nodes.

    Only person, org, and alias nodes qualify as intelligence-relevant associations.
    Centrality is computed live via nx.degree_centrality so the result reflects
    the current graph state even when nodes were created without a stored value.
    Output dicts carry both "name"/"label" (same value) and "id" so callers
    using either schema work without conversion.
    """
    degree_centrality = {}
    try:
        degree_centrality = nx.degree_centrality(G)
    except Exception:
        pass

    try:
        from modules.entity_resolution import is_bad_subject_name as _is_bad
    except Exception:
        _is_bad = lambda *a, **k: False

    subject_lower = (subject_name or "").lower()
    associations  = []

    for node, data in G.nodes(data=True):
        node_type = data.get("node_type", "")

        # Only keep real people, organisations, and known aliases
        if node_type not in ("person", "org", "alias"):
            continue

        # Skip the subject themselves (check both stored label and node id)
        lbl = data.get("label", node)
        if lbl.lower() == subject_lower or node.lower() == subject_lower:
            continue

        # Skip noise labels: email greetings ("Dear Sir"), transaction lines
        # ("Swiggy Order"), spam/vendor labels — never genuine associations.
        if _is_bad(lbl):
            continue

        centrality = round(degree_centrality.get(node, 0), 3)
        # Structural guard: a node with zero centrality is ISOLATED — it has no
        # edge to the subject or anyone else, so it cannot be a real association.
        # This drops extraction noise (transaction-line / promo nodes that were
        # never connected) without needing to recognise each noise string.
        if centrality <= 0:
            continue
        associations.append({
            "id":         node,
            "name":       lbl,   # user-facing schema key
            "label":      lbl,   # backward-compat for report_generator callers
            "centrality": centrality,
            "node_type":  node_type,
        })

    associations.sort(key=lambda x: x["centrality"], reverse=True)
    return associations[:max_count]


def get_primary_subject(entities: list, graph: nx.DiGraph) -> str:
    """
    Force the primary subject to be the most mentioned PERSON,
    not a location or organization.

    Ranking order:
      1. mention_count stored on graph nodes (incremented by get_or_create_node)
      2. Degree centrality as tie-breaker when counts are equal
      3. First entity in the list whose type == "person"
      4. "Unknown Subject" if nothing qualifies

    All candidates are validated through is_bad_subject_name to prevent
    operation titles, file stems, or noise strings from masquerading as
    the primary subject.
    """
    from modules.entity_resolution import is_bad_subject_name as _is_bad

    person_scores: dict = {}

    try:
        degree_centrality = nx.degree_centrality(graph)
    except Exception:
        degree_centrality = {}

    for node, data in graph.nodes(data=True):
        if data.get("node_type") == "person":
            label = data.get("label", node)
            if _is_bad(label):
                continue
            mention_count = data.get("mention_count", 0)
            centrality    = degree_centrality.get(node, 0)
            person_scores[node] = (mention_count, centrality)

    if person_scores:
        best_node = max(person_scores, key=person_scores.get)
        return graph.nodes[best_node].get("label", best_node)

    # Fallback: entities list (pre-graph data) — first clean person-type entry
    for e in (entities or []):
        if e.get("type") == "person" or e.get("node_type") == "person":
            name = e.get("name") or e.get("label") or e.get("id", "")
            if name and not _is_bad(name):
                return name

    return "Unknown Subject"


def detect_boilerplate_locations(raw_documents: list, threshold: float = 0.8) -> set:
    """
    Return the set of normalized LOCATION strings that appear in >= `threshold`
    fraction of source documents (Fix 5 — cross-file boilerplate suppression).

    These are institutional-address / shared-header strings (e.g. a campus
    address printed on every file) that inflate graph centrality and drown real
    signal.  Only location-type strings are considered, so the subject — a
    person who also appears in most files — is NEVER suppressed by this filter.

    Pure and deterministic: same documents → same set every run.
    """
    docs = [d for d in (raw_documents or []) if isinstance(d, dict)]
    n = len(docs)
    if n < 2:
        return set()

    doc_count: dict = {}
    for d in docs:
        seen_in_doc: set = set()
        for loc in (d.get("locations") or []):
            val = loc.get("value", loc) if isinstance(loc, dict) else loc
            k = str(val).strip().lower()
            if len(k) > 3:
                seen_in_doc.add(k)
        for row in (d.get("structured_rows") or []):
            if not isinstance(row, dict):
                continue
            for col, val in row.items():
                if any(t in str(col).lower() for t in ("location", "city", "place", "area", "address")):
                    k = str(val).strip().lower()
                    if len(k) > 3:
                        seen_in_doc.add(k)
        for k in seen_in_doc:
            doc_count[k] = doc_count.get(k, 0) + 1

    cutoff = max(2, int(round(threshold * n)))
    return {k for k, c in doc_count.items() if c >= cutoff}


def graph_summary(G: nx.DiGraph, subject_name: str = "", boilerplate: set = None) -> dict:
    """
    Return basic graph statistics including filtered key associations.

    `boilerplate`: optional set of normalized location labels (from
    detect_boilerplate_locations) that are excluded from top_nodes so shared
    institutional addresses do not dominate the ranking (Fix 5).
    """
    if len(G.nodes) == 0:
        return {"nodes": 0, "edges": 0, "density": 0.0, "top_nodes": [], "top_associations": []}

    boilerplate = boilerplate or set()
    try:
        from modules.entity_resolution import is_bad_subject_name as _is_bad
    except Exception:
        _is_bad = lambda *a, **k: False
    density = nx.density(G)
    try:
        degree_centrality = nx.degree_centrality(G)
        top_nodes_raw = sorted(degree_centrality.items(), key=lambda x: x[1], reverse=True)[:10]
        # Deduplicate by label — keep highest centrality score, preserve node_type
        seen_labels: dict = {}
        for n, c in top_nodes_raw:
            lbl       = G.nodes[n].get("label", n)
            ntype     = G.nodes[n].get("node_type", "unknown")
            lbl_lower = lbl.lower()
            # Fix 5: drop cross-file boilerplate location nodes from the ranking.
            if ntype == "location" and lbl_lower in boilerplate:
                continue
            # Drop noise person/org nodes ("Dear Sir", "Swiggy Order", vendor
            # labels) so the network map shows real entities, not transaction text.
            if ntype in ("person", "org", "alias") and _is_bad(lbl):
                continue
            # Drop ISOLATED person/org nodes (centrality 0) — disconnected
            # extraction noise (e.g. "Hugging Face", "Big Billion Days") that was
            # never linked to the subject. Locations are exempt (shown in §04).
            if ntype in ("person", "org", "alias") and c <= 0:
                continue
            if lbl_lower not in seen_labels or c > seen_labels[lbl_lower]["centrality"]:
                seen_labels[lbl_lower] = {
                    "id":        n,
                    "label":     lbl,
                    "centrality": round(c, 3),
                    "node_type": ntype,
                }
        top_nodes = list(seen_labels.values())[:5]
    except Exception:
        top_nodes = []

    # Filtered associations: person/org/alias only, subject excluded
    top_associations = get_key_associations(G, subject_name) if subject_name else []

    return {
        "nodes":            len(G.nodes),
        "edges":            len(G.edges),
        "density":          round(density, 4),
        "top_nodes":        top_nodes,
        "top_associations": top_associations,
    }
