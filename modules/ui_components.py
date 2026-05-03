"""
AetherLens — UI Component Library
HTML/CSS building blocks that match the 6-screen design system.
All functions return raw HTML strings; callers use
  st.markdown(html, unsafe_allow_html=True)
"""

import datetime

# ── Design token constants (mirrors tokens.css) ───────────────────────────────
_T = {
    "void":    "#000000",
    "abyss":   "#05000D",
    "deep":    "#0A0015",
    "card":    "#100020",
    "mid":     "#1E0040",
    "line":    "#221040",
    "p900":    "#3B0764",
    "p700":    "#6B21A8",
    "p600":    "#7B2FBE",
    "p500":    "#9D4EDD",
    "p300":    "#C084FC",
    "p100":    "#E9D5FF",
    "txt":     "#F0EAD6",
    "txt2":    "#9CA3AF",
    "dim":     "#4B5563",
    "faint":   "#2A2438",
    "online":  "#16A34A",
    "warn":    "#D97706",
    "crit":    "#DC2626",
    "info":    "#2563EB",
    "teal":    "#0D9488",
    "border":  "rgba(123,47,190,0.28)",
    "bsoft":   "rgba(123,47,190,0.14)",
    "bactive": "rgba(123,47,190,0.80)",
    "bhair":   "rgba(240,234,214,0.06)",
}


# ── 1. Classification strip ────────────────────────────────────────────────────

def classification_strip(
    left:   str = "RESTRICTED SYSTEM",
    center: str = "AETHERLENS · INTELLIGENCE FUSION PLATFORM",
    right:  str | None = None,
) -> str:
    """Top banner present on every screen — uses .al-classification-strip CSS class."""
    if right is None:
        now = datetime.datetime.utcnow()
        right = f"UTC {now.strftime('%H:%M:%S')}"
    return f"""
<div class="al-classification-strip">
  <div><span class="dot"></span>{left}</div>
  <div class="center">{center}</div>
  <div>{right}</div>
</div>"""


# ── 2. Page topbar (below classification strip) ────────────────────────────────

def topbar(breadcrumbs: list[str], right_html: str = "") -> str:
    """
    breadcrumbs = ["OPERATIONS", "FUSION MODE", "OP 4F9A"]
    The last item is highlighted purple-300.
    Uses .topbar / .crumbs CSS classes.
    """
    sep = '<span class="sep">&#x203A;</span>'
    parts = []
    for i, crumb in enumerate(breadcrumbs):
        if i == len(breadcrumbs) - 1:
            parts.append(f'<span class="cur">{crumb}</span>')
        else:
            parts.append(f'<span>{crumb}</span>')
    crumb_html = sep.join(parts)

    return f"""
<div class="topbar">
  <div class="left">
    <div class="crumbs">{crumb_html}</div>
  </div>
  <div class="right" style="display:flex;gap:10px;align-items:center;">{right_html}</div>
</div>"""


# ── 3. Pill badge ──────────────────────────────────────────────────────────────

def pill(text: str, variant: str = "default") -> str:
    """
    variant: default | verified | ai | unverified | restricted | online
    """
    styles = {
        "default":    f"color:{_T['txt2']};border-color:{_T['border']};",
        "verified":   f"color:{_T['online']};border-color:rgba(22,163,74,0.4);",
        "ai":         f"color:{_T['warn']};border-color:rgba(217,119,6,0.4);",
        "unverified": f"color:{_T['crit']};border-color:rgba(220,38,38,0.4);",
        "restricted": f"color:{_T['p300']};border-color:{_T['bactive']};",
        "online": (
            f"color:{_T['online']};border-color:rgba(22,163,74,0.4);"
        ),
    }
    s = styles.get(variant, styles["default"])
    dot = ""
    if variant == "online":
        dot = f'<span style="width:6px;height:6px;border-radius:50%;background:{_T["online"]};display:inline-block;margin-right:5px;"></span>'
    return (
        f'<span style="display:inline-flex;align-items:center;gap:4px;'
        f'font-family:\'JetBrains Mono\',monospace;font-size:10px;letter-spacing:2px;'
        f'text-transform:uppercase;font-weight:500;padding:3px 8px;'
        f'border:1px solid;{s}">{dot}{text}</span>'
    )


# ── 4. Stat card (Command Center dashboard grid) ───────────────────────────────

def stat_card(
    idx:        str,
    label:      str,
    big_val:    str,
    meta_lines: list[tuple[str, str]] | None = None,
    bar_pct:    int | None = None,
    bar_color:  str | None = None,
) -> str:
    """
    Stat card matching design .stat class.
    idx       — e.g. "C·01"
    label     — card title
    big_val   — main large number/text
    meta_lines— list of (key, value) tuples shown below
    bar_pct   — 0–100, draws a 3px progress bar if set
    """
    bc = bar_color or _T["p500"]
    meta_html = ""
    if meta_lines:
        rows = "".join(
            f'<div><span class="k">{k}</span> '
            f'<span style="color:var(--text-secondary);font-size:11px;">{v}</span></div>'
            for k, v in meta_lines
        )
        meta_html = f'<div class="meta">{rows}</div>'

    bar_cls = ""
    if bc == _T["online"] or bc == "#16A34A":
        bar_cls = " ok"
    elif bc == _T["warn"] or bc == "#D97706":
        bar_cls = " warn"

    bar_html = ""
    if bar_pct is not None:
        bar_html = f'<div class="bar{bar_cls}"><span style="width:{bar_pct}%;background:{bc};"></span></div>'

    return f"""
<div class="stat brackets"><span class="tr"></span><span class="br"></span>
  <div class="hdr"><span><span class="idx">{idx}</span>{label}</span></div>
  <div class="big">{big_val}</div>
  {meta_html}
  {bar_html}
</div>"""


# ── 5. Panel header ────────────────────────────────────────────────────────────

def panel_hdr(idx: str, title: str, right: str = "") -> str:
    """Panel header — uses .panel-hdr CSS class."""
    return f"""
<div class="panel-hdr">
  <div><span class="idx">{idx}</span><span class="ttl">{title}</span></div>
  <div style="color:var(--text-dim);font-size:9px;">{right}</div>
</div>"""


# ── 6. Panel shell ─────────────────────────────────────────────────────────────

def panel(header_html: str, body_html: str) -> str:
    """Panel shell — uses .panel CSS class."""
    return f"""
<div class="panel">
  {header_html}
  <div style="padding:14px 16px;">
    {body_html}
  </div>
</div>"""


# ── 7. Key-value row ───────────────────────────────────────────────────────────

def kv_row(key: str, value: str, value_color: str | None = None) -> str:
    """Key-value row using .kv CSS class."""
    vc_style = f"color:{value_color};" if value_color else ""
    return f'<div class="kv"><div class="k">{key}</div><div class="v" style="{vc_style}">{value}</div></div>'


# ── 8. Activity log row (Command Center) ─────────────────────────────────────

_TAG_STYLES = {
    "UPLOAD": f"color:{_T['teal']};border-color:rgba(13,148,136,0.4);",
    "AUTH":   f"color:{_T['online']};border-color:rgba(22,163,74,0.4);",
    "QUERY":  f"color:{_T['p300']};border-color:{_T['bactive']};",
    "EXPORT": f"color:{_T['warn']};border-color:rgba(217,119,6,0.4);",
    "FLAG":   f"color:{_T['crit']};border-color:rgba(220,38,38,0.4);",
    "FUSION": f"color:{_T['p300']};border-color:{_T['bactive']};",
    "LOGIN":  f"color:{_T['online']};border-color:rgba(22,163,74,0.4);",
}

def log_row(timestamp: str, user: str, tag: str, message: str, hash_str: str = "") -> str:
    """Activity log row using .log-row CSS class with tag color classes."""
    tag_cls = tag.lower().replace(" ","")
    # Map to supported classes: upload, auth, query, export, flag
    tag_map = {"login":"auth","session":"auth","policy":"auth","audit":"auth","fusion":"query"}
    tag_cls = tag_map.get(tag_cls, tag_cls)
    return f"""
<div class="log-row">
  <span class="ts">{timestamp}</span>
  <span class="user">{user}</span>
  <span class="tag {tag_cls}">{tag}</span>
  <span class="msg">{message}</span>
  <span class="hash">{hash_str}</span>
</div>"""


# ── 9. Section title ──────────────────────────────────────────────────────────

def section_title(title: str, sub: str = "", right: str = "") -> str:
    sub_html = f'<span style="font-family:\'JetBrains Mono\',monospace;font-size:10px;letter-spacing:3px;color:{_T["dim"]};text-transform:uppercase;margin-left:14px;">{sub}</span>' if sub else ""
    right_html = f'<span style="margin-left:auto;font-family:\'JetBrains Mono\',monospace;font-size:10px;letter-spacing:2px;color:{_T["txt2"]};">{right}</span>' if right else ""
    return f"""
<div style="display:flex;align-items:baseline;gap:0;margin-bottom:14px;margin-top:4px;">
  <h2 style="font-family:'Rajdhani',sans-serif;font-weight:600;font-size:22px;
    letter-spacing:3px;margin:0;color:{_T['txt']};">{title}</h2>
  {sub_html}
  {right_html}
</div>"""


# ── 10. File row (Fusion Mode staging list) ────────────────────────────────────

_TYPE_COLORS = {
    ".csv":  ("#0D9488", "CSV"),
    ".xlsx": ("#2563EB", "XLS"),
    ".xls":  ("#2563EB", "XLS"),
    ".pdf":  ("#DC2626", "PDF"),
    ".txt":  ("#9D4EDD", "TXT"),
    ".json": ("#D97706", "JSON"),
}

def file_row(name: str, ext: str, meta: str, size_str: str) -> str:
    """File row using .type CSS classes for the extension badge."""
    _color_map = {
        ".csv":  ("#14B8A6","csv"),
        ".xlsx": ("#2563EB","xlsx"),
        ".xls":  ("#2563EB","xlsx"),
        ".pdf":  ("#DC2626","pdf"),
        ".txt":  ("#F59E0B","txt"),
        ".json": ("#9D4EDD","json"),
    }
    color, cls = _color_map.get(ext.lower(), (_T["p500"], "txt"))
    label = ext.upper().lstrip(".")
    return f"""
<div style="display:grid;grid-template-columns:58px 1fr auto;gap:12px;align-items:center;
  padding:10px 4px;border-bottom:1px solid var(--border-soft);font-family:var(--f-mono);">
  <span class="type {cls}">{label}</span>
  <div style="min-width:0;">
    <div style="font-size:12px;color:var(--text-primary);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{name}</div>
    <div style="font-size:10px;color:var(--text-dim);letter-spacing:1px;margin-top:2px;">{meta}</div>
  </div>
  <span style="font-size:10px;color:var(--text-secondary);white-space:nowrap;">{size_str}</span>
</div>"""


# ── 11. Report section card ────────────────────────────────────────────────────

def report_section(idx: str, title: str, content: str,
                   label: str = "VERIFIED DATA",
                   label_color: str | None = None) -> str:
    """Report section using .sec-hd / .sec-bd CSS classes."""
    is_ai = "AI" in label.upper()
    tag_cls = "a" if is_ai else "v"
    lc = label_color or (_T["warn"] if is_ai else _T["online"])
    return f"""
<div class="sec">
  <div class="sec-hd">
    <div><span class="n">{idx}</span><span class="t">{title}</span>
    <span class="tag {tag_cls}">{label}</span></div>
  </div>
  <div class="sec-bd">
    <div style="font-family:var(--f-mono);font-size:12px;color:var(--text-secondary);line-height:1.6;">{content}</div>
  </div>
</div>"""


# ── 12. Confidence gauge (SVG arc) ────────────────────────────────────────────

def confidence_gauge(score: int, label: str = "CONF") -> str:
    """
    Arc gauge matching Screen 04 design (.gauge class).
    Full arc = 326.7 units. Score 0–100 maps to 0–326.7.
    """
    arc   = round(326.7 * score / 100, 1)
    rest  = round(326.7 - arc, 1)
    color = _T["online"] if score >= 75 else _T["warn"] if score >= 50 else _T["crit"]
    level = "HIGH" if score >= 75 else "MODERATE" if score >= 50 else "LOW" if score >= 25 else "CRITICAL"
    arrow = "▲" if score >= 50 else "▼"
    return f"""
<div class="gauge" style="position:relative;width:160px;height:160px;flex-shrink:0;">
  <svg viewBox="0 0 120 120" style="width:100%;height:100%;transform:rotate(-90deg);">
    <circle cx="60" cy="60" r="52" stroke="rgba(217,119,6,0.15)" stroke-width="10" fill="none"/>
    <circle cx="60" cy="60" r="52" stroke="{color}" stroke-width="10" fill="none"
      stroke-dasharray="{arc} {rest}" stroke-linecap="butt"/>
  </svg>
  <div class="val" style="position:absolute;inset:0;display:grid;place-items:center;text-align:center;">
    <div>
      <div class="num" style="font-family:var(--f-display);font-weight:600;font-size:42px;color:{color};line-height:1;">{score}</div>
      <div style="font-family:var(--f-mono);font-size:9px;letter-spacing:3px;color:var(--text-dim);">/ 100 {label}</div>
      <div style="font-family:var(--f-mono);font-size:10px;letter-spacing:2px;color:{color};margin-top:6px;">{arrow} {level}</div>
    </div>
  </div>
</div>"""


# ── 13. Risk badge ────────────────────────────────────────────────────────────

def risk_badge(score: int, level: str) -> str:
    if level in ("HIGH", "CRITICAL") or score >= 70:
        c = _T["crit"]
    elif level == "MEDIUM" or score >= 40:
        c = _T["warn"]
    else:
        c = _T["online"]
    return (
        f'<span style="font-family:\'JetBrains Mono\',monospace;font-size:9px;'
        f'letter-spacing:2px;padding:2px 8px;'
        f'border:1px solid {c};color:{c};">{level} · {score}</span>'
    )


# ── 14. Corner-bracket tactical frame ────────────────────────────────────────

def tactical_frame(inner_html: str, color: str | None = None) -> str:
    c = color or _T["p500"]
    return f"""
<div style="position:relative;padding:1px;">
  <span style="position:absolute;top:-1px;left:-1px;width:12px;height:12px;
    border-top:1px solid {c};border-left:1px solid {c};"></span>
  <span style="position:absolute;top:-1px;right:-1px;width:12px;height:12px;
    border-top:1px solid {c};border-right:1px solid {c};"></span>
  <span style="position:absolute;bottom:-1px;left:-1px;width:12px;height:12px;
    border-bottom:1px solid {c};border-left:1px solid {c};"></span>
  <span style="position:absolute;bottom:-1px;right:-1px;width:12px;height:12px;
    border-bottom:1px solid {c};border-right:1px solid {c};"></span>
  {inner_html}
</div>"""


# ── 15. Hash chain integrity panel ────────────────────────────────────────────

def hash_chain_panel(entries: int, head_hash: str, intact: bool) -> str:
    """Hash chain summary using .ch CSS classes."""
    status_color = _T["online"] if intact else _T["crit"]
    status_text  = "INTACT" if intact else "TAMPERED"
    cells = [
        ("ENTRIES",    str(entries),                     "SINCE FIRST USE"),
        ("INTEGRITY",  f'<span style="color:{status_color};">{status_text}</span>',
                       f"0 / {entries} TAMPERED" if intact else "BREACH DETECTED"),
        ("CHAIN HEAD", f'<span style="font-family:var(--f-mono);font-size:12px;">{head_hash}</span>',
                       "SHA-256 · SEALED"),
        ("NEXT ANCHOR","AUTO", "BEDROCK KMS"),
    ]
    ok_cls = " ok" if intact else ""
    def _ch(k, v, d):
        val_cls = " ok" if (k == "INTEGRITY" and intact) else ""
        return (
            f'<div class="ch" style="flex:1;border-right:1px solid var(--border-soft);">'
            f'<div class="k">{k}</div>'
            f'<div class="v{val_cls}" style="font-family:var(--f-display);font-weight:600;font-size:22px;margin:6px 0 2px;">{v}</div>'
            f'<div class="d">{d}</div>'
            f'</div>'
        )
    cells_html = "".join(_ch(k, v, d) for k, v, d in cells)
    return f'<div style="display:flex;border:1px solid var(--border);overflow:hidden;">{cells_html}</div>'


# ── 16. Sidebar nav HTML (rendered once inside st.sidebar) ───────────────────

_LOGO_SVG = """<svg width="24" height="24" viewBox="0 0 24 24" fill="none">
  <circle cx="12" cy="12" r="10" stroke="#9D4EDD" stroke-width="1"/>
  <circle cx="12" cy="12" r="6"  stroke="#7B2FBE" stroke-width="1"/>
  <circle cx="12" cy="12" r="1.6" fill="#C084FC"/>
  <line x1="12" y1="1" x2="12" y2="5" stroke="#C084FC" stroke-width="1"/>
  <line x1="12" y1="19" x2="12" y2="23" stroke="#C084FC" stroke-width="1"/>
  <line x1="1"  y1="12" x2="5"  y2="12" stroke="#C084FC" stroke-width="1"/>
  <line x1="19" y1="12" x2="23" y2="12" stroke="#C084FC" stroke-width="1"/>
</svg>"""


def sidebar_brand(version: str = "v3.4.1") -> str:
    """Sidebar brand block — uses .sb-brand CSS classes."""
    return f"""
<div class="sb-brand" style="padding:18px 18px 16px;border-bottom:1px solid var(--border-soft);display:flex;align-items:center;gap:10px;">
  {_LOGO_SVG}
  <div>
    <div style="font-family:var(--f-display);font-weight:600;font-size:16px;letter-spacing:5px;color:var(--text-primary);">ÆTHER</div>
    <div style="font-family:var(--f-mono);font-size:8px;letter-spacing:3px;color:var(--purple-300);">LENS · {version}</div>
  </div>
</div>"""


def sidebar_section_label(label: str) -> str:
    """Sidebar section label — matches .sb-section-label design."""
    return f"""
<div style="font-family:var(--f-mono);font-size:9px;letter-spacing:4px;
  color:var(--text-dim);text-transform:uppercase;
  padding:18px 18px 8px;
  display:flex;justify-content:space-between;align-items:center;">
  {label}
  <span style="flex:1;height:1px;background:var(--border-soft);margin-left:10px;display:inline-block;"></span>
</div>"""


def sidebar_nav_item(label: str, active: bool = False, count: str = "") -> str:
    """Sidebar nav item using .sb-item CSS class."""
    active_cls = " active" if active else ""
    cnt = f'<span class="count">{count}</span>' if count else ""
    arrow = "&#x25B8;&nbsp;" if active else ""
    return f'<div class="sb-item{active_cls}">{arrow}{label}{cnt}</div>'
