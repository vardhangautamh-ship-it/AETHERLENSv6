# AETHERLENS — Ontology Layer & Pattern Analysis: Plain-English Build Report

## In one paragraph (read only this if nothing else)

We taught AETHERLENS to do something new: instead of only *listing* what it found
about a person (phone numbers, deletions, foreign property, court actions), it now
*reasons* about what those findings **mean together** and writes that meaning into
a new report section called **Pattern Analysis (§09B)**. The reasoning is done by a
fixed library of plain "if-this-and-this-then-that" rules — so the same case file
always produces the exact same conclusions, every single time, which is what makes
the output defensible. An AI language model is used **only** at the very end, and
**only** to rewrite those already-decided conclusions in smoother English. If you
switch the AI off completely, the section still reports every single conclusion —
just in plainer words. The AI can never invent a finding.

---

## 1. What was built, in plain terms

**What is an "ontology layer"?**
Think of it as a clean, labelled filing system for one case. Raw investigation data
arrives messy — names, numbers, dates, and notes all mixed together. The ontology
layer sorts every relevant fact into a labelled box: *people*, *phone numbers*,
*organisations*, *bank transactions*, *property*, *communication channels* (like
encrypted apps or VPNs), *legal proceedings*, *deletion events*, and *timeline
events*. Once everything is filed into the right boxes, a machine can reason over it
reliably. The ontology layer is simply the step that turns the mess into tidy,
typed boxes.

**What does the new Pattern Analysis section do for an analyst?**
A §09 "Anomalies & Flags" list might tell an analyst: *the subject has three phones,
uses encrypted chat, owns property in Dubai, has a lookout circular, and deleted a
device.* Each of those is a separate dot. **Pattern Analysis joins the dots.** It
says, in effect: *"these specific facts, taken together, match a known pattern —
for example, a flight-risk profile, or a money-laundering layering structure."* It
states which pattern it sees, how confident it is, and — crucially — exactly which
facts triggered that conclusion, so the analyst can check the reasoning. It does the
interpretive thinking that an analyst would otherwise have to do by hand, and it
shows its work.

---

## 2. The patterns the system can now detect (each in one sentence)

**Financial-crime patterns**

1. **Layering Structure** — Money is broken into small deposits and then wired
   abroad through a shell company, the classic way dirty money is disguised.
2. **Offshore Flight Risk** — The subject has a live lookout circular, owns property
   abroad, and has international contacts, suggesting they may flee the country.
3. **Operational Security** — The subject uses several phones (often "burners") plus
   encrypted messaging, suggesting deliberate effort to hide communications.
4. **Shell Layering Network** — Several associates are all routed through one
   offshore company, suggesting an organised money-moving network.
5. **Enforcement History Escalation** — The subject has faced three or more
   enforcement actions across multiple years and agencies, marking them as a
   persistent target of interest.

**Cyber-crime patterns**

6. **Operational Scale Mismatch** — The money spent or data moved is far too large
   for the harmless purpose the subject claims (e.g. "just research").
7. **Anti-Forensic Behaviour** — Files or devices were deleted right after an
   official inquiry began, suggesting the subject knew evidence was being sought.
8. **Counter-Surveillance** — Use of VPNs, encryption, and separated platforms
   together suggests the subject is actively trying to avoid being watched.

**General patterns**

9. **Network Hub** — The subject is the central link connecting otherwise-separate
   associates, i.e. the person who holds the network together.
10. **Timeline Cluster** — A burst of significant events in a short window, marking
    a period of unusually high activity.

When none of these match, the section says so plainly — it never invents a pattern
to look busy.

---

## 3. Why the intelligence is deterministic — and why that matters legally

**Deterministic** means: same input → same output, always. Every conclusion in
Pattern Analysis comes from fixed rules with no randomness, no guessing, and no
reliance on an AI's mood that day. Run the same case file a hundred times and you
get the identical patterns, in the identical order, with the identical wording.

Why this matters in a legal setting:

- **Reproducibility.** If a defence expert re-runs the same data, they get the same
  result. The conclusion can be independently checked rather than taken on faith.
- **Explainability.** Each conclusion carries its *triggers* — the exact facts that
  produced it. There is no "the computer decided"; there is "the computer concluded
  X **because** of facts A, B, and C."
- **Admissibility.** Courts are rightly suspicious of opaque, unpredictable AI
  output. A transparent, rule-based, repeatable process is far easier to defend than
  a black box that might say something different next time.

This is the single most important design choice in the whole build, because this
section is *interpretive* — it tells the analyst what the data **means** — and
interpretation is the most legally sensitive thing a report can do.

---

## 4. Exactly what role the AI plays (and why it can be switched off)

The AI language model is a **narrator, not an investigator.** After the rules have
already decided every conclusion, the AI may be asked to write one tidy paragraph
that strings those conclusions together in readable prose. That paragraph is clearly
stamped **`[AI NARRATIVE] — non-factual synthesis`**, sitting beneath the
deterministic findings which are stamped **`[DETERMINISTIC ANALYSIS]`**.

The AI is fenced in three ways:
- It is **only** given the conclusions the rules already produced.
- It is **explicitly instructed** to add nothing new — no new fact, name, number,
  date, or conclusion.
- It is **never required.** If the AI service is unavailable, disabled, or errors
  out, the section simply omits the prose paragraph and shows every conclusion in
  plain rule-generated language.

We tested this directly: with the AI switched off, **not a single conclusion
disappears.** The AI adds readability; it never adds intelligence. That is the line
that keeps the legally sensitive content trustworthy.

---

## 5. Files created and existing files touched

**New files (the new capability):**
- `modules/pattern_rules.py` — the 10 deterministic pattern rules + the result type.
- `modules/pattern_engine.py` — runs all rules over a case and sorts the results.

**Existing files changed:**
- `modules/ontology.py` — the new typed "filing system" (the 9 entity types, the
  `Ontology` container, and the `build_ontology()` builder) was **added into the
  existing ontology module** so there is only ever one ontology file.
- `modules/report_generator.py` — builds the new §09B section once for both report
  pipelines, renders it in the PDF, and holds the optional AI-narrative wrapper.
- `app.py` — displays the new §09B section on screen, right after §09.

**New test files (proof it works):**
- `test_pattern_rules.py` (25 checks) — each rule fires correctly and stays silent
  when it shouldn't; identical output across repeated runs.
- `test_ontology_build.py` (25) — messy data is correctly filed into typed boxes.
- `test_pattern_engine.py` (10) — rules run, sort by confidence, detect case type,
  and produce identical results on repeated runs.
- `test_pattern_section.py` (14) — the report section is built and rendered
  correctly, in the right place, identically each run.
- `test_pattern_narrative.py` (14) — the AI narrative is optional, constrained, and
  removes zero conclusions when switched off.

All five suites pass: **88 checks total.**

---

## 6. Any duplicate or dead code removed

This build **added** a capability rather than cleaning one up, so little was removed.
The one consolidation decision worth recording: an `ontology.py` module already
existed (a heavier "digital twin" system). Rather than create a second, competing
ontology file, the new pattern-analysis filing system was **placed inside that same
module**, in a clearly labelled section, with names chosen so they do not clash with
the existing ones. The existing digital-twin code and its risk-score function were
left untouched. (Separately, earlier consolidation work — single phone validator,
single name regex, recalibrated risk formula — is documented in
`CONSOLIDATION_REPORT.md`; it is not part of this build.)

---

## 7. What is NOT yet built / known limitations

- **Financial detail depends on upstream extraction.** The rules can detect layering,
  scale-mismatch, etc., but only as well as the pipeline supplies *transactions*,
  *organisations*, and *property* in a structured form. Today some of those are
  inferred from flag text rather than parsed from source documents; richer financial
  parsing would make the financial rules fire more often and more precisely.
- **The pattern library is a starting set of 10.** It covers common financial and
  cyber cases but is not exhaustive; new rules can be added one at a time using the
  same deterministic template.
- **Network and timeline rules need enough data.** "Network Hub" needs a graph with
  several connected people; "Timeline Cluster" needs enough dated, high-significance
  events. Thin cases will correctly produce fewer patterns (the section says so
  rather than guessing).
- **The AI narrative is English prose only.** It is deliberately powerless over
  conclusions, so it adds polish, not analysis — by design.
- **Confidence levels (Strong/Moderate/Weak) are rule-of-thumb thresholds.** They
  are deterministic and explainable, but the specific cut-offs are a reasonable
  first calibration, not an empirically tuned standard.

---

*Prepared for review. No code has been committed or pushed; the file list in Section
5 is provided so the changes can be reviewed and version-controlled separately.*
