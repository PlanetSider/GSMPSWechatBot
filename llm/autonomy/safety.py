from __future__ import annotations

from typing import Any

from .tools import is_other_bot_command_allowed


DANGEROUS_MARKERS = (
    "kick", "ban", "delete", "remove", "shutdown", "restart", "reboot",
    "token", "password", "passwd", "secret", "transfer", "pay", "admin",
    "exec", "eval", "rm -", "drop table", "truncate",
)


def classify_action(action: dict[str, Any], config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a minimal safety decision for an autonomy action."""
    config = config if isinstance(config, dict) else {}
    action = action if isinstance(action, dict) else {}
    kind = str(action.get("kind") or "").strip().lower()
    content = str(action.get("content") or "").strip()
    lowered = content.casefold()
    if kind in {"shutdown", "restart", "admin", "delete", "transfer", "payment", "privacy"}:
        return {"allowed": False, "hard_block": True, "risk": "high", "reason": f"blocked action kind: {kind}"}
    if kind == "other_bot_command":
        if not is_other_bot_command_allowed(content, config):
            return {"allowed": False, "hard_block": True, "risk": "high", "reason": "other-bot command not allowlisted"}
        return {"allowed": True, "hard_block": False, "risk": "medium", "reason": "allowlisted other-bot command"}
    if kind in {"tool", "tool_call", "bash", "shell", "exec"} and any(marker in lowered for marker in DANGEROUS_MARKERS):
        return {"allowed": False, "hard_block": True, "risk": "high", "reason": "dangerous tool content"}
    if content.startswith("/") and any(marker in lowered for marker in DANGEROUS_MARKERS):
        return {"allowed": False, "hard_block": True, "risk": "high", "reason": "dangerous command-like content"}
    risk = "low"
    if any(marker in lowered for marker in ("password", "token", "secret", "privacy")):
        risk = "medium"
    return {"allowed": True, "hard_block": False, "risk": risk, "reason": "allowed"}