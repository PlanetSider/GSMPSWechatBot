"""
setup_wizard.py — 辅助脚本：启动 weflow-core 配置向导
在交互终端中运行，提取微信解密密钥
"""
import subprocess
import sys
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WEFLOW_DIR = os.path.join(BASE_DIR, "weflow-core")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

dist_js = os.path.join(WEFLOW_DIR, "dist", "index.js")
if not os.path.isfile(dist_js):
    print("[ERROR] weflow-core 未构建，请先运行: cd weflow-core && npm install && node esbuild.config.mjs")
    sys.exit(1)

print("=" * 50)
print("  WeFlow 配置向导")
print("  将提取微信解密密钥并写入 config.json")
print("=" * 50)
print()
print("请确保微信已登录！")
print()

# Use the same runtime as the Bot; preserve interactive stdin for onboarding.
node_exe = os.path.join(WEFLOW_DIR, "WeFlow.exe") if sys.platform == "win32" else "node"
if sys.platform == "win32" and not os.path.isfile(node_exe):
    print("[ERROR] 请先准备 weflow-core/WeFlow.exe，参见 README.MD")
    sys.exit(1)

result = subprocess.run([node_exe, dist_js, CONFIG_PATH], cwd=WEFLOW_DIR)
print("\n配置向导已关闭。")
sys.exit(result.returncode)
