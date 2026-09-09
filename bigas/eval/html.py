"""Clickable HTML for eval reports."""
from __future__ import annotations

import html
import re
from typing import List

from bigas.eval.base import EvalRunResult
from bigas.eval.readable import build_full_markdown, format_run_label

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_CODE_RE = re.compile(r"`([^`]+)`")


def _inline(text: str) -> str:
    escaped = html.escape(text)
    escaped = _BOLD_RE.sub(r"<strong>\1</strong>", escaped)
    return _CODE_RE.sub(r"<code>\1</code>", escaped)


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
            parts.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
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
    title = f"Model eval {run.use_case} · {format_run_label(run.run_id)}"
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
    }}
    .wrap {{
      max-width: 52rem;
      margin: 0 auto;
      padding: 1.5rem 1.25rem 3rem;
    }}
    h1 {{ font-size: 1.45rem; margin: 0 0 0.75rem; }}
    h2 {{ font-size: 1.2rem; margin: 2rem 0 0.6rem; }}
    h3 {{ font-size: 1.05rem; margin: 1.4rem 0 0.4rem; }}
    p, li {{ line-height: 1.5; }}
    a {{ color: #79b8ff; }}
    code {{
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      font-size: 0.9em;
      background: #15202b;
      padding: 0.1em 0.35em;
      border-radius: 4px;
    }}
    pre {{
      background: #15202b;
      border: 1px solid #38444d;
      border-radius: 12px;
      padding: 0.9rem 1rem;
      overflow-x: auto;
    }}
    pre code {{ background: transparent; padding: 0; }}
    table {{
      width: 100%;
      border-collapse: collapse;
      margin: 0.75rem 0 1.25rem;
      font-size: 0.95rem;
    }}
    th, td {{
      border: 1px solid #38444d;
      padding: 0.45rem 0.55rem;
      text-align: left;
      vertical-align: top;
    }}
    th {{ background: #15202b; }}
    .muted {{ color: #8b98a5; }}
  </style>
</head>
<body>
  <main class="wrap">
    {body}
  </main>
</body>
</html>
"""
