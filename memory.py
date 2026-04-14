"""
In-memory interview feedback storage.

Structure (per spec):
  memory = {
    candidate_key: {
      round_name: "markdown content for that round",
    }
  }

Candidate keys are normalized (case/whitespace) for reliable lookup; display names
are preserved for responses. Designed so a future `VectorMemoryBackend` or
`SqliteMemoryBackend` can implement the same protocol.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Protocol


def normalize_candidate_key(name: str) -> str:
    return " ".join(name.strip().split()).casefold()


def build_round_markdown(
    round_name: str,
    strengths: str,
    weaknesses: str,
    notes: str,
    decision: str,
) -> str:
    rn = round_name.strip() or "unknown_round"
    return (
        f"## Round: {rn}\n\n"
        f"*Strengths:*\n{_indent_bullets(strengths)}\n"
        f"*Weaknesses:*\n{_indent_bullets(weaknesses)}\n"
        f"*Notes:*\n{_indent_bullets(notes)}\n"
        f"*Decision:*\n{_indent_bullets(decision)}\n"
    )


def _indent_bullets(text: str) -> str:
    t = (text or "").strip()
    if not t:
        return "  * _(none)_"
    lines = []
    for line in t.splitlines():
        line = line.strip()
        if not line:
            continue
        if not line.startswith(("-", "*", "•")):
            line = f"- {line}"
        lines.append(f"  {line}")
    return "\n".join(lines) if lines else "  * _(none)_"


def _parse_bullet_block(md: str) -> str:
    """
    Convert an indented bullet block back to a plain newline-separated string.
    Handles the placeholder "  * _(none)_" as empty.
    """
    out: List[str] = []
    for line in (md or "").splitlines():
        s = line.strip()
        if not s:
            continue
        if s == "* _(none)_" or s == "* _(none)_" or s == "• _(none)_" or s == "- _(none)_":
            continue
        # drop leading bullet marker
        s = re.sub(r"^(?:[-*•]\s+)", "", s)
        if s:
            out.append(s)
    return "\n".join(out).strip()


def parse_round_markdown_fields(round_markdown: str) -> Dict[str, str]:
    """
    Parse the markdown fragment produced by build_round_markdown back into fields.
    Returns keys: strengths, weaknesses, notes, decision.
    """
    text = round_markdown or ""
    def _section(label: str) -> str:
        # label like "Strengths"
        m = re.search(
            rf"(?is)\*{re.escape(label)}:\*\s*\n(.*?)(?=\n\*[A-Za-z]+:\*\s*\n|\Z)",
            text,
        )
        if not m:
            return ""
        return _parse_bullet_block(m.group(1))

    return {
        "strengths": _section("Strengths"),
        "weaknesses": _section("Weaknesses"),
        "notes": _section("Notes"),
        "decision": _section("Decision"),
    }


class MemoryBackend(Protocol):
    """Hook for future DB / vector / ATS-backed storage."""

    def save_round(
        self,
        candidate_display: str,
        round_name: str,
        markdown: str,
    ) -> str: ...

    def get_candidate_markdown(self, candidate_key: str) -> Optional[str]: ...

    def list_candidates(self) -> List[str]: ...

    def memory_snapshot(self) -> Dict[str, Dict[str, str]]: ...


@dataclass
class MarkdownMemoryManager:
    """
    Nested dict: candidate_key -> round_name -> markdown fragment for that round.
    Full candidate doc is header + concatenated round sections.
    """

    _memory: Dict[str, Dict[str, str]] = field(default_factory=dict)
    _display_names: Dict[str, str] = field(default_factory=dict)

    def _ensure_display(self, key: str, display: str) -> None:
        display = " ".join(display.strip().split())
        if key not in self._display_names and display:
            self._display_names[key] = display

    def save_round(
        self,
        candidate_display: str,
        round_name: str,
        markdown: str,
    ) -> str:
        key = normalize_candidate_key(candidate_display)
        self._ensure_display(key, candidate_display)
        rnd = round_name.strip() or "default"
        # Update/replace round content (dedupe by round key, not by text)
        self._memory.setdefault(key, {})[rnd] = markdown.strip()
        return key

    def get_rounds(self, candidate_key: str) -> Dict[str, str]:
        k = normalize_candidate_key(candidate_key)
        return dict(self._memory.get(k, {}))

    def candidate_exists(self, candidate_key: str) -> bool:
        return normalize_candidate_key(candidate_key) in self._memory

    def display_name(self, candidate_key: str) -> str:
        k = normalize_candidate_key(candidate_key)
        return self._display_names.get(k, candidate_key)

    def build_candidate_doc(self, candidate_key: str) -> Optional[str]:
        k = normalize_candidate_key(candidate_key)
        rounds = self._memory.get(k)
        if not rounds:
            return None
        title = self.display_name(k)
        parts = [f"# Candidate: {title}\n"]
        rnd_keys = sorted(rounds.keys(), key=lambda x: x.lower())
        for i, rnd in enumerate(rnd_keys):
            parts.append(rounds[rnd])
            # Separator only *between* rounds (trailing --- looked like a chopped message in Slack)
            if i < len(rnd_keys) - 1:
                parts.append("\n---\n")
        return "\n".join(parts).rstrip() + "\n"

    def get_candidate_markdown(self, candidate_key: str) -> Optional[str]:
        return self.build_candidate_doc(candidate_key)

    def list_candidates(self) -> List[str]:
        return sorted(
            self._display_names.get(k, k) for k in self._memory.keys()
        )

    def all_feedback_text(self) -> str:
        """Flattened corpus for QA / retrieval (bounded in agent)."""
        chunks: List[str] = []
        for ck in sorted(self._memory.keys()):
            doc = self.build_candidate_doc(ck)
            if doc:
                chunks.append(doc)
        return "\n\n".join(chunks)

    def memory_snapshot(self) -> Dict[str, Dict[str, str]]:
        """Expose nested structure for debugging / future export."""
        return {k: dict(v) for k, v in self._memory.items()}

    def search_candidates_substring(self, query: str) -> List[str]:
        """Cheap retrieval: match query against display names and markdown."""
        q = query.casefold().strip()
        if not q:
            return self.list_candidates()
        hits: List[str] = []
        for ck in self._memory:
            disp = self.display_name(ck).casefold()
            blob = (disp + "\n" + self.build_candidate_doc(ck) or "").casefold()
            if q in blob or q in ck:
                hits.append(self.display_name(ck))
        return sorted(set(hits))

    def clear_candidate(self, candidate_key: str) -> bool:
        """
        Remove all stored rounds for a candidate.
        Returns True if anything was removed.
        """
        k = normalize_candidate_key(candidate_key)
        existed = k in self._memory
        if existed:
            self._memory.pop(k, None)
        # Always drop display name too, if present.
        self._display_names.pop(k, None)
        return existed

    def clear_all(self) -> int:
        """Remove all stored candidates. Returns number of candidates removed."""
        n = len(self._memory)
        self._memory.clear()
        self._display_names.clear()
        return n
