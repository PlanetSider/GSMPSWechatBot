# -*- coding: utf-8 -*-
"""Command plugin for downloading one Bilibili BV video.

Usage: /bv <BVxxxxxxxxxx or a Bilibili URL containing it>
The queue is FIFO with a hard capacity of five jobs. Every downloaded file is
written below the bot workspace temp directory and deleted in finally.
"""

import os
import queue
import re
import threading
import time

from core.sender import send
from services import bilibili

COMMAND = "/bv"
ALIASES = ["/BV"]

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
TEMP_DIR = os.path.join(BASE_DIR, "temp", "bilibili")
QUEUE_LIMIT = 5
_BVID_RE = re.compile(r"(?i)(?<![A-Za-z0-9])BV[0-9A-Za-z]{10}(?![A-Za-z0-9])")
_CONFIG = {}
_QUEUE = queue.Queue(maxsize=QUEUE_LIMIT)
_WORKER = None
_WORKER_LOCK = threading.Lock()
_INFLIGHT = set()
_INFLIGHT_LOCK = threading.RLock()


def init(config):
    global _CONFIG, _WORKER
    _CONFIG = config if isinstance(config, dict) else {}
    bilibili.configure(_CONFIG)
    os.makedirs(TEMP_DIR, exist_ok=True)
    with _WORKER_LOCK:
        if _WORKER is None or not _WORKER.is_alive():
            _WORKER = threading.Thread(target=_worker_loop, daemon=True, name="bilibili-bv-worker")
            _WORKER.start()
    print("[PLUGIN] /bv Bilibili FIFO worker ready (capacity=5)", flush=True)


def _block():
    value = (_CONFIG or {}).get("bilibili")
    return value if isinstance(value, dict) else {}


def _extract_bvid(text):
    match = _BVID_RE.search(str(text or ""))
    if not match:
        return ""
    raw = match.group(0)
    return "BV" + raw[2:]


def _max_bytes():
    try:
        return int(float(_block().get("max_size_mb", 40) or 40) * 1024 * 1024)
    except (TypeError, ValueError):
        return 40 * 1024 * 1024


def _target(context):
    return str((context or {}).get("group") or (context or {}).get("user") or "").strip()


def _send_text(target, text):
    text = str(text or "").strip()
    if not text:
        return False
    ok, error = send(target=target, content=text, mode="wechat_text")
    if not ok:
        print(f"[BV] text send failed target={target} error={error}", flush=True)
    return bool(ok)


def _failure_text(error):
    return f"B站视频处理失败：{error}"


def handle(content, context):
    context = context if isinstance(context, dict) else {}
    text = str(content or "").strip()
    bvid = _extract_bvid(text)
    if not bvid:
        return "用法：/bv BVxxxxxxxxxx 或 /bv https://www.bilibili.com/video/BVxxxxxxxxxx/"

    target = _target(context)
    if not target:
        return "找不到发送目标"

    with _INFLIGHT_LOCK:
        if bvid in _INFLIGHT:
            return f"{bvid} 已在队列中或正在处理，请勿重复提交"
        # Capacity includes the currently running job plus queued jobs.
        if len(_INFLIGHT) >= QUEUE_LIMIT or _QUEUE.full():
            return "B站视频下载队列已满（上限 5），请稍后再试"
        _INFLIGHT.add(bvid)
        try:
            position = len(_INFLIGHT)
            _QUEUE.put_nowait((target, text, bvid))
        except queue.Full:
            _INFLIGHT.discard(bvid)
            return "B站视频下载队列已满（上限 5），请稍后再试"

    return f"已加入B站视频队列（第 {position}/5）：{bvid}"


def _worker_loop():
    while True:
        target, text, bvid = _QUEUE.get()
        try:
            _run_job(target, text, bvid)
        except Exception as exc:
            print(f"[BV] unexpected job error bvid={bvid}: {exc}", flush=True)
            _send_text(target, _failure_text(exc))
        finally:
            with _INFLIGHT_LOCK:
                _INFLIGHT.discard(bvid)
            _QUEUE.task_done()


def _run_job(target, text, bvid):
    os.makedirs(TEMP_DIR, exist_ok=True)
    path = os.path.join(TEMP_DIR, f"{bvid}_{int(time.time() * 1000)}.mp4")
    try:
        print(f"[BV] resolving bvid={bvid} target={target}", flush=True)
        plan = bilibili.resolve(text, max_bytes=_max_bytes())
        info = plan["info"]
        stream = plan["stream"]
        title = str(info.get("title") or bvid).strip()
        print(f"[BV] downloading bvid={bvid} title={title!r} path={path}", flush=True)
        bilibili.download(stream, path, max_bytes=_max_bytes())
        ok, error = send(target=target, file_path=path, mode="wechat_file")
        if not ok:
            print(f"[BV] video send failed bvid={bvid} error={error}", flush=True)
            _send_text(target, _failure_text(error))
            return
        _send_text(target, f"解析成功！{title}")
        print(f"[BV] sent bvid={bvid} title={title!r}", flush=True)
    except bilibili.BilibiliError as exc:
        print(f"[BV] failed bvid={bvid}: {exc}", flush=True)
        _send_text(target, _failure_text(exc))
    except Exception as exc:
        print(f"[BV] unexpected failure bvid={bvid}: {exc}", flush=True)
        _send_text(target, _failure_text(exc))
    finally:
        bilibili.delete_file(path)
        print(f"[BV] cleaned path={path}", flush=True)

WEBUI_CONFIG_SCHEMA = {
    "title": "B站媒体",
    "fields": [
        {"key": "enabled", "type": "boolean", "label": "启用"},
        {"key": "max_size_mb", "type": "number", "label": "最大文件大小 MB"},
        {"key": "max_duration_seconds", "type": "number", "label": "最大时长"},
        {"key": "prefer_quality", "type": "number", "label": "优先画质"},
        {"key": "fallback_quality", "type": "number", "label": "回退画质"},
        {"key": "timeout_seconds", "type": "number", "label": "超时秒数"},
        {"key": "queue_limit", "type": "number", "label": "队列上限"},
    ],
}
