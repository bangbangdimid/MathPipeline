# MathPipeline (MVP)

Multi-agent system to OCR → classify → compose verified math *chuyên đề* from a
~8,000-PDF THCS corpus. Fully local & free for the bulk; cloud (Gemini free) only
as a scalpel for blocks the local engines can't stitch.

## Status
Ingest slice scaffolded. OCR (Pix2Text formulas + EasyOCR-vi prose, merged by
layout) validated on real khoi_9 scans. Schema = Phase D. Workflow = Phase E.

## Setup (Windows)
```bash
python -m pip install -r requirements.txt
python scripts/init_db.py                      # create data/math.db from db/schema.sql
python -m src.pipeline ingest "C:\path\to\de.pdf" --grade khoi_9 --max-ocr-pages 2
```
OCR is CPU-only here (~1–2 min/page). `--max-ocr-pages 0` processes all pages;
the rest stay `ocr_status='pending'` for a later batch run.

## Layout
```
config/     providers.yaml (LLM registry+quota), settings.yaml (paths/thresholds)
db/         schema.sql  (Phase D: documents→pages→elements, problems, solutions,
                         classifications, KG, dedup, chuyen_de, verification,
                         provenance, feedback/rules, jobs, api_usage)
src/
  config.py db.py pipeline.py
  ingest/   triage.py  ocr_hybrid.py (pix2text+easyocr merge)  scalpel.py
  providers/base.py  (swappable LLM backends; keys from env vars)
scripts/    init_db.py
data/       math.db, renders/, quarantine/
```

## Key design facts (see memory `project_ai_math_research_pipeline`)
- Local hybrid OCR: Pix2Text = formulas→LaTeX (near-perfect), EasyOCR-vi = prose.
- Boundary: dense multi-inline-formula blocks → `needs_scalpel=1` → Gemini, not
  stitched locally.
- Verification status is explicit per solution; "many LLMs agree" ≠ proof.
- Learning loop: feedback → feedback_rules → injected into compose prompts (no
  fine-tuning; respects the 0đ constraint).

## Not yet wired (next)
Gemini scalpel key; problem segmentation; embeddings+dedup; classification;
compose+verify; review dashboard. Tests. See Phase E for the full workflow.
