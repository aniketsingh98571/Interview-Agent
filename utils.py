"""
Parsing, Slack text cleanup, and Ollama HTTP client helpers.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


# --- Slack ---

_MENTION_RE = re.compile(r"<@[A-Z0-9]+>")
_BR_RE = re.compile(r"(?i)<br\s*/?>")
# Placeholder so <br> inside a table row does not split the line before pipe-table parsing.
_BR_PLACEHOLDER = "\u2063"  # invisible separator, unlikely in user text


def _md_bold_to_slack(s: str) -> str:
    """Markdown **bold** → Slack *bold* (single pair)."""
    return re.sub(r"\*\*(.+?)\*\*", r"*\1*", s)


def _is_table_row(line: str) -> bool:
    s = line.strip()
    return len(s) >= 2 and s.startswith("|") and s.count("|") >= 2


def _split_table_row(line: str) -> List[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def _is_separator_row(line: str) -> bool:
    if not _is_table_row(line):
        return False
    inner = line.strip()[1:-1]
    parts = [p.strip() for p in inner.split("|") if p.strip()]
    if not parts:
        return False
    if not all(re.match(r"^:?-+:?$", p) for p in parts):
        return False
    return any("-" in p for p in parts)


def _strip_cell(cell: str) -> str:
    return cell.strip()


def _row_looks_like_table_header(cells: List[str]) -> bool:
    if not cells:
        return False
    first = cells[0].replace("*", "").strip().lower()
    if first.isdigit():
        return False
    joined = " ".join(c.lower() for c in cells)
    if first in ("#", "no.", "num", "n", "no"):
        return True
    return any(
        k in joined
        for k in ("question", "answer", "sample", "topic", "round", "interview")
    )


def _format_slack_table(header: List[str], data_rows: List[List[str]]) -> List[str]:
    """Turn parsed markdown table rows into Slack mrkdwn (no pipe tables)."""
    if not data_rows:
        return []
    h = [_strip_cell(x) for x in header] if header else []
    out: List[str] = []

    for row in data_rows:
        if not row:
            continue
        if len(row) >= 3:
            num_cell = _strip_cell(row[0])
            m_num = re.match(r"^(?:\*\*|\*)(.+?)(?:\*\*|\*)$", num_cell.strip())
            num_plain = m_num.group(1).strip() if m_num else num_cell.strip()
            q = _md_bold_to_slack(_strip_cell(row[1]))
            a = _md_bold_to_slack(" ".join(_strip_cell(c) for c in row[2:]))
            a = a.replace(_BR_PLACEHOLDER, "\n")
            lab_q = h[1] if len(h) > 1 else "Question"
            lab_a = h[2] if len(h) > 2 else "Sample answer"
            out.append(f"*{num_plain}.* *{lab_q}:* {q}")
            out.append(f"*{lab_a}:* {a}")
            out.append("")
        elif len(row) >= 2:
            a = _md_bold_to_slack(_strip_cell(row[0]))
            b = _md_bold_to_slack(" ".join(_strip_cell(c) for c in row[1:]))
            b = b.replace(_BR_PLACEHOLDER, "\n")
            lab_a = h[0] if len(h) > 0 else "Item"
            lab_b = h[1] if len(h) > 1 else "Detail"
            out.append(f"*{lab_a}:* {a}")
            out.append(f"*{lab_b}:* {b}")
            out.append("")
        else:
            out.append(f"• {_strip_cell(row[0])}")
            out.append("")

    while out and out[-1] == "":
        out.pop()
    return out


def _convert_markdown_tables(lines: List[str]) -> List[str]:
    """Replace GitHub-style pipe tables with Slack-friendly blocks."""
    out: List[str] = []
    i = 0
    n = len(lines)
    while i < n:
        if not _is_table_row(lines[i]):
            out.append(lines[i])
            i += 1
            continue

        row0 = _split_table_row(lines[i])
        if i + 1 < n and _is_separator_row(lines[i + 1]):
            header = row0
            i += 2
            data_rows: List[List[str]] = []
            while i < n and _is_table_row(lines[i]) and not _is_separator_row(lines[i]):
                data_rows.append(_split_table_row(lines[i]))
                i += 1
            out.extend(_format_slack_table(header, data_rows))
            continue

        block_lines: List[str] = []
        j = i
        while j < n and _is_table_row(lines[j]) and not _is_separator_row(lines[j]):
            block_lines.append(lines[j])
            j += 1
        block = [_split_table_row(line) for line in block_lines]
        i = j

        if len(block) >= 2 and _row_looks_like_table_header(block[0]):
            out.extend(_format_slack_table(block[0], block[1:]))
        elif len(block) >= 1:
            out.extend(_format_slack_table([], block))

    return out


def strip_bot_mention(text: str, bot_user_id: Optional[str] = None) -> str:
    """Remove <@U123> tokens; optionally keep only first mention handling."""
    t = _MENTION_RE.sub("", text or "")
    if bot_user_id:
        t = t.replace(f"<@{bot_user_id}>", "")
    return " ".join(t.split()).strip()


def format_interview_doc_for_slack(text: str) -> str:
    """
    Turn stored / LLM markdown into Slack mrkdwn. Slack does not render # / ## headers;
    lone * with spaces also breaks bold, so we normalize section labels.
    Also converts pipe-markdown tables (not supported in Slack) into labeled blocks,
    and turns HTML <br> tags into newlines.
    """
    if not (text or "").strip():
        return text
    # Normalize common non-Slack markdown constructs early.
    # Slack uses *bold* (single asterisk), so convert Markdown **bold** first.
    text = _md_bold_to_slack(text)
    text = _BR_RE.sub(_BR_PLACEHOLDER, text)
    lines = _convert_markdown_tables(text.splitlines())
    text = "\n".join(lines)
    out: list[str] = []
    for line in text.splitlines():
        s = line.rstrip()
        stripped = s.strip()

        if stripped == "---":
            out.append("")
            continue

        m = re.match(r"^#+\s*Candidate:\s*(.+)$", stripped)
        if m:
            out.append(f"👤 *Candidate:* {m.group(1).strip()}")
            continue

        m = re.match(r"^#+\s*Round:\s*(.+)$", stripped)
        if m:
            if out and out[-1] != "":
                out.append("")
            out.append(f"📋 *Round:* {m.group(1).strip()}")
            continue

        if re.match(r"^#{1,2}\s+", stripped) and not re.match(
            r"^#+\s*(Candidate|Round):", stripped
        ):
            rest = stripped.lstrip("#").strip()
            if rest:
                out.append(f"*{rest}*")
            continue

        m = re.match(r"^\*\s+([A-Za-z][A-Za-z\s]+):\s*$", s)
        if m:
            out.append(f"*{m.group(1).strip()}:*")
            continue

        out.append(_md_bold_to_slack(s))

    result = "\n".join(out)
    while "\n\n\n" in result:
        result = result.replace("\n\n\n", "\n\n")
    result = result.replace(_BR_PLACEHOLDER, "\n")
    return result.strip()


# --- Command parsing (deterministic routing layer) ---

@dataclass
class SaveFeedbackParse:
    candidate: str
    round_name: str
    feedback_body: str


@dataclass
class UpdateFeedbackParse:
    candidate: str
    round_name: str
    feedback_body: str


@dataclass
class AppendFeedbackParse:
    candidate: str
    round_name: str
    feedback_body: str


@dataclass
class JdVerdictParse:
    candidate: str
    jd_url: str


def parse_jd_verdict(text: str) -> Optional[JdVerdictParse]:
    """
    Examples:
      verdict Omkar https://keka.com/careers/...
      jd verdict Omkar: https://...
      select verdict for Omkar from https://...
    """
    t = (text or "").strip()
    if not t:
        return None
    if not re.search(r"(?is)\b(verdict|jd verdict|job description|jd)\b", t):
        return None
    m = re.search(r"(?is)(https?://\S+)", t)
    if not m:
        return None
    url = m.group(1).strip().rstrip(")>]}.," )
    rest = (t[: m.start()] + " " + t[m.end() :]).strip()

    # Common natural language: "can we hire <name>?"
    mhire = re.search(r"(?is)\bhire\s+([A-Za-z][A-Za-z\s.'-]{1,60})", rest)
    if mhire:
        cand = " ".join(mhire.group(1).strip().split())
        # Chop trailing question tail if it got captured
        cand = re.split(r"(?is)\b(with|based|given|for|from|on)\b", cand)[0].strip()
        if cand:
            return JdVerdictParse(candidate=cand, jd_url=url)

    # Common: "verdict for <name>"
    mfor = re.search(r"(?is)\b(?:verdict|jd verdict)\b.*?\bfor\s+([A-Za-z][A-Za-z\s.'-]{1,60})", rest)
    if mfor:
        cand = " ".join(mfor.group(1).strip().split())
        cand = re.split(r"(?is)\b(with|based|given|from|on)\b", cand)[0].strip()
        if cand:
            return JdVerdictParse(candidate=cand, jd_url=url)

    # Fallback: remove command-y words and keep remaining as candidate guess.
    cleaned = re.sub(
        r"(?is)\b(based|this|that|jd|job description|verdict|for|of|from|please|pls|kindly|do you think|can we|should we|with reasoning|reasoning)\b",
        " ",
        rest,
    )
    cleaned = re.sub(r"(?is)[^A-Za-z\s.'-]+", " ", cleaned)
    cand = " ".join(cleaned.split()).strip(":-—")
    if not cand:
        return None
    return JdVerdictParse(candidate=cand, jd_url=url)


def parse_save_feedback(text: str) -> Optional[SaveFeedbackParse]:
    """
    Supported forms:
      save feedback Candidate | Round Name: feedback...
      save feedback Candidate RoundToken: feedback...   (round = last token before ':')

    Case-insensitive on the leading verb.
    """
    raw = (text or "").strip()
    m = re.match(r"(?is)^(?:save|store)\s+feedback\s+(.+)$", raw)
    if not m:
        return None
    rest = m.group(1).strip()
    if ":" not in rest:
        return None
    head, body = rest.split(":", 1)
    head = head.strip()
    body = body.strip()
    if not body:
        return None

    if "|" in head:
        left, right = head.split("|", 1)
        cand = left.strip()
        rnd = right.strip()
        if cand and rnd:
            return SaveFeedbackParse(
                candidate=cand, round_name=rnd, feedback_body=body
            )
        return None

    # Last token before colon is round; rest is candidate name
    parts = head.split()
    if len(parts) < 2:
        return None
    rnd = parts[-1]
    cand = " ".join(parts[:-1])
    return SaveFeedbackParse(
        candidate=cand, round_name=rnd, feedback_body=body
    )


def parse_update_feedback(text: str) -> Optional[UpdateFeedbackParse]:
    """
    Supported forms:
      update feedback Candidate | Round Name: notes...
      update feedback Candidate RoundToken: notes...   (round = last token before ':')
      edit/modify feedback ... (same as update)
    """
    raw = (text or "").strip()
    m = re.match(r"(?is)^(?:update|edit|modify)\s+feedback\s+(.+)$", raw)
    if not m:
        return None
    rest = m.group(1).strip()
    if ":" not in rest:
        return None
    head, body = rest.split(":", 1)
    head = head.strip()
    body = body.strip()
    if not body:
        return None

    if "|" in head:
        left, right = head.split("|", 1)
        cand = left.strip()
        rnd = right.strip()
        if cand and rnd:
            return UpdateFeedbackParse(
                candidate=cand, round_name=rnd, feedback_body=body
            )
        return None

    parts = head.split()
    if len(parts) < 2:
        return None
    rnd = parts[-1]
    cand = " ".join(parts[:-1])
    return UpdateFeedbackParse(
        candidate=cand, round_name=rnd, feedback_body=body
    )


def parse_append_feedback(text: str) -> Optional[AppendFeedbackParse]:
    """
    Supported forms:
      append feedback Candidate | Round Name: notes...
      append feedback Candidate RoundToken: notes...   (round = last token before ':')
      add to feedback ... (alias)
    """
    raw = (text or "").strip()
    m = re.match(r"(?is)^(?:append|add)\s+(?:to\s+)?feedback\s+(.+)$", raw)
    if not m:
        return None
    rest = m.group(1).strip()
    if ":" not in rest:
        return None
    head, body = rest.split(":", 1)
    head = head.strip()
    body = body.strip()
    if not body:
        return None

    if "|" in head:
        left, right = head.split("|", 1)
        cand = left.strip()
        rnd = right.strip()
        if cand and rnd:
            return AppendFeedbackParse(
                candidate=cand, round_name=rnd, feedback_body=body
            )
        return None

    parts = head.split()
    if len(parts) < 2:
        return None
    rnd = parts[-1]
    cand = " ".join(parts[:-1])
    return AppendFeedbackParse(
        candidate=cand, round_name=rnd, feedback_body=body
    )


def parse_target_name_after_keyword(
    text: str, keyword_pattern: str
) -> Optional[str]:
    m = re.match(keyword_pattern, (text or "").strip(), re.IGNORECASE | re.DOTALL)
    if not m:
        return None
    name = (m.group(1) or "").strip()
    return name or None


def parse_get_feedback(text: str) -> Optional[str]:
    return parse_target_name_after_keyword(
        text,
        r"(?is)^(?:get|fetch|retrieve|show|list)\s+feedback\s+(.+)$",
    )


def parse_summarize(text: str) -> Optional[str]:
    return parse_target_name_after_keyword(
        text,
        r"(?is)^summarize\s+(.+)$",
    )


def parse_clear_memory(text: str) -> Optional[str]:
    """
    Clear commands:
      - "clear memory"
      - "clear memory for Omkar"
      - "delete feedback Omkar"
    Returns an optional candidate name. If None, caller may decide to clear all.
    """
    t = (text or "").strip()
    if not t:
        return None
    low = t.lower()
    if not re.search(r"(?is)\b(clear|delete|remove|purge|wipe)\b", low):
        return None
    # Accept common typos for "memory" seen in Slack messages.
    if not re.search(r"(?is)\b(memory|memroy|mmeory|memeory|notes|feedback|records)\b", low):
        return None

    m = re.search(r"(?is)\b(?:for|of)\s+(.+)$", t)
    if m:
        name = m.group(1).strip()
        return name or None

    # Try: "clear out Omkar's memory" / "remove Omkar feedback"
    m = re.search(r"(?is)\b(clear|delete|remove|purge|wipe)\b.*?\b(.+?)\b(?:memory|notes|feedback|records)\b", t)
    if m:
        cand = (m.group(2) or "").strip().strip("'\"")
        cand = re.sub(r"(?is)\b(out|the|a|an|this|that|these|those|pls|please)\b", "", cand).strip()
        return cand or None
    return ""


def extract_json_from_llm(text: str) -> Optional[Dict[str, Any]]:
    """Parse a JSON object from model output (raw JSON or embedded in text)."""
    text = (text or "").strip()
    try:
        out = json.loads(text)
        return out if isinstance(out, dict) else None
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        try:
            out = json.loads(m.group(0))
            return out if isinstance(out, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def llm_extract_save_feedback(
    user_text: str,
    thread_context: Optional[str] = None,
) -> Optional[SaveFeedbackParse]:
    """
    Free-form save: e.g. "record this", "save Rushil's notes from above".
    Uses thread_context when the user refers to "this" / earlier messages.
    """
    ctx = ""
    if (thread_context or "").strip():
        ctx = (
            "\n\nEarlier messages in this conversation (user may refer to \"this\"):\n"
            f"{thread_context.strip()}\n"
        )
    prompt = (
        "You extract interview feedback to save. Reply with JSON only, no markdown fences.\n"
        'Schema: {"candidate": string, "round": string, "feedback": string}\n'
        "Rules:\n"
        '- "candidate": person\'s name; if only one person is discussed in the thread, use that name.\n'
        '- "round": e.g. "Round 1", "Phone screen", or "General" if unknown.\n'
        '- "feedback": full interview notes to store — prefer copying the relevant earlier messages '
        "verbatim when the user says \"this\" / \"that\".\n"
        '- If there is nothing to save, use empty strings for all fields.\n'
        "- Do not invent interviews or companies; only use what is written.\n\n"
        f"User message:\n{(user_text or '').strip()!r}\n"
        f"{ctx}"
    )
    try:
        raw = ollama_chat([{"role": "user", "content": prompt}])
    except Exception:
        return None
    data = extract_json_from_llm(raw) or {}
    cand = str(data.get("candidate", "") or "").strip()
    rnd = str(data.get("round", "") or "").strip() or "General"
    body = str(data.get("feedback", "") or "").strip()
    if not body:
        return None
    if not cand:
        return None
    return SaveFeedbackParse(candidate=cand, round_name=rnd, feedback_body=body)


def llm_extract_update_feedback(
    user_text: str,
    thread_context: Optional[str] = None,
) -> Optional[UpdateFeedbackParse]:
    """
    Free-form update: e.g. "update Omkar's feedback from above — add decision: reject".
    Uses thread_context when the user refers to "this" / earlier messages.
    """
    ctx = ""
    if (thread_context or "").strip():
        ctx = (
            "\n\nEarlier messages in this conversation (user may refer to \"this\"):\n"
            f"{thread_context.strip()}\n"
        )
    prompt = (
        "You extract interview feedback to UPDATE (replace) for an existing record.\n"
        "Reply with JSON only, no markdown fences.\n"
        'Schema: {"candidate": string, "round": string, "feedback": string}\n'
        "Rules:\n"
        '- "candidate": person\'s name; if only one person is discussed in the thread, use that name.\n'
        '- "round": e.g. "Round 1", "Phone screen", or "General" if unknown.\n'
        '- "feedback": the full updated notes to store (replacement content), not a diff.\n'
        "- Do not invent interviews or companies; only use what is written.\n\n"
        f"User message:\n{(user_text or '').strip()!r}\n"
        f"{ctx}"
    )
    try:
        raw = ollama_chat([{"role": "user", "content": prompt}])
    except Exception:
        return None
    data = extract_json_from_llm(raw) or {}
    cand = str(data.get("candidate", "") or "").strip()
    rnd = str(data.get("round", "") or "").strip() or "General"
    body = str(data.get("feedback", "") or "").strip()
    if not body or not cand:
        return None
    return UpdateFeedbackParse(candidate=cand, round_name=rnd, feedback_body=body)


def llm_extract_append_feedback(
    user_text: str,
    thread_context: Optional[str] = None,
) -> Optional[AppendFeedbackParse]:
    """
    Free-form append: e.g. "add this to Omkar's feedback" or "append: decision changed".
    Uses thread_context when the user refers to "this" / earlier messages.
    """
    ctx = ""
    if (thread_context or "").strip():
        ctx = (
            "\n\nEarlier messages in this conversation (user may refer to \"this\"):\n"
            f"{thread_context.strip()}\n"
        )
    prompt = (
        "You extract interview feedback to APPEND (add) to an existing record.\n"
        "Reply with JSON only, no markdown fences.\n"
        'Schema: {"candidate": string, "round": string, "feedback": string}\n'
        "Rules:\n"
        '- "candidate": person\'s name; if only one person is discussed in the thread, use that name.\n'
        '- "round": e.g. "Round 1", "Phone screen", or "General" if unknown.\n'
        '- "feedback": ONLY the new incremental notes to append (not the whole existing record).\n'
        "- Do not invent interviews or companies; only use what is written.\n\n"
        f"User message:\n{(user_text or '').strip()!r}\n"
        f"{ctx}"
    )
    try:
        raw = ollama_chat([{"role": "user", "content": prompt}])
    except Exception:
        return None
    data = extract_json_from_llm(raw) or {}
    cand = str(data.get("candidate", "") or "").strip()
    rnd = str(data.get("round", "") or "").strip() or "General"
    body = str(data.get("feedback", "") or "").strip()
    if not body or not cand:
        return None
    return AppendFeedbackParse(candidate=cand, round_name=rnd, feedback_body=body)


def llm_extract_candidate_name(
    user_text: str,
    *,
    mode: str,
    known_candidates: Optional[List[str]] = None,
) -> Optional[str]:
    """
    Infer candidate name for get/summarize from natural phrasing
    ("what do we have on Rushil", "summarize Jane's interviews").
    mode is 'retrieve' or 'summarize'.
    """
    kc = known_candidates or []
    known_block = ""
    if kc:
        known_block = "\nKnown candidates in memory (prefer exact match if listed):\n" + "\n".join(
            f"- {n}" for n in kc[:40]
        )
    verb = "fetching stored feedback" if mode == "retrieve" else "summarizing stored feedback"
    prompt = (
        f"The user wants help {verb}. Extract exactly ONE candidate name they mean.\n"
        "Reply with JSON only: {\"candidate\": string}.\n"
        'If no specific person is named or inferable, use {"candidate": ""}.\n'
        f"{known_block}\n\n"
        f"User message:\n{(user_text or '').strip()!r}\n"
    )
    try:
        raw = ollama_chat([{"role": "user", "content": prompt}])
    except Exception:
        return None
    data = extract_json_from_llm(raw) or {}
    name = str(data.get("candidate", "") or "").strip()
    return name or None


# --- Ollama (local or cloud) ---

def ollama_chat(
    messages: List[Dict[str, str]],
    model: Optional[str] = None,
    stream: bool = False,
    timeout: int = 120,
) -> str:
    """
    POST /api/chat — works for local Ollama and https://ollama.com with Bearer key.

    Env:
      OLLAMA_BASE_URL — default https://ollama.com or http://127.0.0.1:11434
      OLLAMA_API_KEY — optional; sent as Authorization: Bearer …
      OLLAMA_MODEL — default model name
    """
    base = (os.environ.get("OLLAMA_BASE_URL") or "https://ollama.com").rstrip("/")
    if not base.endswith("/api"):
        chat_url = f"{base}/api/chat"
    else:
        chat_url = f"{base}/chat"

    mdl = model or os.environ.get("OLLAMA_MODEL") or "llama3.2:latest"
    payload: Dict[str, Any] = {
        "model": mdl,
        "messages": messages,
        "stream": stream,
    }
    data = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    key = os.environ.get("OLLAMA_API_KEY") or os.environ.get("OLLAMA_TOKEN")
    if key:
        headers["Authorization"] = f"Bearer {key}"

    req = urllib.request.Request(chat_url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        hint = ""
        if exc.code == 404 and "not found" in body.lower():
            hint = (
                " — ollama.com does not host every library model (e.g. llama3.2 may be missing). "
                "List names: curl -s -H \"Authorization: Bearer $OLLAMA_API_KEY\" https://ollama.com/api/tags "
                "then set OLLAMA_MODEL (e.g. gpt-oss:20b). "
                "For local: ollama pull llama3.2:latest && OLLAMA_BASE_URL=http://127.0.0.1:11434"
            )
        raise RuntimeError(f"Ollama HTTP {exc.code}: {body}{hint}") from exc
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Ollama request failed: {exc}") from exc

    obj = json.loads(raw)
    msg = obj.get("message") or {}
    content = (msg.get("content") or "").strip()
    if not content:
        raise RuntimeError(f"Ollama returned empty content: {obj!r}")
    return content


def reasoning_chat(messages: List[Dict[str, str]], timeout: int = 180) -> str:
    """
    Call a stronger reasoning model when available.

    Env:
      OLLAMA_REASONING_MODEL — optional override for deeper reasoning tasks.
    """
    model = os.environ.get("OLLAMA_REASONING_MODEL") or os.environ.get("OLLAMA_MODEL")
    return ollama_chat(messages, model=model, timeout=timeout)


def is_gratitude_or_light_chat(text: str) -> bool:
    """
    True for thanks, short acknowledgments, or casual chat — not task-oriented feedback commands.
    Used so we never mis-route these to STORE_FEEDBACK.
    """
    t = (text or "").strip()
    if not t or len(t) > 280:
        return False
    low = t.lower()
    # Obvious task verbs → not "just chatting"
    if re.match(
        r"(?is)^(save|store|get|fetch|retrieve|show|list|summarize|record|remember|add|put)\b",
        low,
    ):
        return False
    if re.search(
        r"\b(save|store)\s+feedback\b|\bget\s+feedback\b|\brecord\s+this\b",
        low,
    ):
        return False

    if re.search(
        r"\b(thank you|thanks|thx\b|tysm|much appreciated|appreciate it|cheers)\b",
        low,
    ):
        # "thanks, now save this" is still a task
        if re.search(
            r"(?is)\b(save|store|record|remember|feedback|get feedback|summarize|notes for)\b",
            low,
        ):
            return False
        return True
    if re.match(
        r"(?is)^(that\x27s|that is|thats)\s+(great|wonderful|perfect|awesome|amazing|helpful|nice)\b",
        low,
    ):
        return True
    if re.match(r"(?is)^(ok|okay|cool|nice|got it|perfect|great|awesome)\s*[!.\s]*$", low):
        return True
    if re.match(r"(?is)^(hi|hello|hey|good morning|good afternoon)\b", low) and len(t) < 50:
        return True
    if re.match(r"(?is)^(bye|goodbye|see you|cya)\b", low):
        return True
    return False


def heuristic_intent_label(text: str) -> Optional[str]:
    """Fast path for unambiguous commands; returns STORE_FEEDBACK / UPDATE_FEEDBACK / APPEND_FEEDBACK / JD_VERDICT / RETRIEVE / SUMMARIZE / CLEAR_MEMORY."""
    t = (text or "").strip()
    low = t.lower()
    if re.match(r"(?is)^(save|store)\s+feedback\s+", t):
        return "STORE_FEEDBACK"
    if re.match(r"(?is)^(update|edit|modify)\s+feedback\s+", t):
        return "UPDATE_FEEDBACK"
    if re.match(r"(?is)^(append|add)\s+(?:to\s+)?feedback\s+", t):
        return "APPEND_FEEDBACK"
    if parse_jd_verdict(t) is not None:
        return "JD_VERDICT"
    # Natural phrases: "record this", "save that", "remember the notes above"
    if re.search(
        r"(?is)\b(save|record|store|remember|log|capture)\s+(this|that|it|these|those)\b",
        t,
    ):
        return "STORE_FEEDBACK"
    if re.search(r"(?is)\b(add|put)\s+(this|that|it)\s+to\s+(memory|notes)\b", t):
        return "STORE_FEEDBACK"
    if re.search(
        r"(?is)\b(save|record|store)\s+(the\s+)?(notes|feedback|interview|write-?up)\b",
        t,
    ):
        return "STORE_FEEDBACK"
    if re.match(
        r"(?is)^(get|fetch|retrieve|show|list)\s+feedback\s+", t
    ):
        return "RETRIEVE_FEEDBACK"
    if re.match(r"(?is)^summarize\s+", t):
        return "SUMMARIZE"
    if re.search(r"(?is)^(summarize|summary|recap)\s+(for|of|on)\s+\S+", low):
        return "SUMMARIZE"
    if parse_clear_memory(t) is not None:
        return "CLEAR_MEMORY"
    return None
