from __future__ import annotations

import atexit
import json
import queue
import re
import threading
import time
import uuid
from difflib import SequenceMatcher
from typing import Any, Callable

from llm.core.response_parser import (_extract_emoji_suffix,
                                       _strip_batch_index_prefix,
                                       load_json_lenient)

from .buffer import AttentionBuffer, ThoughtBuffer
from .config import get_autonomy_config
from .effect import AutonomyEffectEvaluator, call_provider_with_timeout
from .gate import AutonomyGate
from .logger import AutonomyLogger
from .models import AutonomyDecision, AutonomyResult, ThoughtCandidate
from .prompt import (build_opportunity_prompt, build_seed_hooks,
                      build_system_prompt, pick_divergence)
from .safety import classify_action
from .tools import extract_bot_commands


_EFFECT_LOCK = threading.RLock()
_EFFECT_STOP = threading.Event()
_EFFECT_QUEUE = queue.Queue(maxsize=64)
_EFFECT_THREADS: list[threading.Thread] = []



def _normalize_action_kind(raw):
    """Accept both plain string kinds and schema-mimicry objects."""
    if isinstance(raw, dict):
        raw = raw.get("type") or raw.get("kind") or raw.get("value") or ""
    return str(raw or "").strip()

def _effect_worker():
    while not _EFFECT_STOP.is_set() or not _EFFECT_QUEUE.empty():
        try:
            engine, delay, event_id, group_id, action, started_at = _EFFECT_QUEUE.get(timeout=1.0)
        except queue.Empty:
            continue
        try:
            if getattr(engine, "_closed", False):
                continue
            if not _EFFECT_STOP.wait(max(0.0, float(delay or 0.0))):
                if not getattr(engine, "_closed", False):
                    engine.evaluate_effect(event_id, group_id, action, started_at)
        except Exception as exc:
            engine.logger.learn(event_id=event_id, effect_worker=False, error=str(exc))
        finally:
            _EFFECT_QUEUE.task_done()


def _ensure_effect_workers():
    with _EFFECT_LOCK:
        if _EFFECT_STOP.is_set():
            _EFFECT_STOP.clear()
        if any(thread.is_alive() for thread in _EFFECT_THREADS):
            return
        _EFFECT_THREADS.clear()
        for index in range(2):
            worker = threading.Thread(
                target=_effect_worker,
                daemon=True,
                name=f"llm-autonomy-effect-{index}",
            )
            worker.start()
            _EFFECT_THREADS.append(worker)


def shutdown_effect_workers():
    _EFFECT_STOP.set()
    for worker in list(_EFFECT_THREADS):
        if worker.is_alive():
            worker.join(timeout=2.0)


atexit.register(shutdown_effect_workers)


_SILENCE_MARKERS = (
    "silence", "stay quiet", "remain silent", "no interaction", "no clear",
    "no need to speak", "observe only", "继续旁观", "保持沉默", "无需回复",
    "不需要回复", "没有明确", "不打扰", "静观",
)


def _clamp(value, minimum=0.0, maximum=1.0):
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = minimum
    return max(minimum, min(maximum, number))


def _is_silence_candidate(item):
    item = item if isinstance(item, dict) else {}
    if bool(item.get("is_silence")):
        return True
    direction = str(item.get("direction") or "").casefold()
    text = str(item.get("text") or "").casefold()
    if "silence" in direction or "沉默" in direction or "旁观" in direction:
        return True
    return any(marker in text for marker in _SILENCE_MARKERS)


def _candidate_score(item):
    item = item if isinstance(item, dict) else {}
    # Weights favour fresh, surprising, thread-building directions and keep the
    # direct-echo (interaction_hook / conversational_momentum) contribution low
    # so the engine does not systematically prefer parroting the latest message.
    return _clamp(
        _clamp(item.get("interest")) * 0.16
        + _clamp(item.get("social_fit")) * 0.12
        + _clamp(item.get("novelty")) * 0.18
        + _clamp(item.get("humor_potential")) * 0.14
        + _clamp(item.get("interaction_hook")) * 0.08
        + _clamp(item.get("conversational_momentum")) * 0.05
        + _clamp(item.get("unexpectedness")) * 0.11
        + _clamp(item.get("social_contribution")) * 0.12
        + _clamp(item.get("thread_creation")) * 0.14
        - _clamp(item.get("risk")) * 0.22
    )


def _candidate_summary(data, minimum=3, minimum_non_silent=2):
    data = data if isinstance(data, dict) else {}
    raw_thoughts = [item for item in (data.get("thoughts") or []) if isinstance(item, dict)]
    candidates = []
    seen = set()
    for index, item in enumerate(raw_thoughts, 1):
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        signature = re.sub(r"\s+", "", text.casefold())
        if signature in seen:
            continue
        seen.add(signature)
        candidate = dict(item)
        candidate["id"] = str(item.get("id") or f"c{index}")
        candidate["direction"] = str(item.get("direction") or "")
        candidate["is_silence"] = _is_silence_candidate(candidate)
        candidate["opportunity_score"] = round(_candidate_score(candidate), 4)
        candidates.append(candidate)
    non_silent = [item for item in candidates if not item["is_silence"]]
    directions = {
        str(item.get("direction") or item.get("text") or "").strip().casefold()
        for item in candidates
        if str(item.get("direction") or item.get("text") or "").strip()
    }
    exploration = data.get("candidate_exploration") if isinstance(data.get("candidate_exploration"), dict) else {}
    declared_directions = exploration.get("directions_considered")
    declared_count = len(declared_directions) if isinstance(declared_directions, list) else 0
    return {
        "candidates": candidates,
        "non_silent": non_silent,
        "candidate_count": len(candidates),
        "non_silent_count": len(non_silent),
        "direction_count": max(len(directions), declared_count),
        "minimum_candidates": int(minimum),
        "minimum_non_silent": int(minimum_non_silent),
        "collapsed": (
            len(candidates) < int(minimum)
            or len(non_silent) < int(minimum_non_silent)
            or max(len(directions), declared_count) < int(minimum)
        ),
    }


def _competition_diagnostics(data, summary):
    data = data if isinstance(data, dict) else {}
    competition = dict(data.get("competition") or {}) if isinstance(data.get("competition"), dict) else {}
    candidates = sorted(summary.get("candidates") or [], key=lambda item: item.get("opportunity_score", 0.0), reverse=True)
    winner = candidates[0] if candidates else {}
    runner_up = candidates[1] if len(candidates) > 1 else {}
    winner_score = float(winner.get("opportunity_score") or 0.0)
    runner_up_score = float(runner_up.get("opportunity_score") or 0.0)
    margin = round(max(0.0, winner_score - runner_up_score), 4)
    competition.setdefault("winner", winner.get("id"))
    competition.setdefault("runner_up", runner_up.get("id"))
    competition.setdefault("compared_ids", [item.get("id") for item in candidates])
    competition["candidate_count"] = summary.get("candidate_count", 0)
    competition["non_silent_count"] = summary.get("non_silent_count", 0)
    competition["direction_count"] = summary.get("direction_count", 0)
    competition["margin"] = margin
    competition["winner_score"] = round(winner_score, 4)
    competition["runner_up_score"] = round(runner_up_score, 4)
    return competition


def _sanitize_action_content(text, emoji_set):
    """Strip transcript artifacts from autonomy action content.

    Mirrors the passive-chain cleaning in parse_llm_response: removes the
    batch_index= prefix, and transfers a trailing/bare "[emoji]" into the
    action's animation field so stickers are sent as pictures, not text.
    Returns (content, animation_or_None).
    """
    content = _strip_batch_index_prefix(text)
    animation = None
    if emoji_set:
        head, matched = _extract_emoji_suffix(content, emoji_set)
        if matched is not None:
            content = head
            animation = matched
    return content.strip(), animation


class AutonomyEngine:
    """Optional sidecar for open-ended autonomous cognition."""

    def __init__(
        self,
        llm_config: dict[str, Any],
        provider_getter: Callable[[], Any],
        provider_call: Callable[..., Any] | None = None,
        memory_manager: Any = None,
        style_learner: Any = None,
        assistant_nickname: str = "LLM",
        action_callback: Callable[[str, dict[str, Any], AutonomyResult], Any] | None = None,
        cycle_store: Any = None,
        effect_evaluator: AutonomyEffectEvaluator | None = None,
        tool_completer: Callable[..., Any] | None = None,
    ):
        self.llm_config = llm_config or {}
        self.config = get_autonomy_config(self.llm_config)
        self.provider_getter = provider_getter
        self.provider_call = provider_call or self._default_provider_call
        self.memory_manager = memory_manager
        self.style_learner = style_learner
        self.assistant_nickname = str(assistant_nickname or "LLM")
        self.action_callback = action_callback
        self.cycle_store = cycle_store
        self.effect_evaluator = effect_evaluator or AutonomyEffectEvaluator()
        self.tool_completer = tool_completer
        self.gate = AutonomyGate(self.config)
        self.logger = AutonomyLogger(self.config)
        self.thought_buffer = ThoughtBuffer()
        self.attention_buffer = AttentionBuffer()
        self.lock = threading.RLock()
        self._closed = False
        try:
            from llm.security import get_emoji_list
            self.emoji_list = get_emoji_list()
        except Exception:
            self.emoji_list = []

    def _default_provider_call(self, messages, **kwargs):
        provider = self.provider_getter()
        return provider.send(messages, **kwargs)

    def close(self):
        self._closed = True

    def _context_messages(self, group_id: str, context: dict[str, Any] | None) -> list[dict[str, Any]]:
        context = context if isinstance(context, dict) else {}
        messages = context.get("messages")
        if not isinstance(messages, list) and self.memory_manager is not None:
            try:
                messages = self.memory_manager.get_group_messages(group_id)
            except Exception:
                messages = []
        result = []
        for item in (messages or [])[-32:]:
            if not isinstance(item, dict):
                continue
            content = str(item.get("content") or "").strip()
            if not content:
                continue
            result.append({
                "message_id": str(
                    item.get("message_id") or item.get("messageKey")
                    or item.get("messageId") or ""
                ),
                "local_id": str(item.get("local_id") or item.get("localId") or ""),
                "server_id": str(
                    item.get("server_id") or item.get("serverId")
                    or item.get("svrid") or item.get("rawid") or ""
                ),
                "speaker": str(item.get("nickname") or item.get("sender_nickname") or item.get("speaker") or ""),
                "wxid": str(item.get("wxid") or item.get("sender_wxid") or ""),
                "content": content[:500],
                "is_bot": bool(item.get("is_bot") or str(item.get("role") or "") == "assistant"),
                "message_type": str(item.get("message_type") or item.get("type") or ""),
                "timestamp": item.get("timestamp") or "",
                "quoted_content": str(item.get("quoted_content") or "")[:240],
                "prefix_used": bool(item.get("prefix_used")),
                "is_at_bot": bool(item.get("is_at_bot") or item.get("is_at")),
                "directed_to_bot": bool(item.get("directed_to_bot") or item.get("reply_to_bot")),
                "directed_to_other": bool(item.get("directed_to_other") or item.get("reply_to_other")),
            })
        return result

    def _identity_snapshot(self, group_id: str) -> dict[str, Any]:
        identity = self.llm_config.get("identity") if isinstance(self.llm_config, dict) else None
        identity = dict(identity) if isinstance(identity, dict) else {}
        profiles = self.llm_config.get("group_profiles") if isinstance(self.llm_config, dict) else {}
        profile = profiles.get(str(group_id or "").strip()) if isinstance(profiles, dict) else None
        if not isinstance(profile, dict):
            profile = profiles.get("default") if isinstance(profiles, dict) else None
        if isinstance(profile, dict) and isinstance(profile.get("identity"), dict):
            identity = dict(profile["identity"])
        return {
            "name": str(identity.get("name") or ""),
            "role": str(identity.get("role") or ""),
            "style": str(identity.get("style") or ""),
            "rules": [
                str(item).strip()
                for item in (identity.get("rules") or [])
                if str(item).strip()
            ][:24],
        }

    def _style_snapshot(self, group_id: str, context_messages: list[dict[str, Any]]) -> dict[str, Any]:
        if self.style_learner is None:
            return {}
        result: dict[str, Any] = {}
        try:
            style = str(self.style_learner.get_prompt_context(group_id) or "").strip()
            if style:
                result["style_context"] = style[:1600]
        except Exception:
            pass
        try:
            store = getattr(self.style_learner, "store", None)
            if store is not None:
                slang = store.get_context_slang(group_id, context_messages, max_items=6, max_chars=900)
                if slang:
                    result["known_group_phrases"] = slang[:6]
                expressions = store.get_context_expressions(group_id, context_messages, max_items=5, max_chars=800)
                if expressions:
                    result["known_expressions"] = expressions[:5]
        except Exception:
            pass
        # Behavior learning belongs in the autonomous chain too.  The passive
        # reply chain (llm_service) already surfaces learned interaction
        # patterns; the autonomy chain must see them as well so "how members
        # open threads / banter / follow" can shape autonomous choices, not
        # only slang vocabulary.  Pure local read, no extra LLM call.
        batch_features: dict[str, Any] = {}
        try:
            from llm.learning.initiative import summarize_batch_features
            human = [
                item for item in (context_messages or [])
                if isinstance(item, dict)
                and str(item.get("content") or "").strip()
                and not item.get("is_bot")
            ]
            batch_features = summarize_batch_features(human[-6:], human)
        except Exception:
            batch_features = {}
        try:
            prior = self.style_learner.compute_behavior_prior(group_id, batch_features)
            if isinstance(prior, dict) and prior:
                result["behavior_prior"] = prior
        except Exception:
            pass
        try:
            proactive = batch_features.get("proactive", -1)
            if proactive is None:
                proactive = -1
            try:
                proactive = int(proactive)
            except (TypeError, ValueError):
                proactive = -1
            records = self.style_learner.lookup_group_behaviors(
                group_id,
                {
                    "text": "\n".join(
                        str(item.get("content") or "")
                        for item in (context_messages or [])[-6:]
                        if isinstance(item, dict)
                    ),
                    "proactive": proactive,
                    "content_type": str(batch_features.get("content_type") or "other"),
                    "target_type": str(batch_features.get("target_type") or "none"),
                },
                max_items=3,
            )
            if records:
                result["behavior_candidates"] = records[:3]
        except Exception:
            pass
        return result

    def _opportunity_seeds(
        self,
        context_messages: list[dict[str, Any]],
        signals: dict[str, Any],
    ) -> dict[str, Any]:
        human = [item for item in context_messages if item.get("content") and not item.get("is_bot")]
        bot_messages = [item for item in context_messages if item.get("is_bot") and item.get("content")]
        latest = human[-1] if human else {}
        recent = human[-12:]
        short_reactions = [
            item.get("content") for item in recent
            if len(str(item.get("content") or "").strip()) <= 8
        ][-6:]
        questions = [
            item.get("content") for item in recent
            if "?" in str(item.get("content") or "") or "？" in str(item.get("content") or "")
        ][-5:]
        media = [
            item.get("content") for item in recent
            if str(item.get("content") or "").strip().startswith("[")
        ][-5:]
        # A "protected dialogue" is only meaningful while it is actually live.
        # Once the two-person exchange has gone quiet or the topic moved on, the
        # bot must not keep treating a stale event as a reason to stay silent
        # forever.  Without a timestamp we fall back to the recent window check;
        # with timestamps we additionally require recency.
        now = time.time()
        recent_window = context_messages[-6:]
        recent_have_ts = any(
            item.get("ts") or item.get("timestamp") or item.get("time")
            for item in recent_window
        )
        protected = any(
            item.get("directed_to_other") and not item.get("directed_to_bot")
            for item in recent_window
        )
        if protected and recent_have_ts:
            stale = True
            for item in recent_window:
                ts = item.get("ts") or item.get("timestamp") or item.get("time")
                if not ts:
                    continue
                try:
                    ts = float(ts)
                except (TypeError, ValueError):
                    continue
                # 900s = 15 min.  If every timed message is older than this the
                # exchange is no longer live and should not gate autonomy.
                if now - ts <= 900:
                    stale = False
                    break
            if stale:
                protected = False
        explicit_bot_target = any(
            item.get("directed_to_bot") or item.get("prefix_used") or item.get("is_at_bot")
            for item in recent_window
        )
        speakers = {
            str(item.get("wxid") or item.get("speaker") or "")
            for item in human
            if str(item.get("wxid") or item.get("speaker") or "").strip()
        }
        return {
            "latest_message": latest,
            "recent_short_reactions": short_reactions,
            "open_questions": questions,
            "recent_media_or_placeholders": media,
            "recent_bot_messages": [item.get("content") for item in bot_messages[-4:]],
            "speaker_count": len(speakers),
            "ordinary_group_chatter": bool(human) and not protected,
            "protected_dialogue": protected,
            "explicit_bot_target": explicit_bot_target,
            "no_explicit_request": not explicit_bot_target,
            "background_scan": str(signals.get("source") or "") == "background_scheduler",
            "interpretation_guard": "no explicit request is not the same as no opportunity",
        }

    def maybe_run(
        self,
        group_id: str,
        signals: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
    ) -> AutonomyResult | None:
        decision = self.gate.evaluate(group_id, signals)
        if not decision.allowed:
            self.logger.opportunity(
                group_id=str(group_id or "unknown"),
                allowed=False,
                reason=decision.reason,
                heat_level=decision.heat_level,
            )
            return None
        return self.run_opportunity(group_id, signals=signals, context=context, gate_decision=decision)

    def run_opportunity(
        self,
        group_id: str,
        signals: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
        gate_decision: Any = None,
    ) -> AutonomyResult:
        gate_decision = gate_decision or self.gate.evaluate(group_id, signals)
        normalized_group = str(group_id or "unknown").strip() or "unknown"
        if normalized_group in {"unknown", "__default__"}:
            # Polluted / unidentifiable groups must not burn provider calls or
            # write persistent junk.  Record a rejected opportunity and stop.
            result = AutonomyResult(
                event_id="auto_" + uuid.uuid4().hex[:12],
                group_id=normalized_group,
                gate=gate_decision,
            )
            self.logger.opportunity(
                event_id=result.event_id,
                group_id=normalized_group,
                allowed=False,
                reason="unknown_group_guard",
                heat_level=gate_decision.heat_level,
            )
            return result
        event_id = "auto_" + uuid.uuid4().hex[:12]
        signals = signals if isinstance(signals, dict) else {}
        context = context if isinstance(context, dict) else {}
        trigger = {
            "source": str(signals.get("source") or context.get("_trigger_source") or context.get("trigger_source") or "environment"),
            "summary": str(signals.get("summary") or context.get("trigger_summary") or "")[:240],
            "message_ids": list(context.get("message_ids") or [])[:10],
        }
        result = AutonomyResult(
            event_id=event_id,
            group_id=str(group_id or "unknown"),
            gate=gate_decision,
        )
        self.logger.opportunity(
            event_id=event_id,
            group_id=result.group_id,
            trigger=trigger,
            allowed=True,
            passive_priority=bool(signals.get("passive_priority", False)),
            reason=gate_decision.reason,
            budget=gate_decision.budget,
            heat_level=gate_decision.heat_level,
            shadow=gate_decision.shadow,
            hard_safety=signals.get("hard_safety", "pass"),
        )
        self._settle_pending_effects(group_id)
        if signals.get("new_message"):
            self.thought_buffer.interrupt(group_id, "new environment message")
        context_messages = self._context_messages(group_id, context)
        seeds = self._opportunity_seeds(context_messages, signals)
        divergence = pick_divergence(self.config)
        try:
            max_hooks = max(0, int(self.config.get("divergence_max_seed_hooks", 2) or 0))
        except (TypeError, ValueError):
            max_hooks = 2
        seed_hooks = build_seed_hooks(context_messages, max_hooks=max_hooks) if max_hooks else []
        recent_cycles = []
        if self.cycle_store:
            try:
                recent_cycles = self.cycle_store.list_recent_autonomy_cycles(group_id, limit=5)
            except Exception:
                recent_cycles = []
        environment = {
            "identity": self._identity_snapshot(group_id),
            "messages": context_messages,
            "tool_candidates": extract_bot_commands(context_messages, self.config),
            "opportunity_seeds": seeds,
            "thought_buffer": self.thought_buffer.snapshot(group_id, limit=8),
            "attention_buffer": self.attention_buffer.snapshot(group_id, limit=5),
            "autonomy_state": self.cycle_store.get_autonomy_state(group_id) if self.cycle_store else {},
            "persistent_state": self._persistent_state_snapshot(group_id),
            "resume_candidates": self._resume_candidates(group_id, recent_cycles),
            "carried_thoughts": self._carried_thoughts(group_id, recent_cycles),
            "recent_thoughts": [
                {
                    "event_id": item.get("event_id"),
                    "attention": item.get("attention"),
                    "thoughts": item.get("thoughts"),
                    "competition": item.get("competition"),
                    "decision": item.get("decision"),
                    "effect": item.get("effect"),
                }
                for item in recent_cycles
            ],
            "signals": signals or {},
            "assistant_nickname": self.assistant_nickname,
            "avoid_repeating_motifs": self._recurring_motifs(
                group_id, recent_cycles, limit=5
            ),
            "recent_self_messages": self._recent_self_messages(
                group_id, recent_cycles, limit=6
            ),
        }
        environment.update(self._style_snapshot(group_id, context_messages))
        # Log which learned behavior patterns the autonomy chain surfaced so the
        # reuse-reduction / feedback loops can see them, mirroring the passive chain.
        if environment.get("behavior_candidates") and self.style_learner is not None:
            try:
                self.style_learner.record_behavior_injection(
                    group_id,
                    event_id,
                    environment["behavior_candidates"],
                )
            except Exception:
                pass
        allowed_kinds = list(self.config.get("allowed_action_kinds") or [])
        minimum_candidates = max(2, int(self.config.get("min_candidates", 3) or 3))
        minimum_non_silent = max(1, int(self.config.get("min_non_silent_candidates", 2) or 2))
        thinking = self.config.get("thinking_params") or {}
        timeout = max(1.0, float(self.config.get("max_duration_seconds", 30) or 30))
        system_prompt = build_system_prompt(self.config)

        def request(user_prompt: str):
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
            if self.tool_completer is not None:
                # Tool-enabled autonomous path: the model may query memory,
                # web, group-learning data or Minecraft RCON, then continue.
                # The completer runs the full assistant/tool/assistant loop and
                # returns the final JSON string, so the opportunity can make a
                # concrete, grounded decision instead of a template guess.
                return self.tool_completer(
                    messages,
                    group_id=result.group_id,
                    thinking=thinking,
                    timeout=timeout,
                )
            return call_provider_with_timeout(
                self.provider_call,
                messages,
                thinking=thinking,
                timeout=timeout,
            )

        initial_prompt = build_opportunity_prompt(
            result.group_id,
            environment,
            {
                "reason": gate_decision.reason,
                "budget": gate_decision.budget,
                "heat_level": gate_decision.heat_level,
                "shadow": gate_decision.shadow,
            },
            allowed_action_kinds=allowed_kinds,
            divergence=divergence,
            seed_hooks=seed_hooks,
        )
        try:
            response = request(initial_prompt)
        except Exception as exc:
            self.logger.decision(
                event_id=event_id,
                group_id=result.group_id,
                type="none",
                reason=f"provider_error: {type(exc).__name__}: {exc}",
            )
            self.gate.record_opportunity(group_id)
            cycle_payload = result.to_dict()
            cycle_payload["trigger"] = trigger
            self.logger.log("cycle", cycle_payload)
            return result

        # A timed-out tool loop returns an empty string, and the provider may
        # also return non-JSON.  Treat both as an empty decision instead of
        # letting the parse exception escape: an unhandled ValueError here
        # aborted the whole opportunity with no logged cycle at all.
        try:
            data = load_json_lenient(response)
        except Exception as parse_exc:
            self.logger.decision(
                event_id=event_id,
                group_id=result.group_id,
                type="none",
                reason=(
                    "unparseable_response: "
                    f"{type(parse_exc).__name__}: {parse_exc}; "
                    f"response_chars={len(str(response or ''))}"
                ),
            )
            self.gate.record_opportunity(group_id)
            cycle_payload = result.to_dict()
            cycle_payload["trigger"] = trigger
            self.logger.log("cycle", cycle_payload)
            return result
        if not isinstance(data, dict):
            data = {}
        summary = _candidate_summary(data, minimum_candidates, minimum_non_silent)
        initial_collapsed = bool(summary.get("collapsed"))
        retries = 0
        retry_limit = max(0, int(self.config.get("divergence_retry_limit", 1) or 0))
        divergence_enabled = bool(self.config.get("candidate_exploration_enabled", True))
        protected_dialogue = bool(seeds.get("protected_dialogue"))
        divergence_reason = ""

        while (
            divergence_enabled
            and not protected_dialogue
            and retries < retry_limit
            and summary.get("collapsed")
        ):
            retries += 1
            divergence_reason = (
                "not_enough_candidates"
                if int(summary.get("candidate_count") or 0) < minimum_candidates
                else "not_enough_non_silent_directions"
            )
            revision = {
                "round": retries,
                "request": (
                    "Do not conclude with a lone silence candidate yet. Re-examine the scene "
                    "and list at least {minimum} distinct candidate directions, including "
                    "concrete non-silent ideas (pun, callback, hook, half-sentence, "
                    "exaggeration, reversal, resurface, short question, known slang). At "
                    "least one candidate must be something you would start yourself rather "
                    "than a reaction to someone else -- trying a capability out of "
                    "curiosity, checking on something you wondered about, or picking up a "
                    "small plan of your own. You may keep one silence candidate, but it must "
                    "compete against the others with concrete scores."
                ).format(minimum=minimum_candidates),
                "collapse_detected": {
                    "candidate_count": summary.get("candidate_count"),
                    "non_silent_count": summary.get("non_silent_count"),
                    "direction_count": summary.get("direction_count"),
                    "minimum_candidates": minimum_candidates,
                    "minimum_non_silent": minimum_non_silent,
                },
                "previous_candidates": [
                    {key: item.get(key) for key in ("id", "direction", "text", "is_silence")}
                    for item in (summary.get("candidates") or [])
                ],
            }
            repair_prompt = build_opportunity_prompt(
                result.group_id,
                environment,
                {
                    "reason": gate_decision.reason,
                    "budget": gate_decision.budget,
                    "heat_level": gate_decision.heat_level,
                    "shadow": gate_decision.shadow,
                },
                allowed_action_kinds=allowed_kinds,
                revision=revision,
                divergence=divergence,
                seed_hooks=seed_hooks,
            )
            repair_response = None
            try:
                repair_response = request(repair_prompt)
            except Exception as exc:
                divergence_reason = "repair_provider_error: %s" % (type(exc).__name__,)
                break
            repair_data = load_json_lenient(repair_response) if repair_response else {}
            if not isinstance(repair_data, dict):
                divergence_reason = "repair_unparsable"
                break
            repair_summary = _candidate_summary(repair_data, minimum_candidates, minimum_non_silent)
            improved = (
                int(repair_summary.get("candidate_count") or 0) > int(summary.get("candidate_count") or 0)
                or int(repair_summary.get("non_silent_count") or 0) > int(summary.get("non_silent_count") or 0)
                or (bool(summary.get("collapsed")) and not repair_summary.get("collapsed"))
            )
            if improved:
                data = repair_data
                summary = repair_summary
                divergence_reason = ""
            else:
                divergence_reason = "repair_still_collapsed"
                break

        if retries > 0:
            self.logger.log("divergence", {
                "event_id": event_id,
                "group_id": result.group_id,
                "attempts": retries,
                "last_reason": divergence_reason,
                "collapsed_after_repair": bool(summary.get("collapsed")),
                "candidate_count": summary.get("candidate_count"),
                "non_silent_count": summary.get("non_silent_count"),
                "protected_dialogue": protected_dialogue,
            })

        result.raw = data
        result.attention = data.get("attention") if isinstance(data.get("attention"), dict) else None
        decision_data = data.get("decision") if isinstance(data.get("decision"), dict) else {}
        action_data = data.get("action") if isinstance(data.get("action"), dict) else {}
        decision_type = str(decision_data.get("type") or "none").lower()
        # Models often say "reply" for the speak-like decision; normalize so
        # the action authorization check does not silently drop it.
        if decision_type in {"reply", "say", "respond", "message"}:
            decision_type = "speak"
        result.candidate_exploration = dict(
            data.get("candidate_exploration") if isinstance(data.get("candidate_exploration"), dict) else {}
        )
        result.candidate_exploration.update({"divergence": divergence})
        result.candidate_exploration.update({
            "divergence_retry": retries > 0,
            "divergence_attempts": retries,
            "divergence_reason": divergence_reason,
            "collapse_detected": initial_collapsed,
            "collapse_after_retry": bool(summary.get("collapsed")),
            "candidate_count": summary.get("candidate_count", 0),
            "non_silent_count": summary.get("non_silent_count", 0),
            "direction_count": summary.get("direction_count", 0),
            "protected_dialogue": protected_dialogue,
            "silence_decided_after_exploration": (
                decision_type in {"silence", "wait", "abandon"}
                and int(summary.get("non_silent_count") or 0) > 0
            ),
        })
        result.divergence_retry = bool(retries > 0)
        result.thoughts = [
            ThoughtCandidate(
                id=str(item.get("id") or ""),
                state=str(item.get("state") or "transient"),
                direction=str(item.get("direction") or ""),
                text=str(item.get("text") or ""),
                is_silence=bool(item.get("is_silence")),
                interest=_clamp(item.get("interest")),
                social_fit=_clamp(item.get("social_fit")),
                risk=_clamp(item.get("risk")),
                confidence=_clamp(item.get("confidence")),
                novelty=_clamp(item.get("novelty")),
                humor_potential=_clamp(item.get("humor_potential")),
                interaction_hook=_clamp(item.get("interaction_hook")),
                conversational_momentum=_clamp(item.get("conversational_momentum")),
                unexpectedness=_clamp(item.get("unexpectedness")),
                social_contribution=_clamp(item.get("social_contribution")),
                thread_creation=_clamp(item.get("thread_creation")),
                opportunity_score=float(item.get("opportunity_score") or 0.0),
            )
            for item in (summary.get("candidates") or [])
        ]
        result.competition = _competition_diagnostics(data, summary)
        action_evaluation = data.get("action_evaluation") if isinstance(data.get("action_evaluation"), dict) else None
        result.goal = data.get("goal") if isinstance(data.get("goal"), dict) else None
        raw_kind = _normalize_action_kind(action_data.get("kind"))
        raw_confidence = _clamp(decision_data.get("confidence"))
        if summary.get("collapsed"):
            calibrated_confidence = min(0.60, raw_confidence)
            calibration_note = "confidence capped because candidates stayed collapsed"
        else:
            margin = _clamp(result.competition.get("margin"))
            cap = 0.72 + min(0.20, margin * 0.80)
            calibrated_confidence = min(cap, max(raw_confidence, 0.35))
            calibration_note = "confidence capped by candidate margin"
        result.competition["calibrated_confidence"] = round(_clamp(calibrated_confidence), 4)
        result.competition["calibration_note"] = calibration_note
        result.decision = AutonomyDecision(
            type=decision_type,
            reason=str(decision_data.get("reason") or ""),
            confidence=calibrated_confidence,
            action_kind=raw_kind,
            action_content=str(action_data.get("content") or ""),
            action_tool_ref=str(action_data.get("tool_ref") or ""),
            action_steps=list(action_data.get("steps") or []),
            raw=decision_data,
        )
        result.action = action_data or None
        if result.action is not None:
            result.action["kind"] = raw_kind
        # --- P10 leak fix: sanitize autonomy action content the same way
        # the passive chain sanitizes parse_llm_response output. ---
        _emoji_set = {
            str(e).strip() for e in (self.emoji_list or []) if str(e).strip()
        }
        if result.action is not None:
            _c, _a = _sanitize_action_content(
                str(result.action.get("content") or ""), _emoji_set)
            result.action["content"] = _c
            if _a and not result.action.get("animation"):
                result.action["animation"] = _a
            _steps = result.action.get("steps")
            if isinstance(_steps, list):
                for _s_item in _steps:
                    if isinstance(_s_item, dict):
                        _c2, _a2 = _sanitize_action_content(
                            str(_s_item.get("content") or ""), _emoji_set)
                        _s_item["content"] = _c2
                        if _a2 and not result.action.get("animation"):
                            result.action["animation"] = _a2

        if result.attention is not None:
            self.logger.attention(event_id=event_id, group_id=result.group_id, attention=result.attention)
        for thought in result.thoughts:
            self.logger.thought(event_id=event_id, group_id=result.group_id, thought=thought.__dict__)
        if result.competition:
            self.logger.competition(event_id=event_id, group_id=result.group_id, competition=result.competition)
        if action_evaluation:
            self.logger.log("evaluation", {
                "event_id": event_id,
                "group_id": result.group_id,
                "action_evaluation": action_evaluation,
            })
        self.logger.decision(
            event_id=event_id,
            group_id=result.group_id,
            decision=result.decision.__dict__ if result.decision else {},
        )
        self.attention_buffer.update(group_id, [result.attention] if result.attention else [])
        self.thought_buffer.observe(group_id, [thought.__dict__ for thought in result.thoughts])
        if result.action:
            self.logger.action(event_id=event_id, group_id=result.group_id, action=result.action)

        action_kind = result.decision.action_kind if result.decision else ""
        allowed_kinds = {str(item) for item in (self.config.get("allowed_action_kinds") or [])}
        executed_any = False
        action_content = str((result.action or {}).get("content") or "").strip()
        repeat_blocked = bool(action_content) and self._is_repeat(action_content, recent_cycles, group_id=result.group_id)
        if repeat_blocked:
            # Hard local anti-repeat: do not send the same concrete message
            # again.  Keep the opportunity (thoughts/persistent write-back still
            # happen) but skip sending; reason is recorded for observability.
            self.logger.action(
                event_id=event_id,
                group_id=result.group_id,
                executed=False,
                reason="repeat_guard",
                action=result.action or {},
            )
        action_authorized = (
            result.decision is not None
            and result.decision.type in {"speak", "tool", "mixed", "reply"}
            and bool(result.decision.action_kind)
        )
        if (
            result.action
            and action_kind
            and action_kind in allowed_kinds
            and action_authorized
            and not gate_decision.shadow
            and not repeat_blocked
            and self.action_callback is not None
        ):
            safety = classify_action(result.action, self.config)
            if not safety.get("allowed", False):
                self.logger.action(
                    event_id=event_id,
                    group_id=result.group_id,
                    executed=False,
                    safety=safety,
                )
            else:
                steps = result.action.get("steps") if isinstance(result.action.get("steps"), list) else []
                actions_to_run = steps if steps else [result.action]
                for index, step in enumerate(actions_to_run):
                    if isinstance(step, str):
                        step = {"kind": action_kind, "content": step}
                    if not isinstance(step, dict):
                        continue
                    step_action = dict(result.action)
                    step_action.update(step)
                    step_action["step_index"] = index
                    step_action["_consumed_messages"] = [
                        item for item in context_messages
                        if isinstance(item, dict) and (item.get("content") or "").strip() and not item.get("is_bot")
                    ][-4:]
                    step_safety = classify_action(step_action, self.config)
                    if not step_safety.get("allowed", False):
                        self.logger.action(
                            event_id=event_id,
                            group_id=result.group_id,
                            step_index=index,
                            executed=False,
                            safety=step_safety,
                        )
                        break
                    try:
                        ok = self.action_callback(group_id, step_action, result)
                        executed_any = bool(executed_any or ok)
                        self.logger.action(
                            event_id=event_id,
                            group_id=result.group_id,
                            step_index=index,
                            executed=bool(ok),
                            action=step_action,
                        )
                        if not ok:
                            break
                    except Exception as exc:
                        self.logger.action(
                            event_id=event_id,
                            group_id=result.group_id,
                            step_index=index,
                            executed=False,
                            error=f"{type(exc).__name__}: {exc}",
                        )
                        break
                if executed_any:
                    self.gate.record_action(group_id)
                    self._mark_consumed(result, context_messages, trigger)
                    self.schedule_effect_evaluation(
                        result.event_id,
                        group_id,
                        result.action,
                        started_at=time.time(),
                    )
        self.gate.record_opportunity(group_id)
        self._record_cycle(result)
        self._write_persistent_update(group_id, data)
        if not executed_any and self.cycle_store:
            try:
                self.cycle_store.update_autonomy_effect(
                    result.event_id,
                    {
                        "outcome": "not_applicable",
                        "reason": "no_action_executed",
                        "shadow": bool(gate_decision.shadow),
                    },
                )
            except Exception as exc:
                self.logger.learn(event_id=result.event_id, settled=False, error=str(exc))
        cycle_payload = result.to_dict()
        cycle_payload["trigger"] = trigger
        self.logger.log("cycle", cycle_payload)
        return result

    def context_hints(self, group_id: str, limit: int = 3) -> list[dict[str, Any]]:
        """Read-only hints for the passive path. Shadow cognition is isolated."""
        if bool(self.config.get("shadow_mode", True)):
            return []
        hints = []
        for item in self.thought_buffer.snapshot(group_id, limit=max(1, min(int(limit or 3), 6))):
            hints.append({
                "text": str(item.get("text") or ""),
                "state": str(item.get("state") or ""),
                "interest": float(item.get("interest") or 0.0),
                "source": "thought_buffer",
            })
        if len(hints) < limit and self.cycle_store:
            try:
                recent = self.cycle_store.list_recent_autonomy_cycles(group_id, limit=limit)
            except Exception:
                recent = []
            for item in recent:
                decision = item.get("decision") if isinstance(item.get("decision"), dict) else {}
                text = str(decision.get("reason") or "").strip()
                if text:
                    hints.append({"text": text, "state": "recent_cycle", "interest": 0.0, "source": "cycle_store"})
                if len(hints) >= limit:
                    break
        return hints[:limit]

    def _mark_consumed(
        self,
        result: AutonomyResult,
        context_messages: list[dict[str, Any]],
        trigger: dict[str, Any],
    ) -> None:
        """Record that this autonomy event already produced a real reply.

        The ordinary proactive/direct pipelines consult this record and
        suppress their own reply built from the same context, so one autonomy
        event cannot be answered twice.  Only messages successfully executed
        (real, non-shadow sends) are recorded.
        """
        try:
            from .consumed import get_consumed_store
            human_messages = [
                item for item in (context_messages or [])
                if isinstance(item, dict) and (item.get("content") or "").strip() and not item.get("is_bot")
            ]
            consumed_messages = human_messages[-4:]
            message_ids = [str(item) for item in (trigger.get("message_ids") or []) if str(item or "").strip()]
            action_content = str((result.action or {}).get("content") or "") if isinstance(result.action, dict) else ""
            ttl = self.config.get("consumed_event_ttl_seconds") or 600
            get_consumed_store().mark_consumed(
                group_id=result.group_id,
                message_ids=message_ids,
                messages=consumed_messages,
                event_id=result.event_id,
                content=action_content,
                ttl_seconds=ttl,
            )
            self.logger.log("consumed", {
                "event_id": result.event_id,
                "group_id": result.group_id,
                "content": action_content,
                "message_ids": message_ids,
                "consumed": len(consumed_messages),
            })
        except Exception as exc:
            self.logger.learn(event_id=getattr(result, "event_id", ""), consumed_mark=False, error=str(exc))

    def _persistent_state_snapshot(self, group_id: str) -> dict:
        """Read durable autonomous-social state for one group (safe on errors)."""
        if not self.cycle_store:
            return {"interests": [], "todos": [], "pending_thoughts": [], "attention_history": [], "social_pulse": {}}
        try:
            getter = getattr(self.cycle_store, "get_persistent_state", None)
            if not callable(getter):
                return {"interests": [], "todos": [], "pending_thoughts": [], "attention_history": [], "social_pulse": {}}
            state = getter(group_id) or {}
            if not isinstance(state, dict):
                return {"interests": [], "todos": [], "pending_thoughts": [], "attention_history": [], "social_pulse": {}}
            return {
                "interests": state.get("interests") or [],
                "todos": state.get("todos") or [],
                "pending_thoughts": state.get("pending_thoughts") or [],
                "attention_history": state.get("attention_history") or [],
                "social_pulse": state.get("social_pulse") or {},
            }
        except Exception:
            return {"interests": [], "todos": [], "pending_thoughts": [], "social_pulse": {}}

    def _recent_action_texts(self, group_id: str, recent_cycles: list[dict]) -> list[str]:
        """Collect concrete contents from recent executed autonomy actions so the
        engine can hard-stop mechanical repeats without an extra LLM call."""
        texts: list[str] = []
        for item in recent_cycles or []:
            if not isinstance(item, dict):
                continue
            action = item.get("action") if isinstance(item.get("action"), dict) else {}
            content = str(action.get("content") or "").strip()
            if content:
                texts.append(content[:200])
            for step in (action.get("steps") if isinstance(action.get("steps"), list) else []):
                if isinstance(step, str) and str(step).strip():
                    texts.append(str(step).strip()[:200])
                elif isinstance(step, dict) and str(step.get("content") or "").strip():
                    texts.append(str(step["content"]).strip()[:200])
        # Also consider what the bot already said through the ordinary passive
        # chain: the same concrete sentence must not be re-sent by autonomy a
        # moment later.  Group transcripts may store is_bot on the record.
        if group_id and self.memory_manager is not None:
            try:
                getter = getattr(self.memory_manager, "get_group_messages", None)
                if callable(getter):
                    for item in getter(group_id)[-24:]:
                        if not isinstance(item, dict):
                            continue
                        if not item.get("is_bot"):
                            continue
                        content = str(item.get("content") or "").strip()
                        if content:
                            texts.append(content[:200])
            except Exception:
                pass
        return texts

    def _recent_self_messages(self, group_id: str, recent_cycles: list[dict], limit: int = 6) -> list[str]:
        """The exact things the bot itself recently said or decided to say.

        The model cannot reliably avoid repeating itself if it never sees its own
        previous lines in the prompt.  This surfaces them as a short, explicit
        list so the next cycle can pick a genuinely different angle.
        """
        texts: list[str] = []
        seen: set[str] = set()
        for item in self._recent_action_texts(group_id, recent_cycles):
            text = str(item or "").strip()
            if not text:
                continue
            signature = re.sub(r"\s+", "", text.casefold())
            if signature in seen:
                continue
            seen.add(signature)
            texts.append(text[:200])
        return texts[-max(1, int(limit or 6)):]

    def _recurring_motifs(self, group_id: str, recent_cycles: list[dict], limit: int = 5) -> list[str]:
        """Find concrete anchors the bot has already leaned on repeatedly.

        The model can keep circling the same joke/theme across many cycles even
        while rewording it, and it can do so while choosing silence -- in which
        case nothing was ever executed, so action-only bookkeeping sees nothing
        and the model gets no "you are stuck on one thing" signal at all.

        Recurring anchors are found as *maximal shared substrings* between the
        bot's recent thoughts/actions across different cycles.  That yields a
        readable anchor ("钱塘江那句支持微积分的服谢谢喵还挂着") instead of the
        meaningless character fragments that plain n-gram counting produces.
        """
        # One bucket per cycle, so an anchor only counts as recurring when it
        # survives across *different* cycles.
        per_cycle: list[list[str]] = []
        action_texts = self._recent_action_texts(group_id, recent_cycles)
        if action_texts:
            per_cycle.append(action_texts)
        for item in recent_cycles or []:
            if not isinstance(item, dict):
                continue
            thoughts = item.get("thoughts")
            if not isinstance(thoughts, list):
                continue
            cycle_texts = [
                str(thought.get("text") or "").strip()
                for thought in thoughts
                if isinstance(thought, dict) and str(thought.get("text") or "").strip()
            ]
            if cycle_texts:
                per_cycle.append(cycle_texts)
        if not per_cycle:
            return []

        def normalize(text: str) -> str:
            return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", str(text or "").casefold())

        normalized = [
            [normalize(text) for text in cycle_texts]
            for cycle_texts in per_cycle
        ]
        # Shared runs shorter than this are almost always filler (的/了/是谁).
        min_run = 4
        # A run has to survive in at least this many separate cycles.
        min_cycles = 3
        # Compare every pair of *different* cycles.  (Pairing texts inside a
        # single cycle would just mean the model wrote two related thoughts,
        # not that it is stuck across turns.)
        cycle_hits: dict[str, set[int]] = {}
        for a_index in range(len(normalized)):
            for b_index in range(a_index + 1, len(normalized)):
                for left in normalized[a_index]:
                    if len(left) < min_run:
                        continue
                    for right in normalized[b_index]:
                        if len(right) < min_run:
                            continue
                        matcher = SequenceMatcher(None, left, right, autojunk=False)
                        for block in matcher.get_matching_blocks():
                            size = int(block.size)
                            if size < min_run:
                                continue
                            run = left[block.a:block.a + size]
                            hits = cycle_hits.setdefault(run, set())
                            hits.add(a_index)
                            hits.add(b_index)
        def _meaningful(run: str) -> bool:
            # A real motif is a concrete phrase: either it is mostly CJK
            # (group chat is Chinese) or a genuinely long English phrase.
            # Otherwise we just re-surface reasoning filler such as
            # "momentumsilencewouldjustletthe".
            cjk = sum(1 for ch in run if "\u4e00" <= ch <= "\u9fff")
            if cjk >= 4:
                return True
            return cjk == 0 and len(run) >= 24

        candidates = [
            (run, len(cycles))
            for run, cycles in cycle_hits.items()
            if len(cycles) >= min_cycles and _meaningful(run)
        ]
        # Prefer longer, more specific anchors; drop any run already contained
        # in a longer chosen anchor, then keep the prompt payload small.
        candidates.sort(key=lambda pair: (len(pair[0]), pair[1]), reverse=True)
        selected: list[str] = []
        for run, _n in candidates:
            if any(run in existing for existing in selected):
                continue
            selected.append(run[:40])
            if len(selected) >= max(1, int(limit or 5)):
                break
        return selected

    def _is_repeat(self, content: str, recent_cycles: list[dict], group_id: str = "") -> bool:
        """True when the candidate content is a near-duplicate of a recent action
        or a passive-chain bot message.

        Pure local hard guard: blocks the same concrete message from being sent
        again and again while the passive chain still handles genuine new events.
        """
        content = str(content or "").strip()
        if len(content) <= 5:
            return False
        prior = self._recent_action_texts(group_id=group_id, recent_cycles=recent_cycles)
        for old in prior:
            if not old:
                continue
            try:
                ratio = SequenceMatcher(None, content.casefold(), old.casefold()).ratio()
            except Exception:
                ratio = 0.0
            if ratio >= 0.80:
                return True
        # Mechanical template echo: the same concrete sentence has been used
        # many times recently even if not byte-identical.  Cheap local check.
        signature = re.sub(r"[^\w\u4e00-\u9fff]+", "", content.casefold())
        if len(signature) >= 6:
            count = sum(
                1 for old in prior
                if old and re.sub(r"[^\w\u4e00-\u9fff]+", "", old.casefold()) == signature
            )
            if count >= 2:
                return True
        # Lower-similarity repeated phrasing: hard-block only very close
        # duplicates, otherwise let the candidate through so the bot can still
        # genuinely respond to a new message that resembles an old one.
        for old in prior:
            if not old:
                continue
            try:
                ratio = SequenceMatcher(None, content.casefold(), old.casefold()).ratio()
            except Exception:
                ratio = 0.0
            if ratio >= 0.72:
                return True
        return False

    def _resume_candidates(self, group_id: str, recent_cycles: list[dict]) -> list[dict[str, Any]]:
        """Turn durable autonomous state into concrete 'things I might continue'
        candidates so persistent state actually drives later behaviour instead of
        being a passive prompt block.

        Each candidate carries a source tag (interest / todo / pending_thought /
        attention / carried_thought) so the model can prefer continuing its own
        threads.  Pure local read, no extra LLM call.
        """
        candidates: list[dict[str, Any]] = []
        used: set[str] = set()

        def push(source: str, text: Any, state: str = "deferred", interest: float = 0.0) -> None:
            text = str(text or "").strip()
            if not text:
                return
            signature = re.sub(r"\s+", "", text.casefold())
            if signature in used:
                return
            used.add(signature)
            candidates.append({
                "source": source,
                "text": text[:200],
                "state": state,
                "interest": round(_clamp(interest), 3),
            })

        state = self._persistent_state_snapshot(group_id)
        for item in state.get("interests") or []:
            if isinstance(item, dict):
                push("interest", item.get("text") or item.get("focus"), "interest", item.get("interest"))
            else:
                push("interest", item, "interest", 0.5)
        for item in state.get("todos") or []:
            if isinstance(item, dict):
                push("todo", item.get("text") or item.get("goal"), "todo", item.get("priority_score"))
            else:
                push("todo", item, "todo", 0.6)
        for item in state.get("pending_thoughts") or []:
            if isinstance(item, dict):
                push("pending_thought", item.get("text") or item.get("focus"), item.get("state") or "deferred", item.get("interest"))
            else:
                push("pending_thought", item, "deferred", 0.4)
        for item in state.get("attention_history") or []:
            if isinstance(item, dict):
                focus = str(item.get("focus") or "").strip()
                if focus:
                    salience = item.get("salience")
                    push("attention", focus, "attention", salience if salience is not None else 0.3)
        for item in self._carried_thoughts(group_id, recent_cycles):
            push(
                "carried_thought",
                item.get("text"),
                item.get("state") or "deferred",
                item.get("interest"),
            )
        candidates.sort(key=lambda item: float(item.get("interest") or 0.0), reverse=True)
        return candidates[:5]

    def _carried_thoughts(self, group_id: str, recent_cycles: list[dict]) -> list[dict]:
        """Pick concrete unfinished/interesting thoughts from previous cycles so the
        model can keep a continuous train of thought across turns instead of
        deliberating from scratch every time.

        A thought that has been surfaced repeatedly without ever leading to an
        action is decayed and eventually dropped, so the bot cannot get stuck on
        one deferred idea forever.
        """
        carry_limit = 3
        carried: list[dict] = []
        seen: set[str] = set()

        def add(text: str, state: str, interest: float, event_id: str, carried_count: int = 0) -> None:
            text = str(text or "").strip()
            if not text:
                return
            signature = re.sub(r"\s+", "", text.casefold())
            if not signature or signature in seen:
                return
            seen.add(signature)
            # A thought that keeps getting carried without being acted on loses
            # half its interest per extra carry and is dropped after the limit.
            if carried_count > carry_limit:
                return
            adjusted = float(interest or 0.0) * (0.5 ** max(0, carried_count))
            carried.append({
                "text": text[:200],
                "state": state,
                "interest": adjusted,
                "event_id": event_id,
                "carried_count": carried_count,
            })

        # Count how often each concrete thought text has already been surfaced in
        # recent cycles without an action.  This is local bookkeeping only.
        carry_counts: dict[str, int] = {}
        for item in recent_cycles or []:
            if not isinstance(item, dict):
                continue
            action = item.get("action") if isinstance(item.get("action"), dict) else {}
            executed = bool(action.get("content") or action.get("steps"))
            thoughts = item.get("thoughts") if isinstance(item.get("thoughts"), list) else []
            for thought in thoughts:
                if not isinstance(thought, dict):
                    continue
                text = str(thought.get("text") or "").strip()
                if not text:
                    continue
                signature = re.sub(r"\s+", "", text.casefold())
                if executed:
                    continue
                carry_counts[signature] = carry_counts.get(signature, 0) + 1

        for item in recent_cycles or []:
            thoughts = item.get("thoughts") if isinstance(item, dict) else None
            if not isinstance(thoughts, list):
                continue
            for thought in thoughts:
                if not isinstance(thought, dict):
                    continue
                state = str(thought.get("state") or "transient").lower()
                text = str(thought.get("text") or "").strip()
                if not text:
                    continue
                if state in {"deferred", "resurfaced", "interrupted", "competing"}:
                    signature = re.sub(r"\s+", "", text.casefold())
                    add(
                        text,
                        state,
                        float(thought.get("interest") or 0.0),
                        str(item.get("event_id") or "")[:24],
                        carry_counts.get(signature, 0),
                    )
        # Durable pending_thoughts may carry explicit state labels from previous
        # cycles; prefer those with a labelled state so the label survives across
        # opportunities instead of being collapsed to a bare string.
        try:
            state = self._persistent_state_snapshot(group_id)
            for item in state.get("pending_thoughts") or []:
                if not isinstance(item, dict):
                    continue
                text = str(item.get("text") or item.get("focus") or "").strip()
                if not text:
                    continue
                label = str(item.get("state") or "deferred").lower()
                if label not in {"deferred", "resurfaced", "interrupted", "competing"}:
                    label = "deferred"
                signature = re.sub(r"\s+", "", text.casefold())
                add(
                    text,
                    label,
                    float(item.get("interest") or 0.0),
                    "persistent",
                    carry_counts.get(signature, 0),
                )
        except Exception:
            pass
        carried.sort(key=lambda t: float(t.get("interest") or 0.0), reverse=True)
        return carried[:5]

    @staticmethod
    def _merge_persistent_items(old_items, new_items, limit=40):
        """Merge new persistent-state items with the existing ones.

        New items win on exact/near-duplicate text (so the model can update an
        interest or thought), while older distinct items are retained so the bot
        does not lose its previous interests/thoughts on every write.  Works for
        both plain strings and dicts.
        """
        def signature(item):
            if isinstance(item, dict):
                text = str(item.get("text") or item.get("focus") or item.get("goal") or "").strip()
            else:
                text = str(item).strip()
            return re.sub(r"\s+", "", text.casefold())

        old_items = [item for item in (old_items or []) if isinstance(item, (dict, str))]
        new_items = [item for item in (new_items or []) if isinstance(item, (dict, str))]
        # If the model deliberately provided an empty list, keep old items: the
        # write path is additive; explicit clearing is not supported yet.
        if not new_items:
            return old_items[:max(1, int(limit or 40))]

        def near_duplicate(a: str, b: str) -> bool:
            """Treat reworded copies of the same carried item as duplicates.

            The model tends to append near-identical interests/thoughts every
            cycle ("fortune luck premonition with X", "fortune luck premonition
            with Y").  Exact-match dedupe lets those pile up, and the growing
            pile then feeds the next cycle's fixation.  Collapse them locally.
            """
            if not a or not b:
                return False
            if a == b:
                return True
            shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
            if len(shorter) >= 6 and shorter in longer:
                return True
            try:
                return SequenceMatcher(None, a, b).ratio() >= 0.78
            except Exception:
                return False

        merged: list = []
        signatures: list[str] = []
        for item in list(new_items) + list(old_items):
            sig = signature(item)
            if not sig:
                continue
            if any(near_duplicate(sig, existing) for existing in signatures):
                continue
            signatures.append(sig)
            merged.append(item)
        return merged[:max(1, int(limit or 40))]

    def _write_persistent_update(self, group_id: str, data: dict) -> None:
        """Persist the model's own carried-forward interests/todos/thoughts after a cycle."""
        if not self.cycle_store or not isinstance(data, dict):
            return
        update = data.get("persistent_update")
        if not isinstance(update, dict):
            return
        writer = getattr(self.cycle_store, "update_persistent_state", None)
        if not callable(writer):
            return
        kwargs: dict = {}
        for key in ("interests", "todos", "pending_thoughts", "attention_history"):
            value = update.get(key)
            if isinstance(value, list):
                if key == "attention_history":
                    cleaned = [
                        {
                            "focus": str(item.get("focus") or "")[:120],
                            "why_relevant": str(item.get("why_relevant") or "")[:200],
                            "salience": _clamp(item.get("salience")),
                            "ts": int(float(item.get("ts") or 0.0)),
                        }
                        for item in value
                        if isinstance(item, dict) and str(item.get("focus") or "").strip()
                    ][-40:]
                elif key == "pending_thoughts":
                    # Keep the lifecycle label so a deferred/interrupted/resurfaced
                    # thought can be re-activated later instead of being flattened
                    # to a bare string.  Plain strings stay strings (back-compat).
                    cleaned = []
                    for item in value:
                        if isinstance(item, dict):
                            text = str(item.get("text") or item.get("focus") or "").strip()
                            if not text:
                                continue
                            state = str(item.get("state") or "deferred").strip()[:20]
                            cleaned.append({
                                "text": text[:200],
                                "state": state or "deferred",
                                "interest": round(_clamp(item.get("interest")), 3),
                                "ts": int(time.time()),
                            })
                        elif isinstance(item, str) and str(item).strip():
                            cleaned.append(str(item).strip()[:200])
                    cleaned = cleaned[:40]
                else:
                    cleaned = [
                        str(item)[:200] for item in value
                        if isinstance(item, str) and str(item).strip()
                    ][:40]
                if not cleaned and key == "pending_thoughts":
                    # The model often answers an empty pending_thoughts list.
                    # Keep the existing concrete threads instead of wiping them,
                    # and promote any concrete attention focus from this cycle so
                    # a real unfinished observation can be revisited later.
                    state = self._persistent_state_snapshot(group_id)
                    existing = state.get("pending_thoughts") or []
                    attention = update.get("attention_history")
                    promoted = []
                    if isinstance(attention, list):
                        for item in attention:
                            if not isinstance(item, dict):
                                continue
                            focus = str(item.get("focus") or "").strip()
                            if not focus or len(focus) <= 2:
                                continue
                            promoted.append({
                                "text": focus[:200],
                                "state": "deferred",
                                "interest": round(_clamp(item.get("salience")), 3),
                                "ts": int(time.time()),
                            })
                    if promoted:
                        cleaned = self._merge_persistent_items(existing, promoted, 12)
                    else:
                        cleaned = existing[:12]
                if cleaned:
                    state = self._persistent_state_snapshot(group_id)
                    existing = state.get(key) or []
                    # Keep the durable lists small: a large pile of near-identical
                    # carried themes becomes a fixation amplifier, because every
                    # cycle re-reads it and keeps talking about the same thing.
                    # interests/todos are orientation, not an archive.
                    caps = {
                        "interests": 8,
                        "todos": 8,
                        "pending_thoughts": 12,
                        "attention_history": 40,
                    }
                    kwargs[key] = self._merge_persistent_items(existing, cleaned, caps.get(key, 40))
        pulse = update.get("social_pulse")
        if isinstance(pulse, dict):
            kwargs["social_pulse"] = {
                str(k)[:40]: str(v)[:80] for k, v in pulse.items()
                if str(v).strip()
            }
        if not kwargs:
            return
        try:
            writer(group_id, **kwargs)
        except Exception as exc:
            self.logger.learn(event_id="persistent_update", updated=False, error=str(exc))

    def _write_social_pulse(self, group_id: str, updates: dict[str, Any]) -> None:
        """Merge small behaviour-feedback notes into social_pulse (durable)."""
        if not self.cycle_store or not isinstance(updates, dict) or not updates:
            return
        try:
            writer = getattr(self.cycle_store, "update_persistent_state", None)
            if not callable(writer):
                return
            state = self.cycle_store.get_persistent_state(group_id)
            pulse = dict(state.get("social_pulse") or {})
            pulse.update(
                {str(k)[:40]: str(v)[:80] for k, v in updates.items() if str(v).strip()}
            )
            writer(group_id, social_pulse=pulse)
        except Exception:
            pass

    def _record_cycle(self, result: AutonomyResult) -> None:
        if not self.cycle_store or not result:
            return
        try:
            self.cycle_store.record_autonomy_cycle(
                result.group_id,
                result.event_id,
                result.to_dict(),
            )
        except Exception as exc:
            self.logger.learn(event_id=result.event_id, recorded=False, error=str(exc))

    def _settle_pending_effects(self, group_id: str) -> None:
        if not self.cycle_store or not self.memory_manager:
            return
        try:
            pending = self.cycle_store.list_pending_autonomy_cycles(group_id, limit=3)
        except Exception:
            return
        now = time.time()
        try:
            delay = max(30.0, float(self.config.get("effect_window_seconds", 120) or 120))
        except (TypeError, ValueError):
            delay = 120.0
        for item in pending:
            created_at = float(item.get("created_at") or 0)
            if created_at and now - created_at < delay:
                continue
            self.evaluate_effect(
                str(item.get("event_id") or ""),
                group_id,
                item.get("action") or {},
                started_at=created_at,
            )

    def evaluate_effect(
        self,
        event_id: str,
        group_id: str,
        action: dict[str, Any],
        started_at: float,
    ) -> dict[str, Any]:
        if not self.memory_manager:
            return {}
        try:
            messages = self.memory_manager.get_group_messages(group_id)
        except Exception:
            messages = []
        effect = self.effect_evaluator.evaluate(action, messages, started_at)
        if bool(self.config.get("effect_judge_enabled", False)):
            try:
                effect = self.effect_evaluator.evaluate_with_llm(
                    None,
                    action,
                    messages,
                    effect,
                    self.config,
                    provider_call=self.provider_call,
                )
            except Exception as exc:
                self.logger.learn(
                    event_id=event_id,
                    effect_judge=False,
                    error=f"{type(exc).__name__}: {exc}",
                )
        self.logger.effect(event_id=event_id, group_id=group_id, effect=effect)
        if self.cycle_store:
            try:
                self.cycle_store.update_autonomy_effect(event_id, effect)
                self.cycle_store.update_autonomy_state(group_id, effect)
            except Exception as exc:
                self.logger.learn(event_id=event_id, updated=False, error=str(exc))
            # Behaviour-level feedback closure: whether the action was worth
            # continuing / reusable is written back into social_pulse so the
            # next opportunity can inject it and the bot can calibrate instead
            # of repeating the same mode blindly.
            try:
                pulse_update = {}
                if "reusable_pattern" in effect:
                    pulse_update["last_reusable_pattern"] = "true" if bool(effect.get("reusable_pattern")) else "false"
                if "worth_continuing" in effect:
                    pulse_update["last_worth_continuing"] = str(round(_clamp(effect.get("worth_continuing")), 3))
                if "outcome" in effect:
                    pulse_update["last_effect_outcome"] = str(effect.get("outcome") or "neutral")
                if "followup_count" in effect:
                    pulse_update["last_followup_count"] = str(int(effect.get("followup_count") or 0))
                pulse_update["last_effect_at"] = str(int(time.time()))
                self._write_social_pulse(group_id, pulse_update)
            except Exception:
                pass
        if effect.get("stop_signal") or str(effect.get("outcome") or "").lower() == "negative":
            self.gate.trigger_loop_guard(
                group_id,
                seconds=float(self.config.get("negative_effect_cooldown_seconds", 300) or 300),
            )
        return effect

    def schedule_effect_evaluation(
        self,
        event_id: str,
        group_id: str,
        action: dict[str, Any],
        started_at: float,
    ) -> None:
        try:
            delay = max(30.0, float(self.config.get("effect_window_seconds", 120) or 120))
        except (TypeError, ValueError):
            delay = 120.0
        if self._closed:
            return

        try:
            _ensure_effect_workers()
            _EFFECT_QUEUE.put_nowait((self, delay, event_id, group_id, action, started_at))
        except queue.Full:
            self.logger.learn(event_id=event_id, effect_scheduled=False, reason="effect_queue_full")
