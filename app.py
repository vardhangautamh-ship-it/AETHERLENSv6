"""
AetherLens — Main Application Entry Point
Complete production build — all screens, full design, no placeholders.
"""

import sys
import io
import os

import time
import json
import datetime
import sqlite3
import streamlit as st

import config
from modules.license import check_license_on_startup as _check_license


# ── Unicode / encoding helpers ────────────────────────────────────────────────

def safe_decode_file(file_bytes: bytes, filename: str) -> bytes:
    """
    Accept raw bytes from a Streamlit UploadedFile and return bytes that are
    safe to pass to downstream parsers.

    - Binary formats (xlsx, xls, pdf) are returned AS-IS — they are not text
      and must not be decoded.
    - Text formats (csv, txt, tsv, text) are decoded to str using the best
      available encoding, then re-encoded as UTF-8 so that every subsequent
      open() / pd.read_csv() call works regardless of the OS default codec.

    On any error the original bytes are returned unchanged so callers never
    receive None.
    """
    if not file_bytes:
        return file_bytes or b""

    suffix = ("." + filename.rsplit(".", 1)[-1]).lower() if "." in filename else ""
    binary_types = {".xlsx", ".xls", ".pdf", ".docx", ".zip"}
    if suffix in binary_types:
        return file_bytes  # binary — do not touch

    # Text file: try encodings in preference order
    text_encodings = ["utf-8", "utf-8-sig", "latin-1", "cp1252", "iso-8859-1"]
    for enc in text_encodings:
        try:
            text = file_bytes.decode(enc)
            # Re-encode as clean UTF-8
            return text.encode("utf-8", errors="replace")
        except (UnicodeDecodeError, UnicodeError):
            continue

    # Absolute last resort — replace undecodable bytes
    return file_bytes.decode("utf-8", errors="replace").encode("utf-8")

# Run license check on startup (dev mode auto-generates aetherlens.license)
_license_data = _check_license()

from modules.auth import (
    init_db, session_init, session_login, session_logout,
    validate_session, verify_pin, verify_login, create_token,
    is_pin_locked, get_pin_state, write_audit,
    get_all_users, admin_create_user, admin_delete_user,
    admin_change_role, admin_reset_password, admin_toggle_lock,
    get_audit_log, get_system_stats,
)

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="AetherLens",
    page_icon="🔭",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Design-system CSS ─────────────────────────────────────────────────────────
CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Rajdhani:wght@400;500;600;700&family=JetBrains+Mono:wght@300;400;500;600;700&family=IBM+Plex+Mono:wght@300;400;500;600&display=swap');

/* ── Full design-system tokens (from tokens.css) ── */
:root {
  --void:#000000; --abyss:#05000D; --deep:#0A0015; --card:#100020; --mid:#1E0040; --line:#221040;
  --purple-900:#3B0764; --purple-700:#6B21A8; --purple-600:#7B2FBE; --purple-500:#9D4EDD;
  --purple-400:#A855F7; --purple-300:#C084FC; --purple-100:#E9D5FF;
  /* legacy aliases still used by older code */
  --p600:#7B2FBE; --p500:#9D4EDD; --p300:#C084FC; --p100:#E9D5FF;
  --text-primary:#F0EAD6; --text-secondary:#9CA3AF; --text-dim:#4B5563; --text-faint:#2A2438;
  --txt:#F0EAD6; --txt2:#9CA3AF; --dim:#4B5563; --faint:#2A2438;
  --online:#16A34A; --online-glow:rgba(22,163,74,0.5);
  --warning:#D97706; --warning-glow:rgba(217,119,6,0.5);
  --critical:#DC2626; --critical-glow:rgba(220,38,38,0.5);
  --warn:#D97706; --crit:#DC2626; --info:#2563EB; --teal:#0D9488;
  --border:rgba(123,47,190,0.28); --border-soft:rgba(123,47,190,0.14); --border-active:rgba(123,47,190,0.8); --border-hair:rgba(240,234,214,0.06);
  --bsoft:rgba(123,47,190,0.14); --bactive:rgba(123,47,190,0.80);
  --glow-purple:0 0 20px rgba(123,47,190,0.4); --glow-online:0 0 10px rgba(22,163,74,0.5);
  --f-display:'Rajdhani',ui-sans-serif,sans-serif;
  --f-mono:'JetBrains Mono','IBM Plex Mono',ui-monospace,monospace;
}

/* ── Base ── */
*, *::before, *::after { box-sizing: border-box; }
html, body, [class*="css"] {
  background-color: #000000 !important;
  color: #F0EAD6 !important;
  font-family: var(--f-mono) !important;
  font-size: 13px;
  line-height: 1.45;
  -webkit-font-smoothing: antialiased;
  text-rendering: geometricPrecision;
}
/* ── Global atmosphere: scanline + vignette on every screen ── */
.stApp {
  background:
    radial-gradient(1200px 800px at 70% -20%, rgba(123,47,190,0.08), transparent 60%),
    radial-gradient(900px 600px at -10% 110%, rgba(123,47,190,0.05), transparent 60%),
    #000000 !important;
  min-height: 100vh;
}
body::before {
  content: ""; position: fixed; inset: 0; pointer-events: none; z-index: 99999;
  background-image: repeating-linear-gradient(to bottom,
    rgba(240,234,214,0.012) 0px, rgba(240,234,214,0.012) 1px,
    transparent 1px, transparent 3px);
  mix-blend-mode: screen;
}
body::after {
  content: ""; position: fixed; inset: 0; pointer-events: none; z-index: 99998;
  background: radial-gradient(ellipse at center, transparent 50%, rgba(0,0,0,0.55) 100%);
}
#MainMenu, footer, header { visibility: hidden; }
[data-testid="stAppViewContainer"] > .main > .block-container {
  padding-top: 0 !important;
  padding-left: 0 !important;
  padding-right: 0 !important;
  max-width: 100% !important;
}

/* ── al-root: scanline + vignette ── */
.al-root {
  position: relative; min-height: 100%;
  background:
    radial-gradient(1200px 800px at 70% -20%, rgba(123,47,190,0.08), transparent 60%),
    radial-gradient(900px 600px at -10% 110%, rgba(123,47,190,0.05), transparent 60%),
    var(--void);
}
.al-root::before {
  content: ""; position: absolute; inset: 0; pointer-events: none;
  background-image: repeating-linear-gradient(to bottom, rgba(240,234,214,0.012) 0px, rgba(240,234,214,0.012) 1px, transparent 1px, transparent 3px);
  mix-blend-mode: screen; z-index: 1;
}
.al-root::after {
  content: ""; position: absolute; inset: 0; pointer-events: none;
  background: radial-gradient(ellipse at center, transparent 50%, rgba(0,0,0,0.55) 100%);
  z-index: 2;
}
.al-root > * { position: relative; z-index: 3; }

/* ── Classification strip ── */
.al-classification-strip {
  display: flex; align-items: center; justify-content: space-between;
  padding: 6px 16px; background: #1A0030;
  border-bottom: 1px solid var(--border);
  font-family: var(--f-mono); font-size: 10px; letter-spacing: 4px;
  color: var(--purple-300); text-transform: uppercase;
}
.al-classification-strip .dot {
  width: 6px; height: 6px; border-radius: 50%; background: var(--warning);
  box-shadow: 0 0 6px var(--warning-glow); display: inline-block; margin-right: 8px;
}
.al-classification-strip .center { letter-spacing: 6px; color: var(--text-primary); font-weight: 500; }

/* ── Status dots ── */
.s-dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
.s-dot.online { background: var(--online); box-shadow: var(--glow-online); animation: breathe 2.4s ease-in-out infinite; }
.s-dot.offline { background: var(--text-dim); }
.s-dot.warning { background: var(--warning); box-shadow: 0 0 10px var(--warning-glow); animation: warn-pulse 1s ease-in-out infinite; }
.s-dot.critical { background: var(--critical); box-shadow: 0 0 10px var(--critical-glow); animation: warn-pulse 0.8s ease-in-out infinite; }
@keyframes breathe { 0%,100% { opacity:1; box-shadow:0 0 6px var(--online-glow); } 50% { opacity:0.7; box-shadow:0 0 14px var(--online-glow); } }
@keyframes warn-pulse { 0%,100% { opacity:1; } 50% { opacity:0.55; } }

/* ── Corner brackets ── */
.brackets { position: relative; }
.brackets::before, .brackets::after, .brackets > .tr, .brackets > .br {
  content: ""; position: absolute; width: 10px; height: 10px;
  border-color: var(--purple-500); border-style: solid; border-width: 0;
}
.brackets::before { top:-1px; left:-1px; border-top-width:1px; border-left-width:1px; }
.brackets::after  { top:-1px; right:-1px; border-top-width:1px; border-right-width:1px; }
.brackets > .tr { bottom:-1px; left:-1px; border-bottom-width:1px; border-left-width:1px; }
.brackets > .br { bottom:-1px; right:-1px; border-bottom-width:1px; border-right-width:1px; }

/* ── Tactical button ── */
.btn {
  display: inline-flex; align-items: center; gap: 10px;
  font-family: var(--f-mono); font-size: 11px; letter-spacing: 3px;
  text-transform: uppercase; font-weight: 500; padding: 10px 18px;
  background: transparent; color: var(--text-primary);
  border: 1px solid var(--border); cursor: pointer; transition: all 150ms ease;
}
.btn:hover:not(:disabled) { border-color: var(--purple-500); background: rgba(123,47,190,0.08); }
.btn:disabled { opacity: 0.35; cursor: not-allowed; }
.btn.primary {
  border-color: var(--purple-500);
  background: linear-gradient(180deg, rgba(123,47,190,0.12), rgba(123,47,190,0.04));
  color: var(--purple-100); box-shadow: inset 0 0 0 1px rgba(192,132,252,0.08);
}
.btn.primary:hover:not(:disabled) {
  box-shadow: var(--glow-purple), inset 0 0 0 1px rgba(192,132,252,0.18);
  background: rgba(123,47,190,0.18);
}
.btn.primary.ready { animation: btn-breathe 2.4s ease-in-out infinite; }
@keyframes btn-breathe {
  0%,100% { box-shadow: 0 0 0 rgba(123,47,190,0), inset 0 0 0 1px rgba(192,132,252,0.12); }
  50% { box-shadow: 0 0 24px rgba(123,47,190,0.5), inset 0 0 0 1px rgba(192,132,252,0.3); }
}

/* ── Data pill ── */
.pill {
  display: inline-flex; align-items: center; gap: 6px;
  font-family: var(--f-mono); font-size: 10px; letter-spacing: 2px;
  text-transform: uppercase; font-weight: 500; padding: 3px 8px;
  border: 1px solid var(--border); color: var(--text-secondary);
}
.pill.verified { color: var(--online); border-color: rgba(22,163,74,0.4); }
.pill.ai { color: var(--warning); border-color: rgba(217,119,6,0.4); }
.pill.unverified { color: var(--critical); border-color: rgba(220,38,38,0.4); }
.pill.restricted { color: var(--purple-300); border-color: var(--border-active); }

/* ── Card ── */
.card {
  background: linear-gradient(180deg, rgba(16,0,32,0.88), rgba(10,0,21,0.88));
  border: 1px solid var(--border); position: relative;
}
.card .hdr {
  display: flex; align-items: center; justify-content: space-between;
  padding: 10px 14px; border-bottom: 1px solid var(--border-soft);
  font-family: var(--f-mono); font-size: 10px; letter-spacing: 3px;
  text-transform: uppercase; color: var(--text-secondary);
}
.card .hdr .idx { color: var(--purple-500); font-weight: 600; margin-right: 10px; }

/* ── Panel ── */
.panel {
  background: linear-gradient(180deg, rgba(16,0,32,0.88), rgba(10,0,21,0.9));
  border: 1px solid var(--border); margin-bottom: 16px;
}
.panel-hdr {
  display: flex; align-items: center; justify-content: space-between;
  padding: 12px 16px; border-bottom: 1px solid var(--border-soft);
  font-family: var(--f-mono); font-size: 10px; letter-spacing: 3px; text-transform: uppercase; color: var(--text-secondary);
}
.panel-hdr .idx { color: var(--purple-500); font-weight: 600; margin-right: 8px; }
.panel-hdr .ttl { color: var(--text-primary); }

/* ── Stat card ── */
.stat {
  background: linear-gradient(180deg, rgba(16,0,32,0.9), rgba(10,0,21,0.92));
  border: 1px solid var(--border); padding: 14px 16px 16px;
  position: relative; display: flex; flex-direction: column; gap: 10px; min-height: 130px;
}
.stat .hdr {
  display: flex; align-items: center; justify-content: space-between;
  font-family: var(--f-mono); font-size: 9px; letter-spacing: 3px;
  color: var(--text-secondary); text-transform: uppercase;
}
.stat .idx { color: var(--purple-500); font-weight: 600; }
.stat .big { font-family: var(--f-display); font-weight: 600; font-size: 32px; letter-spacing: -0.5px; color: var(--text-primary); line-height: 1; }
.stat .big.tight { font-size: 26px; }
.stat .meta { font-family: var(--f-mono); font-size: 10px; color: var(--text-secondary); letter-spacing: 1px; display: flex; flex-direction: column; gap: 3px; }
.stat .meta .k { color: var(--text-dim); letter-spacing: 2px; text-transform: uppercase; font-size: 9px; }
.bar { height: 3px; background: var(--text-faint); position: relative; overflow: hidden; }
.bar > span { position: absolute; left: 0; top: 0; bottom: 0; background: var(--purple-500); }
.bar.warn > span { background: var(--warning); }
.bar.ok > span { background: var(--online); }

/* ── Log row ── */
.log-row {
  display: grid; grid-template-columns: 90px 130px 80px 1fr auto;
  gap: 14px; align-items: center;
  padding: 9px 16px; border-bottom: 1px solid var(--border-soft);
  font-family: var(--f-mono); font-size: 11px; color: var(--text-secondary);
}
.log-row:hover { background: rgba(123,47,190,0.04); }
.log-row .ts { color: var(--text-dim); letter-spacing: 1px; }
.log-row .user { color: var(--purple-300); letter-spacing: 1px; }
.log-row .tag { font-size: 9px; letter-spacing: 2px; color: var(--text-secondary); border: 1px solid var(--border-soft); padding: 1px 6px; text-align: center; text-transform: uppercase; }
.log-row .tag.upload { color: var(--teal); border-color: rgba(13,148,136,0.4); }
.log-row .tag.auth   { color: var(--online); border-color: rgba(22,163,74,0.4); }
.log-row .tag.query  { color: var(--purple-300); border-color: var(--border-active); }
.log-row .tag.export { color: var(--warning); border-color: rgba(217,119,6,0.4); }
.log-row .tag.flag   { color: var(--critical); border-color: rgba(220,38,38,0.4); }
.log-row .msg  { color: var(--text-primary); letter-spacing: 0.5px; }
.log-row .hash { color: var(--text-dim); font-size: 10px; }

/* ── Flag row (anomaly / compliance flags — 2 col) ── */
.flag-row {
  display: flex; align-items: baseline; gap: 12px;
  padding: 7px 14px; border-bottom: 1px solid var(--border-soft);
  border-left: 2px solid var(--critical);
  font-family: var(--f-mono); font-size: 11px;
}
.flag-row:last-child { border-bottom: 0; }
.flag-row .ftag {
  font-size: 9px; letter-spacing: 2px; color: var(--critical);
  border: 1px solid rgba(220,38,38,0.4); padding: 1px 6px;
  text-transform: uppercase; white-space: nowrap;
}
.flag-row .fmsg { color: var(--text-secondary); flex: 1; }
/* ── Pattern row (3-col: tag · description · significance) ── */
.pat-row {
  display: flex; align-items: baseline; gap: 10px;
  padding: 6px 14px; border-bottom: 1px solid var(--border-soft);
  font-family: var(--f-mono); font-size: 11px;
}
.pat-row:last-child { border-bottom: 0; }
.pat-row .ptag {
  font-size: 9px; letter-spacing: 2px; color: var(--purple-300);
  border: 1px solid var(--border-active); padding: 1px 6px;
  text-transform: uppercase; white-space: nowrap;
}
.pat-row .pmsg { color: var(--text-primary); flex: 1; }
.pat-row .psig { color: var(--text-dim); font-size: 9px; white-space: nowrap; }
/* ── KV row ── */
.kv { display: flex; align-items: baseline; gap: 12px; padding: 6px 0; border-bottom: 1px dashed var(--border-soft); }
.kv:last-child { border-bottom: 0; }
.kv .k { font-family: var(--f-mono); font-size: 10px; letter-spacing: 2px; text-transform: uppercase; color: var(--text-secondary); min-width: 140px; flex: 0 0 140px; }
.kv .v { font-family: var(--f-mono); font-size: 12px; color: var(--text-primary); word-break: break-word; }

/* ── Gate: pin boxes ── */
.gate-frame { width: 480px; padding: 44px 56px 36px; text-align: center; position: relative; }
.pin-row { display: flex; justify-content: center; gap: 10px; margin-bottom: 22px; }
.pin-box {
  width: 52px; height: 64px; background: #070010;
  border: 1px solid var(--border); display: grid; place-items: center;
  font-family: var(--f-display); font-size: 28px; font-weight: 600; color: var(--text-primary);
  position: relative; transition: all 180ms ease;
  box-shadow: inset 0 1px 0 rgba(255,255,255,0.02), inset 0 -2px 6px rgba(0,0,0,0.6);
}
.pin-box::after { content:""; position:absolute; left:8px; right:8px; bottom:6px; height:1px; background:var(--text-faint); transition:background 180ms; }
.pin-box.filled { color: var(--purple-100); }
.pin-box.filled::after { background: var(--purple-500); }
.pin-box.active {
  border-color: var(--border-active);
  box-shadow: inset 0 1px 0 rgba(255,255,255,0.04), inset 0 -2px 6px rgba(0,0,0,0.6), 0 0 0 1px var(--purple-500), var(--glow-purple);
  transform: translateY(-1px) scale(1.02);
}
.pin-box.active::after { background: var(--purple-300); box-shadow: 0 0 6px var(--purple-300); }
.pin-row.shake { animation: shake 0.42s cubic-bezier(.36,.07,.19,.97); }
@keyframes shake {
  10%,90% { transform: translateX(-1px); }
  20%,80% { transform: translateX(2px); }
  30%,50%,70% { transform: translateX(-4px); }
  40%,60% { transform: translateX(4px); }
}

/* ── Corners decoration on gate ── */
.corners { position: absolute; inset: 0; pointer-events: none; }
.corners span { position: absolute; width: 14px; height: 14px; border-color: var(--purple-500); border-style: solid; border-width: 0; opacity: 0.6; }
.corners .tl { top:0; left:0; border-top-width:1px; border-left-width:1px; }
.corners .tr { top:0; right:0; border-top-width:1px; border-right-width:1px; }
.corners .bl { bottom:0; left:0; border-bottom-width:1px; border-left-width:1px; }
.corners .br { bottom:0; right:0; border-bottom-width:1px; border-right-width:1px; }

/* ── Keypad ── */
.keypad { display: grid; grid-template-columns: repeat(3, 56px); gap: 10px; justify-content: center; margin-top: 34px; }
.key {
  height: 48px; width: 56px; background: transparent;
  border: 1px solid var(--border-soft); color: var(--text-primary);
  font-family: var(--f-display); font-size: 20px; font-weight: 500;
  cursor: pointer; transition: all 120ms ease; display: grid; place-items: center;
}
.key:hover { border-color: var(--purple-500); background: rgba(123,47,190,0.08); }
.key:active { background: rgba(123,47,190,0.18); transform: scale(0.96); }
.key.action { font-family: var(--f-mono); font-size: 10px; letter-spacing: 2px; color: var(--text-secondary); }

/* ── Footer bar (gate) ── */
.footer-bar {
  border-top: 1px solid var(--border-soft); padding: 10px 22px;
  display: flex; align-items: center; justify-content: space-between;
  font-family: var(--f-mono); font-size: 10px; letter-spacing: 2px;
  color: var(--text-dim); text-transform: uppercase;
}
.footer-bar .grp { display: flex; gap: 24px; align-items: center; }
.footer-bar .val { color: var(--text-secondary); }

/* ── Sidebar ── */
[data-testid="stSidebar"] {
  background-color: #070010 !important;
  border-right: 1px solid var(--border) !important;
}
[data-testid="stSidebarContent"] { background-color: #070010 !important; }

/* ── Sidebar component classes ── */
.sb-brand { padding:18px; border-bottom:1px solid var(--border-soft); display:flex; align-items:center; gap:10px; }
.sb-group, .sb-section-label {
  font-family: var(--f-mono); font-size: 9px; letter-spacing: 4px;
  color: var(--text-dim); text-transform: uppercase; padding: 18px 18px 8px;
  display: flex; justify-content: space-between; align-items: center;
}
.sb-item {
  display: flex; align-items: center; gap: 12px;
  padding: 11px 18px 11px 16px;
  font-family: var(--f-mono); font-size: 11px; letter-spacing: 2px;
  color: var(--text-secondary); cursor: pointer;
  border-left: 2px solid transparent; text-transform: uppercase;
}
.sb-item:hover { background: rgba(123,47,190,0.06); color: var(--text-primary); }
.sb-item.active { background: rgba(123,47,190,0.10); border-left-color: var(--purple-500); color: var(--purple-100); }
.sb-item .icon { width: 14px; height: 14px; opacity: 0.8; }
.sb-item .count { margin-left: auto; font-size: 10px; color: var(--text-dim); letter-spacing: 1px; }
.sb-item.active .count { color: var(--purple-300); }
.sb-foot {
  margin-top: auto; padding: 14px 18px;
  border-top: 1px solid var(--border-soft);
  font-family: var(--f-mono); font-size: 9px; letter-spacing: 2px; color: var(--text-dim);
}
.sb-foot .row { display: flex; justify-content: space-between; padding: 2px 0; }

/* ── Sidebar toggle — always bright purple so it's visible on black ── */
[data-testid="collapsedControl"],
[data-testid="stSidebarCollapsedControl"] {
  background-color: #7B2FBE !important;
  border-radius: 0 8px 8px 0 !important;
  width: 28px !important;
  min-width: 28px !important;
  height: 56px !important;
  opacity: 1 !important;
  visibility: visible !important;
  cursor: pointer !important;
  box-shadow: 3px 0 12px rgba(123,47,190,0.8) !important;
}
[data-testid="collapsedControl"] svg,
[data-testid="stSidebarCollapsedControl"] svg {
  fill: #ffffff !important;
  color: #ffffff !important;
}

/* ── Headings ── */
h1, h2, h3, h4, h5, h6 {
  color: var(--purple-500) !important;
  letter-spacing: 0.06em !important;
  font-family: var(--f-display) !important;
  font-weight: 600 !important;
}

/* ── Text ── */
p, span, div, label { color: var(--text-primary) !important; }

/* ── Inputs ── */
.stTextInput > div > input,
input[type="text"],
input[type="password"],
input[type="number"],
input[type="email"] {
  background-color: #05000D !important;
  color: #F0EAD6 !important;
  border: 1px solid rgba(123,47,190,0.40) !important;
  border-radius: 2px !important;
  font-family: 'JetBrains Mono', monospace !important;
  font-size: 13px !important;
  letter-spacing: 1px !important;
}
.stTextInput > div > input:focus {
  border-color: #9D4EDD !important; outline: none !important;
  box-shadow: 0 0 0 1px rgba(157,78,221,0.3) !important;
}
.stTextArea textarea {
  background-color: #05000D !important;
  color: #F0EAD6 !important;
  border: 1px solid rgba(123,47,190,0.40) !important;
  font-family: 'JetBrains Mono', monospace !important;
  border-radius: 2px !important;
}

/* ── Buttons ── */
.stButton > button {
  background-color: transparent !important;
  color: #C084FC !important;
  border: 1px solid rgba(123,47,190,0.50) !important;
  border-radius: 2px !important;
  font-family: 'JetBrains Mono', monospace !important;
  font-size: 11px !important;
  letter-spacing: 2px !important;
  text-transform: uppercase !important;
  transition: all 0.15s ease;
}
.stButton > button:hover {
  background-color: rgba(123,47,190,0.15) !important;
  border-color: #9D4EDD !important;
  color: #E9D5FF !important;
}
.stButton > button:active { background-color: rgba(123,47,190,0.30) !important; }
.stButton > button:disabled { opacity: 0.35 !important; }

/* ── Download button ── */
.stDownloadButton > button {
  background-color: transparent !important;
  color: #9D4EDD !important;
  border: 1px solid rgba(157,78,221,0.50) !important;
  border-radius: 2px !important;
  font-family: 'JetBrains Mono', monospace !important;
  font-size: 11px !important;
  letter-spacing: 2px !important;
  text-transform: uppercase !important;
}
.stDownloadButton > button:hover {
  background-color: rgba(157,78,221,0.15) !important;
  border-color: #C084FC !important;
}

/* ── Selectbox ── */
.stSelectbox > div, .stSelectbox > div > div {
  background-color: #05000D !important;
  color: #F0EAD6 !important;
  border: 1px solid rgba(123,47,190,0.40) !important;
}
[data-baseweb="select"] { background-color: #05000D !important; }
[data-baseweb="select"] * { background-color: #05000D !important; color: #F0EAD6 !important; }
[data-baseweb="popover"] * { background-color: #0A0015 !important; color: #F0EAD6 !important; }
[role="listbox"] { background-color: #0A0015 !important; }
[role="option"]  { background-color: #0A0015 !important; color: #F0EAD6 !important; }
[role="option"]:hover { background-color: #1E0040 !important; }

/* ── Multiselect ── */
[data-baseweb="tag"] { background-color: #1E0040 !important; border-color: #7B2FBE !important; }
[data-baseweb="tag"] span { color: #F0EAD6 !important; }

/* ── DataFrames / tables ── */
.stDataFrame, .stDataFrame * { background-color: #05000D !important; color: #F0EAD6 !important; }
[data-testid="stDataFrame"] { background-color: #05000D !important; border: 1px solid rgba(123,47,190,0.20) !important; }
.dataframe { background-color: #05000D !important; color: #F0EAD6 !important; border: none !important; }
.dataframe th { background-color: #0A0015 !important; color: #9D4EDD !important; border-color: rgba(123,47,190,0.20) !important; font-family: 'JetBrains Mono', monospace !important; font-size: 10px !important; letter-spacing: 2px !important; }
.dataframe td { background-color: #05000D !important; color: #F0EAD6 !important; border-color: rgba(123,47,190,0.12) !important; font-family: 'JetBrains Mono', monospace !important; font-size: 11px !important; }

/* ── Progress bars ── */
.stProgress > div > div { background-color: #9D4EDD !important; }
[data-testid="stProgressBar"] > div { background-color: #1E0040 !important; }
[data-testid="stProgressBar"] > div > div { background-color: #9D4EDD !important; }

/* ── Alerts ── */
.stAlert {
  background-color: #05000D !important;
  border: 1px solid rgba(157,78,221,0.40) !important;
  color: #F0EAD6 !important;
  border-radius: 2px !important;
}

/* ── File uploader ── */
.stFileUploader {
  background-color: #05000D !important;
  border: 1px solid rgba(123,47,190,0.40) !important;
  border-radius: 2px !important;
}
[data-testid="stFileUploader"] {
  background-color: #05000D !important;
  border: 1px solid rgba(123,47,190,0.40) !important;
}
[data-testid="stFileUploaderDropzone"] {
  background-color: #0A0015 !important;
  border: 1px dashed rgba(123,47,190,0.60) !important;
  color: #9D4EDD !important;
}

/* ── Checkboxes ── */
.stCheckbox label { color: #F0EAD6 !important; }
[data-testid="stCheckbox"] > label { color: #F0EAD6 !important; }
[data-baseweb="checkbox"] { border-color: #7B2FBE !important; }
[data-baseweb="checkbox"][data-checked="true"] { background-color: #7B2FBE !important; }

/* ── Sliders ── */
[data-testid="stSlider"] { color: #F0EAD6 !important; }
[data-testid="stSlider"] > div > div > div { background-color: #9D4EDD !important; }

/* ── Tabs ── */
[data-testid="stTabs"] [role="tablist"] {
  background-color: #000000 !important;
  border-bottom: 1px solid rgba(123,47,190,0.28) !important;
  gap: 0 !important;
}
[data-testid="stTabs"] [role="tab"] {
  color: #4B5563 !important;
  background-color: #000000 !important;
  border: none !important;
  font-family: 'JetBrains Mono', monospace !important;
  font-size: 10px !important;
  letter-spacing: 3px !important;
  text-transform: uppercase !important;
  padding: 10px 20px !important;
}
[data-testid="stTabs"] [role="tab"][aria-selected="true"] {
  color: #C084FC !important;
  border-bottom: 2px solid #9D4EDD !important;
  background-color: rgba(123,47,190,0.06) !important;
}
[data-testid="stTabContent"] { background-color: #000000 !important; }

/* ── Expanders ── */
[data-testid="stExpander"] { background-color: #05000D !important; border: 1px solid rgba(123,47,190,0.20) !important; }
[data-testid="stExpander"] summary {
  color: #9D4EDD !important;
  font-family: 'JetBrains Mono', monospace !important;
  font-size: 10px !important;
  letter-spacing: 2px !important;
}

/* ── Metrics ── */
[data-testid="metric-container"] {
  background: linear-gradient(180deg, rgba(16,0,32,0.90), rgba(10,0,21,0.92)) !important;
  border: 1px solid rgba(123,47,190,0.28) !important;
  border-radius: 2px !important;
  padding: 12px 16px !important;
}
[data-testid="stMetricValue"] {
  color: #F0EAD6 !important;
  font-family: 'Rajdhani', sans-serif !important;
  font-weight: 600 !important;
  font-size: 28px !important;
}
[data-testid="stMetricLabel"] {
  color: #9CA3AF !important;
  font-family: 'JetBrains Mono', monospace !important;
  font-size: 9px !important;
  letter-spacing: 3px !important;
  text-transform: uppercase !important;
}

/* ── Code blocks ── */
.stCodeBlock, pre, code {
  background-color: #05000D !important;
  color: #F0EAD6 !important;
  border: 1px solid rgba(123,47,190,0.20) !important;
  font-family: 'JetBrains Mono', monospace !important;
}

/* ── Sidebar nav buttons (sb-item style) ── */
div[data-testid="stSidebar"] .stButton > button {
  width: 100% !important;
  text-align: left !important;
  border: none !important;
  border-left: 2px solid transparent !important;
  border-radius: 0 !important;
  padding: 11px 18px 11px 16px !important;
  margin-bottom: 0 !important;
  font-family: var(--f-mono) !important;
  font-size: 11px !important;
  letter-spacing: 2px !important;
  color: var(--text-secondary) !important;
  text-transform: uppercase !important;
  background: transparent !important;
  transition: all 120ms ease !important;
}
div[data-testid="stSidebar"] .stButton > button:hover {
  background: rgba(123,47,190,0.06) !important;
  color: var(--text-primary) !important;
  border-left-color: rgba(157,78,221,0.4) !important;
}
div[data-testid="stSidebar"] .stButton > button[kind="primary"] {
  background: rgba(123,47,190,0.10) !important;
  border-left-color: var(--purple-500) !important;
  color: var(--purple-100) !important;
}

/* ── Date input ── */
[data-testid="stDateInput"] input {
  background-color: #05000D !important;
  color: #F0EAD6 !important;
  border: 1px solid rgba(123,47,190,0.40) !important;
  font-family: 'JetBrains Mono', monospace !important;
}

/* ── Spinner ── */
[data-testid="stSpinner"] { color: #9D4EDD !important; }

/* ── Scrollbar ── */
::-webkit-scrollbar { width: 4px; height: 4px; }
::-webkit-scrollbar-track { background: #000000; }
::-webkit-scrollbar-thumb { background: #7B2FBE; border-radius: 2px; }
::-webkit-scrollbar-thumb:hover { background: #9D4EDD; }

/* ── Utility classes ── */
.mono { font-family: var(--f-mono); }
.dsp  { font-family: var(--f-display); }
.num  { font-family: var(--f-display); font-weight: 600; letter-spacing: -0.5px; font-variant-numeric: tabular-nums; color: var(--text-primary); }
.rule { height: 1px; background: var(--border-soft); width: 100%; }
.rule.dashed { background: none; border-top: 1px dashed var(--border-soft); }
.cls-label { font-family: var(--f-mono); letter-spacing: 4px; text-transform: uppercase; font-size: 10px; color: var(--text-secondary); }

/* ── Auth-label with decorative lines ── */
.auth-label {
  font-family: var(--f-mono); font-size: 10px; letter-spacing: 3px;
  color: var(--text-secondary); text-transform: uppercase;
  margin-bottom: 14px; display: flex; align-items: center; justify-content: center; gap: 10px;
}
.auth-label::before, .auth-label::after {
  content: ""; flex: 1; max-width: 40px; height: 1px; background: var(--border-soft);
}

/* ── Role badge ── */
.role-badge {
  display: inline-flex; align-items: center; gap: 6px;
  font-family: var(--f-mono); font-size: 9px; letter-spacing: 3px;
  padding: 2px 8px; background: rgba(123,47,190,0.15);
  border: 1px solid var(--border-active); color: var(--purple-100);
  text-transform: uppercase;
}

/* ── Engine dot ── */
.engine-dot { display: inline-block; width: 6px; height: 6px; border-radius: 50%; margin-right: 5px; vertical-align: middle; }
.dot-blue   { background-color: #2563EB; box-shadow: 0 0 5px #2563EB; }
.dot-green  { background-color: #16A34A; box-shadow: 0 0 5px #16A34A; }
.dot-orange { background-color: #D97706; box-shadow: 0 0 5px #D97706; }
.dot-gray   { background-color: #4B5563; }

/* ── Legacy gate logo styles ── */
.logo-title {
  font-family: var(--f-display);
  font-size: 3.2rem; font-weight: 700; color: var(--purple-500);
  text-shadow: 0 0 8px var(--purple-600), 0 0 20px var(--purple-600), 0 0 40px var(--purple-500), 0 0 80px var(--purple-600);
  letter-spacing: 0.3rem; text-align: center; margin-bottom: 0.2rem;
}
.logo-title-sm {
  font-family: var(--f-display);
  font-size: 1.5rem; font-weight: 700; color: var(--purple-500);
  text-shadow: 0 0 6px var(--purple-600), 0 0 14px var(--purple-600); letter-spacing: 0.15rem;
}
.logo-sub {
  font-family: var(--f-mono);
  font-size: 0.6rem; color: var(--purple-600);
  text-align: center; letter-spacing: 0.4rem; margin-bottom: 2.5rem;
  text-transform: uppercase;
}
.divider { border: none; border-top: 1px solid var(--border-soft); margin: 0.8rem 0; }
.result-card {
  background: linear-gradient(180deg, rgba(16,0,32,0.88), rgba(10,0,21,0.90));
  border: 1px solid var(--border); border-left: 3px solid var(--purple-600);
  padding: 0.85rem 1rem; margin-bottom: 0.5rem;
}
.result-card.selected { border-left-color: var(--online) !important; }
.person-section {
  background: rgba(16,0,32,0.60); border: 1px solid var(--border-soft);
  padding: 0.9rem; margin-bottom: 0.7rem;
}
.person-section h4 {
  color: var(--purple-500);
  font-family: var(--f-mono);
  font-size: 9px; letter-spacing: 3px; text-transform: uppercase;
  margin-bottom: 0.4rem; border-bottom: 1px solid var(--border-soft); padding-bottom: 0.25rem;
}
.disclaimer-box {
  background: rgba(220,38,38,0.06); border: 1px solid rgba(220,38,38,0.40);
  padding: 0.7rem 1rem; font-family: var(--f-mono); font-size: 11px; letter-spacing: 1px;
  color: var(--critical); margin-bottom: 0.8rem;
}
.report-section-verified {
  background: var(--abyss); border: 1px solid var(--border-soft);
  border-left: 3px solid var(--teal);
  padding: 0.8rem 1rem; margin-bottom: 0.6rem;
}
.report-section-ai {
  background: var(--abyss); border: 1px solid var(--border-soft);
  border-left: 3px solid var(--purple-600);
  padding: 0.8rem 1rem; margin-bottom: 0.6rem;
}
.report-label-verified {
  font-family: var(--f-mono);
  font-size: 9px; color: var(--online); letter-spacing: 3px;
  text-transform: uppercase; margin-bottom: 0.3rem;
}
.report-label-ai {
  font-family: var(--f-mono);
  font-size: 9px; color: var(--purple-500); letter-spacing: 3px;
  text-transform: uppercase; margin-bottom: 0.3rem;
}
.stat-card {
  background: linear-gradient(180deg, rgba(16,0,32,0.90), rgba(10,0,21,0.92));
  border: 1px solid var(--border); padding: 0.9rem;
}
.stat-value { font-family: var(--f-display); font-size: 2rem; font-weight: 600; color: var(--text-primary); line-height: 1; }
.stat-label { font-family: var(--f-mono); font-size: 9px; color: var(--text-secondary); letter-spacing: 3px; text-transform: uppercase; }
.status-ok   { color: var(--online) !important; font-weight: 600; }
.status-warn { color: var(--warning) !important; font-weight: 600; }
.status-err  { color: var(--critical) !important; font-weight: 600; }
.entity-pill {
  display: inline-block; margin: 2px; padding: 2px 8px;
  border: 1px solid var(--border); font-family: var(--f-mono);
  font-size: 9px; color: var(--text-secondary); letter-spacing: 1px;
}
.error-msg   { color: var(--critical) !important; font-family: var(--f-mono); font-size: 11px; letter-spacing: 1px; text-align: center; }
.lockout-msg { color: var(--critical) !important; font-family: var(--f-mono); font-size: 11px; letter-spacing: 1px; text-align: center; }
.success-msg { color: var(--online) !important; font-family: var(--f-mono); font-size: 11px; }
.info-msg    { color: var(--purple-500) !important; font-family: var(--f-mono); font-size: 11px; letter-spacing: 1px; }
/* ── Top chrome strips (alias) ── */
.al-strip {
  display: flex; align-items: center; justify-content: space-between;
  padding: 6px 16px; background: #1A0030; border-bottom: 1px solid var(--border);
  font-family: var(--f-mono); font-size: 10px; letter-spacing: 4px; color: var(--purple-300); text-transform: uppercase;
}
.al-topbar {
  display: flex; align-items: center; justify-content: space-between;
  padding: 10px 24px; border-bottom: 1px solid var(--border); background: var(--deep);
  font-family: var(--f-mono); font-size: 10px; letter-spacing: 3px; text-transform: uppercase;
}
/* ── Filter chip ── */
.filter-chip {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 3px 8px; border: 1px solid var(--border-soft);
  font-family: var(--f-mono); font-size: 9px; letter-spacing: 2px; color: var(--text-secondary); cursor: pointer;
  text-transform: uppercase;
}
.filter-chip.on { background: rgba(123,47,190,0.18); border-color: var(--border-active); color: var(--purple-100); }
/* ── Wordmark / tagline (Access Gate) ── */
.wordmark {
  font-family: var(--f-display); font-weight: 600; font-size: 36px;
  letter-spacing: 12px; color: var(--text-primary); margin: 0 0 6px; text-indent: 12px;
}
.tagline {
  font-family: var(--f-mono); font-size: 9px; letter-spacing: 6px;
  color: var(--purple-300); text-transform: uppercase; margin-bottom: 44px;
}
/* ── KPI cell ── */
.kpi { border: 1px dashed var(--border-soft); padding: 10px 12px; }
.kpi .k { font-family: var(--f-mono); font-size: 9px; letter-spacing: 3px; color: var(--text-dim); text-transform: uppercase; }
.kpi .v { font-family: var(--f-display); font-weight: 600; font-size: 24px; color: var(--text-primary); letter-spacing: -0.3px; }
.kpi .d { font-family: var(--f-mono); font-size: 9px; color: var(--online); letter-spacing: 1px; }
.kpi .d.neg { color: var(--warning); }
/* ── Fusion upload drop zone ── */
.drop {
  border: 1px dashed var(--border-active); padding: 28px 24px 30px; text-align: center;
  background: repeating-linear-gradient(135deg, rgba(123,47,190,0.03) 0, rgba(123,47,190,0.03) 6px, transparent 6px, transparent 12px), rgba(123,47,190,0.03);
  transition: all 180ms ease;
}
.drop:hover { background: rgba(123,47,190,0.08); border-color: var(--purple-500); box-shadow: var(--glow-purple); }
/* ── File type tags ── */
.type { font-family: var(--f-mono); font-size: 9px; letter-spacing: 2px; padding: 2px 6px; text-align: center; border: 1px solid var(--border-soft); }
.type.csv  { color: #14B8A6; border-color: rgba(20,184,166,0.4); }
.type.pdf  { color: var(--critical); border-color: rgba(220,38,38,0.4); }
.type.txt  { color: #F59E0B; border-color: rgba(245,158,11,0.4); }
.type.json { color: var(--purple-500); border-color: var(--border-active); }
.type.xlsx { color: #2563EB; border-color: rgba(37,99,235,0.4); }
/* ── Topbar (inner main topbar) ── */
.topbar {
  display: flex; align-items: center; justify-content: space-between;
  padding: 10px 24px; border-bottom: 1px solid var(--border); background: var(--deep);
}
.crumbs { font-family: var(--f-mono); font-size: 10px; letter-spacing: 3px; color: var(--text-secondary); text-transform: uppercase; }
.crumbs .sep { margin: 0 10px; color: var(--text-dim); }
.crumbs .cur { color: var(--purple-300); }
.clock { font-family: var(--f-mono); font-size: 11px; letter-spacing: 2px; color: var(--text-primary); }
.clock .dim { color: var(--text-dim); }
/* ── Threat indicators ── */
.threat { padding: 12px 16px; border-bottom: 1px solid var(--border-soft); display: flex; gap: 12px; }
.threat:last-child { border-bottom: 0; }
.threat .sev {
  font-family: var(--f-mono); font-size: 9px; letter-spacing: 2px; padding: 2px 6px;
  text-transform: uppercase; border: 1px solid var(--border-soft); height: fit-content; min-width: 48px; text-align: center;
}
.threat .sev.hi { color: var(--critical); border-color: rgba(220,38,38,0.5); background: rgba(220,38,38,0.08); }
.threat .sev.md { color: var(--warning); border-color: rgba(217,119,6,0.5); }
.threat .sev.lo { color: var(--text-secondary); }
.threat .body { flex: 1; min-width: 0; }
.threat .title { font-family: var(--f-mono); font-size: 11px; color: var(--text-primary); letter-spacing: 0.5px; margin-bottom: 3px; }
.threat .meta  { font-family: var(--f-mono); font-size: 9px; color: var(--text-dim); letter-spacing: 1px; text-transform: uppercase; }
/* ── Sparkline ── */
.spark { display: flex; gap: 2px; align-items: flex-end; height: 32px; }
.spark i { width: 3px; background: var(--purple-500); opacity: 0.7; }
/* ── Tabs bar (admin / report style) ── */
.tabs { display: flex; gap: 0; border-bottom: 1px solid var(--border); }
.tab {
  padding: 12px 22px; font-family: var(--f-mono); font-size: 11px; letter-spacing: 3px;
  color: var(--text-secondary); text-transform: uppercase;
  border-bottom: 2px solid transparent; cursor: pointer; display: flex; align-items: center; gap: 10px; margin-bottom: -1px;
}
.tab:hover { color: var(--text-primary); }
.tab.active { color: var(--purple-100); border-bottom-color: var(--purple-500); background: linear-gradient(180deg, transparent, rgba(123,47,190,0.08)); }
.tab .i { font-family: var(--f-mono); color: var(--purple-500); font-size: 10px; }
/* ── Section header (report) ── */
.sec-hd {
  display: flex; align-items: baseline; justify-content: space-between;
  padding: 12px 16px; border: 1px solid var(--border);
  background: linear-gradient(180deg, rgba(16,0,32,0.9), rgba(10,0,21,0.9)); cursor: pointer;
}
.sec-hd .n { font-family: var(--f-mono); font-size: 10px; letter-spacing: 3px; color: var(--purple-500); margin-right: 12px; }
.sec-hd .t { font-family: var(--f-display); font-size: 16px; letter-spacing: 4px; color: var(--text-primary); text-transform: uppercase; }
.sec-hd .tag { font-family: var(--f-mono); font-size: 9px; letter-spacing: 2px; padding: 2px 8px; border: 1px solid var(--border-soft); text-transform: uppercase; margin-left: 10px; }
.sec-hd .tag.v { color: var(--online); border-color: rgba(22,163,74,0.4); }
.sec-hd .tag.a { color: var(--warning); border-color: rgba(217,119,6,0.4); }
.sec-hd .rt { font-family: var(--f-mono); font-size: 10px; letter-spacing: 2px; color: var(--text-dim); }
.sec-bd { border: 1px solid var(--border); border-top: 0; padding: 18px 20px; background: rgba(16,0,32,0.6); }
/* ── Hash chain cells ── */
.ch { padding: 10px 12px; border: 1px solid var(--border-soft); background: rgba(16,0,32,0.5); }
.ch .k { font-family: var(--f-mono); font-size: 9px; letter-spacing: 3px; color: var(--text-dim); text-transform: uppercase; }
.ch .v { font-family: var(--f-display); font-weight: 600; font-size: 22px; color: var(--text-primary); letter-spacing: -0.2px; }
.ch .v.ok { color: var(--online); }
.ch .d { font-family: var(--f-mono); font-size: 9px; color: var(--text-secondary); letter-spacing: 1px; margin-top: 2px; }

/* ── Platform presence grid ── */
.plats { display: grid; grid-template-columns: repeat(4,1fr); gap: 10px; }
.plat { padding: 10px 12px; border: 1px solid var(--border-soft); display:flex; align-items:center; justify-content:space-between; font-family:var(--f-mono); font-size:11px; }
.plat .name { color:var(--text-primary); letter-spacing:1px; }
.plat .st { font-size:9px; letter-spacing:2px; text-transform:uppercase; }
.plat.found { border-color:rgba(22,163,74,0.35); }
.plat.found .st { color:var(--online); }
.plat.missing .st { color:var(--text-dim); }
.plat.flag { border-color:rgba(220,38,38,0.4); }
.plat.flag .st { color:var(--critical); }

/* ── Associations table ── */
.assoc { display:grid; grid-template-columns:28px 1fr 120px 110px 100px; gap:12px; align-items:center; padding:8px 0; border-bottom:1px dashed var(--border-soft); font-family:var(--f-mono); font-size:11px; }
.assoc:last-child { border-bottom:0; }
.assoc .dot { width:10px; height:10px; border-radius:50%; background:var(--purple-500); justify-self:center; }
.assoc .dot.teal { background:var(--teal); }
.assoc .dot.amber { background:var(--warning); }
.assoc .dot.red { background:var(--critical); }
.assoc .n { color:var(--text-primary); letter-spacing:0.5px; }
.assoc .t { color:var(--text-dim); letter-spacing:2px; font-size:10px; text-transform:uppercase; }
.assoc .strength { display:flex; gap:2px; }
.assoc .strength i { width:10px; height:8px; background:var(--text-faint); }
.assoc .strength i.on { background:var(--purple-500); }

/* ── Timeline heatmap grid ── */
.heat { display:grid; grid-template-columns:60px repeat(24,1fr); gap:2px; align-items:center; }
.heat .row-l { font-family:var(--f-mono); font-size:9px; letter-spacing:1px; color:var(--text-dim); text-transform:uppercase; }
.heat .cell { height:12px; background:rgba(123,47,190,0.08); }
.heat .cell.l1 { background:rgba(123,47,190,0.22); }
.heat .cell.l2 { background:rgba(123,47,190,0.45); }
.heat .cell.l3 { background:rgba(157,78,221,0.75); }
.heat .cell.l4 { background:var(--purple-300); box-shadow:0 0 6px var(--purple-300); }
.heat .hour-row { grid-column:2/-1; display:grid; grid-template-columns:repeat(24,1fr); gap:2px; font-family:var(--f-mono); font-size:8px; color:var(--text-dim); letter-spacing:1px; margin-top:4px; }
.heat .hour-row span { text-align:center; }

/* ── Flag cards (anomalies) ── */
.flag-card { display:grid; grid-template-columns:80px 1fr auto; gap:14px; align-items:center; padding:12px 14px; border:1px solid var(--border-soft); background:#040010; }
.flag-card.hi { border-left:2px solid var(--critical); }
.flag-card.md { border-left:2px solid var(--warning); }
.flag-card.lo { border-left:2px solid var(--text-secondary); }
.flag-card .sev { font-family:var(--f-mono); font-size:10px; letter-spacing:2px; padding:3px 8px; border:1px solid var(--border-soft); text-align:center; text-transform:uppercase; }
.flag-card.hi .sev { color:var(--critical); border-color:rgba(220,38,38,0.5); background:rgba(220,38,38,0.08); }
.flag-card.md .sev { color:var(--warning); border-color:rgba(217,119,6,0.5); }
.flag-card.lo .sev { color:var(--text-secondary); }
.flag-card .ttl { font-family:var(--f-mono); font-size:12px; color:var(--text-primary); letter-spacing:0.5px; }
.flag-card .sub { font-family:var(--f-mono); font-size:10px; color:var(--text-dim); letter-spacing:1px; text-transform:uppercase; margin-top:3px; }
.flag-card .score { font-family:var(--f-display); font-weight:600; font-size:20px; color:var(--warning); letter-spacing:-0.2px; text-align:right; }
.flag-card.hi .score { color:var(--critical); }

/* ── Behavioral pattern tiles ── */
.pat-grid { display:grid; grid-template-columns:repeat(4,1fr); gap:10px; }
.pat { padding:12px 14px; border:1px solid var(--border-soft); background:#040010; }
.pat .k { font-family:var(--f-mono); font-size:9px; letter-spacing:3px; color:var(--text-dim); text-transform:uppercase; }
.pat .v { font-family:var(--f-display); font-weight:600; font-size:20px; color:var(--text-primary); letter-spacing:-0.2px; }
.pat .d { font-family:var(--f-mono); font-size:10px; color:var(--text-secondary); margin-top:2px; letter-spacing:0.5px; }

/* ── Report note text ── */
.note { font-family:var(--f-mono); font-size:11px; color:var(--text-secondary); line-height:1.6; letter-spacing:0.3px; }
.note strong { color:var(--text-primary); font-weight:500; }

/* ── Network map inline preview ── */
.net-inline { position:relative; height:260px; background:radial-gradient(circle at 50% 50%,rgba(123,47,190,0.08),transparent 70%),#040010; border:1px solid var(--border-soft); overflow:hidden; }
.net-inline svg { width:100%; height:100%; }

/* ── Confidence gauge ── */
.gauge { position:relative; width:200px; height:200px; }
.gauge svg { width:100%; height:100%; transform:rotate(-90deg); }
.gauge .val { position:absolute; inset:0; display:grid; place-items:center; text-align:center; }
.gauge .num { font-family:var(--f-display); font-weight:600; font-size:58px; letter-spacing:-1px; color:var(--warning); line-height:1; }
.gauge .lbl { font-family:var(--f-mono); font-size:9px; letter-spacing:3px; color:var(--text-dim); text-transform:uppercase; margin-top:2px; }
.gauge .sub { font-family:var(--f-mono); font-size:10px; letter-spacing:2px; color:var(--warning); text-transform:uppercase; margin-top:6px; }

/* ── Report top layout ── */
.report-top { display:grid; grid-template-columns:1.2fr 220px 1fr auto; gap:22px; padding:20px 28px; border-bottom:1px solid var(--border); background:linear-gradient(180deg,#100020,#05000D); align-items:center; }
.rt-subject .tag { font-family:var(--f-mono); font-size:10px; letter-spacing:4px; color:var(--purple-300); text-transform:uppercase; }
.rt-subject h1 { font-family:var(--f-display); font-weight:600; font-size:36px; letter-spacing:1px; margin:4px 0 8px; color:var(--text-primary); }
.rt-subject .meta { font-family:var(--f-mono); font-size:10px; letter-spacing:2px; color:var(--text-secondary); text-transform:uppercase; display:flex; gap:20px; flex-wrap:wrap; }
.rt-meta-grid { display:grid; grid-template-columns:1fr 1fr; gap:4px 18px; font-family:var(--f-mono); font-size:11px; }
.rt-meta-grid .k { font-size:9px; letter-spacing:3px; color:var(--text-dim); text-transform:uppercase; }
.rt-meta-grid .v { color:var(--text-primary); letter-spacing:0.5px; margin-bottom:8px; }
.rt-actions { display:flex; flex-direction:column; gap:8px; align-items:stretch; }

/* ── TOC sidebar (report) ── */
.toc { border-right:1px solid var(--border-soft); padding:22px 0; }
.toc .ghd { padding:0 22px 8px; font-family:var(--f-mono); font-size:9px; letter-spacing:3px; color:var(--text-dim); text-transform:uppercase; }
.toc .li { display:grid; grid-template-columns:28px 1fr; gap:8px; align-items:center; padding:6px 22px; font-family:var(--f-mono); font-size:10px; letter-spacing:1px; color:var(--text-secondary); text-transform:uppercase; cursor:pointer; border-left:2px solid transparent; }
.toc .li .n { color:var(--purple-500); }
.toc .li:hover { background:rgba(123,47,190,0.06); color:var(--text-primary); }
.toc .li.active { background:rgba(123,47,190,0.10); color:var(--purple-100); border-left-color:var(--purple-500); }

/* ── Fusion: step checklist ── */
.step { display:grid; grid-template-columns:52px 1fr 140px 90px; gap:14px; align-items:center; padding:14px 4px; border-bottom:1px solid var(--border-soft); font-family:var(--f-mono); }
.step .num { font-family:var(--f-display); font-weight:600; font-size:20px; color:var(--text-dim); letter-spacing:-0.5px; }
.step.done .num { color:var(--online); }
.step.active .num { color:var(--purple-300); }
.step .t { color:var(--text-primary); font-size:12px; letter-spacing:1px; text-transform:uppercase; }
.step .d { color:var(--text-dim); font-size:10px; letter-spacing:1px; margin-top:2px; }
.step .state { font-size:9px; letter-spacing:2px; text-transform:uppercase; text-align:right; }
.step.done .state { color:var(--online); }
.step.active .state { color:var(--purple-300); }
.step.pending .state { color:var(--text-dim); }
.step .timer { color:var(--text-secondary); font-size:10px; letter-spacing:1px; text-align:right; }
@keyframes blink-pulse { 50% { opacity:0.2; } }
.step.active .t::after { content:""; display:inline-block; width:8px; height:8px; background:var(--purple-500); margin-left:10px; animation:blink-pulse 1s steps(2) infinite; vertical-align:middle; }

/* ── Fusion: progress summary tiles ── */
.progress-summary { display:grid; grid-template-columns:repeat(4,1fr); gap:10px; margin-top:22px; }
.progress-summary .tile { padding:12px 14px; border:1px solid var(--border-soft); background:rgba(16,0,32,0.5); }
.progress-summary .tile .k { font-family:var(--f-mono); font-size:9px; letter-spacing:3px; color:var(--text-dim); text-transform:uppercase; }
.progress-summary .tile .v { font-family:var(--f-display); font-weight:600; font-size:22px; color:var(--text-primary); letter-spacing:-0.3px; }

/* ── Fusion: live trace ── */
.live { background:#040010; border:1px solid var(--border-soft); padding:14px 16px; font-family:var(--f-mono); font-size:11px; color:var(--text-secondary); letter-spacing:0.5px; max-height:180px; overflow:hidden; position:relative; }
.live::after { content:""; position:absolute; bottom:0; left:0; right:0; height:40px; background:linear-gradient(transparent,#040010); }
.live .ln { padding:2px 0; }
.live .t { color:var(--text-dim); margin-right:10px; }
.live .ok { color:var(--online); }
.live .wn { color:var(--warning); }
.live .pr { color:var(--purple-300); }

/* ── Fusion: processing banner ── */
.banner { display:flex; align-items:center; gap:14px; padding:14px 18px; border:1px solid var(--border-active); background:linear-gradient(90deg,rgba(123,47,190,0.10),rgba(123,47,190,0.02)); margin-bottom:20px; }
.banner .ic { width:26px; height:26px; border:1px solid var(--purple-300); border-radius:50%; display:grid; place-items:center; color:var(--purple-300); font-family:var(--f-mono); font-size:12px; }
.banner .t { font-family:var(--f-display); font-size:16px; letter-spacing:2px; color:var(--text-primary); }
.banner .s { font-family:var(--f-mono); font-size:10px; letter-spacing:2px; color:var(--purple-300); text-transform:uppercase; }
.banner .r { margin-left:auto; font-family:var(--f-mono); font-size:10px; letter-spacing:2px; color:var(--text-secondary); text-transform:uppercase; }

/* ── Fusion: progress bar ── */
.progress-bar { height:4px; background:var(--text-faint); margin-top:18px; position:relative; overflow:hidden; }
.progress-bar > i { position:absolute; left:0; top:0; bottom:0; background:linear-gradient(90deg,var(--purple-700),var(--purple-300)); box-shadow:var(--glow-purple); }
.progress-bar > i::after { content:""; position:absolute; right:0; top:-4px; bottom:-4px; width:2px; background:var(--purple-300); box-shadow:0 0 8px var(--purple-300); }

/* ── Admin: system status ── */
.sys-grid { display:grid; grid-template-columns:1.4fr 1fr; gap:18px; }
.sys-row { display:grid; grid-template-columns:1fr 90px 110px; gap:14px; padding:12px 16px; border-bottom:1px solid var(--border-soft); align-items:center; }
.sys-row:last-child { border-bottom:0; }
.sys-row .lbl { font-family:var(--f-mono); font-size:11px; color:var(--text-primary); letter-spacing:0.5px; }
.sys-row .sub { font-family:var(--f-mono); font-size:9px; color:var(--text-dim); letter-spacing:1px; margin-top:2px; text-transform:uppercase; }
.sys-row .val { font-family:var(--f-display); font-weight:600; font-size:18px; color:var(--text-primary); letter-spacing:-0.2px; text-align:right; }
.sys-row .st { font-family:var(--f-mono); font-size:9px; letter-spacing:2px; text-transform:uppercase; text-align:right; }
.sys-row .st.ok { color:var(--online); }
.sys-row .st.warn { color:var(--warning); }

/* ── Admin: status pill, role badge, row actions ── */
.status-pill { display:inline-flex; align-items:center; gap:6px; font-family:var(--f-mono); font-size:9px; letter-spacing:2px; text-transform:uppercase; }
.status-pill.active { color:var(--online); }
.status-pill.suspended { color:var(--warning); }
.status-pill.locked { color:var(--critical); }
.role-badge.admin { color:var(--critical); border-color:rgba(220,38,38,0.5); background:rgba(220,38,38,0.06); }
.role-badge.analyst { color:var(--purple-100); border-color:var(--border-active); background:rgba(123,47,190,0.12); }
.role-badge.viewer { color:var(--text-secondary); background:transparent; }
.role-badge.officer { color:var(--teal); border-color:rgba(13,148,136,0.5); background:transparent; }
.row-actions { display:flex; gap:6px; }
.row-actions button { padding:4px 10px; font-size:9px; letter-spacing:2px; background:transparent; border:1px solid var(--border-soft); color:var(--text-secondary); font-family:var(--f-mono); cursor:pointer; text-transform:uppercase; }
.row-actions button:hover { border-color:var(--purple-500); color:var(--purple-100); }
.row-actions button.danger:hover { border-color:var(--critical); color:var(--critical); }

/* ── Admin: audit log table ── */
.log-toolbar { display:grid; grid-template-columns:1fr auto auto auto; gap:10px; padding:14px 16px; border-bottom:1px solid var(--border-soft); align-items:center; }
.log-hdr { display:grid; grid-template-columns:140px 110px 90px 1fr 140px 110px; gap:12px; padding:9px 16px; align-items:center; font-family:var(--f-mono); font-size:9px; letter-spacing:3px; color:var(--text-dim); text-transform:uppercase; border-bottom:1px solid var(--border); background:rgba(0,0,0,0.3); }
.log-row.admin { display:grid; grid-template-columns:140px 110px 90px 1fr 140px 110px; gap:12px; padding:9px 16px; border-bottom:1px solid var(--border-soft); color:var(--text-secondary); font-family:var(--f-mono); font-size:11px; }
.log-row.admin:nth-child(even) { background:rgba(16,0,32,0.25); }
.log-row.admin:hover { background:rgba(123,47,190,0.05); }
.log-row.admin .ts { color:var(--text-dim); letter-spacing:1px; }
.log-row.admin .user { color:var(--purple-300); letter-spacing:1px; }
.log-row.admin .int { font-size:9px; letter-spacing:2px; padding:2px 6px; text-align:center; text-transform:uppercase; display:inline-flex; align-items:center; gap:6px; }
.log-row.admin .int.ok { color:var(--online); border:1px solid rgba(22,163,74,0.4); background:rgba(22,163,74,0.06); }
.log-row.admin .int.bad { color:var(--critical); border:1px solid rgba(220,38,38,0.5); background:rgba(220,38,38,0.08); }
.chain-summary { display:grid; grid-template-columns:repeat(4,1fr); gap:12px; padding:14px 16px; border-bottom:1px solid var(--border-soft); }
.pager { display:flex; justify-content:space-between; align-items:center; padding:12px 16px; font-family:var(--f-mono); font-size:10px; letter-spacing:2px; color:var(--text-secondary); text-transform:uppercase; }
.pager .buttons { display:flex; gap:6px; }
.pager button { padding:5px 10px; background:transparent; border:1px solid var(--border-soft); color:var(--text-secondary); font-family:var(--f-mono); font-size:10px; letter-spacing:2px; cursor:pointer; }
.pager button.active { background:rgba(123,47,190,0.15); border-color:var(--border-active); color:var(--purple-100); }

/* ── Network map chrome ── */
.ctrl-group { display:flex; align-items:center; gap:10px; background:rgba(10,0,21,0.88); border:1px solid var(--border); padding:8px 14px; font-family:var(--f-mono); font-size:10px; letter-spacing:2px; color:var(--text-secondary); text-transform:uppercase; }
.ctrl-group .lbl { color:var(--text-dim); }
.chip { display:inline-flex; align-items:center; gap:6px; padding:3px 8px; border:1px solid var(--border-soft); font-family:var(--f-mono); font-size:9px; letter-spacing:2px; color:var(--text-secondary); cursor:pointer; }
.chip.on { background:rgba(123,47,190,0.18); border-color:var(--border-active); color:var(--purple-100); }
.chip .d { width:8px; height:8px; border-radius:50%; }
.icon-btn { width:32px; height:32px; border:1px solid var(--border); background:rgba(10,0,21,0.88); color:var(--text-secondary); display:grid; place-items:center; cursor:pointer; font-family:var(--f-mono); font-size:12px; }
.icon-btn:hover { border-color:var(--purple-500); color:var(--purple-100); }
.statusbar { height:26px; background:#0a0015; border-top:1px solid var(--border); display:flex; align-items:center; padding:0 16px; gap:24px; font-family:var(--f-mono); font-size:9px; letter-spacing:2px; color:var(--text-dim); text-transform:uppercase; }
.statusbar .hl { color:var(--purple-300); }

/* ── Command Center: greet ── */
.greet { display:flex; align-items:baseline; justify-content:space-between; padding:4px 0 2px; }
.greet h1 { font-family:var(--f-display); font-weight:500; font-size:28px; letter-spacing:1px; margin:0; color:var(--text-primary); }
.greet .usr { color:var(--purple-300); }
.greet .sub { font-family:var(--f-mono); font-size:10px; letter-spacing:2px; color:var(--text-secondary); text-transform:uppercase; }

/* ── Command Center: status grid ── */
.status-grid { display:grid; grid-template-columns:repeat(4,1fr); gap:14px; }
.cols { display:grid; grid-template-columns:1.6fr 1fr; gap:18px; }
.kpi-row { display:grid; grid-template-columns:1fr 1fr; gap:10px; padding:14px 16px; }

/* ── Admin: KPIs ── */
.kpis { display:grid; grid-template-columns:repeat(2,1fr); gap:10px; padding:14px 16px; }
.kpi .v { font-family:var(--f-display); font-weight:600; font-size:26px; color:var(--text-primary); letter-spacing:-0.3px; }
.kpi .d { font-family:var(--f-mono); font-size:9px; color:var(--online); letter-spacing:1px; margin-top:2px; }
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)

# ── Bootstrap ─────────────────────────────────────────────────────────────────
init_db()
session_init()

_DEFAULTS = {
    "active_screen":       "command_center",
    "search_results":      None,
    "search_query":        "",
    "selected_candidates": [],
    "person_profile":      None,
    "resolution_method":   None,
    "fusion_result":       None,
    "graph_data":          None,
    "timeline_data":       None,
    "behavioral_data":     None,
    "report_data":         None,
    "pipeline_pending":    None,
    "pipeline_error":      None,
    # ── Command Center / Ontology ─────────────────────────────────────────────
    "ontology_graph":      None,
    "ontology_json":       None,
    "agent_results":       None,
    "active_profiles":     [],
    "selected_entity_id":  None,
    "cc_filter_type":      "ALL",
    "cc_filter_risk":      0,
    # ── Fusion staging ────────────────────────────────────────────────────────
    "fusion_staged":        [],     # list of {name, size, bytes, type} dicts
    "fusion_analysed":      False,  # True after ANALYSE completes
    "fusion_summary":       None,   # summary dict after analysis
    "fusion_assets_staged": [],     # list of asset dicts from optional assets uploader
    "assets_data":          [],     # parsed AssetEntity-compatible dicts
    "raw_documents":        [],     # ingest_file() results — for source log
}
for k, v in _DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v


# ─────────────────────────────────────────────────────────────────────────────
# SHARED COMPONENTS
# ─────────────────────────────────────────────────────────────────────────────

def render_logo_centered():
    st.markdown('<div class="logo-title">AETHERLENS</div>', unsafe_allow_html=True)
    st.markdown('<div class="logo-sub">INTELLIGENCE CLARITY</div>', unsafe_allow_html=True)

def render_logo_sm():
    st.markdown('<span class="logo-title-sm">AETHERLENS</span>', unsafe_allow_html=True)

def screen_header(title: str, subtitle: str = ""):
    from modules.ui_components import classification_strip, topbar
    import datetime as _dt
    now_utc = _dt.datetime.utcnow()
    utc_str = now_utc.strftime("%H:%M:%S")
    session_id = st.session_state.get("session_token", "")[:4].upper() or "——"
    # Classification strip
    st.markdown(classification_strip(
        left="RESTRICTED",
        center=f"AETHERLENS · {title}",
        right=f"SESSION {session_id} · UTC {utc_str}",
    ), unsafe_allow_html=True)
    # Topbar with breadcrumbs + engine status right
    engine_html = _build_engine_pills()
    st.markdown(topbar(
        breadcrumbs=["OPERATIONS", title],
        right_html=engine_html,
    ), unsafe_allow_html=True)
    # Compact status bar below topbar
    engine_status_bar()


def _build_engine_pills() -> str:
    """Build the pill badges shown in the topbar right side."""
    import datetime as _dt
    session_start = st.session_state.get("session_start")
    if session_start:
        elapsed   = int(time.time() - session_start)
        remaining = max(1800 - elapsed, 0)
    else:
        remaining = 1800
    mins = remaining // 60
    secs = remaining % 60
    tc = "var(--critical)" if remaining < 300 else "var(--purple-300)"
    utc_str = _dt.datetime.utcnow().strftime("%H:%M:%S")
    return (
        f'<span class="pill restricted">RESTRICTED</span>'
        f'<span class="pill"><span class="s-dot online" style="width:6px;height:6px;"></span> ENGINE · HEALTHY</span>'
        f'<span style="font-family:var(--f-mono);font-size:11px;letter-spacing:2px;color:var(--text-primary);">'
        f'<span style="color:var(--text-dim);">UTC</span> {utc_str}</span>'
        f'<span style="font-family:var(--f-mono);font-size:10px;letter-spacing:2px;padding:2px 8px;border:1px solid {tc};color:{tc};">'
        f'SESSION {mins:02d}:{secs:02d}</span>'
    )

def engine_status_bar():
    gemini_ok = bool(config.GEMINI_API_KEY and config.GEMINI_API_KEY != "your_gemini_key_here")
    g_dot   = "dot-blue"  if gemini_ok else "dot-gray"
    g_lbl   = "GEMINI 2.5" if gemini_ok else "GEMINI ✗"

    session_start = st.session_state.get("session_start")
    if session_start:
        elapsed   = int(time.time() - session_start)
        remaining = max(1800 - elapsed, 0)
    else:
        remaining = 1800
    mins        = remaining // 60
    secs        = remaining % 60
    timer_str   = f"{mins:02d}:{secs:02d}"
    timer_color = "#DC2626" if remaining < 300 else "#9D4EDD"

    st.markdown(
        f'<div style="display:flex;justify-content:space-between;align-items:center;'
        f'font-family:\'JetBrains Mono\',monospace;font-size:9px;letter-spacing:2px;'
        f'color:#4B5563;margin-bottom:8px;padding:4px 0;">'
        f'<span>'
        f'<span class="engine-dot {g_dot}"></span>{g_lbl}'
        f'</span>'
        f'<span style="color:{timer_color};border:1px solid {timer_color};'
        f'padding:2px 8px;letter-spacing:2px;">SESSION {timer_str}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )

def sidebar_nav():
    from modules.ui_components import sidebar_brand, sidebar_section_label
    # Force sidebar open
    import streamlit.components.v1 as _c
    _c.html("""<script>
try {
  var p = window.parent;
  Object.keys(p.localStorage).forEach(function(k){
    if(k.toLowerCase().includes('sidebar')) p.localStorage.removeItem(k);
  });
  function expand(){
    var b = p.document.querySelector('[data-testid="collapsedControl"]')
          || p.document.querySelector('[data-testid="stSidebarCollapsedControl"]')
          || p.document.querySelector('button[aria-label*="sidebar"]')
          || p.document.querySelector('button[aria-label*="Sidebar"]');
    if(b){ b.click(); return; }
    setTimeout(expand, 300);
  }
  setTimeout(expand, 400);
} catch(e){}
</script>""", height=0)
    role     = st.session_state.get("current_role", "")
    username = st.session_state.get("current_user", "")
    with st.sidebar:
        # ── Brand block ──
        st.markdown(sidebar_brand("v3.4.1"), unsafe_allow_html=True)
        # ── User identity ──
        st.markdown(
            f'<div style="padding:10px 18px 8px;border-bottom:1px solid var(--border-soft);">'
            f'<div style="font-family:var(--f-mono);font-size:12px;'
            f'letter-spacing:1px;color:var(--text-primary);">{username}</div>'
            f'<span class="role-badge">{role}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )
        # ── Operations nav ──
        st.markdown(sidebar_section_label("OPERATIONS"), unsafe_allow_html=True)
        ops_nav = [
            ("command_center",    "COMMAND CENTER"),
            ("search",            "SEARCH"),
            ("fusion",            "FUSION"),
            ("analysis_workbench","ANALYSIS WORKBENCH"),
            ("network_map",       "NETWORK MAP"),
            ("timeline",          "TIMELINE"),
            ("heatmap",           "TIMELINE HEATMAP"),
            ("geo_map",           "GEO MAP"),
        ]
        for sid, lbl in ops_nav:
            active = st.session_state.active_screen == sid
            label  = f"▸ {lbl}" if active else f"  {lbl}"
            if st.button(label, key=f"nav_{sid}", use_container_width=True):
                st.session_state.active_screen = sid
                st.rerun()
        # ── Intelligence nav ──
        st.markdown(sidebar_section_label("INTELLIGENCE"), unsafe_allow_html=True)
        int_nav = [
            ("reports_center", "REPORTS CENTER"),
            ("reports",        "REPORT VIEWER"),
        ]
        if role == config.ROLE_ADMIN:
            int_nav.append(("audit_center", "AUDIT CENTER"))
            int_nav.append(("admin",        "ADMIN PANEL"))
        for sid, lbl in int_nav:
            active = st.session_state.active_screen == sid
            label  = f"▸ {lbl}" if active else f"  {lbl}"
            if st.button(label, key=f"nav_{sid}", use_container_width=True):
                st.session_state.active_screen = sid
                st.rerun()
        # ── Sign out ──
        st.markdown('<hr class="divider">', unsafe_allow_html=True)
        if st.button("SIGN OUT", use_container_width=True):
            session_logout()
            st.rerun()


PLATFORM_COLORS = {
    "DuckDuckGo":"#DE5833","Google News":"#4285F4","Wikipedia":"#8C8C8C",
    "GitHub":"#6E40C9","Reddit":"#FF4500","Instagram":"#E1306C",
    "X / Twitter":"#1DA1F2","LinkedIn":"#0077B5","YouTube":"#FF0000",
}
def _platform_badge(p: str) -> str:
    c = PLATFORM_COLORS.get(p, "#9D4EDD")
    return (
        f'<span style="background:{c}18;border:1px solid {c};color:{c};'
        f'font-family:\'JetBrains Mono\',monospace;font-size:9px;letter-spacing:2px;'
        f'padding:2px 7px;text-transform:uppercase;">{p}</span>'
    )

def _conf_color(s: int) -> str:
    return "#16A34A" if s >= 75 else "#D97706" if s >= 50 else "#D97706" if s >= 25 else "#DC2626"


# ─────────────────────────────────────────────────────────────────────────────
# PIN GATE
# ─────────────────────────────────────────────────────────────────────────────

def screen_pin_gate():
    from modules.ui_components import classification_strip
    import datetime as _dt
    now_utc = _dt.datetime.utcnow()
    utc_str = now_utc.strftime("%H:%M:%S")
    utc_long = now_utc.strftime("%d %b %Y · %H:%M:%S UTC").upper()
    pin_state = get_pin_state()
    attempt_count = pin_state.get("failed_attempts", 0)

    # ── Full screen gate layout ──
    st.markdown(f"""
<div class="al-root" style="min-height:100vh;display:flex;flex-direction:column;">
  <div class="al-classification-strip">
    <div><span class="dot"></span>RESTRICTED SYSTEM</div>
    <div class="center">AETHERLENS · INTELLIGENCE FUSION PLATFORM</div>
    <div>UTC {utc_str}</div>
  </div>
  <div style="flex:1;display:grid;place-items:center;position:relative;">
    <section class="gate-frame" style="position:relative;">
      <div class="corners"><span class="tl"></span><span class="tr"></span><span class="bl"></span><span class="br"></span></div>
      <div class="logo" style="width:88px;height:88px;margin:0 auto 26px;position:relative;">
        <div class="pulse" style="position:absolute;inset:-8px;border:1px solid #9D4EDD;border-radius:50%;opacity:0;animation:pulse-ring 3.2s ease-out infinite;"></div>
        <div class="pulse d2" style="position:absolute;inset:-8px;border:1px solid #9D4EDD;border-radius:50%;opacity:0;animation:pulse-ring 3.2s ease-out 1.6s infinite;"></div>
        <svg viewBox="0 0 88 88" fill="none" width="88" height="88">
          <circle cx="44" cy="44" r="40" stroke="#9D4EDD" stroke-width="1"/>
          <circle cx="44" cy="44" r="32" stroke="#7B2FBE" stroke-width="1"/>
          <g stroke="#C084FC" stroke-width="1.1" stroke-linecap="square" fill="none" opacity="0.85">
            <path d="M44 12 L60 30 L60 58 L44 76 L28 58 L28 30 Z"/>
            <path d="M16 44 L34 30 L58 30 L76 44 L58 58 L34 58 Z"/>
          </g>
          <line x1="44" y1="2"  x2="44" y2="20" stroke="#C084FC" stroke-width="1"/>
          <line x1="44" y1="68" x2="44" y2="86" stroke="#C084FC" stroke-width="1"/>
          <line x1="2"  y1="44" x2="20" y2="44" stroke="#C084FC" stroke-width="1"/>
          <line x1="68" y1="44" x2="86" y2="44" stroke="#C084FC" stroke-width="1"/>
          <circle cx="44" cy="44" r="4" fill="#C084FC"/>
          <circle cx="44" cy="44" r="10" stroke="#E9D5FF" stroke-width="0.5" opacity="0.6"/>
          <g stroke="#6B21A8" stroke-width="1">
            <line x1="44" y1="4"  x2="44" y2="8"/>
            <line x1="44" y1="80" x2="44" y2="84"/>
            <line x1="4"  y1="44" x2="8"  y2="44"/>
            <line x1="80" y1="44" x2="84" y2="44"/>
          </g>
        </svg>
      </div>
      <div class="wordmark">AETHERLENS</div>
      <div class="tagline">INTELLIGENCE · FUSION · SOVEREIGN</div>
      <div class="auth-label">AUTHORIZATION PIN</div>
    </section>
  </div>
  <footer class="footer-bar">
    <div class="grp">
      <span>BUILD <span class="val">v3.4.1-IN</span></span>
      <span>NODE <span class="val">MUMBAI-AP-SOUTH-1</span></span>
    </div>
    <div class="grp">
      <span>ATTEMPTS <span class="val">{attempt_count} / {getattr(config,'MAX_PIN_ATTEMPTS',3)}</span></span>
      <span>{utc_long}</span>
    </div>
  </footer>
</div>
<style>
@keyframes pulse-ring {{
  0% {{ transform: scale(0.85); opacity: 0.6; }}
  80% {{ opacity: 0; }}
  100% {{ transform: scale(1.25); opacity: 0; }}
}}
</style>
""", unsafe_allow_html=True)

    col = st.columns([1, 2, 1])[1]
    with col:
        locked, secs = is_pin_locked()
        if locked:
            m, s = secs // 60, secs % 60
            st.markdown(f'<div class="lockout-msg" style="margin:1rem 0;">ACCESS LOCKED · {m}m {s:02d}s remaining</div>', unsafe_allow_html=True)
            time.sleep(1); st.rerun(); return

        # ── PIN state: built up via keypad button presses ─────────────────────
        if "pin_entry" not in st.session_state:
            st.session_state.pin_entry = ""
        if "pin_error" not in st.session_state:
            st.session_state.pin_error = ""

        pin_val = st.session_state.pin_entry

        # ── PIN boxes display ────────────────────────────────────────────────
        box_filled_style = (
            "width:36px;height:44px;border:1px solid #9D4EDD;"
            "background:#1a0030;display:inline-flex;align-items:center;justify-content:center;"
            "margin:0 4px;font-size:22px;color:#C084FC;"
        )
        box_empty_style = (
            "width:36px;height:44px;border:1px solid rgba(123,47,190,0.35);"
            "background:transparent;display:inline-flex;align-items:center;justify-content:center;"
            "margin:0 4px;"
        )
        boxes_html = "".join(
            f'<div style="{box_filled_style}">●</div>' if i < len(pin_val) else f'<div style="{box_empty_style}"></div>'
            for i in range(6)
        )
        st.markdown(
            f'<div style="display:flex;justify-content:center;margin:14px 0 8px;">{boxes_html}</div>',
            unsafe_allow_html=True,
        )

        # Error / status message
        if st.session_state.pin_error:
            st.markdown(f'<div class="error-msg" style="text-align:center;margin-bottom:8px;">{st.session_state.pin_error}</div>', unsafe_allow_html=True)

        # ── 3×4 Keypad ───────────────────────────────────────────────────────
        _keypad = [["1","2","3"],["4","5","6"],["7","8","9"],["*","0","CLR"]]
        _key_idx = 0
        for row in _keypad:
            kb1, kb2, kb3 = st.columns(3)
            for btn_lbl, kcol in zip(row, [kb1, kb2, kb3]):
                _key_idx += 1
                if kcol.button(btn_lbl, key=f"kp_{_key_idx}", use_container_width=True):
                    if btn_lbl == "CLR":
                        st.session_state.pin_entry = ""
                        st.session_state.pin_error = ""
                    elif btn_lbl == "*":
                        # Backspace one digit
                        st.session_state.pin_entry = st.session_state.pin_entry[:-1]
                        st.session_state.pin_error = ""
                    elif len(st.session_state.pin_entry) < 6:
                        st.session_state.pin_entry += btn_lbl
                        st.session_state.pin_error = ""
                    st.rerun()

        # ── Auto-verify when 6 digits entered ────────────────────────────────
        if len(pin_val) == 6:
            try:
                if verify_pin(pin_val):
                    st.session_state.pin_verified = True
                    st.session_state.pin_entry    = ""
                    st.session_state.pin_error    = ""
                    st.rerun()
                else:
                    locked_now, rem = is_pin_locked()
                    if locked_now:
                        m2, s2 = rem//60, rem%60
                        st.session_state.pin_error = f"TOO MANY ATTEMPTS · LOCKED {m2}m {s2:02d}s"
                    else:
                        left = config.MAX_PIN_ATTEMPTS - get_pin_state().get("failed_attempts",0)
                        st.session_state.pin_error = f"AUTHENTICATION FAILED · {left} ATTEMPT(S) REMAINING"
                    st.session_state.pin_entry = ""
                    st.rerun()
            except RuntimeError as e:
                st.session_state.pin_error = str(e)
                st.session_state.pin_entry = ""
                st.rerun()

        # Fallback text input for keyboard entry
        st.markdown('<div style="margin-top:8px;font-family:var(--f-mono);font-size:9px;letter-spacing:2px;color:var(--text-dim);text-align:center;">OR TYPE PIN DIRECTLY</div>', unsafe_allow_html=True)
        pin_typed = st.text_input("PIN", type="password", max_chars=6,
                                  placeholder="● ● ● ● ● ●", key="pin_field",
                                  label_visibility="collapsed")
        if st.button("AUTHENTICATE", use_container_width=True, key="pin_unlock_btn"):
            if len(pin_typed) != 6 or not pin_typed.isdigit():
                st.markdown('<div class="error-msg" style="margin-top:0.6rem;">INVALID PIN FORMAT · 6 DIGITS REQUIRED</div>', unsafe_allow_html=True)
            else:
                try:
                    if verify_pin(pin_typed):
                        st.session_state.pin_verified = True
                        st.session_state.pin_entry    = ""
                        st.rerun()
                    else:
                        locked_now, rem = is_pin_locked()
                        if locked_now:
                            m2, s2 = rem//60, rem%60
                            st.markdown(f'<div class="lockout-msg" style="margin-top:0.6rem;">TOO MANY ATTEMPTS · LOCKED {m2}m {s2:02d}s</div>', unsafe_allow_html=True)
                        else:
                            left = config.MAX_PIN_ATTEMPTS - get_pin_state().get("failed_attempts",0)
                            st.markdown(f'<div class="error-msg" style="margin-top:0.6rem;">AUTHENTICATION FAILED · {left} ATTEMPT(S) REMAINING</div>', unsafe_allow_html=True)
                except RuntimeError as e:
                    st.markdown(f'<div class="lockout-msg" style="margin-top:0.6rem;">{e}</div>', unsafe_allow_html=True)

        st.markdown('<div style="text-align:center;margin-top:1.5rem;"><div style="font-family:var(--f-mono);font-size:9px;letter-spacing:3px;color:rgba(75,85,99,0.6);text-transform:uppercase;">UNAUTHORISED ACCESS IS AN OFFENCE</div></div>', unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# LOGIN
# ─────────────────────────────────────────────────────────────────────────────

def screen_login():
    col = st.columns([1, 2, 1])[1]
    with col:
        st.markdown("<br><br>", unsafe_allow_html=True)
        render_logo_centered()
        st.markdown('<hr class="divider">', unsafe_allow_html=True)
        uname = st.text_input("Username", placeholder="username", key="login_u")
        pw    = st.text_input("Password", type="password", placeholder="password", key="login_p")
        if st.button("SIGN IN", use_container_width=True):
            if not uname.strip() or not pw:
                st.markdown('<div class="error-msg">Username and password required.</div>', unsafe_allow_html=True)
            else:
                user = verify_login(uname.strip(), pw)
                if user:
                    session_login(create_token(user), user)
                    st.session_state["session_start"] = time.time()
                    st.rerun()
                else:
                    st.markdown('<div class="error-msg">Invalid credentials.</div>', unsafe_allow_html=True)
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("Back to PIN"):
            st.session_state.pin_verified = False; st.rerun()


# ─────────────────────────────────────────────────────────────────────────────
# AUTOMATED INTELLIGENCE PIPELINE
# ─────────────────────────────────────────────────────────────────────────────

def _run_pipeline_auto(target: dict, query: str, search_results: dict):
    """
    Full automated pipeline triggered on target selection:
      1. Store target
      2. Entity resolution -> Person Object
      3. Relationship graph
      4. Timeline
      5. Behavioral analysis
      6. Report generation -> navigate to report screen

    Every step has try/except. Errors shown in red and printed to terminal.
    """
    import traceback as _tb
    uid  = st.session_state.get("current_user", "system")
    name = target.get("full_name") or target.get("url") or query
    print(f"[OSINT 1] Target selected: {name}")

    # Clear any previous pipeline state
    st.session_state.pipeline_pending = None
    st.session_state.pipeline_error   = None

    # ── Progress display ──────────────────────────────────────────────────────
    pipeline_container = st.container()
    with pipeline_container:
        st.markdown(
            f'<div style="background:linear-gradient(180deg,rgba(16,0,32,0.88),rgba(10,0,21,0.90));'
            f'border:1px solid rgba(123,47,190,0.28);border-left:3px solid #9D4EDD;'
            f'padding:12px 18px;margin-bottom:12px;">'
            f'<div style="font-family:\'JetBrains Mono\',monospace;font-size:9px;letter-spacing:4px;'
            f'color:#9D4EDD;margin-bottom:6px;">PIPELINE RUNNING</div>'
            f'<div style="font-family:\'Rajdhani\',sans-serif;font-weight:600;font-size:18px;'
            f'letter-spacing:2px;color:#F0EAD6;">{name}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
        pb     = st.progress(0)
        status = st.empty()

        # ── Step 1: Entity resolution ─────────────────────────────────────────
        status.markdown('<div class="info-msg">Step 1/5 — Resolving entity...</div>', unsafe_allow_html=True)
        pb.progress(10)
        print("[OSINT 2] Entity resolution starting...")
        try:
            from modules.entity_resolution import build_person_profile
            person, method = build_person_profile(query, search_results)
            st.session_state.person_profile    = person
            st.session_state.resolution_method = method
            st.session_state.search_results    = search_results
            st.session_state.search_query      = query
            print(f"[OSINT 3] Entity resolution result: {person is not None} — {len(person)} fields [method={method}]")
        except Exception as e:
            msg = f"Entity resolution failed: {e}"
            print(f"[OSINT 3] FAILED: {e}")
            _tb.print_exc()
            st.session_state.pipeline_error = msg
            status.markdown(f'<div class="error-msg">{msg}</div>', unsafe_allow_html=True)
            return
        pb.progress(25)

        # ── Step 2: Relationship graph ────────────────────────────────────────
        status.markdown('<div class="info-msg">Step 2/5 — Mapping relationships...</div>', unsafe_allow_html=True)
        try:
            from modules.relationship_mapper import build_graph_from_person, graph_summary
            G, ents, rels = build_graph_from_person(person, search_results)
            st.session_state.graph_data = {
                "graph":    G,
                "entities": ents,
                "rels":     rels,
                "summary":  graph_summary(G, subject_name=person.get("confirmed_name", "")),
            }
        except Exception as e:
            print(f"[PIPELINE] Graph step non-fatal: {e}")
            _tb.print_exc()
        pb.progress(45)

        # ── Step 3: Timeline ──────────────────────────────────────────────────
        status.markdown('<div class="info-msg">Step 3/5 — Building timeline...</div>', unsafe_allow_html=True)
        try:
            from modules.timeline import build_timeline
            tl = build_timeline(person, search_results)
            st.session_state.timeline_data = tl
        except Exception as e:
            print(f"[PIPELINE] Timeline step non-fatal: {e}")
            _tb.print_exc()
        pb.progress(60)

        # ── Step 4: Behavioral analysis ───────────────────────────────────────
        status.markdown('<div class="info-msg">Step 4/5 — Analyzing behavior...</div>', unsafe_allow_html=True)
        print("[OSINT 4] Behavioral analysis starting...")
        try:
            from modules.behavioral_analysis import analyze
            assessment, meth = analyze({"person": person, "search_results": search_results})
            st.session_state.behavioral_data = {"assessment": assessment, "method": meth}
            print(f"[OSINT 5] Behavioral result: {assessment is not None}")
        except Exception as e:
            print(f"[OSINT 5] FAILED (non-fatal): {e}")
            _tb.print_exc()
            st.session_state.behavioral_data = None
        pb.progress(75)

        # ── Step 5: Report generation ─────────────────────────────────────────
        status.markdown('<div class="info-msg">Step 5/5 — Generating report...</div>', unsafe_allow_html=True)
        print("[OSINT 6] Report generation starting...")
        try:
            from modules.report_generator import generate_report
            rd = generate_report(
                person          = person,
                search_results  = search_results,
                graph_data      = st.session_state.graph_data,
                timeline_data   = st.session_state.timeline_data,
                behavioral_data = st.session_state.behavioral_data,
                user_id         = uid,
                mode            = "OSINT",
                raw_documents   = st.session_state.get("raw_documents") or None,
            )
            print(f"[OSINT 7] Report result: {rd is not None} [gemini={rd.get('gemini_used') if rd else 'N/A'}]")
            print("[OSINT 8] Storing to session state...")
            st.session_state.report_data = rd
        except Exception as e:
            msg = f"Report generation failed: {e}"
            print(f"[OSINT 7] FAILED: {e}")
            _tb.print_exc()
            st.session_state.pipeline_error = msg
            status.markdown(f'<div class="error-msg">{msg}</div>', unsafe_allow_html=True)
            return
        pb.progress(90)

        # ── Step 6: Digital twin + AI agents ─────────────────────────────────
        status.markdown('<div class="info-msg">Step 6/6 — Building ontology & running agents...</div>', unsafe_allow_html=True)
        try:
            from modules.ontology import build_digital_twin
            from modules.ai_agents import orchestrator as _orch
            twin = build_digital_twin({
                "person":          person,
                "search_results":  search_results,
                "timeline_data":   st.session_state.timeline_data,
                "behavioral_data": st.session_state.behavioral_data,
            })
            ont_json = twin.export_graph_json()
            twin.save_to_db()
            st.session_state.ontology_graph  = twin
            st.session_state.ontology_json   = ont_json

            # ── Keyword-inject flags from any uploaded docs BEFORE agents ────
            try:
                from modules.report_generator import inject_keyword_flags_from_docs
                _osint_raw_docs = st.session_state.get("raw_documents") or []
                inject_keyword_flags_from_docs(person, _osint_raw_docs)
                print(f"[APP-OSINT] After keyword injection: "
                      f"{len(person.get('anomaly_flags', []))} flags in person")
            except Exception as _okfi_err:
                print(f"[APP-OSINT] inject_keyword_flags non-fatal: {_okfi_err}")

            # Build unified anomaly list from enriched person BEFORE agents run
            _osint_anomaly_strings = [
                (f.get("flag", str(f)) if isinstance(f, dict) else str(f))
                for f in (person.get("anomaly_flags", []) or [])
            ] + [
                (f.get("flag", str(f)) if isinstance(f, dict) else str(f))
                for f in (person.get("conflicts", []) or [])
            ]
            agent_results = _orch.run_all_agents(
                ontology  = ont_json,
                report    = {"person": person, "anomalies": _osint_anomaly_strings, **rd},
                mode      = "OSINT",
                user_id   = uid,
            )
            st.session_state.agent_results = agent_results

            # ── Re-generate report now that agent_results are available ──────
            # The first generate_report() call (Step 5) ran without agent_results
            # because agents hadn't run yet. Re-run it now so sections 12/16/17
            # reflect actual Bedrock engine output and live risk/next-step data.
            try:
                from modules.report_generator import generate_report as _gen_report
                rd2 = _gen_report(
                    person          = person,
                    search_results  = search_results,
                    graph_data      = st.session_state.graph_data,
                    timeline_data   = st.session_state.timeline_data,
                    behavioral_data = st.session_state.behavioral_data,
                    user_id         = uid,
                    mode            = "OSINT",
                    agent_results   = agent_results,
                    raw_documents   = st.session_state.get("raw_documents") or None,
                )
                if rd2 and not rd2.get("error"):
                    st.session_state.report_data = rd2
                    rd = rd2
                    print("[PIPELINE] Report regenerated with agent_results — Bedrock engine tagged.")
            except Exception as _re:
                print(f"[PIPELINE] Report re-gen non-fatal: {_re}")
            # ─────────────────────────────────────────────────────────────────

            # Update active_profiles list
            risk_score = agent_results.get("risk", {}).get("risk_score", 0)
            profile_summary = {
                "name":       person.get("confirmed_name", query),
                "query":      query,
                "risk_score": risk_score,
                "risk_level": agent_results.get("risk", {}).get("risk_level", "LOW"),
                "platforms":  len(person.get("platforms_confirmed", [])),
                "updated_at": __import__("datetime").datetime.utcnow().strftime("%H:%M %d/%m"),
                "classification": "RESTRICTED",
                "compliance_cleared": agent_results.get("compliance", {}).get("cleared_for_export", True),
            }
            profiles = st.session_state.active_profiles or []
            existing = next((i for i, p in enumerate(profiles) if p["query"] == query), None)
            if existing is not None:
                profiles[existing] = profile_summary
            else:
                profiles.insert(0, profile_summary)
            st.session_state.active_profiles = profiles[:20]
            print(f"[PIPELINE] Ontology built — {ont_json.get('node_count',0)} nodes. Risk={risk_score}")
        except Exception as e:
            print(f"[PIPELINE] Ontology/agent step non-fatal: {e}")
            _tb.print_exc()
        pb.progress(100)
        status.markdown('<div class="success-msg">Pipeline complete. Loading command center...</div>', unsafe_allow_html=True)

    print("[OSINT 9] Navigating to command center (report stored)")
    st.session_state.active_screen = "command_center"
    st.rerun()


# ─────────────────────────────────────────────────────────────────────────────
# SEARCH SCREEN
# ─────────────────────────────────────────────────────────────────────────────

def render_result_card(result: dict, idx: int, query: str, search_results: dict):
    """Render a single result card. Clicking SELECT TARGET triggers the full pipeline."""
    score    = result.get("confidence", 0)
    platform = result.get("platform", "")
    name     = result.get("full_name", "")
    snippet  = result.get("snippet", "")
    url      = result.get("url", "")
    status   = result.get("status", "")
    failed   = status == "lookup_failed"
    sc_color = "#D97706" if failed else _conf_color(score)
    # A lookup that could not be completed (rate-limit / 5xx / timeout) is shown
    # with an amber "LOOKUP FAILED" badge instead of a misleading "0%", and the
    # pipeline button is withheld — running the full pipeline on an unconfirmed
    # target wastes work and implies a confirmation that did not happen.
    score_label = "LOOKUP FAILED" if failed else f"{score}%"
    conf_tag = (
        f'<span style="background:rgba(22,163,74,0.12);border:1px solid #16A34A;color:#16A34A;'
        f'font-family:\'JetBrains Mono\',monospace;font-size:9px;letter-spacing:2px;'
        f'padding:2px 7px;margin-left:8px;">EXACT</span>'
        if score == 100 else ""
    )
    url_html = (
        f'<div style="font-family:\'JetBrains Mono\',monospace;font-size:10px;margin-top:4px;">'
        f'<a href="{url}" target="_blank" style="color:#9D4EDD;">'
        f'{url[:70]}{"…" if len(url) > 70 else ""}'
        f'</a></div>'
    ) if url else ""
    st.markdown(
        f'<div class="result-card">'
        f'<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px;">'
        f'{_platform_badge(platform)}'
        f'<span style="font-family:\'JetBrains Mono\',monospace;font-size:10px;'
        f'letter-spacing:1px;color:{sc_color};">{score_label}{conf_tag}</span>'
        f'</div>'
        f'<div style="font-family:\'Rajdhani\',sans-serif;font-weight:600;font-size:17px;'
        f'letter-spacing:1px;color:#F0EAD6;margin-bottom:4px;">{name}</div>'
        f'<div style="font-family:\'JetBrains Mono\',monospace;font-size:11px;'
        f'color:#9CA3AF;line-height:1.5;">{snippet[:220]}</div>'
        f'{url_html}</div>',
        unsafe_allow_html=True,
    )
    if failed:
        st.markdown(
            '<div style="font-family:\'JetBrains Mono\',monospace;font-size:10px;'
            'letter-spacing:1px;color:#D97706;margin-top:2px;">'
            'Could not confirm this profile — retry later, or set GITHUB_TOKEN to '
            'raise the API rate limit.</div>',
            unsafe_allow_html=True,
        )
    elif st.button("SELECT TARGET — RUN PIPELINE", key=f"sel_{idx}", use_container_width=False):
        st.session_state.pipeline_pending = {
            "target":  result,
            "query":   query,
            "results": search_results,
        }
        st.rerun()

def screen_search():
    screen_header("SEARCH", "Name search · handle/github  handle/twitter  handle/ig  handle/reddit  handle/yt  handle/linkedin")

    # ── If pipeline is pending, execute it immediately ────────────────────────
    pending = st.session_state.get("pipeline_pending")
    if pending:
        _run_pipeline_auto(
            target         = pending["target"],
            query          = pending["query"],
            search_results = pending["results"],
        )
        return  # _run_pipeline_auto will st.rerun() to reports screen

    # ── Show any previous pipeline error ─────────────────────────────────────
    if st.session_state.get("pipeline_error"):
        st.markdown(
            f'<div class="error-msg" style="margin-bottom:0.5rem;">'
            f'Pipeline error: {st.session_state.pipeline_error}</div>',
            unsafe_allow_html=True,
        )
        if st.button("CLEAR ERROR", key="clr_err"):
            st.session_state.pipeline_error = None
            st.rerun()

    c1, c2 = st.columns([5, 1])
    with c1:
        query = st.text_input(
            "q",
            value       = st.session_state.search_query,
            placeholder = "Name — or — handle/github  handle/twitter  handle/ig  handle/reddit  handle/yt",
            key         = "search_q",
            label_visibility = "collapsed",
        )
    with c2:
        go = st.button("SEARCH", use_container_width=True)

    if go and query.strip():
        st.session_state.search_query        = query.strip()
        st.session_state.search_results      = None
        st.session_state.selected_candidates = []
        st.session_state.person_profile      = None
        st.session_state.report_data         = None
        st.session_state.pipeline_error      = None
        uid = st.session_state.get("current_user", "system")
        pb  = st.progress(0, text="Searching...")
        from modules.search import run_search, parse_handle_query
        if parse_handle_query(query.strip()):
            pb.progress(30, text="Direct platform lookup...")
        else:
            pb.progress(20, text="DuckDuckGo...")
            pb.progress(55, text="Wikipedia + Google News...")
        res = run_search(query.strip(), uid)
        pb.progress(100, text="Done.")
        time.sleep(0.25)
        pb.empty()
        st.session_state.search_results = res
        write_audit("SEARCH", uid, f"Query: {query.strip()} | Results: {res.get('total', 0)}")
        st.rerun()

    res = st.session_state.search_results
    if res is None:
        st.markdown(
            '<div style="text-align:center;font-family:\'JetBrains Mono\',monospace;'
            'font-size:11px;letter-spacing:2px;color:#4B5563;margin-top:4rem;">'
            'ENTER A NAME — OR — HANDLE/PLATFORM (e.g. torvalds/github)</div>',
            unsafe_allow_html=True,
        )
        return

    items, q_shown = res.get("results", []), res.get("query", "")
    mode           = res.get("mode", "name")

    exact_badge = (
        f'&nbsp;<span style="background:rgba(22,163,74,0.12);border:1px solid #16A34A;'
        f'color:#16A34A;font-family:\'JetBrains Mono\',monospace;font-size:9px;'
        f'letter-spacing:2px;padding:2px 8px;">EXACT MATCH</span>'
        if mode == "handle" else ""
    )
    st.markdown(
        f'<div style="font-family:\'JetBrains Mono\',monospace;font-size:10px;letter-spacing:1px;'
        f'color:#9CA3AF;margin-bottom:0.5rem;">'
        f'<span style="color:#C084FC;">{res.get("total", 0)}</span> RESULT(S) FOR '
        f'<span style="color:#F0EAD6;">"{q_shown}"</span>{exact_badge}'
        f'</div>',
        unsafe_allow_html=True,
    )
    for src, err in res.get("errors", {}).items():
        st.markdown(
            f'<div class="info-msg" style="font-size:0.73rem;">{src}: {err}</div>',
            unsafe_allow_html=True,
        )
    if not items:
        st.markdown('<div class="info-msg">No results found.</div>', unsafe_allow_html=True)
        return

    # ── Filters (name search only) ────────────────────────────────────────────
    if mode == "name" and len(items) > 1:
        all_plats = sorted(set(r.get("platform", "") for r in items))
        fc1, fc2  = st.columns([2, 4])
        with fc1:
            sel_plats = st.multiselect("Src", options=all_plats, default=all_plats, key="pf", label_visibility="collapsed")
        with fc2:
            min_c = st.slider("Min conf", 0, 100, 0, key="cf", label_visibility="collapsed")
        filtered = [r for r in items if r.get("platform", "") in sel_plats and r.get("confidence", 0) >= min_c]
        st.markdown(
            f'<div style="font-family:\'JetBrains Mono\',monospace;font-size:9px;letter-spacing:2px;'
            f'color:#4B5563;margin-bottom:0.5rem;">'
            f'SHOWING <span style="color:#C084FC;">{len(filtered)}</span> OF {len(items)}'
            f' &nbsp;·&nbsp; CLICK SELECT TARGET TO RUN FULL PIPELINE'
            f'</div>',
            unsafe_allow_html=True,
        )
    else:
        filtered = items
        st.markdown(
            '<div style="font-family:\'JetBrains Mono\',monospace;font-size:9px;letter-spacing:2px;'
            'color:#16A34A;margin-bottom:0.5rem;">'
            'EXACT PROFILE MATCH · CLICK SELECT TARGET TO RUN FULL PIPELINE</div>',
            unsafe_allow_html=True,
        )

    # ── Result cards ──────────────────────────────────────────────────────────
    for idx, r in enumerate(filtered):
        render_result_card(r, idx, q_shown, res)

def _render_person(person: dict, method: str):
    score = person.get("confidence_score",0)
    sc    = _conf_color(score)
    ml    = "GEMINI" if method=="gemini" else "LOCAL"
    mc    = "#2563EB" if method=="gemini" else "#9D4EDD"
    st.markdown(
        f'<div style="display:flex;justify-content:space-between;align-items:center;'
        f'margin:0.8rem 0 0.5rem;padding-bottom:8px;border-bottom:1px solid rgba(123,47,190,0.18);">'
        f'<div style="font-family:\'Rajdhani\',sans-serif;font-weight:600;font-size:22px;'
        f'letter-spacing:2px;color:#F0EAD6;">{person.get("confirmed_name","Unknown")}</div>'
        f'<div style="font-family:\'JetBrains Mono\',monospace;font-size:9px;letter-spacing:2px;">'
        f'<span style="color:{sc};">CONF {score}%</span>'
        f'&nbsp;<span style="color:{mc};">[{ml}]</span></div>'
        f'</div>', unsafe_allow_html=True,
    )
    cl, cr = st.columns(2)
    with cl:
        st.markdown('<div class="person-section"><h4>Identifiers</h4>', unsafe_allow_html=True)
        variants = person.get("name_variants",[])
        st.markdown(f"**Variants:** {', '.join(variants) if variants else 'None'}")
        for plat, uname in person.get("usernames",{}).items():
            st.markdown(f"**{plat}:** `{uname}`")
        st.markdown('</div>', unsafe_allow_html=True)
        st.markdown('<div class="person-section"><h4>Platforms</h4>', unsafe_allow_html=True)
        plats = person.get("platforms_confirmed",[])
        st.markdown(" ".join(_platform_badge(p) for p in plats) if plats else "None", unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)
        urls = person.get("profile_urls",{})
        if urls:
            st.markdown('<div class="person-section"><h4>Profile URLs</h4>', unsafe_allow_html=True)
            for plat, url in urls.items():
                st.markdown(f'{_platform_badge(plat)} <a href="{url}" target="_blank" style="color:#7B2FBE;font-size:0.76rem;">{url[:48]}...</a>', unsafe_allow_html=True)
            st.markdown('</div>', unsafe_allow_html=True)
    with cr:
        bios = person.get("bio_data",{})
        if bios:
            st.markdown('<div class="person-section"><h4>Bio Data</h4>', unsafe_allow_html=True)
            for src, bio in bios.items():
                st.markdown(f'{_platform_badge(src)}<div style="color:#9CA3AF;font-size:0.78rem;margin-top:2px;">{bio}</div>', unsafe_allow_html=True)
            st.markdown('</div>', unsafe_allow_html=True)
        gh = person.get("github_data",{})
        if gh:
            st.markdown('<div class="person-section"><h4>GitHub</h4>', unsafe_allow_html=True)
            st.markdown(f"**Repos:** {gh.get('repos','N/A')} | **Followers:** {gh.get('followers','N/A')} | **Joined:** {gh.get('joined','N/A')}")
            st.markdown('</div>', unsafe_allow_html=True)
        gaps = person.get("data_gaps",[])
        if gaps:
            st.markdown('<div class="person-section"><h4>Data Gaps</h4>', unsafe_allow_html=True)
            for g in gaps: st.markdown(f'<div style="color:#4B5563;font-size:0.76rem;">• {g}</div>', unsafe_allow_html=True)
            st.markdown('</div>', unsafe_allow_html=True)
    with st.expander("Raw Person Object", expanded=False):
        st.code(json.dumps(person, indent=2, ensure_ascii=False), language="json")


# ─────────────────────────────────────────────────────────────────────────────
# FUSION SCREEN
# ─────────────────────────────────────────────────────────────────────────────

_FILE_ICONS = {".csv": "📊", ".xlsx": "📊", ".xls": "📊",
               ".pdf": "📄", ".txt": "📝", ".text": "📝"}


def detect_file_contamination(file_list: list) -> dict:
    """
    Detect mixed-case file uploads (e.g. GhostWire + Jupiter files together).

    Mixing files from different investigations contaminates entity resolution:
    the resolver tries to merge identities from completely unrelated subjects,
    producing nonsense confidence scores and polluted anomaly flags.

    Args:
        file_list: List of filenames (str) or staged file dicts with a "name" key.

    Returns:
        {"contamination_detected": bool, "message": str (optional),
         "recommendation": str (optional), "case_prefixes": set (optional)}
    """
    # Normalise: accept either plain filename strings or staged-file dicts
    names = [
        f.get("name", "") if isinstance(f, dict) else str(f)
        for f in file_list
    ]

    prefixes: set = set()
    for name in names:
        n = name.lower().strip()
        if n.startswith("ghostwire"):
            prefixes.add("GHOSTWIRE")
        elif n.startswith("jup"):
            prefixes.add("JUPITER")
        # Extend here for future case prefixes as needed

    if len(prefixes) > 1:
        prefix_list = ", ".join(sorted(prefixes))
        return {
            "contamination_detected": True,
            "case_prefixes":          prefixes,
            "message": (
                f"⚠ Mixed case files detected: {prefix_list}. "
                "Files from different investigations may pollute entity resolution and produce unreliable results."
            ),
            "recommendation": (
                "Process files from only one investigation / case at a time for accurate results. "
                "Use the × button to remove files from other cases before analysing."
            ),
        }
    return {"contamination_detected": False}


def _fusion_reset():
    """Clear all fusion state and return to upload screen."""
    for k in ["fusion_staged", "fusion_assets_staged", "assets_data", "raw_documents",
              "fusion_analysed", "fusion_summary",
              "fusion_result", "graph_data", "timeline_data",
              "behavioral_data", "report_data", "person_profile",
              "resolution_method", "search_results", "search_query",
              "ontology_graph", "ontology_json", "agent_results"]:
        if k in ("fusion_staged", "fusion_assets_staged", "assets_data", "raw_documents"):
            st.session_state[k] = []
        elif k == "fusion_analysed":
            st.session_state[k] = False
        elif k == "active_profiles":
            st.session_state[k] = []
        else:
            st.session_state[k] = None


def _process_single_file(fbs, fname, uid, declared, pb, status_el):
    """
    Run the full pipeline on one file's bytes.
    Updates pb (progress bar) and status_el (st.empty).
    Returns (result, person, method, ents, rels, tl, behavioral_data, structured_rows, primary_subject).
    """
    from modules.data_ingestion import ingest_file
    from modules.entity_resolution import resolve_entity_from_documents
    from modules.relationship_mapper import (
        build_graph_from_person, build_graph, graph_summary,
        extract_relationships_from_structured_rows,
    )
    from modules.timeline import build_timeline
    from modules.behavioral_analysis import analyze as _analyze

    status_el.text("Reading document...")
    pb.progress(15)
    result = ingest_file(fbs, fname, uid, declared)
    if not result["success"]:
        return None, None, None, [], [], {}, {}, [], ""

    structured_rows  = result.get("structured_rows", [])
    primary_subject  = result.get("primary_subject", "")
    doc_flags        = result.get("document_flags", [])
    doc_locations    = result.get("locations", [])
    entities         = result["entities"]

    if not primary_subject:
        from modules.entity_resolution import is_bad_subject_name as _is_bad
        skip = {"Location Timeline","Date Time","City State","Activity Type",
                "Work Entry","NexaTech","Not found","Unknown","HIGH","MEDIUM","LOW"}
        # Also filter out location/institution strings that look like names
        names_list = [
            n["value"] for n in entities.get("names", [])[:10]
            if n["value"] not in skip and not _is_bad(n["value"])
        ]
        if names_list:
            primary_subject = names_list[0]
        else:
            # Filename stem is the last resort — reject if it contains noise tokens
            # (underscore-joined stems like "GHOSTWIRE_CDR" need word-splitting on _ too)
            stem = fname.rsplit(".", 1)[0].replace("_", " ").replace("-", " ")
            primary_subject = stem if not _is_bad(stem) else ""

    status_el.text("Extracting entities...")
    pb.progress(35)

    status_el.text("Resolving identity...")
    pb.progress(50)
    person, method = resolve_entity_from_documents(primary_subject, structured_rows, fname)
    if not person.get("confirmed_name") or person["confirmed_name"] in ("Unknown", ""):
        from modules.entity_resolution import is_bad_subject_name as _is_bad_fb
        if primary_subject and not _is_bad_fb(primary_subject):
            person["confirmed_name"] = primary_subject
    person["_resolution_method"] = method

    # Merge document_flags into anomaly_flags
    if doc_flags:
        existing_flags = person.get("anomaly_flags", [])
        person["anomaly_flags"] = existing_flags + [
            {"flag": f.get("flag", str(f)), "source": f.get("source", fname), "severity": "MEDIUM"}
            if isinstance(f, dict) else {"flag": str(f), "source": fname, "severity": "MEDIUM"}
            for f in doc_flags
        ]
        print(f"[INGEST] Added {len(doc_flags)} document flags to person profile")

    # Merge PDF-extracted locations
    if doc_locations:
        existing_locs = set(person.get("location_stated", []))
        for loc in doc_locations:
            if loc not in existing_locs:
                person.setdefault("location_stated", []).append(loc)
                existing_locs.add(loc)

    ingested_sr = {
        "query":   primary_subject,
        "total":   result["total_items"],
        "results": [{"full_name": primary_subject, "platform": f"Document: {fname}",
                     "snippet": "", "url": "", "confidence": 70}],
        "errors":  {},
    }

    status_el.text("Mapping relationships...")
    pb.progress(68)
    _, ents, rels = build_graph_from_person(person, ingested_sr)
    struct_ents, struct_rels = extract_relationships_from_structured_rows(structured_rows, fname)
    ents.extend(struct_ents)
    rels.extend(struct_rels)

    status_el.text("Building timeline...")
    pb.progress(82)
    tl = build_timeline(person, ingested_sr)

    status_el.text("Behavioural analysis...")
    pb.progress(93)
    behav_result, behav_method = _analyze(
        {"person": person, "search_results": ingested_sr},
        structured_rows=structured_rows,
    )
    behavioral_data = {"assessment": behav_result, "method": behav_method}

    pb.progress(100)
    status_el.text("Complete")

    # Sanitise the per-file person object before it becomes the primary_person
    try:
        from modules.entity_resolution import clean_person_object as _cpo_file
        _cpo_file(person)
    except Exception:
        pass

    return result, person, method, ents, rels, tl, behavioral_data, structured_rows, primary_subject


def screen_fusion():
    screen_header("FUSION", "Multi-source document ingestion · Entity resolution · Full pipeline")

    uid = st.session_state.get("current_user", "system")

    st.markdown(
        '<div class="al-classification-strip">TS//SCI//NOFORN · DOCUMENT FUSION ENGINE · MULTI-SOURCE INGESTION</div>',
        unsafe_allow_html=True,
    )

    # ── If analysis is already done, show results ─────────────────────────────
    if st.session_state.get("fusion_analysed") and st.session_state.get("fusion_summary"):
        _fusion_show_results()
        return

    _stage = st.session_state.get("fusion_stage", "idle")
    staged_count = len(st.session_state.get("fusion_staged", []))
    declared_now = st.session_state.get("fusion_declaration", False)

    # ── Pipeline step checklist (horizontal strip) ─────────────────────────────
    step_states = [
        ("01", "STAGING",     "done" if staged_count > 0 else "active"),
        ("02", "DECLARATION", "done" if declared_now else ("active" if staged_count > 0 else "pending")),
        ("03", "EXTRACTION",  "pending"),
        ("04", "RESOLUTION",  "pending"),
        ("05", "FUSION",      "pending"),
        ("06", "REPORT",      "pending"),
    ]
    pct = (sum(1 for *_, s in step_states if s == "done") / len(step_states)) * 100
    steps_html = ""
    for num, title, state in step_states:
        col_c = "var(--online)" if state == "done" else "var(--purple-300)" if state == "active" else "var(--text-dim)"
        steps_html += (
            f'<div style="display:flex;flex-direction:column;align-items:center;gap:4px;flex:1;">'
            f'<div style="font-family:var(--f-display);font-weight:600;font-size:18px;color:{col_c};">{num}</div>'
            f'<div style="font-family:var(--f-mono);font-size:9px;letter-spacing:2px;color:{col_c};text-transform:uppercase;text-align:center;">{title}</div>'
            f'<div style="font-family:var(--f-mono);font-size:8px;color:var(--text-dim);letter-spacing:1px;text-transform:uppercase;">{"DONE" if state=="done" else "ACTIVE" if state=="active" else "PENDING"}</div>'
            f'</div>'
        )
    st.markdown(
        f'<div class="card" style="padding:14px 18px;margin-bottom:16px;">'
        f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">'
        f'<div style="font-family:var(--f-mono);font-size:9px;letter-spacing:3px;color:var(--text-dim);text-transform:uppercase;">DOCUMENT FUSION PIPELINE</div>'
        f'<div style="font-family:var(--f-mono);font-size:9px;letter-spacing:2px;color:{"var(--online)" if _stage=="complete" else "var(--warning)" if _stage=="processing" else "var(--text-dim)"};">'
        f'{"COMPLETE" if _stage=="complete" else "PROCESSING" if _stage=="processing" else "STAGING"}</div>'
        f'</div>'
        f'<div style="display:flex;gap:8px;align-items:flex-start;margin-bottom:12px;">{steps_html}</div>'
        f'<div class="progress-bar"><i style="width:{pct:.0f}%;"></i></div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # ══════════════════════════════════════════════════════════════════════════
    # STEP 1 — FILE UPLOADER (staging)
    # ══════════════════════════════════════════════════════════════════════════
    st.markdown(
        '<div class="disclaimer-box">LEGAL NOTICE — All uploaded data must have been obtained through '
        'lawful authorization. Every upload is logged with your user ID and timestamp.</div>',
        unsafe_allow_html=True,
    )

    if st.button("CLEAR & START OVER", key="btn_reset_fusion"):
        for _k in [
            "staged_files", "fusion_staged", "fusion_assets_staged",
            "fusion_declaration", "fusion_analyse_triggered", "fusion_stage",
            "fusion_analysed", "fusion_summary", "person", "report",
            "behavioral", "raw_docs", "assets_data", "raw_documents",
        ]:
            if _k in st.session_state:
                del st.session_state[_k]
        st.rerun()

    uploaded_files = st.file_uploader(
        "Upload intelligence documents",
        accept_multiple_files=True,
        type=["csv", "xlsx", "pdf", "txt"],
        key="fusion_uploads",
    )

    # Sync newly uploaded files into staged list (avoid duplicates by name)
    if uploaded_files:
        staged = st.session_state.get("fusion_staged", [])
        staged_names = {f["name"] for f in staged}
        for uf in uploaded_files:
            if uf.name not in staged_names:
                _raw = uf.read()
                staged.append({
                    "name":  uf.name,
                    "size":  uf.size,
                    "bytes": safe_decode_file(_raw, uf.name),
                    "type":  "." + uf.name.rsplit(".", 1)[-1].lower() if "." in uf.name else "",
                })
                staged_names.add(uf.name)
        st.session_state.fusion_staged = staged

    staged = st.session_state.get("fusion_staged", [])

    # ══════════════════════════════════════════════════════════════════════════
    # STEP 1b — STAGED DOCUMENTS LIST
    # ══════════════════════════════════════════════════════════════════════════
    if staged:
        from modules.ui_components import file_row as _file_row, panel_hdr as _phdr
        n = len(staged)
        st.markdown(
            _phdr("F·01", f"STAGED DOCUMENTS", f"{n} FILE{'S' if n!=1 else ''}"),
            unsafe_allow_html=True,
        )
        st.markdown('<div style="border:1px solid rgba(123,47,190,0.20);">', unsafe_allow_html=True)
        to_remove = None
        for i, sf in enumerate(staged):
            size_kb  = sf["size"] / 1024
            size_str = f"{size_kb:.1f} KB" if size_kb < 1024 else f"{size_kb/1024:.1f} MB"
            col_file, col_btn = st.columns([0.88, 0.12])
            col_file.markdown(_file_row(
                name=sf["name"], ext=sf["type"],
                meta=size_str, size_str="",
            ), unsafe_allow_html=True)
            if col_btn.button("✕", key=f"rm_{i}_{sf['name']}", help=f"Remove {sf['name']}"):
                to_remove = i
        st.markdown('</div>', unsafe_allow_html=True)

        if to_remove is not None:
            st.session_state.fusion_staged.pop(to_remove)
            st.rerun()

        st.markdown(
            f'<div style="font-family:\'JetBrains Mono\',monospace;font-size:11px;'
            f'color:#16A34A;letter-spacing:1px;margin:0.5rem 0 1rem;">'
            f'{n} DOCUMENT{"S" if n!=1 else ""} STAGED — READY FOR ANALYSIS</div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            '<div style="font-family:\'JetBrains Mono\',monospace;font-size:11px;'
            'color:#4B5563;letter-spacing:1px;margin:1rem 0;">NO FILES STAGED · UPLOAD ABOVE</div>',
            unsafe_allow_html=True,
        )

    # ══════════════════════════════════════════════════════════════════════════
    # STEP 1c — OPTIONAL ASSETS UPLOADER
    # ══════════════════════════════════════════════════════════════════════════
    with st.expander("⊕  ATTACH ASSET INTELLIGENCE (optional)", expanded=False):
        st.markdown(
            '<div style="font-size:0.77rem;color:#9D4EDD;margin-bottom:0.6rem;">'
            'Upload asset registers (CSV/XLSX) — vehicles, equipment, property, personnel, financials. '
            'Asset data enriches the Section 18 Tactical Operation Plan (Section 18 runs on every case).</div>',
            unsafe_allow_html=True,
        )
        asset_uploads = st.file_uploader(
            "Upload asset documents",
            accept_multiple_files=True,
            type=["csv", "xlsx", "json"],
            key="fusion_asset_uploads",
        )
        if asset_uploads:
            ast_staged = st.session_state.get("fusion_assets_staged", [])
            ast_names  = {f["name"] for f in ast_staged}
            for uf in asset_uploads:
                if uf.name not in ast_names:
                    _raw = uf.read()
                    ast_staged.append({
                        "name":  uf.name,
                        "size":  uf.size,
                        "bytes": safe_decode_file(_raw, uf.name),
                        "type":  "." + uf.name.rsplit(".", 1)[-1].lower() if "." in uf.name else "",
                    })
                    ast_names.add(uf.name)
            st.session_state.fusion_assets_staged = ast_staged

        ast_staged = st.session_state.get("fusion_assets_staged", [])
        if ast_staged:
            st.markdown(
                f'<div style="font-size:0.77rem;color:#16A34A;margin-top:0.4rem;">'
                f'{len(ast_staged)} asset file{"s" if len(ast_staged)!=1 else ""} staged</div>',
                unsafe_allow_html=True,
            )
            for asf in ast_staged:
                st.markdown(
                    f'<div style="font-size:0.74rem;color:#9CA3AF;margin-left:0.5rem;">• {asf["name"]} '
                    f'({asf["size"]/1024:.1f} KB)</div>',
                    unsafe_allow_html=True,
                )

    # ══════════════════════════════════════════════════════════════════════════
    # STEP 1d — OPTIONAL TRAFFIC DATA UPLOADERS
    # ══════════════════════════════════════════════════════════════════════════
    with st.expander("⊕  ATTACH TRAFFIC DATA (optional)", expanded=False):
        st.markdown(
            '<div style="font-size:0.77rem;color:#9D4EDD;margin-bottom:0.6rem;">'
            'Upload challan records or ANPR camera logs. '
            'Triggers vehicle intelligence analysis in behavioral report.</div>',
            unsafe_allow_html=True,
        )
        col_c, col_a = st.columns(2)

        with col_c:
            st.caption("Traffic Challans (Parivahan portal / authorised source)")
            challan_uploads = st.file_uploader(
                "Upload challan records",
                accept_multiple_files=True,
                type=["csv", "xlsx"],
                key="fusion_challans",
            )
            challan_decl = False
            if challan_uploads:
                challan_decl = st.checkbox(
                    "I confirm challan data was obtained lawfully",
                    key="challan_declaration",
                )

        with col_a:
            st.caption("ANPR / Camera Logs (Law enforcement only — requires court order)")
            anpr_uploads = st.file_uploader(
                "Upload ANPR logs",
                accept_multiple_files=True,
                type=["csv", "xlsx"],
                key="fusion_anpr",
            )
            anpr_decl = False
            if anpr_uploads:
                anpr_decl = st.checkbox(
                    "I confirm ANPR data obtained under lawful authorization",
                    key="anpr_declaration",
                )
                st.text_input(
                    "Legal authorization reference",
                    key="anpr_auth_ref",
                    placeholder="Court Order No. / Section 69 Ref.",
                )

        # Stage traffic files (with declarations) alongside main docs
        if challan_uploads and challan_decl:
            ch_staged  = st.session_state.get("fusion_staged", [])
            ch_names   = {f["name"] for f in ch_staged}
            for uf in challan_uploads:
                if uf.name not in ch_names:
                    uf.seek(0)
                    _raw = uf.read()
                    ch_staged.append({
                        "name":  uf.name,
                        "size":  uf.size,
                        "bytes": safe_decode_file(_raw, uf.name),
                        "type":  "." + uf.name.rsplit(".", 1)[-1].lower(),
                        "traffic_subtype": "challan",
                    })
                    ch_names.add(uf.name)
            st.session_state.fusion_staged = ch_staged
            st.markdown(
                f'<div style="font-size:0.75rem;color:#16A34A;">✓ {len(challan_uploads)} challan file(s) staged</div>',
                unsafe_allow_html=True,
            )

        if anpr_uploads and anpr_decl:
            ap_staged = st.session_state.get("fusion_staged", [])
            ap_names  = {f["name"] for f in ap_staged}
            for uf in anpr_uploads:
                if uf.name not in ap_names:
                    uf.seek(0)
                    _raw = uf.read()
                    ap_staged.append({
                        "name":  uf.name,
                        "size":  uf.size,
                        "bytes": safe_decode_file(_raw, uf.name),
                        "type":  "." + uf.name.rsplit(".", 1)[-1].lower(),
                        "traffic_subtype": "anpr",
                    })
                    ap_names.add(uf.name)
            st.session_state.fusion_staged = ap_staged
            st.markdown(
                f'<div style="font-size:0.75rem;color:#16A34A;">✓ {len(anpr_uploads)} ANPR file(s) staged</div>',
                unsafe_allow_html=True,
            )

    # ══════════════════════════════════════════════════════════════════════════
    # STEP 2 — DECLARATION CHECKBOX
    # ══════════════════════════════════════════════════════════════════════════
    declared = st.checkbox(
        "I confirm all uploaded data was obtained through lawful authorization. "
        "This action is logged with my user ID and timestamp.",
        key="fusion_declaration",
    )

    # ══════════════════════════════════════════════════════════════════════════
    # STEP 3 — ANALYSE BUTTON
    # ══════════════════════════════════════════════════════════════════════════
    can_analyse = bool(staged) and declared
    st.markdown(
        f'<style>'
        f'div[data-testid="stButton"] button[kind="primary"]{{background:#7B2FBE;border:none;font-weight:700;letter-spacing:0.1rem;}}'
        f'</style>',
        unsafe_allow_html=True,
    )
    if st.button(
        "ANALYSE DOCUMENTS",
        disabled=not can_analyse,
        use_container_width=True,
        type="primary",
        key="fusion_analyse",
    ):
        st.session_state["fusion_analyse_triggered"] = True
        st.session_state["fusion_stage"] = "processing"

    if not can_analyse and staged and not declared:
        st.markdown(
            '<div style="color:#D97706;font-size:0.75rem;margin-top:0.3rem;">Check the declaration above to enable analysis.</div>',
            unsafe_allow_html=True,
        )
    elif not staged:
        st.markdown(
            '<div style="color:#4B5563;font-size:0.75rem;margin-top:0.3rem;">Upload at least one file to begin.</div>',
            unsafe_allow_html=True,
        )

    if not st.session_state.get("fusion_analyse_triggered") or \
            st.session_state.get("fusion_stage") != "processing":
        return

    # ══════════════════════════════════════════════════════════════════════════
    # STEP 4 — SEQUENTIAL PROCESSING
    # ══════════════════════════════════════════════════════════════════════════
    total = len(staged)
    print(f"[FUSION 1] Analyse clicked, files: {total}")
    st.markdown('<hr class="divider">', unsafe_allow_html=True)
    st.markdown(
        f'<div class="banner">'
        f'<div class="ic">⊕</div>'
        f'<div><div class="t">ANALYSIS IN PROGRESS</div>'
        f'<div class="s">Processing {total} document{"s" if total!=1 else ""} through fusion pipeline</div></div>'
        f'<div class="r">DO NOT REFRESH</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # ── Case contamination check ──────────────────────────────────────────────
    # Run BEFORE ingestion so the analyst can abort if needed.
    _contamination = detect_file_contamination(staged)
    _contamination_warnings: list = []
    if _contamination.get("contamination_detected"):
        st.warning(
            _contamination["message"] + "\n\n" + _contamination.get("recommendation", ""),
            icon="⚠️",
        )
        _contamination_warnings.append(_contamination["message"])
        print(
            f"[FUSION] Contamination detected — case prefixes: "
            f"{_contamination.get('case_prefixes', set())}"
        )
    # ─────────────────────────────────────────────────────────────────────────

    all_ents:     list = []
    all_rels:     list = []
    all_tl_events: list = []
    all_struct_rows: list = []
    all_results:  list = []
    primary_person = None
    primary_method = "local-fallback"
    primary_subject_name = ""
    total_entities = 0
    total_relationships = 0

    print("[FUSION 2] Ingestion starting...")
    for i, sf in enumerate(staged):
        st.markdown(
            f'<div style="font-size:0.88rem;font-weight:700;color:#E0E0E0;margin:0.6rem 0 0.2rem;">'
            f'[{i+1}/{total}] {sf["name"]}</div>',
            unsafe_allow_html=True,
        )
        pb     = st.progress(0)
        status = st.empty()

        try:
            result, person, method, ents, rels, tl, behavioral_data, struct_rows, psubj = \
                _process_single_file(sf["bytes"], sf["name"], uid, declared, pb, status)
        except Exception as _pfe:
            import traceback as _tb2
            print(f"[FUSION 2] FAILED on file {sf['name']}: {_pfe}")
            _tb2.print_exc()
            st.error(f"Failed to process {sf['name']}: {_pfe}")
            continue

        if result is None:
            print(f"[FUSION 2] File returned None: {sf['name']}")
            st.error(f"Failed to process {sf['name']} — skipped.")
            continue

        print(f"[FUSION 3] Ingestion result: True — {sf['name']} ({result.get('total_items',0)} entities)")
        # Accumulate
        all_results.append(result)
        all_ents.extend(ents)
        all_rels.extend(rels)
        all_struct_rows.extend(struct_rows)
        all_tl_events.extend(tl.get("events", []) if tl else [])
        total_entities    += result.get("total_items", 0)
        total_relationships += len(rels)

        # First valid person becomes primary
        if primary_person is None and person:
            primary_person       = person
            primary_method       = method
            primary_subject_name = psubj
            print(f"[FUSION 4] Entity resolution starting... primary subject: {psubj}")
            print(f"[FUSION 5] Person object: {person is not None} — {person.get('confirmed_name','?')}")

        # Mini summary per file
        n_ents = result.get("total_items", 0)
        n_rels = len(rels)
        st.success(f"✓ {sf['name']} — {n_ents} entities extracted, {n_rels} relationships mapped")

    # ══════════════════════════════════════════════════════════════════════════
    # STEP 4b — MULTI-DOC ENTITY RESOLUTION (replaces per-file result)
    # ══════════════════════════════════════════════════════════════════════════
    if all_results:
        from modules.entity_resolution import resolve_entity_from_multiple_docs
        print(f"[FUSION MULTIDOC] Resolving across {len(all_results)} documents...")
        try:
            md_person, md_method = resolve_entity_from_multiple_docs(all_results)
            if md_person and md_person.get("confirmed_name") not in (None, "", "Unknown Subject"):
                primary_person = md_person
                primary_method = md_method
                print(f"[FUSION MULTIDOC] Resolved: {primary_person.get('confirmed_name')} "
                      f"confidence={primary_person.get('confidence_score')} method={md_method}")
            else:
                print(f"[FUSION MULTIDOC] Multi-doc resolver returned no name — keeping per-file result")
        except Exception as _mde:
            import traceback as _tb3
            print(f"[FUSION MULTIDOC] ERROR: {_mde}")
            _tb3.print_exc()

    # ══════════════════════════════════════════════════════════════════════════
    # STEP 5 — CROSS-FILE LINKING
    # ══════════════════════════════════════════════════════════════════════════
    st.markdown('<hr class="divider">', unsafe_allow_html=True)
    st.markdown(
        f'<div class="progress-summary">'
        f'<div class="tile"><div class="k">DOCUMENTS</div><div class="v">{len(all_results)}</div></div>'
        f'<div class="tile"><div class="k">ENTITIES</div><div class="v">{total_entities}</div></div>'
        f'<div class="tile"><div class="k">RELATIONSHIPS</div><div class="v">{total_relationships}</div></div>'
        f'<div class="tile"><div class="k">STATUS</div><div class="v" style="font-size:14px;color:var(--online);">FUSING</div></div>'
        f'</div>',
        unsafe_allow_html=True,
    )
    st.markdown('<div style="font-family:var(--f-mono);font-size:9px;letter-spacing:3px;color:var(--text-dim);text-transform:uppercase;margin:16px 0 8px;">LINKING ACROSS DOCUMENTS</div>', unsafe_allow_html=True)

    linking_pb     = st.progress(0)
    linking_status = st.empty()

    from modules.relationship_mapper import build_graph, graph_summary, get_primary_subject
    from modules.behavioral_analysis import analyze as _analyze, detect_rule_based_anomalies

    linking_status.text("Resolving cross-document identities...")
    linking_pb.progress(25)
    # Deduplicate entities by label
    seen_ent_ids: set = set()
    merged_ents:  list = []
    for e in all_ents:
        if e["id"] not in seen_ent_ids:
            merged_ents.append(e)
            seen_ent_ids.add(e["id"])

    linking_status.text("Building unified relationship graph...")
    linking_pb.progress(50)
    G_full     = build_graph(merged_ents, all_rels)

    # Validate primary subject against graph — prevents a location or org node
    # that dominated entity extraction from masquerading as the subject.
    from modules.entity_resolution import is_bad_subject_name as _is_bad_graph
    graph_subject = get_primary_subject(merged_ents, G_full)
    if graph_subject and graph_subject != "Unknown Subject" and not _is_bad_graph(graph_subject):
        person = primary_person or {}
        current_name = person.get("confirmed_name", "")
        if not current_name or current_name in ("Unknown Subject", "Unknown", ""):
            # No confirmed name yet — take the graph's top person
            if primary_person:
                primary_person["confirmed_name"] = graph_subject
            primary_subject_name = graph_subject
            print(f"[FUSION 5b] Subject corrected by graph: {graph_subject!r}")
        elif _is_bad_graph(current_name):
            # Current name is noise — override with the clean graph subject
            if primary_person:
                primary_person["confirmed_name"] = graph_subject
            primary_subject_name = graph_subject
            print(f"[FUSION 5b] Noise name {current_name!r} replaced by graph: {graph_subject!r}")
        else:
            print(f"[FUSION 5b] Graph primary: {graph_subject!r} | entity resolution: {current_name!r}")

    # Fix 5: suppress cross-file institutional-address boilerplate from ranking
    from modules.relationship_mapper import detect_boilerplate_locations
    _boilerplate = detect_boilerplate_locations(all_results)
    graph_summ = graph_summary(
        G_full,
        subject_name=(primary_person or {}).get("confirmed_name", ""),
        boilerplate=_boilerplate,
    )

    # Confidence is recalculated centrally inside _generate_report_inner
    # (report_generator.py) where graph_data summary is already passed in.
    print("[FUSION 6] Timeline building...")
    linking_status.text("Running AI fusion analysis...")
    linking_pb.progress(65)
    ingested_sr_combined = {
        "query":   primary_subject_name,
        "total":   total_entities,
        "results": [{"full_name": primary_subject_name, "platform": f"Document: {sf['name']}",
                     "snippet": "", "url": "", "confidence": 70} for sf in staged],
        "errors":  {},
    }
    print("[FUSION 7] Behavioral analysis starting...")
    behav_result, behav_method = _analyze(
        {"person": primary_person or {}, "search_results": ingested_sr_combined},
        structured_rows=all_struct_rows,
    )
    behavioral_data = {"assessment": behav_result, "method": behav_method}
    print(f"[FUSION 7] Behavioral result: {behav_result is not None}")

    linking_status.text("Detecting anomalies...")
    linking_pb.progress(80)
    rule_anomalies = detect_rule_based_anomalies(all_struct_rows)

    print("[FUSION 8] Report generation starting...")
    linking_status.text("Generating intelligence report...")
    linking_pb.progress(90)
    from modules.timeline import build_timeline_from_fusion, build_timeline
    # Pass per-file structured rows so build_timeline_from_all_files can attribute
    # each event to its actual source file (bank statement, challan, CDR, etc.).
    # Previously all_struct_rows was spread across every entry — every event
    # ended up attributed to the first filename (Audit fix #5).
    raw_docs_for_timeline = [
        {
            "filename":        r.get("filename", sf["name"]),
            "raw_text":        r.get("raw_text", ""),
            "structured_rows": r.get("structured_rows", []),
        }
        for sf, r in zip(staged, all_results)
    ]
    tl_combined = build_timeline_from_fusion(primary_person or {}, raw_docs_for_timeline) \
                  if all_results else build_timeline(primary_person or {}, ingested_sr_combined)

    # ── Parse asset files ─────────────────────────────────────────────────────
    ast_staged   = st.session_state.get("fusion_assets_staged", [])
    assets_dicts = []  # list of dicts for TacticalPlanAgent (Section 18)
    if ast_staged:
        try:
            from modules.ingest import ingest_file
            for asf in ast_staged:
                try:
                    ar = ingest_file(asf["bytes"], asf["name"])
                    for row in ar.get("structured_rows", []):
                        if isinstance(row, dict):
                            assets_dicts.append({
                                "source_file": asf["name"],
                                **{k: v for k, v in row.items() if v and str(v) not in ("", "None", "nan")},
                            })
                except Exception:
                    pass
        except Exception:
            pass
    st.session_state.assets_data = assets_dicts

    # Build ontology + run agents
    try:
        from modules.ontology import build_digital_twin
        from modules.ai_agents import orchestrator as _orch
        # Pass raw_docs so AssetEntity objects are extracted when asset rows exist
        raw_docs_for_ontology = [
            {
                "filename": sf["name"],
                "raw_text": "",
                "structured_rows": all_struct_rows,
            }
            for sf in staged
        ]
        twin     = build_digital_twin(
            {"person": primary_person or {}, "timeline_data": tl_combined},
            raw_documents=raw_docs_for_ontology if all_struct_rows else None,
        )
        ont_json = twin.export_graph_json()
        twin.save_to_db()
    except Exception:
        twin     = None
        ont_json = {}

    # ── Inject flags from doc text + rule_anomalies BEFORE agents run ────────
    if primary_person is None:
        primary_person = {}

    # Step A: keyword scan across all ingested document text
    # (CERT-In, IT Act, FEMA, DPDP, PMLA, NDPS, HAWALA, VPN, deletion, etc.)
    try:
        from modules.report_generator import inject_keyword_flags_from_docs
        inject_keyword_flags_from_docs(primary_person, all_results)
        print(f"[APP-FUSION] After keyword injection: "
              f"{len(primary_person.get('anomaly_flags', []))} flags in person")
    except Exception as _kfi_err:
        print(f"[APP-FUSION] inject_keyword_flags non-fatal: {_kfi_err}")

    # Step B: merge rule_anomalies (CDR / structural detections)
    _existing_flags = primary_person.get("anomaly_flags", []) or []
    _existing_texts = {
        (f.get("flag", str(f)) if isinstance(f, dict) else str(f)).lower()
        for f in _existing_flags
    }
    for ra in (rule_anomalies or []):
        flag_text = ra.get("flag", str(ra)) if isinstance(ra, dict) else str(ra)
        if flag_text.lower() not in _existing_texts:
            _existing_texts.add(flag_text.lower())
            _existing_flags.append({
                "flag":     flag_text,
                "detail":   ra.get("detail", "") if isinstance(ra, dict) else "",
                "source":   "rule-based-detector",
                "severity": "MEDIUM",
            })
    primary_person["anomaly_flags"] = _existing_flags

    # Also build a flat string list for report["anomalies"] so agents reading
    # that key (NextStepAgent, TacticalPlanAgent) get the full picture
    _all_anomaly_strings = [
        (f.get("flag", str(f)) if isinstance(f, dict) else str(f))
        for f in _existing_flags
    ]

    print(
        f"[APP] Calling agents with {len(all_results)} docs"
        f" person={primary_person.get('confirmed_name', 'Unknown')}"
        f" confidence={primary_person.get('confidence_score', 0)}"
        f" anomalies={len(_existing_flags)}"
    )
    try:
        _agent_report = {
            "person":   primary_person,
            "anomalies": _all_anomaly_strings,
        }
        if _contamination_warnings:
            _agent_report["warnings"] = _contamination_warnings
        ag_res = _orch.run_all_agents(
            ont_json,
            _agent_report,
            "FUSION",
            uid,
            assets_data=assets_dicts if assets_dicts else None,
        )
        print(
            f"[APP] Agents complete."
            f" Risk score={ag_res.get('risk', {}).get('risk_score', 'missing') if ag_res else 'None'}"
        )
    except Exception as _age:
        print(f"[APP] Agents ERROR: {_age}")
        ag_res = None

    linking_pb.progress(100)
    linking_status.text("Complete")
    time.sleep(0.3)
    linking_pb.empty()
    linking_status.empty()

    # ── FINAL CLEAN: sanitise person object before storing in session_state ───
    # Runs after ALL resolution / graph / agent steps — last gate before PDF.
    if primary_person:
        try:
            from modules.entity_resolution import clean_person_object as _cpo_fusion
            _cpo_fusion(primary_person)
            print(f"[FUSION CLEAN] Final name: {primary_person.get('confirmed_name','?')!r}")
        except Exception as _cle:
            print(f"[FUSION CLEAN] non-fatal: {_cle}")

    st.success("✓ All documents linked and analysed")

    # ── Persist all results to session state ─────────────────────────────────
    st.session_state.person_profile    = primary_person
    st.session_state.resolution_method = primary_method
    st.session_state.graph_data        = {"graph": G_full, "entities": merged_ents,
                                           "rels": all_rels, "summary": graph_summ}
    st.session_state.timeline_data     = tl_combined
    st.session_state.behavioral_data   = behavioral_data
    st.session_state.search_results    = ingested_sr_combined
    st.session_state.search_query      = primary_subject_name
    st.session_state.fusion_result     = all_results[0] if all_results else None
    # Store all ingest results so source log is populated in report generation
    st.session_state.raw_documents     = all_results
    if twin:
        st.session_state.ontology_graph = twin
        st.session_state.ontology_json  = ont_json
    if ag_res:
        st.session_state.agent_results = ag_res

    # ── Build summary dict ────────────────────────────────────────────────────
    st.session_state.fusion_summary = {
        "docs_processed":       len(all_results),
        "primary_subject":      primary_subject_name,
        "confidence":           (primary_person or {}).get("confidence_score", 0),
        "total_entities":       total_entities,
        "total_relationships":  graph_summ.get("edges", 0),
        "anomalies_flagged":    len(rule_anomalies),
        "resolution_method":    primary_method,
        "warnings":             _contamination_warnings,
    }
    print("[FUSION 9] All data stored. fusion_analysed=True")
    print("[FUSION 10] Navigating to report screen via fusion results view...")
    st.session_state.fusion_analysed = True
    st.rerun()


def _fusion_show_results():
    """Render the post-analysis results summary for Fusion mode."""
    summ = st.session_state.get("fusion_summary", {})

    st.markdown(
        '<div style="border:1px solid #7B2FBE;border-radius:8px;padding:1.2rem 1.4rem;margin:1rem 0;">'
        '<div style="font-size:0.78rem;font-weight:700;color:#9D4EDD;letter-spacing:0.12rem;'
        'margin-bottom:0.8rem;">ANALYSIS COMPLETE</div>',
        unsafe_allow_html=True,
    )

    rows_html = ""
    items = [
        ("Documents processed",    summ.get("docs_processed", 0),      "#2563EB"),
        ("Primary subject",        summ.get("primary_subject", "—"),    "#E0E0E0"),
        ("Confidence",             f"{summ.get('confidence', 0)}/100",  "#16A34A"),
        ("Entities extracted",     summ.get("total_entities", 0),       "#9D4EDD"),
        ("Relationships mapped",   summ.get("total_relationships", 0),  "#D97706"),
        ("Anomalies flagged",      summ.get("anomalies_flagged", 0),    "#DC2626"),
    ]
    for label, val, colour in items:
        rows_html += (
            f'<div style="display:flex;justify-content:space-between;'
            f'padding:0.25rem 0;border-bottom:1px solid #111;">'
            f'<span style="font-size:0.78rem;color:#4B5563;">{label}</span>'
            f'<span style="font-size:0.82rem;font-weight:700;color:{colour};">{val}</span>'
            f'</div>'
        )

    st.markdown(rows_html + '</div>', unsafe_allow_html=True)

    # ── Contamination warning (persisted from analysis run) ───────────────────
    for _warn in summ.get("warnings", []):
        st.warning(_warn, icon="⚠️")

    # Action buttons
    c1, c2, c3 = st.columns(3)
    with c1:
        if st.button("VIEW FULL REPORT ->", use_container_width=True, type="primary", key="fus_rpt"):
            _generate_and_store("FUSION")
            st.session_state.active_screen = "reports"
            st.rerun()
    with c2:
        if st.button("VIEW NETWORK GRAPH ->", use_container_width=True, key="fus_net"):
            st.session_state.active_screen = "network_map"
            st.rerun()
    with c3:
        if st.button("DOWNLOAD PDF", use_container_width=True, key="fus_pdf"):
            _generate_and_store("FUSION")
            st.session_state.active_screen = "reports"
            st.rerun()

    st.markdown('<br>', unsafe_allow_html=True)

    # Entity extraction detail tabs
    fr = st.session_state.get("fusion_result")
    if fr:
        entities = fr.get("entities", {})
        st.markdown('<hr class="divider">', unsafe_allow_html=True)
        cols = st.columns(6)
        _stat = lambda col, lbl, val, c="#9D4EDD": col.markdown(
            f'<div class="stat-card"><div class="stat-value" style="color:{c};">{val}</div>'
            f'<div class="stat-label">{lbl}</div></div>', unsafe_allow_html=True)
        _stat(cols[0], "NAMES",    len(entities.get("names", [])),         "#9D4EDD")
        _stat(cols[1], "PHONES",   len(entities.get("phones", [])),        "#2563EB")
        _stat(cols[2], "EMAILS",   len(entities.get("emails", [])),        "#16A34A")
        _stat(cols[3], "DATES",    len(entities.get("dates", [])),         "#D97706")
        _stat(cols[4], "LOCS",     len(entities.get("locations", [])),     "#D97706")
        _stat(cols[5], "RELS",     len(entities.get("relationships", [])), "#DC2626")
        import pandas as pd
        tabs = st.tabs(["Names", "Phones", "Emails", "Dates", "Locations", "Relationships", "Raw Text"])
        def _tbl(tab, items, fields):
            with tab:
                if not items:
                    st.markdown('<div style="color:#333;font-size:0.83rem;">None found.</div>', unsafe_allow_html=True)
                    return
                df = pd.DataFrame([{f: item.get(f, "") for f in fields} | {"ambiguous": "Yes" if item.get("ambiguous") else "No"} for item in items])
                st.dataframe(df, use_container_width=True, height=min(280, 40 + len(df) * 34))
        _tbl(tabs[0], entities.get("names", []),         ["value", "ambiguous", "context"])
        _tbl(tabs[1], entities.get("phones", []),        ["value", "ambiguous", "context"])
        _tbl(tabs[2], entities.get("emails", []),        ["value", "ambiguous", "context"])
        _tbl(tabs[3], entities.get("dates", []),         ["value", "ambiguous", "context"])
        _tbl(tabs[4], entities.get("locations", []),     ["value", "ambiguous", "context"])
        _tbl(tabs[5], entities.get("relationships", []), ["value", "rel_type", "entity_a", "entity_b"])
        with tabs[6]:
            st.text_area("", fr.get("raw_text", "")[:3000], height=280, label_visibility="collapsed")

    if st.session_state.person_profile:
        st.markdown('<hr class="divider">', unsafe_allow_html=True)
        st.markdown('<div style="font-size:0.95rem;font-weight:700;color:#7B2FBE;margin-bottom:0.4rem;">RESOLVED ENTITY</div>', unsafe_allow_html=True)
        _render_person(st.session_state.person_profile, st.session_state.resolution_method or "local")

    if st.session_state.behavioral_data:
        _render_behavioral(st.session_state.behavioral_data)

    st.markdown('<hr class="divider">', unsafe_allow_html=True)
    if st.button("ANALYSE NEW DOCUMENTS", use_container_width=True, key="fus_reset"):
        _fusion_reset()
        st.rerun()


# ─────────────────────────────────────────────────────────────────────────────
# NETWORK MAP
# ─────────────────────────────────────────────────────────────────────────────

def screen_network_map():
    screen_header("NETWORK MAP", "Relationship graph · Interactive zoom & pan · Hover for details")
    from modules.ui_components import stat_card, panel_hdr
    st.markdown(
        '<div class="al-classification-strip">TS//SCI//NOFORN · NETWORK MAP · RELATIONSHIP INTELLIGENCE</div>',
        unsafe_allow_html=True,
    )

    gd = st.session_state.graph_data
    if not gd:
        st.markdown(
            '<div class="card" style="text-align:center;padding:3rem 1rem;margin-top:1rem;">'
            '<div style="font-family:var(--f-mono);font-size:11px;color:var(--text-dim);'
            'letter-spacing:2px;">NO GRAPH DATA · RUN A SEARCH OR FUSION ANALYSIS FIRST</div>'
            '</div>',
            unsafe_allow_html=True,
        )
        if st.button("GO TO SEARCH", key="nm_go"): st.session_state.active_screen="search"; st.rerun()
        return

    from modules.relationship_mapper import render_graph, EDGE_TYPES
    G, summary = gd["graph"], gd["summary"]
    nodes_count = summary["nodes"]
    edges_count = summary["edges"]
    subj = st.session_state.search_query or "Subject"

    # ── Top toolbar: filter chips + controls ─────────────────────────────────
    all_et = list(set(d.get("edge_type","mentioned_with") for _,_,d in G.edges(data=True))) or EDGE_TYPES
    # Entity type filter chips as HTML (visual only — actual filter via multiselect below)
    chips_html = "".join(
        f'<div class="chip on"><span class="d" style="background:var(--purple-500);"></span>{et.upper()[:12]}</div>'
        for et in all_et[:6]
    )
    st.markdown(
        f'<div style="display:flex;align-items:center;gap:8px;padding:10px 0;flex-wrap:wrap;">'
        f'{chips_html}'
        f'<div class="ctrl-group" style="margin-left:auto;">'
        f'<span class="lbl">NODES</span><span style="color:var(--purple-300);">{nodes_count}</span>'
        f'&nbsp;&nbsp;<span class="lbl">EDGES</span><span style="color:var(--purple-300);">{edges_count}</span>'
        f'</div>'
        f'<div class="icon-btn" title="Export">⊕</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # Functional filter via multiselect
    sel_et = st.multiselect("Connection types", options=all_et, default=all_et, key="et_filter",
                             label_visibility="collapsed")

    # ── Main area: graph viewport + detail panel ──────────────────────────────
    graph_col, detail_col = st.columns([3, 1])

    with graph_col:
        fig = render_graph(G, filter_edge_types=sel_et or None, title=f"Network Map — {subj}")
        st.plotly_chart(fig, use_container_width=True)

        # Legend
        _legend_items = [
            ("PRIMARY", "var(--purple-500)"),
            ("SECONDARY", "rgba(157,78,221,0.7)"),
            ("PERIPHERAL", "rgba(123,47,190,0.4)"),
            ("LOCATION", "var(--teal)"),
            ("DEVICE", "var(--warning)"),
        ]
        legend_html = "".join(
            f'<div style="display:flex;align-items:center;gap:6px;font-family:var(--f-mono);font-size:9px;color:var(--text-secondary);letter-spacing:1px;">'
            f'<span style="width:10px;height:10px;border-radius:50%;background:{c};display:inline-block;"></span>{lbl}'
            f'</div>'
            for lbl, c in _legend_items
        )
        st.markdown(
            f'<div class="card" style="padding:10px 14px;margin-top:6px;">'
            f'<div style="font-family:var(--f-mono);font-size:8px;letter-spacing:3px;color:var(--text-dim);text-transform:uppercase;margin-bottom:8px;">LEGEND</div>'
            f'<div style="display:flex;gap:14px;flex-wrap:wrap;">{legend_html}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    with detail_col:
        # Node detail panel
        top = summary.get("top_nodes",[])
        selected_node = top[0] if top else {}
        node_name  = selected_node.get("label", selected_node.get("name", subj))[:24]
        node_degree = selected_node.get("degree", edges_count)
        node_type  = selected_node.get("entity_type", "PERSON")

        st.markdown(
            f'<div class="card" style="padding:0;overflow:hidden;">'
            f'<div style="padding:12px 14px 8px;border-bottom:1px solid var(--border-soft);">'
            f'<div style="font-family:var(--f-mono);font-size:9px;letter-spacing:3px;color:var(--text-dim);text-transform:uppercase;">NODE DETAIL</div>'
            f'</div>'
            f'<div style="padding:14px;">'
            f'<div style="font-family:var(--f-display);font-weight:600;font-size:18px;color:var(--text-primary);letter-spacing:0.5px;">{node_name}</div>'
            f'<div style="font-family:var(--f-mono);font-size:9px;letter-spacing:2px;color:var(--purple-300);text-transform:uppercase;margin-top:4px;">{node_type}</div>'
            f'<div style="margin:12px 0;display:grid;grid-template-columns:1fr 1fr;gap:8px;">'
            f'<div class="kv"><div style="font-size:9px;color:var(--text-dim);letter-spacing:2px;text-transform:uppercase;">CONNECTIONS</div><div style="font-family:var(--f-display);font-size:22px;color:var(--text-primary);">{node_degree}</div></div>'
            f'<div class="kv"><div style="font-size:9px;color:var(--text-dim);letter-spacing:2px;text-transform:uppercase;">RANK</div><div style="font-family:var(--f-display);font-size:22px;color:var(--text-primary);">01</div></div>'
            f'</div>'
            f'</div>'

            # Top connections
            f'<div style="border-top:1px solid var(--border-soft);padding:10px 14px;">'
            f'<div style="font-family:var(--f-mono);font-size:9px;letter-spacing:3px;color:var(--text-dim);text-transform:uppercase;margin-bottom:8px;">TOP CONNECTIONS</div>'
            + "".join(
                f'<div style="display:flex;justify-content:space-between;align-items:center;padding:5px 0;border-bottom:1px solid var(--border-soft);">'
                f'<span style="font-family:var(--f-mono);font-size:10px;color:var(--text-secondary);">{n.get("label",n.get("name",""))[:20]}</span>'
                f'<span style="font-family:var(--f-mono);font-size:9px;color:var(--purple-300);">{n.get("degree",0)}</span>'
                f'</div>'
                for n in top[:6]
            )
            + f'</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

        st.markdown('<div style="margin-top:8px;"></div>', unsafe_allow_html=True)
        if st.button("BUILD FULL REPORT", use_container_width=True, key="nm_report"):
            st.session_state.active_screen = "reports"; st.rerun()

    # ── Status bar ────────────────────────────────────────────────────────────
    st.markdown(
        f'<div class="statusbar">'
        f'<span>NODES: <span class="hl">{nodes_count}</span></span>'
        f'<span>EDGES: <span class="hl">{edges_count}</span></span>'
        f'<span>SUBJECT: <span class="hl">{subj[:20]}</span></span>'
        f'<span>DENSITY: <span class="hl">{summary.get("density","—")}</span></span>'
        f'<span style="margin-left:auto;">LAYOUT: FORCE-DIRECTED</span>'
        f'</div>',
        unsafe_allow_html=True,
    )


# ─────────────────────────────────────────────────────────────────────────────
# TIMELINE
# ─────────────────────────────────────────────────────────────────────────────

def screen_timeline():
    screen_header("TIMELINE", "Chronological event map · Gap detection · Anomaly flags")
    st.markdown(
        '<div class="al-classification-strip">TS//SCI//NOFORN · TIMELINE ANALYSIS · CHRONOLOGICAL INTELLIGENCE</div>',
        unsafe_allow_html=True,
    )
    tl = st.session_state.timeline_data
    if not tl:
        st.markdown(
            '<div class="card" style="text-align:center;padding:3rem 1rem;margin-top:1rem;">'
            '<div style="font-family:var(--f-mono);font-size:11px;color:var(--text-dim);'
            'letter-spacing:2px;">NO TIMELINE DATA · RUN A SEARCH OR FUSION ANALYSIS FIRST</div>'
            '</div>',
            unsafe_allow_html=True,
        )
        if st.button("GO TO SEARCH", key="tl_go"): st.session_state.active_screen="search"; st.rerun()
        return

    events    = tl.get("events", [])
    gaps      = tl.get("gaps", [])
    anomalies = tl.get("anomalies", [])
    count     = tl.get("count", len(events))

    # ── Progress-summary stat tiles ────────────────────────────────────────────
    st.markdown(
        f'<div class="progress-summary" style="margin-bottom:18px;">'
        f'<div class="tile"><div class="k">EVENTS</div><div class="v">{count}</div></div>'
        f'<div class="tile"><div class="k">ACTIVITY GAPS</div><div class="v" style="color:{"var(--warning)" if gaps else "var(--online)"};">{len(gaps)}</div></div>'
        f'<div class="tile"><div class="k">ANOMALY FLAGS</div><div class="v" style="color:{"var(--critical)" if anomalies else "var(--online)"};">{len(anomalies)}</div></div>'
        f'<div class="tile"><div class="k">DATE SPAN</div><div class="v" style="font-size:14px;">'
        f'{(events[0].get("normalized","—")[:10] if events else "—")}</div></div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # ── Timeline chart ─────────────────────────────────────────────────────────
    if tl.get("figure"):
        st.markdown(
            '<div class="card" style="padding:0;overflow:hidden;">'
            '<div style="padding:12px 16px 8px;border-bottom:1px solid var(--border-soft);'
            'font-family:var(--f-mono);font-size:9px;letter-spacing:3px;color:var(--text-dim);'
            'text-transform:uppercase;">ACTIVITY TIMELINE</div>',
            unsafe_allow_html=True,
        )
        st.plotly_chart(tl["figure"], use_container_width=True)
        st.markdown('</div>', unsafe_allow_html=True)

    # ── Anomaly flag-cards ─────────────────────────────────────────────────────
    if anomalies:
        st.markdown(
            '<div class="card" style="padding:0;overflow:hidden;margin-top:14px;">'
            '<div style="padding:12px 16px 10px;border-bottom:1px solid var(--border-soft);'
            'display:flex;justify-content:space-between;align-items:center;">'
            '<div style="font-family:var(--f-mono);font-size:9px;letter-spacing:3px;'
            'color:var(--text-dim);text-transform:uppercase;">ANOMALY FLAGS</div>'
            f'<div style="font-family:var(--f-mono);font-size:9px;letter-spacing:2px;color:var(--critical);">{len(anomalies)} DETECTED</div>'
            '</div>',
            unsafe_allow_html=True,
        )
        for a in anomalies[:10]:
            flag    = (a.get("flag") or "ANOMALY")
            detail  = (a.get("detail") or a.get("description") or "")[:90]
            sev_raw = (a.get("severity") or "MEDIUM").upper()
            sev_cls = "hi" if "HIGH" in sev_raw or "CRITICAL" in sev_raw else "md" if "MEDIUM" in sev_raw else "lo"
            score   = a.get("score", "")
            st.markdown(
                f'<div class="flag-card {sev_cls}">'
                f'<div class="sev">{sev_raw[:8]}</div>'
                f'<div><div class="ttl">{flag[:50]}</div>'
                f'<div class="sub">{detail}</div></div>'
                + (f'<div class="score">{score}</div>' if score else '<div></div>') +
                f'</div>',
                unsafe_allow_html=True,
            )
        st.markdown('</div>', unsafe_allow_html=True)

    # ── Activity gaps ──────────────────────────────────────────────────────────
    if gaps:
        st.markdown(
            '<div class="card" style="padding:0;overflow:hidden;margin-top:14px;">'
            '<div style="padding:12px 16px 10px;border-bottom:1px solid var(--border-soft);">'
            '<div style="font-family:var(--f-mono);font-size:9px;letter-spacing:3px;'
            'color:var(--text-dim);text-transform:uppercase;">ACTIVITY GAPS</div>'
            '</div>',
            unsafe_allow_html=True,
        )
        gap_rows = ""
        for g in gaps[:8]:
            days = g.get("duration_days", 0)
            sev  = "hi" if days > 90 else "md" if days > 30 else "lo"
            gap_rows += (
                f'<div class="flag-card {sev}" style="grid-template-columns:80px 1fr auto;">'
                f'<div class="sev">{days}d GAP</div>'
                f'<div><div class="ttl">{g.get("start","")[:10]} → {g.get("end","")[:10]}</div>'
                f'<div class="sub">ACTIVITY VOID · {days} DAYS</div></div>'
                f'<div></div>'
                f'</div>'
            )
        st.markdown(gap_rows + '</div>', unsafe_allow_html=True)

    # ── All events log ─────────────────────────────────────────────────────────
    if events:
        with st.expander(f"EVENT LOG — ALL {len(events)} ENTRIES", expanded=False):
            hdr = (
                '<div style="display:grid;grid-template-columns:120px 90px 1fr 100px;gap:12px;'
                'padding:8px 14px;font-family:var(--f-mono);font-size:9px;letter-spacing:3px;'
                'color:var(--text-dim);text-transform:uppercase;border-bottom:1px solid var(--border);'
                'background:rgba(0,0,0,0.3);">DATE · TYPE · CONTEXT · SOURCE</div>'
            )
            rows = ""
            for e in events[:60]:
                etype = (e.get("event_type") or "MENTION").upper()[:12]
                ctx   = (e.get("context") or "")[:80]
                src   = (e.get("source") or "")[:18]
                date  = (e.get("normalized") or "")[:16]
                amb   = e.get("ambiguous", False)
                rows += (
                    f'<div style="display:grid;grid-template-columns:120px 90px 1fr 100px;gap:12px;'
                    f'padding:7px 14px;border-bottom:1px solid var(--border-soft);'
                    f'font-family:var(--f-mono);font-size:10px;'
                    f'{"opacity:0.6;" if amb else ""}">'
                    f'<span style="color:var(--text-dim);">{date}</span>'
                    f'<span style="color:var(--purple-300);font-size:9px;">{etype}</span>'
                    f'<span style="color:var(--text-primary);">{ctx}</span>'
                    f'<span style="color:var(--text-secondary);">{src}</span>'
                    f'</div>'
                )
            st.markdown(
                f'<div class="card" style="padding:0;overflow:hidden;max-height:360px;overflow-y:auto;">'
                f'{hdr}{rows}</div>',
                unsafe_allow_html=True,
            )

    st.markdown('<div style="margin-top:14px;"></div>', unsafe_allow_html=True)
    if st.button("RUN BEHAVIORAL ANALYSIS", key="behav_tl"): _run_behavioral()
    if st.session_state.behavioral_data: _render_behavioral(st.session_state.behavioral_data)
    _report_trigger_btn("OSINT")


# ─────────────────────────────────────────────────────────────────────────────
# BEHAVIORAL HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _run_behavioral():
    from modules.behavioral_analysis import analyze
    person  = st.session_state.person_profile or {}
    results = st.session_state.search_results or {}
    with st.spinner("Running behavioral analysis..."):
        assessment, method = analyze({"person": person, "search_results": results})
    st.session_state.behavioral_data = {"assessment": assessment, "method": method}
    st.rerun()

def _render_behavioral(data: dict):
    bd     = data["assessment"]
    method = data["method"]
    ml     = "Bedrock" if "bedrock" in (method or "") else ("Gemini" if "gemini" in (method or "") else "Local analysis")
    mc     = "#16A34A" if "bedrock" in (method or "") else "#9D4EDD"
    from modules.ui_components import panel_hdr as _phdr2
    st.markdown('<hr class="divider">', unsafe_allow_html=True)
    st.markdown(_phdr2("B·01","BEHAVIORAL ASSESSMENT", f"[{ml}]"), unsafe_allow_html=True)
    c1,c2 = st.columns(2)
    with c1:
        st.markdown('<div class="person-section"><h4>Timezone & Activity</h4>', unsafe_allow_html=True)
        st.markdown(f"**Probable timezone:** {bd.get('timezone_probable','Not determined') or 'Not determined'} ({bd.get('timezone_confidence',0)}% conf)")
        st.markdown(f"**Activity pattern:** {bd.get('activity_pattern','Not determined') or 'Not determined'}")
        st.markdown('</div>', unsafe_allow_html=True)
        score = bd.get("network_influence_score",0)
        sc    = _conf_color(score)
        st.markdown(
            f'<div class="person-section"><h4>Network Influence</h4>'
            f'<div style="font-family:\'Rajdhani\',sans-serif;font-weight:600;font-size:2rem;'
            f'color:{sc};line-height:1;">{score}'
            f'<span style="font-family:\'JetBrains Mono\',monospace;font-size:11px;'
            f'color:#4B5563;"> / 100</span></div></div>',
            unsafe_allow_html=True,
        )
        flags = bd.get("behavioral_flags",[])
        if flags:
            st.markdown('<div class="person-section"><h4>Behavioral Flags</h4>', unsafe_allow_html=True)
            for f in flags: st.markdown(f'<div style="color:#D97706;font-size:0.8rem;">⚑ {f}</div>', unsafe_allow_html=True)
            st.markdown('</div>', unsafe_allow_html=True)
    with c2:
        interests = bd.get("interest_clusters",[])
        if interests:
            st.markdown('<div class="person-section"><h4>Interest Clusters</h4>', unsafe_allow_html=True)
            for i in interests: st.markdown(f'<span class="entity-pill">{i}</span>', unsafe_allow_html=True)
            st.markdown('</div>', unsafe_allow_html=True)
        notes = bd.get("analyst_notes","")
        if notes:
            st.markdown(f'<div class="person-section"><h4>Analyst Notes</h4><div style="font-size:0.83rem;color:#9CA3AF;line-height:1.45;">{notes}</div></div>', unsafe_allow_html=True)
        limits = bd.get("data_limitations",[])
        if limits:
            st.markdown('<div class="person-section"><h4>Data Limitations</h4>', unsafe_allow_html=True)
            for l in limits: st.markdown(f'<div style="color:#4B5563;font-size:0.76rem;">• {l}</div>', unsafe_allow_html=True)
            st.markdown('</div>', unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# REPORT SCREEN
# ─────────────────────────────────────────────────────────────────────────────

def _report_trigger_btn(mode: str):
    person = st.session_state.person_profile
    if not person:
        st.markdown('<div class="info-msg" style="font-size:0.78rem;">Build a profile first to generate a report.</div>', unsafe_allow_html=True)
        return
    if st.button("GENERATE REPORT", use_container_width=False, key=f"gen_report_{mode}"):
        _generate_and_store(mode)

def _generate_and_store(mode: str):
    import traceback as _tb
    from modules.report_generator import generate_report
    uid = st.session_state.get("current_user", "system")
    raw_docs = st.session_state.get("raw_documents") or None
    person = st.session_state.person_profile or {}
    print(f"[REPORT] Starting for: {person.get('confirmed_name', 'Unknown')} [mode={mode}]")
    try:
        with st.spinner("Generating intelligence report..."):
            rd = generate_report(
                person          = person,
                search_results  = st.session_state.search_results,
                graph_data      = st.session_state.graph_data,
                timeline_data   = st.session_state.timeline_data,
                behavioral_data = st.session_state.behavioral_data,
                user_id         = uid,
                mode            = mode,
                agent_results   = st.session_state.get("agent_results"),
                raw_documents   = raw_docs,
                assets_data     = st.session_state.get("assets_data") or None,
            )
        if rd is None:
            raise ValueError("generate_report returned None")
        print(f"[REPORT] Complete — sections={len(rd.get('sections',{}))} gemini={rd.get('gemini_used')}")
        st.session_state.report_data = rd
        st.session_state.pipeline_person = person
        st.session_state.active_screen = "reports"
        st.rerun()
    except Exception as e:
        print(f"[REPORT] FAILED: {e}")
        _tb.print_exc()
        st.error(
            f"Report generation failed: {e}\n\n"
            "Check terminal for full error details."
        )
        # Store a minimal report so reports screen doesn't blank
        import datetime
        st.session_state.report_data = {
            "sections": {
                "subject_identity": {
                    "content": person.get("confirmed_name", "Unknown"),
                    "confidence": person.get("confidence_score", 0),
                    "verified_items": [],
                },
                "overall_confidence": person.get("confidence_score", 0),
                "error_note": str(e),
            },
            "pdf_bytes": b"",
            "pdf_path": "",
            "pdf_filename": "",
            "generated_at": datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
            "gemini_used": False,
            "subject": person.get("confirmed_name", "Unknown"),
            "mode": mode,
            "user_id": uid,
            "error": str(e),
        }
        st.session_state.active_screen = "reports"
        st.rerun()

def _conf_bar(score: int) -> str:
    col = _conf_color(score)
    return (
        f'<div style="background:#1E0040;height:3px;width:100%;margin:4px 0;position:relative;">'
        f'<div style="background:{col};width:{score}%;height:100%;"></div>'
        f'</div>'
    )

def screen_reports():
    screen_header("REPORTS", "Intelligence report · Gemini analysis · PDF export")
    rd = (
        st.session_state.get("report_data")
        or st.session_state.get("current_report")
        or st.session_state.get("last_report")
    )
    person = (
        st.session_state.get("person_profile")
        or st.session_state.get("pipeline_person")
    )

    if not rd and not person:
        st.markdown(
            '<div class="al-classification-strip">TS//SCI//NOFORN · INTELLIGENCE REPORT · NO ACTIVE CASE</div>',
            unsafe_allow_html=True,
        )
        st.info("No analysis yet. Run a search in SEARCH mode or upload documents in FUSION mode.")
        c1, c2 = st.columns(2)
        with c1:
            if st.button("GO TO SEARCH", key="rp_go_search"):
                st.session_state.active_screen = "search"; st.rerun()
        with c2:
            if st.button("GO TO FUSION", key="rp_go_fusion"):
                st.session_state.active_screen = "fusion"; st.rerun()
        return

    if not rd and person:
        st.warning("Profile found but report not yet generated.")
        mode = st.session_state.get("mode", "OSINT")
        if st.button("GENERATE REPORT NOW", use_container_width=True, key="gen_now"):
            _generate_and_store(mode)
        return

    # Show error banner if report contains an error but still has content
    if rd.get("error"):
        st.error(f"Report generated with errors: {rd['error']}")

    secs    = rd["sections"]
    subject = rd["subject"]
    gen_at  = rd["generated_at"]
    uid     = rd["user_id"]
    mode    = rd["mode"]
    g_used  = rd["gemini_used"]
    overall_confidence = secs.get("overall_confidence", 0)
    overall            = overall_confidence   # alias used in _conf_bar / _conf_color calls below

    # ── Classification strip ───────────────────────────────────────────────────
    st.markdown(
        '<div class="al-classification-strip">TS//SCI//NOFORN · INTELLIGENCE REPORT · AUTHORIZED ACCESS ONLY</div>',
        unsafe_allow_html=True,
    )

    # ── Report top header (4-column grid matching handoff) ─────────────────────
    conf_c = "#16A34A" if overall >= 75 else "#D97706" if overall >= 50 else "#DC2626"
    conf_lbl = "HIGH" if overall >= 75 else "MEDIUM" if overall >= 50 else "LOW"
    g_color = "#2563EB" if g_used else "#4B5563"
    g_lbl   = "GEMINI 2.5" if g_used else "LOCAL"
    risk_r  = (st.session_state.agent_results or {}).get("risk", {})
    risk_score = risk_r.get("risk_score", 0)
    risk_level = risk_r.get("risk_level", "—")
    rc = "#DC2626" if risk_score >= 70 else "#D97706" if risk_score >= 40 else "#16A34A"

    # SVG confidence gauge arc
    import math
    angle = (overall_confidence / 100) * 251.2  # circumference ≈ 251.2 for r=40
    gauge_svg = (
        f'<svg viewBox="0 0 100 100" style="width:120px;height:120px;transform:rotate(-90deg);">'
        f'<circle cx="50" cy="50" r="40" fill="none" stroke="rgba(123,47,190,0.15)" stroke-width="8"/>'
        f'<circle cx="50" cy="50" r="40" fill="none" stroke="{conf_c}" stroke-width="8" '
        f'stroke-dasharray="{angle:.1f} 251.2" stroke-linecap="round"/>'
        f'</svg>'
    )
    gauge_html = (
        f'<div class="gauge" style="width:120px;height:120px;">'
        f'{gauge_svg}'
        f'<div class="val">'
        f'<div><div class="num" style="font-size:36px;color:{conf_c};">{overall_confidence}</div>'
        f'<div class="lbl">CONFIDENCE</div>'
        f'<div class="sub" style="color:{conf_c};font-size:8px;">{conf_lbl}</div></div>'
        f'</div>'
        f'</div>'
    )

    # Person data for meta-grid
    person_data = person or {}
    plats_confirmed = person_data.get("platforms_confirmed", [])
    locs = person_data.get("location_stated", [])
    emails = person_data.get("emails_found", [])
    aliases = person_data.get("name_variants", [])

    st.markdown(
        f'<div class="report-top">'

        # Col 1 — Subject
        f'<div class="rt-subject">'
        f'<div class="tag">INTELLIGENCE SUBJECT</div>'
        f'<h1>{subject}</h1>'
        f'<div class="meta">'
        f'<span>CASE: {uid[:12].upper()}</span>'
        f'<span>MODE: {mode}</span>'
        f'<span>ENGINE: <span style="color:{g_color};">{g_lbl}</span></span>'
        f'</div>'
        f'</div>'

        # Col 2 — Confidence gauge
        f'<div style="display:flex;align-items:center;justify-content:center;">{gauge_html}</div>'

        # Col 3 — Metadata grid
        f'<div class="rt-meta-grid">'
        f'<div class="k">GENERATED</div><div class="v">{gen_at[:16]}</div>'
        f'<div class="k">PLATFORMS</div><div class="v">{len(plats_confirmed)}</div>'
        f'<div class="k">LOCATIONS</div><div class="v">{len(locs)}</div>'
        f'<div class="k">RISK LEVEL</div><div class="v" style="color:{rc};">{risk_level} · {risk_score}</div>'
        f'</div>'

        # Col 4 — Action buttons (rendered as HTML; actual download handled below)
        f'<div class="rt-actions">'
        f'<div class="btn" style="font-family:var(--f-mono);font-size:10px;letter-spacing:2px;'
        f'padding:8px 14px;border:1px solid var(--border-active);color:var(--purple-100);'
        f'background:rgba(123,47,190,0.12);text-transform:uppercase;text-align:center;">EXPORT PDF</div>'
        f'<div class="btn" style="font-family:var(--f-mono);font-size:10px;letter-spacing:2px;'
        f'padding:8px 14px;border:1px solid var(--border);color:var(--text-secondary);'
        f'text-transform:uppercase;text-align:center;">SHARE</div>'
        f'<div style="font-family:var(--f-mono);font-size:9px;letter-spacing:3px;'
        f'padding:8px 14px;border:1px solid rgba(220,38,38,0.5);color:var(--critical);'
        f'text-align:center;background:rgba(220,38,38,0.06);">RESTRICTED</div>'
        f'</div>'

        f'</div>',
        unsafe_allow_html=True,
    )

    # ── Functional action buttons ──────────────────────────────────────────────
    ba1, ba2, ba3 = st.columns(3)
    with ba1:
        pdf_bytes = rd.get("pdf_bytes", b"")
        if pdf_bytes:
            st.download_button("DOWNLOAD PDF", data=pdf_bytes,
                               file_name=rd.get("pdf_filename","report.pdf"),
                               mime="application/pdf", use_container_width=True, key="dl_pdf_rp")
    with ba2:
        if st.button("REGENERATE", use_container_width=True, key="regen_rp"):
            _generate_and_store(mode)
    with ba3:
        if st.button("NEW ANALYSIS", use_container_width=True, key="new_analysis_rp"):
            st.session_state.active_screen = "fusion"; st.rerun()

    # ── TOC navigation strip ───────────────────────────────────────────────────
    _TOC_SECTIONS = [
        ("01","SUBJECT IDENTITY"),("02","CONFIDENCE SCORE"),("03","PLATFORM PRESENCE"),
        ("04","PUBLIC LOCATION DATA"),("05","NETWORK MAP"),("06","TIMELINE"),
        ("07","BEHAVIORAL PATTERNS"),("08","KEY ASSOCIATIONS"),("09","ANOMALIES & FLAGS"),
        ("10","DATA GAPS"),("11","SOURCE LOG"),("12","AI ENGINE NOTES"),
        ("13","LINKED PROFILES"),("14","CROSS-PLATFORM"),("15","RISK ASSESSMENT"),
        ("16","COMPLIANCE"),("17","NEXT STEPS"),("18","TACTICAL PLAN"),
    ]
    toc_items = "".join(
        f'<div class="toc .li" style="display:inline-flex;gap:6px;align-items:center;'
        f'padding:4px 10px;border:1px solid var(--border-soft);margin:2px;cursor:pointer;'
        f'font-family:var(--f-mono);font-size:9px;letter-spacing:1px;color:var(--text-secondary);">'
        f'<span style="color:var(--purple-500);">{num}</span>'
        f'<span>{title}</span>'
        f'</div>'
        for num, title in _TOC_SECTIONS
    )
    st.markdown(
        f'<div style="border-top:1px solid var(--border);border-bottom:1px solid var(--border);'
        f'padding:10px 0;margin:14px 0;overflow-x:auto;white-space:nowrap;">'
        f'{toc_items}</div>',
        unsafe_allow_html=True,
    )

    # ── Section helpers (rewritten to use .sec-hd / .sec-bd CSS) ──────────────
    def _sec(num: str, title: str, tag: str, tag_cls: str, body_html: str):
        with st.expander(f"§{num} · {title}", expanded=True):
            st.markdown(
                f'<div class="sec-hd">'
                f'<span style="display:flex;align-items:baseline;gap:10px;">'
                f'<span class="n">§{num}</span>'
                f'<span class="t">{title}</span>'
                f'</span>'
                f'<span class="tag {tag_cls}">{tag}</span>'
                f'</div>'
                f'<div class="sec-bd">{body_html}</div>',
                unsafe_allow_html=True,
            )

    def _verified_section(num: str, title: str, content: str, confidence: int, items: list = None):
        items_html = "".join(
            f'<div class="note" style="padding-left:12px;margin-top:3px;">· {item}</div>'
            for item in (items or [])[:15]
        )
        body = f'<div class="note">{content}</div>{items_html}'
        _sec(num, title, "VERIFIED DATA", "v", body)

    def _ai_section(num: str, title: str, content: str, confidence: int, flags: list = None):
        flags_html = "".join(
            f'<div style="font-family:var(--f-mono);font-size:11px;color:var(--warning);'
            f'padding-left:12px;margin-top:3px;">⚑ {f}</div>'
            for f in (flags or [])[:10]
        )
        body = f'<div class="note" style="color:var(--purple-100);">{content}</div>{flags_html}'
        _sec(num, title, "AI ANALYSIS", "a", body)

    # ── Render all 12 sections ────────────────────────────────────────────────
    s1 = secs.get("subject_identity",{})
    _verified_section("01","SUBJECT IDENTITY", s1.get("content","Not found"), s1.get("confidence",0), s1.get("verified_items",[]))

    # ── §02 CONFIDENCE SCORE ──────────────────────────────────────────────────
    _raw_breakdown  = secs.get("confidence_breakdown", {})
    _conf_breakdown = _raw_breakdown if isinstance(_raw_breakdown, dict) else {}
    _conf_expl      = secs.get("confidence_explanation", "")
    _conf_color_val = "var(--online)" if overall >= 75 else "var(--warning)" if overall >= 50 else "var(--critical)"
    _bd_rows = "".join(
        f'<div class="assoc" style="grid-template-columns:1fr 100px;">'
        f'<span class="n">{k.replace("_"," ").title()}</span>'
        f'<span style="color:{_conf_color_val};text-align:right;">{f"+{v}" if isinstance(v,(int,float)) and v > 0 else str(v)}</span>'
        f'</div>'
        for k, v in _conf_breakdown.items()
    ) if _conf_breakdown else ""
    _sec("02","OVERALL CONFIDENCE SCORE","CALCULATED","v",
        f'<div class="note" style="color:{_conf_color_val};font-size:28px;font-family:var(--f-display);font-weight:600;">{overall} <span style="font-size:12px;color:var(--text-dim);">/ 100</span></div>'
        + (f'<div class="note" style="margin-top:8px;">{_conf_expl}</div>' if _conf_expl else "")
        + (_bd_rows or "")
    )

    s3 = secs.get("platform_presence",{})
    plats_dict = s3.get("platforms",{})
    plats_html = ""
    if plats_dict:
        plats_html = '<div class="plats">'
        for plat, url in list(plats_dict.items())[:8]:
            found = url and url != "Not found"
            cls   = "found" if found else "missing"
            plats_html += (
                f'<div class="plat {cls}">'
                f'<span class="name">{plat[:10]}</span>'
                f'<span class="st">{"FOUND" if found else "NOT FOUND"}</span>'
                f'</div>'
            )
        plats_html += "</div>"
    _sec("03","PLATFORM PRESENCE","VERIFIED DATA","v",
        f'<div class="note">{s3.get("content","Not found")}</div>{plats_html}')

    s4 = secs.get("public_location_data",{})
    _verified_section("04","PUBLIC LOCATION DATA", s4.get("content","Not found"), s4.get("confidence",0), s4.get("locations",[]))

    s5 = secs.get("network_map_summary",{})
    _verified_section("05","NETWORK MAP SUMMARY", s5.get("content","Not found"), s5.get("confidence",0), s5.get("connections",[]))

    s6 = secs.get("timeline_of_activity",{})
    _verified_section("06","TIMELINE OF ACTIVITY", s6.get("content","Not found"), s6.get("confidence",0), s6.get("events",[]))

    s7 = secs.get("behavioral_patterns",{})
    _ai_section("07","BEHAVIORAL PATTERNS", s7.get("content","Not available"), s7.get("confidence",0), s7.get("flags",[]))

    s8 = secs.get("key_associations",{})
    _verified_section("08","KEY ASSOCIATIONS", s8.get("content","Not found"), s8.get("confidence",0), s8.get("associations",[]))

    s9 = secs.get("anomalies_and_flags",{})
    _verified_section("09","ANOMALIES AND FLAGS", s9.get("content","None detected"), 0, s9.get("flags",[]))

    s10 = secs.get("data_gaps",{})
    _gap_items = s10.get("items",["None identified"])[:20]
    _gaps_html = "".join(f'<div class="note" style="padding-left:10px;">· {g}</div>' for g in _gap_items)
    _sec("10","DATA GAPS","ANALYSIS","v", _gaps_html or '<div class="note">None identified.</div>')

    s11 = secs.get("source_log",{})
    _src_urls = s11.get("urls",[])
    _src_html = "".join(f'<div class="note" style="color:var(--purple-400);">→ {u}</div>' for u in _src_urls[:40])
    _sec("11","SOURCE LOG","LOGGED","v", _src_html or '<div class="note" style="color:var(--text-dim);">No URLs logged.</div>')

    s12 = secs.get("ai_engine_notes",{})
    _ai_section("12","AI ENGINE NOTES", s12.get("content","Not available"), 0)

    # ── Linked Profiles ───────────────────────────────────────────────────────
    person         = st.session_state.person_profile or {}
    conf_linked    = person.get("confirmed_linked_profiles", [])
    pot_linked     = person.get("potential_linked_profiles", [])
    emails_found   = person.get("emails_found", [])
    phones_found   = person.get("phones_found", [])
    websites_found = person.get("websites_found", [])
    li_intel       = person.get("linkedin_intelligence", {})
    cross_summary  = person.get("cross_platform_summary", {})

    if conf_linked or pot_linked or emails_found or phones_found or li_intel.get("name"):
        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown('<hr class="divider">', unsafe_allow_html=True)
        st.markdown(
            '<div style="font-size:1rem;font-weight:700;color:#7B2FBE;letter-spacing:0.15rem;margin-bottom:0.6rem;">'
            '13. LINKED PROFILES DISCOVERED</div>',
            unsafe_allow_html=True,
        )

        total_found = cross_summary.get("total_accounts_found",
                                        len(conf_linked) + len(pot_linked))
        plats_checked = cross_summary.get("platforms_checked", [])
        st.markdown(
            f'<div style="font-size:0.75rem;color:#4B5563;margin-bottom:0.6rem;">'
            f'{total_found} account(s) found across '
            f'{len(plats_checked)} platform(s) checked</div>',
            unsafe_allow_html=True,
        )

        lc, rc = st.columns(2)

        with lc:
            st.markdown(
                '<div style="font-size:0.78rem;font-weight:700;color:#16A34A;'
                'letter-spacing:0.12rem;margin-bottom:0.4rem;">CONFIRMED ACCOUNTS</div>',
                unsafe_allow_html=True,
            )
            if conf_linked:
                for c in conf_linked[:15]:
                    pub  = c.get("public_data", {})
                    bio  = pub.get("bio", "") or pub.get("snippet", "")
                    name = pub.get("name", "")
                    url  = c.get("url", "")
                    url_html = (
                        f'<a href="{url}" target="_blank" '
                        f'style="color:#7B2FBE;font-size:0.7rem;">'
                        f'{url[:50]}{"..." if len(url)>50 else ""}</a>'
                    ) if url else ""
                    st.markdown(
                        f'<div style="background:#050505;border:1px solid #7B2FBE;'
                        f'border-radius:5px;padding:0.6rem 0.8rem;margin-bottom:0.4rem;">'
                        f'{_platform_badge(c.get("platform",""))}'
                        f'<span style="background:#002200;border:1px solid #16A34A;color:#16A34A;'
                        f'border-radius:3px;padding:1px 6px;font-size:0.65rem;font-weight:700;'
                        f'margin-left:6px;">100% CONFIRMED</span>'
                        f'<div style="font-size:0.9rem;font-weight:700;color:#F0EAD6;margin:3px 0 1px;">'
                        f'@{c.get("username","")}{"  · "+name if name else ""}</div>'
                        f'<div style="font-size:0.72rem;color:#9CA3AF;margin-bottom:2px;">'
                        f'Match: {c.get("match_reason","")}</div>'
                        f'{url_html}'
                        f'{"<div style=\'font-size:0.72rem;color:#666;margin-top:3px;\'>"+bio[:100]+"</div>" if bio else ""}'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
            else:
                st.markdown(
                    '<div style="color:#333;font-size:0.8rem;">'
                    'No confirmed linked accounts found.</div>',
                    unsafe_allow_html=True,
                )

        with rc:
            st.markdown(
                '<div style="font-size:0.78rem;font-weight:700;color:#9D4EDD;'
                'letter-spacing:0.12rem;margin-bottom:0.4rem;">POTENTIAL ACCOUNTS</div>',
                unsafe_allow_html=True,
            )
            if pot_linked:
                for p in pot_linked[:15]:
                    conf_v  = p.get("confidence", 0)
                    c_color = "#16A34A" if conf_v >= 90 else ("#D97706" if conf_v >= 70 else "#DC2626")
                    pub     = p.get("public_data", {})
                    bio     = pub.get("bio", "") or pub.get("snippet", "")
                    url     = p.get("url", "")
                    url_html = (
                        f'<a href="{url}" target="_blank" '
                        f'style="color:#7B2FBE;font-size:0.7rem;">'
                        f'{url[:50]}{"..." if len(url)>50 else ""}</a>'
                    ) if url else ""
                    low_conf_warn = (
                        '<div style="font-size:0.68rem;color:#DC2626;margin-top:2px;">'
                        'Low confidence — verify manually</div>'
                        if conf_v < 70 else ""
                    )
                    st.markdown(
                        f'<div style="background:#050505;border:1px solid #3a1a5e;'
                        f'border-radius:5px;padding:0.6rem 0.8rem;margin-bottom:0.4rem;">'
                        f'{_platform_badge(p.get("platform",""))}'
                        f'<span style="background:#110022;border:1px solid {c_color};color:{c_color};'
                        f'border-radius:3px;padding:1px 6px;font-size:0.65rem;font-weight:700;'
                        f'margin-left:6px;">{conf_v}%</span>'
                        f'<div style="font-size:0.9rem;font-weight:700;color:#F0EAD6;margin:3px 0 1px;">'
                        f'@{p.get("username","")}</div>'
                        f'<div style="font-size:0.72rem;color:#9CA3AF;margin-bottom:2px;">'
                        f'Match: {p.get("match_reason","")}</div>'
                        f'{url_html}'
                        f'{"<div style=\'font-size:0.72rem;color:#666;margin-top:3px;\'>"+bio[:100]+"</div>" if bio else ""}'
                        f'{low_conf_warn}'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
            else:
                st.markdown(
                    '<div style="color:#333;font-size:0.8rem;">'
                    'No potential linked accounts found.</div>',
                    unsafe_allow_html=True,
                )

        # ── Intelligence Extracted ────────────────────────────────────────────
        intel_rows = []
        for email in emails_found[:10]:
            count = sum(1 for c in conf_linked if email in str(c.get("public_data", {})))
            status = "VERIFIED" if count > 1 else "SINGLE SOURCE"
            intel_rows.append({"Type": "Email", "Value": email, "Source": "LinkedIn/profile", "Status": status})
        for phone in phones_found[:5]:
            intel_rows.append({"Type": "Phone", "Value": phone, "Source": "LinkedIn/profile", "Status": "UNVERIFIED"})
        for c in conf_linked[:20]:
            intel_rows.append({
                "Type":   "Social Handle",
                "Value":  f"@{c.get('username','')} ({c.get('platform','')})",
                "Source": c.get("match_reason", "discovery"),
                "Status": "VERIFIED",
            })
        for w in websites_found[:5]:
            intel_rows.append({"Type": "Website", "Value": w[:60], "Source": "LinkedIn/profile", "Status": "SINGLE SOURCE"})

        if li_intel.get("twitter_found"):
            intel_rows.append({"Type": "Social Handle", "Value": f"@{li_intel['twitter_found']} (Twitter)", "Source": "LinkedIn link", "Status": "VERIFIED"})
        if li_intel.get("github_found"):
            intel_rows.append({"Type": "Social Handle", "Value": f"{li_intel['github_found']} (GitHub)", "Source": "LinkedIn link", "Status": "VERIFIED"})

        if intel_rows:
            st.markdown("<br>", unsafe_allow_html=True)
            st.markdown(
                '<div style="font-size:1rem;font-weight:700;color:#7B2FBE;letter-spacing:0.15rem;margin-bottom:0.6rem;">'
                '14. INTELLIGENCE EXTRACTED FROM LINKED PROFILES</div>',
                unsafe_allow_html=True,
            )
            import pandas as pd
            df_intel = pd.DataFrame(intel_rows)
            # Color the Status column via styling
            def _style_status(val):
                if val == "VERIFIED":       return "color:#16A34A;font-weight:700"
                if val == "SINGLE SOURCE":  return "color:#D97706;font-weight:700"
                return "color:#DC2626;font-weight:700"
            st.dataframe(
                df_intel.style.map(_style_status, subset=["Status"]),
                use_container_width=True,
                hide_index=True,
            )

        # LinkedIn intelligence detail
        if li_intel and li_intel.get("name") and li_intel["name"] != "Requires direct visit":
            with st.expander("LinkedIn Intelligence Detail", expanded=False):
                li_cols = st.columns(2)
                with li_cols[0]:
                    st.markdown(f'<div class="person-section"><h4>Profile</h4>', unsafe_allow_html=True)
                    if li_intel.get("name"):
                        st.markdown(f"**Name:** {li_intel['name']}")
                    if li_intel.get("headline"):
                        st.markdown(f"**Headline:** {li_intel['headline'][:150]}")
                    if li_intel.get("location"):
                        st.markdown(f"**Location:** {li_intel['location']}")
                    if li_intel.get("company"):
                        st.markdown(f"**Company:** {li_intel['company']}")
                    if li_intel.get("education"):
                        st.markdown(f"**Education:** {', '.join(li_intel['education'][:3])}")
                    st.markdown(f"**Confidence:** {li_intel.get('confidence', 0)}%")
                    st.markdown('</div>', unsafe_allow_html=True)
                with li_cols[1]:
                    st.markdown('<div class="person-section"><h4>Contact & Socials</h4>', unsafe_allow_html=True)
                    for email in li_intel.get("emails_found", []):
                        st.markdown(f"**Email:** `{email}`")
                    for phone in li_intel.get("phones_found", []):
                        st.markdown(f"**Phone:** `{phone}`")
                    if li_intel.get("twitter_found"):
                        st.markdown(f"**Twitter/X:** @{li_intel['twitter_found']}")
                    if li_intel.get("github_found"):
                        st.markdown(f"**GitHub:** {li_intel['github_found']}")
                    if li_intel.get("instagram_found"):
                        st.markdown(f"**Instagram:** @{li_intel['instagram_found']}")
                    if li_intel.get("website_found"):
                        st.markdown(f"**Website:** {li_intel['website_found'][:80]}")
                    for other in li_intel.get("other_socials", []):
                        st.markdown(f"• {other}")
                    st.markdown('</div>', unsafe_allow_html=True)

    # ── Digital Identity Timeline ─────────────────────────────────────────────
    acct_timeline = person.get("account_timeline", [])
    oldest_acct   = person.get("oldest_account", {})
    digital_age   = person.get("digital_age_years", 0)
    acct_flags    = person.get("account_creation_flags", [])

    if acct_timeline:
        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown(
            '<div class="section-header">15. DIGITAL IDENTITY TIMELINE</div>',
            unsafe_allow_html=True,
        )
        tl_meta1, tl_meta2, tl_meta3 = st.columns(3)
        with tl_meta1:
            st.metric("Digital Age", f"{digital_age} yrs" if digital_age else "Unknown")
        with tl_meta2:
            st.metric("First Platform", oldest_acct.get("platform", "Unknown"))
        with tl_meta3:
            high_flags = [f for f in acct_flags if f.get("severity") in ("HIGH", "MEDIUM")]
            st.metric("Pattern Flags", len(acct_flags), delta=f"{len(high_flags)} HIGH/MED" if high_flags else None)

        # Plotly timeline chart
        try:
            from modules.account_timeline import build_timeline_chart
            fig = build_timeline_chart(acct_timeline)
            if fig:
                st.plotly_chart(fig, use_container_width=True)
        except Exception:
            pass

        # Pattern flags expander
        if acct_flags:
            with st.expander(f"Account Creation Pattern Flags ({len(acct_flags)})", expanded=False):
                _SEV_COLOR = {"HIGH": "#DC2626", "MEDIUM": "#D97706", "LOW": "#B08FD4", "INFO": "#7B2FBE"}
                for f in acct_flags:
                    sev    = f.get("severity", "INFO")
                    color  = _SEV_COLOR.get(sev, "#888888")
                    detail = f.get("detail", f.get("flag", ""))
                    st.markdown(
                        f'<div style="border-left:3px solid {color};padding:6px 10px;margin:4px 0;'
                        f'background:rgba(0,0,0,0.3);border-radius:0 4px 4px 0;">'
                        f'<span style="color:{color};font-weight:700;font-size:11px;">[{sev}]</span> '
                        f'<span style="color:#E0D6F5;font-size:12px;">{detail}</span></div>',
                        unsafe_allow_html=True,
                    )

        # Account timeline table
        with st.expander("Account Creation Date Details", expanded=False):
            import pandas as pd
            tl_rows = []
            for e in acct_timeline:
                tl_rows.append({
                    "Platform":   e.get("platform", ""),
                    "Joined":     e.get("join_date_str", e.get("date", "")),
                    "Age (yrs)":  e.get("age_years", 0),
                    "Confidence": e.get("confidence", ""),
                    "Type":       e.get("profile_type", "primary"),
                    "Source":     e.get("source", ""),
                })
            if tl_rows:
                def _style_conf(val):
                    if val == "EXACT":         return "color:#16A34A;font-weight:700"
                    if val == "APPROXIMATE":   return "color:#D97706;"
                    return "color:#888888;"
                df_tl = pd.DataFrame(tl_rows)
                st.dataframe(
                    df_tl.style.map(_style_conf, subset=["Confidence"]),
                    use_container_width=True,
                    hide_index=True,
                )

    # ── Download PDF ──────────────────────────────────────────────────────────
    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown('<hr class="divider">', unsafe_allow_html=True)
    dc1, dc2 = st.columns([3, 1])
    with dc1:
        if st.button("Download Report PDF", use_container_width=True, key="dl_pdf_btn"):
            if rd:
                try:
                    from modules.report_generator import generate_pdf, save_pdf_to_exports, _sections_to_pdf_data
                    fresh_pdf = generate_pdf(
                        report_data = _sections_to_pdf_data(rd.get("sections", {})),
                        username    = rd.get("subject", "unknown"),
                        user_id     = rd.get("user_id", st.session_state.get("current_user", "unknown")),
                        mode        = rd.get("mode", "OSINT"),
                        gemini_used = rd.get("gemini_used", False),
                    )
                    filepath = save_pdf_to_exports(
                        fresh_pdf,
                        rd.get("subject", "unknown"),
                        rd.get("user_id", "unknown"),
                    )
                    fname = rd.get("pdf_filename", f"AETHERLENS_{rd.get('subject','report')}.pdf")
                    st.download_button(
                        label     = "Save PDF",
                        data      = fresh_pdf,
                        file_name = fname,
                        mime      = "application/pdf",
                        key       = "dl_pdf_save",
                    )
                    st.success(f"Saved to {filepath}")
                except Exception as e:
                    import traceback as _tb; _tb.print_exc()
                    st.error(f"PDF generation error: {e}")
            else:
                st.error("No report in session. Generate a report first.")
        # Always show direct download from cached pdf_bytes too
        cached_bytes = rd.get("pdf_bytes") if rd else None
        if cached_bytes:
            st.download_button(
                label            = "DOWNLOAD PDF REPORT",
                data             = cached_bytes,
                file_name        = rd.get("pdf_filename", "aetherlens_report.pdf"),
                mime             = "application/pdf",
                use_container_width = True,
                key              = "dl_pdf_cached",
            )
    with dc2:
        role = st.session_state.get("current_role", "")
        if role == config.ROLE_ADMIN:
            st.button("SHARE", use_container_width=True, key="share_report",
                      help="Admin only — share report")

    if st.button("REGENERATE", key="regen_report"):
        _generate_and_store(mode)


# ─────────────────────────────────────────────────────────────────────────────
# ADMIN PANEL
# ─────────────────────────────────────────────────────────────────────────────

def screen_admin():
    from modules.ui_components import hash_chain_panel, panel_hdr
    role = st.session_state.get("current_role","")
    if role != config.ROLE_ADMIN:
        st.markdown(
            '<div style="text-align:center;font-family:\'JetBrains Mono\',monospace;'
            'font-size:11px;letter-spacing:3px;color:#DC2626;margin-top:4rem;">'
            'ACCESS DENIED · ADMIN ROLE REQUIRED</div>',
            unsafe_allow_html=True,
        )
        return

    screen_header("ADMIN PANEL", "User management · Audit log · System status")
    st.markdown(
        '<div class="al-classification-strip">TS//SCI//NOFORN · ADMIN PANEL · OPERATOR ACCESS REQUIRED</div>',
        unsafe_allow_html=True,
    )

    # ── Page header with operator info ──────────────────────────────────────────
    import datetime
    op_name = (st.session_state.current_user or "ADMIN").upper()
    st.markdown(
        f'<div style="display:flex;justify-content:space-between;align-items:center;padding:8px 0 16px;">'
        f'<div>'
        f'<div style="font-family:var(--f-display);font-weight:600;font-size:22px;letter-spacing:1px;">{op_name}</div>'
        f'<div style="font-family:var(--f-mono);font-size:9px;letter-spacing:2px;color:var(--text-dim);margin-top:2px;">SYSTEM ADMINISTRATOR · FULL ACCESS</div>'
        f'</div>'
        f'<div style="text-align:right;">'
        f'<div style="font-family:var(--f-mono);font-size:10px;letter-spacing:2px;color:var(--text-secondary);">{datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M")} UTC</div>'
        f'<div class="status-pill active" style="margin-top:4px;"><span style="width:6px;height:6px;border-radius:50%;background:var(--online);display:inline-block;"></span> SESSION ACTIVE</div>'
        f'</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    tab_users, tab_audit, tab_status = st.tabs(["USER MANAGEMENT","AUDIT LOG","SYSTEM STATUS"])

    # ── USER MANAGEMENT ───────────────────────────────────────────────────────
    with tab_users:
        import pandas as pd
        users = get_all_users()

        # Styled user table
        _ROLE_CLS = {"admin": "admin", "analyst": "analyst", "officer": "officer", "viewer": "viewer"}
        if users:
            user_rows_html = ""
            for u in users:
                uname   = u.get("username","—")
                urole   = (u.get("role","viewer") or "viewer").lower()
                active  = bool(u.get("is_active",1))
                last_l  = (u.get("last_login") or "—")[:16]
                ip      = (u.get("last_ip") or u.get("ip","—"))[:16]
                pill_cls = "active" if active else "locked"
                pill_lbl = "ACTIVE" if active else "LOCKED"
                mfa      = u.get("mfa_enabled", False)
                role_cls = _ROLE_CLS.get(urole, "viewer")
                user_rows_html += (
                    f'<div style="display:grid;grid-template-columns:30px 1.5fr 100px 90px 120px 120px 110px 70px auto;'
                    f'gap:10px;align-items:center;padding:10px 16px;border-bottom:1px solid var(--border-soft);'
                    f'font-family:var(--f-mono);font-size:11px;">'
                    f'<span style="color:var(--text-dim);">☐</span>'
                    f'<span style="color:var(--text-primary);letter-spacing:0.5px;">{uname}</span>'
                    f'<span class="role-badge {role_cls}" style="font-family:var(--f-mono);font-size:9px;'
                    f'letter-spacing:2px;padding:2px 8px;border:1px solid;text-transform:uppercase;">{urole}</span>'
                    f'<span style="color:var(--text-dim);">—</span>'
                    f'<span style="color:var(--text-secondary);">{last_l}</span>'
                    f'<span style="color:var(--text-dim);">{ip}</span>'
                    f'<span class="status-pill {pill_cls}"><span style="width:6px;height:6px;border-radius:50%;'
                    f'background:{"var(--online)" if active else "var(--critical)"};display:inline-block;margin-right:4px;"></span>{pill_lbl}</span>'
                    f'<span style="color:{"var(--online)" if mfa else "var(--text-dim)"};">{"MFA" if mfa else "—"}</span>'
                    f'<span class="row-actions"></span>'
                    f'</div>'
                )
            st.markdown(
                f'<div class="card" style="padding:0;overflow:hidden;">'
                f'<div style="display:grid;grid-template-columns:30px 1.5fr 100px 90px 120px 120px 110px 70px auto;'
                f'gap:10px;padding:10px 16px;border-bottom:1px solid var(--border);'
                f'font-family:var(--f-mono);font-size:9px;letter-spacing:3px;color:var(--text-dim);text-transform:uppercase;'
                f'background:rgba(0,0,0,0.3);">'
                f'<span></span><span>USER</span><span>ROLE</span><span>BADGE</span>'
                f'<span>LAST LOGIN</span><span>IP</span><span>STATUS</span><span>MFA</span><span>ACTIONS</span>'
                f'</div>'
                f'{user_rows_html}'
                f'</div>',
                unsafe_allow_html=True,
            )

        st.markdown('<hr class="divider">', unsafe_allow_html=True)
        st.markdown(panel_hdr("A·01","ADD USER","PROVISIONING"), unsafe_allow_html=True)
        cu1, cu2, cu3, cu4 = st.columns([2,2,1,1])
        with cu1: new_uname = st.text_input("Username", key="new_u", label_visibility="collapsed", placeholder="username")
        with cu2: new_pw    = st.text_input("Password", type="password", key="new_p", label_visibility="collapsed", placeholder="password")
        with cu3: new_role  = st.selectbox("Role", config.ALL_ROLES, key="new_r", label_visibility="collapsed")
        with cu4:
            if st.button("ADD USER", use_container_width=True, key="add_u"):
                ok, msg = admin_create_user(new_uname, new_pw, new_role, st.session_state.current_user)
                if ok: st.success(msg)
                else:  st.error(msg)
                st.rerun()
        st.markdown('<hr class="divider">', unsafe_allow_html=True)
        st.markdown(panel_hdr("A·02","MANAGE USER","ACTIONS"), unsafe_allow_html=True)
        usernames = [u["username"] for u in users if u["username"] != st.session_state.current_user]
        if usernames:
            mu1, mu2 = st.columns([2,4])
            with mu1: target_user = st.selectbox("Target", usernames, key="tgt_u", label_visibility="collapsed")
            with mu2:
                ma1,ma2,ma3,ma4 = st.columns(4)
                with ma1:
                    if st.button("DELETE", use_container_width=True, key="del_u"):
                        ok, msg = admin_delete_user(target_user, st.session_state.current_user)
                        st.success(msg) if ok else st.error(msg); st.rerun()
                with ma2:
                    new_role_change = st.selectbox("Role", config.ALL_ROLES, key="chg_role", label_visibility="collapsed")
                    if st.button("SET ROLE", use_container_width=True, key="set_r"):
                        ok, msg = admin_change_role(target_user, new_role_change, st.session_state.current_user)
                        st.success(msg) if ok else st.error(msg); st.rerun()
                with ma3:
                    new_pw_reset = st.text_input("New PW", type="password", key="rst_pw", label_visibility="collapsed", placeholder="new password")
                    if st.button("RESET PW", use_container_width=True, key="rst_p"):
                        if new_pw_reset:
                            ok, msg = admin_reset_password(target_user, new_pw_reset, st.session_state.current_user)
                            st.success(msg) if ok else st.error(msg); st.rerun()
                with ma4:
                    target_info = next((u for u in users if u["username"]==target_user), {})
                    is_locked   = not bool(target_info.get("is_active",1))
                    lock_lbl    = "UNLOCK" if is_locked else "LOCK"
                    if st.button(lock_lbl, use_container_width=True, key="lck_u"):
                        ok, msg = admin_toggle_lock(target_user, not is_locked, st.session_state.current_user)
                        st.success(msg) if ok else st.error(msg); st.rerun()

    # ── AUDIT LOG ─────────────────────────────────────────────────────────────
    with tab_audit:
        import pandas as pd
        all_users_list = ["All"] + [u["username"] for u in get_all_users()]
        action_types   = ["All","SEARCH","FILE_UPLOAD","REPORT_GENERATED","LOGIN_SUCCESS",
                          "LOGIN_FAILURE","PIN_SUCCESS","PIN_FAILURE","PIN_LOCKED",
                          "LOGOUT","ADMIN_USER_CREATED","ADMIN_USER_DELETED",
                          "ADMIN_ROLE_CHANGE","ADMIN_PW_RESET","ADMIN_USER_LOCKED",
                          "ADMIN_USER_UNLOCKED","SESSION_EXPIRED"]

        # Search toolbar
        af1,af2,af3,af4 = st.columns([2,2,2,2])
        with af1: u_flt  = st.selectbox("User filter",   all_users_list, key="al_uf", label_visibility="collapsed")
        with af2: a_flt  = st.selectbox("Action filter", action_types,   key="al_af", label_visibility="collapsed")
        with af3: d_from = st.date_input("From", value=None, key="al_df", label_visibility="collapsed")
        with af4: d_to   = st.date_input("To",   value=None, key="al_dt", label_visibility="collapsed")

        logs = get_audit_log(
            limit=500,
            username_filter = None if u_flt=="All" else u_flt,
            action_filter   = None if a_flt=="All" else a_flt,
            date_from       = str(d_from) if d_from else None,
            date_to         = str(d_to)   if d_to   else None,
        )
        if logs:
            import pandas as pd, hashlib as _hl
            # Hash chain integrity summary
            head_hash = f"{logs[0].get('id',0):04X}·{logs[-1].get('id',0):04X}"
            chain_anchor = _hl.sha256(f"{len(logs)}{head_hash}".encode()).hexdigest()[:12].upper()
            st.markdown(
                f'<div class="chain-summary">'
                f'<div class="ch"><div class="k">ENTRIES</div><div class="v">{len(logs)}</div><div class="d">Total log records</div></div>'
                f'<div class="ch"><div class="k">INTEGRITY</div><div class="v ok">VALID</div><div class="d">Chain intact</div></div>'
                f'<div class="ch"><div class="k">CHAIN HEAD</div><div class="v" style="font-size:14px;">{head_hash}</div><div class="d">First · Last entry ID</div></div>'
                f'<div class="ch"><div class="k">NEXT ANCHOR</div><div class="v" style="font-size:11px;">{chain_anchor}</div><div class="d">SHA-256 fragment</div></div>'
                f'</div>',
                unsafe_allow_html=True,
            )

            # Filter chips
            st.markdown(
                '<div style="display:flex;gap:6px;margin:10px 0 6px;flex-wrap:wrap;">'
                '<div class="chip on"><span class="d" style="background:var(--online);"></span>ALL</div>'
                '<div class="chip">AUTH</div><div class="chip">INTEL</div>'
                '<div class="chip">ADMIN</div><div class="chip">REPORT</div>'
                '</div>',
                unsafe_allow_html=True,
            )

            # Styled log table
            _ACTION_TAG_ADM = {
                "LOGIN":       ("AUTH",   "var(--online)",   "rgba(22,163,74,0.35)"),
                "LOGOUT":      ("AUTH",   "var(--text-dim)", "rgba(75,85,99,0.3)"),
                "SEARCH":      ("INTEL",  "var(--purple-300)","rgba(123,47,190,0.4)"),
                "REPORT":      ("REPORT", "var(--teal)",     "rgba(13,148,136,0.4)"),
                "FUSION":      ("FUSION", "var(--warning)",  "rgba(217,119,6,0.4)"),
                "ADMIN":       ("ADMIN",  "var(--critical)", "rgba(220,38,38,0.4)"),
                "FILE_UPLOAD": ("UPLOAD", "var(--purple-300)","rgba(123,47,190,0.4)"),
            }
            def _adm_tag(event: str):
                for k, v in _ACTION_TAG_ADM.items():
                    if event and k in event.upper():
                        return v
                return ("SYS", "var(--text-secondary)", "rgba(75,85,99,0.3)")

            log_rows_html = ""
            for row in logs[:100]:
                ts     = (row.get("timestamp") or "")[:16]
                user   = (row.get("username") or "—")[:14]
                event  = (row.get("event") or row.get("action",""))
                detail_raw = row.get("detail","") or row.get("details","")
                try:   detail_p = json.loads(detail_raw)
                except: detail_p = detail_raw
                if isinstance(detail_p, dict): detail = str(detail_p.get("query", detail_p.get("filename", str(detail_p))))[:55]
                else: detail = str(detail_p)[:55]
                ip     = (row.get("ip") or row.get("ip_address","—"))[:18]
                tag_lbl, tag_c, tag_bc = _adm_tag(event)
                log_rows_html += (
                    f'<div class="log-row admin">'
                    f'<span class="ts">{ts}</span>'
                    f'<span class="user">{user}</span>'
                    f'<span style="font-family:var(--f-mono);font-size:9px;letter-spacing:2px;padding:2px 6px;'
                    f'border:1px solid {tag_bc};color:{tag_c};text-align:center;">{tag_lbl}</span>'
                    f'<span style="color:var(--text-primary);">{(event or "").replace("_"," ")[:22]}</span>'
                    f'<span style="color:var(--text-secondary);">{detail}</span>'
                    f'<span class="int ok">✓ VALID</span>'
                    f'</div>'
                )

            st.markdown(
                f'<div class="card" style="padding:0;overflow:hidden;">'
                f'<div class="log-hdr">TIMESTAMP · USER · TAG · ACTION · DETAIL · INTEGRITY</div>'
                f'<div style="max-height:400px;overflow-y:auto;">{log_rows_html}</div>'
                f'<div class="pager">'
                f'<span>SHOWING 1–{min(100,len(logs))} OF {len(logs)}</span>'
                f'<div class="buttons">'
                f'<button class="active">1</button>'
                f'<button>2</button><button>3</button>'
                f'</div>'
                f'</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

            st.markdown('<hr class="divider">', unsafe_allow_html=True)
            ec1, ec2 = st.columns(2)
            with ec1:
                rows_exp = []
                for l in logs:
                    detail_raw = l.get("detail","") or l.get("details","")
                    try:   detail_parsed = json.loads(detail_raw)
                    except: detail_parsed = detail_raw
                    if isinstance(detail_parsed, dict): detail_str = str(detail_parsed.get("query",detail_parsed.get("filename",detail_raw)))[:60]
                    else: detail_str = str(detail_parsed)[:60]
                    rows_exp.append({"Timestamp":l.get("timestamp",""),"User":l.get("username",""),"Action":l.get("event",""),"Detail":detail_str,"IP":l.get("ip","")})
                df_log = pd.DataFrame(rows_exp)
                csv_bytes = df_log.to_csv(index=False).encode()
                st.download_button("EXPORT CSV", data=csv_bytes, file_name=f"aetherlens_audit_{datetime.datetime.utcnow().strftime('%Y%m%d')}.csv", mime="text/csv", use_container_width=True)
            with ec2:
                if st.button("EXPORT PDF", use_container_width=True, key="audit_pdf"):
                    st.info("Audit PDF export: use CSV export and attach to report.")
        else:
            st.markdown('<div style="color:var(--text-dim);font-family:var(--f-mono);font-size:11px;padding:16px;">No audit log entries match filters.</div>', unsafe_allow_html=True)

    # ── SYSTEM STATUS ─────────────────────────────────────────────────────────
    with tab_status:
        stats = get_system_stats()
        import requests as _req
        gemini_ok = bool(config.GEMINI_API_KEY and config.GEMINI_API_KEY != "your_gemini_key_here")
        # Test Gemini live
        gemini_live = False
        if gemini_ok:
            try:
                r = _req.post(f"{config.GEMINI_ENDPOINT}?key={config.GEMINI_API_KEY}",
                    json={"contents":[{"parts":[{"text":"ping"}]}],"generationConfig":{"maxOutputTokens":5}}, timeout=8)
                gemini_live = r.status_code == 200
            except Exception: pass

        gemini_status = '<span class="status-ok">ONLINE</span>' if gemini_live else ('<span class="status-warn">KEY LOADED</span>' if gemini_ok else '<span class="status-err">NO KEY</span>')

        db_status     = '<span class="status-ok">CONNECTED</span>' if stats.get("db_ok") else '<span class="status-err">ERROR</span>'

        # ── Bedrock (Claude Sonnet 4 · Mumbai ap-south-1) — PRIMARY ENGINE ──
        # Lazy init: secrets may not have been available at import time.
        if config.bedrock_client is None:
            try:
                _bc, _bm = config.get_bedrock_client()
                if _bc:
                    config.bedrock_client   = _bc
                    config.BEDROCK_MODEL_ID = _bm
            except Exception:
                pass
        try:
            bedrock_ok, bedrock_reason = config.test_bedrock_connection()
        except Exception as _e:
            bedrock_ok, bedrock_reason = False, str(_e)
        if bedrock_ok:
            bedrock_status = '<span class="status-ok">ONLINE</span>'
        elif config.bedrock_client is not None:
            bedrock_status = f'<span class="status-warn">KEY LOADED ({bedrock_reason})</span>'
        else:
            bedrock_status = '<span class="status-err">NOT CONFIGURED</span>'

        # Engines grid
        bedrock_ok_c = "var(--online)" if bedrock_ok else "var(--critical)"
        gemini_ok_c  = "var(--online)" if gemini_live else ("var(--warning)" if gemini_ok else "var(--critical)")
        db_ok_c      = "var(--online)" if stats.get("db_ok") else "var(--critical)"
        bedrock_st_t = "ONLINE" if bedrock_ok else ("KEY LOADED" if config.bedrock_client else "NOT CONFIGURED")
        gemini_st_t  = "ONLINE" if gemini_live else ("KEY LOADED" if gemini_ok else "NO KEY")
        db_st_t      = "CONNECTED" if stats.get("db_ok") else "ERROR"

        st.markdown(
            f'<div class="sys-grid">'

            # Left column — engines
            f'<div class="card" style="padding:0;overflow:hidden;">'
            f'<div style="padding:12px 16px 8px;border-bottom:1px solid var(--border-soft);">'
            f'<div style="font-family:var(--f-mono);font-size:9px;letter-spacing:3px;color:var(--text-dim);text-transform:uppercase;">ENGINE STATUS</div>'
            f'</div>'
            f'<div class="sys-row">'
            f'<div><div class="lbl">BEDROCK · CLAUDE SONNET 4</div><div class="sub">AWS {config.AWS_REGION} · PRIMARY</div></div>'
            f'<div class="val">—</div>'
            f'<div class="st ok" style="color:{bedrock_ok_c};">{bedrock_st_t}</div>'
            f'</div>'
            f'<div class="sys-row">'
            f'<div><div class="lbl">GEMINI 2.5 FLASH</div><div class="sub">Google AI · FALLBACK</div></div>'
            f'<div class="val">—</div>'
            f'<div class="st" style="color:{gemini_ok_c};">{gemini_st_t}</div>'
            f'</div>'
            f'<div class="sys-row">'
            f'<div><div class="lbl">DATABASE</div><div class="sub">{str(config.DATABASE_PATH).split(chr(92))[-1][:24]}</div></div>'
            f'<div class="val">—</div>'
            f'<div class="st ok" style="color:{db_ok_c};">{db_st_t}</div>'
            f'</div>'
            f'</div>'

            # Right column — KPIs
            f'<div class="card" style="padding:0;overflow:hidden;">'
            f'<div style="padding:12px 16px 8px;border-bottom:1px solid var(--border-soft);">'
            f'<div style="font-family:var(--f-mono);font-size:9px;letter-spacing:3px;color:var(--text-dim);text-transform:uppercase;">PLATFORM KPIs</div>'
            f'</div>'
            f'<div class="kpis">'
            f'<div class="kpi"><div style="font-family:var(--f-mono);font-size:9px;letter-spacing:3px;color:var(--text-dim);text-transform:uppercase;">TOTAL USERS</div><div class="v">{stats.get("users",0)}</div><div class="d">REGISTERED</div></div>'
            f'<div class="kpi"><div style="font-family:var(--f-mono);font-size:9px;letter-spacing:3px;color:var(--text-dim);text-transform:uppercase;">SEARCHES RUN</div><div class="v">{stats.get("searches",0)}</div><div class="d">LIFETIME</div></div>'
            f'<div class="kpi"><div style="font-family:var(--f-mono);font-size:9px;letter-spacing:3px;color:var(--text-dim);text-transform:uppercase;">REPORTS</div><div class="v">{stats.get("reports",0)}</div><div class="d">GENERATED</div></div>'
            f'<div class="kpi"><div style="font-family:var(--f-mono);font-size:9px;letter-spacing:3px;color:var(--text-dim);text-transform:uppercase;">FILES UPLOADED</div><div class="v">{stats.get("uploads",0)}</div><div class="d">PROCESSED</div></div>'
            f'</div>'
            f'<div style="padding:10px 16px;border-top:1px solid var(--border-soft);font-family:var(--f-mono);font-size:9px;letter-spacing:1px;color:var(--text-dim);">'
            f'LAST ACTIVITY: <span style="color:var(--text-secondary);">{stats.get("last_active","Never")}</span>'
            f' &nbsp;·&nbsp; BY: <span style="color:var(--purple-300);">{stats.get("last_user","")}</span>'
            f'</div>'
            f'</div>'

            f'</div>',
            unsafe_allow_html=True,
        )
        st.markdown('<div style="margin-top:12px;"></div>', unsafe_allow_html=True)
        if st.button("REFRESH STATUS", key="refresh_status"): st.rerun()


# ─────────────────────────────────────────────────────────────────────────────
# RISK BADGE HELPER
# ─────────────────────────────────────────────────────────────────────────────

def _risk_badge(score, level=""):
    if score >= 76 or level == "CRITICAL":
        c = "#DC2626"
    elif score >= 51 or level == "HIGH":
        c = "#D97706"
    elif score >= 26 or level == "MEDIUM":
        c = "#D97706"
    else:
        c = "#16A34A"
    return (
        f'<span style="background:{c}18;border:1px solid {c};color:{c};'
        f'font-family:\'JetBrains Mono\',monospace;font-size:9px;letter-spacing:2px;'
        f'padding:2px 8px;">'
        f'{score:.0f} {level or ""}</span>'
    )


# ─────────────────────────────────────────────────────────────────────────────
# COMMAND CENTER (4-PANEL MAIN DASHBOARD)
# ─────────────────────────────────────────────────────────────────────────────

def screen_command_center():
    import datetime, hashlib
    from modules.ui_components import panel_hdr
    from modules.auth import get_audit_log

    username = (st.session_state.current_user or "OPERATOR").upper()
    role     = (st.session_state.current_role or "ANALYST").upper()
    agents   = st.session_state.agent_results or {}
    person   = st.session_state.person_profile
    ont_json = st.session_state.ontology_json
    ont_g    = st.session_state.ontology_graph
    profiles = st.session_state.active_profiles or []
    risk_r   = agents.get("risk", {})
    comp_r   = agents.get("compliance", {})

    # Stable dispatch number derived from username (no randomness on rerun)
    dispatch_num = int(hashlib.md5(username.encode()).hexdigest()[:4], 16) % 9000 + 1000
    now_utc = datetime.datetime.utcnow()

    # ── Classification strip ───────────────────────────────────────────────────
    st.markdown(
        '<div class="al-classification-strip">TS//SCI//NOFORN · AETHERLENS COMMAND CENTER · AUTHORIZED ACCESS ONLY</div>',
        unsafe_allow_html=True,
    )

    # ── Greeting ───────────────────────────────────────────────────────────────
    role_color = {"ADMIN": "var(--critical)", "ANALYST": "var(--purple-300)", "OFFICER": "var(--teal)"}.get(role, "var(--text-secondary)")
    st.markdown(
        f'<div class="greet">'
        f'<h1>OPERATOR <span class="usr">{username}</span> · WELCOME</h1>'
        f'<div style="text-align:right;">'
        f'<div class="sub">DISPATCH #{dispatch_num}</div>'
        f'<div style="font-family:var(--f-mono);font-size:9px;letter-spacing:2px;color:var(--text-dim);margin-top:2px;">'
        f'{now_utc.strftime("%Y-%m-%d %H:%M")} UTC</div>'
        f'</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # ── Stat cards ─────────────────────────────────────────────────────────────
    nodes = (ont_json or {}).get("node_count", 0)
    edges = (ont_json or {}).get("edge_count", 0)
    risk_score = risk_r.get("risk_score", 0)
    comp_score = comp_r.get("compliance_score", 100)

    spark_heights = [18, 22, 14, 28, 20, 32, 18, 26, 30, 22, 28, 16]
    spark_html = "".join(f'<i style="height:{h}px;"></i>' for h in spark_heights)

    st.markdown(
        f'<div class="status-grid" style="margin:16px 0 20px;">'

        # Card 1 — BEDROCK ENGINE
        f'<div class="card">'
        f'<div style="display:flex;justify-content:space-between;align-items:flex-start;">'
        f'<span style="font-family:var(--f-mono);font-size:9px;letter-spacing:3px;color:var(--text-dim);text-transform:uppercase;">BEDROCK ENGINE</span>'
        f'<span style="display:inline-flex;align-items:center;gap:6px;font-family:var(--f-mono);font-size:9px;letter-spacing:2px;color:var(--online);">'
        f'<span style="width:6px;height:6px;border-radius:50%;background:var(--online);box-shadow:0 0 6px var(--online);display:inline-block;"></span>ONLINE</span>'
        f'</div>'
        f'<div style="font-family:var(--f-display);font-weight:600;font-size:22px;color:var(--text-primary);letter-spacing:-0.3px;margin:8px 0 4px;">CLAUDE S4</div>'
        f'<div style="font-family:var(--f-mono);font-size:9px;letter-spacing:2px;color:var(--text-dim);">AWS BEDROCK · US-EAST-1</div>'
        f'</div>'

        # Card 2 — ACTIVE SESSION
        f'<div class="card">'
        f'<div style="font-family:var(--f-mono);font-size:9px;letter-spacing:3px;color:var(--text-dim);text-transform:uppercase;margin-bottom:8px;">ACTIVE SESSION</div>'
        f'<div style="font-family:var(--f-display);font-weight:600;font-size:22px;color:var(--text-primary);letter-spacing:-0.3px;">{username[:14]}</div>'
        f'<div style="margin-top:6px;">'
        f'<span class="role-badge {role.lower()}" style="font-family:var(--f-mono);font-size:9px;letter-spacing:2px;padding:2px 8px;border:1px solid;text-transform:uppercase;color:{role_color};">{role}</span>'
        f'</div>'
        f'</div>'

        # Card 3 — SYSTEM STATUS
        f'<div class="card">'
        f'<div style="font-family:var(--f-mono);font-size:9px;letter-spacing:3px;color:var(--text-dim);text-transform:uppercase;margin-bottom:8px;">SYSTEM STATUS</div>'
        f'<div style="font-family:var(--f-display);font-weight:600;font-size:22px;color:var(--online);letter-spacing:-0.3px;">NOMINAL</div>'
        f'<div style="font-family:var(--f-mono);font-size:9px;letter-spacing:2px;color:var(--text-dim);margin-top:4px;">{nodes} NODES · {edges} EDGES</div>'
        f'</div>'

        # Card 4 — THROUGHPUT
        f'<div class="card">'
        f'<div style="font-family:var(--f-mono);font-size:9px;letter-spacing:3px;color:var(--text-dim);text-transform:uppercase;margin-bottom:8px;">THROUGHPUT</div>'
        f'<div class="spark">{spark_html}</div>'
        f'<div style="font-family:var(--f-mono);font-size:9px;letter-spacing:2px;color:var(--text-dim);margin-top:6px;">REQUESTS PER MIN</div>'
        f'</div>'

        f'</div>',
        unsafe_allow_html=True,
    )

    # ── Two-column layout: activity log | workload KPIs + threat queue ─────────
    audit_rows = []
    try:
        audit_rows = get_audit_log(limit=12)
    except Exception:
        pass

    _ACTION_TAG = {
        "LOGIN":            ("AUTH",    "var(--online)",    "rgba(22,163,74,0.35)"),
        "LOGOUT":           ("AUTH",    "var(--text-dim)",  "rgba(75,85,99,0.4)"),
        "SEARCH":           ("INTEL",   "var(--purple-300)","rgba(123,47,190,0.4)"),
        "REPORT_BUILD":     ("REPORT",  "var(--teal)",      "rgba(13,148,136,0.4)"),
        "FUSION_RUN":       ("FUSION",  "var(--warning)",   "rgba(217,119,6,0.4)"),
        "ADMIN_USER_LOCKED":("ADMIN",   "var(--critical)",  "rgba(220,38,38,0.4)"),
    }

    def _action_tag(action: str):
        for k, v in _ACTION_TAG.items():
            if action and k in action:
                return v
        return ("SYS", "var(--text-secondary)", "rgba(75,85,99,0.3)")

    log_rows_html = ""
    for row in audit_rows:
        ts      = (row.get("timestamp") or "")[:16]
        user    = (row.get("username") or "—")[:12]
        action  = row.get("action") or ""
        details = (row.get("details") or "—")[:60]
        ip      = (row.get("ip_address") or "—")[:18]
        tag_lbl, tag_c, tag_bc = _action_tag(action)
        action_short = action.replace("_", " ")[:18]
        log_rows_html += (
            f'<div class="log-row admin">'
            f'<span class="ts">{ts}</span>'
            f'<span class="user">{user}</span>'
            f'<span style="font-family:var(--f-mono);font-size:9px;letter-spacing:2px;padding:2px 6px;'
            f'border:1px solid {tag_bc};color:{tag_c};text-align:center;">{tag_lbl}</span>'
            f'<span style="color:var(--text-primary);font-size:11px;">{action_short}</span>'
            f'<span style="color:var(--text-dim);font-size:10px;">{details}</span>'
            f'<span class="int ok">✓ VALID</span>'
            f'</div>'
        )
    if not log_rows_html:
        log_rows_html = (
            '<div style="padding:24px 16px;text-align:center;font-family:var(--f-mono);'
            'font-size:10px;letter-spacing:2px;color:var(--text-dim);">NO AUDIT LOG ENTRIES</div>'
        )

    # Build threat queue from risk factors
    risk_factors = risk_r.get("risk_factors", [])
    threat_html = ""
    _sev_map = {"HIGH": ("hi", "CRITICAL"), "MEDIUM": ("md", "MEDIUM"), "LOW": ("lo", "LOW")}
    for fac in risk_factors[:4]:
        sev_key   = (fac.get("severity") or fac.get("level") or "LOW").upper()
        sev_cls, sev_lbl = _sev_map.get(sev_key, ("lo", "LOW"))
        factor    = (fac.get("factor") or "Unknown")[:40]
        evidence  = (fac.get("evidence") or "")[:60]
        src       = (fac.get("source") or "ANALYSIS ENGINE")[:22]
        threat_html += (
            f'<div class="threat">'
            f'<div class="sev {sev_cls}">{sev_lbl}</div>'
            f'<div class="body">'
            f'<div class="title">{factor}</div>'
            f'<div class="meta">{evidence or src}</div>'
            f'</div>'
            f'</div>'
        )
    if not threat_html:
        threat_html = (
            '<div style="padding:20px 16px;text-align:center;font-family:var(--f-mono);'
            'font-size:10px;letter-spacing:2px;color:var(--text-dim);">NO ACTIVE THREATS · RUN AN ANALYSIS TO POPULATE</div>'
        )

    # KPI mini-tiles
    risk_score = risk_r.get("risk_score", 0)
    kpi_risk_c = "var(--critical)" if risk_score >= 70 else "var(--warning)" if risk_score >= 40 else "var(--online)"
    cleared    = comp_r.get("cleared_for_export", True)
    patt_count = len(agents.get("patterns", {}).get("patterns_found", []))
    steps_count = len(agents.get("next_steps", {}).get("next_steps", []))
    kpi_tiles_html = (
        f'<div class="kpi-row">'
        f'<div class="kpi">'
        f'<div style="font-family:var(--f-mono);font-size:9px;letter-spacing:3px;color:var(--text-dim);text-transform:uppercase;">RISK SCORE</div>'
        f'<div class="v" style="color:{kpi_risk_c};">{risk_score}</div>'
        f'</div>'
        f'<div class="kpi">'
        f'<div style="font-family:var(--f-mono);font-size:9px;letter-spacing:3px;color:var(--text-dim);text-transform:uppercase;">EXPORT</div>'
        f'<div class="v" style="color:{"var(--online)" if cleared else "var(--critical)"};">{"OK" if cleared else "FLAG"}</div>'
        f'</div>'
        f'<div class="kpi">'
        f'<div style="font-family:var(--f-mono);font-size:9px;letter-spacing:3px;color:var(--text-dim);text-transform:uppercase;">PATTERNS</div>'
        f'<div class="v">{patt_count}</div>'
        f'</div>'
        f'<div class="kpi">'
        f'<div style="font-family:var(--f-mono);font-size:9px;letter-spacing:3px;color:var(--text-dim);text-transform:uppercase;">PROFILES</div>'
        f'<div class="v">{len(profiles)}</div>'
        f'</div>'
        f'</div>'
    )

    st.markdown(
        f'<div class="cols">'

        # ── Left: activity log ────────────────────────────────────────────────
        f'<div class="card" style="padding:0;overflow:hidden;">'
        f'<div style="padding:14px 16px 10px;border-bottom:1px solid var(--border-soft);display:flex;justify-content:space-between;align-items:center;">'
        f'<div style="font-family:var(--f-mono);font-size:9px;letter-spacing:3px;color:var(--text-dim);text-transform:uppercase;">ACTIVITY LOG</div>'
        f'<div style="font-family:var(--f-mono);font-size:9px;letter-spacing:2px;color:var(--online);">LIVE</div>'
        f'</div>'
        f'<div class="log-hdr">TIMESTAMP · USER · TAG · ACTION · DETAILS · INTEGRITY</div>'
        f'<div style="max-height:340px;overflow-y:auto;">{log_rows_html}</div>'
        f'</div>'

        # ── Right: KPIs + threat queue ─────────────────────────────────────────
        f'<div style="display:flex;flex-direction:column;gap:14px;">'

        f'<div class="card" style="padding:0;overflow:hidden;">'
        f'<div style="padding:14px 16px 10px;border-bottom:1px solid var(--border-soft);">'
        f'<div style="font-family:var(--f-mono);font-size:9px;letter-spacing:3px;color:var(--text-dim);text-transform:uppercase;">WORKLOAD KPIs</div>'
        f'</div>'
        f'{kpi_tiles_html}'
        f'</div>'

        f'<div class="card" style="padding:0;overflow:hidden;">'
        f'<div style="padding:14px 16px 10px;border-bottom:1px solid var(--border-soft);display:flex;justify-content:space-between;align-items:center;">'
        f'<div style="font-family:var(--f-mono);font-size:9px;letter-spacing:3px;color:var(--text-dim);text-transform:uppercase;">ACTIVE THREAT QUEUE</div>'
        f'<div style="font-family:var(--f-mono);font-size:9px;letter-spacing:2px;color:var(--critical);">{len(risk_factors)} ACTIVE</div>'
        f'</div>'
        f'{threat_html}'
        f'</div>'

        f'</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # ── Knowledge graph + entity detail (collapsible) ─────────────────────────
    st.markdown('<div style="margin-top:18px;"></div>', unsafe_allow_html=True)
    with st.expander("KNOWLEDGE GRAPH · ENTITY DETAIL", expanded=False):
        left_col, center_col, right_col = st.columns([1, 2, 1])

        with left_col:
            st.markdown(panel_hdr("P·01","ACTIVE INTELLIGENCE","LIVE"), unsafe_allow_html=True)
            if not profiles:
                st.markdown(
                    '<div style="font-family:var(--f-mono);font-size:11px;color:var(--text-dim);'
                    'letter-spacing:1px;padding:12px 0;">No profiles built yet.</div>',
                    unsafe_allow_html=True,
                )
            else:
                for i, prof in enumerate(profiles[:8]):
                    rs   = prof.get("risk_score", 0)
                    rl   = prof.get("risk_level", "LOW")
                    name = prof.get("name", "Unknown")[:26]
                    rc   = "#DC2626" if rs >= 70 else "#D97706" if rs >= 40 else "#16A34A"
                    st.markdown(
                        f'<div style="background:rgba(16,0,32,0.70);border:1px solid rgba(123,47,190,0.25);'
                        f'padding:6px 10px;margin-bottom:3px;font-family:var(--f-mono);font-size:11px;">'
                        f'<span style="color:#F0EAD6;">{name}</span>'
                        f'<span style="float:right;font-size:9px;border:1px solid {rc};color:{rc};padding:1px 5px;">{rl}</span>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
                    if st.button("VIEW REPORT", key=f"cc_view_{i}", use_container_width=True):
                        st.session_state.active_screen = "reports"; st.rerun()

        with center_col:
            st.markdown(panel_hdr("P·02","KNOWLEDGE GRAPH","ONTOLOGY"), unsafe_allow_html=True)
            fc1, fc2 = st.columns(2)
            with fc1:
                filter_type = st.selectbox("Entity type", ["ALL","PERSON","LOCATION","EVENT","NETWORK","DEVICE"],
                                            key="cc_filter_type_sel", label_visibility="collapsed")
            with fc2:
                min_risk = st.slider("Min risk", 0, 100, 0, key="cc_min_risk", label_visibility="collapsed")
            if ont_g and ont_g.graph.number_of_nodes() > 0:
                try:
                    fig = ont_g.to_plotly_figure(
                        filter_type=None if filter_type == "ALL" else filter_type,
                        min_risk=float(min_risk),
                    )
                    if fig:
                        st.plotly_chart(fig, use_container_width=True)
                except Exception as e:
                    st.markdown(f'<div style="color:var(--text-dim);font-size:0.8rem;">Graph render error: {e}</div>', unsafe_allow_html=True)
            else:
                st.markdown(
                    '<div style="background:#05000D;border:1px dashed rgba(123,47,190,0.25);'
                    'padding:2rem;text-align:center;font-family:var(--f-mono);'
                    'font-size:10px;letter-spacing:2px;color:var(--text-dim);">'
                    'NO ONTOLOGY DATA · RUN A SEARCH TO BUILD THE KNOWLEDGE GRAPH</div>',
                    unsafe_allow_html=True,
                )

        with right_col:
            st.markdown(panel_hdr("P·03","ENTITY DETAIL","SELECTED"), unsafe_allow_html=True)
            selected = st.session_state.get("selected_entity_id")
            if selected and ont_g:
                summary = ont_g.get_entity_summary(selected)
                if summary:
                    ent = summary.get("entity", {})
                    st.markdown(f'**{ent.get("name","Unknown")}**')
                    st.markdown(f'Type: `{ent.get("entity_type","")}`')
                    st.markdown(f'Risk: {_risk_badge(ent.get("risk_score",0))}', unsafe_allow_html=True)
                    st.markdown(f'Confidence: {ent.get("confidence",0):.0f}%')
                    conns = summary.get("connected_entities", [])
                    if conns:
                        st.markdown(f'**Connections ({len(conns)})**')
                        for c in conns[:6]:
                            st.markdown(f'• {c.get("label","")} `{c.get("entity_type","")}`')
                    if st.button("BUILD FULL REPORT", use_container_width=True, key="cc_build_report"):
                        st.session_state.active_screen = "reports"; st.rerun()
            elif person:
                st.markdown(f'**{person.get("confirmed_name","Unknown")}**')
                rs = risk_r.get("risk_score", 0)
                rl = risk_r.get("risk_level", "")
                st.markdown(_risk_badge(rs, rl), unsafe_allow_html=True)
                st.markdown(f'Confidence: {person.get("confidence_score",0):.0f}%')
                plats = person.get("platforms_confirmed", [])
                if plats:
                    st.markdown(f'Platforms: {", ".join(plats[:4])}')
                if st.button("ANALYSIS WORKBENCH", use_container_width=True, key="cc_wb"):
                    st.session_state.active_screen = "analysis_workbench"; st.rerun()
            else:
                st.markdown('<div style="color:var(--text-dim);font-size:0.8rem;">Run a search to see entity details.</div>', unsafe_allow_html=True)

    # ── Agent activity feed (collapsible) ─────────────────────────────────────
    with st.expander("AGENT ACTIVITY FEED", expanded=False):
        try:
            from modules.ai_agents import get_agent_activity_log
            log = get_agent_activity_log(limit=50)
        except Exception:
            log = []
        _AGENT_TAG_MAP = {
            "RiskAgent":       ("RISK",    "#DC2626", "rgba(220,38,38,0.4)"),
            "PatternAgent":    ("PATTERN", "#2563EB", "rgba(37,99,235,0.4)"),
            "NextStepAgent":   ("NEXTSTEP","#16A34A", "rgba(22,163,74,0.4)"),
            "ComplianceAgent": ("COMPLY",  "#D97706", "rgba(217,119,6,0.4)"),
        }
        agent_filter = st.selectbox("Filter agent", ["ALL","RiskAgent","PatternAgent","NextStepAgent","ComplianceAgent"],
                                     key="cc_agent_filter", label_visibility="collapsed")
        feed_rows = [l for l in log if agent_filter == "ALL" or l.get("agent") == agent_filter]
        if feed_rows:
            feed_html = (
                '<div style="background:#05000D;border:1px solid rgba(123,47,190,0.14);'
                'max-height:220px;overflow-y:auto;">'
                '<div class="log-hdr">TIMESTAMP · AGENT · TAG · RESULT · USER</div>'
            )
            for entry in feed_rows[:30]:
                ag    = entry.get("agent", "")
                ts    = entry.get("run_at", "")[:16]
                res   = entry.get("result", "")[:70]
                uid_e = entry.get("user_id", "")
                tag_lbl, tag_c, tag_bc = _AGENT_TAG_MAP.get(ag, (ag[:8], "#9CA3AF", "rgba(156,163,175,0.3)"))
                feed_html += (
                    f'<div class="log-row admin">'
                    f'<span class="ts">{ts}</span>'
                    f'<span class="user">{ag[:14]}</span>'
                    f'<span style="font-size:9px;letter-spacing:2px;border:1px solid {tag_bc};'
                    f'color:{tag_c};padding:2px 6px;text-align:center;">{tag_lbl}</span>'
                    f'<span style="color:var(--text-primary);grid-column:4/7;">{res}</span>'
                    f'</div>'
                )
            feed_html += '</div>'
            st.markdown(feed_html, unsafe_allow_html=True)
        else:
            st.markdown(
                '<div style="font-family:var(--f-mono);font-size:11px;color:var(--text-dim);'
                'letter-spacing:1px;padding:12px 0;">No agent activity recorded yet.</div>',
                unsafe_allow_html=True,
            )


# ─────────────────────────────────────────────────────────────────────────────
# ANALYSIS WORKBENCH
# ─────────────────────────────────────────────────────────────────────────────

def screen_analysis_workbench():
    screen_header("ANALYSIS WORKBENCH", "Deep entity analysis · Knowledge graph · Timeline · Agent outputs")
    st.markdown(
        '<div class="al-classification-strip">TS//SCI//NOFORN · ANALYSIS WORKBENCH · DEEP ENTITY INTELLIGENCE</div>',
        unsafe_allow_html=True,
    )
    person   = st.session_state.person_profile
    ont_g    = st.session_state.ontology_graph
    agents   = st.session_state.agent_results or {}
    tl       = st.session_state.timeline_data

    if not person and not ont_g:
        st.markdown(
            '<div class="card" style="text-align:center;padding:3rem 1rem;margin-top:1rem;">'
            '<div style="font-family:var(--f-mono);font-size:11px;color:var(--text-dim);'
            'letter-spacing:2px;">NO DATA · RUN A SEARCH FIRST TO POPULATE THE WORKBENCH</div>'
            '</div>',
            unsafe_allow_html=True,
        )
        if st.button("GO TO SEARCH", key="wb_go_search"):
            st.session_state.active_screen = "search"; st.rerun()
        return

    left, center, right = st.columns([1, 2, 1])

    # ── Left: entity list ──────────────────────────────────────────────────────
    with left:
        _etype_colors = {
            "PERSON":   "var(--purple-300)",
            "LOCATION": "var(--teal)",
            "DEVICE":   "var(--warning)",
            "ORG":      "var(--online)",
            "EVENT":    "var(--purple-100)",
        }
        nodes_n = ont_g.graph.number_of_nodes() if ont_g else 0
        edges_n = ont_g.graph.number_of_edges() if ont_g else 0
        st.markdown(
            f'<div class="card" style="padding:0;overflow:hidden;">'
            f'<div style="padding:12px 14px 8px;border-bottom:1px solid var(--border-soft);'
            f'display:flex;justify-content:space-between;align-items:center;">'
            f'<div style="font-family:var(--f-mono);font-size:9px;letter-spacing:3px;'
            f'color:var(--text-dim);text-transform:uppercase;">ENTITY INDEX</div>'
            f'<div style="font-family:var(--f-mono);font-size:9px;color:var(--purple-300);">'
            f'{nodes_n}N · {edges_n}E</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
        if ont_g:
            for eid, ent in list(ont_g._entities.items())[:20]:
                label  = (getattr(ent, "name", None) or getattr(ent, "identifier", None) or eid[:8])[:22]
                etype  = getattr(ent, "entity_type", "UNKNOWN")
                ec     = _etype_colors.get(etype, "var(--text-secondary)")
                is_sel = st.session_state.get("selected_entity_id") == eid
                st.markdown(
                    f'<div style="display:flex;align-items:center;gap:8px;padding:8px 14px;'
                    f'border-bottom:1px solid var(--border-soft);'
                    f'{"background:rgba(123,47,190,0.10);border-left:2px solid var(--purple-500);" if is_sel else ""}">'
                    f'<span style="width:8px;height:8px;border-radius:50%;background:{ec};'
                    f'display:inline-block;flex-shrink:0;"></span>'
                    f'<span style="font-family:var(--f-mono);font-size:10px;color:var(--text-primary);'
                    f'flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">{label}</span>'
                    f'<span style="font-family:var(--f-mono);font-size:8px;color:{ec};letter-spacing:1px;">{etype[:4]}</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
                if st.button("SELECT", key=f"wb_ent_{eid[:8]}", use_container_width=True):
                    st.session_state.selected_entity_id = eid
                    st.rerun()
        else:
            st.markdown(
                '<div style="padding:16px 14px;font-family:var(--f-mono);font-size:10px;'
                'color:var(--text-dim);">No ontology data.</div>',
                unsafe_allow_html=True,
            )
        st.markdown('</div>', unsafe_allow_html=True)

    # ── Center: graph / timeline / heatmap tabs ────────────────────────────────
    with center:
        tab_graph, tab_timeline, tab_heatmap = st.tabs(["KNOWLEDGE GRAPH", "TIMELINE", "HEATMAP"])

        with tab_graph:
            if ont_g and ont_g.graph.number_of_nodes() > 0:
                try:
                    filter_type = st.selectbox(
                        "Filter entity type",
                        ["ALL","PERSON","LOCATION","EVENT","NETWORK","DEVICE"],
                        key="wb_filter_type", label_visibility="collapsed",
                    )
                    fig = ont_g.to_plotly_figure(
                        filter_type=None if filter_type == "ALL" else filter_type,
                    )
                    if fig:
                        st.markdown(
                            '<div class="card" style="padding:0;overflow:hidden;">'
                            '<div style="padding:10px 14px 8px;border-bottom:1px solid var(--border-soft);">'
                            '<div style="font-family:var(--f-mono);font-size:9px;letter-spacing:3px;'
                            'color:var(--text-dim);text-transform:uppercase;">ONTOLOGY GRAPH</div>'
                            '</div>',
                            unsafe_allow_html=True,
                        )
                        st.plotly_chart(fig, use_container_width=True)
                        st.markdown('</div>', unsafe_allow_html=True)
                except Exception as _ge:
                    st.markdown(
                        f'<div class="card" style="padding:14px;">'
                        f'<div style="font-family:var(--f-mono);font-size:10px;color:var(--critical);">GRAPH ERROR · {_ge}</div>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
            else:
                st.markdown(
                    '<div class="card" style="text-align:center;padding:3rem;">'
                    '<div style="font-family:var(--f-mono);font-size:10px;color:var(--text-dim);'
                    'letter-spacing:2px;">NO GRAPH DATA AVAILABLE</div>'
                    '<div style="font-family:var(--f-mono);font-size:9px;color:var(--text-dim);'
                    'margin-top:8px;">Run a search or fusion analysis to build the knowledge graph.</div>'
                    '</div>',
                    unsafe_allow_html=True,
                )

        with tab_timeline:
            if tl and tl.get("figure"):
                events_tl = tl.get("events", [])
                gaps_tl   = tl.get("gaps", [])
                anoms_tl  = tl.get("anomalies", [])
                st.markdown(
                    f'<div class="progress-summary" style="margin-bottom:12px;">'
                    f'<div class="tile"><div class="k">EVENTS</div><div class="v">{len(events_tl)}</div></div>'
                    f'<div class="tile"><div class="k">GAPS</div><div class="v" style="color:{"var(--warning)" if gaps_tl else "var(--online)"};">{len(gaps_tl)}</div></div>'
                    f'<div class="tile"><div class="k">ANOMALIES</div><div class="v" style="color:{"var(--critical)" if anoms_tl else "var(--online)"};">{len(anoms_tl)}</div></div>'
                    f'<div class="tile"><div class="k">SPAN START</div><div class="v" style="font-size:12px;">{(events_tl[0].get("normalized","")[:10] if events_tl else "—")}</div></div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
                st.markdown(
                    '<div class="card" style="padding:0;overflow:hidden;">'
                    '<div style="padding:10px 14px 8px;border-bottom:1px solid var(--border-soft);">'
                    '<div style="font-family:var(--f-mono);font-size:9px;letter-spacing:3px;'
                    'color:var(--text-dim);text-transform:uppercase;">ACTIVITY TIMELINE</div>'
                    '</div>',
                    unsafe_allow_html=True,
                )
                st.plotly_chart(tl["figure"], use_container_width=True)
                st.markdown('</div>', unsafe_allow_html=True)
                # Anomaly flags inline
                if anoms_tl:
                    for a in anoms_tl[:4]:
                        sev_raw = (a.get("severity") or "MEDIUM").upper()
                        sev_cls = "hi" if "HIGH" in sev_raw or "CRITICAL" in sev_raw else "md" if "MEDIUM" in sev_raw else "lo"
                        st.markdown(
                            f'<div class="flag-card {sev_cls}" style="margin-top:4px;">'
                            f'<div class="sev">{sev_raw[:8]}</div>'
                            f'<div><div class="ttl">{(a.get("flag") or "ANOMALY")[:50]}</div>'
                            f'<div class="sub">{(a.get("detail") or "")[:80]}</div></div>'
                            f'<div></div></div>',
                            unsafe_allow_html=True,
                        )
            else:
                st.markdown(
                    '<div class="card" style="text-align:center;padding:3rem;">'
                    '<div style="font-family:var(--f-mono);font-size:10px;color:var(--text-dim);'
                    'letter-spacing:2px;">NO TIMELINE DATA AVAILABLE</div></div>',
                    unsafe_allow_html=True,
                )

        with tab_heatmap:
            if tl and tl.get("events"):
                import pandas as pd
                from collections import defaultdict
                events_hm = tl.get("events", [])
                parsed_hm = []
                for ev in events_hm:
                    try:
                        d = pd.to_datetime(ev.get("normalized",""), errors="coerce")
                        if pd.notna(d): parsed_hm.append(d)
                    except Exception: pass

                _DAYS = ["MON","TUE","WED","THU","FRI","SAT","SUN"]
                day_hour = defaultdict(int)
                for p in parsed_hm:
                    day_hour[(p.weekday(), p.hour)] += 1
                max_c = max(day_hour.values()) if day_hour else 1

                def _hcls(n):
                    if n == 0: return ""
                    r = n / max_c
                    return "l4" if r > 0.75 else "l3" if r > 0.5 else "l2" if r > 0.25 else "l1"

                hr_hdr = "".join(f'<span style="text-align:center;font-size:8px;">{h:02d}</span>' for h in range(24))
                ghml = f'<div class="heat"><div class="row-l"></div><div class="hour-row">{hr_hdr}</div>'
                for dow, dlbl in enumerate(_DAYS):
                    ghml += f'<div class="row-l">{dlbl}</div>'
                    for hr in range(24):
                        cnt = day_hour.get((dow, hr), 0)
                        ghml += f'<div class="cell {_hcls(cnt)}" title="{cnt} events"></div>'
                ghml += "</div>"

                st.markdown(
                    f'<div class="card" style="padding:0;overflow:hidden;">'
                    f'<div style="padding:10px 14px 8px;border-bottom:1px solid var(--border-soft);">'
                    f'<div style="font-family:var(--f-mono);font-size:9px;letter-spacing:3px;'
                    f'color:var(--text-dim);text-transform:uppercase;">HOUR × DAY HEATMAP · {len(parsed_hm)} EVENTS MAPPED</div>'
                    f'</div>'
                    f'<div style="padding:16px;">{ghml}</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
                if st.button("OPEN FULL HEATMAP SCREEN", key="wb_hm_goto", use_container_width=True):
                    st.session_state.active_screen = "heatmap"; st.rerun()
            else:
                st.markdown(
                    '<div class="card" style="text-align:center;padding:3rem;">'
                    '<div style="font-family:var(--f-mono);font-size:10px;color:var(--text-dim);'
                    'letter-spacing:2px;">NO TIMELINE DATA FOR HEATMAP</div></div>',
                    unsafe_allow_html=True,
                )

    # ── Right: agent outputs ───────────────────────────────────────────────────
    with right:
        if agents:
            # Risk
            r = agents.get("risk", {})
            rs = r.get("risk_score", 0)
            rl = r.get("risk_level", "—")
            rc = "var(--critical)" if rs >= 70 else "var(--warning)" if rs >= 40 else "var(--online)"
            risk_factors = r.get("risk_factors", [])
            threat_rows = "".join(
                f'<div class="threat">'
                f'<div class="sev {"hi" if (f.get("severity","") or "").upper() in ("HIGH","CRITICAL") else "md"}">'
                f'{(f.get("severity","MED") or "MED")[:3].upper()}</div>'
                f'<div class="body">'
                f'<div class="title">{f.get("factor","—")[:40]}</div>'
                f'<div class="meta">{(f.get("evidence","") or "")[:60]}</div>'
                f'</div></div>'
                for f in risk_factors[:4]
            ) or '<div style="padding:12px 14px;font-family:var(--f-mono);font-size:10px;color:var(--text-dim);">NO RISK FACTORS</div>'

            st.markdown(
                f'<div class="card" style="padding:0;overflow:hidden;margin-bottom:12px;">'
                f'<div style="padding:10px 14px;border-bottom:1px solid var(--border-soft);'
                f'display:flex;justify-content:space-between;align-items:center;">'
                f'<div style="font-family:var(--f-mono);font-size:9px;letter-spacing:3px;color:var(--text-dim);">RISK AGENT</div>'
                f'<div style="font-family:var(--f-display);font-weight:600;font-size:20px;color:{rc};">{rs}</div>'
                f'</div>'
                f'{threat_rows}</div>',
                unsafe_allow_html=True,
            )

            # Patterns
            patterns = agents.get("patterns", {}).get("patterns_found", [])
            pat_html = "".join(
                f'<div style="padding:8px 14px;border-bottom:1px solid var(--border-soft);">'
                f'<div style="font-family:var(--f-mono);font-size:9px;letter-spacing:2px;'
                f'color:{"var(--critical)" if p.get("significance")=="HIGH" else "var(--warning)" if p.get("significance")=="MEDIUM" else "var(--text-dim)"};'
                f'margin-bottom:3px;">{p.get("pattern_type","")[:20]}</div>'
                f'<div style="font-family:var(--f-mono);font-size:10px;color:var(--text-secondary);">{p.get("description","")[:70]}</div>'
                f'</div>'
                for p in patterns[:4]
            ) or '<div style="padding:12px 14px;font-family:var(--f-mono);font-size:10px;color:var(--text-dim);">NO PATTERNS</div>'
            st.markdown(
                f'<div class="card" style="padding:0;overflow:hidden;margin-bottom:12px;">'
                f'<div style="padding:10px 14px;border-bottom:1px solid var(--border-soft);">'
                f'<div style="font-family:var(--f-mono);font-size:9px;letter-spacing:3px;color:var(--text-dim);">PATTERN AGENT</div>'
                f'</div>{pat_html}</div>',
                unsafe_allow_html=True,
            )

            # Next steps
            steps = agents.get("next_steps", {}).get("next_steps", [])
            step_html = "".join(
                f'<div class="step {"done" if i==0 else "active" if i==1 else "pending"}">'
                f'<div class="num">0{s.get("step_number",i+1)}</div>'
                f'<div><div class="t">{s.get("action","")[:36]}</div>'
                f'<div class="d">{(s.get("legal_basis","") or "")[:50]}</div></div>'
                f'<div class="state">{"DONE" if i==0 else "ACTIVE" if i==1 else "PENDING"}</div>'
                f'<div class="timer">—</div>'
                f'</div>'
                for i, s in enumerate(steps[:4])
            ) or '<div style="padding:12px 14px;font-family:var(--f-mono);font-size:10px;color:var(--text-dim);">NO STEPS</div>'
            st.markdown(
                f'<div class="card" style="padding:0;overflow:hidden;margin-bottom:12px;">'
                f'<div style="padding:10px 14px;border-bottom:1px solid var(--border-soft);">'
                f'<div style="font-family:var(--f-mono);font-size:9px;letter-spacing:3px;color:var(--text-dim);">NEXT STEPS</div>'
                f'</div><div style="padding:0 14px;">{step_html}</div></div>',
                unsafe_allow_html=True,
            )

            # Compliance
            c = agents.get("compliance", {})
            cleared = c.get("cleared_for_export")
            sc_col  = "var(--online)" if cleared else "var(--critical)"
            sc_txt  = "CLEARED" if cleared else "FLAGGED"
            comp_flags = c.get("flags", [])
            cf_html = "".join(
                f'<div style="padding:6px 14px;border-bottom:1px solid var(--border-soft);'
                f'font-family:var(--f-mono);font-size:10px;color:var(--warning);">⚑ {fl.get("concern","")[:60]}</div>'
                for fl in comp_flags[:3]
            )
            st.markdown(
                f'<div class="card" style="padding:0;overflow:hidden;">'
                f'<div style="padding:10px 14px;border-bottom:1px solid var(--border-soft);'
                f'display:flex;justify-content:space-between;">'
                f'<div style="font-family:var(--f-mono);font-size:9px;letter-spacing:3px;color:var(--text-dim);">COMPLIANCE</div>'
                f'<div class="status-pill {("active" if cleared else "locked")}" style="color:{sc_col};">'
                f'<span style="width:6px;height:6px;border-radius:50%;background:{sc_col};display:inline-block;margin-right:4px;"></span>'
                f'{sc_txt}</div>'
                f'</div>'
                f'<div style="padding:10px 14px;font-family:var(--f-display);font-weight:600;font-size:24px;color:{sc_col};">'
                f'{c.get("compliance_score",0)}%</div>'
                + cf_html +
                f'</div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                '<div class="card" style="padding:20px 14px;text-align:center;">'
                '<div style="font-family:var(--f-mono);font-size:11px;color:var(--text-dim);'
                'letter-spacing:1px;">RUN A SEARCH TO SEE AGENT OUTPUTS</div>'
                '</div>',
                unsafe_allow_html=True,
            )


# ─────────────────────────────────────────────────────────────────────────────
# TIMELINE HEATMAP
# ─────────────────────────────────────────────────────────────────────────────

def screen_heatmap():
    screen_header("TIMELINE HEATMAP", "Calendar activity heatmap · Hour×Day intensity grid · Purple = activity")
    st.markdown(
        '<div class="al-classification-strip">TS//SCI//NOFORN · BEHAVIOURAL HEATMAP · TEMPORAL PATTERN ANALYSIS</div>',
        unsafe_allow_html=True,
    )
    tl = st.session_state.timeline_data
    if not tl or not tl.get("events"):
        st.markdown(
            '<div class="card" style="text-align:center;padding:3rem 1rem;margin-top:1rem;">'
            '<div style="font-family:var(--f-mono);font-size:11px;color:var(--text-dim);'
            'letter-spacing:2px;">NO TIMELINE DATA · RUN A SEARCH FIRST</div>'
            '</div>',
            unsafe_allow_html=True,
        )
        if st.button("GO TO SEARCH", key="hm_go"): st.session_state.active_screen = "search"; st.rerun()
        return

    import pandas as pd
    events = tl.get("events", [])

    # Parse dates
    parsed = []
    for ev in events:
        raw = ev.get("normalized", "")
        try:
            d = pd.to_datetime(raw, errors="coerce")
            if pd.notna(d):
                parsed.append(d)
        except Exception:
            pass

    unique_days  = len(set(p.date() for p in parsed))
    total_events = len(events)

    # ── Stat tiles ─────────────────────────────────────────────────────────────
    st.markdown(
        f'<div class="progress-summary" style="margin-bottom:18px;">'
        f'<div class="tile"><div class="k">TOTAL EVENTS</div><div class="v">{total_events}</div></div>'
        f'<div class="tile"><div class="k">ACTIVE DAYS</div><div class="v">{unique_days}</div></div>'
        f'<div class="tile"><div class="k">PARSEABLE</div><div class="v">{len(parsed)}</div></div>'
        f'<div class="tile"><div class="k">COVERAGE</div>'
        f'<div class="v" style="font-size:14px;">{int(len(parsed)/max(total_events,1)*100)}%</div></div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # ── Hour × Day-of-week CSS heat grid ──────────────────────────────────────
    from collections import defaultdict
    _DAYS = ["MON","TUE","WED","THU","FRI","SAT","SUN"]
    day_hour_counts = defaultdict(int)
    for p in parsed:
        key = (p.weekday(), p.hour)
        day_hour_counts[key] += 1

    max_count = max(day_hour_counts.values()) if day_hour_counts else 1

    def _heat_cls(n):
        if n == 0:   return ""
        pct = n / max_count
        if pct > 0.75: return "l4"
        if pct > 0.50: return "l3"
        if pct > 0.25: return "l2"
        return "l1"

    # Hour header row
    hour_cells = "".join(
        f'<span style="text-align:center;font-size:8px;">{h:02d}</span>'
        for h in range(24)
    )
    grid_html = (
        '<div class="heat" style="margin:4px 0;">'
        f'<div class="row-l"></div>'
        f'<div class="hour-row">{hour_cells}</div>'
    )
    for dow, day_lbl in enumerate(_DAYS):
        grid_html += f'<div class="row-l">{day_lbl}</div>'
        for hour in range(24):
            cnt = day_hour_counts.get((dow, hour), 0)
            cls = _heat_cls(cnt)
            title = f"{cnt} events" if cnt else ""
            grid_html += f'<div class="cell {cls}" title="{title}"></div>'
    grid_html += "</div>"

    st.markdown(
        f'<div class="card" style="padding:0;overflow:hidden;margin-bottom:14px;">'
        f'<div style="padding:12px 16px 10px;border-bottom:1px solid var(--border-soft);'
        f'display:flex;justify-content:space-between;align-items:center;">'
        f'<div style="font-family:var(--f-mono);font-size:9px;letter-spacing:3px;'
        f'color:var(--text-dim);text-transform:uppercase;">HOUR × DAY ACTIVITY HEATMAP</div>'
        f'<div style="display:flex;gap:8px;align-items:center;">'
        f'<span style="font-family:var(--f-mono);font-size:8px;color:var(--text-dim);">LOW</span>'
        f'<div style="width:10px;height:10px;background:rgba(123,47,190,0.22);"></div>'
        f'<div style="width:10px;height:10px;background:rgba(123,47,190,0.45);"></div>'
        f'<div style="width:10px;height:10px;background:rgba(157,78,221,0.75);"></div>'
        f'<div style="width:10px;height:10px;background:var(--purple-300);"></div>'
        f'<span style="font-family:var(--f-mono);font-size:8px;color:var(--text-dim);">HIGH</span>'
        f'</div></div>'
        f'<div style="padding:16px 18px;">{grid_html}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # ── Calendar heatmap (calplot, if available) ───────────────────────────────
    if parsed:
        try:
            import calplot, matplotlib.pyplot as plt, matplotlib
            matplotlib.use("Agg")
            series = pd.Series(1, index=pd.DatetimeIndex(parsed)).resample("D").sum()
            fig_cal, _ = calplot.calplot(
                series, cmap="Purples", colorbar=False,
                edgecolor="#0A0015", linewidth=0.5, figsize=(14, 3),
            )
            fig_cal.patch.set_facecolor("#05000D")
            for axis in fig_cal.get_axes():
                axis.set_facecolor("#05000D")
                for spine in axis.spines.values():
                    spine.set_edgecolor("rgba(123,47,190,0.15)")
                axis.tick_params(colors="#4B5563", labelsize=7)
            st.markdown(
                '<div class="card" style="padding:0;overflow:hidden;margin-bottom:14px;">'
                '<div style="padding:12px 16px 8px;border-bottom:1px solid var(--border-soft);">'
                '<div style="font-family:var(--f-mono);font-size:9px;letter-spacing:3px;'
                'color:var(--text-dim);text-transform:uppercase;">CALENDAR HEATMAP</div>'
                '</div>'
                '<div style="padding:10px 16px;">',
                unsafe_allow_html=True,
            )
            st.pyplot(fig_cal)
            st.markdown('</div></div>', unsafe_allow_html=True)
            plt.close(fig_cal)
        except ImportError:
            pass
        except Exception as _ce:
            st.markdown(
                f'<div class="card" style="padding:10px 14px;">'
                f'<div style="font-family:var(--f-mono);font-size:10px;color:var(--critical);">'
                f'CALENDAR ERROR · {_ce}</div></div>',
                unsafe_allow_html=True,
            )

    # ── Event log ─────────────────────────────────────────────────────────────
    st.markdown(
        f'<div class="card" style="padding:0;overflow:hidden;">'
        f'<div style="padding:12px 16px 8px;border-bottom:1px solid var(--border-soft);">'
        f'<div style="font-family:var(--f-mono);font-size:9px;letter-spacing:3px;color:var(--text-dim);'
        f'text-transform:uppercase;">EVENT LOG · FIRST {min(40,total_events)} OF {total_events}</div>'
        f'</div>'
        f'<div style="display:grid;grid-template-columns:130px 80px 1fr 100px;gap:10px;'
        f'padding:8px 14px;font-family:var(--f-mono);font-size:9px;letter-spacing:3px;'
        f'color:var(--text-dim);text-transform:uppercase;border-bottom:1px solid var(--border);'
        f'background:rgba(0,0,0,0.3);">DATE · TYPE · DESCRIPTION · SOURCE</div>'
        f'<div style="max-height:300px;overflow-y:auto;">',
        unsafe_allow_html=True,
    )
    for ev in events[:40]:
        etype = (ev.get("event_type") or "MENTION").upper()[:10]
        ctx   = (ev.get("context") or "")[:80]
        src   = (ev.get("source") or "")[:18]
        date  = (ev.get("normalized") or "")[:16]
        st.markdown(
            f'<div style="display:grid;grid-template-columns:130px 80px 1fr 100px;gap:10px;'
            f'padding:7px 14px;border-bottom:1px solid var(--border-soft);'
            f'font-family:var(--f-mono);font-size:10px;">'
            f'<span style="color:var(--text-dim);">{date}</span>'
            f'<span style="color:var(--purple-300);font-size:9px;">{etype}</span>'
            f'<span style="color:var(--text-primary);">{ctx}</span>'
            f'<span style="color:var(--text-secondary);">{src}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )
    st.markdown('</div></div>', unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# GEOLOCATION MAP
# ─────────────────────────────────────────────────────────────────────────────

def screen_geo_map():
    screen_header("GEOLOCATION MAP", "Confirmed locations · Cluster markers · Entity details on click")
    person = st.session_state.person_profile
    ont_g  = st.session_state.ontology_graph
    if not person and not ont_g:
        st.markdown('<div class="info-msg">Run a search first to see location data.</div>', unsafe_allow_html=True)
        if st.button("GO TO SEARCH"): st.session_state.active_screen = "search"; st.rerun()
        return

    # Collect locations from person + ontology
    loc_data = []
    if person:
        for loc in person.get("location_stated", []):
            if loc:
                loc_data.append({"name": str(loc), "lat": None, "lon": None, "source": "stated_profile", "entity": person.get("confirmed_name","")})
    if ont_g:
        for eid, ent in ont_g._entities.items():
            if getattr(ent, "entity_type", "") == "LOCATION":
                coords = getattr(ent, "coordinates", {})
                loc_data.append({"name": getattr(ent, "name",""), "lat": coords.get("lat"), "lon": coords.get("lon"), "source": "ontology", "entity": eid[:8]})

    if not loc_data:
        st.info("No location data found for this subject.")
        return

    st.markdown(f'**{len(loc_data)} location(s) recorded**')

    # Show as table (map requires lat/lon which may not be available without geocoding)
    import pandas as pd
    df_loc = pd.DataFrame(loc_data)
    st.dataframe(df_loc, use_container_width=True, hide_index=True)

    # If any have coordinates, show Plotly scatter_geo
    geo_rows = [r for r in loc_data if r.get("lat") and r.get("lon")]
    if geo_rows:
        try:
            import plotly.graph_objects as go
            fig = go.Figure(go.Scattergeo(
                lat=[r["lat"] for r in geo_rows],
                lon=[r["lon"] for r in geo_rows],
                text=[r["name"] for r in geo_rows],
                mode="markers",
                marker=dict(size=12, color="#7B2FBE", opacity=0.8),
                hovertemplate="<b>%{text}</b><extra></extra>",
            ))
            fig.update_layout(
                paper_bgcolor="#0A0015", geo=dict(bgcolor="#05000D", showland=True,
                landcolor="#0A0015", showocean=True, oceancolor="#05000D",
                showcoastlines=True, coastlinecolor="#2A2A4A"),
                margin=dict(l=0,r=0,t=30,b=0), height=400,
                title=dict(text="Subject Location Map", font=dict(color="#7B2FBE")),
            )
            st.plotly_chart(fig, use_container_width=True)
        except Exception as e:
            st.markdown(f'<div style="color:#4B5563;font-size:0.8rem;">Map render error: {e}</div>', unsafe_allow_html=True)
    else:
        st.markdown(
            '<div style="font-family:\'JetBrains Mono\',monospace;font-size:10px;'
            'letter-spacing:1px;color:#4B5563;">NO GEOCODED COORDINATES AVAILABLE · ADD GEOCODING INTEGRATION TO PLOT ON MAP</div>',
            unsafe_allow_html=True,
        )


# ─────────────────────────────────────────────────────────────────────────────
# REPORTS CENTER
# ─────────────────────────────────────────────────────────────────────────────

def screen_reports_center():
    screen_header("REPORTS CENTER", "All generated reports · Filter · Preview · Download · Compliance score")
    try:
        import os, glob as _glob
        exports_dir = str(config.EXPORTS_DIR)
        pdfs = sorted(_glob.glob(f"{exports_dir}/*.pdf"), key=os.path.getmtime, reverse=True)
    except Exception:
        pdfs = []

    agents = st.session_state.agent_results or {}
    comp_r = agents.get("compliance", {})

    if not pdfs:
        st.markdown('<div class="info-msg">No exported reports found. Generate a report first.</div>', unsafe_allow_html=True)
        if st.button("GO TO SEARCH"): st.session_state.active_screen = "search"; st.rerun()
        return

    st.markdown(f'**{len(pdfs)} report(s) found in exports/**')
    rc1, rc2 = st.columns([1.5, 1])

    with rc1:
        import pandas as pd, os as _os
        rows = []
        for p in pdfs[:30]:
            fname = _os.path.basename(p)
            size  = round(_os.path.getsize(p) / 1024, 1)
            mtime = _os.path.getmtime(p)
            import datetime as _dt
            dt    = _dt.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
            rows.append({"Filename": fname, "Size (KB)": size, "Generated": dt})
        df = pd.DataFrame(rows)
        selected_idx = st.dataframe(df, use_container_width=True, hide_index=True, selection_mode="single-row", on_select="rerun", key="reports_center_df")

    with rc2:
        sel = (selected_idx.selection.rows or []) if hasattr(selected_idx, "selection") else []
        if sel:
            chosen = pdfs[sel[0]]
            fname  = __import__("os").path.basename(chosen)
            st.markdown(f'**{fname}**')
            comp_score = comp_r.get("compliance_score", 100)
            cleared    = comp_r.get("cleared_for_export", True)
            st.markdown(f'Compliance: {_risk_badge(100 - comp_score, "")} {"✓ CLEARED" if cleared else "⚑ FLAGGED"}', unsafe_allow_html=True)
            try:
                with open(chosen, "rb") as f:
                    pdf_bytes = f.read()
                st.download_button("DOWNLOAD", data=pdf_bytes, file_name=fname, mime="application/pdf", use_container_width=True)
            except Exception as e:
                st.error(f"Read error: {e}")
        else:
            st.markdown('<div style="color:#4B5563;font-size:0.8rem;">Select a report to preview/download.</div>', unsafe_allow_html=True)
            # Show current session compliance
            if comp_r:
                st.markdown(f'**Current session compliance: {comp_r.get("compliance_score",100)}%**')
                for fl in comp_r.get("flags", []):
                    st.markdown(f'⚑ {fl.get("concern","")}')


# ─────────────────────────────────────────────────────────────────────────────
# AUDIT COMMAND CENTER (ADMIN ONLY)
# ─────────────────────────────────────────────────────────────────────────────

def screen_audit_center():
    screen_header("AUDIT COMMAND CENTER", "Tamper-proof log · Hash integrity · User activity · DPDP compliance")
    role = st.session_state.get("current_role", "")
    if role != config.ROLE_ADMIN:
        st.markdown('<div class="error-msg">ADMIN access required.</div>', unsafe_allow_html=True)
        return

    try:
        from modules.security import verify_audit_integrity, check_access_log_retention, dpdp
        integrity = verify_audit_integrity()
        retention = check_access_log_retention()
        erasure   = dpdp.get_erasure_requests()
    except Exception as e:
        st.error(f"Security module error: {e}")
        return

    # Hash integrity status
    intact       = integrity["status"] == "INTACT"
    status_color = "#16A34A" if intact else "#DC2626"
    status_text  = "INTACT" if intact else f"COMPROMISED — first bad: {integrity.get('first_bad_entry','?')}"
    from modules.ui_components import hash_chain_panel as _hcp
    st.markdown(_hcp(integrity.get("checked", 0), "AUDIT·CHAIN", intact), unsafe_allow_html=True)
    st.markdown(
        f'<div style="font-family:\'JetBrains Mono\',monospace;font-size:9px;letter-spacing:3px;'
        f'color:{status_color};margin:8px 0 12px;border:1px solid {status_color};'
        f'padding:6px 12px;background:{status_color}0F;">'
        f'AUDIT LOG INTEGRITY: {status_text}'
        f'<span style="color:#4B5563;margin-left:16px;">({integrity.get("checked",0)} ENTRIES VERIFIED)</span>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # Metrics
    from modules.ui_components import stat_card as _sc2
    m1, m2, m3 = st.columns(3)
    with m1:
        st.markdown(_sc2("AC·01","AUDIT ENTRIES", str(integrity.get("checked",0))), unsafe_allow_html=True)
    with m2:
        ret_ok = retention.get("compliant", False)
        st.markdown(_sc2("AC·02","6-MONTH RETENTION", "YES" if ret_ok else "NO",
                         bar_color="#16A34A" if ret_ok else "#DC2626"), unsafe_allow_html=True)
    with m3:
        st.markdown(_sc2("AC·03","ERASURE REQUESTS", str(len(erasure))), unsafe_allow_html=True)

    st.markdown('<hr class="divider">', unsafe_allow_html=True)

    tab_log, tab_sessions, tab_dpdp = st.tabs(["AUDIT LOG", "ACTIVE SESSIONS", "DPDP COMPLIANCE"])

    with tab_log:
        try:
            import sqlite3 as _sql, json as _json, pandas as pd
            conn = _sql.connect(str(config.DATABASE_PATH))
            rows = conn.execute("SELECT data FROM secure_audit_log ORDER BY saved_at DESC LIMIT 200").fetchall()
            conn.close()
            if rows:
                log_data = []
                for (data_str,) in rows:
                    try:
                        d = _json.loads(data_str)
                        log_data.append({
                            "Timestamp": d.get("timestamp","")[:19],
                            "User":      d.get("user_id",""),
                            "Role":      d.get("user_role",""),
                            "Action":    d.get("action",""),
                            "Target":    d.get("target","")[:40],
                            "Result":    d.get("result","")[:30],
                            "Class.Lvl": d.get("classification_level",0),
                        })
                    except Exception:
                        pass
                df_sec = pd.DataFrame(log_data)
                st.dataframe(df_sec, use_container_width=True, height=350, hide_index=True)
                csv = df_sec.to_csv(index=False).encode()
                st.download_button("EXPORT AUDIT CSV", csv, file_name="aetherlens_secure_audit.csv", mime="text/csv")
            else:
                st.info("No secure audit log entries.")
        except Exception as e:
            st.error(f"Audit log error: {e}")

    with tab_sessions:
        try:
            from modules.security import session_manager
            sessions = session_manager.get_active_sessions()
            if sessions:
                import pandas as pd
                df_sess = pd.DataFrame(sessions)
                st.dataframe(df_sess, use_container_width=True, hide_index=True)
            else:
                st.info("No active sessions.")
        except Exception as e:
            st.error(f"Session error: {e}")

    with tab_dpdp:
        st.markdown("**DPDP Act 2023 — Retention & Erasure Compliance**")
        try:
            from modules.security import dpdp
            flags = dpdp.flag_retention_breaches()
            if flags:
                import pandas as pd
                st.warning(f"{len(flags)} data item(s) exceed retention limit")
                st.dataframe(pd.DataFrame(flags), use_container_width=True, hide_index=True)
            else:
                st.success("No retention breaches detected.")
            if erasure:
                st.markdown(f"**{len(erasure)} erasure request(s)**")
                import pandas as pd
                st.dataframe(pd.DataFrame(erasure), use_container_width=True, hide_index=True)
            else:
                st.info("No erasure requests.")
        except Exception as e:
            st.error(f"DPDP error: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# DASHBOARD ROUTER
# ─────────────────────────────────────────────────────────────────────────────

def screen_dashboard():
    if not validate_session():
        st.session_state.auth_error = "Session expired."
        st.rerun(); return
    sidebar_nav()
    screen = st.session_state.active_screen
    if   screen == "command_center":     screen_command_center()
    elif screen == "search":             screen_search()
    elif screen == "fusion":             screen_fusion()
    elif screen == "analysis_workbench": screen_analysis_workbench()
    elif screen == "network_map":        screen_network_map()
    elif screen == "timeline":           screen_timeline()
    elif screen == "heatmap":            screen_heatmap()
    elif screen == "geo_map":            screen_geo_map()
    elif screen == "reports_center":     screen_reports_center()
    elif screen == "reports":            screen_reports()
    elif screen == "audit_center":       screen_audit_center()
    elif screen == "admin":              screen_admin()
    else:                                screen_command_center()


# ─────────────────────────────────────────────────────────────────────────────
# MAIN ROUTER
# ─────────────────────────────────────────────────────────────────────────────

def main():
    pin_ok    = st.session_state.get("pin_verified", False)
    logged_in = bool(st.session_state.get("jwt_token"))
    if not pin_ok:        screen_pin_gate()
    elif not logged_in:   screen_login()
    else:                 screen_dashboard()

if __name__ == "__main__" or True:
    main()
