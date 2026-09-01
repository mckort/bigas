"""Mobile-friendly HTML for X-post human-in-the-loop pages."""
from __future__ import annotations

import html
from typing import Any, Dict, List
from urllib.parse import urlencode

from bigas.providers.notifications.x import TWEET_MAX_CHARS
from bigas.resources.product.x_posts.prompts import product_label_for_project_keys
from bigas.resources.product.x_posts.service import draft_posts


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
    h2 {{
      font-size: 1.15rem;
      margin: 0 0 0.2rem;
    }}
    p, li {{ line-height: 1.45; }}
    .card {{
      background: #15202b;
      border: 1px solid #38444d;
      border-radius: 12px;
      padding: 1rem 1.1rem;
      margin: 1rem 0;
      white-space: pre-wrap;
    }}
    .account-card {{
      background: #15202b;
      border: 1px solid #38444d;
      border-radius: 12px;
      padding: 1rem 1.1rem 1.15rem;
      margin: 1.15rem 0;
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
      background: #0f1419;
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
    .notice {{
      background: #052e16;
      border: 1px solid #166534;
      color: #86efac;
      border-radius: 12px;
      padding: 0.75rem 1rem;
      margin: 0 0 1rem;
    }}
    .notice.warn {{
      background: #422006;
      border-color: #a16207;
      color: #fde68a;
    }}
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


def _tweet_editors(account: str, tweets: List[str]) -> str:
    values = tweets or [""]
    numbered = len(values) > 1
    blocks = []
    safe_account = html.escape(account)
    for i, tweet in enumerate(values, start=1):
        field_id = f"tweet-{safe_account}-{i}"
        label = f"Tweet {i}" if numbered else "Tweet"
        blocks.append(
            f"""
      <label for="{field_id}">{html.escape(label)}</label>
      <textarea id="{field_id}" name="tweets" maxlength="{TWEET_MAX_CHARS}" rows="6">{html.escape(tweet)}</textarea>
      <p class="count" data-count-for="{field_id}">{len(tweet)} / {TWEET_MAX_CHARS}</p>"""
        )
    return "".join(blocks)


def preview_page(
    draft: Dict[str, Any],
    *,
    action_base: str,
    token: str,
    notice: str = "",
    notice_kind: str = "ok",
) -> str:
    posts = draft_posts(draft)
    query = urlencode({"token": token})
    approve_url = f"{action_base}/approve?{query}"
    decline_url = f"{action_base}/decline?{query}"
    cards = []
    for post in posts:
        account = str(post.get("account") or "").strip()
        tweets = [str(t) for t in (post.get("tweets") or [])] or [""]
        keys = post.get("project_keys") or []
        label = product_label_for_project_keys(keys) if keys else ""
        subtitle = ""
        if label and label != "the product":
            subtitle = f'<p class="muted">{html.escape(label)}</p>'
        cards.append(
            f"""
    <section class="account-card">
      <h2>@{html.escape(account)}</h2>
      {subtitle}
      <form method="post" action="{html.escape(approve_url)}">
        <input type="hidden" name="account" value="{html.escape(account)}">
        {_tweet_editors(account, tweets)}
        <div class="actions">
          <button class="approve" type="submit">Approve and post</button>
        </div>
      </form>
      <form method="post" action="{html.escape(decline_url)}">
        <input type="hidden" name="account" value="{html.escape(account)}">
        <div class="actions">
          <button class="decline" type="submit">Skip</button>
        </div>
      </form>
    </section>"""
        )
    notice_html = ""
    if notice:
        kind = "warn" if notice_kind == "warn" else "ok"
        notice_html = f'<p class="notice {kind}">{html.escape(notice)}</p>'
    heading = "Approve X posts" if len(posts) != 1 else "Approve X post"
    body = f"""
    <h1>{html.escape(heading)}</h1>
    {notice_html}
    <p class="muted">Each post is for one X account. Edit if needed, then approve or skip that account. Skip deletes only that draft.</p>
      {''.join(cards) or '<p class="muted">No pending posts.</p>'}
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
    return _page(heading, body)


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
