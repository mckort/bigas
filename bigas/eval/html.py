"""Clickable HTML for eval reports."""
from __future__ import annotations

import html
import re
from typing import List

from bigas.eval.base import EvalRunResult
from bigas.eval.readable import build_full_markdown

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)(?:\s*\{#([\w-]+)\})?$")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_CODE_RE = re.compile(r"`([^`]+)`")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def _inline(text: str) -> str:
    escaped = html.escape(text)
    escaped = _BOLD_RE.sub(r"<strong>\1</strong>", escaped)
    escaped = _CODE_RE.sub(r"<code>\1</code>", escaped)
    escaped = _LINK_RE.sub(r'<a href="\2">\1</a>', escaped)
    escaped = escaped.replace("✓", '<span class="status-good">✓</span>')
    escaped = escaped.replace("⚠", '<span class="status-warning">⚠</span>')
    escaped = escaped.replace("✗", '<span class="status-bad">✗</span>')
    return escaped


def _is_table_row(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("|") and stripped.endswith("|") and stripped.count("|") >= 2


def _is_table_divider(line: str) -> bool:
    stripped = line.strip().strip("|").replace(":", "").replace("-", "").replace("|", "").replace(" ", "")
    return _is_table_row(line) and not stripped


def _render_table(rows: List[str]) -> str:
    body_rows = [row for row in rows if not _is_table_divider(row)]
    if not body_rows:
        return ""
    html_parts = ['<div style="overflow-x: auto;"><table>']
    for index, row in enumerate(body_rows):
        cells = [cell.strip() for cell in row.strip().strip("|").split("|")]
        tag = "th" if index == 0 else "td"
        html_parts.append("<tr>" + "".join(f"<{tag}>{_inline(cell)}</{tag}>" for cell in cells) + "</tr>")
    html_parts.append("</table></div>")
    return "".join(html_parts)


def markdown_to_html_body(markdown: str) -> str:
    lines = (markdown or "").replace("\r\n", "\n").split("\n")
    parts: List[str] = []
    i = 0
    in_code = False
    code_lines: List[str] = []
    paragraph: List[str] = []
    list_items: List[str] = []

    def flush_paragraph() -> None:
        if paragraph:
            parts.append("<p>" + "<br>\n".join(_inline(line) for line in paragraph) + "</p>")
            paragraph.clear()

    def flush_list() -> None:
        if list_items:
            parts.append("<ul>" + "".join(f"<li>{_inline(item)}</li>" for item in list_items) + "</ul>")
            list_items.clear()

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith("```"):
            flush_paragraph()
            flush_list()
            if in_code:
                parts.append("<pre><code>" + html.escape("\n".join(code_lines)) + "</code></pre>")
                code_lines = []
                in_code = False
            else:
                in_code = True
            i += 1
            continue
        if in_code:
            code_lines.append(line)
            i += 1
            continue
        if _is_table_row(stripped):
            flush_paragraph()
            flush_list()
            table_rows = []
            while i < len(lines) and _is_table_row(lines[i].strip()):
                table_rows.append(lines[i].strip())
                i += 1
            parts.append(_render_table(table_rows))
            continue
        heading = _HEADING_RE.match(stripped)
        if heading:
            flush_paragraph()
            flush_list()
            level = min(len(heading.group(1)), 3)
            title = heading.group(2).strip()
            anchor_id = heading.group(3)
            if not anchor_id:
                anchor_id = re.sub(r"[^\w\s-]", "", title.lower())
                anchor_id = re.sub(r"[\s_]+", "-", anchor_id).strip("-")
            parts.append(f'<h{level} id="{html.escape(anchor_id)}">{_inline(title)}</h{level}>')
            i += 1
            continue
        if stripped.startswith("- "):
            flush_paragraph()
            list_items.append(stripped[2:])
            i += 1
            continue
        if not stripped:
            flush_paragraph()
            flush_list()
            i += 1
            continue
        flush_list()
        paragraph.append(stripped)
        i += 1

    flush_paragraph()
    flush_list()
    if in_code:
        parts.append("<pre><code>" + html.escape("\n".join(code_lines)) + "</code></pre>")
    return "\n".join(parts)


def build_html_report(run: EvalRunResult) -> str:
    title = f"Model eval {run.use_case} · {run.run_id}"
    body = markdown_to_html_body(build_full_markdown(run))
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    :root {{ color-scheme: light dark; }}
    body {{
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #0f1419;
      color: #e7e9ea;
      line-height: 1.6;
    }}
    .wrap {{
      max-width: 56rem;
      margin: 0 auto;
      padding: 1.5rem 1.25rem 3rem;
    }}
    
    /* Typography */
    h1 {{
      font-size: 1.6rem;
      margin: 0 0 1rem;
      padding-bottom: 0.5rem;
      border-bottom: 2px solid #38444d;
    }}
    h2 {{
      font-size: 1.3rem;
      margin: 2.5rem 0 0.75rem;
      color: #79b8ff;
    }}
    h3 {{
      font-size: 1.1rem;
      margin: 1.75rem 0 0.5rem;
    }}
    p, li {{ line-height: 1.65; }}
    a {{ color: #79b8ff; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    hr {{
      border: none;
      border-top: 1px solid #38444d;
      margin: 2rem 0;
    }}
    
    /* Code */
    code {{
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      font-size: 0.88em;
      background: #15202b;
      padding: 0.15em 0.4em;
      border-radius: 4px;
      color: #f0f6fc;
    }}
    pre {{
      background: #15202b;
      border: 1px solid #38444d;
      border-radius: 8px;
      padding: 1rem 1.25rem;
      overflow-x: auto;
      font-size: 0.9rem;
    }}
    pre code {{ background: transparent; padding: 0; }}
    
    /* Tables */
    table {{
      width: 100%;
      border-collapse: collapse;
      margin: 1rem 0 1.5rem;
      font-size: 0.92rem;
    }}
    th, td {{
      border: 1px solid #38444d;
      padding: 0.6rem 0.75rem;
      text-align: left;
      vertical-align: top;
    }}
    th {{
      background: #15202b;
      font-weight: 600;
    }}
    tr:nth-child(even) td {{
      background: rgba(21, 32, 43, 0.5);
    }}
    
    /* Lists */
    ul {{
      padding-left: 1.5rem;
      margin: 0.75rem 0;
    }}
    li {{
      margin: 0.4rem 0;
    }}
    
    /* Status indicators */
    .status-good {{
      color: #3fb950;
      font-weight: 600;
    }}
    .status-warning {{
      color: #d29922;
      font-weight: 600;
    }}
    .status-bad {{
      color: #f85149;
      font-weight: 600;
    }}
    
    /* Executive Summary Box */
    #executive-summary {{
      background: linear-gradient(135deg, #1a3a2a 0%, #15202b 100%);
      border: 1px solid #3fb950;
      border-radius: 12px;
      padding: 1.25rem 1.5rem;
      margin: 1.5rem 0;
    }}
    #executive-summary h2 {{
      color: #3fb950;
      margin-top: 0;
      font-size: 1.2rem;
    }}
    
    /* Table of Contents */
    #contents {{
      background: #15202b;
      border: 1px solid #38444d;
      border-radius: 8px;
      padding: 1rem 1.5rem;
      margin: 1.5rem 0;
    }}
    #contents h2 {{
      margin-top: 0;
      font-size: 1.1rem;
      color: #8b98a5;
    }}
    #contents ul {{
      list-style: none;
      padding-left: 0;
    }}
    #contents li {{
      margin: 0.3rem 0;
    }}
    #contents ul ul {{
      padding-left: 1.25rem;
      margin: 0.2rem 0;
    }}
    #contents a {{
      color: #e7e9ea;
    }}
    #contents a:hover {{
      color: #79b8ff;
    }}
    
    /* Model result cards */
    h3[id] {{
      background: #15202b;
      padding: 0.75rem 1rem;
      border-radius: 8px 8px 0 0;
      border: 1px solid #38444d;
      border-bottom: none;
      margin-bottom: 0;
    }}
    h3[id] + p {{
      background: #15202b;
      padding: 0.5rem 1rem 0.75rem;
      border: 1px solid #38444d;
      border-top: none;
      border-radius: 0 0 8px 8px;
      margin-top: 0;
    }}
    
    /* Score styling in tables */
    td:nth-child(4) {{
      font-weight: 600;
    }}
    
    /* Full model output section */
    #full-model-outputs ~ h1 {{
      background: linear-gradient(135deg, #1a2a3a 0%, #15202b 100%);
      border: 1px solid #38444d;
      border-radius: 8px;
      padding: 1rem 1.25rem;
      margin-top: 2rem;
    }}
    
    .muted {{ color: #8b98a5; }}
    
    /* Responsive adjustments */
    @media (max-width: 640px) {{
      .wrap {{
        padding: 1rem;
      }}
      table {{
        font-size: 0.85rem;
      }}
      th, td {{
        padding: 0.4rem 0.5rem;
      }}
    }}
  </style>
</head>
<body>
  <main class="wrap">
    {body}
  </main>
</body>
</html>
"""
