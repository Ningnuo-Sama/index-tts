# 临时出片会话：切显存 → 出片 → 还原日常全量 GPU。**默认不起 WebUI**。
#
# 为什么要两条路径：
#   - 默认（无 GUI）：直接 batch_infer.py，进程内加载模型后出片，跑完退出。
#     实测 3 段 95.5 秒（切显存 5.8 s + 加载与生成 79 s + 还原 12 s），不起 gradio、不占端口。
#   - -ViaWebUI：起 Gradio WebUI + tts_client 调接口。单条 112 秒（多花约 25~30 秒在
#     gradio 初始化与 HTTP 往返上）。只有要在界面上试音色/试情感参数时才需要。
#
# 用法：
#   pwsh -File tools\tts_session.ps1 -Text "要合成的话" -Out out.wav
#   pwsh -File tools\tts_session.ps1 -Script 稿子.txt -OutDir outputs\job -Voice voice_01
#   pwsh -File tools\tts_session.ps1 -Text "..." -Out a.wav -ViaWebUI      # 需要界面时
#   pwsh -File tools\tts_session.ps1 -Text "..." -Out a.wav -NoRestore     # 不切回全量 GPU
param(
    [string]$Text = '',
    [string]$Script = '',
    [string]$Out = '',
    [string]$OutDir = '',
    [string]$Voice = '',
    [string]$Preset = '',
    [string]$EmoAudio = '',
    [double]$Speed = 0,
    [double]$DurationFactor = 0,
    [int]$MaxTokens = 0,
    [switch]$ViaWebUI,
    [switch]$KeepWebUI,
    [switch]$NoRestore
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$py = Join-Path $root '.venv\Scripts\python.exe'
$modeScript = Join-Path $PSScriptRoot 'ollama_mode.ps1'
$startScript = Join-Path $PSScriptRoot 'start_webui.ps1'

if (-not $Text -and -not $Script) { throw "给 -Text 或 -Script" }

function Test-WebUI {
    try { return (Invoke-WebRequest 'http://127.0.0.1:7860/' -TimeoutSec 4 -UseBasicParsing).StatusCode -eq 200 }
    catch { return $false }
}

$startedHere = $false
$prevMode = 'gpu'

# 0) 记住进来前 Ollama 在哪，收摊时按它还原
try {
    $ps = Invoke-WebRequest 'http://127.0.0.1:11434/api/ps' -TimeoutSec 6 -UseBasicParsing | Select-Object -ExpandProperty Content | ConvertFrom-Json
    $vram = 0
    if ($ps.models -and $ps.models.Count -gt 0) { $vram = $ps.models[0].size_vram }
    $prevMode = if ($vram -gt 3.5GB) { 'gpu' } elseif ($vram -gt 0) { 'partial' } else { 'cpu' }
} catch { $prevMode = 'gpu' }

try {
    # 1) 让位
    & pwsh -NoProfile -ExecutionPolicy Bypass -File $modeScript -Mode partial

    if ($ViaWebUI) {
        # ---- 路径 A：走 WebUI 接口（要界面/多次试听时用）
        if (-not (Test-WebUI)) {
            Write-Host "启动 WebUI（约 60 秒）..."
            Start-Process pwsh -ArgumentList '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $startScript -WindowStyle Hidden | Out-Null
            $startedHere = $true
            $deadline = (Get-Date).AddSeconds(300)
            while ((Get-Date) -lt $deadline) { if (Test-WebUI) { break }; Start-Sleep -Seconds 5 }
            if (-not (Test-WebUI)) { throw "WebUI 未在 300 秒内就绪，看 scratch\webui.log" }
        }
        $clientArgs = @((Join-Path $PSScriptRoot 'tts_client.py'))
        if ($Preset) { Write-Host "提示：-ViaWebUI 路径不读预设文件；需要在界面「预设」下拉里选，或改用默认无 GUI 路径。" }
        if ($Voice) { $clientArgs += @('--voice', $Voice) }
        if ($MaxTokens) { $clientArgs += @('--max-tokens', $MaxTokens) }
        if ($Text) { $clientArgs += @('--text', $Text) }
        if ($Script) { $clientArgs += @('--script', $Script) }
        if ($Out) { $clientArgs += @('--out', $Out) }
        if ($OutDir) { $clientArgs += @('--out-dir', $OutDir) }
        if ($EmoAudio) { $clientArgs += @('--emo-audio', $EmoAudio) }
        if ($Speed) { $clientArgs += @('--speed', $Speed) }
        if ($DurationFactor) { $clientArgs += @('--duration-factor', $DurationFactor) }
        & $py @clientArgs
    } else {
        # ---- 路径 B（默认）：直接 batch_infer，不起 WebUI / gradio
        $tmpScript = $null
        $scriptPath = $Script
        if ($Text) {
            $tmpScript = Join-Path $root ('scratch\_session_{0}.txt' -f ([guid]::NewGuid().ToString('N').Substring(0, 8)))
            New-Item -ItemType Directory -Force -Path (Split-Path $tmpScript) | Out-Null
            Set-Content -Path $tmpScript -Value $Text -Encoding utf8NoBOM
            $scriptPath = $tmpScript
        }
        # batch_infer 的 --out 是「输出目录」；单条指定 -Out 文件时先落临时目录再改名
        $batchOutDir = $OutDir
        if (-not $batchOutDir -and $Out) {
            $batchOutDir = Join-Path $root ('scratch\_session_out_{0}' -f ([guid]::NewGuid().ToString('N').Substring(0, 6)))
        }
        $batchArgs = @((Join-Path $PSScriptRoot 'batch_infer.py'), '--script', $scriptPath)
        if ($Voice) { $batchArgs += @('--voice', $Voice) }
        if ($Preset) { $batchArgs += @('--preset', $Preset) }
        if ($MaxTokens) { $batchArgs += @('--max-tokens', $MaxTokens) }
        if ($batchOutDir) { $batchArgs += @('--out', $batchOutDir) }
        if ($Speed) { $batchArgs += @('--duration-factor', [string]([math]::Round(1.0 / $Speed, 3))) }
        if ($DurationFactor) { $batchArgs += @('--duration-factor', [string]$DurationFactor) }
        if ($EmoAudio) { $batchArgs += @('--emo-audio', $EmoAudio) }
        & $py @batchArgs

        # 单条且指定了 -Out：把生成的 wav 改名到目标路径
        if ($Out -and -not $OutDir -and $batchOutDir) {
            $wav = Get-ChildItem $batchOutDir -Filter *.wav -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($wav) {
                $dest = if ([IO.Path]::IsPathRooted($Out)) { $Out } else { Join-Path $root $Out }
                New-Item -ItemType Directory -Force -Path (Split-Path $dest) | Out-Null
                Copy-Item $wav.FullName $dest -Force
                Write-Host "输出：$dest"
            }
            Remove-Item $batchOutDir -Recurse -Force -ErrorAction SilentlyContinue
        }
        if ($tmpScript) { Remove-Item $tmpScript -ErrorAction SilentlyContinue }
    }
} finally {
    if ($startedHere -and -not $KeepWebUI) {
        $p = (Get-NetTCPConnection -LocalPort 7860 -State Listen -ErrorAction SilentlyContinue)[0].OwningProcess
        if ($p) { Stop-Process -Id $p -Force -ErrorAction SilentlyContinue; Write-Host "已停 WebUI（pid=$p）" }
        Start-Sleep -Seconds 4
    }
    if (-not $NoRestore) {
        Write-Host "还原 Ollama 到出片前的状态（$prevMode）..."
        & pwsh -NoProfile -ExecutionPolicy Bypass -File $modeScript -Mode $prevMode
    }
}
