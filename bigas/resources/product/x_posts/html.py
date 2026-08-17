"""Mobile-friendly HTML for X-post human-in-the-loop pages."""
from __future__ import annotations

import html
from typing import Any, Dict, List
from urllib.parse import urlencode

from bigas.providers.notifications.x import TWEET_MAX_CHARS


def _page(title: str, body: str) -> str:
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
      max-width: 32rem;
      margin: 0 auto;
      padding: 1.5rem 1.25rem 2.5rem;
    }}
    h1 {{ font-size: 1.35rem; margin: 0 0 0.75rem; }}
    p, li {{ line-height: 1.45; }}
    .card {{
      background: #15202b;
      border: 1px solid #38444d;
      border-radius: 12px;
      padding: 1rem 1.1rem;
      margin: 1rem 0;
      white-space: pre-wrap;
    }}
    label {{
      display: block;
      font-size: 0.9rem;
      color: #8b98a5;
      margin: 1rem 0 0.4rem;
    }}
    textarea {{
      width: 100%;
      box-sizing: border-box;
      min-height: 8rem;
      padding: 0.9rem 1rem;
      border: 1px solid #38444d;
      border-radius: 12px;
      background: #15202b;
      color: #e7e9ea;
      font: inherit;
      line-height: 1.45;
      resize: vertical;
    }}
    .count {{
      color: #8b98a5;
      font-size: 0.85rem;
      text-align: right;
      margin: 0.35rem 0 0;
    }}
    .count.over {{ color: #f4212e; }}
    .muted {{ color: #8b98a5; font-size: 0.95rem; }}
    .actions {{
      display: flex;
      flex-direction: column;
      gap: 0.75rem;
      margin-top: 1.25rem;
    }}
    button {{
      appearance: none;
      border: 0;
      border-radius: 999px;
      padding: 0.9rem 1rem;
      font-size: 1.05rem;
      font-weight: 650;
      cursor: pointer;
    }}
    .approve {{ background: #1d9bf0; color: #fff; }}
    .decline {{ background: #273340; color: #e7e9ea; }}
    .ok {{ color: #00ba7c; }}
    .warn {{ color: #ffad1f; }}
    .err {{ color: #f4212e; }}
  </style>
</head>
<body>
  <div class="wrap">
    {body}
  </div>
</body>
</html>
"""


def preview_page(draft: Dict[str, Any], *, action_base: str, token: str) -> str:
    accounts = ", ".join(str(a) for a in (draft.get("accounts") or []))
    tweets: List[str] = [str(t) for t in (draft.get("tweets") or [])] or [""]
    editors = []
    numbered = len(tweets) > 1
    for i, tweet in enumerate(tweets, start=1):
        label = f"Tweet {i}" if numbered else "Tweet"
        editors.append(
            f"""
      <label for="tweet-{i}">{html.escape(label)}</label>
      <textarea id="tweet-{i}" name="tweets" maxlength="{TWEET_MAX_CHARS}" rows="6">{html.escape(tweet)}</textarea>
      <p class="count" data-count-for="tweet-{i}">{len(tweet)} / {TWEET_MAX_CHARS}</p>"""
        )
    query = urlencode({"token": token})
    approve_url = f"{action_base}/approve?{query}"
    decline_url = f"{action_base}/decline?{query}"
    body = f"""
    <h1>Approve X post?</h1>
    <p class="muted">Accounts: {html.escape(accounts or "(none)")}</p>
    <form method="post" action="{html.escape(approve_url)}">
      {''.join(editors)}
      <p class="muted">Edit the text if needed, then approve to publish to the accounts above. Decline deletes this draft from storage.</p>
      <div class="actions">
        <button class="approve" type="submit">Approve and post</button>
      </div>
    </form>
    <form method="post" action="{html.escape(decline_url)}">
      <div class="actions">
        <button class="decline" type="submit">Decline</button>
      </div>
    </form>
    <script>
      const maxChars = {TWEET_MAX_CHARS};
      document.querySelectorAll("textarea[name=tweets]").forEach((el) => {{
        const count = document.querySelector('[data-count-for="' + el.id + '"]');
        const sync = () => {{
          const n = el.value.length;
          if (count) {{
            count.textContent = n + " / " + maxChars;
            count.classList.toggle("over", n > maxChars);
          }}
        }};
        el.addEventListener("input", sync);
        sync();
      }});
    </script>
    """
    return _page("Approve X post", body)


def success_page(*, title: str, message: str, extra: str = "") -> str:
    extra_html = f'<div class="card">{html.escape(extra)}</div>' if extra else ""
    body = f"""
    <h1 class="ok">{html.escape(title)}</h1>
    <p>{html.escape(message)}</p>
    {extra_html}
    """
    return _page(title, body)


def partial_success_page(*, title: str, message: str, extra: str = "") -> str:
    extra_html = f'<div class="card">{html.escape(extra)}</div>' if extra else ""
    body = f"""
    <h1 class="warn">{html.escape(title)}</h1>
    <p>{html.escape(message)}</p>
    {extra_html}
    """
    return _page(title, body)


def error_page(*, title: str, message: str, extra: str = "") -> str:
    extra_html = f'<div class="card">{html.escape(extra)}</div>' if extra else ""
    body = f"""
    <h1 class="err">{html.escape(title)}</h1>
    <p>{html.escape(message)}</p>
    {extra_html}
    """
    return _page(title, body)
