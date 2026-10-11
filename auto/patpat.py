import re

CONFIG = None
MATCH_RAW_MESSAGE = True
INTERCEPT_LLM = True


def init(config):
    global CONFIG
    CONFIG = config
    print("[AUTO PLUGIN] patpat 初始化完成")


# =========================================================
# 🧠 统一 content 提取（适配 dispatcher context）
# =========================================================
def _get_content(context):

    if not isinstance(context, dict):
        return ""

    # 直接标准字段（新 dispatcher 保证有）
    content = context.get("content")

    if content:
        return str(content).strip()

    # fallback：兼容旧 raw
    raw = context.get("raw")
    if isinstance(raw, dict):
        return str(
            raw.get("content")
            or raw.get("text")
            or ""
        ).strip()

    return ""


# =========================================================
# 🔥 dispatcher 统一入口（必须叫 handle_auto）
# =========================================================
def handle_auto(context):

    content = _get_content(context)

    user = context.get("user", "未知用户")
    group = context.get("group")

    # =========================
    # 空内容直接跳过
    # =========================
    if not content:
        return None

    # =========================
    # 匹配规则：拍了拍 + prefix 即触发
    # WeChat 拍一拍消息可能吞掉 prefix 开头的 @，做兼容
    # =========================
    prefixes = CONFIG.get("prefix", []) if CONFIG else []

    def _match_prefix(text, pfx):
        pfx = str(pfx or "").strip()
        if not pfx:
            return False
        candidates = [pfx]
        if pfx.startswith("@"):
            candidates.append(pfx[1:])
        # Match only the immediate target after the action; do not search the
        # remainder of the sentence, which can mention the bot as bystander.
        for candidate in candidates:
            escaped = re.escape(candidate)
            pattern = r"\u62cd\u4e86\u62cd\s*[\"\u201c\u2018]?@?" + escaped + r"[\"\u201d\u2019]?(?:\s|$|[\u3001\u3002\uff0c\uff01\uff1f!?])"
            if re.search(pattern, text, flags=re.IGNORECASE):
                return True
        return False

    for prefix in prefixes:
        if _match_prefix(content, prefix):
            target = group or user
            if not target:
                return None
            return "拍你吗"

    return None


def allow_llm(context):
    content = _get_content(context)
    if not content:
        return True
    prefixes = CONFIG.get("prefix", []) if CONFIG else []
    for prefix in prefixes:
        if _match_prefix(content, prefix):
            return False
    return True
