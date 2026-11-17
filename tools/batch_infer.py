"""批量台词合成：把稿子按行/按段批量出 wav（IndexTTS 2.5，无头模式）。

两种用法：
  1) 直接用命令行参数
  2) 直接用 WebUI 里调好的配方（预设）：--preset <名字>
     预设存在 outputs\\presets\\<名字>\\{preset.json, prompt.wav, emo_ref.wav}，
     是 WebUI「预设管理」页保存出来的同一份文件。命令行显式给的参数会覆盖预设。

用法（在项目根执行）：
    .venv\\Scripts\\python.exe tools\\batch_infer.py --script 稿子.txt --voice voice_01 --out outputs\\job
    .venv\\Scripts\\python.exe tools\\batch_infer.py --script 稿子.txt --preset 我的音色 --out outputs\\job
    .venv\\Scripts\\python.exe tools\\batch_infer.py --list-presets

参数（都能被预设覆盖，命令行优先）：
    --script           UTF-8 文本，一行一段（空行忽略，行首 # 注释）
    --voice            参考音频：预设名 voice_01..voice_12、examples 里的 emo_sad、或路径
    --preset           WebUI 保存的配方名（自动带上参考音频/情感/采样参数）
    --out              输出目录，默认 outputs\\batch_<时间戳>
    --lang             ZH / EN / JA / ES / AR，默认 ZH（预设不存语言）
    --emo-audio        情感参考音频
    --emo-alpha        情感强度 0~1，默认 1.0（预设里叫 emo_weight）
    --emo-vector       8 个逗号分隔数值：喜,怒,哀,惧,厌,忧,惊,平静
    --emo-text         情感描述文本（需模型以 --qwen_emo 加载，见 start_webui.ps1 -QwenEmo）
    --duration-factor  语速：>1 变慢、<1 变快，0.5~2.0，默认 1.0（预设不存这一项）
    --max-tokens       单段最大 token，默认 120（预设里的 max_text_tokens_per_segment）
    --top-p/--top-k/--temperature/--num-beams/--repetition-penalty/--max-mel-tokens/--length-penalty
    --no-sample        关闭采样（do_sample=False，更稳定但更平）

注意：WebUI 与批量脚本会各占约 6.3 GB 显存，12 GB 卡上不要同时跑。
日常出片建议用 tools\\tts_session.ps1（会自动切显存并在跑完后还原）。
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PRESETS_DIR = ROOT / "outputs" / "presets"

PRESETS = {f"voice_{i:02d}": f"examples/voice_{i:02d}.wav" for i in range(1, 13)}

# WebUI 预设里的 emo_control_method 取值
EMO_SAME_VOICE, EMO_REF_AUDIO, EMO_VECTOR, EMO_TEXT = 0, 1, 2, 3


def resolve_audio(path_like: str) -> Path:
    """支持预设名 voice_01..voice_12、examples 里的 emo_sad 等、以及普通路径。"""
    preset = PRESETS.get(path_like if path_like in PRESETS else Path(path_like).stem)
    p = Path(preset) if preset else Path(path_like)
    if not p.is_absolute():
        p = ROOT / p
    if not p.exists():
        example = ROOT / "examples" / f"{Path(path_like).stem}.wav"
        if example.exists():
            return example
        raise SystemExit(f"找不到音频：{p}（预设名：{', '.join(PRESETS)}；情感参考可用 emo_sad / emo_hate）")
    return p


def list_presets() -> list:
    if not PRESETS_DIR.is_dir():
        return []
    return sorted(p.name for p in PRESETS_DIR.iterdir() if (p / "preset.json").is_file())


def load_preset(name: str) -> dict:
    """读 outputs/presets/<name>/preset.json，音频路径转成绝对路径。"""
    d = PRESETS_DIR / name
    f = d / "preset.json"
    if not f.is_file():
        available = ", ".join(list_presets()) or "（还没有任何预设）"
        raise SystemExit(f"找不到预设：{name}\n现有预设：{available}")
    data = json.loads(f.read_text(encoding="utf-8"))
    for key, fname in (("prompt_audio", "prompt.wav"), ("emo_audio", "emo_ref.wav")):
        rel = data.get(key) or ""
        data[key] = str(d / rel) if rel else ""
        if data[key] and not Path(data[key]).exists() and (d / fname).exists():
            data[key] = str(d / fname)
    data["_dir"] = str(d)
    return data


def main() -> int:
    ap = argparse.ArgumentParser(description="IndexTTS 2.5 批量台词合成（无头）")
    ap.add_argument("--script", default="", help="台词文本，一行一段")
    ap.add_argument("--voice", default="", help="参考音频（预设名或路径）")
    ap.add_argument("--preset", default="", help="WebUI 保存的配方名")
    ap.add_argument("--out", default="", help="输出目录")
    ap.add_argument("--lang", default="ZH", choices=["ZH", "EN", "JA", "ES", "AR"])
    ap.add_argument("--emo-audio", default="")
    ap.add_argument("--emo-alpha", type=float, default=None)
    ap.add_argument("--emo-vector", default="", help="8 个逗号分隔数值")
    ap.add_argument("--emo-text", default="")
    ap.add_argument("--duration-factor", type=float, default=None)
    ap.add_argument("--max-tokens", type=int, default=None)
    ap.add_argument("--top-p", type=float, default=None)
    ap.add_argument("--top-k", type=int, default=None)
    ap.add_argument("--temperature", type=float, default=None)
    ap.add_argument("--num-beams", type=int, default=None)
    ap.add_argument("--repetition-penalty", type=float, default=None)
    ap.add_argument("--max-mel-tokens", type=int, default=None)
    ap.add_argument("--length-penalty", type=float, default=None)
    ap.add_argument("--no-sample", action="store_true")
    ap.add_argument("--prefix", default="")
    ap.add_argument("--list-presets", action="store_true")
    args = ap.parse_args()

    if args.list_presets:
        names = list_presets()
        if not names:
            print("还没有预设。在 WebUI 的「预设管理」页调好参数后保存，会出现在 outputs\\presets\\ 下。")
        else:
            print(f"共 {len(names)} 个预设（{PRESETS_DIR}）：")
            for n in names:
                try:
                    d = load_preset(n)
                    ap_ = d.get("advanced_params", {})
                    print(f"  {n}  emo_method={d.get('emo_control_method')} alpha={d.get('emo_weight')} "
                          f"top_p={ap_.get('top_p')} top_k={ap_.get('top_k')} temp={ap_.get('temperature')} "
                          f"max_tokens={ap_.get('max_text_tokens_per_segment')}")
                except SystemExit:
                    print(f"  {n}  （读取失败）")
        return 0

    if not args.script:
        raise SystemExit("需要 --script（除非只用 --list-presets）")

    # 预设优先作为基线，命令行参数覆盖
    preset = load_preset(args.preset) if args.preset else {}
    adv = preset.get("advanced_params", {})

    voice_arg = args.voice or preset.get("prompt_audio") or "voice_01"
    voice = resolve_audio(voice_arg)
    emo_audio_arg = args.emo_audio or preset.get("emo_audio") or ""
    emo = resolve_audio(emo_audio_arg) if emo_audio_arg else None

    emo_alpha = args.emo_alpha if args.emo_alpha is not None else float(preset.get("emo_weight", 1.0))
    if args.emo_vector:
        emo_vector = [float(x) for x in args.emo_vector.split(",")]
    elif preset.get("emo_control_method") == EMO_VECTOR:
        emo_vector = [float(x) for x in preset.get("emo_vector", [0] * 8)]
    else:
        emo_vector = None
    emo_text = args.emo_text or (preset.get("emo_text", "") if preset.get("emo_control_method") == EMO_TEXT else "")
    use_emo_text = bool(emo_text)

    # 只用预设声明的情感来源，避免误传
    method = preset.get("emo_control_method", None)
    if method is not None:
        if method == EMO_SAME_VOICE:
            emo, emo_vector, emo_text, use_emo_text = None, None, "", False
        elif method == EMO_REF_AUDIO:
            emo_vector, emo_text, use_emo_text = None, "", False
        elif method == EMO_VECTOR:
            emo, emo_text, use_emo_text = None, "", False
        elif method == EMO_TEXT:
            emo, emo_vector = None, None

    gen = {}
    if args.no_sample:
        gen["do_sample"] = False
    elif adv.get("do_sample") is not None:
        gen["do_sample"] = bool(adv["do_sample"])
    for key, val, adv_key in (
        ("top_p", args.top_p, "top_p"), ("top_k", args.top_k, "top_k"),
        ("temperature", args.temperature, "temperature"), ("length_penalty", args.length_penalty, "length_penalty"),
        ("num_beams", args.num_beams, "num_beams"), ("repetition_penalty", args.repetition_penalty, "repetition_penalty"),
        ("max_mel_tokens", args.max_mel_tokens, "max_mel_tokens"),
    ):
        if val is not None:
            gen[key] = val
        elif adv.get(adv_key) is not None:
            gen[key] = adv[adv_key]
    max_tokens = args.max_tokens if args.max_tokens is not None else int(adv.get("max_text_tokens_per_segment", 120))
    duration_factor = args.duration_factor if args.duration_factor is not None else 1.0
    if not 0.5 <= duration_factor <= 2.0:
        raise SystemExit("duration_factor 需在 0.5~2.0")

    script_path = Path(args.script)
    if not script_path.is_absolute():
        script_path = ROOT / script_path
    if not script_path.exists():
        print(f"找不到台词文件：{script_path}")
        return 2

    lines = [ln.strip() for ln in script_path.read_text(encoding="utf-8-sig").splitlines()
             if ln.strip() and not ln.strip().startswith("#")]
    if not lines:
        print("台词文件里没有有效内容")
        return 2

    out_dir = Path(args.out) if args.out else ROOT / "outputs" / f"batch_{datetime.now():%Y%m%d_%H%M%S}"
    if not out_dir.is_absolute():
        out_dir = ROOT / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    from indextts.infer_v2_5 import IndexTTS2

    print(f"配方：{args.preset or '命令行参数'}")
    print(f"  音色参考：{voice}")
    print(f"  情感：audio={emo} vector={emo_vector} text={emo_text!r} alpha={emo_alpha}")
    print(f"  采样：{gen or '默认'}  max_tokens={max_tokens}  duration_factor={duration_factor}")
    print(f"加载模型（{len(lines)} 段待合成）...")
    tts = IndexTTS2(cfg_path="checkpoints/config.yaml", model_dir="checkpoints", use_bf16=True,
                    use_qwen_emo=use_emo_text)
    if use_emo_text and getattr(tts, "qwen_emo", None) is None:
        print("警告：用了情感描述文本但 QwenEmotion 没加载，2.5 会报错；改 --emo-text 或用情感参考音频。")

    ok = 0
    for idx, text in enumerate(lines, start=1):
        safe = "".join(c for c in text[:12] if c not in '\\/:*?"<>|').strip().replace(" ", "_")
        name = f"{args.prefix}{idx:04d}_{safe}.wav" if args.prefix else f"{idx:04d}_{safe}.wav"
        dst = out_dir / name
        t0 = time.time()
        try:
            tts.infer(
                spk_audio_prompt=str(voice),
                text=text,
                lang=args.lang,
                output_path=str(dst),
                emo_audio_prompt=str(emo) if emo else None,
                emo_alpha=emo_alpha,
                emo_vector=emo_vector,
                use_emo_text=use_emo_text,
                emo_text=emo_text or None,
                duration_factor=duration_factor,
                max_text_tokens_per_segment=max_tokens,
                verbose=False,
                **gen,
            )
            ok += 1
            print(f"[{idx}/{len(lines)}] {dst.name}  {time.time() - t0:.1f}s")
        except Exception as exc:  # 单段失败不中断整批
            print(f"[{idx}/{len(lines)}] 失败：{text[:20]}... -> {exc}")
    print(f"完成：{ok}/{len(lines)}，输出目录 {out_dir}")
    return 0 if ok == len(lines) else 1


if __name__ == "__main__":
    raise SystemExit(main())
