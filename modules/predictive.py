"""
PHASE 4 STEP 15 — PREDICTIVE ELEMENTS (bounded, cautious).

The ONLY predictive element built here is RECURRENCE PROJECTION of a behaviour
that is ALREADY DEMONSTRATED and dated in the cited evidence: when the same
typed behaviour has recurred >= _MIN_OCCURRENCES times on parseable dates, we
surface — as a WEAK, SPECULATIVE, human-review, NON-DETERMINATIVE suggestion —
that "IF the pattern continues it may recur around <projected date>." The
projection extrapolates the OBSERVED series by exactly one median interval; it
invents no new behaviour and asserts no escalation.

WHAT IS DELIBERATELY NOT BUILT (would require speculation — so, per the step,
we don't build it and we SAY SO, both here and in the result's `not_built`):

  * predicting a NEW kind of behaviour the subject has not already demonstrated;
  * predicting ESCALATION to conduct not evidenced in cited data;
  * predicting from IDENTITY attributes (nationality/ethnicity/religion) or any
    profile trait — never an input, never a predictor (no predictive policing);
  * projecting from < _MIN_OCCURRENCES occurrences, or from undated / unparseable
    dates, or by auto-classifying free-text so a "pattern" appears where the
    cited data does not establish one.

HARD CONSTRAINT (Phase 4): every projection is labelled speculative, evidence-
grounded, weak, and non-determinative; autonomous=False and
human_review_required=True on every result; PREDICTION_NOTICE rides on every
result. Deterministic and general — same input → same output, no case-name /
subject-name / file-name branches.
"""
import statistics
from datetime import date, datetime

# Verbatim on every result. Do not shorten.
PREDICTION_NOTICE = (
    "SPECULATIVE — FOR HUMAN REVIEW ONLY: each item below extrapolates a "
    "behaviour ALREADY DEMONSTRATED and dated in the cited evidence by one "
    "median interval. It is a WEAK, non-determinative suggestion that a "
    "demonstrated pattern MAY continue — NOT a prediction that it will, NOT a "
    "determination of guilt, and NOT a directive to act. A human officer must "
    "weigh it. No new behaviour, no escalation, and no identity attribute "
    "(nationality, ethnicity, or religion) is used or predicted."
)

_MIN_OCCURRENCES = 3          # below this, a series is not a groundable pattern
_MAX_PROJECT_STEPS = 1        # bounded: only ONE median interval ahead, ever

_DATE_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d",
                 "%d %b %Y", "%d %B %Y", "%b %d %Y", "%B %d %Y", "%Y.%m.%d")


def _get(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _norm(s) -> str:
    return " ".join(str(s or "").strip().lower().split())


def _parse_date(s):
    """Deterministic parse to a date, or None. ISO first, then common formats.
    Anything ambiguous or unparseable returns None (never guessed)."""
    txt = str(s or "").strip()
    if not txt:
        return None
    try:
        return date.fromisoformat(txt[:10])
    except ValueError:
        pass
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(txt, fmt).date()
        except ValueError:
            continue
    return None


def _transaction_series(onto) -> dict:
    """Group transactions into demonstrated behaviour series keyed by
    (direction, cross_border, normalised counterparty). Returns
    {series_key: {"label", "items": [{date, raw, source}], "undated": n}}.
    Reads only typed transaction fields — no identity attributes."""
    series: dict = {}
    for t in (_get(onto, "transactions") or []):
        cp_raw = str(_get(t, "counterparty", "") or "").strip()
        cp = _norm(cp_raw)
        if not cp:
            continue
        direction = _norm(_get(t, "direction", "")) or "transfer"
        cross = bool(_get(t, "cross_border", False))
        key = (direction, cross, cp)
        label = (f"{direction} {'cross-border ' if cross else ''}"
                 f"transfer to {cp_raw}")
        entry = series.setdefault(key, {"label": label, "items": [], "undated": 0})
        d = _parse_date(_get(t, "date", ""))
        if d is None:
            entry["undated"] += 1
        else:
            entry["items"].append({"date": d.isoformat(),
                                   "raw": (f"{_get(t, 'direction', '')} "
                                           f"{_get(t, 'amount', '')} to {cp_raw}").strip(),
                                   "source": str(_get(t, "source", "")
                                                 or "source not recorded in the analysed case")})
    return series


def _project_series(label: str, items: list, undated: int) -> dict | None:
    """Build a bounded recurrence projection from a dated occurrence series,
    or record WHY it can't be grounded (returned under key '_not_built')."""
    dated = sorted(items, key=lambda i: i["date"])
    if len(dated) < _MIN_OCCURRENCES:
        return {"_not_built": (f"{label}: only {len(dated)} dated occurrence(s)"
                               + (f" (+{undated} undated)" if undated else "")
                               + f" — need >= {_MIN_OCCURRENCES} to ground a "
                               f"recurrence without speculation")}
    days = [date.fromisoformat(d["date"]).toordinal() for d in dated]
    intervals = [b - a for a, b in zip(days, days[1:])]
    median_iv = int(statistics.median(intervals))
    if median_iv <= 0:
        return {"_not_built": (f"{label}: {len(dated)} occurrences but no positive "
                               f"interval between them — cadence not groundable")}
    last = date.fromordinal(days[-1])
    projected = date.fromordinal(days[-1] + median_iv * _MAX_PROJECT_STEPS)
    irregular = intervals and (max(intervals) > 3 * max(median_iv, 1))
    basis = (f"{len(dated)} cited occurrence(s) between {dated[0]['date']} and "
             f"{dated[-1]['date']} at a median interval of ~{median_iv} day(s)"
             + (f"; intervals are irregular ({min(intervals)}–{max(intervals)} days), "
                f"so the projected date is only a rough extrapolation" if irregular else ""))
    return {
        "label": label,
        "occurrences": len(dated),
        "first_date": dated[0]["date"],
        "last_date": dated[-1]["date"],
        "median_interval_days": median_iv,
        "projected_next_date": projected.isoformat(),
        "irregular_cadence": bool(irregular),
        "confidence": "WEAK",
        "speculative": True,
        "determinative": False,
        "basis": basis,
        "citations": dated,
        "suggestion": (f"SPECULATIVE (WEAK) — FOR HUMAN REVIEW: the demonstrated "
                       f"pattern '{label}' recurred {len(dated)} time(s) through "
                       f"{dated[-1]['date']}; IF it continues on the same cadence "
                       f"it may recur around {projected.isoformat()}. This is not "
                       f"a determination that it will occur."),
    }


def predict_from_ontology(onto) -> dict:
    """Surface bounded recurrence projections from a case's cited, dated typed
    evidence. Deterministic; speculative-and-weak by construction; refuses (and
    records the reason in `not_built`) anything it cannot ground."""
    predictions, not_built = [], []
    if onto is not None:
        for key in sorted(_transaction_series(onto)):
            s = _transaction_series(onto)[key]
            out = _project_series(s["label"], s["items"], s["undated"])
            if out is None:
                continue
            if "_not_built" in out:
                not_built.append(out["_not_built"])
            else:
                predictions.append(out)
    predictions.sort(key=lambda p: (-p["occurrences"], p["projected_next_date"], p["label"]))
    not_built.sort()

    return {
        "predictions": predictions,
        "prediction_count": len(predictions),
        # Realises the step's rule: if it can't be grounded, we DON'T build it —
        # and we say so, here, per series.
        "not_built": not_built,
        "human_review_required": True,
        "autonomous": False,
        "determinative": False,
        "prediction_notice": PREDICTION_NOTICE,
    }


def render_predictions(result: dict) -> str:
    """Officer-facing plain-text rendering of a predict_from_ontology result."""
    if not isinstance(result, dict):
        return ""
    lines = ["PREDICTIVE ELEMENTS (SPECULATIVE, WEAK — FOR HUMAN REVIEW ONLY)",
             str(result.get("prediction_notice") or PREDICTION_NOTICE), ""]
    preds = result.get("predictions") or []
    if not preds:
        lines.append("No recurrence could be grounded in cited dated evidence "
                     "without speculation — no predictive element built.")
    for p in preds:
        lines.append(f"[WEAK / SPECULATIVE] {p['suggestion']}")
        lines.append(f"    basis: {p['basis']}")
        for c in p["citations"]:
            lines.append(f"    occurrence {c['date']}: \"{c['raw']}\" — {c['source']}")
    if result.get("not_built"):
        lines.append("NOT BUILT (insufficient cited grounding — stated, not hidden):")
        for reason in result["not_built"]:
            lines.append(f"    · {reason}")
    return "\n".join(lines)
