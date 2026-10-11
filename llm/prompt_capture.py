"""Thread-safe snapshots of prompts sent to the LLM provider.

The provider is also used for memory/style/compression helpers.  Those helper
requests must not replace the prompt shown by ``/prompt``: the command is meant
to inspect the latest normal reply request.  Keep normal and auxiliary
snapshots separate while retaining the old ``get_last_prompt()`` API.
"""

from __future__ import annotations

import json
import copy
import threading
import time


_LOCK = threading.RLock()
_LAST_PROMPT = ""  # Backwards-compatible alias for the latest normal request.
_LAST_NORMAL_PROMPT = ""
_LAST_AUXILIARY_PROMPT = ""
_LAST_METADATA = {}
_LAST_NORMAL_MESSAGES = []


def _render_message(message, index):
    if isinstance(message, dict):
        role = str(message.get("role") or "unknown")
        content = message.get("content")
        lines = [f"[{index}] role={role}"]
        if content not in (None, ""):
            lines.append(str(content))
        for key in ("name", "tool_call_id"):
            if message.get(key) not in (None, ""):
                lines.append(f"{key}={message[key]}")
        if message.get("tool_calls"):
            lines.append(
                "tool_calls="
                + json.dumps(message["tool_calls"], ensure_ascii=False, default=str)
            )
        return "\n".join(lines)

    return f"[{index}] role=unknown\n{str(message)}"


def capture_prompt(messages, kind="auxiliary", metadata=None) -> str:
    """Capture a provider request without mixing normal and helper prompts.

    ``kind`` is intentionally metadata-only and never enters the router body.
    Normal reply kinds are ``normal_reply``; every other kind is considered an
    auxiliary request unless the caller explicitly supplies a different value.
    """
    global _LAST_PROMPT, _LAST_NORMAL_PROMPT, _LAST_AUXILIARY_PROMPT, _LAST_METADATA, _LAST_NORMAL_MESSAGES
    rendered = "\n\n".join(
        _render_message(message, index)
        for index, message in enumerate(messages or [], 1)
    ).strip()
    normalized_kind = str(kind or "auxiliary").strip().lower() or "auxiliary"
    is_normal = normalized_kind in {"normal_reply", "direct_reply", "proactive_reply"}
    info = dict(metadata) if isinstance(metadata, dict) else {}
    info.update({
        "kind": normalized_kind,
        "captured_at": time.time(),
        "message_count": len(messages or []),
        "characters": len(rendered),
    })
    with _LOCK:
        if is_normal:
            _LAST_NORMAL_PROMPT = rendered
            _LAST_PROMPT = rendered
            _LAST_NORMAL_MESSAGES = copy.deepcopy(messages or [])
        else:
            _LAST_AUXILIARY_PROMPT = rendered
        _LAST_METADATA = info
    return rendered


def get_last_prompt(kind="normal") -> str:
    """Return the latest normal prompt by default, or an auxiliary snapshot."""
    with _LOCK:
        normalized = str(kind or "normal").strip().lower()
        if normalized in {"auxiliary", "aux", "helper"}:
            return _LAST_AUXILIARY_PROMPT
        return _LAST_NORMAL_PROMPT or _LAST_PROMPT


def get_last_prompt_metadata() -> dict:
    with _LOCK:
        return dict(_LAST_METADATA)

def get_last_prompt_messages():
    """Return a safe structured copy for real business-prompt cache diagnostics."""
    with _LOCK:
        return copy.deepcopy(_LAST_NORMAL_MESSAGES)
