"""IndexTTS WebUI 的轻量客户端：走 gradio API，复用已运行的模型，**不额外占显存**。

前提：WebUI 已在跑（tools\\start_webui.ps1），默认 http://127.0.0.1:7860。

单条：
    .venv\\Scripts\\python.exe tools\\tts_client.py --text "要合成的话" --out out.wav --voice examples\\voice_01.wav

批量（一行一段，复用同一个 WebUI，不重新加载模型）：
    .venv\\Scripts\\python.exe tools\\tts_client.py --script 稿子.txt --out-dir outputs\\job --voice examples\\voice_01.wav

常用参数：
    --voice           预设名 voice_01..voice_12，或音频路径
    --lang            ZH / EN / JA / ES / AR，默认 ZH
    --speed           OpenAI 风格：1.0 正常，>1 更快（内部换算 duration_factor=1/speed）
    --duration-factor 直接给 IndexTTS 的时长系数（0.5~2.0，>1 变慢）
    --emo-audio       情感参考音频（音色仍取 --voice）
    --emo-alpha       情感强度 0~1，默认 1.0
    --emo-vector      8 个逗号分隔数值：喜,怒,哀,惧,厌,忧,惊,平静
    --url             WebUI 地址，默认 http://127.0.0.1:7860
"""

import argparse
import os
import shutil
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

for _k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
    os.environ.pop(_k, None)
os.environ["NO_PROXY"] = "127.0.0.1,localhost,::1"
os.environ.pop("no_proxy", None)

PRESETS = {f"voice_{i:02d}": f"examples/voice_{i:02d}.wav" for i in range(1, 13)}


def resolve(path_like: str) -> Path:
    preset = PRESETS.get(path_like if path_like in PRESETS else Path(path_like).stem)
    p = Path(preset) if preset else Path(path_like)
    if not p.is_absolute():
        p = ROOT / p
    if not p.exists():
        example = ROOT / "examples" / f"{Path(path_like).stem}.wav"
        if example.exists():
            return example
        raise SystemExit(f"找不到音频：{p}")
    return p


def webui_alive(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description="IndexTTS WebUI 轻量客户端")
    ap.add_argument("--text", default="", help="单条文本")
    ap.add_argument("--script", default="", help="批量文本，一行一段")
    ap.add_argument("--out", default="", help="单条输出路径")
    ap.add_argument("--out-dir", default="", help="批量输出目录")
    ap.add_argument("--voice", default="voice_01")
    ap.add_argument("--lang", default="ZH", choices=["ZH", "EN", "JA", "ES", "AR"])
    ap.add_argument("--speed", type=float, default=0.0, help="1.0 正常，>1 更快")
    ap.add_argument("--duration-factor", type=float, default=0.0, help="0.5~2.0，>1 变慢")
    ap.add_argument("--emo-audio", default="")
    ap.add_argument("--emo-alpha", type=float, default=1.0)
    ap.add_argument("--emo-vector", default="", help="8 个逗号分隔数值")
    ap.add_argument("--max-tokens", type=int, default=120)
    ap.add_argument("--url", default="http://127.0.0.1:7860")
    args = ap.parse_args()

    if not args.text and not args.script:
        raise SystemExit("给 --text 或 --script")

    host_port = args.url.split("//")[-1].split("/")[0]
    host, _, port = host_port.partition(":")
    if not webui_alive(host, int(port or 80)):
        raise SystemExit(f"{args.url} 上没有 WebUI，先跑 tools\\start_webui.ps1")

    from gradio_client import Client, handle_file

    voice = resolve(args.voice)
    emo = resolve(args.emo_audio) if args.emo_audio else None
    if args.duration_factor:
        factor = args.duration_factor
    elif args.speed:
        factor = round(1.0 / max(args.speed, 0.01), 3)
    else:
        factor = 1.0
    vector = [float(x) for x in args.emo_vector.split(",")] if args.emo_vector else [0] * 8

    tasks = []
    if args.text:
        tasks.append((args.text, Path(args.out) if args.out else ROOT / "outputs" / "client_out.wav"))
    if args.script:
        script_path = Path(args.script)
        if not script_path.is_absolute():
            script_path = ROOT / script_path
        out_dir = Path(args.out_dir) if args.out_dir else ROOT / "outputs" / "client_batch"
        if not out_dir.is_absolute():
            out_dir = ROOT / out_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        idx = 0
        for raw in script_path.read_text(encoding="utf-8-sig").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            idx += 1
            safe = "".join(c for c in line[:12] if c not in '\\/:*?"<>|').strip().replace(" ", "_")
            tasks.append((line, out_dir / f"{idx:04d}_{safe}.wav"))

    client = Client(args.url, verbose=False)
    ok = 0
    for text, dst in tasks:
        try:
            result = client.predict(
                "使用情感参考音频" if emo else ("使用情感向量控制" if args.emo_vector else "与音色参考音频相同"),
                handle_file(str(voice)),
                text,
                args.lang,
                handle_file(str(emo)) if emo else None,
                args.emo_alpha,
                *vector,
                "",
                False,
                args.max_tokens,
                factor,
                True, 0.8, 30, 0.8, 0, 3, 10, 1500,
                api_name="/gen_single",
            )
            src = result.get("value") if isinstance(result, dict) else result
            dst = dst if dst.is_absolute() else ROOT / dst
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
            ok += 1
            print(f"OK  {dst}")
        except Exception as exc:
            print(f"失败：{text[:20]}... -> {exc}")
    print(f"完成 {ok}/{len(tasks)}")
    return 0 if ok == len(tasks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
