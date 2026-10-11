from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class GateDecision:
    allowed: bool
    reason: str = ""
    budget: int = 0
    cooldown_seconds: float = 0.0
    shadow: bool = True
    heat_level: str = "unknown"
    max_duration_seconds: float = 30.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ThoughtCandidate:
    id: str = ""
    state: str = "transient"
    direction: str = ""
    text: str = ""
    is_silence: bool = False
    interest: float = 0.0
    social_fit: float = 0.0
    risk: float = 0.0
    confidence: float = 0.0
    novelty: float = 0.0
    humor_potential: float = 0.0
    interaction_hook: float = 0.0
    conversational_momentum: float = 0.0
    unexpectedness: float = 0.0
    social_contribution: float = 0.0
    thread_creation: float = 0.0
    opportunity_score: float = 0.0


@dataclass
class AutonomyDecision:
    type: str = "none"
    reason: str = ""
    confidence: float = 0.0
    action_kind: str = ""
    action_content: str = ""
    action_tool_ref: str = ""
    action_steps: list[Any] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class AutonomyResult:
    event_id: str
    group_id: str
    gate: GateDecision
    attention: dict[str, Any] | None = None
    thoughts: list[ThoughtCandidate] = field(default_factory=list)
    competition: dict[str, Any] = field(default_factory=dict)
    goal: dict[str, Any] | None = None
    decision: AutonomyDecision | None = None
    action: dict[str, Any] | None = None
    effect: dict[str, Any] | None = None
    candidate_exploration: dict[str, Any] = field(default_factory=dict)
    divergence_retry: bool = False
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["thoughts"] = [asdict(item) for item in self.thoughts]
        if self.decision is not None:
            payload["decision"] = asdict(self.decision)
        return payload