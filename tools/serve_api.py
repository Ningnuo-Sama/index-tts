"""IndexTTS 2.5 本地 HTTP API 服务（FastAPI）。

在项目根启动：
    .venv\\Scripts\\python.exe tools\\serve_api.py --port 7861
    # 或者直接：pwsh -ExecutionPolicy Bypass -File tools\\start_api.ps1

端点：
    GET  /health                    模型状态、设备、显存、是否在忙
    GET  /v1/models                 OpenAI 风格模型列表
    POST /v1/audio/speech           OpenAI 兼容：{model,input,voice,response_format,speed}
    POST /v1/audio/speech/file      同上，但 voice 用 multipart 上传参考音频（file 字段）
    POST /tts                       完整参数：text, voice, lang, emo_audio, emo_vector,
                                    emo_text, emo_alpha, duration_factor, max_tokens

说明：
    - 12 GB 卡上一次只能跑一个请求，服务内部串行排队；WebUI 与本服务不要同时开。
    - voice 支持三种写法：预设名 voice_01..voice_12、项目内相对路径、绝对路径。
    - 只输出 wav（系统没装 ffmpeg，不提供 mp3）。
"""

import argparse
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parent.parent

# 与 WebUI 一样的网络/环境修正，必须在导入 torch 之前完成
for _k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
    os.environ.pop(_k, None)
os.environ["NO_PROXY"] = "127.0.0.1,localhost,::1"
os.environ.pop("no_proxy", None)
os.environ.setdefault("USE_MODELSCOPE", "true")
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

import uvicorn  # noqa: E402
from fastapi import FastAPI, File, Form, HTTPException, UploadFile  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

MODEL_ID = "indextts-2.5"
PRESET_VOICES = {f"voice_{i:02d}": f"examples/voice_{i:02d}.wav" for i in range(1, 13)}
OUT_DIR = ROOT / "outputs" / "api"

_lock = threading.Lock()
_state = {"tts": None, "device": "", "vram_gb": 0.0, "loaded_at": None, "requests": 0, "busy": False}


def _load_model(model_dir: str, use_qwen_emo: bool) -> None:
    import torch

    from indextts.infer_v2_5 import IndexTTS2

    if torch.cuda.is_available():
        props = torch.cuda.get_device_properties(0)
        _state["device"] = props.name
        _state["vram_gb"] = round(props.total_memory / (1024 ** 3), 1)
    else:
        _state["device"] = "cpu"
    _state["tts"] = IndexTTS2(
        cfg_path=os.path.join(model_dir, "config.yaml"),
        model_dir=model_dir,
        use_bf16=torch.cuda.is_available(),
        use_qwen_emo=use_qwen_emo,
    )
    _state["loaded_at"] = datetime.now().isoformat(timespec="seconds")
    print(f"[api] 模型就绪：{MODEL_ID} on {_state['device']} ({_state['vram_gb']} GB)")


def _resolve_voice(voice: str) -> Path:
    raw = (voice or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="voice 不能为空：用预设名 voice_01..voice_12，或给音频路径")
    preset = PRESET_VOICES.get(raw if raw in PRESET_VOICES else Path(raw).stem)
    if preset:
        candidate = ROOT / preset
    else:
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = ROOT / candidate
        example = ROOT / "examples" / f"{Path(raw).stem}.wav"
        if not candidate.exists() and example.exists():
            candidate = example
    if not candidate.exists():
        raise HTTPException(
            status_code=400,
            detail=f"找不到参考音频：{candidate}（预设名：{', '.join(PRESET_VOICES)}；情感参考可用 emo_sad / emo_hate）",
        )
    return candidate


def _output_path(text: str) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^\w\u4e00-\u9fff]+", "", text)[:16] or "tts"
    return OUT_DIR / f"{datetime.now():%Y%m%d_%H%M%S}_{safe}.wav"


def _synthesize(
    *,
    text: str,
    voice: Path,
    lang: str = "ZH",
    emo_audio: Optional[Path] = None,
    emo_alpha: float = 1.0,
    emo_vector: Optional[List[float]] = None,
    emo_text: Optional[str] = None,
    duration_factor: float = 1.0,
    max_tokens: int = 120,
) -> Path:
    tts = _state["tts"]
    if tts is None:
        raise HTTPException(status_code=503, detail="模型尚未加载完成")
    if not text.strip():
        raise HTTPException(status_code=400, detail="text 不能为空")
    if not 0.5 <= duration_factor <= 2.0:
        raise HTTPException(status_code=400, detail="duration_factor 需在 0.5~2.0")
    if emo_vector is not None and len(emo_vector) != 8:
        raise HTTPException(status_code=400, detail="emo_vector 需 8 个 0~1 的浮点：[喜,怒,哀,惧,厌,忧,惊,平静]")

    dst = _output_path(text)
    started = time.time()
    with _lock:
        _state["busy"] = True
        try:
            tts.infer(
                spk_audio_prompt=str(voice),
                text=text,
                lang=lang,
                output_path=str(dst),
                emo_audio_prompt=str(emo_audio) if emo_audio else None,
                emo_alpha=emo_alpha,
                emo_vector=emo_vector,
                use_emo_text=bool(emo_text),
                emo_text=emo_text,
                duration_factor=duration_factor,
                max_text_tokens_per_segment=max_tokens,
                verbose=False,
            )
        finally:
            _state["busy"] = False
    _state["requests"] += 1
    print(f"[api] {dst.name}  {time.time() - started:.1f}s  {len(text)}字")
    return dst


class SpeechRequest(BaseModel):
    model: str = MODEL_ID
    input: str = Field(..., description="要合成的文本")
    voice: str = Field("voice_01", description="预设名 voice_01..voice_12 或音频路径")
    response_format: str = "wav"
    speed: float = Field(1.0, ge=0.5, le=2.0, description="1.0 正常；>1 更快（内部换算 duration_factor=1/speed）")
    lang: str = "ZH"
    emo_audio: Optional[str] = None
    emo_alpha: float = 1.0
    emo_vector: Optional[List[float]] = None
    emo_text: Optional[str] = None


class TTSRequest(BaseModel):
    text: str
    voice: str = "voice_01"
    lang: str = "ZH"
    emo_audio: Optional[str] = None
    emo_alpha: float = 1.0
    emo_vector: Optional[List[float]] = None
    emo_text: Optional[str] = None
    duration_factor: float = Field(1.0, ge=0.5, le=2.0)
    max_tokens: int = 120


def create_app() -> FastAPI:
    app = FastAPI(title="IndexTTS 2.5 本地 API", version="2.5")

    @app.get("/health")
    def health():
        return {
            "status": "ok" if _state["tts"] else "loading",
            "model": MODEL_ID,
            "device": _state["device"],
            "vram_gb": _state["vram_gb"],
            "loaded_at": _state["loaded_at"],
            "busy": _state["busy"],
            "requests": _state["requests"],
        }

    @app.get("/v1/models")
    def models():
        return {"object": "list", "data": [{"id": MODEL_ID, "object": "model", "owned_by": "local"}]}

    @app.post("/v1/audio/speech")
    def speech(req: SpeechRequest):
        if req.response_format.lower() not in ("wav", "pcm"):
            raise HTTPException(status_code=400, detail="只支持 wav/pcm（本机没装 ffmpeg）")
        dst = _synthesize(
            text=req.input,
            voice=_resolve_voice(req.voice),
            lang=req.lang,
            emo_audio=_resolve_voice(req.emo_audio) if req.emo_audio else None,
            emo_alpha=req.emo_alpha,
            emo_vector=req.emo_vector,
            emo_text=req.emo_text,
            duration_factor=round(1.0 / req.speed, 3),
        )
        return FileResponse(dst, media_type="audio/wav", filename=dst.name)

    @app.post("/v1/audio/speech/file")
    def speech_file(
        input: str = Form(...),
        voice_file: UploadFile = File(..., description="参考音频（wav/mp3/flac）"),
        lang: str = Form("ZH"),
        speed: float = Form(1.0),
        duration_factor: float = Form(0.0, description="给 0 表示用 speed 换算"),
        emo_alpha: float = Form(1.0),
        emo_file: Optional[UploadFile] = File(None, description="情感参考音频（可选）"),
        emo_text: Optional[str] = Form(None),
    ):
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        stamp = f"{datetime.now():%Y%m%d_%H%M%S%f}"
        voice_path = OUT_DIR / f"upload_voice_{stamp}_{voice_file.filename}"
        voice_path.write_bytes(voice_file.file.read())
        emo_path = None
        if emo_file is not None:
            emo_path = OUT_DIR / f"upload_emo_{stamp}_{emo_file.filename}"
            emo_path.write_bytes(emo_file.file.read())
        factor = duration_factor if duration_factor else round(1.0 / max(speed, 0.01), 3)
        dst = _synthesize(
            text=input,
            voice=voice_path,
            lang=lang,
            emo_audio=emo_path,
            emo_alpha=emo_alpha,
            emo_text=emo_text,
            duration_factor=factor,
        )
        return FileResponse(dst, media_type="audio/wav", filename=dst.name)

    @app.post("/tts")
    def tts(req: TTSRequest):
        dst = _synthesize(
            text=req.text,
            voice=_resolve_voice(req.voice),
            lang=req.lang,
            emo_audio=_resolve_voice(req.emo_audio) if req.emo_audio else None,
            emo_alpha=req.emo_alpha,
            emo_vector=req.emo_vector,
            emo_text=req.emo_text,
            duration_factor=req.duration_factor,
            max_tokens=req.max_tokens,
        )
        return FileResponse(dst, media_type="audio/wav", filename=dst.name)

    return app


def main() -> None:
    global ROOT
    ap = argparse.ArgumentParser(description="IndexTTS 2.5 本地 HTTP API")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=7861)
    ap.add_argument("--model_dir", default="checkpoints")
    ap.add_argument("--qwen_emo", action="store_true", help="额外加载 QwenEmotion（多占约 1.2 GB）")
    args = ap.parse_args()

    os.chdir(ROOT)
    print(f"[api] 加载模型 {args.model_dir} ...（首次约 60 秒）")
    _load_model(args.model_dir, args.qwen_emo)
    app = create_app()
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
