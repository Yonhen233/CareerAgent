param(
    [switch]$ShowLogs,
    [int]$PreferredWebPort = 8000
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$runtimeDir = Join-Path $root "data\runtime"
$statePath = Join-Path $runtimeDir "career-agent-startup.json"
New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null

function Write-Step([string]$message) {
    Write-Host "[CareerAgent] $message" -ForegroundColor Cyan
}

function Test-ListeningPort([int]$port) {
    return [bool](Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue)
}

function Find-FreeWebPort([int]$preferred) {
    for ($port = $preferred; $port -le ($preferred + 20); $port++) {
        if (-not (Test-ListeningPort $port)) {
            return $port
        }
    }
    throw "没有找到可用的网页端口（已检查 $preferred-$($preferred + 20)）。"
}

function Find-Python {
    $venvPython = Join-Path $root ".venv\Scripts\python.exe"
    if (Test-Path $venvPython) {
        return $venvPython
    }
    $command = Get-Command python -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }
    throw "找不到 Python。请先安装 Python 3.12，或在项目目录创建 .venv。"
}

function Find-Redis {
    $command = Get-Command redis-server -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }
    $knownPaths = @(
        "C:\Program Files\Redis\redis-server.exe",
        "C:\Program Files\Memurai\memurai.exe"
    )
    foreach ($path in $knownPaths) {
        if (Test-Path $path) {
            return $path
        }
    }
    throw "找不到 redis-server。请先安装 Redis，并确保 redis-server 在 PATH 中。"
}

function Test-Redis([int]$port = 6379) {
    $cli = Get-Command redis-cli -ErrorAction SilentlyContinue
    if (-not $cli) {
        return $false
    }
    try {
        return ((& $cli.Source -h 127.0.0.1 -p $port ping 2>$null) -join "").Trim() -eq "PONG"
    }
    catch {
        return $false
    }
}

function Test-PythonImports([string]$python) {
    & $python -c "import fastapi, langgraph, sentence_transformers" 2>$null
    if ($LASTEXITCODE -eq 0) {
        return
    }

    $bundledSitePackages = Join-Path $env:USERPROFILE ".codex\python312\Lib\site-packages"
    if ((Test-Path $bundledSitePackages) -and (-not $env:PYTHONPATH)) {
        $env:PYTHONPATH = $bundledSitePackages
        & $python -c "import fastapi, langgraph, sentence_transformers" 2>$null
        if ($LASTEXITCODE -eq 0) {
            Write-Step "已发现并启用本机 Codex Python 依赖目录。"
            return
        }
    }
    throw "Python 依赖不完整。请执行：python -m pip install -r requirements.txt"
}

if (-not (Test-Path (Join-Path $root ".env"))) {
    throw "项目缺少 .env。请先复制 .env.example 为 .env，并填写 LLM_API_KEY。"
}

Set-Location $root
$python = Find-Python
Test-PythonImports $python
$webPort = Find-FreeWebPort $PreferredWebPort
$startedRedis = $false
$startedWorker = $false
$startedWeb = $false
$redisProcess = $null
$workerProcess = $null
$webProcess = $null
$windowStyle = if ($ShowLogs) { "Normal" } else { "Hidden" }

Write-Step "Python: $python"
Write-Step "网页端口: $webPort"

if (Test-Redis) {
    Write-Step "Redis 已运行，直接复用 127.0.0.1:6379。"
}
else {
    $redis = Find-Redis
    Write-Step "启动 Redis..."
    $redisProcess = Start-Process -FilePath $redis `
        -ArgumentList @("--bind", "127.0.0.1", "--port", "6379", "--dir", $runtimeDir, "--dbfilename", "careeragent.rdb") `
        -WorkingDirectory $root -PassThru -WindowStyle $windowStyle
    $startedRedis = $true
    $redisReady = $false
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        Start-Sleep -Milliseconds 250
        if (Test-Redis) {
            $redisReady = $true
            break
        }
    }
    if (-not $redisReady) {
        throw "Redis 启动失败，请使用 -ShowLogs 重新运行查看 Redis 日志。"
    }
}

$workerCommand = Join-Path $root "scripts\run_agent_worker.py"
Write-Step "启动后台 Agent Worker..."
$workerProcess = Start-Process -FilePath $python `
    -ArgumentList @($workerCommand) `
    -WorkingDirectory $root -PassThru -WindowStyle $windowStyle
$startedWorker = $true

Write-Step "启动网页服务..."
$webProcess = Start-Process -FilePath $python `
    -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "$webPort") `
    -WorkingDirectory $root -PassThru -WindowStyle $windowStyle
$startedWeb = $true

$url = "http://127.0.0.1:$webPort/"
$healthy = $false
for ($attempt = 0; $attempt -lt 40; $attempt++) {
    Start-Sleep -Milliseconds 500
    try {
        $health = Invoke-RestMethod -Uri "$url`health" -TimeoutSec 2
        if ($health.status -eq "ok") {
            $healthy = $true
            break
        }
    }
    catch {
        # Uvicorn may need a few seconds to import the application and load settings.
    }
}

if (-not $healthy) {
    throw "网页服务没有通过健康检查。请使用 -ShowLogs 重新运行，或检查端口和 .env 配置。"
}

$state = @{
    started_at = (Get-Date).ToString("o")
    web_url = $url
    web_pid = if ($startedWeb) { $webProcess.Id } else { $null }
    worker_pid = if ($startedWorker) { $workerProcess.Id } else { $null }
    redis_pid = if ($startedRedis) { $redisProcess.Id } else { $null }
    redis_reused = -not $startedRedis
}
$state | ConvertTo-Json | Set-Content -Path $statePath -Encoding UTF8

Write-Host ""
Write-Host "CareerAgent 已启动" -ForegroundColor Green
Write-Host "网页地址: $url" -ForegroundColor Green
Write-Host "健康检查: $url`health" -ForegroundColor Green
Write-Host "停止服务: .\stop_career_agent.ps1" -ForegroundColor Yellow
Start-Process $url
