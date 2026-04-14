"""
Pluggable tools for the agent. Web search is intentionally simple and only used
when memory cannot answer (see agent routing).

Future: swap `web_search` with Tavily, SerpAPI, or an internal enterprise search.
"""

from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
from html import unescape
from typing import Any, Dict, List


def web_search(query: str, max_chars: int = 3500) -> str:
    """
    Return a short text digest for `query` using DuckDuckGo Instant Answer API.

    No extra pip packages; suitable for optional enrichment only.
    """
    q = (query or "").strip()
    if not q:
        return "No search query provided."

    url = (
        "https://api.duckduckgo.com/?"
        + urllib.parse.urlencode(
            {
                "q": q,
                "format": "json",
                "no_html": "1",
                "skip_disambig": "1",
            }
        )
    )
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "InterviewFeedbackAgent/1.0"},
        )
        with urllib.request.urlopen(req, timeout=12) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        data: Dict[str, Any] = json.loads(raw)
    except Exception as exc:  # noqa: BLE001 — surface as tool output
        return f"Web search failed: {exc}"

    parts: List[str] = []

    abst = (data.get("AbstractText") or "").strip()
    if abst:
        parts.append(abst)

    ans = (data.get("Answer") or "").strip()
    if ans and ans not in abst:
        parts.append(ans)

    for t in data.get("RelatedTopics") or []:
        if isinstance(t, dict):
            tx = (t.get("Text") or "").strip()
            if tx:
                parts.append(tx)
        if len("\n".join(parts)) > max_chars:
            break

    if not parts:
        return (
            "No instant answer from web search for this query. "
            "Try rephrasing or narrowing the question."
        )

    text = "\n\n".join(parts)
    if len(text) > max_chars:
        text = text[: max_chars - 3] + "..."
    # Strip excessive HTML entities / noise
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _html_to_text(html: str, max_chars: int = 25_000) -> str:
    """
    Very lightweight HTML → text. Keeps it dependency-free.
    Not perfect, but good enough for JD extraction.
    """
    s = html or ""
    # Drop scripts/styles
    s = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", s)
    s = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", s)
    # Insert line breaks for block-ish tags
    s = re.sub(r"(?is)</(p|div|li|ul|ol|h1|h2|h3|h4|br|section|article)>", "\n", s)
    # Drop remaining tags
    s = re.sub(r"(?is)<[^>]+>", " ", s)
    s = unescape(s)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n\s+\n", "\n\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    s = s.strip()
    if len(s) > max_chars:
        return s[: max_chars - 40] + "\n\n…(truncated)"
    return s


def scrape_jd_via_serpapi(jd_url: str, max_chars: int = 25_000) -> str:
    """Backward-compatible alias; now uses Serper scrape endpoint."""
    return scrape_jd_via_serper(jd_url, max_chars=max_chars)


def scrape_jd_via_serper(jd_url: str, max_chars: int = 25_000) -> str:
    """
    Scrape job description content using Serper.dev scraping endpoint.

    POST https://scrape.serper.dev
    Headers: X-API-KEY, Content-Type: application/json
    Body: {"url": "...", "includeMarkdown": true}
    """
    url = (jd_url or "").strip()
    if not url:
        return "No JD URL provided."

    api_key = os.environ.get("SERPER_API_KEY")
    if not api_key:
        return "Serper API key missing. Set `SERPER_API_KEY`."

    payload = json.dumps({"url": url, "includeMarkdown": True}).encode("utf-8")
    req = urllib.request.Request(
        "https://scrape.serper.dev",
        data=payload,
        method="POST",
        headers={
            "X-API-KEY": api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "InterviewFeedbackAgent/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=35) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        data: Dict[str, Any] = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        return f"JD scrape failed (Serper): {exc}"

    markdown = (data.get("markdown") or "").strip()
    if markdown:
        if len(markdown) > max_chars:
            markdown = markdown[: max_chars - 40] + "\n\n…(truncated)"
        return f"Source: {url}\n\n{markdown}"

    # Fallbacks (different wrappers sometimes return text/html)
    text = (data.get("text") or "").strip()
    if text:
        if len(text) > max_chars:
            text = text[: max_chars - 40] + "\n\n…(truncated)"
        return f"Source: {url}\n\n{text}"

    html = (data.get("html") or "").strip()
    if html:
        return f"Source: {url}\n\n{_html_to_text(html, max_chars=max_chars)}"

    return f"Could not extract JD text from {url}."


def tool_registry() -> Dict[str, Any]:
    """Future: register scoring, ATS fetch, summarization backends."""
    return {
        "web_search": web_search,
        "scrape_jd_via_serpapi": scrape_jd_via_serpapi,
        "scrape_jd_via_serper": scrape_jd_via_serper,
    }
