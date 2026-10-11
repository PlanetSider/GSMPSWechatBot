from __future__ import annotations

from typing import Any


DANGEROUS_MARKERS = (
    "kick", "ban", "delete", "remove", "shutdown", "restart", "reboot",
    "token", "password", "passwd", "secret", "transfer", "pay", "admin",
    "exec", "eval", "rm ", "drop ", "truncate",
)


def _is_command_like(content: str, prefixes: list[str]) -> bool:
    text = str(content or "").strip()
    if not text:
        return False
    for prefix in prefixes:
        if prefix and text.startswith(prefix):
            return True
    return text.startswith("/")


def is_other_bot_command_allowed(command: str, config: dict[str, Any]) -> bool:
    text = str(command or "").strip()
    if not text:
        return False
    lowered = text.casefold()
    denylist = [str(item or "").strip().casefold() for item in (config.get("other_bot_command_denylist") or [])]
    allowlist = [str(item or "").strip().casefold() for item in (config.get("other_bot_command_allowlist") or [])]
    if any(marker and marker in lowered for marker in DANGEROUS_MARKERS):
        return False
    if any(item and lowered.startswith(item) for item in denylist):
        return False
    if allowlist:
        return any(item and lowered.startswith(item) for item in allowlist)
    return bool(config.get("allow_other_bot_commands", False))


def extract_bot_commands(messages: list[dict[str, Any]], config: dict[str, Any]) -> list[dict[str, Any]]:
    prefixes = [str(item or "").strip() for item in (config.get("other_bot_command_prefixes") or ["/"])]
    bot_names = {
        str(item or "").strip().casefold()
        for item in (config.get("other_bot_names") or [])
        if str(item or "").strip()
    }
    candidates = []
    for item in messages or []:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or "").strip()
        speaker = str(item.get("speaker") or "").strip()
        if not _is_command_like(content, prefixes):
            continue
        is_known_bot = speaker.casefold() in bot_names if speaker else False
        command = content.splitlines()[0].strip()
        candidates.append({
            "speaker": speaker,
            "command": command[:160],
            "confidence": 0.9 if is_known_bot else 0.45,
            "is_known_bot": is_known_bot,
            "safe": is_other_bot_command_allowed(command, config),
        })
    return candidates[-12:]