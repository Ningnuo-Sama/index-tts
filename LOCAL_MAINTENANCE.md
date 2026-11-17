# index-tts 本地部署维护说明

## 定位

B 站 IndexTTS 官方仓库（`https://github.com/index-tts/index-tts`）本地部署，用于零样本音色克隆、情感控制与语速控制的语音合成。当前跑的是仓库 `main` 分支的 **IndexTTS-2.5**（0.8B，多语言：中/英/日/西/阿）。

## 路径与版本

| 项目 | 值 |
| --- | --- |
| 源码 | `D:\GitHub\index-tts`（官方 upstream `origin`，未建本地定制分支） |
| 依赖环境 | 项目内 `.venv`（`uv sync --extra webui`，Python 3.11.13） |
| 权重 | `D:\GitHub\index-tts\checkpoints`（ModelScope `IndexTeam/IndexTTS-2.5`，约 5.6 GB） |
| 运行日志 | `scratch\webui.log` |
| 输出音频 | `outputs\`（WebUI）／`scratch\*.wav`（自测） |
| 当前版本 | 提交 `d9e41aa`（2026-09-30，fix: 末尾加淡出） |

权重文件：`gpt.pth`(3.0G)、`qwen0.6bemo4-merge/model.safetensors`(1.1G)、`codec.pth`(566M)、`s2mel.pth`(386M)、`config.yaml`、`pinyin.vocab`、`multilingual_zh_ja_yue_char_del.tiktoken`。

## 启动与停止

```powershell
# 默认：127.0.0.1:7860，bf16 半精度，不加载 QwenEmotion，**不动 Ollama**
# （向量库的 qwen3-embedding-4b-16k 保持常驻，代价是生成偏慢，见性能表）
pwsh -ExecutionPolicy Bypass -File D:\GitHub\index-tts\tools\start_webui.ps1

# 需要快速出片/批量时：显式把 Ollama 权重移出显存（服务与向量库数据不动）
... -FreeVram

# 需要“情感描述文本控制”时（额外约 1.2 GB 显存）
... -QwenEmo

# 局域网/其它机器访问
... -Public
```

停止：结束监听 7860 的进程（`Get-NetTCPConnection -LocalPort 7860` 取 `OwningProcess` 后 `Stop-Process`）。

命令行合成（不经 WebUI）：

```powershell
cd D:\GitHub\index-tts
$env:PYTHONPATH='.'
.\.venv\Scripts\python.exe -c "from indextts.infer_v2_5 import IndexTTS2; t=IndexTTS2(cfg_path='checkpoints/config.yaml', model_dir='checkpoints', use_bf16=True); t.infer(spk_audio_prompt='examples/voice_01.wav', text='测试文本', lang='ZH', output_path='scratch/gen.wav', verbose=True)"
```

批量台词合成（一行一段，抖音口播稿直接用这个）：

```powershell
cd D:\GitHub\index-tts
.\.venv\Scripts\python.exe tools\batch_infer.py `
  --script 稿子.txt --voice examples\voice_01.wav --out outputs\myjob
# 可选：--lang ZH|EN|JA|ES|AR  --emo-audio 情感参考.wav  --emo-alpha 0.9
#       --duration-factor 1.2（>1 变慢，<1 变快，0.5~2.0）  --max-tokens 120
```

实测：3 段口播 73.8 s（含加载模型约 60 s），每段 4~8 s，RTF 0.77。单段失败不中断整批。

## 三种 API 用法

### 1. WebUI 自带 gradio API（最省事，不用另起服务）

打开 `http://127.0.0.1:7860/?view=api` 看参数清单。端点 `/gen_single` 需要 26 个位置参数，且**参考音频必须先上传**（不能直接给本地路径，gradio 会拦 `InvalidPathError`）。curl 三步（已实测跑通，返回 5.86 秒 wav）：

```bash
# 1) 上传参考音频，拿到服务器缓存路径
curl -s -X POST http://127.0.0.1:7860/gradio_api/upload -F 'files=@examples/voice_01.wav'
# => ["C:\\Users\\...\\Temp\\gradio\\<hash>\\voice_01.wav"]

# 2) 提交任务，拿 event_id（prompt 用上一步返回的 path）
curl -s -X POST http://127.0.0.1:7860/gradio_api/call/gen_single \
  -H 'Content-Type: application/json' \
  -d '{"data":["与音色参考音频相同",{"path":"<上一步路径>","orig_name":"voice_01.wav","meta":{"_type":"gradio.FileData"}},"要合成的话","ZH",null,0.65,0,0,0,0,0,0,0,0,"",false,120,1.0,true,0.8,30,0.8,0,3,10,1500]}'
# => {"event_id":"..."}

# 3) 读事件流（SSE），complete 那一行里有音频路径和下载 URL
curl -s http://127.0.0.1:7860/gradio_api/call/gen_single/<event_id>
# => event: complete / data: [{"value":{"url":"http://127.0.0.1:7860/gradio_api/file=..."}}]
```

参数顺序：`情感模式, 参考音频, 文本, 语言, 情感参考音频, 情感强度, 8个情感向量, 情感描述, 随机, 分段token, 时长系数, 采样7项`。情感模式取 `与音色参考音频相同` / `使用情感参考音频` / `使用情感向量控制` / `使用情感描述文本控制`。

### 2. 轻量客户端（推荐脚本调用）

`tools\tts_client.py` 封装了上面三步，复用已运行的 WebUI，**不再加载第二份模型**：

```powershell
.\.venv\Scripts\python.exe tools\tts_client.py --text "要合成的话" --voice voice_01 --out out.wav
.\.venv\Scripts\python.exe tools\tts_client.py --script 稿子.txt --out-dir outputs\job --voice examples\voice_01.wav --speed 1.1
```

### 3. 独立 FastAPI 服务（OpenAI 兼容，给网关/其它 Agent 接）

```powershell
pwsh -ExecutionPolicy Bypass -File tools\start_api.ps1     # 默认 127.0.0.1:7861
```

| 端点 | 说明 |
| --- | --- |
| `GET /health` | 模型状态、忙闲、显存 |
| `GET /v1/models` | OpenAI 风格模型列表 |
| `POST /v1/audio/speech` | `{model,input,voice,response_format,speed}`，voice 用预设名或路径 |
| `POST /v1/audio/speech/file` | multipart 上传参考音频（`voice_file`） |
| `POST /tts` | 全参数：`emo_audio` / `emo_vector` / `emo_text` / `duration_factor` / `max_tokens` |

只输出 wav（本机没装 ffmpeg）。7861 与 7860 二选一：两套各占 6.3 GB 显存。

## 日常基线与临时出片（2026-11-17 定版）

**日常基线（冻结）**：IndexTTS 完全不跑，`qwen3-embedding-4b-16k:latest` 全量在 GPU（3.88 GB），显存常驻 5.4~5.5 GB，向量检索 0.12~0.16 秒。7860/7861 都不监听。

**要出片时一条命令，跑完自动还原基线。默认不起 WebUI**：

```powershell
cd D:\GitHub\index-tts
pwsh -File tools\tts_session.ps1 -Text "要合成的话" -Out out.wav
pwsh -File tools\tts_session.ps1 -Script 稿子.txt -OutDir outputs\job -Voice voice_01
pwsh -File tools\tts_session.ps1 -Text "要试音色" -Out a.wav -ViaWebUI   # 确实要界面时
```

| 路径 | 做法 | 单条耗时 | 说明 |
| --- | --- | --- | --- |
| **默认（无 GUI）** | 直接跑 `batch_infer.py`，进程内加载模型后出片退出 | **77 秒** | 不起 gradio、不占端口，实测产物 6.66 秒 wav |
| `-ViaWebUI` | 起 Gradio WebUI + `tts_client.py` 调接口 | 112 秒 | 多花约 35 秒在 gradio 初始化与 HTTP 往返；只在要界面试音色/情感时需要 |

两条路径都会：把 Ollama 收到 24 层 → 出片 → 把 Ollama 拉回全量 GPU（`finally` 块保证异常也收摊）。`-KeepWebUI` 保留服务、`-NoRestore` 不还原。

出片期间显存分配（12 GB 装不下 dwm 2 GB + Ollama 全量 3.9 GB + IndexTTS 6.3 GB，所以两边各砍一刀）：

- IndexTTS：`INDEXTTS_SEMANTIC_DEVICE=cpu` 把 w2v-bert 特征提取器放 CPU → 6.3 GB 降到 **5.0 GB**（`infer_v2_5.py` 两处小改动，不设变量时行为不变）
- Ollama：只放 **24 层** 到 GPU → **2.74 GB**
- 合计 9.49 GB，留 2.8 GB 给推理峰值

| 出片配置 | 启动方式 | TTS 一句 | 向量检索 | 显存 |
| --- | --- | --- | --- | --- |
| **出片会话（24 层 + w2v 在 CPU）** | `tts_session.ps1` | 3.5 秒往返（RTF 1.04~1.11） | 1.0~2.6 秒 | 9.5 GB |
| 全量 GPU | `start_webui.ps1 -OllamaGpu` | 12~380 秒 | 0.12 秒 | 11.6 GB |
| 全 CPU | `-OllamaCpu` | 5~9 秒 | 1.6~10 秒 | 7.5 GB |
| 移出显存 | `-FreeVram` | 4.4~5.2 秒 | 首次 +0.32 秒 | 7.5 GB |
| 不砍 w2v-bert | 加 `-NoSemanticCpu` | 视显存而定 | 同左 | +1.3 GB |

手动切 Ollama 位置（幂等；只动算力分配，服务/端口/模型文件/向量数据都不动）：

```powershell
pwsh -File D:\GitHub\index-tts\tools\ollama_mode.ps1 -Mode status    # 看现在在哪（全量/部分层/CPU）
pwsh -File D:\GitHub\index-tts\tools\ollama_mode.ps1 -Mode partial   # 24 层到 GPU
pwsh -File D:\GitHub\index-tts\tools\ollama_mode.ps1 -Mode partial -Layers 30   # 想更快检索就多给几层
pwsh -File D:\GitHub\index-tts\tools\ollama_mode.ps1 -Mode gpu       # 全量回 GPU（日常态）
pwsh -File D:\GitHub\index-tts\tools\ollama_mode.ps1 -Mode cpu       # 全 CPU
pwsh -File D:\GitHub\index-tts\tools\ollama_mode.ps1 -Mode offload   # 移出显存
```

**坑**：Ollama 的 runner 从 CPU 态往 GPU 重载时偶尔卡死、请求不返回（表现为 300 秒超时）。`ollama_mode.ps1` 已内置自愈：120 秒未返回就杀掉 `llama-server` 重试一次（实测杀进程后 3.7 秒重建 runner 正常）。

完整可核验快照见 `docs\BASELINE_2026-11-17.md`，现场事实用 `tools\baseline_check.ps1` 复跑。

## 配方（预设）：调好的音色/参数怎么存、怎么用

WebUI 自带预设系统，**配方会连音频一起复制**，原文件删了也不影响。

| 事项 | 位置 |
| --- | --- |
| 保存 | WebUI「预设管理」页 → 填名字 → 保存。同名覆盖 |
| 存盘 | `outputs\presets\<名字>\preset.json` + `prompt.wav`（音色参考副本）+ `emo_ref.wav`（情感参考副本） |
| 加载 | WebUI 顶部预设下拉框直接选，参数与音频一起回填 |
| 删除 | 「预设管理」页选中后删除 |
| 无头复用 | `batch_infer.py --preset <名字>`；`--list-presets` 看有哪些 |
| 版本控制 | `outputs/` 在 `.gitignore` 里，不进仓库 |

`preset.json` 存的字段（这是 WebUI 与无头脚本共用的唯一真源）：

```json
{
  "version": "1.0",
  "emo_control_method": 1,            // 0 同音色 / 1 情感参考音频 / 2 情感向量 / 3 情感描述文本
  "emo_weight": 0.75,                 // 情感强度，对应 infer 的 emo_alpha
  "emo_vector": [0,0,0,0,0,0,0,0],    // 喜,怒,哀,惧,厌,忧,惊,平静
  "emo_text": "",
  "emo_random": false,
  "advanced_params": {
    "do_sample": true, "top_p": 0.75, "top_k": 25, "temperature": 0.7,
    "length_penalty": 0.0, "num_beams": 3, "repetition_penalty": 9.0,
    "max_mel_tokens": 1500, "max_text_tokens_per_segment": 100
  },
  "prompt_audio": "prompt.wav",
  "emo_audio": "emo_ref.wav"
}
```

**注意两点**：预设里**不含语言**（`lang`）和**语速**（`duration_factor`）—— 这两项每次调用单独给。

### 参数在哪调、管什么

| 参数 | WebUI 位置 | 无头参数 | 作用 / 经验范围 |
| --- | --- | --- | --- |
| 参考音频 | 顶部音频框 | `--voice` | 决定音色。3~15 秒、单人、无 BGM、无混响 |
| 情感方式 | 情感控制单选 | 由预设决定，`--emo-audio` / `--emo-vector` / `--emo-text` 覆盖 | 0 同音色 / 1 情感参考 / 2 情感向量 / 3 文本描述（要 `-QwenEmo`） |
| 情感强度 | emo weight 滑块 | `--emo-alpha` | 0~1，默认 1.0；用文本描述时建议 0.6 以下更自然 |
| 8 维情感向量 | 8 个滑块 | `--emo-vector 0,0,0.8,0,0,0,0,0` | 喜/怒/哀/惧/厌/忧/惊/平静，每项 0~1 |
| 情感描述文本 | 文本框 | `--emo-text "你吓死我了"` | 需 QwenEmotion，额外 1.2 GB 显存 |
| 语速/时长 | duration_factor 滑块 | `--duration-factor` 或 `--speed` | >1 变慢变长、<1 变快，0.5~2.0；`--speed` 是 OpenAI 口径（1.1 = 略快） |
| 分段长度 | 高级参数 | `--max-tokens` | 默认 120；长句切段，段间 200 ms 静音 |
| 采样 | 高级参数 | `--no-sample` / `--top-p` / `--top-k` / `--temperature` / `--num-beams` / `--repetition-penalty` / `--max-mel-tokens` / `--length-penalty` | 想更稳就 `--no-sample`；想更有起伏就提高 temperature |
| 语言 | 顶部下拉 | `--lang ZH/EN/JA/ES/AR` | 预设不存，每次单独给 |

### 按稿子精调情绪（两条路，2026-11-17 实测）

| 路径 | 做法 | 粒度 | 代价 |
| --- | --- | --- | --- |
| 模型自判 | `use_emo_text=True`（`script_to_voice.py --auto-emo`） | 整段一个情绪 | 需 QwenEmotion（`-QwenEmo`，+1.2 GB 显存） |
| **分段标注** | 每句给一个情绪向量/强度/语速，逐句生成再拼接 | **每句可控** | 无额外开销 |

同文本四档对比（`快躲起来！是他要来了！他要来抓我们了！`，音色 voice_12）：

| 设置 | 时长 | 基频均值 | RMS |
| --- | --- | --- | --- |
| 默认同音色 | 4.42 s | 134.9 Hz | 0.036 |
| 自动识别语气（alpha 0.6） | 4.06 s | 176.6 Hz | 0.117 |
| 手动恐惧向量 `[惧0.9, 惊0.3]` | 3.75 s | 207.5 Hz | 0.121 |
| 手动平静向量 `[平静1.0]` | 5.11 s | 153.4 Hz | 0.029 |

模型自判把这段文本判成高唤醒情绪（音高 176 Hz、能量 0.117），方向与手动「恐惧」一致。恐惧=音高高/能量大/更短促，平静=音高低/能量小/更长，差异 36% 时长、35% 基频，说明向量真在起作用。

工具 `tools\script_to_voice.py`：带情绪标签的稿子 → 逐句出 wav → 拼成一条完整音频。

```powershell
cd D:\GitHub\index-tts
.\.venv\Scripts\python.exe tools\script_to_voice.py --script 稿子.txt --preset 老江口播 --out outputs\口播.wav
.\.venv\Scripts\python.exe tools\script_to_voice.py --script 稿子.txt --voice voice_01 --auto-emo --out out.wav
```

稿子格式（一行一段，标签可省，省了用 `--default-emo`，默认平静）：

```
[兴奋] 大家好，我是做抖音运营八年的老江。
[严肃] 今天说一个所有新手都会踩的坑。
[惊讶] 你猜怎么着？九成的人前三条就选错了赛道。
[平静] 记住一句话，先做能出数据的选题。
```

需要精确控制时用 JSONL：`{"text":"…","emo":[0.8,0,0,0,0,0,0.1,0],"alpha":1.0,"duration_factor":1.05,"pause":400}`。

可用情绪名：开心/兴奋/严肃/愤怒/悲伤/恐惧/惊讶/厌恶/平静/温柔/催促（英文同名兼容），也可直接给 8 维向量（喜,怒,哀,惧,厌,忧,惊,平静）。

实测：4 段不同情绪 57 秒出片（含加载），各段 3.19 / 4.31 / 4.39 / 3.87 秒，拼接成 16.95 秒；严肃段基频 237.7 Hz / RMS 0.060（最沉），惊讶段 RMS 0.092（最亮），方向符合预期。

### 无头模式能不能设这些参数

**能，而且比界面更完整**：`--preset` 直接吃界面里调好的那份配方（实测读到 `emo_control_method=1`、`emo_weight 0.75`、`top_p 0.75`、`top_k 25`、`temperature 0.7`、`max_tokens 100`），命令行再补 `--duration-factor` / `--lang` 这类预设里没有的项，显式参数优先于预设。

```powershell
cd D:\GitHub\index-tts
.\.venv\Scripts\python.exe tools\batch_infer.py --list-presets
.\.venv\Scripts\python.exe tools\batch_infer.py --script 稿子.txt --preset 老江口播 --out outputs\job
pwsh -File tools\tts_session.ps1 -Preset 老江口播 -Script 稿子.txt -OutDir outputs\job   # 带显存调度与还原
```

实测：session + 预设单条 47 秒出 5.86 秒 wav；3 段批量 3/3 成功（6.5 / 7.0 / 5.2 秒）。

长文本靠 `max_text_tokens_per_segment`（默认 120）自动分段，段间自动插 200 ms 静音。

拼音/音素纠音（2.5 支持）：`他在银<行|XING2>里<行|HANG2>走了半天`；英文 `a <minute|M IH1 . N AH0 T>`；日文 `<上手|じょうず>`。

## 本机踩过的三个坑

1. **gradio 起不来：`httpx.InvalidURL: Invalid port ':1]'`**
   系统环境变量 `NO_PROXY` 里含 `[::1]`，httpx 0.28 把方括号 IPv6 当主机+端口解析直接抛异常。修复方式是去掉方括号（`tools\run_webui.py` 在 Python 进程内把 `NO_PROXY` 设为 `127.0.0.1,localhost,::1`）。注意本机 pwsh 里改 `$env:` 不一定能覆盖到子进程，所以放在 Python 侧设置。

2. **显存被挤爆，25 token 跑 3 分 21 秒**
   4070 只有 12 GB：`dwm` 约 2 GB，Ollama 全量 3.9 GB，IndexTTS 6.3 GB，超出的部分被 WDDM 分页到共享内存，token 速率从 25 it/s 掉到 0.03 it/s。解法不是二选一，而是两边各砍一刀：IndexTTS 的 w2v-bert 放 CPU（省 1.3 GB），Ollama 只给 24 层（省 1.2 GB），合计 9.5 GB 装得下，两边都跑全速。

3. **Ollama runner 从 CPU 态往 GPU 重载会卡死**
   表现为请求 300 秒不返回、显存不动。手动处理：`Get-Process llama-server | Stop-Process -Force`，5 秒后新 runner 自己起来（3.7 秒加载完）。`ollama_mode.ps1` 已内置这个自愈逻辑。

## 性能实测（RTX 4070 12 GB，bf16，未装 cuda_kernel/accel）

| 场景 | 服务端推理 | 音频长度 | RTF |
| --- | --- | --- | --- |
| **默认配置（Ollama 24 层 + w2v 在 CPU），热态短句** | **2.2~2.5 s** | 2.2 s | **1.04~1.11** |
| 默认配置，长句（7 秒音频） | 5.9 s | 7.06 s | 0.84 |
| 冷启动首轮（含 CUDA 预热） | 24.19 s | 8.22 s | 2.94 |
| 稳定轮（CLI，显存干净） | 5.68 s | 6.50 s | 0.87 |
| WebUI `/gen_single`，显存干净 | 6.20 s（gpt 4.8 / s2mel 0.96 / bigvgan 0.18） | 5.86 s | 1.06 |
| WebUI `/gen_single`，Ollama 占 4 GB | 17.82 s | 5.63 s | 3.16 |
| WebUI `/gen_single`，Ollama 满载挤爆 | 255.65 s | 5.58 s | 45.8 |

WebUI 一次点击的客户端往返：显存干净 7.8 s，显存被挤 22–28 s（含参考音频上传与 gradio 队列）。

**结论：本机 IndexTTS 的速度由“Ollama 那 4 GB 在不在显存”决定。** 默认配置不碰 Ollama（向量库要一直可用），慢就慢；需要批量/长文快出片时才临时腾一次：

```powershell
pwsh -ExecutionPolicy Bypass -File D:\GitHub\index-tts\tools\free_vram.ps1
```

该脚本只发 `keep_alive: 0` 让 Ollama 把权重移出显存，服务、端口、模型文件、向量数据都不动，下次向量请求自动加载回来（实测 0.32 秒、200 正常、2560 维）。后台记忆检索本身就会把模型唤醒，所以腾出来的窗口只对紧接着的那次批量有效，别指望长期空着。

未做的加速项：`--accel`（flash-attn 2.8.3）与 `--cuda_kernel` 在 Windows 需要 `triton-windows` + CUDA Toolkit 12.8 编译链，`--deepspeed` 在 Windows 容易装崩，均按官方建议跳过。显存充裕时可再评估 `--torch_compile`。

## 网络与流量

按用户要求全程直连、不走梯子：

- PyPI：`UV_DEFAULT_INDEX=https://mirrors.aliyun.com/pypi/simple`（本机已有）
- torch 2.8.0+cu128：`download.pytorch.org` 直连实测 8.9 MB/s
- 权重：ModelScope 直连实测 22–29 MB/s，5.6 GB 用 2 分 35 秒
- 示例音频：设 `USE_MODELSCOPE=true`，走 `hf-mirror.com` 而不是 huggingface.co
- gradio 遥测：`GRADIO_ANALYTICS_ENABLED=False`

## 上游更新

只做本地同步、不做本地定制，所以直接 `git pull` + `uv sync --extra webui` 即可；权重变更时重新 `modelscope download --model IndexTeam/IndexTTS-2.5 --local_dir checkpoints`。更新前记录当前 `git rev-parse HEAD` 与 `uv.lock` 校验和，便于回退。

## 未验证项

- 未测 IndexTTS-2（`--version 2`，需另下 `checkpoints_2`）。
- 未测 vLLM 生产部署路径与 DeepSpeed。
- 未做长时间批量合成压测（显存与内存余量未量化）。
- 未配开机自启/计划任务（用户未要求）。
