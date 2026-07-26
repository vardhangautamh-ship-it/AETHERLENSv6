"""
PHASE 2 STEP 12 — CLUSTER / NETWORK DETECTION across analysed subjects.

Mines ACROSS the subjects of one analysed case set (in-memory input — NOT
persistent cross-case storage) for exactly four evidence-typed link kinds:

    shared phones · shared organizations · shared counterparties ·
    shared locations

HARD RULES (binding on every function here and on any future edit):

  * CITED LINKS ONLY, NO FABRICATED EDGES. A link exists only when the SAME
    normalised value appears in the typed evidence of two or more distinct
    subjects, and every link carries, for EVERY subject on it, the raw value
    as it appears in that subject's evidence plus its source file. Nothing is
    inferred, extrapolated, or guessed; absence of a link is not evidence of
    absence.
  * EVIDENCE-BASED ONLY. The miner reads phones, organizations, transaction
    counterparties, and locations from the typed case ontology — never flags,
    narrative text, or identity attributes (nationality/ethnicity/religion
    are not inputs and must never become inputs).
  * DETERMINISTIC AND GENERAL. Same input → same output; matching is
    vocabulary-driven and case/punctuation-insensitive; no case-name /
    subject-name / file-name branches.
  * DECISION SUPPORT ONLY. A shared value establishes association, not
    culpability. Every result carries MINING_NOTICE; action on any cluster
    requires human review and authorisation.
"""
import re
from collections import defaultdict

# Verbatim on every mining result. Do not shorten.
MINING_NOTICE = (
    "DETERMINISTIC DATA MINING — DECISION SUPPORT ONLY: every link below is "
    "cited to the source evidence of each linked case; no link is inferred "
    "or fabricated, and absence of a link is not evidence of absence. A "
    "shared value establishes association, not culpability. This output is "
    "for analyst review; any action requires human review and authorisation. "
    "Links derive from documents, numbers, and behaviour only — no "
    "nationality, ethnicity, or religion was used."
)

# Values too generic to constitute an evidentiary link between two subjects.
# Vocabulary-driven (compared after normalisation) — extend the vocabulary,
# never special-case a single investigation.
_GENERIC_TOKENS = {"unknown", "n a", "na", "none", "nil", "self", "cash", "",
                   # Transaction-TYPE descriptors that leak into counterparty
                   # columns ("Counter withdrawal") — a banking phrase, not an
                   # entity; two subjects sharing one can never be an
                   # evidentiary link (generic-value vs entity-value rule).
                   "counter withdrawal", "cash withdrawal", "counter deposit",
                   "cash deposit", "atm withdrawal", "atm deposit",
                   "self withdrawal", "cheque deposit", "cheque withdrawal",
                   "withdrawal", "deposit", "transfer", "wire transfer",
                   "bank transfer", "counter"}
_GENERIC_LOCATIONS = {"india", "bharat"}

_MIN_PHONE_DIGITS = 7   # below this a digit string is not a phone line

_LINK_TYPES = ("shared_phone", "shared_organization",
               "shared_counterparty", "shared_location")


def _norm_text(s) -> str:
    """Case/punctuation-insensitive canonical form for names/places."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", str(s or "").lower())).strip()


def _norm_phone(s) -> str:
    """Digits-only canonical form; last 10 digits when longer (so a line with
    a country code matches the same line written without one — both raw forms
    stay visible in the citations). Empty when too short to be a line.
    Thin delegate to the single source of truth (sanitizer.phone_key) with
    this module's historical junk gate (_MIN_PHONE_DIGITS) preserved."""
    from modules.sanitizer import phone_key
    return phone_key(s, min_digits=_MIN_PHONE_DIGITS) or ""


def _get(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _cite(raw, source) -> dict:
    src = str(source or "").strip()
    return {"raw": str(raw or ""),
            "source": src if src else "source not recorded in the analysed case"}


# Hard-identifier attribute vocabulary for entities. An organization or a
# transaction counterparty is the SAME entity across cases only when it shares
# one of these (a registration/CIN, or a counterparty account) — never on a
# matching name alone (Weakness-2 fix). Extend the vocabulary, never
# special-case one investigation. Absent everywhere in the real pipeline today,
# so real org/counterparty overlaps surface as UNVERIFIED flags until a
# registration/account channel is ingested — the honest, conservative state.
_ORG_HARD_ID_ATTRS = ("registration", "registration_id", "registration_no",
                      "reg_no", "cin", "llpin", "gstin", "identifier")
_CP_HARD_ID_ATTRS = ("counterparty_account", "counterparty_id",
                     "counterparty_registration", "beneficiary_account")


def _entity_hard_id(obj, attrs) -> str:
    """First non-empty hard-identifier attribute of an entity, or ''."""
    for a in attrs:
        v = _get(obj, a, "")
        if str(v or "").strip():
            return str(v).strip()
    return ""


def _ecite(raw, source, hard_id="") -> dict:
    """Entity citation — a cited value that also carries its hard identifier
    (empty when the entity has none). The miner links entities on hard_id,
    never on raw name."""
    d = _cite(raw, source)
    d["id"] = str(hard_id or "")
    return d


def extract_case_features(subject: str, onto) -> dict:
    """Serialisable projection of ONE case's linkable evidence from its typed
    ontology (the Phase 0.5 backbone — single source of truth). Reads only
    the four evidence types the miner links on. Organizations and
    counterparties additionally carry any hard identifier they expose, so the
    cross-case matcher can link them on that identifier rather than on name."""
    feats = {"subject": str(subject or "Unknown Subject"),
             "phones": [], "organizations": [], "counterparties": [], "locations": []}
    for p in (_get(onto, "phones") or []):
        feats["phones"].append(_cite(_get(p, "number", ""), _get(p, "source", "")))
    for o in (_get(onto, "organizations") or []):
        feats["organizations"].append(_ecite(
            _get(o, "name", ""), _get(o, "source", ""),
            _entity_hard_id(o, _ORG_HARD_ID_ATTRS)))
    for t in (_get(onto, "transactions") or []):
        cp = _get(t, "counterparty", "")
        if str(cp or "").strip():
            feats["counterparties"].append(_ecite(
                cp, _get(t, "source", ""),
                _entity_hard_id(t, _CP_HARD_ID_ATTRS)))
    for l in (_get(onto, "locations") or []):
        feats["locations"].append(_cite(_get(l, "name", ""), _get(l, "source", "")))
    return feats


def _normalise_case(case) -> dict | None:
    """Accept {"subject", "ontology"} or an extract_case_features() dict."""
    if not isinstance(case, dict):
        return None
    if case.get("ontology") is not None:
        return extract_case_features(case.get("subject", ""), case["ontology"])
    if any(isinstance(case.get(k), list)
           for k in ("phones", "organizations", "counterparties", "locations")):
        return {"subject": str(case.get("subject") or "Unknown Subject"),
                "phones": list(case.get("phones") or []),
                "organizations": list(case.get("organizations") or []),
                "counterparties": list(case.get("counterparties") or []),
                "locations": list(case.get("locations") or [])}
    return None


def _linkable(link_type: str, norm: str) -> bool:
    if not norm or norm in _GENERIC_TOKENS:
        return False
    if link_type == "shared_location" and norm in _GENERIC_LOCATIONS:
        return False
    if link_type != "shared_phone" and len(norm) < 3:
        return False
    return True


def _phone_strength(raw) -> str:
    """'strong' (personal mobile — can anchor identity/links) or 'weak'
    (fixed line / shared-office infrastructure — may corroborate, never
    sufficient alone). Single source of truth: the single-case CHIMERA
    landline fix in entity_resolution._is_landline_number."""
    from modules.entity_resolution import _is_landline_number
    return "weak" if _is_landline_number(str(raw or "")) else "strong"


def _strong_phone_norms(feats: dict) -> set:
    """Normalised STRONG (personal-mobile) phone lines of one case's
    features. Landlines/shared-office numbers are excluded — they are
    shared infrastructure, not a personal hard identifier."""
    out = set()
    for item in feats.get("phones") or []:
        raw = _get(item, "raw", "") or _get(item, "value", "")
        norm = _norm_phone(raw)
        if norm and _phone_strength(raw) == "strong":
            out.add(norm)
    return out


def assign_cross_case_identities(cases) -> tuple:
    """Cross-case SUBJECT identity, decided on shared HARD identifiers only
    (the HYDRA/CHIMERA doctrine at cross-case scale).

    Returns (resolved, merges, name_flags):
      resolved   — [(identity_key, features_or_None)] one entry per input
                   case, in input order (None where the case is malformed);
      merges     — audit entries for same-person merges, each citing the
                   anchoring identifier in BOTH cases;
      name_flags — 'possible same person — UNVERIFIED' flags for same-name
                   cases with NO shared hard identifier. Flag only: the
                   cases stay SEPARATE people.

    Two same-named cases merge into one cross-case subject ONLY when they
    share a STRONG (personal-mobile) phone line. Name-string equality never
    merges; a shared landline never merges. When same-named cases do NOT
    merge, every one of them is disambiguated with its case ordinal so no
    entry silently claims the bare name."""
    feats_per_case = [_normalise_case(c) for c in (cases or [])]

    by_name: dict = {}
    for idx, f in enumerate(feats_per_case):
        if f is not None:
            by_name.setdefault(_norm_text(f["subject"]), []).append(idx)

    keys = {}            # case index -> identity key
    merges, name_flags = [], []
    for _name, idxs in sorted(by_name.items()):
        if len(idxs) == 1:
            keys[idxs[0]] = feats_per_case[idxs[0]]["subject"]
            continue
        # union-find over this name group; edges = shared strong mobile
        parent = {i: i for i in idxs}
        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x
        strongs = {i: _strong_phone_norms(feats_per_case[i]) for i in idxs}
        anchor_of: dict = {}
        for ai, i in enumerate(idxs):
            for j in idxs[ai + 1:]:
                shared = strongs[i] & strongs[j]
                if shared:
                    ra, rb = find(i), find(j)
                    if ra != rb:
                        parent[max(ra, rb)] = min(ra, rb)
                    anchor_of[(i, j)] = sorted(shared)
        comps: dict = {}
        for i in idxs:
            comps.setdefault(find(i), []).append(i)
        components = sorted(comps.values(), key=lambda c: c[0])
        display = feats_per_case[idxs[0]]["subject"]
        if len(components) == 1:
            for i in idxs:
                keys[i] = display
        else:
            # unmerged same-name collision: every component is qualified —
            # nobody silently owns the bare name
            for comp in components:
                key = f"{display} (case {comp[0] + 1})"
                for i in comp:
                    keys[i] = key
            name_flags.append({
                "type": "possible_same_person_unverified",
                "name": display,
                "parties": [f"{display} (case {c[0] + 1})" for c in components],
                "note": ("same name string across cases with NO shared hard "
                         "identifier — treated as DIFFERENT people; name "
                         "equality is never an identity; human review "
                         "required before treating them as one."),
            })
        for (i, j), shared in sorted(anchor_of.items()):
            def _cites(k, norms):
                return [c for item in feats_per_case[k]["phones"]
                        if _norm_phone(_get(item, "raw", "")
                                       or _get(item, "value", "")) in norms
                        for c in [_cite(_get(item, "raw", "")
                                        or _get(item, "value", ""),
                                        _get(item, "source", ""))]]
            merges.append({
                "type": "same_person_merge",
                "name": display,
                "cases": [i + 1, j + 1],
                "anchor": f"shared personal mobile ({'/'.join(shared)})",
                "citations": {f"case {i + 1}": _cites(i, set(shared)),
                              f"case {j + 1}": _cites(j, set(shared))},
                "note": ("merged as ONE person on a shared hard identifier "
                         "(personal mobile) — never on the name."),
            })

    resolved = []
    for idx, f in enumerate(feats_per_case):
        if f is None:
            resolved.append((None, None))
        else:
            f = dict(f, subject=keys[idx])
            resolved.append((keys[idx], f))
    return resolved, merges, name_flags


def _mine_entity_links(link_type, subjects, features, feat_key, links, entity_flags):
    """Cross-case organization / counterparty matching (Weakness-2 fix).

    An entity link is ASSERTED only when >=2 distinct subjects share a
    normalised HARD IDENTIFIER (registration / CIN / counterparty account).
    Subjects who share only a normalised NAME — with no hard identifier
    joining them — raise a 'possible same entity — UNVERIFIED' flag for human
    review; they are NOT linked and never enter a cluster. When uncertain,
    flag; never assert. Nothing is fabricated: an entity with no identifier
    contributes only to the flag path."""
    by_id = defaultdict(lambda: defaultdict(list))     # id norm  → subj → cites
    by_name = defaultdict(lambda: defaultdict(list))   # name norm → subj → cites
    id_display = {}
    for subj in subjects:
        for item in features[subj][feat_key]:
            raw = _get(item, "raw", "") or _get(item, "value", "")
            name_norm = _norm_text(raw)
            if not _linkable(link_type, name_norm):
                continue
            hid_norm = _norm_text(_get(item, "id", ""))
            cite = _cite(raw, _get(item, "source", ""))
            if hid_norm:
                if cite not in by_id[hid_norm][subj]:
                    by_id[hid_norm][subj].append(cite)
                id_display.setdefault(hid_norm, str(_get(item, "id", "")).strip())
            if cite not in by_name[name_norm][subj]:
                by_name[name_norm][subj].append(cite)

    # Asserted links: a shared hard identifier across >=2 distinct subjects.
    linked_pairs = set()
    for hid in sorted(by_id):
        per_subject = by_id[hid]
        if len(per_subject) < 2:
            continue
        subs = sorted(per_subject)
        for a in range(len(subs)):
            for b in range(a + 1, len(subs)):
                linked_pairs.add((subs[a], subs[b]))
        raws = sorted({c["raw"] for cites in per_subject.values() for c in cites})
        links.append({
            "type": link_type,
            "value": f"{raws[0]}"
                     + (f" (also as: {', '.join(raws[1:])})" if len(raws) > 1 else "")
                     + f" [hard id: {id_display.get(hid, hid)}]",
            "hard_id": id_display.get(hid, hid),
            "subjects": subs,
            "citations": {s: list(per_subject[s]) for s in subs},
        })

    # Flags: same NAME across >=2 subjects not already joined by a shared
    # hard identifier — possible same entity, UNVERIFIED. Never a link.
    kind = "organization" if link_type == "shared_organization" else "counterparty"
    for name_norm in sorted(by_name):
        per_subject = by_name[name_norm]
        if len(per_subject) < 2:
            continue
        subs = sorted(per_subject)
        all_linked = all((subs[a], subs[b]) in linked_pairs
                         for a in range(len(subs)) for b in range(a + 1, len(subs)))
        if all_linked:
            continue   # a genuine hard-id link already covers them
        raws = sorted({c["raw"] for cites in per_subject.values() for c in cites})
        entity_flags.append({
            "type": "possible_same_entity_unverified",
            "entity_kind": kind,
            "value": raws[0] if len(raws) == 1 else f"{raws[0]} (also as: {', '.join(raws[1:])})",
            "subjects": subs,
            "citations": {s: list(per_subject[s]) for s in subs},
            "note": (f"same {kind} NAME across cases with NO shared hard "
                     f"identifier (registration / account) — possible same "
                     f"entity, UNVERIFIED. A matching name is not an identity; "
                     f"NOT linked. Obtain a registration/account to confirm "
                     f"before treating as one entity."),
        })


def _weak_shared_signals(subjects, features, feat_key, norm_fn, keep, find,
                         corrob_note, context_note):
    """Split a WEAK (non-hard-identifier) shared signal — a location, or a
    landline — into (corroborations, context). A value shared by >=2 subjects
    who are all in ONE hard-linked cluster corroborates that link; a value
    spanning subjects not otherwise linked is context only, never an edge.
    `keep(raw)` filters which raw values participate (e.g. landlines only)."""
    by_value = defaultdict(lambda: defaultdict(list))
    for subj in subjects:
        for item in features[subj][feat_key]:
            raw = _get(item, "raw", "") or _get(item, "value", "")
            norm = norm_fn(raw)
            if not norm or not keep(raw):
                continue
            if feat_key == "locations" and not _linkable("shared_location", norm):
                continue
            cite = _cite(raw, _get(item, "source", ""))
            if cite not in by_value[norm][subj]:
                by_value[norm][subj].append(cite)
    corrob, context = [], []
    for norm in sorted(by_value):
        per_subject = by_value[norm]
        if len(per_subject) < 2:
            continue
        subs = sorted(per_subject)
        raws = sorted({c["raw"] for cites in per_subject.values() for c in cites})
        entry = {
            "value": raws[0] if len(raws) == 1 else f"{raws[0]} (also as: {', '.join(raws[1:])})",
            "subjects": subs,
            "citations": {s: list(per_subject[s]) for s in subs},
        }
        if len({find(s) for s in subs}) == 1:
            entry["note"] = corrob_note
            corrob.append(entry)
        else:
            entry["note"] = context_note
            context.append(entry)
    return corrob, context


def mine_case_set(cases, link_types=_LINK_TYPES) -> dict:
    """Detect cited cross-subject links and the clusters they form.

    Returns plain data: links (each citing every subject on it), clusters
    (connected components over the links), unlinked subjects, and honest
    counts for skipped input. Deterministic ordering throughout.

    `link_types` restricts which of the four evidence-typed links are mined
    (the specialised Step 13 miners pass a subset so their rings reuse THIS
    detector rather than a second copy of the logic)."""
    # Cross-case subject identity: decided on shared HARD identifiers, never
    # on name-string equality (Weakness-1 fix). Cases resolved to the SAME
    # identity (genuinely one person, anchored on a shared personal mobile)
    # merge their features; same-named cases without a shared hard
    # identifier stay separate, disambiguated, and flagged for human review.
    resolved, same_person_merges, name_flags = assign_cross_case_identities(cases)
    features, skipped = {}, 0
    for key, f in resolved:
        if f is None:
            skipped += 1
            continue
        if key in features:
            for k in ("phones", "organizations", "counterparties", "locations"):
                features[key][k].extend(f[k])
        else:
            features[key] = f
    subjects = sorted(features)

    _FEATURE_KEY = {"shared_phone": "phones", "shared_organization": "organizations",
                    "shared_counterparty": "counterparties", "shared_location": "locations"}
    # Entity link types (organization / counterparty) link on a shared HARD
    # IDENTIFIER only; a bare name match becomes an UNVERIFIED flag, never a
    # link (Weakness-2 fix). Value link types (phone / location) keep the
    # value-equality path.
    _ENTITY_TYPES = ("shared_organization", "shared_counterparty")
    links, entity_flags = [], []
    for link_type in link_types:
        if link_type in _ENTITY_TYPES:
            _mine_entity_links(link_type, subjects, features,
                               _FEATURE_KEY[link_type], links, entity_flags)
            continue
        if link_type == "shared_location":
            # A shared city/location is coincidence-prone and NEVER a sole
            # cross-case link (Weakness-3 fix). It is handled after clustering:
            # it CORROBORATES a link already anchored by a hard identifier, or
            # surfaces as context for human review — never an asserted edge.
            continue
        # Only shared_phone reaches here (entity + location types handled
        # above). A shared personal MOBILE can establish a link; a shared
        # LANDLINE / shared-office number is weak-context — it may corroborate
        # but never asserts a cross-case link (Weakness-4 fix, same
        # identifier-typing as the single-case landline trap). Landlines are
        # collected separately and resolved after clustering.
        by_value = defaultdict(lambda: defaultdict(list))   # norm → subject → cites
        for subj in subjects:
            for item in features[subj][_FEATURE_KEY[link_type]]:
                raw = _get(item, "raw", "") or _get(item, "value", "")
                norm = _norm_phone(raw)
                if not _linkable(link_type, norm):
                    continue
                if _phone_strength(raw) != "strong":
                    continue   # landline → not a sole link; handled post-cluster
                cite = _cite(raw, _get(item, "source", ""))
                if cite not in by_value[norm][subj]:
                    by_value[norm][subj].append(cite)
        for norm in sorted(by_value):
            per_subject = by_value[norm]
            if len(per_subject) < 2:      # a link needs >= 2 DISTINCT subjects
                continue
            raws = sorted({c["raw"] for cites in per_subject.values() for c in cites})
            links.append({
                "type": link_type,
                "value": raws[0] if len(raws) == 1 else f"{raws[0]} (also as: {', '.join(raws[1:])})",
                "subjects": sorted(per_subject),
                "citations": {s: list(per_subject[s]) for s in sorted(per_subject)},
            })

    # Clusters — connected components over the cited links (union-find).
    parent = {s: s for s in subjects}
    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for link in links:
        first = link["subjects"][0]
        for other in link["subjects"][1:]:
            ra, rb = find(first), find(other)
            if ra != rb:
                parent[max(ra, rb)] = min(ra, rb)
    groups = defaultdict(list)
    for s in subjects:
        groups[find(s)].append(s)

    clusters = []
    for members in groups.values():
        if len(members) < 2:
            continue
        mset = set(members)
        clinks = [l for l in links if set(l["subjects"]) & mset]
        clusters.append({
            "subjects": sorted(members),
            "size": len(members),
            "link_count": len(clinks),
            "link_types": sorted({l["type"] for l in clinks}),
        })
    clusters.sort(key=lambda c: (-c["size"], c["subjects"]))
    linked = {s for c in clusters for s in c["subjects"]}

    # ── Weak-signal channels (Weakness 3 + 4): corroboration vs context ───────
    # A signal that is NOT a hard identifier — a shared city/location, or a
    # shared landline / shared-office number — is never a sole cross-case link.
    # It CORROBORATES when every sharing subject is already in one hard-linked
    # cluster, and is CONTEXT (human review only, never an edge) otherwise.
    loc_corroborations, loc_context = _weak_shared_signals(
        subjects, features, "locations", _norm_text, lambda raw: True, find,
        "shared location among subjects already linked by a hard identifier — "
        "CORROBORATES that link; not itself the basis of the connection.",
        "shared location among subjects NOT otherwise linked — CONTEXT for "
        "human review only; a shared city/place is not a cross-case link."
    ) if "shared_location" in link_types else ([], [])

    ll_corroborations, ll_context = _weak_shared_signals(
        subjects, features, "phones", _norm_phone,
        lambda raw: _norm_phone(raw) and _phone_strength(raw) == "weak", find,
        "shared landline / office line among subjects already linked by a hard "
        "identifier — CORROBORATES; a shared office line is not itself a "
        "personal-identity link.",
        "shared landline / office line among subjects NOT otherwise linked — "
        "CONTEXT only; a shared office line is shared infrastructure, never a "
        "cross-case link or a basis to fuse two people."
    ) if "shared_phone" in link_types else ([], [])

    return {
        "subject_count": len(subjects),
        "links": links,
        "link_count": len(links),
        "clusters": clusters,
        "cluster_count": len(clusters),
        "unlinked_subjects": sorted(set(subjects) - linked),
        "location_corroborations": loc_corroborations,
        "shared_location_context": loc_context,
        "weak_phone_corroborations": ll_corroborations,
        "shared_landline_context": ll_context,
        "same_person_merges": same_person_merges,
        "unverified_same_name": name_flags,
        "unverified_same_entity": entity_flags,
        "skipped_malformed": skipped,
        "human_review_required": True,
        "mining_notice": MINING_NOTICE,
    }


def render_mining_result(result: dict) -> str:
    """Analyst-facing plain-text rendering of a mine_case_set() result."""
    if not isinstance(result, dict):
        return ""
    lines = ["CROSS-SUBJECT CLUSTER / NETWORK DETECTION (DECISION SUPPORT ONLY)",
             str(result.get("mining_notice") or MINING_NOTICE),
             ""]
    clusters = result.get("clusters") or []
    if not clusters:
        lines.append("No cited links between the analysed subjects.")
    for i, c in enumerate(clusters, 1):
        lines.append(f"CLUSTER {i}: {', '.join(c['subjects'])} "
                     f"({c['link_count']} cited link(s); "
                     f"types: {', '.join(c['link_types'])})")
    for l in (result.get("links") or []):
        lines.append(f"  [{l['type'].upper()}] {l['value']} — "
                     f"shared by: {', '.join(l['subjects'])}")
        for subj in l["subjects"]:
            for cite in l["citations"].get(subj, []):
                lines.append(f"      {subj}: \"{cite['raw']}\" — {cite['source']}")
    if result.get("unlinked_subjects"):
        lines.append(f"UNLINKED SUBJECTS (no cited link — NOT implicated): "
                     f"{', '.join(result['unlinked_subjects'])}")
    for m in (result.get("same_person_merges") or []):
        lines.append(f"SAME-PERSON MERGE [hard-identifier anchored]: "
                     f"'{m['name']}' across case {m['cases'][0]} and case "
                     f"{m['cases'][1]} — {m['anchor']}. {m['note']}")
    for fl in (result.get("unverified_same_name") or []):
        lines.append(f"POSSIBLE SAME PERSON — UNVERIFIED: "
                     f"{' / '.join(fl['parties'])}. {fl['note']}")
    for fl in (result.get("unverified_same_entity") or []):
        lines.append(f"POSSIBLE SAME {fl['entity_kind'].upper()} — UNVERIFIED: "
                     f"'{fl['value']}' shared by name across "
                     f"{', '.join(fl['subjects'])} — NOT linked. {fl['note']}")
    for c in (result.get("location_corroborations") or []):
        lines.append(f"LOCATION CORROBORATION: '{c['value']}' shared by "
                     f"{', '.join(c['subjects'])} — {c['note']}")
    for c in (result.get("shared_location_context") or []):
        lines.append(f"SHARED LOCATION — CONTEXT ONLY (NOT A LINK): '{c['value']}' "
                     f"noted for {', '.join(c['subjects'])}. {c['note']}")
    for c in (result.get("weak_phone_corroborations") or []):
        lines.append(f"LANDLINE CORROBORATION: '{c['value']}' shared by "
                     f"{', '.join(c['subjects'])} — {c['note']}")
    for c in (result.get("shared_landline_context") or []):
        lines.append(f"SHARED LANDLINE / OFFICE LINE — CONTEXT ONLY (NOT A LINK): "
                     f"'{c['value']}' noted for {', '.join(c['subjects'])}. {c['note']}")
    if result.get("skipped_malformed"):
        lines.append(f"({result['skipped_malformed']} malformed case(s) skipped — "
                     f"not mined, not guessed.)")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════
# PHASE 2 STEP 13 — SPECIALISED MINERS.
#
# Four deterministic, cited miners over an analysed case set: SIM-farming,
# document-fraud rings, remittance/hawala-style flows, and movement/timeline
# patterns. Each reuses TWO existing single-sources-of-truth and adds no third:
#
#   * WHAT counts as an indicator — the Phase 1 deterministic pattern rules in
#     pattern_rules.py (rule_sim_farming_signature, rule_document_fraud_cluster,
#     rule_remittance_corridor, rule_border_movement_cluster). A subject is
#     flagged ONLY when its own typed evidence trips that rule, and the rule's
#     own triggers + supporting sources become the citation — no re-derivation,
#     no second definition of "SIM farming" etc.
#   * HOW subjects link into a ring — the Step 12 cited-link detector above
#     (mine_case_set with a link_types subset). A ring is a cluster of flagged
#     subjects joined by a SHARED CITED VALUE (operator, supplier, corridor
#     counterparty, or crossing) — never a fabricated edge.
#
# Everything here is decision support: MINING_NOTICE (association not
# culpability, human review, no identity attributes) rides on every result.
# ══════════════════════════════════════════════════════════════════════════

# Each miner: the Phase-1 rule that defines its indicator, and the Step-12
# link type(s) whose SHARED value constitutes the ring. Vocabulary-style
# config — extend the table, never special-case one investigation.
_SPECIALISED = {
    "sim_farming": {
        "rule": "rule_sim_farming_signature",
        "link_types": ("shared_phone", "shared_organization", "shared_counterparty"),
        "ring_basis": "a shared SIM line, operator, or handler",
        "description": "bulk / pre-activated SIM infrastructure across subjects",
    },
    "document_fraud_ring": {
        "rule": "rule_document_fraud_cluster",
        "link_types": ("shared_organization", "shared_counterparty"),
        "ring_basis": "a shared forged-document supplier",
        "description": "travel/identity document fraud sharing a common supplier",
    },
    "remittance_hawala": {
        "rule": "rule_remittance_corridor",
        "link_types": ("shared_counterparty",),
        "ring_basis": "a shared corridor counterparty (operator/beneficiary)",
        "description": "repeated small cross-border outflows over a shared corridor",
    },
    "movement": {
        "rule": "rule_border_movement_cluster",
        # A shared crossing is coincidence-prone and never a sole link
        # (Weakness 3): a genuine movement RING must rest on a hard
        # identifier (a shared handler/operator line or account). Shared
        # crossings then corroborate the ring or surface as context.
        "link_types": ("shared_phone", "shared_counterparty", "shared_location"),
        "ring_basis": "a shared handler/operator line or account "
                      "(shared crossings corroborate, never form the ring alone)",
        "description": "border movement converging on shared crossings, "
                       "ringed only on a shared hard identifier",
    },
}


def _run_specialised_miner(cases, miner_name: str) -> dict:
    """Flag subjects whose OWN evidence trips the miner's Phase-1 rule (cited to
    the rule's triggers/sources), then group the flagged subjects into rings
    using the Step-12 cited-link detector on the miner's link type(s)."""
    from modules import pattern_rules as PR

    spec = _SPECIALISED[miner_name]
    rule_fn = getattr(PR, spec["rule"])

    # Cross-case subject identity: same hard-identifier resolution the Step-12
    # detector uses (Weakness-1 fix). Two different people sharing a name are
    # two subjects — the second is flagged on their own evidence, never
    # dropped; only a genuinely-same person (shared personal mobile) dedupes.
    resolved, _merges, name_flags = assign_cross_case_identities(cases)

    flagged, flagged_cases, seen, skipped = [], [], set(), 0
    for (key, _f), case in zip(resolved, cases or []):
        if key is None or not isinstance(case, dict) or case.get("ontology") is None:
            skipped += 1
            continue
        if key in seen:                # one resolved identity is one subject
            continue
        try:
            match = rule_fn(case["ontology"])
        except Exception:
            match = None
        if match is None:
            continue
        seen.add(key)
        flagged.append({
            "subject": key,
            "confidence": getattr(match, "confidence", ""),
            "explanation": getattr(match, "plain_explanation", ""),
            "indicators": list(getattr(match, "triggers_met", []) or []),
            "sources": list(getattr(match, "supporting_sources", []) or []),
        })
        flagged_cases.append({"subject": key, "ontology": case["ontology"]})

    # Rings — reuse the Step 12 detector on the flagged subset, restricted to
    # this miner's link type(s). Clusters of >= 2 flagged subjects sharing a
    # cited value ARE the rings; every ring link keeps its per-subject citation.
    mined = mine_case_set(flagged_cases, link_types=spec["link_types"])

    return {
        "miner": miner_name,
        "description": spec["description"],
        "ring_basis": spec["ring_basis"],
        "subjects_flagged": flagged,
        "flagged_count": len(flagged),
        "rings": mined["clusters"],
        "ring_links": mined["links"],
        "ring_count": mined["cluster_count"],
        "unlinked_flagged_subjects": mined["unlinked_subjects"],
        "unverified_same_name": name_flags,
        "unverified_same_entity": mined.get("unverified_same_entity", []),
        "location_corroborations": mined.get("location_corroborations", []),
        "shared_location_context": mined.get("shared_location_context", []),
        "weak_phone_corroborations": mined.get("weak_phone_corroborations", []),
        "shared_landline_context": mined.get("shared_landline_context", []),
        "skipped_malformed": skipped,
        "suggestion_only": True,
        "human_review_required": True,
        "mining_notice": MINING_NOTICE,
    }


def mine_sim_farming(cases) -> dict:
    """Cross-subject SIM-farming miner (reuses rule_sim_farming_signature)."""
    return _run_specialised_miner(cases, "sim_farming")


def mine_document_fraud_rings(cases) -> dict:
    """Cross-subject document-fraud-ring miner (reuses rule_document_fraud_cluster)."""
    return _run_specialised_miner(cases, "document_fraud_ring")


def mine_remittance_hawala(cases) -> dict:
    """Cross-subject remittance/hawala miner (reuses rule_remittance_corridor)."""
    return _run_specialised_miner(cases, "remittance_hawala")


def mine_movement_patterns(cases) -> dict:
    """Cross-subject movement/timeline miner (reuses rule_border_movement_cluster)."""
    return _run_specialised_miner(cases, "movement")


def run_all_specialised_miners(cases) -> dict:
    """Run all four specialised miners over one analysed case set."""
    return {name: _run_specialised_miner(cases, name) for name in _SPECIALISED}


def render_specialised_result(result: dict) -> str:
    """Analyst-facing plain-text rendering of one specialised-miner result."""
    if not isinstance(result, dict):
        return ""
    lines = [f"SPECIALISED MINER — {str(result.get('miner', '')).upper()} "
             f"({result.get('description', '')}) — DECISION SUPPORT ONLY",
             str(result.get("mining_notice") or MINING_NOTICE), ""]
    flagged = result.get("subjects_flagged") or []
    if not flagged:
        lines.append("No subject's own evidence trips this indicator.")
    for f in flagged:
        lines.append(f"FLAGGED: {f['subject']} [{f.get('confidence', '?')}] — "
                     f"{f.get('explanation', '')}")
        for ind in f.get("indicators", []):
            lines.append(f"    indicator: {ind}")
        if f.get("sources"):
            lines.append(f"    sources: {', '.join(str(s) for s in f['sources'])}")
    rings = result.get("rings") or []
    if rings:
        lines.append(f"RINGS ({result.get('ring_basis', 'a shared cited value')}):")
        for i, r in enumerate(rings, 1):
            lines.append(f"  RING {i}: {', '.join(r['subjects'])} "
                         f"({r['link_count']} cited link(s))")
        for l in (result.get("ring_links") or []):
            lines.append(f"    [{l['type'].upper()}] {l['value']} — "
                         f"shared by: {', '.join(l['subjects'])}")
            for subj in l["subjects"]:
                for cite in l["citations"].get(subj, []):
                    lines.append(f"        {subj}: \"{cite['raw']}\" — {cite['source']}")
    elif flagged:
        lines.append("No cited link between the flagged subjects — each flag "
                     "stands alone (NOT presented as a ring).")
    if result.get("skipped_malformed"):
        lines.append(f"({result['skipped_malformed']} case(s) without a typed "
                     f"ontology skipped — not mined, not guessed.)")
    return "\n".join(lines)
