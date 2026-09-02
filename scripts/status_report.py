#!/usr/bin/env python3
"""Render a self-contained, read-only WUP status report as HTML."""
from __future__ import annotations

import html
from collections import Counter
from typing import Any, Mapping, Optional
from urllib.parse import urlparse

try:
    from status_snapshot import StatusSnapshot, ToolStatusSnapshot
except ImportError:  # pragma: no cover - supports package-style imports
    from .status_snapshot import StatusSnapshot, ToolStatusSnapshot


_STATUS_CLASS = {
    "CURRENT": "current",
    "UPDATE": "update",
    "URGENT": "urgent",
    "WATCH": "watch",
    "UNKNOWN": "unknown",
}


def _get(source: Any, field: str, default: Any = None) -> Any:
    if isinstance(source, Mapping):
        return source.get(field, default)
    return getattr(source, field, default)


def _display(value: Any) -> str:
    """Return a user-facing value, with unavailable values explicitly named."""
    return "unknown" if value is None or value == "" else str(value)


def _escape(value: Any) -> str:
    return html.escape(_display(value), quote=True)


def _tools(snapshot: Any) -> tuple[Any, ...]:
    raw = _get(snapshot, "tools", ())
    return tuple(raw) if isinstance(raw, (list, tuple)) else ()


def _safe_url(value: Any) -> Optional[str]:
    if not isinstance(value, str) or not value:
        return None
    parsed = urlparse(value)
    if parsed.scheme.lower() not in {"https", "http"} or not parsed.netloc:
        return None
    return value


def _url_markup(value: Any) -> str:
    shown = _escape(value)
    safe = _safe_url(value)
    if safe is None:
        if value in (None, ""):
            return '<span class="wup-muted">unknown</span>'
        return f'<span class="wup-link wup-link--unsafe">{shown} <span class="wup-link-note">(not opened)</span></span>'
    escaped_href = html.escape(safe, quote=True)
    return f'<a class="wup-link" href="{escaped_href}">{shown}</a>'


def _status_markup(status: Any) -> str:
    text = _display(status)
    key = text.upper()
    css_class = _STATUS_CLASS.get(key, "unknown")
    return f'<span class="wup-status wup-status--{css_class}">{html.escape(text, quote=True)}</span>'


def _tool_row(tool: Any) -> str:
    name = _escape(_get(tool, "tool_name", _get(tool, "name")))
    installed = _escape(_get(tool, "installed_version"))
    latest = _escape(_get(tool, "latest_version"))
    status = _status_markup(_get(tool, "status", "UNKNOWN"))
    health = _escape(_get(tool, "health", "UNVERIFIED"))
    installed_provenance = _escape(_get(tool, "installed_version_provenance", "LOCAL"))
    latest_provenance = _escape(_get(tool, "latest_version_provenance", "REMOTE"))
    local_at = _escape(_get(tool, "local_observed_at"))
    remote_at = _escape(_get(tool, "remote_observed_at"))
    url = _url_markup(_get(tool, "release_or_docs_url", _get(tool, "release_url")))
    return f"""
      <tr class="wup-tool-row">
        <th scope="row" data-label="Tool"><span class="wup-tool-name">{name}</span></th>
        <td data-label="Versions">
          <div class="wup-version"><span class="wup-label">Installed <b>{installed_provenance}</b></span><code>{installed}</code></div>
          <div class="wup-version"><span class="wup-label">Latest <b>{latest_provenance}</b></span><code>{latest}</code></div>
        </td>
        <td data-label="State"><div class="wup-state">{status}<span class="wup-health">Health: {health}</span></div></td>
        <td data-label="Observation">
          <div class="wup-observation"><span class="wup-label">Local observed</span><code>{local_at}</code></div>
          <div class="wup-observation"><span class="wup-label">Remote observed</span><code>{remote_at}</code></div>
        </td>
        <td data-label="Reference"><div class="wup-reference">{url}</div></td>
      </tr>"""


def render_status_html(snapshot: StatusSnapshot | Mapping[str, Any], history: Any = None) -> str:
    """Return deterministic HTML from supplied snapshot data only.

    ``history`` is accepted for a future compatible call shape but deliberately
    remains unused: MIC-127/MIC-139 own history parsing and the renderer must
    not perform hidden file reads or duplicate those semantics.
    """
    del history
    tools = _tools(snapshot)
    counts = Counter(str(_get(tool, "status", "UNKNOWN")).upper() for tool in tools)
    timestamp = _escape(_get(snapshot, "audit_report_timestamp"))
    rows = "\n".join(_tool_row(tool) for tool in tools)
    empty = "" if tools else '<tr><td class="wup-empty" colspan="5">No tool results were supplied.</td></tr>'
    summary = " · ".join(
        f"{label.title()} {counts.get(label, 0)}" for label in ("current", "update", "urgent", "watch", "unknown")
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>WUP · Local status</title>
  <style>
    :root {{
      --wup-ink: #162522;
      --wup-muted: #5f716c;
      --wup-paper: #f5f4ef;
      --wup-panel: #fffefa;
      --wup-rule: #d6ded9;
      --wup-teal: #17665c;
      --wup-teal-soft: #e2f0eb;
      --wup-update-soft: #fff4d9;
      --wup-urgent-soft: #fde7e7;
      --wup-watch-soft: #e6f1fa;
      --wup-unknown-soft: #edf0ee;
      --wup-current: #17665c;
      --wup-update: #835400;
      --wup-urgent: #9b2929;
      --wup-watch: #245e8a;
      --wup-unknown: #5f716c;
      --wup-space-1: 4px;
      --wup-space-2: 8px;
      --wup-space-3: 16px;
      --wup-space-4: 24px;
      --wup-space-5: 40px;
      --wup-radius: 4px;
    }}
    * {{ box-sizing: border-box; }}
    html, body {{ overflow-x: clip; }}
    body {{ margin: 0; background: var(--wup-paper); color: var(--wup-ink); font: 16px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }}
    .wup-shell {{ width: min(1180px, calc(100% - 48px)); margin: 0 auto; padding: var(--wup-space-5) 0 56px; }}
    .wup-masthead {{ display: grid; grid-template-columns: 1fr auto; gap: var(--wup-space-4); align-items: end; padding-bottom: var(--wup-space-4); border-bottom: 2px solid var(--wup-ink); }}
    .wup-kicker, .wup-label {{ color: var(--wup-muted); font-size: 0.72rem; font-weight: 750; letter-spacing: 0.09em; text-transform: uppercase; }}
    h1 {{ max-width: 14ch; margin: var(--wup-space-1) 0 0; font-size: clamp(2.3rem, 6vw, 5rem); line-height: 0.95; letter-spacing: -0.06em; }}
    .wup-audit {{ text-align: right; }}
    .wup-audit strong {{ display: block; margin-top: var(--wup-space-1); font: 700 0.92rem/1.2 ui-monospace, SFMono-Regular, Consolas, monospace; }}
    .wup-intro {{ display: grid; grid-template-columns: 1.2fr 0.8fr; gap: var(--wup-space-4); padding: var(--wup-space-4) 0; border-bottom: 1px solid var(--wup-rule); }}
    .wup-intro p {{ max-width: 62ch; margin: 0; color: var(--wup-muted); }}
    .wup-key {{ display: flex; flex-wrap: wrap; justify-content: flex-end; gap: var(--wup-space-2); align-content: start; }}
    .wup-key-item {{ display: inline-flex; align-items: center; gap: var(--wup-space-1); padding: var(--wup-space-1) var(--wup-space-2); border: 1px solid var(--wup-rule); border-radius: var(--wup-radius); background: var(--wup-panel); font: 700 0.76rem/1.3 ui-monospace, SFMono-Regular, Consolas, monospace; }}
    .wup-key-item b {{ color: var(--wup-teal); }}
    .wup-summary {{ padding: var(--wup-space-3) 0; color: var(--wup-muted); font: 700 0.78rem/1.2 ui-monospace, SFMono-Regular, Consolas, monospace; letter-spacing: 0.04em; text-transform: uppercase; }}
    .wup-table-wrap {{ overflow-x: auto; border-top: 1px solid var(--wup-ink); background: var(--wup-panel); }}
    table {{ width: 100%; border-collapse: collapse; text-align: left; }}
    thead {{ background: var(--wup-ink); color: var(--wup-panel); }}
    th, td {{ padding: var(--wup-space-3); vertical-align: top; border-bottom: 1px solid var(--wup-rule); }}
    thead th {{ font-size: 0.7rem; letter-spacing: 0.09em; text-transform: uppercase; }}
    tbody th {{ min-width: 150px; font-weight: 700; }}
    .wup-tool-name {{ display: block; font-size: 1.05rem; }}
    .wup-version, .wup-observation {{ display: grid; gap: var(--wup-space-1); margin-bottom: var(--wup-space-2); }}
    .wup-version:last-child, .wup-observation:last-child {{ margin-bottom: 0; }}
    .wup-version b {{ color: var(--wup-teal); }}
    code {{ color: var(--wup-ink); font: 0.86rem/1.35 ui-monospace, SFMono-Regular, Consolas, monospace; overflow-wrap: anywhere; }}
    .wup-state {{ display: grid; gap: var(--wup-space-2); min-width: 110px; }}
    .wup-status {{ width: fit-content; padding: var(--wup-space-1) var(--wup-space-2); border: 1px solid currentColor; border-radius: var(--wup-radius); font: 800 0.72rem/1.2 ui-monospace, SFMono-Regular, Consolas, monospace; letter-spacing: 0.06em; }}
    .wup-status--current {{ color: var(--wup-current); background: var(--wup-teal-soft); }}
    .wup-status--update {{ color: var(--wup-update); background: var(--wup-update-soft); }}
    .wup-status--urgent {{ color: var(--wup-urgent); background: var(--wup-urgent-soft); }}
    .wup-status--watch {{ color: var(--wup-watch); background: var(--wup-watch-soft); }}
    .wup-status--unknown {{ color: var(--wup-unknown); background: var(--wup-unknown-soft); }}
    .wup-health {{ color: var(--wup-muted); font-size: 0.8rem; }}
    .wup-reference {{ max-width: 230px; overflow-wrap: anywhere; }}
    .wup-link {{ color: var(--wup-teal); font-weight: 700; }}
    .wup-link:focus-visible {{ outline: 3px solid var(--wup-update); outline-offset: 3px; }}
    .wup-link--unsafe, .wup-muted {{ color: var(--wup-muted); }}
    .wup-link-note {{ font-size: 0.75rem; font-weight: 500; }}
    .wup-empty {{ padding: var(--wup-space-4); color: var(--wup-muted); text-align: center; }}
    .wup-footer {{ display: flex; justify-content: space-between; gap: var(--wup-space-3); padding-top: var(--wup-space-4); color: var(--wup-muted); font-size: 0.82rem; }}
    @media (max-width: 780px) {{
      .wup-shell {{ width: calc(100% - 32px); max-width: 620px; padding-top: var(--wup-space-4); }}
      .wup-masthead, .wup-intro {{ min-width: 0; grid-template-columns: 1fr; }}
      .wup-masthead > *, .wup-intro > * {{ min-width: 0; }}
      .wup-intro h2, .wup-intro p {{ max-width: 100%; overflow-wrap: anywhere; }}
      .wup-audit, .wup-key {{ text-align: left; justify-content: flex-start; }}
      .wup-key-item {{ white-space: normal; }}
      .wup-table-wrap {{ overflow: visible; border-top: 0; background: transparent; }}
      table, thead, tbody, tr, th, td {{ display: block; }}
      thead {{ position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; }}
      .wup-tool-row {{ margin-bottom: var(--wup-space-3); border: 1px solid var(--wup-rule); background: var(--wup-panel); }}
      th, td {{ display: grid; grid-template-columns: 7.5rem 1fr; gap: var(--wup-space-2); min-width: 0; padding: var(--wup-space-2) var(--wup-space-3); }}
      tbody th {{ display: block; padding-top: var(--wup-space-3); border-bottom: 0; }}
      td::before {{ content: attr(data-label); color: var(--wup-muted); font-size: 0.7rem; font-weight: 750; letter-spacing: 0.09em; text-transform: uppercase; }}
      td > * {{ min-width: 0; }}
      .wup-reference {{ max-width: none; }}
      .wup-footer {{ display: grid; }}
    }}
  </style>
</head>
<body>
  <main class="wup-shell">
    <header class="wup-masthead">
      <div><div class="wup-kicker">Workstation update watch</div><h1>Local status.</h1></div>
      <div class="wup-audit"><span class="wup-label">Audit report timestamp</span><strong>{timestamp}</strong></div>
    </header>
    <section class="wup-intro" aria-labelledby="freshness-title">
      <div><h2 id="freshness-title">A clear read on what WUP knows.</h2><p>Installed versions are local workstation knowledge. Latest versions are upstream knowledge. This report timestamp belongs to the audit as a whole; independent per-tool observation times are unavailable and remain <strong>unknown</strong>.</p></div>
      <div class="wup-key" aria-label="Knowledge provenance"><span class="wup-key-item"><b>LOCAL</b> installed</span><span class="wup-key-item"><b>REMOTE</b> latest</span><span class="wup-key-item"><b>UNKNOWN</b> per-tool time</span></div>
    </section>
    <div class="wup-summary" aria-live="polite">{html.escape(summary, quote=True)}</div>
    <section aria-labelledby="tools-title"><h2 id="tools-title">Monitored tools</h2><div class="wup-table-wrap"><table><thead><tr><th scope="col">Tool</th><th scope="col">Versions</th><th scope="col">State</th><th scope="col">Observation</th><th scope="col">Reference</th></tr></thead><tbody>{rows}{empty}</tbody></table></div></section>
    <footer class="wup-footer"><span>Read-only local report · no probes or network calls</span><span>Recent changes remain available through the WUP history CLI.</span></footer>
  </main>
</body>
</html>"""
