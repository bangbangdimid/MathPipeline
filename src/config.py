"""Load YAML config with safe fallbacks (so smoke tests run without pyyaml)."""
from __future__ import annotations
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_DEFAULTS = {
    "paths": {"db": "data/math.db", "renders": "data/renders",
              "quarantine": "data/quarantine", "chuyende": "data/chuyende",
              "schema": "db/schema.sql"},
    "tools": {"xelatex": "xelatex"},
    "ocr": {"engine": "gemini", "workers": 6, "render_dpi": 200,
            "text_layer_min_cpp": 200, "scan_max_cpp": 40, "sample_pages": 6},
    "scalpel": {"provider": "gemini", "enabled": False},
    "dedup": {"minhash_threshold": 0.85, "cosine_threshold": 0.90},
}


def _load_yaml(path):
    try:
        import yaml  # type: ignore
        with open(path, encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except Exception:
        return None


def load_settings():
    data = _load_yaml(os.path.join(ROOT, "config", "settings.yaml"))
    return data if data else _DEFAULTS


def load_providers():
    data = _load_yaml(os.path.join(ROOT, "config", "providers.yaml"))
    return data if data else {"providers": {}, "routing": {}}


def abspath(rel: str) -> str:
    return rel if os.path.isabs(rel) else os.path.join(ROOT, rel)
