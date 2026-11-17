# 切换 Ollama 常驻模型的位置：GPU 全量 / GPU 部分层 / CPU / 移出显存
#
# 用法：
#   pwsh -File tools\ollama_mode.ps1 -Mode status     # 看现在在哪
#   pwsh -File tools\ollama_mode.ps1 -Mode partial    # 只放 24 层到 GPU（默认，约 2.7 GB，检索 0.12 秒）
#   pwsh -File tools\ollama_mode.ps1 -Mode gpu        # 全量 GPU（约 3.9 GB，检索 0.12 秒，TTS 会被挤爆）
#   pwsh -File tools\ollama_mode.ps1 -Mode cpu        # 全 CPU（不占显存，检索 1.6~10 秒）
#   pwsh -File tools\ollama_mode.ps1 -Mode offload    # 移出显存（下次请求 0.3 秒自动回载）
#
# 说明：
#   - 只动算力/显存分配，Ollama 服务、端口、模型文件、向量库数据都不动。
#   - partial 是 4070 12 GB 上的两全解：IndexTTS 把 w2v-bert 放 CPU 后占约 5 GB，
#     Ollama 24 层占 2.74 GB，dwm 约 2 GB，总 9.5 GB，两边都跑全速。
#   - Ollama 的 runner 从 CPU 态往 GPU 重载时偶尔会卡住不返回；脚本会杀掉 runner 重试一次。
param(
    [ValidateSet('status', 'cpu', 'gpu', 'partial', 'offload')]
    [string]$Mode = 'status',
    [int]$Layers = 24,
    [string]$Model = '',
    [int]$KeepAlive = 3600
)

$ErrorActionPreference = 'Stop'
$base = 'http://127.0.0.1:11434'

function Get-Models {
    try {
        $ps = Invoke-WebRequest "$base/api/ps" -TimeoutSec 6 -UseBasicParsing | Select-Object -ExpandProperty Content | ConvertFrom-Json
        return $ps.models
    } catch {
        return $null
    }
}

$models = Get-Models
if ($null -eq $models) { Write-Host "Ollama 服务不可达（$base）"; exit 1 }

if (-not $Model) {
    if ($models -and $models.Count -gt 0) { $Model = $models[0].name }
    else { $Model = 'qwen3-embedding-4b-16k:latest' }
}

function Show-State {
    $ms = Get-Models
    if (-not $ms -or $ms.Count -eq 0) { Write-Host "常驻模型：无" }
    else {
        foreach ($m in $ms) {
            $gb = [math]::Round($m.size_vram / 1GB, 2)
            $where = if ($m.size_vram -gt 3.5GB) { "GPU 全量（$gb GB）" }
                     elseif ($m.size_vram -gt 0) { "GPU 部分层（$gb GB）" }
                     else { "CPU（显存 0）" }
            Write-Host ("常驻：{0}  ->  {1}  到期 {2}" -f $m.name, $where, $m.expires_at)
        }
    }
    $used = (nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits) -join ''
    Write-Host "GPU 显存占用：$used MiB"
}

if ($Mode -eq 'status') { Show-State; exit 0 }

# 触发重载；超时/失败时杀掉卡住的 runner 再试一次
function Invoke-Reload([hashtable]$payload) {
    $json = $payload | ConvertTo-Json -Depth 5
    try {
        Invoke-WebRequest "$base/api/embed" -Method Post -Body $json -ContentType 'application/json' -TimeoutSec 120 -UseBasicParsing | Out-Null
        return
    } catch {
        Write-Host "重载未在 120 秒内返回（runner 可能卡住），杀掉 runner 重试..."
        Get-Process llama-server -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 5
        Invoke-WebRequest "$base/api/embed" -Method Post -Body $json -ContentType 'application/json' -TimeoutSec 180 -UseBasicParsing | Out-Null
    }
}

$sw = [Diagnostics.Stopwatch]::StartNew()
$current = Get-Models
$vramNow = 0
if ($current -and $current.Count -gt 0) { $vramNow = $current[0].size_vram }

switch ($Mode) {
    'partial' {
        if ($vramNow -gt 2.2GB -and $vramNow -lt 3.4GB) { Write-Host "已是部分层模式（$([math]::Round($vramNow/1GB,2)) GB），跳过。"; break }
        Write-Host "把 $Model 的 $Layers 层放到 GPU（重载约 17 秒）..."
        Invoke-Reload @{ model = $Model; input = 'warmup'; keep_alive = $KeepAlive; options = @{ num_gpu = $Layers } }
    }
    'gpu' {
        if ($vramNow -gt 3.5GB) { Write-Host "已经在 GPU 全量，跳过。"; break }
        Write-Host "把 $Model 全量拉回 GPU..."
        Invoke-Reload @{ model = $Model; input = 'warmup'; keep_alive = $KeepAlive; options = @{ num_gpu = 999 } }
    }
    'cpu' {
        if ($current -and $current.Count -gt 0 -and $vramNow -eq 0) { Write-Host "已经是 CPU 模式，跳过。"; break }
        Write-Host "把 $Model 切到 CPU（重载约 24 秒）..."
        Invoke-Reload @{ model = $Model; input = 'warmup'; keep_alive = $KeepAlive; options = @{ num_gpu = 0 } }
    }
    'offload' {
        if (-not $current -or $current.Count -eq 0) { Write-Host "当前没有常驻模型，无需释放。"; break }
        Write-Host "把 $Model 移出显存（下次请求自动回载）..."
        Invoke-Reload @{ model = $Model; input = 'warmup'; keep_alive = 0 }
    }
}
$sw.Stop()
Write-Host ("完成，用时 {0:N1} 秒" -f $sw.Elapsed.TotalSeconds)
Start-Sleep -Seconds 1
Show-State
