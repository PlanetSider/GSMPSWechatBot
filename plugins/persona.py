import json

from utils.persona_manager import (
    create_persona, delete_persona, get_persona, get_active_persona,
    load_persona, stop_persona, set_banned,
)
from utils.points_manager import try_spend_points, add_points, get_points

COMMAND = "/persona"
CREATE_COST = 100
DELETE_REFUND = 70
_config = {}
_llm_service = None


def init(config):
    global _config
    _config = config or {}


def set_llm_service(service):
    global _llm_service
    _llm_service = service


def _wxid(context):
    raw = (context or {}).get("raw") or {}
    return str((context or {}).get("wxid") or raw.get("wxid") or "").strip()


def _group(context):
    return str((context or {}).get("group") or "").strip()


def _admins():
    llm_config = _config.get("llm") or {}
    raw = _config.get("admin_wxids") or llm_config.get("admin_wxids") or []
    if isinstance(raw, str):
        raw = [raw]
    return {str(value).strip() for value in raw if str(value).strip()}


def _is_admin(wxid):
    return str(wxid or "").strip() in _admins()


def _number(value):
    try:
        number = int(str(value).strip())
        return number if number > 0 else None
    except (TypeError, ValueError):
        return None


def _help():
    return (
        "Persona \u547d\u4ee4\uff1a\n"
        "/persona <\u63cf\u8ff0>\uff1a\u521b\u5efa Persona\uff1a\u6d88\u8017 100 \u79ef\u5206\n"
        "/persona load <\u7f16\u53f7>\uff1a\u52a0\u8f7d Persona\n"
        "/persona stop\uff1a\u6062\u590d\u9ed8\u8ba4 Persona\n"
        "/persona delete <\u7f16\u53f7>\uff1a\u5220\u9664\u81ea\u5df1\u7684 Persona\uff1a\u6216\u7531 Bot \u7ba1\u7406\u5458\u5220\u9664\uff1a\u8fd4\u8fd8 70 \u79ef\u5206\n"
        "/persona ban <\u7f16\u53f7>\uff1a\u7981\u7528 Persona\uff1a\u7ba1\u7406\u5458\uff1a\n"
        "/persona unban <\u7f16\u53f7>\uff1a\u542f\u7528 Persona\uff1a\u7ba1\u7406\u5458\uff1a"
    )


def _summary(identity):
    identity = identity if isinstance(identity, dict) else {}
    name = str(identity.get("name") or "\u672a\u547d\u540d Persona").strip()
    role = str(identity.get("role") or "\u672a\u63d0\u4f9b").strip()
    style = str(identity.get("style") or "\u672a\u63d0\u4f9b").strip()
    return name, role, style


def handle(content, context):
    text = str(content or "").strip()
    wxid = _wxid(context)
    group = _group(context)
    if not wxid:
        return "\u65e0\u6cd5\u8bc6\u522b\u547d\u4ee4\u64cd\u4f5c\u8005\uff1a\u4e0d\u80fd\u5904\u7406 Persona \u547d\u4ee4\uff1a"
    if not text:
        return _help()

    parts = text.split(None, 1)
    action = parts[0].casefold()
    rest = parts[1].strip() if len(parts) > 1 else ""

    if action == "stop":
        active = get_active_persona(group)
        if not active:
            return "\u5f53\u524d\u7fa4\u804a\u6ca1\u6709\u52a0\u8f7d\u4e2d\u7684 Persona\uff1a"
        state = active.get("active") or {}
        allowed = {
            str(state.get("loaded_by_wxid") or "").strip(),
            str(state.get("creator_wxid") or active.get("creator_wxid") or "").strip(),
            str(state.get("persona_creator_wxid") or active.get("persona_creator_wxid") or "").strip(),
        }
        if wxid not in {x for x in allowed if x} and not _is_admin(wxid):
            return "\u53ea\u6709 Persona \u53d1\u8d77\u4eba\uff1a\u521b\u5efa\u8005\u6216 Bot \u7ba1\u7406\u5458\u53ef\u4ee5\u505c\u6b62\u5f53\u524d Persona\uff1a"
        spent = float(state.get("spent_points") or 0.0)
        requests = int(state.get("request_count") or 0)
        stop_persona(group)
        return f"\u5df2\u6062\u590d\u9ed8\u8ba4 Persona\uff1a\n\u672c\u6b21\u5df2\u6d88\u8017\u79ef\u5206\uff1a{spent:.1f}\n\u5b9e\u9645\u53d1\u8d77 LLM \u8bf7\u6c42\uff1a{requests} \u6b21"

    if action == "delete":
        persona_id = _number(rest)
        if not persona_id:
            return "\u7528\u6cd5\uff1a/persona delete <\u7f16\u53f7>"
        item = get_persona(persona_id, include_banned=True)
        if not item:
            return f"\u672a\u627e\u5230 Persona #{persona_id}\uff1a"
        owner = str(item.get("creator_wxid") or "").strip()
        if wxid != owner and not _is_admin(wxid):
            return "\u53ea\u6709 Persona \u521b\u5efa\u8005\u672c\u4eba\u6216 Bot \u7ba1\u7406\u5458\u53ef\u4ee5\u5220\u9664\u8be5 Persona\uff1a"
        deleted = delete_persona(persona_id)
        if not deleted:
            return f"Persona #{persona_id} \u5220\u9664\u5931\u8d25\uff1a"
        remaining = add_points(wxid, DELETE_REFUND)
        return f"\u5df2\u5220\u9664 Persona #{persona_id}\uff1a\u8fd4\u8fd8 {DELETE_REFUND} \u79ef\u5206\uff1a\u5f53\u524d\u79ef\u5206\uff1a{float(remaining):.1f}"

    if action in {"ban", "unban"}:
        if not _is_admin(wxid):
            return "\u53ea\u6709 Bot \u7ba1\u7406\u5458\u53ef\u4ee5\u7981\u7528\u6216\u542f\u7528 Persona\uff1a"
        persona_id = _number(rest)
        if not persona_id:
            return f"\u7528\u6cd5\uff1a/persona {action} <\u7f16\u53f7>"
        item = set_banned(persona_id, action == "ban")
        if not item:
            return f"\u672a\u627e\u5230 Persona #{persona_id}\uff1a"
        if action == "ban":
            return f"\u5df2\u7981\u7528 Persona #{persona_id}\uff1a\u65b0\u7684\u52a0\u8f7d\u8bf7\u6c42\u5c06\u88ab\u62d2\u7edd\uff1a"
        return f"\u5df2\u542f\u7528 Persona #{persona_id}\uff1a"

    if action == "load":
        persona_id = _number(rest)
        if not persona_id:
            return "\u7528\u6cd5\uff1a/persona load <\u7f16\u53f7>"
        item = get_persona(persona_id)
        if not item:
            banned = get_persona(persona_id, include_banned=True)
            if banned and banned.get("banned"):
                return f"Persona #{persona_id} \u5df2\u88ab\u7981\u7528\uff1a"
            return f"\u672a\u627e\u5230 Persona #{persona_id}\uff1a"
        if not group:
            return "\u5f53\u524d\u6d88\u606f\u6ca1\u6709\u5bf9\u5e94\u7fa4\u804a\uff1a\u4e0d\u80fd\u52a0\u8f7d Persona\uff1a"
        loaded = load_persona(group, persona_id, wxid)
        if not loaded:
            return "Persona \u52a0\u8f7d\u5931\u8d25\uff1a\u8bf7\u7a0d\u540e\u91cd\u8bd5\uff1a"
        name, role, style = _summary(loaded.get("identity"))
        return (
            f"\u5df2\u52a0\u8f7d Persona #{persona_id}\n"
            f"\u540d\u79f0\uff1a{name}\n\u89d2\u8272\uff1a{role}\n\u98ce\u683c\uff1a{style}\n\n"
            "\u52a0\u8f7d\u671f\u95f4\u6bcf\u6b21\u5b9e\u9645 LLM \u8bf7\u6c42\u5c06\u989d\u5916\u6d88\u8017\u547d\u4ee4\u53d1\u8d77\u8005 5 \u79ef\u5206\uff1a\n"
            "\u7ed3\u675f\u52a0\u8f7d\uff1a/persona stop"
        )

    source = text
    if len(source) > 4000:
        return "Persona \u63cf\u8ff0\u4e0d\u80fd\u8d85\u8fc7 4000 \u4e2a\u5b57\u7b26\uff1a"
    if not _llm_service:
        return "LLM \u670d\u52a1\u5c1a\u672a\u521d\u59cb\u5316\uff1a\u6682\u65f6\u4e0d\u80fd\u751f\u6210 Persona\uff1a"
    ok, remaining = try_spend_points(wxid, CREATE_COST)
    if not ok:
        return f"\u521b\u5efa Persona \u9700\u8981 {CREATE_COST} \u79ef\u5206\uff1a\u5f53\u524d\u53ea\u6709 {get_points(wxid):.1f} \u79ef\u5206\uff1a"
    try:
        identity = _llm_service.create_persona_from_text(source)
        item = create_persona(wxid, "", source, identity)
    except Exception as exc:
        add_points(wxid, CREATE_COST)
        print("[PERSONA CREATE ERROR]", exc, flush=True)
        return "Persona \u751f\u6210\u5931\u8d25\uff1a\u5df2\u9000\u8fd8\u521b\u5efa\u8d39\u7528\uff1a"

    saved_card = json.dumps({"identity": item.get("identity") or {}}, ensure_ascii=False, indent=2)
    return (
        "Persona \u521b\u5efa\u6210\u529f\uff1a\n\n"
        f"\u7f16\u53f7\uff1a{item['id']}\n"
        "\u751f\u6210\u7684\u4eba\u8bbe\u5361\uff1a\n"
        f"{saved_card}\n\n"
        f"\u5df2\u6d88\u8017\uff1a{CREATE_COST} \u79ef\u5206\uff1a\u5269\u4f59\uff1a{float(remaining):.1f}\n"
        f"\u52a0\u8f7d\u65b9\u5f0f\uff1a/persona load {item['id']}\n"
        "\u7ed3\u675f\u52a0\u8f7d\uff1a/persona stop"
    )
