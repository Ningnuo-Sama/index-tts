# 启动 IndexTTS 2.5 本地 HTTP API（FastAPI + uvicorn）
# 用法：pwsh -ExecutionPolicy Bypass -File tools\start_api.ps1 [-Port 7861] [-Public] [-QwenEmo] [-OllamaGpu|-FreeVram]
#
# 显存策略与 WebUI 一致：默认把 Ollama 向量模型切 CPU（服务常驻、检索变慢但可用），
# -FreeVram 移出显存（TTS 最快、检索首次多 0.32 秒），-OllamaGpu 完全不动（TTS 会慢）。
# API 服务与 WebUI 各占约 6.3 GB 显存，12 GB 卡上二选一。
#
# 端点速查（默认 127.0.0.1:7861）：
#   GET  /health                 模型状态、忙闲
#   GET  /v1/models              OpenAI 风格
#   POST /v1/audio/speech        OpenAI 兼容（voice 用预设名或路径）
#   POST /v1/audio/speech/file   multipart 上传参考音频
#   POST /tts                    全参数（情感参考/向量/文本、duration_factor）
param(
    [int]$Port = 7861,
    [switch]$Public,
    [switch]$QwenEmo,
    [switch]$OllamaGpu,
    [switch]$FreeVram
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$py = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) { throw "未找到虚拟环境：$py，先执行 uv sync --extra webui" }

$modeScript = Join-Path $PSScriptRoot 'ollama_mode.ps1'
if ($FreeVram) {
    & pwsh -NoProfile -ExecutionPolicy Bypass -File $modeScript -Mode offload
} elseif ($OllamaGpu) {
    Write-Host "提示：按 -OllamaGpu 保留 Ollama 在显存，TTS 会明显变慢（实测 12~380 秒/句）。"
    & pwsh -NoProfile -ExecutionPolicy Bypass -File $modeScript -Mode status
} else {
    & pwsh -NoProfile -ExecutionPolicy Bypass -File $modeScript -Mode cpu
}

if (Get-NetTCPConnection -LocalPort 7860 -State Listen -ErrorAction SilentlyContinue) {
    Write-Host "注意：7860 上的 WebUI 正在运行，两套模型各占 6.3 GB，显存会不够，建议先停掉 WebUI。"
}

$runArgs = @((Join-Path $PSScriptRoot 'serve_api.py'), '--model_dir', 'checkpoints', '--port', $Port,
    '--host', $(if ($Public) { '0.0.0.0' } else { '127.0.0.1' }))
if ($QwenEmo) { $runArgs += '--qwen_emo' }

$logDir = Join-Path $root 'scratch'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$logPath = Join-Path $logDir 'api.log'

Write-Host "IndexTTS 2.5 API 启动中 -> http://127.0.0.1:$Port/docs (日志：$logPath)"
& $py -u @runArgs *>> $logPath
