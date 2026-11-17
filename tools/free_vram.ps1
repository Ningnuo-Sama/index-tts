# 释放显存：把 Ollama 常驻模型移出显存（只影响显存，不动服务/端口/数据）
# 用途：跑 IndexTTS / ComfyUI 等吃显存的推理时临时腾出约 4 GB。
#
# 影响范围（实测 2026-11-17）：
#   - Ollama 服务照常在 127.0.0.1:11434 监听，模型文件、向量库数据都不动
#   - 下一次向量请求会自动把模型加载回显存，冷加载实测 0.32 秒，请求正常返回 2560 维
#   - 也就是说向量库不会“挂”，只会让紧跟着的那一次检索多 0.3 秒
#
# 用法：pwsh -ExecutionPolicy Bypass -File tools\free_vram.ps1 [-Quiet]
param([switch]$Quiet)

try {
    $ps = Invoke-WebRequest 'http://127.0.0.1:11434/api/ps' -TimeoutSec 6 -UseBasicParsing | Select-Object -ExpandProperty Content | ConvertFrom-Json
} catch {
    if (-not $Quiet) { Write-Host 'Ollama 服务未运行，无需释放' }
    return
}

if (-not $ps.models) {
    if (-not $Quiet) { Write-Host 'Ollama 当前没有常驻模型' }
    return
}

foreach ($m in $ps.models) {
    $body = @{ model = $m.name; keep_alive = 0 } | ConvertTo-Json
    try {
        Invoke-WebRequest 'http://127.0.0.1:11434/api/generate' -Method Post -Body $body -ContentType 'application/json' -TimeoutSec 20 -UseBasicParsing | Out-Null
        if (-not $Quiet) { Write-Host ("已移出显存：{0}（释放 {1:N1} GB；下次向量请求自动加载回来，约 0.3 秒）" -f $m.name, ($m.size_vram / 1GB)) }
    } catch {
        if (-not $Quiet) { Write-Host "释放失败：$($m.name) -> $($_.Exception.Message)" }
    }
}
