"""
PHASE 4 STEP 14 — NETWORK-DISMANTLING SUGGESTIONS (decision support).

Given a DETECTED network — the cross-subject cited links produced by the
Phase 2 miners (data_mining.mine_case_set / the specialised miners) — surface
which nodes and links are STRUCTURALLY CENTRAL, as cited, human-review-only
suggestions FOR OFFICER CONSIDERATION.

HARD CONSTRAINT (Phase 4, binding on every function here and on any future edit):

  * NEVER AUTONOMOUS, NEVER A DIRECTIVE. Every output is an analytical
    suggestion for an officer to weigh — "for officer consideration," not an
    instruction. autonomous=False and human_review_required=True on every
    result; the DISMANTLING_NOTICE rides on every result.
  * NOT A DETERMINATION OF GUILT. Structural centrality in an evidence graph
    describes connectivity, not culpability. Every result states this.
  * GROUNDED IN THE ACTUAL GRAPH, NEVER FABRICATED. Nodes and edges come only
    from the cited links passed in; each suggestion cites the real links that
    give a node/link its centrality. If the network has no edges, there are no
    suggestions. Nothing is inferred or invented.
  * EVIDENCE-BASED ONLY, NO IDENTITY ATTRIBUTES. The graph is built from the
    Phase 2 links, which derive from documents/numbers/behaviour only —
    nationality, ethnicity, and religion are not inputs and never become
    ranking inputs (no predictive-policing on identity).
  * DETERMINISTIC AND GENERAL. Same input → same output; no case-name /
    subject-name / file-name branches.
"""
import networkx as nx

# Verbatim on every result. Do not shorten.
DISMANTLING_NOTICE = (
    "FOR OFFICER CONSIDERATION — ANALYTICAL SUGGESTION ONLY: the items below "
    "identify which subjects and links are STRUCTURALLY CENTRAL in the cited "
    "evidence network. This is decision support, NOT an instruction and NOT a "
    "directive to act. Structural centrality describes connectivity in the "
    "graph — it is NOT a determination of guilt. Every suggestion is grounded "
    "in the cited links shown; nothing is inferred or fabricated. A human "
    "officer must weigh each suggestion and decide independently. Centrality "
    "derives from documents, numbers, and behaviour only — no nationality, "
    "ethnicity, or religion was used."
)


def _links_from(network) -> list:
    """Accept a mine_case_set result, a specialised-miner result, or a bare
    list of cited links. Returns the list of link dicts (each with
    'subjects' and 'citations'). Unknown shapes yield an empty list."""
    if isinstance(network, dict):
        for key in ("links", "ring_links"):
            if isinstance(network.get(key), list):
                return network[key]
        return []
    if isinstance(network, list):
        return network
    return []


def _pair_key(a: str, b: str) -> tuple:
    return (a, b) if a <= b else (b, a)


def build_network_graph(network):
    """Build an undirected NetworkX graph from cited cross-subject links.

    Nodes are subjects; an edge joins every pair of subjects that share a
    link (a link over 3 subjects contributes the triangle). Each edge carries
    the link types and the per-subject citations that establish it — so every
    edge is traceable to real evidence, never fabricated. Returns (graph,
    edge_evidence) where edge_evidence[(a,b)] = list of contributing links."""
    g = nx.Graph()
    edge_evidence: dict = {}
    for link in _links_from(network):
        if not isinstance(link, dict):
            continue
        subjects = sorted({str(s) for s in (link.get("subjects") or [])})
        if len(subjects) < 2:
            continue
        for s in subjects:
            g.add_node(s)
        cites = link.get("citations") or {}
        for i in range(len(subjects)):
            for j in range(i + 1, len(subjects)):
                a, b = subjects[i], subjects[j]
                key = _pair_key(a, b)
                rec = {
                    "type": link.get("type", ""),
                    "value": link.get("value", ""),
                    "citations": {a: list(cites.get(a, [])), b: list(cites.get(b, []))},
                }
                if g.has_edge(a, b):
                    edge_evidence[key].append(rec)
                else:
                    g.add_edge(a, b)
                    edge_evidence[key] = [rec]
    return g, edge_evidence


def suggest_network_dismantling(network) -> dict:
    """Surface structurally central nodes and links as human-review-only
    suggestions grounded in the cited graph. Pure, deterministic projection —
    no new evidence, no LLM, no autonomy."""
    g, edge_evidence = build_network_graph(network)
    node_count, edge_count = g.number_of_nodes(), g.number_of_edges()
    components = [sorted(c) for c in nx.connected_components(g)] if node_count else []

    # Structural measures on the ACTUAL graph. Betweenness needs the graph as
    # given; articulation points / bridges identify single points whose removal
    # fragments a component — the textbook "structurally central" elements.
    betweenness = nx.betweenness_centrality(g) if node_count else {}
    artic = set(nx.articulation_points(g)) if node_count else set()
    bridges = {_pair_key(u, v) for u, v in nx.bridges(g)} if edge_count else set()
    degrees = dict(g.degree())
    max_degree = max(degrees.values()) if degrees else 0

    central_nodes = []
    for subj in sorted(g.nodes()):
        deg = degrees.get(subj, 0)
        if deg < 1:
            continue
        is_artic = subj in artic
        # Structurally central: a cut vertex, or a top-degree hub (>= 2 links).
        is_central = is_artic or (deg == max_degree and max_degree >= 2)
        neighbours = sorted(g.neighbors(subj))
        cites = []
        for nb in neighbours:
            for rec in edge_evidence.get(_pair_key(subj, nb), []):
                cites.append({"with": nb, "type": rec["type"], "value": rec["value"],
                              "citations": rec["citations"]})
        rationale = []
        if is_artic:
            rationale.append("removal would fragment its cluster (cut vertex)")
        if deg == max_degree and max_degree >= 2:
            rationale.append(f"most-connected node ({deg} cited link(s))")
        if not rationale:
            rationale.append(f"connected to {deg} other subject(s) by cited link(s)")
        central_nodes.append({
            "subject": subj,
            "degree": deg,
            "betweenness": round(float(betweenness.get(subj, 0.0)), 4),
            "is_articulation_point": is_artic,
            "is_structurally_central": is_central,
            "connects": neighbours,
            "rationale": "; ".join(rationale),
            "citations": cites,
            "suggestion": (f"FOR OFFICER CONSIDERATION: {subj} is structurally "
                           f"central ({'; '.join(rationale)}). An officer may "
                           f"weigh focusing review here — this is not a directive "
                           f"and not a finding of guilt."),
        })
    # Rank: cut vertices first, then degree, then betweenness, then name.
    central_nodes.sort(key=lambda n: (not n["is_articulation_point"], -n["degree"],
                                      -n["betweenness"], n["subject"]))

    central_links = []
    for (a, b) in sorted(bridges):
        recs = edge_evidence.get((a, b), [])
        central_links.append({
            "subjects": [a, b],
            "types": sorted({r["type"] for r in recs}),
            "is_bridge": True,
            "rationale": ("sole cited connection between two parts of the "
                          "network — its removal disconnects them"),
            "citations": recs,
            "suggestion": (f"FOR OFFICER CONSIDERATION: the link between {a} and "
                           f"{b} is the sole cited bridge joining two parts of "
                           f"the network. An officer may weigh its significance — "
                           f"this is not a directive and not a finding of guilt."),
        })

    return {
        "network": {
            "node_count": node_count,
            "edge_count": edge_count,
            "component_count": len(components),
            "components": components,
        },
        "central_nodes": central_nodes,
        "central_links": central_links,
        "has_structural_bridges": bool(bridges),
        "redundant_connectivity": bool(edge_count) and not bridges and not artic,
        "human_review_required": True,
        "autonomous": False,
        "determination_of_guilt": False,
        "dismantling_notice": DISMANTLING_NOTICE,
    }


def render_dismantling_suggestions(result: dict) -> str:
    """Officer-facing plain-text rendering of a suggest_network_dismantling result."""
    if not isinstance(result, dict):
        return ""
    net = result.get("network") or {}
    lines = ["NETWORK-DISMANTLING SUGGESTIONS (FOR OFFICER CONSIDERATION — NOT A DIRECTIVE)",
             str(result.get("dismantling_notice") or DISMANTLING_NOTICE),
             f"Network: {net.get('node_count', 0)} subject(s), "
             f"{net.get('edge_count', 0)} cited link(s), "
             f"{net.get('component_count', 0)} component(s).", ""]
    if not result.get("central_nodes"):
        lines.append("No cited links between subjects — no structural "
                     "suggestions to make.")
        return "\n".join(lines)
    lines.append("STRUCTURALLY CENTRAL SUBJECTS (for consideration):")
    for n in result["central_nodes"]:
        star = " ***" if n["is_structurally_central"] else ""
        lines.append(f"  {n['subject']}{star} — degree {n['degree']}, "
                     f"betweenness {n['betweenness']} — {n['rationale']}")
        for c in n["citations"]:
            for subj, cs in c["citations"].items():
                for cite in cs:
                    lines.append(f"      link with {c['with']} [{c['type']}]: "
                                 f"{subj} \"{cite['raw']}\" — {cite['source']}")
    if result.get("central_links"):
        lines.append("STRUCTURALLY CENTRAL LINKS (cited bridges, for consideration):")
        for l in result["central_links"]:
            lines.append(f"  {l['subjects'][0]} — {l['subjects'][1]} "
                         f"[{', '.join(l['types'])}]: {l['rationale']}")
    elif result.get("redundant_connectivity"):
        lines.append("No single link is structurally critical — the network has "
                     "redundant connectivity (no cited bridges).")
    return "\n".join(lines)
