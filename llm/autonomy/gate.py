from __future__ import annotations

import threading
import time
from typing import Any

from .models import GateDecision


class AutonomyGate:
    """Allocate autonomy opportunities without choosing their content."""

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.lock = threading.RLock()
        self.state: dict[str, dict[str, Any]] = {}

    def _state_for(self, group_id: str) -> dict[str, Any]:
        key = str(group_id or "unknown")
        return self.state.setdefault(key, {
            "last_opportunity_at": 0.0,
            "opportunity_times": [],
            "action_times": [],
            "cooldown_until": 0.0,
            "loop_until": 0.0,
        })

    @staticmethod
    def _heat_level(signals: dict[str, Any]) -> str:
        level = str(signals.get("heat_level") or "").strip().lower()
        if level in {"low", "medium", "high"}:
            return level
        try:
            score = float(signals.get("heat_score") or 0.0)
        except (TypeError, ValueError):
            score = 0.0
        if score <= 0.25:
            return "low"
        if score <= 0.65:
            return "medium"
        return "high"

    @staticmethod
    def _prune(times: list[float], now: float, window: float) -> list[float]:
        return [item for item in times if now - float(item) <= window]

    def evaluate(self, group_id: str, signals: dict[str, Any] | None = None) -> GateDecision:
        signals = signals if isinstance(signals, dict) else {}
        if not self.config.get("enabled", False):
            return GateDecision(False, "autonomy disabled")
        now = float(signals.get("now") or time.time())
        with self.lock:
            state = self._state_for(group_id)
            if float(state.get("loop_until") or 0) > now:
                return GateDecision(False, "loop guard active", heat_level=self._heat_level(signals))
            if float(state.get("cooldown_until") or 0) > now:
                return GateDecision(False, "cooldown active", heat_level=self._heat_level(signals))
            try:
                min_interval = max(0.0, float(self.config.get("min_interval_seconds", 60)))
            except (TypeError, ValueError):
                min_interval = 60.0
            if now - float(state.get("last_opportunity_at") or 0) < min_interval:
                return GateDecision(False, "minimum interval not reached", heat_level=self._heat_level(signals))
            state["opportunity_times"] = self._prune(list(state.get("opportunity_times") or []), now, 3600.0)
            state["action_times"] = self._prune(list(state.get("action_times") or []), now, 3600.0)
            shadow = bool(self.config.get("shadow_mode", True))
            try:
                max_actions = max(0, int(self.config.get("max_actions_per_hour", 2)))
            except (TypeError, ValueError):
                max_actions = 2
            if not shadow and (max_actions <= 0 or len(state["action_times"]) >= max_actions):
                return GateDecision(False, "hourly action budget exhausted", heat_level=self._heat_level(signals))
            try:
                max_opportunities = max(0, int(self.config.get("max_opportunities_per_hour", 6)))
            except (TypeError, ValueError):
                max_opportunities = 6
            if shadow:
                try:
                    max_opportunities = max(0, int(self.config.get("shadow_max_opportunities_per_hour", 1)))
                except (TypeError, ValueError):
                    max_opportunities = 1
            if max_opportunities <= 0 or len(state["opportunity_times"]) >= max_opportunities:
                return GateDecision(False, "hourly opportunity budget exhausted", heat_level=self._heat_level(signals))
            try:
                budget = max(1, int(signals.get("budget") or 1))
            except (TypeError, ValueError):
                budget = 1
            return GateDecision(
                True,
                "opportunity allowed",
                budget=budget,
                cooldown_seconds=0.0,
                shadow=shadow,
                heat_level=self._heat_level(signals),
                max_duration_seconds=max(1.0, float(self.config.get("max_duration_seconds", 30) or 30)),
                metadata={"signals": signals},
            )

    def record_opportunity(self, group_id: str, now: float | None = None) -> None:
        now = float(now or time.time())
        with self.lock:
            state = self._state_for(group_id)
            state["last_opportunity_at"] = now
            state["opportunity_times"] = self._prune(list(state.get("opportunity_times") or []) + [now], now, 3600.0)
            try:
                cooldown = max(0.0, float(self.config.get("cooldown_seconds", 300)))
            except (TypeError, ValueError):
                cooldown = 300.0
            state["cooldown_until"] = now + cooldown

    def record_action(self, group_id: str, now: float | None = None) -> bool:
        now = float(now or time.time())
        with self.lock:
            state = self._state_for(group_id)
            state["action_times"] = self._prune(list(state.get("action_times") or []) + [now], now, 3600.0)
            try:
                limit = max(1, int(self.config.get("max_actions_per_hour", 2)))
            except (TypeError, ValueError):
                limit = 2
            return len(state["action_times"]) <= limit

    def trigger_loop_guard(self, group_id: str, seconds: float = 300.0) -> None:
        with self.lock:
            state = self._state_for(group_id)
            state["loop_until"] = time.time() + max(1.0, float(seconds))

    def snapshot(self, group_id: str) -> dict[str, Any]:
        with self.lock:
            return dict(self._state_for(group_id))