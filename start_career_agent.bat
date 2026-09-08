@echo off
set "ROOT=%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%start_career_agent.ps1" %*
if errorlevel 1 (
  echo.
  echo CareerAgent 启动失败。可以在 PowerShell 中执行 start_career_agent.ps1 -ShowLogs 查看详细日志。
  pause
)
