from __future__ import annotations

import threading
import time
import uuid
from typing import Any



_SILENCE_MARKERS = (
    "silence", "stay quiet", "remain silent", "no interaction", "no clear",
    "没有明确", "无需回复", "不需要回复", "继续旁观", "保持沉默", "不打扰",
)


def _is_silence_thought(item):
    if not isinstance(item, dict):
        return False
    if bool(item.get("is_silence")):
        return True
    text = str(item.get("text") or "").casefold()
    return any(marker in text for marker in _SILENCE_MARKERS)

class ThoughtBuffer:
    """In-memory, short-lived, competing thoughts. Not a task queue."""

    def __init__(self, max_per_group: int = 24):
        self.max_per_group = max(4, int(max_per_group or 24))
        self.lock = threading.RLock()
        self.state: dict[str, list[dict[str, Any]]] = {}

    def _group(self, group_id: str) -> list[dict[str, Any]]:
        return self.state.setdefault(str(group_id or "unknown"), [])

    def observe(self, group_id: str, thoughts: list[Any]) -> list[dict[str, Any]]:
        now = time.time()
        normalized = []
        with self.lock:
            items = self._group(group_id)
            for item in items:
                if item.get("state") == "competing":
                    item["state"] = "deferred"
            for raw in thoughts or []:
                if not isinstance(raw, dict):
                    continue
                text = str(raw.get("text") or "").strip()
                if not text:
                    continue
                normalized.append({
                    "id": str(raw.get("id") or ("tb_" + uuid.uuid4().hex[:8])),
                    "state": str(raw.get("state") or "transient"),
                    "direction": str(raw.get("direction") or ""),
                    "text": text,
                    "is_silence": bool(raw.get("is_silence")),
                    "interest": float(raw.get("interest") or 0.0),
                    "social_fit": float(raw.get("social_fit") or 0.0),
                    "risk": float(raw.get("risk") or 0.0),
                    "confidence": float(raw.get("confidence") or 0.0),
                    "novelty": float(raw.get("novelty") or 0.0),
                    "humor_potential": float(raw.get("humor_potential") or 0.0),
                    "interaction_hook": float(raw.get("interaction_hook") or 0.0),
                    "conversational_momentum": float(raw.get("conversational_momentum") or 0.0),
                    "unexpectedness": float(raw.get("unexpectedness") or 0.0),
                    "social_contribution": float(raw.get("social_contribution") or 0.0),
                    "thread_creation": float(raw.get("thread_creation") or 0.0),
                    "created_at": now,
                    "last_seen_at": now,
                })
            items.extend(normalized)
            self.state[str(group_id or "unknown")] = items[-self.max_per_group:]
        return normalized

    def snapshot(self, group_id: str, limit: int = 8, include_silence: bool = False) -> list[dict[str, Any]]:
        with self.lock:
            items = list(self._group(group_id))
        active = [
            item for item in items
            if item.get("state") != "abandoned"
            and (include_silence or not _is_silence_thought(item))
        ]
        active.sort(
            key=lambda item: (
                float(item.get("interest") or 0.0),
                float(item.get("confidence") or 0.0),
                float(item.get("last_seen_at") or 0.0),
            ),
            reverse=True,
        )
        return active[:limit]

    def interrupt(self, group_id: str, reason: str = "") -> int:
        changed = 0
        with self.lock:
            for item in self._group(group_id):
                if item.get("state") in {"transient", "competing", "deferred"}:
                    item["state"] = "interrupted"
                    item["interrupt_reason"] = str(reason or "")[:120]
                    changed += 1
        return changed

    def mark(self, group_id: str, thought_id: str, state: str) -> bool:
        with self.lock:
            for item in self._group(group_id):
                if str(item.get("id") or "") == str(thought_id or ""):
                    item["state"] = str(state or "transient")
                    item["last_seen_at"] = time.time()
                    return True
        return False


class AttentionBuffer:
    """Small competition board for current attention candidates."""

    def __init__(self, max_items: int = 8):
        self.max_items = max(2, int(max_items or 8))
        self.lock = threading.RLock()
        self.state: dict[str, list[dict[str, Any]]] = {}

    def update(self, group_id: str, items: list[Any]) -> list[dict[str, Any]]:
        normalized = []
        now = time.time()
        with self.lock:
            for raw in items or []:
                if isinstance(raw, str):
                    raw = {"focus": raw}
                if not isinstance(raw, dict):
                    continue
                focus = str(raw.get("focus") or raw.get("text") or "").strip()
                if not focus:
                    continue
                normalized.append({
                    "focus": focus,
                    "why_relevant": str(raw.get("why_relevant") or ""),
                    "salience": float(raw.get("salience") or raw.get("interest") or 0.0),
                    "updated_at": now,
                })
            existing = self.state.get(str(group_id or "unknown"), [])
            combined = normalized + existing
            combined.sort(key=lambda item: float(item.get("salience") or 0.0), reverse=True)
            self.state[str(group_id or "unknown")] = combined[:self.max_items]
        return normalized

    def snapshot(self, group_id: str, limit: int = 5) -> list[dict[str, Any]]:
        with self.lock:
            return list(self.state.get(str(group_id or "unknown"), []))[:limit]