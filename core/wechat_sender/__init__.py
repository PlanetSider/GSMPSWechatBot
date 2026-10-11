"""Persistent lifecycle wrapper for the WeChat sending HTTP service.

The wxSend endpoint is started once during bot startup and remains available for
all subsequent sends.  Individual send requests only reuse the already-running
listener; they never create or destroy an HTTP server.
"""

from __future__ import annotations

import atexit
import os
import sys
import threading
from typing import Any, Dict, Optional

# Keep the package importable when this module is launched from a subdirectory.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_server_lock = threading.RLock()
_startup_lock = threading.Lock()
_server_started = False
_server_thread: Optional[threading.Thread] = None


def start_server(
    host: str = "127.0.0.1",
    port: int = 9999,
    config: Optional[Dict[str, Any]] = None,
    startup_timeout: float = 5.0,
) -> bool:
    """Start the wxSend listener once and wait until its socket is bound.

    This function is idempotent and startup-serialized. A repeated or
    concurrent call while the existing listener is alive reuses it and does
    not attempt a second bind.
    """
    global _server_started, _server_thread

    # Keep the bind/startup sequence atomic. This matters during accidental
    # double initialization (for example, a launcher retrying main.py).
    with _startup_lock:
        with _server_lock:
            if _server_thread is not None and _server_thread.is_alive() and _server_started:
                return True

            # A prior startup may have failed after creating its thread. Ask
            # that thread to exit before retrying the same port.
            stale_thread = _server_thread if _server_thread and _server_thread.is_alive() else None
            _server_started = False

        if stale_thread is not None:
            try:
                from core.wechat_sender.wechat_hook_server import shutdown_server
                shutdown_server()
            except Exception:
                pass
            if stale_thread is not threading.current_thread():
                stale_thread.join(timeout=2.0)

        ready_event = threading.Event()
        startup_state: Dict[str, Any] = {}

        try:
            from core.wechat_sender.wechat_hook_server import run_server

            thread = threading.Thread(
                target=run_server,
                args=(host, port, config),
                kwargs={"ready_event": ready_event, "startup_state": startup_state},
                daemon=True,
                name="wechat-sender-server",
            )
            with _server_lock:
                _server_thread = thread
            thread.start()
        except Exception as exc:
            with _server_lock:
                _server_thread = None
            print(f"[sender] failed to start persistent HTTP service: {exc}", flush=True)
            return False

        if not ready_event.wait(max(0.1, float(startup_timeout))):
            print("[sender] persistent HTTP service startup timed out", flush=True)
            return False

        with _server_lock:
            if startup_state.get("error"):
                _server_started = False
                print(f"[sender] persistent HTTP service startup failed: {startup_state['error']}", flush=True)
                return False
            _server_started = True
            print(f"[sender] persistent HTTP service listening: http://{host}:{port}", flush=True)
            return True


def stop_server() -> None:
    """Stop the listener during orderly bot shutdown.

    Normal message sending never calls this function.  It exists only for
    process shutdown/tests and closes the actual server instead of merely
    flipping a boolean.
    """
    global _server_started, _server_thread

    with _server_lock:
        thread = _server_thread
        _server_started = False
        if thread is None:
            return
        try:
            from core.wechat_sender.wechat_hook_server import shutdown_server
            shutdown_server()
        except Exception as exc:
            print(f"[sender] failed to request HTTP service shutdown: {exc}", flush=True)

    if thread is not threading.current_thread() and thread.is_alive():
        thread.join(timeout=5.0)

    with _server_lock:
        if _server_thread is thread and not thread.is_alive():
            _server_thread = None


def is_server_running() -> bool:
    """Return whether the persistent listener thread is currently alive."""
    with _server_lock:
        return bool(_server_started and _server_thread and _server_thread.is_alive())

# Ensure an orderly process exit closes the actual listening socket.
# Message sends never invoke stop_server().
atexit.register(stop_server)
