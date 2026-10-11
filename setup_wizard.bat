@echo off
chcp 65001 >nul
title WeFlow 配置向导
cd /d "%~dp0weflow-core"
echo ============================================
echo   WeFlow 配置向导 - 提取微信解密密钥
echo ============================================
echo.
echo 请确保微信已登录！
echo.
if not exist "WeFlow.exe" (
    echo [ERROR] 请先构建 weflow-core 并复制 x64 node.exe 为 WeFlow.exe，参见 README.MD。
    pause
    exit /b 1
)
WeFlow.exe dist/index.js ../config.json
echo.
echo ============================================
echo 配置完成！按任意键关闭...
pause >nul
