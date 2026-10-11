from __future__ import annotations

import json
import os
import random
import threading
import time
from datetime import datetime
from typing import Any


_SENSITIVE_KEYS = {
    "wxid", "token", "api_key", "apikey", "password", "passwd",
    "secret", "phone", "email", "address",
}


class AutonomyLogger:
    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.lock = threading.RLock()
        self.log_file_path = str(self.config.get("log_file_path") or "logs/autonomy.jsonl")

    def _sanitize(self, value: Any) -> Any:
        if isinstance(value, dict):
            result = {}
            for key, item in value.items():
                if str(key).casefold() in _SENSITIVE_KEYS:
                    result[key] = "[redacted]"
                else:
                    result[key] = self._sanitize(item)
            return result
        if isinstance(value, list):
            return [self._sanitize(item) for item in value]
        if isinstance(value, tuple):
            return [self._sanitize(item) for item in value]
        return value

    def log(self, event_type: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        record = {
            "event": str(event_type or "event"),
            "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
        }
        if payload:
            record.update(payload)
        record = self._sanitize(record)
        mode = str(self.config.get("log_mode") or "full").strip().lower()
        if mode == "summary" and str(event_type).lower() not in {"action", "effect", "cycle"}:
            summary = {
                key: record.get(key)
                for key in ("event", "timestamp", "event_id", "group_id", "allowed", "reason", "type", "outcome")
                if key in record
            }
            record = summary
        try:
            sample_rate = max(0.0, min(1.0, float(self.config.get("log_sample_rate", 1.0))))
        except (TypeError, ValueError):
            sample_rate = 1.0
        critical = str(event_type).lower() in {"action", "effect", "cycle", "opportunity"}
        if not critical and sample_rate < 1.0 and random.random() > sample_rate:
            return record
        line = json.dumps(record, ensure_ascii=False, default=str)
        with self.lock:
            if self.config.get("log_json_console", True):
                print(f"[AUTONOMY_{str(event_type).upper()}] {line}", flush=True)
            if self.config.get("log_json_file", False):
                path = self.log_file_path
                os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
                with open(path, "a", encoding="utf-8", newline="\n") as file:
                    file.write(line + "\n")
        return record

    def opportunity(self, **payload):
        return self.log("opportunity", payload)

    def attention(self, **payload):
        return self.log("attention", payload)

    def thought(self, **payload):
        return self.log("thought", payload)

    def competition(self, **payload):
        return self.log("competition", payload)

    def decision(self, **payload):
        return self.log("decision", payload)

    def action(self, **payload):
        return self.log("action", payload)

    def effect(self, **payload):
        return self.log("effect", payload)

    def learn(self, **payload):
        return self.log("learn", payload)