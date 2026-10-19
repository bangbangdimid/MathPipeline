"""LLM prose generation for a chuyên đề (Mục tiêu / dẫn nhập).

Injects active feedback_rules into the prompt (the learning loop) so the model
avoids repeating past mistakes. Returns plain Vietnamese prose.
"""
from __future__ import annotations
from src.feedback.store import rules_for_prompt


def generate_intro(provider, conn, title, grade, label) -> str:
    rules = rules_for_prompt(conn)
    prompt = (f"{rules}\n\n" if rules else "") + (
        f"Viết phần 'Mục tiêu và kiến thức tiên quyết' cho chuyên đề Toán '{title}' "
        f"(lĩnh vực: {label or 'tổng hợp'}, cấp: {grade or 'THCS'}). "
        "Khoảng 3-5 câu tiếng Việt, văn phong sư phạm, súc tích, nêu được mục tiêu học tập "
        "và kiến thức nền cần có. CHỈ trả về đoạn văn thuần (không tiêu đề, không markdown).")
    return provider.complete(prompt)
