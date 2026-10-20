"""Provider abstraction: swappable LLM/VLM backends, quota-aware.

No vendor lock-in — each provider is a plugin loaded from config/providers.yaml.
API keys come ONLY from environment variables named in that file (never stored
in the repo, never passed in plaintext through chat).
"""
from __future__ import annotations
import abc
import os

_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
         ".webp": "image/webp"}


def _mime_of(path: str) -> str:
    return _MIME.get(os.path.splitext(path)[1].lower(), "image/png")


class LLMProvider(abc.ABC):
    name = "base"

    def __init__(self, cfg: dict):
        self.cfg = cfg or {}
        self.api_key = os.environ.get(self.cfg.get("env_key", ""), None)

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    @abc.abstractmethod
    def complete(self, prompt: str, images: list[str] | None = None) -> str:
        ...

    def vlm_to_latex(self, image_path: str) -> str:
        raise NotImplementedError


_SCALPEL_PROMPT = (
    "Đây là một mảnh đề/bài toán Toán THCS tiếng Việt (ảnh scan). "
    "Hãy phiên âm CHÍNH XÁC sang LaTeX: giữ đúng dấu tiếng Việt; bọc mọi biểu thức "
    "toán trong $...$ (inline) hoặc \\[...\\] (hiển thị); giữ đúng thứ tự đọc và các ý a), b), c). "
    "CHỈ trả về phần thân LaTeX, không giải thích, không khối mã (```)."
)


_QUOTA_MARKERS = ("429", "RESOURCE_EXHAUSTED", "quota", "exhausted", "RateLimit",
                  "rate limit", "RESOURCE_EXHAUSTED")


class GeminiProvider(LLMProvider):
    name = "gemini"

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        from src.providers.keyring import load_keys, KeyRotator
        self.rotator = KeyRotator(load_keys())

    @property
    def available(self) -> bool:
        return len(self.rotator) > 0

    def _model(self) -> str:
        models = self.cfg.get("models") or ["gemini-flash-latest"]
        return models[0]

    def complete(self, prompt: str, images: list[str] | None = None) -> str:
        if not self.available:
            raise RuntimeError("No Gemini API keys configured "
                               "(GEMINI_API_KEYS / config/gemini_keys.txt / GEMINI_API_KEY)")
        import time
        from google import genai
        from google.genai import types
        contents = []
        for p in (images or []):
            with open(p, "rb") as fh:
                contents.append(types.Part.from_bytes(data=fh.read(), mime_type=_mime_of(p)))
        contents.append(prompt)

        last_err = None
        slept = 0.0
        for _ in range(len(self.rotator) * 2 + 8):
            picked = self.rotator.pick()
            if picked is None:                      # all keys cooling down
                if slept >= 45:
                    break
                w = min(max(self.rotator.wait_hint(), 1.0), 15.0)
                time.sleep(w)
                slept += w
                continue
            idx, key = picked
            try:
                client = genai.Client(api_key=key)
                resp = client.models.generate_content(model=self._model(), contents=contents)
                return (resp.text or "").strip()
            except Exception as e:
                last_err = e
                m = str(e)
                self.rotator.penalize(idx, 60 if any(q in m for q in _QUOTA_MARKERS) else 20)
                continue
        raise RuntimeError(f"Gemini hết lượt (đã chờ {slept:.0f}s). Lỗi cuối: {last_err}")

    def vlm_to_latex(self, image_path: str) -> str:
        out = self.complete(_SCALPEL_PROMPT, images=[image_path])
        return out.strip().strip("`").removeprefix("latex").strip()


_REGISTRY = {"gemini": GeminiProvider}


def load_provider(name: str, providers_cfg: dict):
    pc = (providers_cfg.get("providers", {}) or {}).get(name)
    if pc is None or name not in _REGISTRY:
        return None
    return _REGISTRY[name](pc)


def get_active_provider(providers_cfg: dict, name: str = "gemini"):
    """Return a ready provider (key present) or None."""
    p = load_provider(name, providers_cfg)
    return p if (p and p.available) else None
