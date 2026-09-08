$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$statePath = Join-Path $root "data\runtime\career-agent-startup.json"

if (-not (Test-Path $statePath)) {
    Write-Host "没有找到启动记录，未停止任何进程。" -ForegroundColor Yellow
    exit 0
}

$state = Get-Content $statePath -Raw | ConvertFrom-Json
foreach ($property in @("web_pid", "worker_pid", "redis_pid")) {
    $value = [int]($state.$property)
    if ($value -le 0) {
        continue
    }
    $process = Get-Process -Id $value -ErrorAction SilentlyContinue
    if ($process) {
        Stop-Process -Id $value -Force
        Write-Host "已停止 $property (PID $value)" -ForegroundColor Cyan
    }
}

Remove-Item $statePath -Force -ErrorAction SilentlyContinue
Write-Host "CareerAgent 已停止。" -ForegroundColor Green
