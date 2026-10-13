"""Frozen entry point for the PyInstaller onedir build (GSMPSWechatBot.exe).

This wrapper is packaging infrastructure only — the project source stays
untouched. Responsibilities:

1. Pin the process working directory to the directory that contains
   GSMPSWechatBot.exe, so every CWD-relative convention (config.json,
   data/, music-source/, plugins/, weflow-core/) behaves exactly like the
   `cd /d %~dp0` in start.bat, regardless of how the exe was launched
   (double click, shortcut with a custom "Start in", Run as administrator).
2. Make the application directory importable, so plugins dropped into
   plugins/ or auto/ by the user can still be loaded dynamically at runtime.
3. Execute the frozen "main" module (main.py) exactly like `python main.py`,
   including its exit codes and signal handling.

The spec builds with contents_directory='.', which restores the flat
PyInstaller-5 onedir layout: sys._MEIPASS == the exe directory. The project
resolves every data path from module __file__ (pinned by PyInstaller under
_MEIPASS), so BASE_DIR == exe directory holds without any source change.
"""

import os
import sys


def _bootstrap() -> None:
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    os.chdir(exe_dir)
    if exe_dir not in sys.path:
        sys.path.insert(0, exe_dir)

    # When stdout/stderr are redirected (pipes, log files), CPython falls back
    # to the ANSI code page (GBK on zh-CN Windows) and the bot's UTF-8 console
    # output would raise UnicodeEncodeError. Force UTF-8 so behaviour matches
    # the `chcp 65001` console sessions the project's start.bat relies on.
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass

    import runpy

    # Execute the frozen "main" module as __main__ (same as `python main.py`).
    # SystemExit raised by main.py propagates and becomes the process exit code.
    runpy.run_module("main", run_name="__main__", alter_sys=True)


if __name__ == "__main__":
    _bootstrap()
