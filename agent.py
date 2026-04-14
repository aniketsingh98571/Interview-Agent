"""
Interview feedback agent: intent routing, memory-first QA, optional web search.

Extension points:
  - `IntentClassifier` can be swapped for a fine-tuned model or rules engine.
  - `InterviewAgent` accepts injectable `memory` and `tools` for multi-agent / DB.
"""

from __future__ import annotations

import json
import re
from enum import Enum
from typing import Callable, Dict, List, Optional, Tuple

from memory import (
    MarkdownMemoryManager,
    build_round_markdown,
    normalize_candidate_key,
    parse_round_markdown_fields,
)
from tools import scrape_jd_via_serpapi, web_search
from utils import (
    AppendFeedbackParse,
    JdVerdictParse,
    SaveFeedbackParse,
    format_interview_doc_for_slack,
    heuristic_intent_label,
    is_gratitude_or_light_chat,
    llm_extract_candidate_name,
    llm_extract_save_feedback,
    llm_extract_append_feedback,
    llm_extract_update_feedback,
    ollama_chat,
    parse_jd_verdict,
    parse_clear_memory,
    parse_append_feedback,
    parse_get_feedback,
    parse_save_feedback,
    parse_summarize,
    parse_update_feedback,
    reasoning_chat,
)


class Intent(str, Enum):
    STORE_FEEDBACK = "STORE_FEEDBACK"
    UPDATE_FEEDBACK = "UPDATE_FEEDBACK"
    APPEND_FEEDBACK = "APPEND_FEEDBACK"
    JD_VERDICT = "JD_VERDICT"
    RETRIEVE_FEEDBACK = "RETRIEVE_FEEDBACK"
    QUESTION = "QUESTION"
    SUMMARIZE = "SUMMARIZE"
    CLEAR_MEMORY = "CLEAR_MEMORY"
    HELP = "HELP"


class IntentClassifier:
    """Rule-first; LLM disambiguation for short or unclear messages."""

    def __init__(self, classify_fn: Optional[Callable[..., str]] = None) -> None:
        self._classify_fn = classify_fn or ollama_chat

    def classify(self, text: str) -> Intent:
        h = heuristic_intent_label(text)
        if h == "STORE_FEEDBACK":
            return Intent.STORE_FEEDBACK
        if h == "UPDATE_FEEDBACK":
            return Intent.UPDATE_FEEDBACK
        if h == "APPEND_FEEDBACK":
            return Intent.APPEND_FEEDBACK
        if h == "JD_VERDICT":
            return Intent.JD_VERDICT
        if h == "RETRIEVE_FEEDBACK":
            return Intent.RETRIEVE_FEEDBACK
        if h == "SUMMARIZE":
            return Intent.SUMMARIZE
        if h == "CLEAR_MEMORY":
            return Intent.CLEAR_MEMORY

        t = (text or "").strip()
        if not t:
            return Intent.HELP
        if len(t) < 3:
            return Intent.HELP

        low = t.lower()
        if any(
            low.startswith(x)
            for x in ("help", "commands", "what can you do", "usage")
        ):
            return Intent.HELP

        # Short messages without keywords: ask LLM (QUESTION vs HELP)
        prompt = (
            "Classify the user's Slack message to ONE label.\n"
            "Labels: STORE_FEEDBACK, UPDATE_FEEDBACK, APPEND_FEEDBACK, JD_VERDICT, RETRIEVE_FEEDBACK, SUMMARIZE, CLEAR_MEMORY, QUESTION, HELP.\n"
            "STORE_FEEDBACK: user wants to save interview notes (e.g. "
            '"record this", "save that", "remember these notes").\n'
            "UPDATE_FEEDBACK: user wants to overwrite/replace existing interview notes for a candidate/round.\n"
            "APPEND_FEEDBACK: user wants to add new notes to an existing candidate/round without removing prior notes.\n"
            "JD_VERDICT: user provides a job description (JD) URL and asks for a select/reject verdict for a candidate.\n"
            "Never STORE_FEEDBACK for: thank-you, gratitude, small talk, or replies that do not ask to save notes.\n"
            "RETRIEVE_FEEDBACK: user wants stored feedback for a candidate.\n"
            "SUMMARIZE: user wants a summary of a candidate's interviews.\n"
            "CLEAR_MEMORY: user wants to delete/clear saved notes/feedback/memory (for a candidate or all).\n"
            "QUESTION: questions, casual chat, or anything that is not clearly save/get/summarize/help.\n"
            "HELP: asking what the bot can do.\n"
            "Reply with exactly one word: the label.\n\n"
            f"Message: {t!r}"
        )
        try:
            raw = self._classify_fn(
                [{"role": "user", "content": prompt}],
            )
            label = raw.strip().split()[0].upper().replace(".", "")
            label = label.strip("`\"'")
            return Intent(label)
        except (ValueError, IndexError):
            return Intent.QUESTION
        except Exception:
            return Intent.QUESTION


def _extract_json_object(text: str) -> Optional[dict]:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    return None


def structure_feedback_fields(raw_notes: str) -> Tuple[str, str, str, str]:
    """Map free-form feedback into structured sections without inventing facts."""
    raw_notes = (raw_notes or "").strip()
    if not raw_notes:
        return ("", "", "", "")

    def _deterministic_sections(text: str) -> Tuple[str, str, str, str]:
        """
        Cheap parser for common patterns like:
          Strengths: ...
          Weaknesses: ...
          Notes: ...
          Decision: ...
        Works even when the LLM is unavailable.
        """
        labels = ("strengths", "weaknesses", "notes", "decision")
        found: Dict[str, List[str]] = {k: [] for k in labels}

        current: Optional[str] = None
        for raw_line in (text or "").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            m = re.match(r"(?is)^(strengths|weaknesses|notes|decision)\s*:\s*(.*)$", line)
            if m:
                current = m.group(1).lower()
                rest = (m.group(2) or "").strip()
                if rest:
                    found[current].append(rest)
                continue
            if current:
                found[current].append(line)

        if any(v for v in found.values()):
            def _join(k: str) -> str:
                return "\n".join(found[k]).strip()
            return (_join("strengths"), _join("weaknesses"), _join("notes"), _join("decision"))

        # Inline "strengths: x; weaknesses: y" in a single line.
        inline = {}
        for k in labels:
            m = re.search(rf"(?is)\b{k}\s*:\s*([^;]+)", text)
            if m:
                inline[k] = m.group(1).strip()
        if inline:
            return (
                inline.get("strengths", ""),
                inline.get("weaknesses", ""),
                inline.get("notes", ""),
                inline.get("decision", ""),
            )
        return ("", "", "", "")

    ds = _deterministic_sections(raw_notes)
    if any(x.strip() for x in ds):
        return ds

    prompt = (
        "You structure interview notes into JSON. Rules:\n"
        '- Keys: "strengths", "weaknesses", "notes", "decision".\n'
        "- Each value is a single string; use bullet phrases separated by semicolons if needed.\n"
        "- Copy only what is implied in the input; if a field is not supported by the text, use an empty string.\n"
        "- Do not invent candidate outcomes or skills.\n\n"
        f"Raw notes:\n{raw_notes}\n\n"
        "Respond with JSON only."
    )
    try:
        out = ollama_chat([{"role": "user", "content": prompt}])
        data = _extract_json_object(out) or {}
        s = str(data.get("strengths", "") or "").strip()
        w = str(data.get("weaknesses", "") or "").strip()
        n = str(data.get("notes", "") or "").strip()
        d = str(data.get("decision", "") or "").strip()
        if not (s or w or n or d):
            raise ValueError("empty structure")
        return (s, w, n, d)
    except Exception:
        return ("", "", raw_notes, "")


class InterviewAgent:
    def __init__(
        self,
        memory: Optional[MarkdownMemoryManager] = None,
        classifier: Optional[IntentClassifier] = None,
    ) -> None:
        self.memory = memory or MarkdownMemoryManager()
        self.classifier = classifier or IntentClassifier()

    # --- Public API ---

    def handle_message(self, text: str, thread_context: Optional[str] = None) -> str:
        if is_gratitude_or_light_chat(text):
            return self._handle_conversational(text)
        intent = self.classifier.classify(text)
        if intent == Intent.STORE_FEEDBACK:
            return self._handle_store(text, thread_context)
        if intent == Intent.UPDATE_FEEDBACK:
            return self._handle_update(text, thread_context)
        if intent == Intent.APPEND_FEEDBACK:
            return self._handle_append(text, thread_context)
        if intent == Intent.JD_VERDICT:
            return self._handle_jd_verdict(text)
        if intent == Intent.RETRIEVE_FEEDBACK:
            return self._handle_retrieve(text)
        if intent == Intent.SUMMARIZE:
            return self._handle_summarize(text)
        if intent == Intent.CLEAR_MEMORY:
            return self._handle_clear(text)
        if intent == Intent.HELP:
            return self._help_text()
        return self._handle_question(text)

    def _handle_jd_verdict(self, text: str) -> str:
        parsed: Optional[JdVerdictParse] = parse_jd_verdict(text)
        if not parsed:
            return (
                "Please provide a candidate name and a JD URL.\n"
                "Example: `verdict Omkar https://keka.com/...`"
            )

        # If the parser guessed wrong (common in natural language), try matching
        # against candidates already in memory using the whole message.
        cand = parsed.candidate.strip()
        key = normalize_candidate_key(cand)
        if not self.memory.candidate_exists(key):
            hits = self.memory.search_candidates_substring(text)
            if len(hits) == 1:
                cand = hits[0]
                key = normalize_candidate_key(cand)
            elif hits:
                return (
                    "Which candidate should I evaluate?\n"
                    "Matched candidates:\n" + "\n".join(f"• {h}" for h in hits[:10])
                )

        if not self.memory.candidate_exists(key):
            return (
                f"No stored feedback for *{cand}*.\n"
                "Save feedback first, then ask for a verdict."
            )

        feedback_md = self.memory.get_candidate_markdown(key) or ""
        jd_text = scrape_jd_via_serpapi(parsed.jd_url)

        prompt = (
            "You are an interview committee assistant.\n"
            "Given (1) a Job Description and (2) interview feedback notes, produce a hiring verdict.\n\n"
            "Output format for Slack:\n"
            "• *Verdict:* Select / No-select / Hold\n"
            "• *Reasons (evidence-based):* 3-7 bullets. Each bullet must cite either JD or Feedback.\n"
            "• *Risks / gaps:* 2-6 bullets\n"
            "Rules:\n"
            "- Do not invent skills or experiences not present in the feedback.\n"
            "- If the JD text is incomplete, say so and proceed cautiously.\n"
            "- Prefer concrete mapping: requirement -> evidence.\n\n"
            f"JD (scraped):\n{jd_text}\n\n"
            f"Feedback (stored):\n{feedback_md}\n"
        )
        try:
            raw = reasoning_chat([{"role": "user", "content": prompt}])
            return format_interview_doc_for_slack(raw)
        except Exception as exc:  # noqa: BLE001
            return f"Verdict generation failed: {exc}"

    # --- Handlers ---

    def _handle_conversational(self, text: str) -> str:
        """Short friendly replies — no save/retrieve errors for thanks or hello."""
        low = (text or "").strip().lower()
        if re.search(r"\b(bye|goodbye|see you|cya)\b", low):
            return "Take care — ping me anytime you need interview notes."
        if re.search(r"\b(thank|thanks|appreciate|cheers)\b", low):
            return (
                "You're welcome! If you need anything else — saving notes, pulling up a candidate, "
                "or a quick summary — just ask."
            )
        if re.match(r"(?is)^(hi|hello|hey|good morning|good afternoon)\b", low):
            return (
                "Hi! I help with interview feedback: save notes, retrieve them, or summarize a candidate. "
                "Say *help* for examples."
            )
        if re.match(r"(?is)^(ok|okay|cool|nice|got it|perfect|great)\s*[!.\s]*$", low):
            return "Sounds good! Let me know if you want to save or look up any feedback."
        return (
            "I'm here when you need me — try *help* for what I can do, or ask anything about your saved candidates."
        )

    def _handle_store(self, text: str, thread_context: Optional[str] = None) -> str:
        parsed = parse_save_feedback(text)
        if not parsed:
            parsed = llm_extract_save_feedback(text, thread_context)
        if not parsed:
            if is_gratitude_or_light_chat(text):
                return self._handle_conversational(text)
            return (
                "I couldn’t infer *who* to save this for or what text to store.\n"
                "If you meant to save interview notes: reply *in the thread* under the write-up with "
                "*record this* or say the candidate and round in one message. "
                "Or say *help* for examples — no stress."
            )

        strengths, weaknesses, notes, decision = structure_feedback_fields(
            parsed.feedback_body
        )
        md = build_round_markdown(
            parsed.round_name,
            strengths,
            weaknesses,
            notes,
            decision,
        )
        key = self.memory.save_round(parsed.candidate, parsed.round_name, md)
        disp = self.memory.display_name(key)
        return (
            f"Saved feedback for *{disp}* — round *{parsed.round_name.strip()}* "
            f"(key `{key}`)."
        )

    def _handle_update(self, text: str, thread_context: Optional[str] = None) -> str:
        parsed = parse_update_feedback(text)
        if not parsed:
            parsed = llm_extract_update_feedback(text, thread_context)
        if not parsed:
            return (
                "I couldn’t infer what to update.\n"
                "Try: `update feedback Candidate | Round Name: notes` "
                "or `update feedback Candidate Round: notes`."
            )

        key = normalize_candidate_key(parsed.candidate)
        rounds = self.memory.get_rounds(key)
        existed_round = (parsed.round_name.strip() or "default") in rounds

        strengths, weaknesses, notes, decision = structure_feedback_fields(
            parsed.feedback_body
        )
        md = build_round_markdown(
            parsed.round_name,
            strengths,
            weaknesses,
            notes,
            decision,
        )
        saved_key = self.memory.save_round(parsed.candidate, parsed.round_name, md)
        disp = self.memory.display_name(saved_key)

        action = "Updated" if existed_round else "Created"
        return f"{action} feedback for *{disp}* — round *{parsed.round_name.strip()}*."

    @staticmethod
    def _append_text(old: str, new: str) -> str:
        o = (old or "").strip()
        n = (new or "").strip()
        if not n:
            return o
        if not o:
            return n
        return o.rstrip() + "\n" + n

    def _handle_append(self, text: str, thread_context: Optional[str] = None) -> str:
        parsed: Optional[AppendFeedbackParse] = parse_append_feedback(text)
        if not parsed:
            parsed = llm_extract_append_feedback(text, thread_context)
        if not parsed:
            return (
                "I couldn’t infer what to append.\n"
                "Try: `append feedback Candidate | Round Name: notes` "
                "or `append feedback Candidate Round: notes`."
            )

        key = normalize_candidate_key(parsed.candidate)
        round_key = parsed.round_name.strip() or "default"
        existing_md = self.memory.get_rounds(key).get(round_key, "")

        existing = parse_round_markdown_fields(existing_md) if existing_md else {
            "strengths": "",
            "weaknesses": "",
            "notes": "",
            "decision": "",
        }

        strengths, weaknesses, notes, decision = structure_feedback_fields(
            parsed.feedback_body
        )
        merged_strengths = self._append_text(existing.get("strengths", ""), strengths)
        merged_weaknesses = self._append_text(existing.get("weaknesses", ""), weaknesses)
        merged_notes = self._append_text(existing.get("notes", ""), notes)
        merged_decision = self._append_text(existing.get("decision", ""), decision)

        md = build_round_markdown(
            parsed.round_name,
            merged_strengths,
            merged_weaknesses,
            merged_notes,
            merged_decision,
        )
        saved_key = self.memory.save_round(parsed.candidate, parsed.round_name, md)
        disp = self.memory.display_name(saved_key)

        action = "Appended to" if existing_md else "Created"
        return f"{action} feedback for *{disp}* — round *{parsed.round_name.strip()}*."

    def _handle_retrieve(self, text: str) -> str:
        name = parse_get_feedback(text)
        if not name:
            name = llm_extract_candidate_name(
                text,
                mode="retrieve",
                known_candidates=self.memory.list_candidates(),
            )
        if not name:
            return (
                "Which candidate should I look up? "
                "e.g. *get feedback Rushil* or *show me notes on Jane*."
            )

        key = normalize_candidate_key(name)
        if not self.memory.candidate_exists(key):
            known = self.memory.list_candidates()
            hint = ""
            if known:
                hint = "\n\nKnown candidates:\n" + "\n".join(
                    f"• {c}" for c in known[:20]
                )
            return (
                f"No stored feedback for *{name}*. "
                f"Check spelling or save feedback first.{hint}"
            )

        doc = self.memory.get_candidate_markdown(name)
        if not doc:
            return "_No content._"
        return format_interview_doc_for_slack(doc)

    def _handle_clear(self, text: str) -> str:
        parsed = parse_clear_memory(text)
        # parsed is None => not a clear request (shouldn't happen if routed correctly)
        # parsed is "" => clear request but no candidate extracted
        name = (parsed or "").strip() if parsed is not None else ""

        if not name:
            # Prefer deterministic matching against current memory.
            hits = self.memory.search_candidates_substring(text)
            if len(hits) == 1:
                name = hits[0]
            else:
                # Fallback: LLM extraction for pronouns ("clear out his memory")
                name = llm_extract_candidate_name(
                    text,
                    mode="retrieve",
                    known_candidates=self.memory.list_candidates(),
                ) or ""

        if name:
            key = normalize_candidate_key(name)
            existed = self.memory.clear_candidate(key)
            disp = self.memory.display_name(key) if existed else " ".join(name.split()).strip()
            if existed:
                return f"*Memory cleared.* No records remain for *{disp}*."
            return f"No stored records found for *{disp}*."

        removed = self.memory.clear_all()
        if removed == 0:
            return "Memory is already empty."
        return f"*Memory cleared.* Removed {removed} candidate record(s)."

    def _handle_summarize(self, text: str) -> str:
        name = parse_summarize(text)
        if not name:
            name = llm_extract_candidate_name(
                text,
                mode="summarize",
                known_candidates=self.memory.list_candidates(),
            )
        if not name:
            return "Who should I summarize? e.g. *summarize Rushil* or *recap of Jane’s interviews*."

        if not self.memory.candidate_exists(name):
            return (
                f"No stored feedback for *{name}*. "
                "Use `get feedback <name>` after saving notes."
            )

        doc = self.memory.get_candidate_markdown(name) or ""
        prompt = (
            "Summarize the interview feedback below for a hiring committee.\n"
            "Output sections:\n"
            "• *Strengths summary* — bullet list\n"
            "• *Weaknesses summary* — bullet list\n"
            "• *Hiring recommendation* — one short paragraph (only from evidence in the text; "
            "if unclear, say evidence is insufficient).\n"
            "Do not invent interviews or scores.\n"
            "Format for Slack: use *bold* for labels, lines starting with • or - for bullets. "
            "Do not use # or ## markdown headers.\n\n"
            f"Feedback markdown:\n{doc}"
        )
        try:
            raw = ollama_chat([{"role": "user", "content": prompt}])
            return format_interview_doc_for_slack(raw)
        except Exception as exc:  # noqa: BLE001
            return f"Summarization failed: {exc}"

    def _memory_context(self, question: str, max_chars: int = 12000) -> str:
        corpus = self.memory.all_feedback_text()
        if not corpus.strip():
            return ""
        if len(corpus) > max_chars:
            return corpus[: max_chars - 20] + "\n…(truncated)"
        return corpus

    def _handle_question(self, text: str) -> str:
        """Memory-first; web search only if the model signals it."""
        mem = self._memory_context(text)
        gate = (
            "You are an assistant for interview feedback stored in memory.\n"
            "Format answers for Slack mrkdwn: use *bold*, bullets (• or -), and short sections. "
            "Do not use markdown pipe tables or HTML tags like <br>.\n"
            "Answer using ONLY the Memory section when it contains the needed facts.\n"
            "If Memory does not contain enough information, reply starting with exactly:\n"
            "WEB_SEARCH:\n"
            "followed by a concise search query on the next line.\n"
            "If Memory is empty and the question is about candidates/interviews, start with WEB_SEARCH.\n"
            "If the question is unrelated to hiring and Memory is empty, you may answer from general knowledge "
            "without WEB_SEARCH.\n"
            "For candidate-specific facts, never guess — use Memory or WEB_SEARCH.\n\n"
        )
        user_block = f"Question:\n{text.strip()}\n\nMemory:\n{mem or '_(empty)_'}\n"
        try:
            reply = ollama_chat(
                [{"role": "user", "content": gate + user_block}],
            )
        except Exception as exc:  # noqa: BLE001
            return f"LLM error: {exc}"

        if reply.strip().upper().startswith("WEB_SEARCH:"):
            qline = reply.splitlines()
            query = ""
            for line in qline[1:]:
                line = line.strip()
                if line:
                    query = line
                    break
            if not query:
                query = text.strip()
            ws = web_search(query)
            follow = (
                "Synthesize a concise answer for Slack using Memory (if relevant) and Web results.\n"
                "Use *bold* and bullets; do not use markdown tables or HTML <br> tags.\n"
                "If Web results are irrelevant, say so.\n\n"
                f"Question: {text.strip()}\n\nMemory:\n{mem or '_(empty)_'}\n\n"
                f"Web results:\n{ws}\n"
            )
            try:
                raw_follow = ollama_chat([{"role": "user", "content": follow}])
                return format_interview_doc_for_slack(raw_follow)
            except Exception as exc:  # noqa: BLE001
                return f"Web search retrieved data but follow-up failed: {exc}\n\n{ws[:1500]}"

        return format_interview_doc_for_slack(reply)

    @staticmethod
    def _help_text() -> str:
        return (
            "*Interview feedback agent*\n\n"
            "• Natural language: *record this*, *save that*, *remember these notes* "
            "(works best when you reply *in the thread* under the interview write-up).\n"
            "• Or: `save feedback Candidate Round: notes` "
            "or `save feedback Candidate | Round Name: notes`\n"
            "• Update: `update feedback Candidate Round: notes` "
            "or `update feedback Candidate | Round Name: notes`\n"
            "• Append: `append feedback Candidate Round: notes` "
            "or `append feedback Candidate | Round Name: notes`\n"
            "• JD verdict: `verdict Candidate https://...` — scrape JD + decide select/no-select\n"
            "• *get feedback …* or *show notes on …* — full markdown\n"
            "• *summarize …* — strengths / weaknesses / recommendation\n"
            "• *clear memory for …* — delete a candidate’s stored notes\n"
            "• *clear memory* — delete all stored notes\n"
            "• Ask any question — memory first; web search only if needed\n\n"
            "_In-memory only; restarting the process clears data._"
        )


def build_default_agent() -> InterviewAgent:
    return InterviewAgent()
