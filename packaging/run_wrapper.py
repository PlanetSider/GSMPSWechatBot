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
4. When launched by double-click (parent process = explorer.exe), hold the
   console window open after exit so startup hints and errors stay visible.

The spec builds with contents_directory='.', which restores the flat
PyInstaller-5 onedir layout: sys._MEIPASS == the exe directory. The project
resolves every data path from module __file__ (pinned by PyInstaller under
_MEIPASS), so BASE_DIR == exe directory holds without any source change.
"""

import os
import sys


def _launched_from_explorer() -> bool:
    """True when the exe was started by double-click (parent = explorer.exe).

    A double-clicked console process closes its window the moment it exits,
    which makes startup hints and errors invisible ("window flashes and
    disappears"). In that case we hold the window open below.
    """
    try:
        import psutil

        parent = psutil.Process().parent()
        return parent is not None and parent.name().lower() == "explorer.exe"
    except Exception:
        return False


def _hold_window(reason: str) -> None:
    print(f"\n{reason}", flush=True)
    try:
        input("按 Enter 键关闭窗口 / Press Enter to close this window...")
    except EOFError:
        pass


def _selfheal_config(exe_dir: str) -> None:
    """Recover from a WeFlow setup-wizard clobber.

    The wizard rewrites config.json with only the `weflow` section when the
    file is missing or unparseable at wizard start, silently dropping
    target_group / llm / group_features. Detect that signature and rebuild
    the full config from a healthy donor (previous backup, else the bundled
    sample), grafting the wizard-written weflow values back in.
    """
    import json
    import shutil

    cfg_path = os.path.join(exe_dir, "config.json")
    try:
        with open(cfg_path, encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        cfg = None

    if isinstance(cfg, dict) and "target_group" in cfg:
        # Healthy config: keep a rolling backup for future recovery.
        try:
            shutil.copy2(cfg_path, os.path.join(exe_dir, "config.json.autobak"))
        except OSError:
            pass
        return

    donor = None
    for name in ("config.json.autobak", "sample_config.json"):
        cand = os.path.join(exe_dir, name)
        try:
            with open(cand, encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict) and "target_group" in d:
                donor = (name, d)
                break
        except Exception:
            continue
    if donor is None:
        return  # nothing to merge from; main.py prints its normal hint

    src_name, base = donor
    merged = json.loads(json.dumps(base))  # deep copy
    if isinstance(cfg, dict):
        wf = cfg.get("weflow") or {}
        merged.setdefault("weflow", {})
        for key in ("dbPath", "decryptKey", "myWxid"):
            if wf.get(key):
                merged["weflow"][key] = wf[key]
        token = cfg.get("token")
        if token and not str(token).startswith("your-"):
            merged["token"] = token

    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)

    print("[SETUP] 检测到 config.json 缺少 target_group（通常是配置向导在完整配置"
          "生成前运行所致），已自动恢复完整结构并保留已提取的微信密钥。", flush=True)
    print("[SETUP] 请编辑 config.json 中的 target_group 与 llm 配置后重新运行"
          " start.bat。", flush=True)


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

    _selfheal_config(exe_dir)

    import runpy

    hold = _launched_from_explorer()
    try:
        # Execute the frozen "main" module as __main__ (same as `python main.py`).
        runpy.run_module("main", run_name="__main__", alter_sys=True)
    except SystemExit as exc:
        if hold:
            _hold_window(f"[EXIT] 程序已退出（退出码 {exc.code}）。"
                         f"如为首次使用，请按上方提示完成 config.json 配置后重新运行。")
        raise
    except BaseException:
        if hold:
            import traceback

            traceback.print_exc()
            _hold_window("[ERROR] 程序异常退出，上方为错误详情。")
        raise


if __name__ == "__main__":
    _bootstrap()
