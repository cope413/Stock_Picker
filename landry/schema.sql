-- Landry System — SQLite schema (landry.db)
--
-- Replaces the flat-JSON persistence (landry_scores.json, landry_snapshot.json,
-- landry_actions.json) and the hand-edited workbook as the source of truth for
-- Landry operational data: approved scores, positions, watchlist state, drawdown
-- regime, correlations, journal, action items. Design rationale lives in
-- LANDRY_IMPLEMENTATION_PLAN.md and the schema-design plan this file implements.
--
-- Call PRAGMA foreign_keys = ON per-connection (SQLite defaults it off) — see
-- landry/db.py, which also applies this file on a fresh database.

PRAGMA foreign_keys = ON;

-- ============================================================= --
-- Identity & reference tables
-- ============================================================= --

CREATE TABLE users (
  username     TEXT PRIMARY KEY,
  display_name TEXT,
  active       INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
  created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE tickers (
  ticker         TEXT PRIMARY KEY,
  company        TEXT NOT NULL DEFAULT '',
  sector         TEXT,
  financial_kind TEXT CHECK (financial_kind IS NULL
                    OR financial_kind IN ('bank','insurer','broker','asset_manager')),
  active         INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
  added_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

-- makes the quant/judgment invariant structurally checkable (see trg below)
CREATE TABLE indicators (
  code         TEXT PRIMARY KEY,
  tier         INTEGER NOT NULL CHECK (tier IN (1,2,3)),
  weight       REAL NOT NULL CHECK (weight > 0 AND weight <= 1),
  display_name TEXT NOT NULL,
  kind         TEXT NOT NULL CHECK (kind IN ('quant','judgment','mixed')),
  sort_order   INTEGER NOT NULL
);

INSERT INTO indicators (code, tier, weight, display_name, kind, sort_order) VALUES
 ('fcf_yield_trend',            1, 0.20, 'FCF Yield & Trend',                         'mixed',    1),
 ('revenue_growth_consistency', 1, 0.15, 'Revenue Growth Consistency',                'quant',    2),
 ('competitive_moat',           1, 0.15, 'Competitive Moat Quality',                  'judgment', 3),
 ('revenue_visibility',         1, 0.10, 'Revenue Visibility (ARR/Backlog)',          'judgment', 4),
 ('fcf_margin_trend',           1, 0.10, 'FCF Margin Trend',                          'quant',    5),
 ('management_quality',         2, 0.08, 'Management Quality & Capital Allocation',   'judgment', 6),
 ('roic_vs_wacc',               2, 0.06, 'ROIC vs. WACC Spread',                      'quant',    7),
 ('relative_strength',          2, 0.03, 'Relative Strength vs. SPY (6-12 mo.)',      'quant',    8),
 ('valuation_multiples',        2, 0.08, 'Valuation Multiples (P/FCF, EV/EBITDA, PEG)','quant',    9),
 ('technical_trend',            3, 0.02, 'Technical Trend Structure',                 'quant',   10),
 ('analyst_consensus',          3, 0.02, 'Analyst Consensus & Estimate Revisions',    'quant',   11),
 ('volume_accumulation',        3, 0.01, 'Volume & Accumulation Signals',             'quant',   12);


-- ============================================================= --
-- Part 12 — the approval gate (replaces approvals.py's ScoreStore
-- entirely: pending/approved/rejected dicts AND the audit list)
-- ============================================================= --

CREATE TABLE score_events (
  id                  INTEGER PRIMARY KEY,
  ticker              TEXT NOT NULL REFERENCES tickers(ticker),
  indicator           TEXT NOT NULL REFERENCES indicators(code),
  action              TEXT NOT NULL CHECK (action IN ('propose','approve','reject')),
  score               INTEGER NOT NULL CHECK (score BETWEEN 1 AND 5),
  confidence          TEXT NOT NULL CHECK (confidence IN ('H','M','L')),
  source              TEXT NOT NULL CHECK (source IN ('ai_draft','manual','quant_draft')),
  rationale           TEXT NOT NULL DEFAULT '',
  adaptation          TEXT,
  citations           TEXT,                       -- JSON array (propose only)
  model               TEXT,                        -- propose only (ai_draft)
  prompt_hash         TEXT,                        -- propose only (ai_draft)
  original_score      INTEGER CHECK (original_score BETWEEN 1 AND 5),      -- approve only
  original_confidence TEXT CHECK (original_confidence IN ('H','M','L')),   -- approve only
  actor               TEXT REFERENCES users(username),
  reason              TEXT,                        -- reject only
  occurred_at         TEXT NOT NULL,
  recorded_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  CHECK (action != 'approve' OR (actor IS NOT NULL AND original_score IS NOT NULL
                                  AND original_confidence IS NOT NULL)),
  CHECK (action != 'reject'  OR (actor IS NOT NULL AND reason IS NOT NULL))
);
CREATE INDEX ix_score_events_ticker_ind ON score_events(ticker, indicator, id);
CREATE INDEX ix_score_events_action     ON score_events(ticker, indicator, action, id);

CREATE TRIGGER trg_score_events_no_quant_judgment
BEFORE INSERT ON score_events
FOR EACH ROW WHEN NEW.source = 'quant_draft'
  AND (SELECT kind FROM indicators WHERE code = NEW.indicator) = 'judgment'
BEGIN
  SELECT RAISE(ABORT, 'quant_draft source is not allowed for a judgment-only indicator');
END;

-- "current" views — the derived state that used to be the JSON dicts
CREATE VIEW v_score_current_approved AS
SELECT e.* FROM score_events e
JOIN (SELECT ticker, indicator, MAX(id) AS max_id FROM score_events
      WHERE action = 'approve' GROUP BY ticker, indicator) latest
  ON latest.ticker = e.ticker AND latest.indicator = e.indicator AND latest.max_id = e.id;

CREATE VIEW v_score_current_rejected AS
SELECT e.* FROM score_events e
JOIN (SELECT ticker, indicator, MAX(id) AS max_id FROM score_events
      WHERE action = 'reject' GROUP BY ticker, indicator) latest
  ON latest.ticker = e.ticker AND latest.indicator = e.indicator AND latest.max_id = e.id;

CREATE VIEW v_score_current_pending AS
SELECT e.* FROM score_events e
JOIN (SELECT ticker, indicator, MAX(id) AS max_id FROM score_events
      GROUP BY ticker, indicator) latest
  ON latest.ticker = e.ticker AND latest.indicator = e.indicator AND latest.max_id = e.id
WHERE e.action = 'propose';


-- ============================================================= --
-- Part 3 — composite scoring events (point-in-time ScoreCard)
-- ============================================================= --

CREATE TABLE scoring_runs (
  id                       INTEGER PRIMARY KEY,
  ticker                   TEXT NOT NULL REFERENCES tickers(ticker),
  run_at                   TEXT NOT NULL,
  run_by                   TEXT REFERENCES users(username),
  tier1_weighted_average   REAL NOT NULL,
  tier1_contribution       REAL NOT NULL,
  tier2_contribution       REAL,
  tier3_contribution       REAL,
  raw_composite            REAL CHECK (raw_composite IS NULL OR raw_composite >= 0),
  composite                REAL CHECK (composite IS NULL OR composite >= 0),
  decision                 TEXT CHECK (decision IS NULL OR
                              decision IN ('STRONG BUY','BUY','WATCH LIST','AVOID','PASS')),
  rule1_flag               TEXT NOT NULL CHECK (rule1_flag IN ('OK','FAIL')),
  rule2_flag               TEXT NOT NULL CHECK (rule2_flag IN ('OK','REVIEW')),
  rule3_flag               TEXT NOT NULL CHECK (rule3_flag IN ('OK','AVOID')),
  rule4_flag               TEXT NOT NULL CHECK (rule4_flag IN ('OK','CAP AT BUY')),
  debt_to_fcf              REAL,
  financial_cap_triggered  INTEGER NOT NULL DEFAULT 0 CHECK (financial_cap_triggered IN (0,1)),
  unadapted_sector         INTEGER NOT NULL DEFAULT 0 CHECK (unadapted_sector IN (0,1)),
  notes                    TEXT,                    -- JSON array of note strings
  created_at               TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX ix_scoring_runs_ticker ON scoring_runs(ticker, run_at);

CREATE TABLE scoring_run_inputs (
  scoring_run_id INTEGER NOT NULL REFERENCES scoring_runs(id) ON DELETE CASCADE,
  indicator      TEXT NOT NULL REFERENCES indicators(code),
  score_event_id INTEGER NOT NULL REFERENCES score_events(id),
  PRIMARY KEY (scoring_run_id, indicator)
);


-- ============================================================= --
-- Part 4 — Entry Checklist (Rules 5-13)
-- ============================================================= --

CREATE TABLE entry_checklist_runs (
  id                 INTEGER PRIMARY KEY,
  ticker             TEXT NOT NULL REFERENCES tickers(ticker),
  scoring_run_id     INTEGER REFERENCES scoring_runs(id),
  run_at             TEXT NOT NULL,
  staging_fraction   REAL NOT NULL CHECK (staging_fraction IN (0.5, 1.0)),
  authorized         INTEGER NOT NULL CHECK (authorized IN (0,1)),
  review_required    TEXT,
  run_by             TEXT REFERENCES users(username)
);

CREATE TABLE entry_checklist_results (
  id                       INTEGER PRIMARY KEY,
  entry_checklist_run_id   INTEGER NOT NULL REFERENCES entry_checklist_runs(id) ON DELETE CASCADE,
  rule                     TEXT NOT NULL,
  passed                   INTEGER NOT NULL CHECK (passed IN (0,1)),
  detail                   TEXT NOT NULL,
  UNIQUE (entry_checklist_run_id, rule)
);

CREATE TABLE scenario_sets (
  id                          INTEGER PRIMARY KEY,
  ticker                      TEXT NOT NULL REFERENCES tickers(ticker),
  documented_at               TEXT NOT NULL,
  current_price               REAL NOT NULL CHECK (current_price > 0),
  cyclical                    INTEGER NOT NULL DEFAULT 0 CHECK (cyclical IN (0,1)),
  bear_assumption_documented  INTEGER NOT NULL DEFAULT 0 CHECK (bear_assumption_documented IN (0,1)),
  documented_by               TEXT REFERENCES users(username)
);

CREATE TABLE scenarios (
  id                   INTEGER PRIMARY KEY,
  scenario_set_id      INTEGER NOT NULL REFERENCES scenario_sets(id) ON DELETE CASCADE,
  name                 TEXT NOT NULL CHECK (name IN ('base','bear','bull')),
  year5_fcf_ps         REAL NOT NULL,
  terminal_multiple    REAL NOT NULL,
  cum_distributions_ps REAL NOT NULL DEFAULT 0,
  tag                  TEXT NOT NULL CHECK (tag IN ('L','P','U')),
  UNIQUE (scenario_set_id, name)
);

-- Rule 12: exactly one scenario tagged Likely, and it must be the Base Case
CREATE TRIGGER trg_scenarios_one_likely
BEFORE INSERT ON scenarios
FOR EACH ROW WHEN NEW.tag = 'L'
  AND (SELECT COUNT(*) FROM scenarios
       WHERE scenario_set_id = NEW.scenario_set_id AND tag = 'L') > 0
BEGIN
  SELECT RAISE(ABORT, 'Rule 12: only one scenario per set may be tagged Likely');
END;
CREATE TRIGGER trg_scenarios_likely_is_base
BEFORE INSERT ON scenarios
FOR EACH ROW WHEN NEW.tag = 'L' AND NEW.name != 'base'
BEGIN
  SELECT RAISE(ABORT, 'Rule 12: the Likely tag must be the Base Case scenario');
END;


-- ============================================================= --
-- Part 5/8 — positions, lots, tax decisions
-- ============================================================= --

CREATE TABLE accounts (
  id      INTEGER PRIMARY KEY,
  name    TEXT NOT NULL UNIQUE,
  taxable INTEGER NOT NULL DEFAULT 1 CHECK (taxable IN (0,1))
);

CREATE TABLE positions (
  id               INTEGER PRIMARY KEY,
  account_id       INTEGER NOT NULL REFERENCES accounts(id),
  ticker           TEXT NOT NULL REFERENCES tickers(ticker),
  description      TEXT NOT NULL DEFAULT '',
  asset_class      TEXT NOT NULL DEFAULT 'Equity',
  quantity         REAL NOT NULL CHECK (quantity >= 0),
  market_value     REAL NOT NULL,
  pct_of_portfolio REAL NOT NULL,
  unrealized_pct   REAL,
  notes            TEXT NOT NULL DEFAULT '',
  as_of_date       TEXT NOT NULL,
  UNIQUE (account_id, ticker)
);

CREATE TABLE lots (
  id                    INTEGER PRIMARY KEY,
  position_id           INTEGER NOT NULL REFERENCES positions(id) ON DELETE CASCADE,
  lot_id                TEXT NOT NULL,
  quantity              REAL NOT NULL CHECK (quantity > 0),
  cost_basis_per_share  REAL NOT NULL,
  acquired_date         TEXT NOT NULL,
  closed                INTEGER NOT NULL DEFAULT 0 CHECK (closed IN (0,1)),
  UNIQUE (position_id, lot_id)
);

CREATE TABLE tax_decisions (
  id                INTEGER PRIMARY KEY,
  ticker            TEXT NOT NULL REFERENCES tickers(ticker),
  decided_at        TEXT NOT NULL,
  decision_type     TEXT NOT NULL CHECK (decision_type IN ('timing','lot_selection','harvest')),
  trade_reason      TEXT CHECK (trade_reason IS NULL OR
                       trade_reason IN ('fundamental_sell','hard_cap_trim','discretionary_trim')),
  rule              TEXT NOT NULL,
  detail            TEXT NOT NULL,
  may_delay         INTEGER CHECK (may_delay IN (0,1)),
  max_delay_days    INTEGER,
  lots_selected     TEXT,                  -- JSON [[lot_id, shares], ...]
  estimated_tax     REAL,
  harvest_allowed   INTEGER CHECK (harvest_allowed IN (0,1)),
  harvest_failures  TEXT,
  decided_by        TEXT REFERENCES users(username)
);


-- ============================================================= --
-- Part 6 — watch list state machine + holding monitor + triggers
-- ============================================================= --

CREATE TABLE watchlist_state (
  ticker                      TEXT PRIMARY KEY REFERENCES tickers(ticker),
  status                      TEXT NOT NULL CHECK (status IN
                                ('Core Hold / Add','Trim to Buy Maximum','Hold',
                                 'Probationary Hold','Exit Review','Mandatory Sell')),
  band_entered_date           TEXT NOT NULL,
  deadline                    TEXT,
  remediation_plan_approved   INTEGER NOT NULL DEFAULT 0 CHECK (remediation_plan_approved IN (0,1)),
  remediation_plan_detail     TEXT,
  rule                        TEXT NOT NULL,
  updated_at                  TEXT NOT NULL
);

CREATE TABLE watchlist_transitions (
  id                INTEGER PRIMARY KEY,
  ticker            TEXT NOT NULL REFERENCES tickers(ticker),
  transitioned_at   TEXT NOT NULL,
  prior_status      TEXT,
  new_status        TEXT NOT NULL,
  prior_composite   REAL,
  current_composite REAL,
  rule              TEXT NOT NULL,
  action            TEXT NOT NULL,
  deadline          TEXT,
  scoring_run_id    INTEGER REFERENCES scoring_runs(id)
);

CREATE TABLE holding_monitor_runs (
  id                                   INTEGER PRIMARY KEY,
  ticker                               TEXT NOT NULL REFERENCES tickers(ticker),
  run_at                               TEXT NOT NULL,
  prior_scoring_run_id                 INTEGER REFERENCES scoring_runs(id),
  current_scoring_run_id               INTEGER REFERENCES scoring_runs(id),
  position_pct                         REAL,
  max_full_pct                         REAL,
  debt_to_fcf                          REAL,
  p_fcf                                REAL,
  fcf_growth_pct                       REAL,
  implied_5yr_return                   REAL,
  consecutive_quarters_fcf_yield_at_1  INTEGER,
  roic_below_wacc_two_years            INTEGER NOT NULL DEFAULT 0 CHECK (roic_below_wacc_two_years IN (0,1)),
  revenue_negative_two_quarters        INTEGER NOT NULL DEFAULT 0 CHECK (revenue_negative_two_quarters IN (0,1)),
  cyclical_rationale_documented        INTEGER NOT NULL DEFAULT 0 CHECK (cyclical_rationale_documented IN (0,1)),
  deleveraging_path_documented         INTEGER NOT NULL DEFAULT 0 CHECK (deleveraging_path_documented IN (0,1)),
  market_selloff                       INTEGER NOT NULL DEFAULT 0 CHECK (market_selloff IN (0,1)),
  one_quarter_miss                     INTEGER NOT NULL DEFAULT 0 CHECK (one_quarter_miss IN (0,1)),
  analyst_downgrades                   INTEGER NOT NULL DEFAULT 0 CHECK (analyst_downgrades IN (0,1)),
  negative_press                       INTEGER NOT NULL DEFAULT 0 CHECK (negative_press IN (0,1)),
  price_decline_pct                    REAL,
  run_by                               TEXT REFERENCES users(username)
);

CREATE TABLE holding_monitor_triggers (
  id               INTEGER PRIMARY KEY,
  monitor_run_id   INTEGER NOT NULL REFERENCES holding_monitor_runs(id) ON DELETE CASCADE,
  rule             TEXT NOT NULL,               -- 'Rule 21' .. 'Rule 29'
  kind             TEXT NOT NULL CHECK (kind IN ('fundamental','valuation')),
  description      TEXT NOT NULL,
  act_within_days  INTEGER NOT NULL DEFAULT 30
);

-- Hold Through is DERIVED, never a stored flag: a fired trigger always wins,
-- by construction, not by an app-level check that could drift out of sync.
CREATE VIEW v_holding_monitor_hold_through AS
SELECT r.id AS monitor_run_id, r.ticker, r.run_at,
  (NOT EXISTS (SELECT 1 FROM holding_monitor_triggers t WHERE t.monitor_run_id = r.id))
  AND (r.market_selloff = 1 OR r.one_quarter_miss = 1 OR r.analyst_downgrades = 1
       OR r.negative_press = 1
       OR (r.price_decline_pct IS NOT NULL
           AND r.price_decline_pct BETWEEN 20.0 AND 30.0)) AS hold_through
FROM holding_monitor_runs r;


-- ============================================================= --
-- Part 7 — drawdown regime log + sizing decisions
-- ============================================================= --

CREATE TABLE drawdown_regime_log (
  id               INTEGER PRIMARY KEY,
  log_date         TEXT NOT NULL UNIQUE,
  portfolio_value  REAL NOT NULL,
  peak_value       REAL NOT NULL,
  drawdown         REAL NOT NULL,
  candidate_level  INTEGER NOT NULL CHECK (candidate_level BETWEEN 0 AND 3),
  active_level     INTEGER NOT NULL CHECK (active_level BETWEEN 0 AND 3),
  active_status    TEXT NOT NULL CHECK (active_status IN ('Normal','Elevated','Severe','Critical')),
  cash_floor_pct   REAL NOT NULL,
  restriction      TEXT NOT NULL,
  new_high         INTEGER NOT NULL DEFAULT 0 CHECK (new_high IN (0,1))
);

CREATE TABLE sizing_decisions (
  id                          INTEGER PRIMARY KEY,
  ticker                      TEXT NOT NULL REFERENCES tickers(ticker),
  decided_at                  TEXT NOT NULL,
  scoring_run_id              INTEGER REFERENCES scoring_runs(id),
  composite                   REAL NOT NULL,
  base_initial_pct            REAL NOT NULL,
  base_full_pct               REAL NOT NULL,
  max_initial_pct             REAL NOT NULL,
  max_full_pct                REAL NOT NULL,
  staged_initial_pct          REAL NOT NULL,
  blocked                     INTEGER NOT NULL DEFAULT 0 CHECK (blocked IN (0,1)),
  adjustments                 TEXT NOT NULL DEFAULT '[]',   -- JSON list, in application order
  drawdown_regime_log_id      INTEGER REFERENCES drawdown_regime_log(id),
  drawdown_level_at_decision  INTEGER,      -- frozen snapshot: immune to any later
  cash_floor_pct_at_decision  REAL,         -- correction/backfill of the regime log
  restriction_at_decision     TEXT,
  decided_by                  TEXT REFERENCES users(username)
);


-- ============================================================= --
-- Rule 36 — correlation clusters
-- ============================================================= --

CREATE TABLE correlation_reports (
  id            INTEGER PRIMARY KEY,
  computed_at   TEXT NOT NULL,
  window_weeks  INTEGER NOT NULL,
  sufficient    INTEGER NOT NULL CHECK (sufficient IN (0,1))
);

CREATE TABLE correlation_pairs (
  id           INTEGER PRIMARY KEY,
  report_id    INTEGER NOT NULL REFERENCES correlation_reports(id) ON DELETE CASCADE,
  ticker_a     TEXT NOT NULL REFERENCES tickers(ticker),
  ticker_b     TEXT NOT NULL REFERENCES tickers(ticker),
  correlation  REAL NOT NULL,
  flagged      INTEGER NOT NULL DEFAULT 0 CHECK (flagged IN (0,1)),
  CHECK (ticker_a < ticker_b)
);

CREATE TABLE correlation_clusters (
  id                INTEGER PRIMARY KEY,
  report_id         INTEGER NOT NULL REFERENCES correlation_reports(id) ON DELETE CASCADE,
  cluster_index     INTEGER NOT NULL,
  aggregate_weight  REAL,
  over_cap          INTEGER CHECK (over_cap IN (0,1)),
  note              TEXT
);

CREATE TABLE correlation_cluster_members (
  cluster_id  INTEGER NOT NULL REFERENCES correlation_clusters(id) ON DELETE CASCADE,
  ticker      TEXT NOT NULL REFERENCES tickers(ticker),
  PRIMARY KEY (cluster_id, ticker)
);

CREATE TABLE market_data_snapshots (
  id                        INTEGER PRIMARY KEY,
  ticker                    TEXT NOT NULL REFERENCES tickers(ticker),
  as_of                     TEXT NOT NULL,
  price REAL, volume REAL, market_cap REAL, pe REAL,
  wk52_low REAL, wk52_high REAL, dividend_yield REAL,
  beta REAL, beta_window_weeks INTEGER, beta_source TEXT,
  rs_diff_6m REAL, rs_diff_12m REAL, rs_diff_blended REAL,
  above_200w_ma INTEGER CHECK (above_200w_ma IN (0,1)),
  reclaimed_within_6m INTEGER CHECK (reclaimed_within_6m IN (0,1)),
  staging_ok INTEGER CHECK (staging_ok IN (0,1)),
  monthly_macd REAL, monthly_macd_signal REAL,
  macd_positive_or_turning INTEGER CHECK (macd_positive_or_turning IN (0,1)),
  supertrend_bullish INTEGER CHECK (supertrend_bullish IN (0,1)),
  ad_line_score INTEGER, technical_trend_score INTEGER,
  UNIQUE (ticker, as_of)
);
CREATE VIEW v_latest_market_snapshot AS
SELECT s.* FROM market_data_snapshots s
JOIN (SELECT ticker, MAX(as_of) AS max_as_of FROM market_data_snapshots GROUP BY ticker) l
  ON l.ticker = s.ticker AND l.max_as_of = s.as_of;


-- ============================================================= --
-- Part 10 — financial-sector cap/ceiling audit
-- ============================================================= --

CREATE TABLE financial_sector_checks (
  id               INTEGER PRIMARY KEY,
  ticker           TEXT NOT NULL REFERENCES tickers(ticker),
  checked_at       TEXT NOT NULL,
  check_type       TEXT NOT NULL CHECK (check_type IN ('rule48_cap','rule49_ceiling')),
  triggered_or_ok  INTEGER NOT NULL CHECK (triggered_or_ok IN (0,1)),
  rule             TEXT NOT NULL,
  detail           TEXT NOT NULL
);


-- ============================================================= --
-- Part 9 — Journal (built from scratch, no prior code path exists)
-- ============================================================= --

CREATE TABLE journal_entries (
  id                      INTEGER PRIMARY KEY,
  ticker                  TEXT REFERENCES tickers(ticker),   -- NULL = portfolio-wide note
  entry_date              TEXT NOT NULL,
  author                  TEXT NOT NULL REFERENCES users(username),
  category                TEXT NOT NULL DEFAULT 'note'
                            CHECK (category IN ('note','decision','post_mortem','review','other')),
  notes                   TEXT NOT NULL,
  related_scoring_run_id  INTEGER REFERENCES scoring_runs(id),
  related_rule            TEXT,
  created_at              TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE TRIGGER trg_journal_no_update BEFORE UPDATE ON journal_entries
BEGIN SELECT RAISE(ABORT, 'journal_entries is append-only'); END;
CREATE TRIGGER trg_journal_no_delete BEFORE DELETE ON journal_entries
BEGIN SELECT RAISE(ABORT, 'journal_entries is append-only'); END;


-- ============================================================= --
-- daily.py — action items (Action Items tab equivalent)
-- ============================================================= --

CREATE TABLE action_item_runs (
  id            INTEGER PRIMARY KEY,
  run_date      TEXT NOT NULL UNIQUE,
  generated_at  TEXT NOT NULL
);

CREATE TABLE action_items (
  id            INTEGER PRIMARY KEY,
  run_id        INTEGER NOT NULL REFERENCES action_item_runs(id) ON DELETE CASCADE,
  priority      INTEGER NOT NULL CHECK (priority IN (1,2,3)),
  ticker        TEXT NOT NULL,
  action        TEXT NOT NULL,
  rule          TEXT NOT NULL,
  deadline      TEXT,
  resolved      INTEGER NOT NULL DEFAULT 0 CHECK (resolved IN (0,1)),
  resolved_at   TEXT,
  resolved_by   TEXT REFERENCES users(username)
);


-- ============================================================= --
-- Part 9 — Performance Tracking
-- ============================================================= --

CREATE TABLE performance_entries (
  id                          INTEGER PRIMARY KEY,
  ticker                      TEXT NOT NULL REFERENCES tickers(ticker),
  entry_date                  TEXT NOT NULL,
  entry_price                 REAL NOT NULL CHECK (entry_price > 0),
  entry_score                 REAL NOT NULL,
  band                        TEXT NOT NULL CHECK (band IN ('STRONG BUY','BUY')),
  confidence                  TEXT NOT NULL DEFAULT 'M' CHECK (confidence IN ('H','M','L')),
  benchmark_price_at_entry    REAL,
  exit_date                   TEXT,
  exit_price                  REAL,
  exit_reason                 TEXT NOT NULL DEFAULT '',
  current_price                REAL,
  benchmark_price_now         REAL,
  scoring_run_id               INTEGER REFERENCES scoring_runs(id),
  entry_checklist_run_id      INTEGER REFERENCES entry_checklist_runs(id),
  CHECK (exit_date IS NULL OR exit_price IS NOT NULL)
);
