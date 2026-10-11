from __future__ import annotations

import copy


DEFAULT_AUTONOMY_CONFIG = {
    "enabled": False,
    "shadow_mode": True,
    "log_json_console": True,
    "log_json_file": False,
    "log_sample_rate": 1.0,
    "log_file_path": "logs/autonomy.jsonl",
    "cooldown_seconds": 300,
    "min_interval_seconds": 60,
    "max_opportunities_per_hour": 6,
    "shadow_max_opportunities_per_hour": 1,
    "max_actions_per_hour": 2,
    "max_duration_seconds": 30,
    "candidate_exploration_enabled": True,
    "min_candidates": 3,
    "min_non_silent_candidates": 2,
    "divergence_retry_limit": 1,
    "check_interval_seconds": 60,
    "background_scheduler_enabled": True,
    "allow_followup_after_autonomy": False,
    "consumed_event_ttl_seconds": 600,
    "negative_effect_cooldown_seconds": 300,
    "effect_window_seconds": 120,
    "effect_judge_enabled": True,
    "effect_judge_max_messages": 20,
    "effect_judge_thinking_params": {},
    "autonomy_cycle_retention_days": 30,
    "direct_send_enabled": False,
    "allowed_action_kinds": ["reply", "other_bot_command", "tool", "tool_call", "mixed_action"],
    "allow_other_bot_commands": False,
    "other_bot_command_prefixes": ["/"],
    "other_bot_command_allowlist": [],
    "other_bot_command_denylist": [
        "/kick", "/ban", "/delete", "/remove", "/shutdown", "/restart",
        "/reboot", "/admin", "/exec", "/eval", "/pay", "/transfer",
    ],
    "other_bot_names": [],
    "thinking_params": {},
    "system_guidance": "",
    "divergence_enabled": True,
    "drift_weights": {"subtle": 1, "active": 4, "scattered": 3, "wild": 2},
    "divergence_anchor_policies": ["strict", "balanced", "loose"],
    "divergence_reaction_styles": ["reserved", "natural", "lively"],
    "divergence_flavor_chance": 0.7,
    "divergence_max_seed_hooks": 2,
    "persistent_state_enabled": True,
    "exploration_bias": "auto",
    "idle_consolidation_enabled": True,
    "persistent_max_items": 40,
}


def get_autonomy_config(llm_config):
    config = copy.deepcopy((llm_config or {}).get("autonomy") or {})
    for key, value in DEFAULT_AUTONOMY_CONFIG.items():
        if key not in config:
            config[key] = copy.deepcopy(value)
    if not isinstance(config.get("allowed_action_kinds"), list):
        config["allowed_action_kinds"] = ["reply"]
    if not isinstance(config.get("thinking_params"), dict):
        config["thinking_params"] = {}
    return config