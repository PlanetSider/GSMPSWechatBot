# -*- coding: utf-8 -*-
"""Persistent storage and group-local state for temporary Personas."""
import copy
import threading
from datetime import datetime

from utils.sqlite_store import load_document, save_document

_DOCUMENT = "data/personas.json"
_LOCK = threading.RLock()


def _blank():
    return {"next_id": 1, "personas": {}, "active": {}}


def _load():
    data = load_document(_DOCUMENT, _blank())
    if not isinstance(data, dict):
        data = _blank()
    data.setdefault("next_id", 1)
    data.setdefault("personas", {})
    data.setdefault("active", {})
    if not isinstance(data["personas"], dict):
        data["personas"] = {}
    if not isinstance(data["active"], dict):
        data["active"] = {}
    return data


def _group(group_id):
    return str(group_id or "").strip() or "__private__"


def _copy(value):
    return copy.deepcopy(value)


def create_persona(creator_wxid, creator_name, source, identity):
    with _LOCK:
        data = _load()
        persona_id = int(data.get("next_id") or 1)
        data["next_id"] = persona_id + 1
        item = {
            "id": persona_id,
            "creator_wxid": str(creator_wxid or "").strip(),
            "creator_name": str(creator_name or "").strip(),
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "source": str(source or "")[:4000],
            "identity": _copy(identity),
            "banned": False,
        }
        data["personas"][str(persona_id)] = item
        save_document(_DOCUMENT, data)
        return _copy(item)


def get_persona(persona_id, include_banned=False):
    try:
        key = str(int(str(persona_id).strip()))
    except (TypeError, ValueError):
        return None
    with _LOCK:
        item = _load()["personas"].get(key)
        if not isinstance(item, dict) or (item.get("banned") and not include_banned):
            return None
        return _copy(item)


def set_banned(persona_id, banned):
    try:
        key = str(int(str(persona_id).strip()))
    except (TypeError, ValueError):
        return None
    with _LOCK:
        data = _load()
        item = data["personas"].get(key)
        if not isinstance(item, dict):
            return None
        item["banned"] = bool(banned)
        if banned:
            data["active"] = {
                group: active for group, active in data["active"].items()
                if str(active.get("persona_id")) != key
            }
        save_document(_DOCUMENT, data)
        return _copy(item)


def delete_persona(persona_id):
    """Delete a Persona and remove any group state that references it."""
    try:
        key = str(int(str(persona_id).strip()))
    except (TypeError, ValueError):
        return None
    with _LOCK:
        data = _load()
        item = data["personas"].get(key)
        if not isinstance(item, dict):
            return None
        deleted = _copy(item)
        data["personas"].pop(key, None)
        data["active"] = {
            group: active
            for group, active in data["active"].items()
            if not isinstance(active, dict) or str(active.get("persona_id")) != key
        }
        save_document(_DOCUMENT, data)
        return deleted


def load_persona(group_id, persona_id, loaded_by_wxid):
    try:
        key = str(int(str(persona_id).strip()))
    except (TypeError, ValueError):
        return None
    with _LOCK:
        data = _load()
        item = data["personas"].get(key)
        if not isinstance(item, dict) or item.get("banned"):
            return None
        data["active"][_group(group_id)] = {
            "persona_id": int(key),
            # Charges during the active period belong to the command issuer
            # who loaded the Persona, not necessarily its original creator.
            "creator_wxid": str(loaded_by_wxid or item.get("creator_wxid", "")).strip(),
            "persona_creator_wxid": item.get("creator_wxid", ""),
            "loaded_by_wxid": str(loaded_by_wxid or "").strip(),
            "loaded_at": datetime.now().isoformat(timespec="seconds"),
            "spent_points": 0.0,
            "request_count": 0,
        }
        save_document(_DOCUMENT, data)
        result = _copy(item)
        result["active"] = _copy(data["active"][_group(group_id)])
        return result


def record_persona_usage(group_id, points):
    """Persist billable usage for the currently loaded Persona."""
    try:
        amount = max(0.0, float(points or 0.0))
    except (TypeError, ValueError):
        amount = 0.0
    if amount <= 0:
        return None
    with _LOCK:
        data = _load()
        active = data["active"].get(_group(group_id))
        if not isinstance(active, dict):
            return None
        active["spent_points"] = round(float(active.get("spent_points") or 0.0) + amount, 1)
        active["request_count"] = int(active.get("request_count") or 0) + 1
        save_document(_DOCUMENT, data)
        return _copy(active)


def stop_persona(group_id):
    with _LOCK:
        data = _load()
        active = data["active"].pop(_group(group_id), None)
        save_document(_DOCUMENT, data)
        return _copy(active) if active else None


def get_active_persona(group_id):
    with _LOCK:
        data = _load()
        active = data["active"].get(_group(group_id))
        if not isinstance(active, dict):
            return None
        persona = data["personas"].get(str(active.get("persona_id")))
        if not isinstance(persona, dict) or persona.get("banned"):
            return None
        result = _copy(persona)
        result["active"] = _copy(active)
        return result
