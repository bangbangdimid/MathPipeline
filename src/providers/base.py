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
                  "rate limit")
# model is unusable (bad name / no access) -> cool it for a long time
_MODEL_DEAD = ("404", "NOT_FOUND", "not found", "does not exist", "not supported",
               "is not supported", "unsupported", "PERMISSION_DENIED")
# model temporarily overloaded / server hiccup -> cool it briefly
_MODEL_BUSY = ("503", "UNAVAILABLE", "overloaded", "overload", "500", "INTERNAL")


class GeminiProvider(LLMProvider):
    name = "gemini"

    def __init__(self, cfg: dict):
        super().__init__(cfg)
        from src.providers.keyring import load_keys, KeyRotator
        self.rotator = KeyRotator(load_keys())
        self._model_cool: dict[str, float] = {}   # model -> epoch available again
        self._clients: dict = {}                  # key -> reused genai.Client

    @property
    def available(self) -> bool:
        return len(self.rotator) > 0

    def _models(self) -> list[str]:
        env = os.environ.get("LLM_MODEL")
        if env:
            return [env.strip()]
        return self.cfg.get("models") or ["gemini-flash-latest"]

    def _client(self, key: str):
        """Reuse one Client per key — creating one per call exhausts connections
        ('Cannot send a request, as the client has been closed') and is slow."""
        c = self._clients.get(key)
        if c is None:
            from google import genai
            c = genai.Client(api_key=key)
            self._clients[key] = c
        return c

    def complete(self, prompt: str, images: list[str] | None = None) -> str:
        if not self.available:
            raise RuntimeError("No Gemini API keys configured "
                               "(GEMINI_API_KEYS / config/gemini_keys.txt / GEMINI_API_KEY)")
        import time
        from google.genai import types
        contents = []
        for p in (images or []):
            with open(p, "rb") as fh:
                contents.append(types.Part.from_bytes(data=fh.read(), mime_type=_mime_of(p)))
        contents.append(prompt)

        all_models = self._models()
        last_err = None
        slept = 0.0
        for _ in range(len(self.rotator) * len(all_models) + 12):
            now = time.time()
            avail_models = [m for m in all_models if self._model_cool.get(m, 0) <= now]
            if not avail_models:                       # every model cooling (rare)
                if slept >= 60:
                    break
                time.sleep(10); slept += 10
                continue
            picked = self.rotator.pick()
            if picked is None:                         # every key cooling
                if slept >= 60:
                    break
                w = min(max(self.rotator.wait_hint(), 1.0), 15.0)
                time.sleep(w); slept += w
                continue
            idx, key = picked
            client = self._client(key)
            quota_hit = False
            # Try each still-usable model with THIS key (models have separate quotas),
            # strongest first, before giving up on the key.
            for model in avail_models:
                try:
                    resp = client.models.generate_content(model=model, contents=contents)
                    return (resp.text or "").strip()
                except Exception as e:
                    last_err = e
                    em = str(e)
                    if "has been closed" in em:
                        self._clients.pop(key, None)                 # drop & recreate next use
                        client = self._client(key)
                        continue                                     # retry same model
                    if any(x in em for x in _MODEL_DEAD):
                        self._model_cool[model] = time.time() + 6 * 3600     # 6h
                    elif any(x in em for x in _MODEL_BUSY):
                        self._model_cool[model] = time.time() + 300          # 5 min
                    elif any(q in em for q in _QUOTA_MARKERS):
                        quota_hit = True                                     # try next model
                        self._model_cool[model] = time.time() + 120         # skip it briefly
                    else:
                        self.rotator.penalize(idx, 45)                       # transient -> next key
                        quota_hit = False
                        break
            else:
                # all available models failed for this key without a hard error
                if quota_hit:
                    self.rotator.penalize(idx, 300)                          # key rate-limited 5 min
        raise RuntimeError(
            f"Gemini hết lượt cho mọi model {all_models} (đã chờ {slept:.0f}s). Lỗi cuối: {last_err}")

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
