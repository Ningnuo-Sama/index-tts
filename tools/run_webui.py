"""IndexTTS WebUI 启动器：进程内设置环境，再跑 webui.py。

为什么不用 PowerShell 设环境变量：本机系统 NO_PROXY 含 `[::1]`，httpx 解析方括号
IPv6 会抛 InvalidURL: Invalid port ':1]'，gradio 起不来；而 csrss 层注入的环境变量
在本机 pwsh 里未必能覆盖到子进程。直接在 Python 进程内改写 os.environ 最可靠。

默认直连（不消耗代理流量）：PyPI 走阿里云镜像、权重走 ModelScope、示例音频走
hf-mirror。留 --proxy 开关给确实需要出墙的场景。
"""

import os
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    passthrough = sys.argv[1:]
    use_proxy = "--proxy" in passthrough
    if use_proxy:
        passthrough.remove("--proxy")
        for key in ("http_proxy", "https_proxy", "no_proxy"):
            if os.environ.get(key.upper()):
                os.environ[key] = os.environ[key.upper()]
    else:
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
            os.environ.pop(key, None)
        # 去掉方括号形式的 [::1]，否则 httpx 建 Client 时解析端口失败
        os.environ["NO_PROXY"] = "127.0.0.1,localhost,::1"
        os.environ.pop("no_proxy", None)

    # --semantic-cpu：把 w2v-bert 特征提取器放 CPU，显存省约 2 GB，让 Ollama 能留在 GPU
    if "--semantic-cpu" in passthrough:
        passthrough.remove("--semantic-cpu")
        os.environ["INDEXTTS_SEMANTIC_DEVICE"] = "cpu"

    os.environ.setdefault("USE_MODELSCOPE", "true")  # 示例音频走 hf-mirror
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    os.environ.setdefault("GRADIO_ANALYTICS_ENABLED", "False")
    os.environ.setdefault("GRADIO_ANALYTICS_ALLOWED", "False")
    os.environ.setdefault("PYTHONPATH", str(ROOT))
    # 减少显存碎片，本机显存被 Ollama 等常驻进程挤压时更稳
    os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    sys.argv = ["webui.py", *passthrough]
    runpy.run_path(str(ROOT / "webui.py"), run_name="__main__")


if __name__ == "__main__":
    main()
