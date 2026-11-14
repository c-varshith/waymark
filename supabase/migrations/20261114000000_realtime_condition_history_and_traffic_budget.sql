-- Cell-only, fixed-retention condition history and persisted provider request counters.
-- TomTom-derived congestion is deliberately always NULL until its terms permit
-- historical retention/analysis; request counters contain no provider content.
CREATE TABLE IF NOT EXISTS public.condition_snapshots (
  id BIGSERIAL PRIMARY KEY,
  cell_id VARCHAR NOT NULL,
  at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
  rain BOOLEAN NULL,
  night BOOLEAN NULL,
  rush BOOLEAN NOT NULL,
  congestion_bucket VARCHAR NULL,
  base_score DOUBLE PRECISION NOT NULL,
  live_score DOUBLE PRECISION NOT NULL,
  active_incident_count INTEGER NOT NULL,
  model_version VARCHAR NULL,
  condition_lift_version VARCHAR NULL
);
CREATE INDEX IF NOT EXISTS ix_condition_snapshots_cell_id ON public.condition_snapshots(cell_id);
CREATE INDEX IF NOT EXISTS ix_condition_snapshots_at ON public.condition_snapshots(at);
CREATE INDEX IF NOT EXISTS ix_condition_snapshots_cell_at ON public.condition_snapshots(cell_id, at);

CREATE TABLE IF NOT EXISTS public.traffic_request_counters (
  period_key VARCHAR PRIMARY KEY,
  request_count INTEGER NOT NULL DEFAULT 0
);
