@echo off
chcp 65001 >nul
title MyBuddy 导师自动演示
set "DEMO_LOG=%~dp0mentor_demo-launch.log"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0mentor_demo.ps1" >"%DEMO_LOG%" 2>&1
set "DEMO_EXIT=%ERRORLEVEL%"
powershell.exe -NoProfile -Command "Get-Content -LiteralPath '%~dp0mentor_demo-launch.log'"
echo.
if not "%DEMO_EXIT%"=="0" (
    echo 自动演示未通过，请保留上方 FAIL 与证据目录。
) else (
    echo 自动演示全部通过。
)
echo 按任意键关闭本窗口。
pause >nul
exit /b %DEMO_EXIT%
