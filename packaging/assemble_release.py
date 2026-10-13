"""Assemble the portable Windows release directory after PyInstaller.

Usage (from the repository root, after `pyinstaller packaging/app.spec`):

    python packaging/assemble_release.py                       # assemble only
    python packaging/assemble_release.py --zip                 # also produce a zip
    python packaging/assemble_release.py --name MySuffix       # custom folder name
    python packaging/assemble_release.py --full-resources      # keep non-Win64 native resources

Result: a self-contained portable ("green") distribution folder:

    <release>/<name>/
      GSMPSWechatBot.exe + Python runtime      (PyInstaller onedir output)
      weflow-core/                             (Node runtime: WeFlow.exe, dist/, resources/, node_modules subset)
      assets/  plugins/  auto/  data/          (tracked runtime resources & plugin sources)
      sample_config.json  setup_wizard.bat  start.bat (generated)
      README.MD  config_guide.md  admin_commands.txt
      LICENSE  LICENSES/  THIRD_PARTY_NOTICES.md

Tracked files are enumerated with `git ls-files`, so a dirty local working
tree cannot leak untracked private data (keys, audio corpus, music scripts)
into a distributable.  If git is unavailable the script falls back to a plain
directory copy of the current tree.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PYINSTALLER_APP = Path("dist") / "GSMPSWechatBot"

# Tracked runtime resources copied verbatim (git ls-files, fallback: full copy).
TRACKED_TREES = ["assets", "plugins", "auto", "data", "music-source"]

# Single files / dirs copied from the repository root into the release.
ROOT_DOCS = [
    "sample_config.json",
    "admin_commands.txt",
    "config_guide.md",
    "README.MD",
    "THIRD_PARTY_NOTICES.md",
    "LICENSE",
]
ROOT_DIRS = ["LICENSES"]
ROOT_BATS = ["setup_wizard.bat"]

# weflow-core pieces required at runtime (esbuild externals + native resources).
WEFLOW_SUBDIRS = ["dist", "resources"]
WEFLOW_NODE_MODULES = ["koffi", "fzstd", "ffmpeg-static"]
WEFLOW_EXE = "WeFlow.exe"
WEFLOW_DIST_REQUIRED = ["index.js", "wcdbWorker.js"]

# Non-Win64 resource subtrees dropped unless --full-resources is given.
PRUNED_RESOURCE_PARTS = ["key/linux", "key/macos", "wcdb/win32/arm64", "wedecrypt/win32/arm64"]

START_BAT = r"""@echo off
:: NOTE: keep this file ASCII-only. cmd.exe misparses UTF-8 Chinese inside
:: parenthesized blocks even with `chcp 65001`, silently swallowing lines.
chcp 65001 >nul
title GSMPS Wechat Bot (EXE)

echo ============================================
echo   GSMPS Wechat Bot - portable EXE edition
echo ============================================
echo.

cd /d "%~dp0"
echo [INFO] Running from: %CD%
echo.

:: First run: create config.json from the bundled template.
if not exist "config.json" (
    if exist "sample_config.json" (
        copy /y "sample_config.json" "config.json" >nul
        echo [SETUP] config.json has been generated from sample_config.json.
        echo [SETUP] Please edit config.json: weflow keys, target_group, LLM API.
        echo [SETUP] WeChat desktop client must be logged in before starting.
        echo [SETUP] Then run start.bat again. See config_guide.md for details.
        pause
        exit /b 0
    )
    echo [ERROR] config.json and sample_config.json were not found in:
    echo         %CD%
    echo.
    echo [HINT] start.bat must run from the FULL extracted folder containing
    echo [HINT] GSMPSWechatBot.exe, sample_config.json and weflow-core\.
    echo [HINT] 1. Do NOT double-click start.bat inside the ZIP viewer;
    echo [HINT]    extract the whole ZIP to a folder first.
    echo [HINT] 2. Do NOT move start.bat out of that folder.
    echo [HINT] 3. Make sure extraction completed (disk space / file count).
    echo [HINT] Chinese guide: see config_guide.md in this folder.
    pause
    exit /b 1
)

:: Ensure WeFlow.exe (a copy of node.exe) exists next to dist/index.js.
if not exist "weflow-core\WeFlow.exe" (
    echo [ERROR] weflow-core\WeFlow.exe not found. This build is incomplete.
    echo [HINT] Re-extract the whole ZIP; weflow-core\ must sit next to start.bat.
    pause
    exit /b 1
)

:loop
echo [INFO] Starting GSMPSWechatBot.exe ...
"GSMPSWechatBot.exe"
echo.
echo [WARN] Exited, restarting in 3 seconds... (close this window to stop)
timeout /t 3 >nul
goto loop
"""


def _git_ls_files(relpaths: list[str]) -> list[Path] | None:
    """Return tracked files under relpaths, or None when git is unusable."""
    try:
        out = subprocess.run(
            ["git", "-C", str(ROOT), "ls-files", "-z", "--", *relpaths],
            capture_output=True, check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    files = [ROOT / p for p in out.decode("utf-8", "replace").split("\0") if p]
    return [f for f in files if f.is_file()] or None


def _copy_tracked_trees(release: Path) -> list[str]:
    copied = []
    for tree in TRACKED_TREES:
        src_root = ROOT / tree
        if not src_root.is_dir():
            continue
        files = _git_ls_files([tree])
        if files is None:
            # Fallback: copy the working tree as-is (CI checkouts are clean).
            shutil.copytree(
                src_root, release / tree, dirs_exist_ok=True,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
        else:
            for f in files:
                dest = release / f.relative_to(ROOT)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, dest)
        copied.append(tree)
    return copied


def _copy_tree(src: Path, dest: Path, prune: list[str] | None = None) -> None:
    if not src.is_dir():
        raise FileNotFoundError(f"missing required directory: {src}")
    shutil.copytree(
        src, dest, dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc") if prune is None else None,
    )
    for rel in prune or []:
        target = dest / rel
        if target.exists():
            shutil.rmtree(target)


def _dir_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", default=str(ROOT / PYINSTALLER_APP),
                        help="PyInstaller onedir output directory")
    parser.add_argument("--release-root", default=str(ROOT / "release"),
                        help="parent directory for the assembled release")
    parser.add_argument("--name", default="GSMPSWechatBot-Win64",
                        help="release folder (and zip) name")
    parser.add_argument("--zip", action="store_true", help="also produce <name>.zip")
    parser.add_argument("--full-resources", action="store_true",
                        help="keep non-Win64 native resources (larger archive)")
    args = parser.parse_args()

    dist_app = Path(args.dist).resolve()
    exe = dist_app / "GSMPSWechatBot.exe"
    if not exe.is_file():
        print(f"[ERROR] {exe} not found. Run `pyinstaller packaging/app.spec` first.")
        return 1

    weflow = ROOT / "weflow-core"
    for req in [weflow / WEFLOW_EXE, *(weflow / "dist" / f for f in WEFLOW_DIST_REQUIRED)]:
        if not req.is_file():
            print(f"[ERROR] {req} not found. Build weflow-core first "
                  f"(npm ci && node esbuild.config.mjs && copy node.exe -> WeFlow.exe).")
            return 1
    for pkg in WEFLOW_NODE_MODULES:
        if not (weflow / "node_modules" / pkg).is_dir():
            print(f"[ERROR] weflow-core/node_modules/{pkg} missing; run npm ci first.")
            return 1

    release = Path(args.release_root).resolve() / args.name
    if release.exists():
        # Sandboxed environments may terminate the process outright when a
        # bulk deletion (hundreds of files) is attempted, so never rmtree a
        # previous release here. Instead rename it aside (a single metadata
        # operation) and assemble into a fresh directory; stale "-old-*"
        # directories are left for the user/CI to clean.
        import time

        stamp = time.strftime("%Y%m%d-%H%M%S")
        aside = release.with_name(f"{release.name}-old-{stamp}")
        try:
            print(f"[INFO] previous release found; moving it aside: {aside}")
            release.rename(aside)
        except OSError:
            # Cannot move it aside either (e.g. a file inside is locked):
            # fall back to assembling over it in place.
            print(f"[WARN] could not move previous directory aside; "
                  f"assembling over {release} in place")
    release.mkdir(parents=True, exist_ok=True)

    # 1) PyInstaller output (exe + Python runtime) at the release root.
    shutil.copytree(dist_app, release, dirs_exist_ok=True)

    # 2) weflow-core Node runtime side.
    wdest = release / "weflow-core"
    wdest.mkdir()
    shutil.copy2(weflow / WEFLOW_EXE, wdest / WEFLOW_EXE)
    for sub in WEFLOW_SUBDIRS:
        _copy_tree(weflow / sub, wdest / sub,
                   prune=None if args.full_resources else PRUNED_RESOURCE_PARTS)
    nm = wdest / "node_modules"
    nm.mkdir()
    for pkg in WEFLOW_NODE_MODULES:
        _copy_tree(weflow / "node_modules" / pkg, nm / pkg)

    # 3) Tracked runtime resources (assets/plugins/auto/data/music-source).
    _copy_tracked_trees(release)

    # 4) Docs, licenses, template and wizard entry.
    for name in ROOT_DOCS:
        if (ROOT / name).is_file():
            shutil.copy2(ROOT / name, release / name)
    for name in ROOT_DIRS:
        if (ROOT / name).is_dir():
            shutil.copytree(ROOT / name, release / name / name if False else release / name)
    for name in ROOT_BATS:
        if (ROOT / name).is_file():
            shutil.copy2(ROOT / name, release / name)

    # 5) Release start script.
    (release / "start.bat").write_text(START_BAT, encoding="utf-8", newline="\r\n")

    size_mb = _dir_size(release) / (1024 * 1024)
    print(f"[OK] assembled: {release}  ({size_mb:.1f} MB)")
    if args.zip:
        # NOTE: str concat, not Path.with_suffix — release names may contain
        # dots (e.g. "GSMPSWechatBot-Win64-v0.1") and with_suffix would eat
        # the ".1" as an extension.
        zip_base = release.parent / args.name
        zip_path = Path(str(zip_base) + ".zip")
        shutil.make_archive(str(zip_base), "zip", root_dir=release.parent, base_dir=args.name)
        print(f"[OK] zip: {zip_path}  ({zip_path.stat().st_size / (1024*1024):.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
