"""
AETHERLENS — Statute-Era Gate.

Ported from the Chiron-style era mechanism in the sibling product
(NyayaVajra `ingestion/tagging.py`): statute era is detectable from content —
which statutes a text cites — via deterministic keyword matching, and an
era conflict is surfaced, never silently mixed.

India replaced its criminal-procedure era in 2023/24:
    IPC 1860  → BNS 2023   (Bharatiya Nyaya Sanhita)
    CrPC 1973 → BNSS 2023  (Bharatiya Nagarik Suraksha Sanhita)
    IEA 1872  → BSA 2023   (Bharatiya Sakshya Adhiniyam)

Current matters must cite the NEW era. Special acts (PMLA, FEMA, IT Act,
NDPS, DPDP, TRAI, Passport Act, Foreigners Act, Aadhaar Act, MLAT) were not
repealed and pass through untouched.

`enforce_new_era` rewrites old-era citations to their published new-era
equivalents. Where a specific section has no entry in the equivalence table,
the act name is modernised and the original section is kept visibly flagged
for verification — a section number is NEVER invented.
"""

from __future__ import annotations

import re

# ── Detection (ported) ────────────────────────────────────────────────────────
# Old regime: IPC 1860, CrPC 1973, Indian Evidence Act 1872.
_OLD_ERA = re.compile(
    r"\b(indian penal code|i\.?p\.?c\.?|code of criminal procedure|cr\.?p\.?c\.?|"
    r"indian evidence act|evidence act,? 1872)\b",
    re.IGNORECASE,
)
# New regime (2023/24): BNS, BNSS, BSA.
_NEW_ERA = re.compile(
    r"\b(bharatiya nyaya sanhita|bnss|bns|bsa|bharatiya nagarik suraksha sanhita|"
    r"bharatiya sakshya adhiniyam)\b",
    re.IGNORECASE,
)


def detect_statute_era(text: str) -> str:
    """'old' / 'new' / 'mixed' / 'unknown' — which statute era a text cites."""
    s = str(text or "")
    old = bool(_OLD_ERA.search(s))
    new = bool(_NEW_ERA.search(s))
    if old and new:
        return "mixed"
    if old:
        return "old"
    if new:
        return "new"
    return "unknown"


# ── Published section equivalences (only well-established mappings) ──────────
CRPC_TO_BNSS = {
    "41":   "35",       # arrest without warrant
    "41A":  "35(3)",    # notice of appearance
    "91":   "94",       # summons to produce documents
    "93":   "96",       # search warrant
    "154":  "173",      # FIR
    "160":  "179",      # attendance of witnesses
    "161":  "180",      # police examination of witnesses
    "164":  "183",      # statements before magistrate
    "166A": "112",      # letter rogatory
    "167":  "187",      # remand
    "173":  "193",      # police report / chargesheet
    "438":  "482",      # anticipatory bail
    "482":  "528",      # inherent powers
}
IEA_TO_BSA = {
    "65B": "63",        # electronic-records certificate
    "65A": "62",
    "45":  "39",        # expert opinion
}
IPC_TO_BNS = {
    "420":  "318(4)",   # cheating
    "406":  "316",      # criminal breach of trust
    "120B": "61(2)",    # criminal conspiracy
}

_DASH = r"[—–\-]"


def _map_sections(secs: str, table: dict, new_act: str, old_desc: str) -> str:
    """Map a '41/41A'-style section list through `table`. Unknown sections are
    kept, visibly flagged — never given an invented number. `old_desc` names
    the repealed act WITHOUT its era keywords, so the flag text survives the
    residual act-name rewrites and never re-trips the era detector."""
    mapped, flagged = [], []
    for sec in re.split(r"\s*/\s*", secs.strip()):
        key = sec.strip().upper()
        if not key:
            continue
        if key in table:
            mapped.append(table[key])
        else:
            flagged.append(sec.strip())
    parts = []
    if mapped:
        uniq = list(dict.fromkeys(mapped))
        parts.append(f"{new_act} Section {'/'.join(uniq)}")
    for sec in flagged:
        parts.append(f"{new_act} 2023 (equivalent of §{sec} {old_desc} — verify section)")
    return " / ".join(parts)


_SEC_LIST = r"([0-9]+[A-Za-z]?(?:\s*/\s*[0-9]+[A-Za-z]?)*)"


def enforce_new_era(text) -> str:
    """Rewrite old-era statutory citations to the new era (BNS/BNSS/BSA).

    Deterministic, table-driven; special acts untouched; unknown sections
    surfaced for verification rather than invented. Idempotent on new-era
    text.
    """
    s = str(text or "")
    if not s:
        return s

    # 1) Section 65B — the electronic-records certificate. It belongs to the
    #    Evidence Act 1872 (frequently mislabelled "IT Act 65B"); either way
    #    the current citation is BSA 2023 Section 63.
    s = re.sub(rf"IT Act(?:,? 2000)?\s*{_DASH}*\s*Section\s+65\s*B",
               "BSA 2023 — Section 63 (electronic records)", s, flags=re.I)
    s = re.sub(r"Section\s+65\s*B\s+IT Act", "Section 63 BSA 2023", s, flags=re.I)
    s = re.sub(rf"(?:Indian\s+)?Evidence Act(?:,? 1872)?\s*{_DASH}*\s*Section\s+65\s*B",
               "BSA 2023 — Section 63", s, flags=re.I)
    s = re.sub(r"Section\s+65\s*B\b(?:\s+of\s+the\s+(?:Indian\s+)?Evidence Act(?:,? 1872)?)?",
               "Section 63 BSA 2023", s, flags=re.I)

    # 2) CrPC sections → BNSS ("Section 91 CrPC" and "CrPC Section 41/41A")
    s = re.sub(rf"Section[s]?\s+{_SEC_LIST}\s+(?:of\s+the\s+)?CrPC\b(?:,?\s*1973)?",
               lambda m: _map_sections(m.group(1), CRPC_TO_BNSS, "BNSS", "of the repealed 1973 procedure code"),
               s, flags=re.I)
    s = re.sub(rf"\bCrPC(?:,?\s*1973)?\s*{_DASH}*\s*(?:Section[s]?\s+)?{_SEC_LIST}",
               lambda m: _map_sections(m.group(1), CRPC_TO_BNSS, "BNSS", "of the repealed 1973 procedure code"),
               s, flags=re.I)

    # 3) IEA sections → BSA
    s = re.sub(rf"Section[s]?\s+{_SEC_LIST}\s+(?:of\s+the\s+)?(?:Indian\s+)?Evidence Act(?:,?\s*1872)?",
               lambda m: _map_sections(m.group(1), IEA_TO_BSA, "BSA", "of the repealed 1872 evidence act"),
               s, flags=re.I)
    s = re.sub(rf"(?:Indian\s+)?Evidence Act(?:,?\s*1872)?\s*{_DASH}*\s*(?:Section[s]?\s+)?{_SEC_LIST}",
               lambda m: _map_sections(m.group(1), IEA_TO_BSA, "BSA", "of the repealed 1872 evidence act"),
               s, flags=re.I)

    # 4) IPC sections → BNS
    s = re.sub(rf"Section[s]?\s+{_SEC_LIST}\s+(?:of\s+the\s+)?(?:Indian Penal Code|IPC)(?:,?\s*1860)?",
               lambda m: _map_sections(m.group(1), IPC_TO_BNS, "BNS", "of the repealed 1860 penal code"),
               s, flags=re.I)
    s = re.sub(rf"\b(?:Indian Penal Code|IPC)(?:,?\s*1860)?\s*{_DASH}*\s*(?:Section[s]?\s+)?{_SEC_LIST}",
               lambda m: _map_sections(m.group(1), IPC_TO_BNS, "BNS", "of the repealed 1860 penal code"),
               s, flags=re.I)

    # 5) Residual bare act names (no section attached)
    s = re.sub(r"\b(?:the\s+)?Code of Criminal Procedure(?:,?\s*1973)?\b|\bCrPC(?:,?\s*1973)?\b",
               "BNSS 2023", s, flags=re.I)
    s = re.sub(r"\b(?:Indian\s+)?Evidence Act(?:,?\s*1872)?\b|\bIEA(?:,?\s*1872)?\b",
               "BSA 2023", s, flags=re.I)
    s = re.sub(r"\bIndian Penal Code(?:,?\s*1860)?\b|\bIPC(?:,?\s*1860)?\b",
               "BNS 2023", s, flags=re.I)

    # 6) Collapse exact-duplicate " / " segments introduced by mapping
    #    ("BNSS Section 35 / BNSS Section 35" → one)
    parts = [p.strip() for p in s.split(" / ")]
    if len(parts) > 1:
        s = " / ".join(dict.fromkeys(parts))
    return s
