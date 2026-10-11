from __future__ import annotations

import json
from typing import Any

from llm.core.response_parser import load_json_lenient


POSITIVE_MARKERS = ("哈哈", "笑死", "草", "乐", "绷不住", "离谱", "真实", "有道理", "好活")
NEGATIVE_MARKERS = ("别说了", "无聊", "尴尬", "冷场", "算了", "不要", "别刷", "停一下")
QUESTION_MARKERS = ("?", "？", "吗", "怎么", "为什么", "啥")


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def call_provider_with_timeout(send, messages, *, thinking=None, timeout=None):
    """Call a provider with optional timeout, tolerating legacy test/custom providers."""
    kwargs = {}
    if thinking:
        kwargs["thinking"] = thinking
    if timeout is not None:
        kwargs["timeout"] = timeout
    try:
        return send(messages, **kwargs)
    except TypeError as exc:
        message = str(exc).lower()
        if "timeout" not in message or "unexpected keyword" not in message:
            raise
        kwargs.pop("timeout", None)
        return send(messages, **kwargs)


class AutonomyEffectEvaluator:
    """Cheap post-action evaluation of whether an autonomy action was worth it."""

    def evaluate(self, action: dict[str, Any], messages: list[dict[str, Any]], started_at: float) -> dict[str, Any]:
        action = action if isinstance(action, dict) else {}
        followups = []
        for item in messages or []:
            if not isinstance(item, dict):
                continue
            if item.get("is_bot") or str(item.get("role") or "") == "assistant":
                continue
            try:
                timestamp = float(item.get("timestamp") or 0)
            except (TypeError, ValueError):
                continue
            if timestamp >= float(started_at or 0):
                followups.append(item)
        texts = [str(item.get("content") or "").strip() for item in followups if str(item.get("content") or "").strip()]
        joined = " ".join(texts)
        positive = sum(1 for marker in POSITIVE_MARKERS if marker in joined)
        negative = sum(1 for marker in NEGATIVE_MARKERS if marker in joined)
        questions = sum(1 for marker in QUESTION_MARKERS if marker in joined)
        continuation = len(texts) >= 2
        naturalness = _clamp(0.55 + 0.15 * continuation + 0.08 * positive - 0.12 * negative)
        timing = _clamp(0.55 + 0.12 * continuation - 0.08 * negative)
        disruption = _clamp(0.15 + 0.25 * negative - 0.05 * positive)
        social_gain = _clamp(0.35 + 0.18 * positive + 0.12 * continuation + 0.05 * questions)
        fun = _clamp(0.35 + 0.22 * positive - 0.10 * negative)
        novelty = _clamp(0.25 + 0.10 * positive + 0.06 * continuation - 0.05 * negative)
        humor_potential = _clamp(0.20 + 0.18 * positive + 0.10 * continuation - 0.06 * negative)
        interaction_hook = _clamp(0.25 + 0.08 * questions + 0.06 * continuation)
        conversational_momentum = _clamp(0.30 + 0.15 * continuation + 0.08 * positive - 0.05 * negative)
        unexpectedness = _clamp(0.15 + 0.05 * positive - 0.03 * negative)
        social_contribution = _clamp(0.30 + 0.12 * positive + 0.10 * continuation)
        thread_creation = _clamp(0.15 + 0.06 * continuation + 0.04 * positive)
        mood_fit = _clamp(0.5 + 0.15 * positive - 0.15 * negative)
        scriptedness = _clamp(0.2 - 0.05 * positive + 0.1 * negative)
        worth_continuing = _clamp(
            social_gain * 0.30
            + fun * 0.24
            + novelty * 0.12
            + humor_potential * 0.12
            + interaction_hook * 0.08
            + conversational_momentum * 0.08
            + thread_creation * 0.08
            + timing * 0.10
            - disruption * 0.18
            - scriptedness * 0.10
        )
        stop_signal = bool(negative >= 2 or disruption >= 0.75)
        return {
            "action_kind": str(action.get("kind") or ""),
            "naturalness": round(naturalness, 4),
            "timing": round(timing, 4),
            "disruption": round(disruption, 4),
            "social_gain": round(social_gain, 4),
            "fun": round(fun, 4),
            "novelty": round(novelty, 4),
            "humor_potential": round(humor_potential, 4),
            "interaction_hook": round(interaction_hook, 4),
            "conversational_momentum": round(conversational_momentum, 4),
            "unexpectedness": round(unexpectedness, 4),
            "social_contribution": round(social_contribution, 4),
            "thread_creation": round(thread_creation, 4),
            "mood_fit": round(mood_fit, 4),
            "scriptedness": round(scriptedness, 4),
            "worth_continuing": round(worth_continuing, 4),
            "stop_signal": stop_signal,
            "reusable_pattern": bool(social_gain >= 0.55 and disruption <= 0.45),
            "outcome": "positive" if worth_continuing >= 0.55 else ("negative" if stop_signal else "neutral"),
            "followup_count": len(texts),
        }

    def evaluate_with_llm(
        self,
        provider,
        action: dict[str, Any],
        messages: list[dict[str, Any]],
        local_effect: dict[str, Any],
        config: dict[str, Any] | None = None,
        provider_call=None,
    ) -> dict[str, Any]:
        """Ask the model whether the action was worth it, then merge with local fallback."""
        config = config if isinstance(config, dict) else {}
        max_messages = max(1, min(int(config.get("effect_judge_max_messages", 20) or 20), 40))
        followups = []
        for item in (messages or [])[-max_messages:]:
            if not isinstance(item, dict):
                continue
            followups.append({
                "speaker": item.get("nickname") or item.get("sender_nickname") or "",
                "content": str(item.get("content") or "")[:500],
                "is_bot": bool(item.get("is_bot")),
            })
        prompt = {
            "task": "Evaluate whether an autonomous group-chat action was worth doing.",
            "action": action or {},
            "local_heuristic": local_effect or {},
            "followup_messages": followups,
            "output_schema": {
                "naturalness": 0.0,
                "timing": 0.0,
                "disruption": 0.0,
                "social_gain": 0.0,
                "fun": 0.0,
                "novelty": 0.0,
                "humor_potential": 0.0,
                "interaction_hook": 0.0,
                "conversational_momentum": 0.0,
                "unexpectedness": 0.0,
                "social_contribution": 0.0,
                "thread_creation": 0.0,
                "mood_fit": 0.0,
                "scriptedness": 0.0,
                "worth_continuing": 0.0,
                "stop_signal": False,
                "reusable_pattern": False,
                "outcome": "positive|negative|neutral",
                "reason": "short evidence-based reason",
            },
        }
        thinking = config.get("effect_judge_thinking_params") or {}
        request = [
            {
                "role": "system",
                "content": (
                    "You are an evaluator for a group-chat bot's autonomous action. "
                    "Judge whether the action was natural, timely, disruptive, socially valuable, fun, "
                    "novel, humorous, hook-creating, momentum-building, fitting the mood, "
                    "scripted/uncanny, worth continuing, and reusable. "
                    "Use the follow-up messages as evidence. Return JSON only."
                ),
            },
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
        ]
        send = provider_call or (provider.send if provider is not None else None)
        if send is None:
            return local_effect
        timeout = max(1.0, float(config.get("max_duration_seconds", 30) or 30))
        try:
            response = call_provider_with_timeout(
                send,
                request,
                thinking=thinking,
                timeout=timeout,
            )
            judged = load_json_lenient(response)
        except Exception:
            return local_effect
        if not isinstance(judged, dict):
            return local_effect
        merged = dict(local_effect or {})
        for key in (
            "naturalness", "timing", "disruption", "social_gain", "fun",
            "novelty", "humor_potential", "interaction_hook",
            "conversational_momentum", "unexpectedness",
            "social_contribution", "thread_creation",
            "mood_fit", "scriptedness", "worth_continuing",
        ):
            if key in judged:
                try:
                    merged[key] = round(_clamp(float(judged[key])), 4)
                except (TypeError, ValueError):
                    pass
        if "stop_signal" in judged:
            merged["stop_signal"] = bool(judged["stop_signal"])
        if "reusable_pattern" in judged:
            merged["reusable_pattern"] = bool(judged["reusable_pattern"])
        outcome = str(judged.get("outcome") or "").strip().lower()
        if outcome in {"positive", "negative", "neutral"}:
            merged["outcome"] = outcome
        reason = str(judged.get("reason") or "").strip()
        if reason:
            merged["judge_reason"] = reason[:500]
        return merged