# 本机定制变更自述（2026-11-17）

这个 fork 是 IndexTTS 2.5 在 **Windows 11 + RTX 4070 12 GB** 上的本地部署，改动分三层：一处源码扩展、一套本地工具链、一份可核验基线。上游代码保持原样可用，不设本地定制分支前缀，改动都能线性回退。

## 1. 为什么需要改

不是功能不足，是**显存装不下**。本机常态显存占用已经吃掉一半：

| 占用方 | 显存 |
| --- | --- |
| `dwm`（桌面窗口管理器） | 约 2 GB |
| Ollama `qwen3-embedding-4b-16k`（本机向量库常驻，全天在线） | 3.9 GB |
| IndexTTS 2.5 自身 | 6.3 GB |
| 合计 | 12.2 GB > 12.28 GB 可用 |

满载时 Windows WDDM 把权重分页到共享内存，token 速率从 25 it/s 掉到 0.03 it/s——同一句 5 秒的音频，从 4 秒变成 **255~380 秒**。所以本地工作全部围绕“怎么让两边都活着”。

## 2. 源码改动（唯一一处，+15/−3）

`indextts/infer_v2_5.py`：让 w2v-bert 特征提取器（`hf_cache/w2v-bert-2.0`，约 2.2 GB）可以单独放在 CPU，其余模型照旧在 GPU。

- 触发方式：环境变量 `INDEXTTS_SEMANTIC_DEVICE=cpu`
- 不设变量时行为与上游完全一致（`self.semantic_device = self.device`）
- 改动点两处：`__init__` 里按变量决定加载设备；`get_emb()` 把输入搬到该设备、结果搬回主设备
- 收益：IndexTTS 显存 6.3 GB → **5.0 GB**

为什么选 w2v-bert：它只在参考音频上做一次特征提取（结果还有 `cache_spk_cond` 缓存），搬到 CPU 的代价是一次几百毫秒，不影响每 token 的生成速度。

## 3. 本地工具链（`tools/`，9 个脚本）

| 脚本 | 解决的问题 |
| --- | --- |
| `run_webui.py` | 启动器。本机系统 `NO_PROXY` 含 `[::1]`，httpx 0.28 解析会抛 `InvalidURL`，gradio 起不来；在 Python 进程内改 `NO_PROXY` 才生效（本机 pwsh 改 `$env:` 覆盖不到子进程）。同时负责直连、`--semantic-cpu` → 环境变量 |
| `start_webui.ps1` | 一键起 WebUI（7860），先按策略安排 Ollama 显存 |
| `start_api.ps1` + `serve_api.py` | 独立 FastAPI 服务（7861），OpenAI 兼容 `/v1/audio/speech`，给网关/其它 Agent 接 |
| `tts_client.py` | 轻量客户端，走 WebUI 的 gradio API 复用已加载模型，不再加载第二份 |
| `batch_infer.py` | 无头批量合成主路径，支持 `--preset` 直接吃 WebUI 配好的配方 |
| `script_to_voice.py` | 按稿子逐句改情绪/语速/停顿，分段生成后拼成一条完整音频 |
| `tts_session.ps1` | 临时出片会话：切显存 → 出片 → 停服务 → 还原日常状态，`finally` 保证异常也收摊 |
| `ollama_mode.ps1` | 切 Ollama 位置：`gpu` / `partial`(24 层) / `cpu` / `offload`，幂等，内置 runner 卡死自愈 |
| `free_vram.ps1` | 只把权重移出显存，服务与数据不动（下次请求 0.32 秒回载） |
| `baseline_check.ps1` | 只读输出当前部署的全部可核验事实（`-Json` 给机器读） |

## 4. 显存调度：三版迭代与最终形态

| 版本 | 做法 | 结果 |
| --- | --- | --- |
| V1 | 出片前卸载 Ollama（`keep_alive: 0`） | TTS 4.4~5.2 s/句，检索首次多 0.32 s；但“卸载”这个词让人担心向量库挂掉 |
| V2 | 把 Ollama 常驻 CPU（`num_gpu: 0`） | TTS 5~9 s，但向量检索从 0.16 s 掉到 **1.6~10 s**，不可接受 |
| **V3（采用）** | 双边各降一档：IndexTTS 的 w2v-bert 搬到 CPU（省 1.3 GB）、Ollama 只放 24 层到 GPU（省 1.2 GB） | TTS 3.5 s 往返、检索 1.0~2.6 s、总显存 9.5 GB，两边都活着 |

最终形态按“日常 → 出片”分时：

- **日常基线**：IndexTTS 完全不跑，Ollama 全量在 GPU（3.88 GB），显存常驻 5.5 GB，检索 0.12~0.16 s，7860/7861 不监听
- **临时出片**：`tts_session.ps1` 一条命令把 Ollama 收到 24 层 → 起服务 → 出片 → 收摊 → 拉回全量 GPU。实测单条 77 秒（无 GUI）/ 112 秒（走 WebUI），跑完自动还原

关键取舍：出片不用 WebUI。`batch_infer.py` 直接在进程内加载模型，省掉 gradio 初始化与 HTTP 往返的 35 秒，也不占端口。WebUI 只在要试音色、试参数时用。

## 5. 配方（预设）打通了界面与无头

WebUI 自带预设系统，存 `outputs/presets/<名字>/{preset.json, prompt.wav, emo_ref.wav}`——音频是复制进去的，原文件删了不影响。

`batch_infer.py --preset <名字>` 直接读同一份 JSON，字段对应关系：

| preset.json | 无头参数 |
| --- | --- |
| `emo_control_method` 0/1/2/3 | 决定走同音色 / 情感参考音频 / 8 维向量 / 描述文本 |
| `emo_weight` | `--emo-alpha` |
| `emo_vector` | `--emo-vector` |
| `advanced_params.*` | `--top-p / --top-k / --temperature / --num-beams / --repetition-penalty / --max-mel-tokens / --length-penalty / --max-tokens` |
| `prompt_audio` / `emo_audio` | 自动带上，不用再指路径 |

命令行显式参数优先于预设。预设里**不含语言和语速**，这两项每次单独给。

## 6. 情绪控制的探索结论

两条路，实测同一句“快躲起来！是他要来了！”：

| 设置 | 时长 | 基频 | 能量 RMS |
| --- | --- | --- | --- |
| 不加情感 | 4.42 s | 134.9 Hz | 0.036 |
| `use_emo_text`（模型自判，QwenEmotion） | 4.06 s | 176.6 Hz | 0.117 |
| 手动恐惧 `[惧0.9, 惊0.3]` | 3.75 s | 207.5 Hz | 0.121 |
| 手动平静 `[平静1.0]` | 5.11 s | 153.4 Hz | 0.029 |

结论：

- 模型自判会把戏剧性文本判成高唤醒情绪，方向和手动“恐惧”一致，但**粒度是整段一个情绪**
- 向量确实在起作用（时长差 36%、基频差 35%），但**高唤醒 + alpha 1.0 会过火**，听感容易滑稽；实用区间是 alpha 0.4~0.6，情绪只在关键时刻提
- 口播稿通常一句一个情绪，所以更需要 `script_to_voice.py` 的分段标注（`[严肃]`、`[惊讶]` 一行一段，或 JSONL 精确给向量）

## 7. 音色素材

本机已有的历史素材（GPT-SoVITS / RVC 训练用，2024 年初）：

- `D:\软件\RVCxunlianku\{FQ, Qixiansheng, WangYe, YuanYin}`，187 个切片 131.6 MB
- `G:\AI相关\GPT-SoVITS训练素材\`，1377 个音频 1074.9 MB，含 `SoVITS_weights`（FQ_e12_s384.pth 等 12 个权重）
- FQ 的 `asr_opt\slicer_opt.list` 带每段台词转写，可按语义挑片段

从中挑出 5 条适合零样本克隆的参考（3~15 秒、单人、干声），统一归一化到 -20 dBFS 后放 `voices/fq/`（该目录不进版本库）。原素材音量偏轻（RMS 0.03~0.06），不归一化会导致克隆发虚。实测 ref_02（5.3 s）克隆后基频 131 Hz，与参考 139 Hz 最接近。

## 8. 实测基线（RTX 4070 12 GB，bf16，无 cuda_kernel/accel/deepspeed）

| 场景 | 结果 |
| --- | --- |
| 日常（IndexTTS 不跑） | 检索 0.12~0.16 s，显存 5.5 GB |
| 出片无 GUI，单条 | 77 秒总时长（含切显存 5.8 s、加载约 60 s、还原 9.5 s） |
| 出片无 GUI，3 段 | 95.5 秒，服务端每段 4.9~9.1 s，RTF 0.93~1.36 |
| 出片走 WebUI，单条 | 112 秒（gradio 初始化多花约 35 s） |
| 情绪分段 4 段口播 | 57 秒出片，拼接 16.95 秒 |
| 冷启动首轮（含 CUDA 预热） | RTF 2.94 |

官方 4090 上 2.5 bf16 的 RTF 约 0.20；4070 单卡 0.84~1.36 属正常区间。

## 9. 已知限制

- 未装 `--accel`（flash-attn）、`--cuda_kernel`、`--deepspeed`：Windows 需 `triton-windows` + CUDA Toolkit 12.8 编译链；`--torch_compile` 未评估
- 未下 IndexTTS-2 / 1.5（`--version 2` 需另建 `checkpoints_2`），未测 vLLM 生产路径
- 未压测长文本与并发（服务内部串行）
- 未配开机自启/计划任务
- `ollama_mode partial` 的幂等判断按显存区间（2.2~3.4 GB）估算，不是精确层数校验
- 出片语速默认偏慢，正式配音前需要按稿子调 `duration_factor`
- 情感向量仍需人工试听确定，尚无自动选参

## 10. 复现

```powershell
git clone <this-fork> && cd index-tts
uv sync --extra webui                      # 本机 UV_DEFAULT_INDEX 指阿里云镜像
.\.venv\Scripts\modelscope.exe download --model IndexTeam/IndexTTS-2.5 --local_dir checkpoints
pwsh -File tools\baseline_check.ps1        # 核对环境与现状
pwsh -File tools\tts_session.ps1 -Text "验收一句话" -Out scratch\check.wav
```

配套文档：`docs/BASELINE_2026-11-17.md`（当前状态快照）、`LOCAL_MAINTENANCE.md`（日常操作手册与三个坑）。
