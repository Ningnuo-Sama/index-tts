# 启动 IndexTTS 2.5 WebUI（本地部署入口）
# 用法：pwsh -ExecutionPolicy Bypass -File tools\start_webui.ps1 [-Port 7860] [-Public] [-QwenEmo] [-NoSemanticCpu] [-OllamaGpu|-OllamaCpu|-FreeVram] [-Proxy]
#
# 12 GB 卡默认配置（两边都跑全速，实测 2026-11-17）：
#   IndexTTS 把 w2v-bert 特征提取器放 CPU  -> 占 5.0 GB（原 6.3 GB）
#   Ollama 向量模型只放 24 层到 GPU        -> 占 2.74 GB，检索 0.12 秒
#   dwm 约 2 GB，总计 9.5 GB，留 2.8 GB 给推理峰值
#   结果：TTS 3.5~5.9 秒/句（RTF 0.84~0.97），向量检索 0.12 秒
#
# 其它显存策略：
#   -OllamaCpu    向量模型全放 CPU（不占显存，检索 1.6~10 秒）
#   -FreeVram     移出显存（TTS 最快，检索首次多 0.32 秒）
#   -OllamaGpu    全量放 GPU（检索 0.12 秒，但 TTS 被挤到 12~380 秒）
#   -NoSemanticCpu  不把 w2v-bert 放 CPU（显存充裕时才用）
param(
    [int]$Port = 7860,
    [switch]$Public,
    [switch]$QwenEmo,
    [switch]$NoSemanticCpu,
    [switch]$OllamaGpu,
    [switch]$OllamaCpu,
    [switch]$FreeVram,
    [switch]$Proxy
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$py = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) {
    throw "未找到虚拟环境：$py，先在项目根执行 uv sync --extra webui"
}

# 1) 显存分配
$modeScript = Join-Path $PSScriptRoot 'ollama_mode.ps1'
if ($FreeVram) {
    & pwsh -NoProfile -ExecutionPolicy Bypass -File $modeScript -Mode offload
} elseif ($OllamaGpu) {
    & pwsh -NoProfile -ExecutionPolicy Bypass -File $modeScript -Mode gpu
} elseif ($OllamaCpu) {
    & pwsh -NoProfile -ExecutionPolicy Bypass -File $modeScript -Mode cpu
} else {
    & pwsh -NoProfile -ExecutionPolicy Bypass -File $modeScript -Mode partial
}

# 2) 起 WebUI
$launcher = Join-Path $root 'tools\run_webui.py'
$runArgs = @($launcher, '--model_dir', 'checkpoints', '--version', '2.5', '--fp16',
    '--port', $Port, '--host', $(if ($Public) { '0.0.0.0' } else { '127.0.0.1' }))
if ($QwenEmo) { $runArgs += '--qwen_emo' }
if (-not $NoSemanticCpu) { $runArgs += '--semantic-cpu' }
if ($Proxy) { $runArgs += '--proxy' }

$logDir = Join-Path $root 'scratch'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$logPath = Join-Path $logDir 'webui.log'

Write-Host "IndexTTS 2.5 WebUI 启动中 -> http://127.0.0.1:$Port (日志：$logPath)"
# python -u：日志实时落盘，方便看每轮 gpt_gen/s2mel/bigvgan 计时
& $py -u @runArgs *>> $logPath
