"""Provider abstraction: swappable LLM/VLM backends, quota-aware.

No vendor lock-in — each provider is a plugin loaded from config/providers.yaml.
Keys come from environment variables named in that file (never stored in repo).
"""
from __future__ import annotations
import abc
import os


class LLMProvider(abc.ABC):
    name: str = "base"

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.api_key = os.environ.get(cfg.get("env_key", ""), None)

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    @abc.abstractmethod
    def complete(self, prompt: str, images: list[str] | None = None) -> str:
        ...

    def vlm_to_latex(self, image_path: str) -> str:
        raise NotImplementedError


class GeminiProvider(LLMProvider):
    name = "gemini"

    def complete(self, prompt: str, images=None) -> str:
        # Implemented when a key is configured (google-generativeai).
        raise NotImplementedError("Configure GEMINI_API_KEY and install google-generativeai")

    def vlm_to_latex(self, image_path: str) -> str:
        raise NotImplementedError("Configure GEMINI_API_KEY and install google-generativeai")


_REGISTRY = {"gemini": GeminiProvider}


def load_provider(name: str, providers_cfg: dict):
    pc = providers_cfg.get("providers", {}).get(name)
    if pc is None or name not in _REGISTRY:
        return None
    return _REGISTRY[name](pc)
