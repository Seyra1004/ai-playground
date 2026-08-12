-- =========================================================================
-- Phase 1A (unchanged, except delivery_history gains a nullable report_id
-- FK — see the "Phase 2 DB Foundation" section below for why).
-- =========================================================================

CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL UNIQUE,
  run_date TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT NOT NULL,
  failure_stage TEXT,
  notes TEXT
);

-- =========================================================================
-- Phase 2 DB Foundation (v5 architecture, approved/frozen).
-- Canonical flow:
--   SOURCE -> RAW -> NORMALIZED FACT -> OBSERVATION -> DERIVED SIGNAL
--   -> TREND SIGNAL -> MODEL INTERPRETATION -> FORECAST -> REPORT -> DELIVERY
-- Parallel operational metadata: RUN -> RUN_METADATA / RUN_SOURCE_STATUS /
-- RUN_CATEGORY_STATUS.
--
-- Created in FK-dependency order. delivery_history is intentionally placed
-- at the end of this file (after `reports` exists) since it now carries an
-- optional FK to reports — this is a DDL-ordering adjustment only, not a
-- semantic change (allowed under the v5 implementation contract).
-- =========================================================================

-- ---- music identity (platform-independent) -----------------------------

CREATE TABLE IF NOT EXISTS music_entities (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  canonical_artist TEXT,
  canonical_title TEXT,
  variant TEXT NOT NULL CHECK(variant IN
    ('ORIGINAL','REMIX','SPED_UP','SLOWED','MASHUP','LIVE','UNRELEASED_SOUND','UNKNOWN')),
  related_entity_id INTEGER REFERENCES music_entities(id) ON DELETE RESTRICT,
  resolution_status TEXT NOT NULL CHECK(resolution_status IN
    ('UNRESOLVED','PARTIALLY_RESOLVED','RESOLVED')),
  first_seen_at TEXT NOT NULL,
  first_seen_source TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS music_entity_aliases (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  music_entity_id INTEGER NOT NULL REFERENCES music_entities(id) ON DELETE CASCADE,
  alias_type TEXT NOT NULL CHECK(alias_type IN
    ('SPOTIFY_ID','SPOTIFY_URL','ISRC','YOUTUBE_VIDEO_ID','TIKTOK_SOUND_LABEL','ALTERNATE_TITLE','NEWS_MENTION_LABEL','APPLE_MUSIC_ID')),
  alias_value TEXT NOT NULL,
  source_name TEXT NOT NULL,
  confirmed_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_music_entity_alias
  ON music_entity_aliases(alias_type, alias_value);

-- ---- trend identity (platform-independent) ------------------------------

CREATE TABLE IF NOT EXISTS trend_entities (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  trend_type TEXT NOT NULL CHECK(trend_type IN ('GENRE','PRODUCTION_STYLE')),
  trend_key TEXT NOT NULL,
  label TEXT NOT NULL,
  first_seen_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_trend_entity
  ON trend_entities(trend_type, trend_key);

-- ---- RAW -----------------------------------------------------------------

CREATE TABLE IF NOT EXISTS raw_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_name TEXT NOT NULL,
  source_item_key TEXT NOT NULL,
  source_type TEXT NOT NULL,
  source_url TEXT NOT NULL,
  title TEXT,
  snippet TEXT,
  published_at TEXT,
  collected_at TEXT NOT NULL,
  region TEXT,
  -- Ingestion-time snapshot of SourceConfig.category (Category Provenance
  -- Correction). NULL only for legacy rows collected before this column
  -- existed; normalization falls back to a live registry lookup for those
  -- only — never for rows that already have a category (see
  -- ingestion/normalize.py). This is what keeps a later sources.yaml edit
  -- from silently changing an already-collected item's classification.
  category TEXT,
  payload_hash TEXT,
  extra_json TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_raw_item_identity
  ON raw_items(source_name, source_item_key);

-- ---- NORMALIZED FACT -------------------------------------------------------

CREATE TABLE IF NOT EXISTS normalized_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  raw_item_id INTEGER NOT NULL REFERENCES raw_items(id) ON DELETE RESTRICT,
  category TEXT NOT NULL,
  event_key TEXT NOT NULL,
  entity_type TEXT,
  entity_name TEXT,
  normalized_title TEXT NOT NULL,
  language TEXT,
  resolved_music_entity_id INTEGER REFERENCES music_entities(id) ON DELETE RESTRICT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_normalized_event_key ON normalized_items(event_key);

-- ---- OBSERVATION -----------------------------------------------------------

CREATE TABLE IF NOT EXISTS music_observations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  music_entity_id INTEGER NOT NULL REFERENCES music_entities(id) ON DELETE RESTRICT,
  raw_item_id INTEGER REFERENCES raw_items(id) ON DELETE RESTRICT,
  source_name TEXT NOT NULL,
  metric_name TEXT NOT NULL,
  metric_value REAL NOT NULL CHECK(metric_value >= 0),
  unit TEXT NOT NULL,
  region TEXT NOT NULL,
  evidence_type TEXT NOT NULL CHECK(evidence_type IN
    ('MEASURED_PLATFORM_SIGNAL','REPORTED_PLATFORM_SIGNAL')),
  observed_at TEXT NOT NULL,
  collected_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_observation
  ON music_observations(music_entity_id, source_name, metric_name, region, observed_at);
CREATE INDEX IF NOT EXISTS idx_observation_entity_time
  ON music_observations(music_entity_id, observed_at);

-- ---- DERIVED SIGNAL (entity level) -----------------------------------------

CREATE TABLE IF NOT EXISTS derived_signals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  music_entity_id INTEGER NOT NULL REFERENCES music_entities(id) ON DELETE RESTRICT,
  signal_type TEXT NOT NULL CHECK(signal_type IN
    ('VELOCITY','ACCELERATION','PERSISTENCE','CROSS_PLATFORM_CONFIRMATION','SOURCE_BREADTH','REGION_BREADTH','MEDIA_MOMENTUM')),
  period_start TEXT NOT NULL,
  period_end TEXT NOT NULL,
  value REAL NOT NULL,
  unit TEXT NOT NULL,
  computed_at TEXT NOT NULL,
  method_version TEXT NOT NULL,
  CHECK(period_start <= period_end)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_derived_signal
  ON derived_signals(music_entity_id, signal_type, period_start, period_end, method_version);
CREATE INDEX IF NOT EXISTS idx_derived_signal_entity
  ON derived_signals(music_entity_id, signal_type, period_start);

-- ---- MODEL INTERPRETATION --------------------------------------------------

CREATE TABLE IF NOT EXISTS llm_interpretations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE RESTRICT,
  category TEXT NOT NULL,
  model_used TEXT NOT NULL,
  prompt_version TEXT NOT NULL,
  input_hash TEXT,
  input_tokens INTEGER,
  output_tokens INTEGER,
  estimated_cost REAL,
  output_text TEXT NOT NULL,
  evidence_type TEXT NOT NULL DEFAULT 'MODEL_INFERENCE' CHECK(evidence_type = 'MODEL_INFERENCE'),
  confidence TEXT NOT NULL CHECK(confidence IN ('LOW','MEDIUM','HIGH')),
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_interpretation_input_hash ON llm_interpretations(input_hash);

-- ---- music <-> trend classification (HISTORY, not current-state) ----------

CREATE TABLE IF NOT EXISTS music_trend_links (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  music_entity_id INTEGER NOT NULL REFERENCES music_entities(id) ON DELETE RESTRICT,
  trend_entity_id INTEGER NOT NULL REFERENCES trend_entities(id) ON DELETE RESTRICT,
  interpretation_id INTEGER NOT NULL REFERENCES llm_interpretations(id) ON DELETE RESTRICT,
  confidence TEXT NOT NULL CHECK(confidence IN ('LOW','MEDIUM','HIGH')),
  created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_music_trend_link
  ON music_trend_links(music_entity_id, trend_entity_id, interpretation_id);

-- ---- TREND SIGNAL (genre/style level) --------------------------------------

CREATE TABLE IF NOT EXISTS trend_signals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  trend_entity_id INTEGER NOT NULL REFERENCES trend_entities(id) ON DELETE RESTRICT,
  signal_type TEXT NOT NULL CHECK(signal_type IN
    ('VELOCITY','ACCELERATION','PERSISTENCE','CROSS_PLATFORM_CONFIRMATION','SOURCE_BREADTH','REGION_BREADTH','MEDIA_MOMENTUM')),
  period_start TEXT NOT NULL,
  period_end TEXT NOT NULL,
  value REAL NOT NULL,
  unit TEXT NOT NULL,
  computed_at TEXT NOT NULL,
  method_version TEXT NOT NULL,
  CHECK(period_start <= period_end)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_trend_signal
  ON trend_signals(trend_entity_id, signal_type, period_start, period_end, method_version);
CREATE INDEX IF NOT EXISTS idx_trend_signal_entity
  ON trend_signals(trend_entity_id, signal_type, period_start);

-- ---- Interpretation provenance (typed junctions, no polymorphic FK) -------

CREATE TABLE IF NOT EXISTS interpretation_items (
  interpretation_id INTEGER NOT NULL REFERENCES llm_interpretations(id) ON DELETE CASCADE,
  normalized_item_id INTEGER NOT NULL REFERENCES normalized_items(id) ON DELETE RESTRICT,
  PRIMARY KEY (interpretation_id, normalized_item_id)
);
CREATE INDEX IF NOT EXISTS idx_interp_items_item ON interpretation_items(normalized_item_id);

CREATE TABLE IF NOT EXISTS interpretation_observations (
  interpretation_id INTEGER NOT NULL REFERENCES llm_interpretations(id) ON DELETE CASCADE,
  observation_id INTEGER NOT NULL REFERENCES music_observations(id) ON DELETE RESTRICT,
  PRIMARY KEY (interpretation_id, observation_id)
);
CREATE INDEX IF NOT EXISTS idx_interp_obs_obs ON interpretation_observations(observation_id);

CREATE TABLE IF NOT EXISTS interpretation_signals (
  interpretation_id INTEGER NOT NULL REFERENCES llm_interpretations(id) ON DELETE CASCADE,
  derived_signal_id INTEGER NOT NULL REFERENCES derived_signals(id) ON DELETE RESTRICT,
  PRIMARY KEY (interpretation_id, derived_signal_id)
);
CREATE INDEX IF NOT EXISTS idx_interp_sig_sig ON interpretation_signals(derived_signal_id);

CREATE TABLE IF NOT EXISTS interpretation_trend_signals (
  interpretation_id INTEGER NOT NULL REFERENCES llm_interpretations(id) ON DELETE CASCADE,
  trend_signal_id INTEGER NOT NULL REFERENCES trend_signals(id) ON DELETE RESTRICT,
  PRIMARY KEY (interpretation_id, trend_signal_id)
);
CREATE INDEX IF NOT EXISTS idx_interp_trend_sig ON interpretation_trend_signals(trend_signal_id);

-- ---- REPORT (immutable per-run output) -------------------------------------

CREATE TABLE IF NOT EXISTS reports (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE RESTRICT,
  report_date TEXT NOT NULL,
  report_type TEXT NOT NULL,
  category TEXT NOT NULL,
  content TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  generated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_report_run_type ON reports(run_id, report_type);
CREATE INDEX IF NOT EXISTS idx_reports_run ON reports(run_id);

-- ---- operational metadata ---------------------------------------------------

CREATE TABLE IF NOT EXISTS run_source_status (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE RESTRICT,
  category TEXT NOT NULL,
  source_name TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('SUCCESS','FAILED','PARTIAL','SKIPPED')),
  started_at TEXT NOT NULL,
  finished_at TEXT,
  items_collected INTEGER NOT NULL DEFAULT 0 CHECK(items_collected >= 0),
  retry_count INTEGER NOT NULL DEFAULT 0 CHECK(retry_count >= 0),
  failure_reason TEXT,
  CHECK(finished_at IS NULL OR finished_at >= started_at)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_run_source_status
  ON run_source_status(run_id, category, source_name);
CREATE INDEX IF NOT EXISTS idx_run_source_status_run ON run_source_status(run_id);

CREATE TABLE IF NOT EXISTS monthly_forecasts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE RESTRICT,
  interpretation_id INTEGER REFERENCES llm_interpretations(id) ON DELETE RESTRICT,
  trend_entity_id INTEGER NOT NULL REFERENCES trend_entities(id) ON DELETE RESTRICT,
  forecast_cycle TEXT NOT NULL,
  forecast_created_at TEXT NOT NULL,
  forecast_for_start TEXT NOT NULL,
  forecast_for_end TEXT NOT NULL,
  prediction_direction TEXT NOT NULL CHECK(prediction_direction IN ('RISING','STABLE','DECLINING')),
  confidence TEXT NOT NULL CHECK(confidence IN ('LOW','MEDIUM','HIGH')),
  confidence_basis TEXT,
  prediction_text TEXT,
  contrary_evidence_text TEXT,
  target_metric TEXT,
  baseline_period_start TEXT,
  baseline_period_end TEXT,
  baseline_value REAL,
  CHECK(forecast_for_start <= forecast_for_end),
  CHECK((baseline_period_start IS NULL) = (baseline_period_end IS NULL)),
  CHECK(baseline_period_start IS NULL OR baseline_period_start <= baseline_period_end),
  CHECK(baseline_value IS NULL OR target_metric IS NOT NULL),
  CHECK(baseline_value IS NULL OR baseline_period_start IS NOT NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_forecast_run_target
  ON monthly_forecasts(run_id, trend_entity_id, forecast_for_start, forecast_for_end);
CREATE INDEX IF NOT EXISTS idx_forecast_trend ON monthly_forecasts(trend_entity_id);

CREATE TABLE IF NOT EXISTS run_category_status (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE RESTRICT,
  category TEXT NOT NULL CHECK(category IN
    ('TIKTOK','SPOTIFY','AI','ECONOMY','SOCIETY','MONTHLY_FORECAST','MUSIC')),
  status TEXT NOT NULL CHECK(status IN ('REPORT_GENERATED','REPORT_FAILED','NOT_READY')),
  failure_stage TEXT CHECK(failure_stage IN ('SOURCE','NORMALIZATION','SIGNAL','LLM','REPORT') OR failure_stage IS NULL),
  report_id INTEGER REFERENCES reports(id) ON DELETE RESTRICT,
  items_collected INTEGER,
  items_rejected INTEGER,
  items_selected INTEGER,
  failure_reason TEXT,
  retry_count INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_run_category_status ON run_category_status(run_id, category);

CREATE TABLE IF NOT EXISTS run_metadata (
  run_id INTEGER PRIMARY KEY REFERENCES runs(id) ON DELETE RESTRICT,
  source_registry_hash TEXT,
  created_at TEXT NOT NULL
);

-- ---- Phase 1A delivery_history (defined last: carries the new nullable
-- FK to `reports`, which must exist first for a clean, unambiguous DDL
-- read order — this is the one allowed ordering adjustment; every other
-- column and the existing partial UNIQUE index are byte-for-byte unchanged
-- from Phase 1A). ------------------------------------------------------------

CREATE TABLE IF NOT EXISTS delivery_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER NOT NULL REFERENCES runs(id),
  report_date TEXT NOT NULL,
  report_type TEXT NOT NULL,
  destination TEXT NOT NULL,
  idempotency_key TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  delivered_at TEXT,
  status TEXT NOT NULL,
  report_id INTEGER REFERENCES reports(id) ON DELETE RESTRICT
);

-- Only one successful send per idempotency_key; failed/skipped attempts don't
-- occupy the slot, so a failed delivery can be retried.
CREATE UNIQUE INDEX IF NOT EXISTS ux_delivery_sent_once
  ON delivery_history(idempotency_key)
  WHERE status = 'sent';
