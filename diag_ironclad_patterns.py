"""
TEMP DIAGNOSTIC (delete after use) — why does IRONCLAD detect zero patterns?

Reproduces the EXACT argument shapes the report pipeline passes to
run_pattern_analysis (see report_generator._generate_report_inner):

    run_pattern_analysis(
        person        = person,                                # dict
        entities      = (graph_data or {}).get("entities", []),# LIST of {id,label,type}
        flags         = sections["anomalies_and_flags"]["flags"],
        timeline      = timeline_data,                         # {"events":[...]}
        phones        = person["phones_found"],
        financial_data= assets_data,                           # LIST of flat row-dicts
    )

It changes NO logic. It only feeds build_ontology the way the live pipeline
feeds it, with PA_DEBUG on, then reports per-rule why each returned None.
Run:  PA_DEBUG=1 python diag_ironclad_patterns.py
"""
import os, csv, glob
os.environ.setdefault("PA_DEBUG", "1")

from modules.report_generator import inject_keyword_flags_from_docs
from modules.pattern_engine import run_pattern_analysis
from modules import pattern_rules as PR
from modules.ontology import build_ontology

DESK = os.path.join(os.path.expanduser("~"), "OneDrive", "Desktop")

def _read(name):
    p = os.path.join(DESK, name)
    try:
        with open(p, encoding="utf-8", errors="ignore") as f:
            return f.read()
    except Exception:
        return ""

def _rows(name):
    p = os.path.join(DESK, name)
    try:
        with open(p, encoding="utf-8", errors="ignore") as f:
            return list(csv.DictReader(f))
    except Exception:
        return []

# ── 1. person dict (as resolver would produce it) ─────────────────────────────
person = {
    "confirmed_name": "Daniyal Farooqui",
    "name": "Daniyal Farooqui",
    "phones_found": [
        {"number": "+91-98300-71142"}, {"number": "+91-98300-71143"},
        {"number": "+91-90510-33218"}, {"number": "+91-90510-33219"},
        {"number": "+44-7700-900812"},
    ],
    "anomaly_flags": [],
}

# ── 2. flags — exactly how the pipeline derives them: keyword scan over docs ───
raw_documents = [
    {"filename": n, "raw_text": _read(n)}
    for n in ("IRONCLAD_04_incident_log.txt",
              "IRONCLAD_02_call_records.csv", "IRONCLAD_03_crypto_flow.csv",
              "IRONCLAD_05_platform_metadata.csv", "IRONCLAD_06_email_receipts.csv")
]
inject_keyword_flags_from_docs(person, raw_documents)
flags = list(person.get("anomaly_flags", []))

# ── 3. entities — as relationship_mapper emits: LIST of node dicts ────────────
entities = [
    {"id": "n0", "label": "Daniyal Farooqui", "type": "person"},
    {"id": "n1", "label": "Rohit Banik", "type": "person"},
    {"id": "n2", "label": "Kevin D'Souza", "type": "person"},
    {"id": "n3", "label": "Farida Sheikh", "type": "person"},
    {"id": "n4", "label": "Ironclad SecOps", "type": "organization"},
]

# ── 4. financial_data — as app.py builds assets_data: LIST of flat row-dicts ──
#    (crypto_flow.csv columns are date/transaction_type/amount_inr_equiv/... —
#     NOT direction/amount/cross_border/structured)
assets_data = [
    {"source_file": "IRONCLAD_03_crypto_flow.csv", **r} for r in _rows("IRONCLAD_03_crypto_flow.csv")
]

# ── 5. timeline — built from the docs (as the live pipeline does) ─────────────
timeline = {"events": []}

# ── 6. records — flat structured rows from every CSV (HOP 3 source rows) ───────
records = []
for n in ("IRONCLAD_02_call_records.csv", "IRONCLAD_03_crypto_flow.csv",
          "IRONCLAD_05_platform_metadata.csv", "IRONCLAD_06_email_receipts.csv"):
    records.extend(_rows(n))

print("\n##### FLAGS THE PIPELINE PRODUCES FOR IRONCLAD #####")
for f in flags:
    print("   -", (f.get("flag") if isinstance(f, dict) else f))

# ── run exactly as the pipeline does ──────────────────────────────────────────
result = run_pattern_analysis(
    person=person, entities=entities, flags=flags,
    timeline=timeline, graph=None,
    phones=person["phones_found"], financial_data=assets_data, records=records,
)

print("\n##### ENGINE RESULT #####")
print("  case_type_detected:", result["case_type_detected"])
print("  patterns fired    :", [p.pattern_id for p in result["patterns"]])
print("  counts            :", result["counts"])

# ── per-rule: what it received + why None ─────────────────────────────────────
onto = build_ontology(person, entities, flags, timeline, None,
                      person["phones_found"], assets_data, records)

def n(s): return str(s or "").strip().lower()

print("\n##### PER-RULE: WHAT IT RECEIVED & WHY IT RETURNED None #####")
exp = {"LAYERING_STRUCTURE", "OPERATIONAL_SECURITY",
       "ANTI_FORENSIC_BEHAVIOUR", "COUNTER_SURVEILLANCE", "OPERATIONAL_SCALE_MISMATCH"}
for rule in PR.ALL_RULES:
    pid = [k for k, v in PR.RULES_BY_ID.items() if v is rule][0]
    fired = rule(onto)
    tag = "  <-- EXPECTED FOR IRONCLAD" if pid in exp else ""
    print(f"\n[{pid}] -> {'FIRED ('+fired.confidence+')' if fired else 'None'}{tag}")
    if pid == "LAYERING_STRUCTURE":
        struct = [t for t in onto.transactions if PR._is_structured_deposit(t)]
        wires = [t for t in onto.transactions if n(t.direction) == "out" and t.cross_border]
        shells = [o for o in onto.organizations if n(o.type) in ("shell", "front")]
        print(f"    structured deposits={len(struct)}  cross-border wires={len(wires)}  shell orgs={len(shells)}")
        print(f"    => needs all three >0; organizations list is {len(onto.organizations)} long")
    elif pid == "OPERATIONAL_SECURITY":
        enc = [c for c in onto.comm_channels if c.encrypted or n(c.type) in PR._ENCRYPTED_CHANNEL_TYPES]
        print(f"    phones={len(onto.phones)} (need>=3)  encrypted channels={len(enc)} (need>=1)")
    elif pid == "ANTI_FORENSIC_BEHAVIOUR":
        dels = onto.deletion_events
        inq = [lp for lp in onto.legal_proceedings if n(lp.kind) in ("inquiry", "notice")]
        inq_dates = [PR._parse_date(lp.date) for lp in inq]
        print(f"    deletion events={len(dels)} dates={[d.timestamp for d in dels]}")
        print(f"    inquiry/notice procs={len(inq)} parsable_dates={[str(d.date()) if d else None for d in inq_dates]}")
        print(f"    => needs deletion date AND inquiry date within 0-30d; dates present? {any(d for d in inq_dates)}")
    elif pid == "COUNTER_SURVEILLANCE":
        vpn = [c for c in onto.comm_channels if n(c.type) == "vpn"]
        enc = [c for c in onto.comm_channels if c.encrypted or n(c.type) in PR._ENCRYPTED_CHANNEL_TYPES]
        plat = sorted({n(c.type) for c in onto.comm_channels if n(c.type)})
        print(f"    vpn channels={len(vpn)}  encrypted={len(enc)}  distinct platforms={plat} (need>=2)")
    elif pid == "OPERATIONAL_SCALE_MISMATCH":
        benign = any(any(tok in n(f if isinstance(f, str) else f.get('flag','')) for tok in PR._BENIGN_PURPOSE_TOKENS) for f in onto.flags)
        out = sum(float(t.amount or 0) for t in onto.transactions if n(t.direction) == "out")
        print(f"    benign-purpose claim in flags={benign}  outbound spend total={out}")
