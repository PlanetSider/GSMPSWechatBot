# -*- coding: utf-8 -*-
"""Local, deterministic computation of human interaction initiative.

This module deliberately has no model, embedding, or database dependency.  It
turns a transcript window into:

- per-message initiative features (proactive vs follow-up, timing, content hints)
- an aggregated group initiative / timing profile used to bias when the bot
  should participate (work / attention / fragmented-chat windows)
- topic-overlap helpers shared by behavior-pattern retrieval

The output is evidence for the LLM decision prompt, never an instruction to
speak.  A proactive label means "a group member started this thread / chose to
speak"; it does not order the bot to post.
"""

from __future__ import annotations

import re
import time
from datetime import datetime

_QUESTION_MARKS = ("?", "\uff1f")
_REQUEST_MARKERS = (
    "帮我", "帮忙", "能不能", "可以吗", "有没有", "怎么", "如何", "请问", "求",
    "推荐", "解释", "看看", "查一下", "告诉我", "有谁", "谁有", "咋", "为什么",
    "啥情况", "怎么办", "哪个", "多少", "几号", "几点", "咋办",
)
_OPINION_MARKERS = (
    "觉得", "认为", "怎么看", "意见", "建议", "投票", "选哪个", "感觉", "点评",
    "评价", "感想", "观点", "站谁",
)
_BANTER_MARKERS = (
    "笑死", "绷不住", "难绷", "好活", "典", "逆天", "乐", "整活", "玩梗", "抽象",
    "流汗", "蚌埠", "破防", "麻了", "绝了", "离谱", "天才", "嘴硬", "开团",
    "引战", "钓鱼", "锐评", "吐槽", "调侃", "阴阳", "复读", "烂梗", "梗",
    "哈哈哈", "绷", "孝", "捞", "鉴定",
)
_SHORT_REACTION = re.compile(
    r"^[\W_\d]*(?:哈|笑|乐|绷|典|6|啊|哦|嗯|啧|草|麻|绝|强|牛|菜|捞|顶|nb|yyds|awsl|dddd|skr){1,12}[\W_\d]*$",
    re.UNICODE,
)
_TOPIC_SPLIT = re.compile(r"[^\w\u3400-\u9fff]+")
_EVENT_MARKERS = (
    "今天", "昨天", "刚才", "服务器", "更新", "比赛", "活动", "直播", "开服",
    "版本", "公告", "维护", "炸服", "崩了", "事件", "事故",
)
_MEMBER_MARKERS = ("某人", "这位", "那位", "有人", "谁", "他", "她", "你", "我")
_SHARE_MARKERS = ("分享", "转发", "链接", "视频", "教程", "攻略", "推荐", "看看这个", "发你")


def _text(item):
    if isinstance(item, dict):
        return str(item.get("content") or "").strip()
    return ""


def _is_bot(item):
    return bool(isinstance(item, dict) and (item.get("is_bot") or str(item.get("role") or "") == "assistant"))


def _timestamp(item, fallback=0.0):
    try:
        value = float(item.get("timestamp"))
    except (TypeError, ValueError):
        return float(fallback or 0.0)
    return value if value > 0 else float(fallback or 0.0)


def _speaker(item):
    return str(
        item.get("wxid") or item.get("sender_wxid") or item.get("sender_id")
        or item.get("sender") or item.get("speaker") or item.get("nickname") or ""
    ).strip()


def _is_direct(item):
    return bool(
        isinstance(item, dict)
        and (item.get("directed_to_bot") or item.get("reply_to_bot")
             or item.get("is_at_bot") or item.get("prefix_used"))
    )


def _is_directed_other(item):
    return bool(
        isinstance(item, dict)
        and (item.get("directed_to_other") or item.get("reply_to_other"))
        and not _is_direct(item)
    )
def topic_tokens(text):
    """Extract a small token set used for cheap topic-overlap matching."""
    compact = "".join(str(text or "").split()).lower()
    if len(compact) < 2:
        return set()
    words = {word for word in re.findall(r"[a-z0-9_]{2,}", compact) if len(word) >= 2}
    cjk = "".join(char for char in compact if "\u3400" <= char <= "\u9fff")
    grams = {cjk[index:index + 2] for index in range(len(cjk) - 1)}
    return words | grams


def topic_overlap(left, right):
    a = topic_tokens(left)
    b = topic_tokens(right)
    if not a or not b:
        return 0.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def content_type_hint(text, direct=False, is_short=False):
    text = str(text or "").strip()
    compact = "".join(text.split()).lower()
    if not compact:
        return "other"
    if is_short and _SHORT_REACTION.match(compact):
        return "reaction"
    if any(mark in text for mark in _QUESTION_MARKS) or any(mark in text for mark in _REQUEST_MARKERS):
        return "question"
    if any(mark in text for mark in _OPINION_MARKERS):
        return "opinion"
    if any(mark in text for mark in _BANTER_MARKERS):
        return "banter"
    if len(compact) <= 4:
        return "reaction"
    if len(compact) >= 12 and any(mark in text for mark in _SHARE_MARKERS):
        return "share"
    return "other"


def guess_target_type(text):
    """Cheap local target-type guess used only to soften retrieval matching."""
    text = str(text or "")
    if any(mark in text for mark in _MEMBER_MARKERS):
        return "group_member"
    if any(mark in text for mark in _EVENT_MARKERS):
        return "event"
    if any(mark in text for mark in _BANTER_MARKERS):
        return "meme"
    return "none"


def compute_message_initiative(message, context=None, now=None):
    """Compute local initiative features for one human message.

    ``context`` is the ordered list of messages before ``message`` in the same
    window (bot messages are skipped for speaker/topic comparison but kept for
    density/gap when they carry real timestamps).
    """
    now = float(now or time.time())
    result = {
        "proactive": -1,
        "follows_previous": False,
        "reply_target_kind": "none",
        "gap_seconds": -1.0,
        "hour_of_day": -1,
        "density_60s": 0,
        "content_type": "other",
        "topic_overlap": 0.0,
        "short_reaction": False,
        "has_context": False,
    }
    text = _text(message)
    compact = "".join(text.split()).lower()
    if not text:
        return result
    ts = _timestamp(message, now)
    hour = -1
    if ts > 0:
        try:
            hour = datetime.fromtimestamp(ts).hour
        except (OverflowError, OSError, ValueError):
            hour = -1
    result["hour_of_day"] = hour
    context = [item for item in (context or []) if isinstance(item, dict) and _text(item)]
    result["has_context"] = bool(context)
    if ts > 0:
        recent_60 = [
            item for item in context
            if _timestamp(item, 0.0) > 0 and ts - 60 <= _timestamp(item, 0.0) <= ts
        ]
        result["density_60s"] = len(recent_60)
    prev_ts = 0.0
    if context:
        prev_ts = _timestamp(context[-1], ts)
    if ts > 0 and prev_ts > 0:
        result["gap_seconds"] = round(ts - prev_ts, 1)
    if _is_direct(message):
        result["reply_target_kind"] = "bot"
    elif _is_directed_other(message):
        result["reply_target_kind"] = "other"
    human_prev = None
    for item in reversed(context):
        if not _is_bot(item):
            human_prev = item
            break
    prev_text = _text(human_prev) if human_prev is not None else ""
    prev_speaker = _speaker(human_prev) if human_prev is not None else ""
    overlap = topic_overlap(prev_text, text) if prev_text else 0.0
    result["topic_overlap"] = round(overlap, 3)
    is_short = len(compact) <= 8
    result["short_reaction"] = bool(is_short and _SHORT_REACTION.match(compact))
    result["content_type"] = content_type_hint(text, direct=_is_direct(message), is_short=is_short)
    if result["reply_target_kind"] in ("bot", "other"):
        result["proactive"] = 0
        result["follows_previous"] = True
    elif not prev_text:
        result["proactive"] = 1
    elif prev_speaker == _speaker(message):
        result["proactive"] = 1 if overlap < 0.15 else -1
    elif is_short or overlap >= 0.25:
        result["proactive"] = 0
        result["follows_previous"] = True
    elif overlap < 0.12 and result["gap_seconds"] >= 30:
        result["proactive"] = 1
    else:
        result["proactive"] = -1
    return result


def build_initiative_profile(messages, now=None):
    """Aggregate initiative / timing stats for a window of human messages."""
    profile = {
        "sample_count": 0,
        "proactive_count": 0,
        "follow_count": 0,
        "unknown_count": 0,
        "proactive_rate": 0.5,
        "follow_rate": 0.5,
        "avg_gap_seconds": 0.0,
        "topic_switch_rate": 0.0,
        "content_types": {},
        "hour_buckets": {},
        "updated_at": int(time.time()),
    }
    human = [
        item for item in (messages or [])
        if isinstance(item, dict) and _text(item) and not _is_bot(item)
    ]
    if not human:
        return profile
    gaps = []
    switches = 0
    prev_topic_tokens = set()
    for index, item in enumerate(human):
        feats = compute_message_initiative(item, human[:index], now=now)
        if feats["proactive"] == 1:
            profile["proactive_count"] += 1
        elif feats["proactive"] == 0:
            profile["follow_count"] += 1
        else:
            profile["unknown_count"] += 1
        content_type = feats["content_type"]
        profile["content_types"][content_type] = profile["content_types"].get(content_type, 0) + 1
        hour = feats["hour_of_day"]
        if hour >= 0:
            profile["hour_buckets"][hour] = profile["hour_buckets"].get(hour, 0) + 1
        if feats["gap_seconds"] >= 0:
            gaps.append(feats["gap_seconds"])
        tokens = topic_tokens(_text(item))
        if prev_topic_tokens and not (tokens & prev_topic_tokens):
            switches += 1
        if tokens:
            prev_topic_tokens = tokens
    profile["sample_count"] = len(human)
    profile["proactive_rate"] = round(profile["proactive_count"] / max(1, len(human)), 3)
    profile["follow_rate"] = round(profile["follow_count"] / max(1, len(human)), 3)
    if gaps:
        profile["avg_gap_seconds"] = round(sum(gaps) / len(gaps), 1)
    profile["topic_switch_rate"] = round(switches / max(1, len(human)), 3)
    return profile
def merge_timing_profiles(existing, new, max_samples=6000):
    """Weighted merge of an existing stored profile with a fresh window."""
    merged = dict(existing or {})
    if not isinstance(merged, dict):
        merged = {}
    old_count = max(0, int(merged.get("sample_count") or 0))
    new_count = max(0, int((new or {}).get("sample_count") or 0))
    if new_count <= 0:
        return merged
    total = old_count + new_count
    for key in ("proactive_count", "follow_count", "unknown_count"):
        merged[key] = int(merged.get(key) or 0) + int((new or {}).get(key) or 0)
    old_buckets = merged.get("hour_buckets") if isinstance(merged.get("hour_buckets"), dict) else {}
    new_buckets = (new or {}).get("hour_buckets") if isinstance((new or {}).get("hour_buckets"), dict) else {}
    buckets = dict(old_buckets)
    for hour, count in (new_buckets or {}).items():
        buckets[int(hour)] = int(buckets.get(int(hour), 0)) + int(count)
    merged["hour_buckets"] = buckets
    old_content = merged.get("content_types") if isinstance(merged.get("content_types"), dict) else {}
    new_content = (new or {}).get("content_types") if isinstance((new or {}).get("content_types"), dict) else {}
    content = dict(old_content)
    for name, count in (new_content or {}).items():
        content[name] = int(content.get(name, 0)) + int(count)
    merged["content_types"] = content
    merged["sample_count"] = min(max_samples, total)
    merged["proactive_count"] = int(merged.get("proactive_count") or 0)
    merged["follow_count"] = int(merged.get("follow_count") or 0)
    sample_count = merged["sample_count"]
    merged["proactive_rate"] = round(merged["proactive_count"] / max(1, sample_count), 3)
    merged["follow_rate"] = round(merged["follow_count"] / max(1, sample_count), 3)
    old_gap = float(merged.get("avg_gap_seconds") or 0.0)
    new_gap = float((new or {}).get("avg_gap_seconds") or 0.0)
    if old_gap > 0 and new_gap > 0 and total:
        merged["avg_gap_seconds"] = round((old_gap * old_count + new_gap * new_count) / total, 1)
    elif new_gap > 0:
        merged["avg_gap_seconds"] = round(new_gap, 1)
    old_switch = float(merged.get("topic_switch_rate") or 0.0)
    new_switch = float((new or {}).get("topic_switch_rate") or 0.0)
    if total:
        merged["topic_switch_rate"] = round(
            (old_switch * old_count + new_switch * new_count) / total, 3
        )
    merged["updated_at"] = int(time.time())
    return merged


def hour_activity_level(profile, hour=None):
    """Map current hour to high / medium / low / unknown activity level."""
    buckets = profile.get("hour_buckets") if isinstance(profile, dict) else {}
    if not buckets:
        return "unknown"
    if hour is None:
        hour = datetime.now().hour
    counts = sorted([int(value) for value in buckets.values() if int(value) > 0], reverse=True)
    if not counts:
        return "unknown"
    threshold_high = counts[min(2, len(counts) - 1)]
    threshold_low = counts[-1]
    current = int(buckets.get(int(hour), 0) or 0)
    if current >= threshold_high and threshold_high > threshold_low:
        return "high"
    if current > threshold_low:
        return "medium"
    return "low"


def summarize_batch_features(batch_messages, group_messages=None):
    """Aggregate initiative features for a decision batch (used for prior)."""
    batch = [item for item in (batch_messages or []) if isinstance(item, dict) and _text(item)]
    if not batch:
        return {
            "proactive": -1,
            "content_type": "other",
            "target_type": "none",
            "has_context": False,
            "batch_size": 0,
            "per_message": [],
        }
    context = [item for item in (group_messages or []) if isinstance(item, dict)] + batch[:-1]
    features = []
    for item in batch:
        features.append(compute_message_initiative(item, context, now=None))
    proactive_values = [f["proactive"] for f in features if f["proactive"] in (0, 1)]
    proactive = (
        1 if proactive_values and sum(proactive_values) / len(proactive_values) >= 0.5 else (
            0 if proactive_values else -1
        )
    )
    content_counts = {}
    for f in features:
        content_counts[f["content_type"]] = content_counts.get(f["content_type"], 0) + 1
    content_type = max(content_counts, key=content_counts.get) if content_counts else "other"
    texts = "\n".join(_text(item) for item in batch)
    return {
        "proactive": proactive,
        "content_type": content_type,
        "target_type": guess_target_type(texts),
        "has_context": any(f["has_context"] for f in features),
        "batch_size": len(batch),
        "per_message": features,
    }