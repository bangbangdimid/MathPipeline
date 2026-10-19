"""Gemini scalpel: whole-element structured OCR for blocks the local hybrid
cannot stitch reliably (dense inline formulas, low confidence, stylized headers).

This is the ONLY place cloud quota is spent on OCR. Interface is defined now;
wiring a real key happens when scalpel.enabled=true in settings.yaml.
"""
from __future__ import annotations


class ScalpelNotConfigured(RuntimeError):
    pass


def scalpel_element(image_crop_path: str, provider=None) -> str:
    """Send a cropped element image to a free VLM; return LaTeX for the whole block.

    Prompt contract (to implement when a key is available):
      "Transcribe this Vietnamese math exam fragment to LaTeX. Keep Vietnamese
       diacritics. Wrap math in $...$. Preserve reading order. Output LaTeX only."
    """
    if provider is None:
        raise ScalpelNotConfigured(
            "Scalpel not configured. Set scalpel.enabled=true and a GEMINI_API_KEY, "
            "then pass a provider from src.providers.")
    return provider.vlm_to_latex(image_crop_path)
