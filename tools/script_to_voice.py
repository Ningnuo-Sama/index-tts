"""按稿子的情绪分段出片：每句不同的情感向量/强度/语速，最后拼成一条完整音频。

为什么需要它：IndexTTS 的 `use_emo_text` 是「整段一个情绪」，口播稿通常一句一个情绪
（开场抓人、中间严肃、结尾上扬），所以要按句控制。

输入两种格式：

1) 带标签的纯文本（一行一段，标签可省，省了用 --default-emo）：
       [开心] 大家好，我是做抖音运营八年的老江。
       [严肃] 今天说一个所有新手都会踩的坑。
       [惊讶] 你猜怎么着？前三条就选错了赛道。

2) JSONL（可精确控制每一段）：
       {"text": "大家好…", "emo": [0.8,0,0,0,0,0,0.1,0], "alpha": 1.0, "duration_factor": 1.0, "pause": 400}
       {"text": "今天说…", "emo_named": "serious", "alpha": 0.9, "duration_factor": 1.05}

用法：
    .venv\\Scripts\\python.exe tools\\script_to_voice.py --script 稿子.txt --preset 老江口播 --out outputs\\口播.wav --segments-dir outputs\\口播_分段
    .venv\\Scripts\\python.exe tools\\script_to_voice.py --script 稿子.jsonl --voice voice_01 --auto-emo --out out.wav

关键参数：
    --preset        复用 WebUI 配方（音色 + 情感方式 + 采样参数）
    --auto-emo      不手写向量，让模型按文本自判情绪（需要 QwenEmotion，见 -QwenEmo 启动）
    --default-emo   没有标签的段落用哪个情绪，默认「平静」
    --gap           段间静音毫秒，默认 250（每段 JSONL 里的 pause 优先）
    --speed/--duration-factor  全局语速，单段的 duration_factor 优先
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
PRESETS_DIR = ROOT / "outputs" / "presets"
PRESET_AUDIO = {f"voice_{i:02d}": f"examples/voice_{i:02d}.wav" for i in range(1, 13)}

# 情绪名 → 8 维向量（喜,怒,哀,惧,厌,忧,惊,平静）
EMO_NAMED = {
    "开心": [0.8, 0, 0, 0, 0, 0, 0.1, 0],
    "兴奋": [0.9, 0, 0, 0, 0, 0, 0.3, 0],
    "严肃": [0, 0, 0, 0, 0, 0.3, 0, 0.7],
    "愤怒": [0, 0.8, 0, 0, 0, 0, 0, 0],
    "悲伤": [0, 0, 0.8, 0, 0, 0.2, 0, 0],
    "恐惧": [0, 0, 0, 0.9, 0, 0, 0.3, 0],
    "惊讶": [0, 0, 0, 0, 0, 0, 0.9, 0],
    "厌恶": [0, 0, 0, 0, 0.8, 0, 0, 0],
    "平静": [0, 0, 0, 0, 0, 0, 0, 1.0],
    "温柔": [0.2, 0, 0, 0, 0, 0, 0, 0.8],
    "催促": [0.2, 0.2, 0, 0, 0, 0, 0.5, 0],
}
EMO_NAMED.update({
    "happy": EMO_NAMED["开心"], "excited": EMO_NAMED["兴奋"], "serious": EMO_NAMED["严肃"],
    "angry": EMO_NAMED["愤怒"], "sad": EMO_NAMED["悲伤"], "fear": EMO_NAMED["恐惧"],
    "surprised": EMO_NAMED["惊讶"], "disgusted": EMO_NAMED["厌恶"], "calm": EMO_NAMED["平静"],
    "gentle": EMO_NAMED["温柔"], "urgent": EMO_NAMED["催促"],
})


def resolve_audio(path_like: str) -> Path:
    preset = PRESET_AUDIO.get(path_like if path_like in PRESET_AUDIO else Path(path_like).stem)
    p = Path(preset) if preset else Path(path_like)
    if not p.is_absolute():
        p = ROOT / p
    if not p.exists():
        example = ROOT / "examples" / f"{Path(path_like).stem}.wav"
        if example.exists():
            return example
        raise SystemExit(f"找不到音频：{p}")
    return p


def load_preset(name: str) -> dict:
    f = PRESETS_DIR / name / "preset.json"
    if not f.is_file():
        names = [p.name for p in PRESETS_DIR.iterdir() if (p / "preset.json").is_file()] if PRESETS_DIR.is_dir() else []
        raise SystemExit(f"找不到预设 {name}；现有：{', '.join(names) or '（无）'}")
    data = json.loads(f.read_text(encoding="utf-8"))
    d = PRESETS_DIR / name
    for key in ("prompt_audio", "emo_audio"):
        rel = data.get(key) or ""
        data[key] = str(d / rel) if rel else ""
    data["_dir"] = str(d)
    return data


def parse_script(path: Path):
    """返回 [{text, emo(list|None), emo_named, alpha, duration_factor, pause}]"""
    raw = path.read_text(encoding="utf-8-sig")
    segs = []
    if path.suffix.lower() == ".jsonl":
        for line in raw.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            segs.append(json.loads(line))
    else:
        for line in raw.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = re.match(r"^[\[【]([^\]】]+)[\]】]\s*(.+)$", line)
            if m:
                segs.append({"text": m.group(2).strip(), "emo_named": m.group(1).strip()})
            else:
                segs.append({"text": line})
    return segs


def main() -> int:
    ap = argparse.ArgumentParser(description="按情绪分段出片并拼接")
    ap.add_argument("--script", required=True)
    ap.add_argument("--preset", default="")
    ap.add_argument("--voice", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--segments-dir", default="")
    ap.add_argument("--lang", default="ZH", choices=["ZH", "EN", "JA", "ES", "AR"])
    ap.add_argument("--auto-emo", action="store_true", help="让模型按文本自判情绪（需 QwenEmotion）")
    ap.add_argument("--default-emo", default="平静")
    ap.add_argument("--gap", type=int, default=250)
    ap.add_argument("--speed", type=float, default=0.0)
    ap.add_argument("--duration-factor", type=float, default=0.0)
    ap.add_argument("--max-tokens", type=int, default=0)
    args = ap.parse_args()

    script_path = Path(args.script)
    if not script_path.is_absolute():
        script_path = ROOT / script_path
    if not script_path.exists():
        raise SystemExit(f"找不到稿子：{script_path}")
    segs = parse_script(script_path)
    if not segs:
        raise SystemExit("稿子里没有有效内容")

    preset = load_preset(args.preset) if args.preset else {}
    adv = preset.get("advanced_params", {})
    voice_arg = args.voice or preset.get("prompt_audio") or "voice_01"
    voice = resolve_audio(voice_arg)
    base_alpha = float(preset.get("emo_weight", 1.0))
    max_tokens = args.max_tokens or int(adv.get("max_text_tokens_per_segment", 120))
    global_factor = args.duration_factor or (round(1.0 / args.speed, 3) if args.speed else 1.0)

    gen = {}
    for k in ("do_sample", "top_p", "top_k", "temperature", "length_penalty", "num_beams",
              "repetition_penalty", "max_mel_tokens"):
        if adv.get(k) is not None:
            gen[k] = adv[k]

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = Path(args.out) if args.out else ROOT / "outputs" / f"script_{stamp}.wav"
    if not out_path.is_absolute():
        out_path = ROOT / out_path
    seg_dir = Path(args.segments_dir) if args.segments_dir else out_path.with_suffix("")
    if not seg_dir.is_absolute():
        seg_dir = ROOT / seg_dir
    seg_dir.mkdir(parents=True, exist_ok=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    os.chdir(ROOT)
    from indextts.infer_v2_5 import IndexTTS2

    need_qwen = args.auto_emo
    print(f"加载模型（auto_emo={need_qwen}）...")
    tts = IndexTTS2(cfg_path="checkpoints/config.yaml", model_dir="checkpoints",
                    use_bf16=True, use_qwen_emo=need_qwen)
    if need_qwen and getattr(tts, "qwen_emo", None) is None:
        raise SystemExit("用了 --auto-emo 但 QwenEmotion 没加载成功")

    import numpy as np
    import soundfile as sf

    pieces = []
    sr_out = None
    for i, seg in enumerate(segs, start=1):
        text = seg["text"]
        emo = seg.get("emo")
        named = seg.get("emo_named") or (None if (emo or args.auto_emo) else args.default_emo)
        if not emo and named:
            emo = EMO_NAMED.get(named)
            if emo is None:
                raise SystemExit(f"未知情绪名：{named}；可用：{', '.join(sorted(set(EMO_NAMED)))}")
        alpha = float(seg.get("alpha", base_alpha))
        factor = float(seg.get("duration_factor", global_factor))
        pause = int(seg.get("pause", args.gap))
        clean = re.sub(r"[^\w\u4e00-\u9fff]+", "", text)[:10] or "seg"
        dst = seg_dir / f"{i:03d}_{clean}.wav"
        t0 = time.time()
        kwargs = dict(gen)
        if args.auto_emo and not emo:
            kwargs.update(use_emo_text=True, emo_alpha=alpha)
        elif emo:
            kwargs.update(emo_vector=emo, emo_alpha=alpha)
        tts.infer(spk_audio_prompt=str(voice), text=text, lang=args.lang, output_path=str(dst),
                  duration_factor=factor, max_text_tokens_per_segment=max_tokens, verbose=False, **kwargs)
        y, sr = sf.read(dst, dtype="float32")
        sr_out = sr
        pieces.append(y)
        if pause > 0:
            pieces.append(np.zeros(int(sr * pause / 1000), dtype=np.float32))
        label = named or ("自动" if (args.auto_emo and not emo) else "同音色")
        print(f"[{i}/{len(segs)}] {label:<4} {len(y)/sr:.2f}s  {time.time()-t0:.1f}s  {dst.name}")

    merged = np.concatenate(pieces) if pieces else np.zeros(1, dtype=np.float32)
    sf.write(out_path, merged, sr_out or 22050)
    print(f"拼接完成：{out_path}  {len(merged)/sr_out:.2f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
