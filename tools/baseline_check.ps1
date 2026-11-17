# 基线快照：把 index-tts 部署的可核验事实一次性打出来（只读，不改任何东西）
# 用法：pwsh -ExecutionPolicy Bypass -File tools\baseline_check.ps1 [-Json]
param([switch]$Json)

$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$out = [ordered]@{}

# --- 版本 ---
$out.repo_path = $root
$out.git_head = (git rev-parse HEAD)
$out.git_head_short = (git rev-parse --short HEAD)
$out.git_commit_date = (git log -1 --format=%ad --date=iso)
$out.git_branch = (git rev-parse --abbrev-ref HEAD)
$out.git_dirty = @(git status --short)
$out.uv_lock_sha256 = (Get-FileHash uv.lock -Algorithm SHA256).Hash

# --- 本地改动 ---
$diff = git diff --stat -- indextts/
$out.local_source_patch = @($diff)

# --- 环境 ---
$py = Join-Path $root '.venv\Scripts\python.exe'
if (Test-Path $py) {
    $out.python = (& $py -c "import sys; print(sys.version.split()[0])")
    $out.torch = (& $py -c "import torch; print(torch.__version__, torch.version.cuda, torch.cuda.is_available())")
    $out.packages = @(& $py -c "import importlib.metadata as m; [print(f'{p}=={m.version(p)}') for p in ('gradio','gradio_client','transformers','fastapi','uvicorn','modelscope')]")
} else {
    $out.python = 'MISSING .venv'
}
$out.gpu = @(nvidia-smi --query-gpu=name,driver_version,memory.total,memory.used --format=csv,noheader)

# --- 权重 ---
$ck = Join-Path $root 'checkpoints'
if (Test-Path $ck) {
    $files = Get-ChildItem $ck -Recurse -File | Sort-Object Length -Descending
    $out.checkpoints_total_gb = [math]::Round(($files | Measure-Object Length -Sum).Sum / 1GB, 2)
    $out.checkpoints_top = @($files | Select-Object -First 8 | ForEach-Object {
            "{0}  {1} MB" -f $_.FullName.Replace("$ck\", ''), [math]::Round($_.Length / 1MB, 1)
        })
} else { $out.checkpoints_total_gb = 'MISSING' }

# --- 运行时状态 ---
$out.ollama = try {
    $ps = Invoke-WebRequest 'http://127.0.0.1:11434/api/ps' -TimeoutSec 6 -UseBasicParsing | Select-Object -ExpandProperty Content | ConvertFrom-Json
    if ($ps.models.Count -eq 0) { 'no resident model' }
    else { ($ps.models | ForEach-Object { "{0} vram={1:N2}GB" -f $_.name, ($_.size_vram / 1GB) }) -join '; ' }
} catch { 'unreachable' }
$out.webui_7860 = try { (Invoke-WebRequest 'http://127.0.0.1:7860/' -TimeoutSec 4 -UseBasicParsing).StatusCode } catch { 'down' }
$out.api_7861 = try { (Invoke-WebRequest 'http://127.0.0.1:7861/health' -TimeoutSec 4 -UseBasicParsing).StatusCode } catch { 'down' }

# --- 脚本清单 ---
$out.tools = @(Get-ChildItem $PSScriptRoot -File | Where-Object { $_.Name -match '^(start_|tts_|batch_|ollama_|free_|serve_|run_|baseline_|gpu_)' } |
    ForEach-Object { "$($_.Name)  $([math]::Round($_.Length/1KB,1)) KB" })

if ($Json) {
    $out | ConvertTo-Json -Depth 4
} else {
    Write-Host "=== index-tts 基线快照 ==="
    $out.GetEnumerator() | ForEach-Object {
        Write-Host ""
        Write-Host "[$($_.Key)]"
        $v = $_.Value
        if ($v -is [array]) { $v | ForEach-Object { Write-Host "  $_" } }
        else { Write-Host "  $v" }
    }
}
