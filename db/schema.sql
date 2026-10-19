-- MathPipeline — schema (Phase D). SQLite system-of-record.
-- Vectors live in LanceDB (keyed by problem_id); blobs on filesystem.
PRAGMA foreign_keys = ON;

-- ============ ① Ingestion & OCR ============
CREATE TABLE IF NOT EXISTS documents (
  id            INTEGER PRIMARY KEY,
  sha256        TEXT UNIQUE NOT NULL,
  pdf_path      TEXT NOT NULL,
  source_url    TEXT,
  title         TEXT,
  grade         TEXT,            -- khoi_7/8/9/chung/tuyen_sinh_10
  category      TEXT,
  doc_type      TEXT,
  num_pages     INTEGER,
  source_type   TEXT,            -- text | scan | mixed
  license_flag  TEXT DEFAULT 'internal_only',
  ocr_status    TEXT DEFAULT 'pending',   -- pending|partial|done|failed
  bytes         INTEGER,
  downloaded_at TEXT,
  ingested_at   TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS pages (
  id            INTEGER PRIMARY KEY,
  document_id   INTEGER NOT NULL REFERENCES documents(id),
  page_no       INTEGER NOT NULL,
  kind          TEXT,            -- text|scan|mixed
  image_path    TEXT,
  text_chars    INTEGER,
  ocr_engine    TEXT,            -- native|pix2text+easyocr|gemini
  ocr_status    TEXT DEFAULT 'pending', -- pending|local_done|scalpel_done|failed|needs_review
  latex         TEXT,            -- reconstructed page LaTeX
  min_conf      REAL,
  needs_review  INTEGER DEFAULT 0,
  processed_at  TEXT,
  UNIQUE(document_id, page_no)
);

CREATE TABLE IF NOT EXISTS page_elements (
  id            INTEGER PRIMARY KEY,
  page_id       INTEGER NOT NULL REFERENCES pages(id),
  idx           INTEGER,
  el_type       TEXT,            -- title|text|formula|figure
  bbox          TEXT,            -- json [x0,y0,x1,y1]
  content       TEXT,            -- latex | prose
  source_engine TEXT,            -- pix2text|easyocr|gemini
  confidence    REAL,
  needs_scalpel INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS figures (
  id            INTEGER PRIMARY KEY,
  page_id       INTEGER REFERENCES pages(id),
  bbox          TEXT,
  image_path    TEXT,
  tikz          TEXT,
  recon_status  TEXT DEFAULT 'none'  -- none|drafted|verified
);

-- ============ ② Problem / Solution / Classification ============
CREATE TABLE IF NOT EXISTS problems (
  id              INTEGER PRIMARY KEY,
  statement_latex TEXT NOT NULL,
  hypotheses      TEXT,
  conclusion      TEXT,
  grade_level     TEXT,
  has_figure      INTEGER DEFAULT 0,
  canonical_hash  TEXT,
  embedding_id    TEXT,
  extracted_by    TEXT,
  extraction_conf REAL,
  status          TEXT DEFAULT 'extracted', -- extracted|classified|in_compilation|published|flagged
  created_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS solutions (
  id             INTEGER PRIMARY KEY,
  problem_id     INTEGER NOT NULL REFERENCES problems(id),
  approach_name  TEXT,
  solution_latex TEXT NOT NULL,
  method_tags    TEXT,      -- json
  is_primary     INTEGER DEFAULT 0,
  origin         TEXT,      -- original|ai_generated|ported
  created_at     TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS taxonomy (
  id         INTEGER PRIMARY KEY,
  tier       INTEGER,       -- 1 linh_vuc .. 4 ky_thuat_tu_duy
  parent_id  INTEGER REFERENCES taxonomy(id),
  name       TEXT,
  description TEXT
);

CREATE TABLE IF NOT EXISTS classifications (
  id          INTEGER PRIMARY KEY,
  problem_id  INTEGER REFERENCES problems(id),
  tier        INTEGER,
  taxonomy_id INTEGER REFERENCES taxonomy(id),
  label       TEXT,
  confidence  REAL,
  assigned_by TEXT,         -- ai|human
  confirmed   INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS difficulty (
  problem_id  INTEGER PRIMARY KEY REFERENCES problems(id),
  score       INTEGER,      -- 1..5
  rubric      TEXT,         -- json: prereq, depth, nonobvious_steps, creativity
  assigned_by TEXT
);

-- ============ ③ Knowledge graph + Dedup ============
CREATE TABLE IF NOT EXISTS techniques (
  id        INTEGER PRIMARY KEY,
  node_type TEXT,           -- technique|theorem|lemma|definition|method
  name      TEXT,
  statement_latex TEXT,
  domain    TEXT
);
CREATE TABLE IF NOT EXISTS problem_techniques (
  problem_id   INTEGER REFERENCES problems(id),
  technique_id INTEGER REFERENCES techniques(id),
  PRIMARY KEY(problem_id, technique_id)
);
CREATE TABLE IF NOT EXISTS kg_edges (
  id       INTEGER PRIMARY KEY,
  src_type TEXT, src_id INTEGER, dst_type TEXT, dst_id INTEGER,
  relation TEXT,            -- uses|prerequisite|alternative_to|generalizes|same_essence|co_occurs
  weight   REAL, evidence TEXT
);
CREATE TABLE IF NOT EXISTS duplicates (
  id        INTEGER PRIMARY KEY,
  problem_a INTEGER REFERENCES problems(id),
  problem_b INTEGER REFERENCES problems(id),
  similarity REAL, method TEXT,  -- embedding|minhash
  verdict   TEXT DEFAULT 'suspected'  -- suspected|confirmed_dup|variant|distinct
);

-- ============ ④ Compilation (chuyên đề) + versioning ============
CREATE TABLE IF NOT EXISTS style_profiles (
  id         INTEGER PRIMARY KEY,
  name       TEXT,
  rules_json TEXT,
  sample_paths TEXT,
  created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS chuyen_de (
  id              INTEGER PRIMARY KEY,
  title           TEXT, grade TEXT,
  style_profile_id INTEGER REFERENCES style_profiles(id),
  status          TEXT DEFAULT 'draft',  -- draft|in_review|needs_rewrite|published
  current_version INTEGER DEFAULT 1,
  created_at      TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS chuyen_de_versions (
  id            INTEGER PRIMARY KEY,
  chuyen_de_id  INTEGER REFERENCES chuyen_de(id),
  version       INTEGER,
  content_latex TEXT, pdf_path TEXT, docx_path TEXT,
  quality_score REAL, change_note TEXT,
  created_at    TEXT DEFAULT (datetime('now')),
  UNIQUE(chuyen_de_id, version)
);
CREATE TABLE IF NOT EXISTS chuyen_de_problems (
  chuyen_de_id INTEGER REFERENCES chuyen_de(id),
  problem_id   INTEGER REFERENCES problems(id),
  position     INTEGER,
  role         TEXT       -- example|exercise|challenge
);

-- ============ ⑤ Verification + Provenance ============
CREATE TABLE IF NOT EXISTS verification (
  id          INTEGER PRIMARY KEY,
  solution_id INTEGER REFERENCES solutions(id),
  status      TEXT,  -- tool_verified|numerically_tested|ai_reviewed_only|needs_human|contradiction_found
  tool_used   TEXT,  -- sympy|numeric|bruteforce|none
  evidence    TEXT, checked_by TEXT,
  checked_at  TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS provenance (
  id          INTEGER PRIMARY KEY,
  problem_id  INTEGER REFERENCES problems(id),
  document_id INTEGER REFERENCES documents(id),
  page_no     INTEGER, bbox TEXT,
  source_name TEXT, source_url TEXT, confidence REAL
);

-- ============ ⑥ Feedback / Learning loop ============
CREATE TABLE IF NOT EXISTS feedback (
  id          INTEGER PRIMARY KEY,
  target_type TEXT, target_id INTEGER,  -- problem|solution|chuyen_de
  editor      TEXT, action TEXT,        -- approve|reject|edit|rewrite
  before_text TEXT, after_text TEXT, reason TEXT,
  created_at  TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS feedback_rules (
  id           INTEGER PRIMARY KEY,
  rule_text    TEXT, category TEXT,     -- style|math|pedagogy
  derived_from INTEGER REFERENCES feedback(id),
  active       INTEGER DEFAULT 1,
  created_at   TEXT DEFAULT (datetime('now'))
);

-- ============ ⑦ Orchestration (checkpoint, quota, cost) ============
CREATE TABLE IF NOT EXISTS jobs (
  id           INTEGER PRIMARY KEY,
  job_type     TEXT,   -- ocr_page|extract|classify|dedup|compile|verify|qc
  target_type  TEXT, target_id INTEGER,
  status       TEXT DEFAULT 'queued',   -- queued|running|done|failed|retry
  attempts     INTEGER DEFAULT 0, max_attempts INTEGER DEFAULT 4,
  provider     TEXT, tokens INTEGER, cost REAL, latency_ms INTEGER,
  error        TEXT, checkpoint TEXT,
  created_at   TEXT DEFAULT (datetime('now')), updated_at TEXT
);
CREATE TABLE IF NOT EXISTS api_usage (
  provider   TEXT, usage_date TEXT,
  requests   INTEGER DEFAULT 0, tokens INTEGER DEFAULT 0, errors INTEGER DEFAULT 0,
  quota_rpd  INTEGER,
  PRIMARY KEY(provider, usage_date)
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, job_type);
CREATE INDEX IF NOT EXISTS idx_pages_ocr   ON pages(ocr_status);
CREATE INDEX IF NOT EXISTS idx_prob_hash   ON problems(canonical_hash);
CREATE INDEX IF NOT EXISTS idx_prov_prob   ON provenance(problem_id);
