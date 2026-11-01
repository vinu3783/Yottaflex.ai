-- Generated from src/plantsim/schema.py. Portable across PostgreSQL 13+ and SQLite 3.35+.

DROP TABLE IF EXISTS alarm_review;
DROP TABLE IF EXISTS alarm_event;
DROP TABLE IF EXISTS alarm;
DROP TABLE IF EXISTS component_status_event;
DROP TABLE IF EXISTS valve_state_event;
DROP TABLE IF EXISTS pump_state_event;
DROP TABLE IF EXISTS telemetry;
DROP TABLE IF EXISTS simulation_run_mode_segment;
DROP TABLE IF EXISTS simulation_run_assumption;
DROP TABLE IF EXISTS simulation_run;
DROP TABLE IF EXISTS simulation_assumption;
DROP TABLE IF EXISTS operating_mode_pump_setting;
DROP TABLE IF EXISTS operating_mode_valve_setting;
DROP TABLE IF EXISTS operating_mode;
DROP TABLE IF EXISTS sensor;
DROP TABLE IF EXISTS pipe;
DROP TABLE IF EXISTS valve;
DROP TABLE IF EXISTS pump;
DROP TABLE IF EXISTS tank;
DROP TABLE IF EXISTS node;
DROP TABLE IF EXISTS facility;
DROP TABLE IF EXISTS rejected_record;

CREATE TABLE facility (
  facility_id TEXT NOT NULL,
  name TEXT NOT NULL,
  facility_type TEXT NOT NULL CHECK (facility_type IN ('WATER_TREATMENT', 'PROCESS_PLANT', 'UTILITY')),
  region TEXT NOT NULL,
  timezone TEXT NOT NULL CHECK (timezone IN ('UTC')),
  commissioned_date DATE NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('ACTIVE', 'INACTIVE')),
  PRIMARY KEY (facility_id),
  UNIQUE (name)
);

CREATE TABLE node (
  node_id TEXT NOT NULL,
  node_type TEXT NOT NULL CHECK (node_type IN ('PUMP', 'TANK', 'VALVE')),
  facility_id TEXT NOT NULL,
  PRIMARY KEY (node_id),
  FOREIGN KEY (facility_id) REFERENCES facility (facility_id)
);

CREATE TABLE tank (
  tank_id TEXT NOT NULL,
  facility_id TEXT NOT NULL,
  tank_role TEXT NOT NULL CHECK (tank_role IN ('INTAKE', 'INTERMEDIATE', 'DISTRIBUTION')),
  height_m DOUBLE PRECISION NOT NULL CHECK (height_m >= 1 AND height_m <= 30),
  area_m2 DOUBLE PRECISION NOT NULL CHECK (area_m2 >= 1),
  capacity_m3 DOUBLE PRECISION NOT NULL,
  base_elevation_m DOUBLE PRECISION NOT NULL,
  low_level_m DOUBLE PRECISION NOT NULL,
  high_level_m DOUBLE PRECISION NOT NULL,
  install_date DATE NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('INSTALLED', 'ACTIVE', 'MAINTENANCE', 'RETIRED')),
  PRIMARY KEY (tank_id),
  FOREIGN KEY (tank_id) REFERENCES node (node_id),
  FOREIGN KEY (facility_id) REFERENCES facility (facility_id)
);

CREATE TABLE pump (
  pump_id TEXT NOT NULL,
  facility_id TEXT NOT NULL,
  model_code TEXT NOT NULL,
  pump_type TEXT NOT NULL CHECK (pump_type IN ('CENTRIFUGAL')),
  rated_flow_m3h DOUBLE PRECISION NOT NULL CHECK (rated_flow_m3h >= 1),
  rated_head_m DOUBLE PRECISION NOT NULL CHECK (rated_head_m >= 1),
  shutoff_head_m DOUBLE PRECISION NOT NULL,
  curve_k_m_per_m3h2 DOUBLE PRECISION NOT NULL,
  bep_efficiency DOUBLE PRECISION NOT NULL CHECK (bep_efficiency >= 0.3 AND bep_efficiency <= 0.95),
  motor_rated_kw DOUBLE PRECISION NOT NULL,
  has_vfd BOOLEAN NOT NULL,
  install_date DATE NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('INSTALLED', 'ACTIVE', 'MAINTENANCE', 'RETIRED')),
  PRIMARY KEY (pump_id),
  FOREIGN KEY (pump_id) REFERENCES node (node_id),
  FOREIGN KEY (facility_id) REFERENCES facility (facility_id)
);

CREATE TABLE valve (
  valve_id TEXT NOT NULL,
  facility_id TEXT NOT NULL,
  valve_type TEXT NOT NULL CHECK (valve_type IN ('GATE', 'CONTROL')),
  valve_role TEXT NOT NULL CHECK (valve_role IN ('INLET', 'OUTLET', 'SUCTION', 'DISCHARGE', 'GRAVITY_CONTROL')),
  size_mm INTEGER NOT NULL CHECK (size_mm >= 25 AND size_mm <= 600),
  kv_m3h DOUBLE PRECISION NOT NULL CHECK (kv_m3h >= 1),
  normal_state TEXT NOT NULL CHECK (normal_state IN ('OPEN', 'CLOSED')),
  design_flow_m3h DOUBLE PRECISION,
  install_date DATE NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('INSTALLED', 'ACTIVE', 'MAINTENANCE', 'RETIRED')),
  PRIMARY KEY (valve_id),
  FOREIGN KEY (valve_id) REFERENCES node (node_id),
  FOREIGN KEY (facility_id) REFERENCES facility (facility_id)
);

CREATE TABLE pipe (
  pipe_id TEXT NOT NULL,
  facility_id TEXT NOT NULL,
  from_node_id TEXT NOT NULL,
  to_node_id TEXT NOT NULL,
  length_m DOUBLE PRECISION NOT NULL CHECK (length_m >= 0.5),
  diameter_mm INTEGER NOT NULL CHECK (diameter_mm >= 25 AND diameter_mm <= 600),
  darcy_friction_factor DOUBLE PRECISION NOT NULL CHECK (darcy_friction_factor >= 0.005 AND darcy_friction_factor <= 0.08),
  resistance_m_per_m3h2 DOUBLE PRECISION NOT NULL,
  install_date DATE NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('INSTALLED', 'ACTIVE', 'MAINTENANCE', 'RETIRED')),
  PRIMARY KEY (pipe_id),
  UNIQUE (from_node_id, to_node_id),
  FOREIGN KEY (facility_id) REFERENCES facility (facility_id),
  FOREIGN KEY (from_node_id) REFERENCES node (node_id),
  FOREIGN KEY (to_node_id) REFERENCES node (node_id)
);

CREATE TABLE sensor (
  sensor_id TEXT NOT NULL,
  facility_id TEXT NOT NULL,
  measurement_type TEXT NOT NULL CHECK (measurement_type IN ('LEVEL', 'FLOW', 'DIFF_PRESSURE')),
  unit TEXT NOT NULL CHECK (unit IN ('m', 'm3/h', 'kPa')),
  attached_node_id TEXT,
  attached_pipe_id TEXT,
  sensor_role TEXT NOT NULL CHECK (sensor_role IN ('PRIMARY', 'REDUNDANT')),
  range_min DOUBLE PRECISION NOT NULL,
  range_max DOUBLE PRECISION NOT NULL,
  accuracy_pct DOUBLE PRECISION NOT NULL CHECK (accuracy_pct >= 0 AND accuracy_pct <= 5),
  install_date DATE NOT NULL,
  calibration_due_date DATE NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('INSTALLED', 'ACTIVE', 'MAINTENANCE', 'RETIRED')),
  PRIMARY KEY (sensor_id),
  FOREIGN KEY (facility_id) REFERENCES facility (facility_id),
  FOREIGN KEY (attached_node_id) REFERENCES node (node_id),
  FOREIGN KEY (attached_pipe_id) REFERENCES pipe (pipe_id),
  CHECK ((CASE WHEN attached_node_id IS NULL THEN 0 ELSE 1 END) + (CASE WHEN attached_pipe_id IS NULL THEN 0 ELSE 1 END) = 1)
);

CREATE TABLE operating_mode (
  mode_id TEXT NOT NULL,
  facility_id TEXT NOT NULL,
  mode_code TEXT NOT NULL CHECK (mode_code IN ('NORMAL', 'HIGH_DEMAND', 'LOW_DEMAND', 'MAINTENANCE')),
  description TEXT NOT NULL,
  demand_factor DOUBLE PRECISION NOT NULL CHECK (demand_factor >= 0.1 AND demand_factor <= 2.0),
  maintenance_pump_id TEXT,
  effective_from DATE NOT NULL,
  effective_to DATE,
  PRIMARY KEY (mode_id),
  UNIQUE (facility_id, mode_code),
  FOREIGN KEY (facility_id) REFERENCES facility (facility_id),
  FOREIGN KEY (maintenance_pump_id) REFERENCES pump (pump_id)
);

CREATE TABLE operating_mode_valve_setting (
  mode_id TEXT NOT NULL,
  valve_id TEXT NOT NULL,
  opening_pct DOUBLE PRECISION NOT NULL CHECK (opening_pct >= 0 AND opening_pct <= 100),
  valve_state TEXT NOT NULL CHECK (valve_state IN ('OPEN', 'CLOSED', 'THROTTLED')),
  PRIMARY KEY (mode_id, valve_id),
  FOREIGN KEY (mode_id) REFERENCES operating_mode (mode_id),
  FOREIGN KEY (valve_id) REFERENCES valve (valve_id)
);

CREATE TABLE operating_mode_pump_setting (
  mode_id TEXT NOT NULL,
  pump_id TEXT NOT NULL,
  enabled BOOLEAN NOT NULL,
  speed_pct DOUBLE PRECISION NOT NULL CHECK (speed_pct >= 50 AND speed_pct <= 100),
  PRIMARY KEY (mode_id, pump_id),
  FOREIGN KEY (mode_id) REFERENCES operating_mode (mode_id),
  FOREIGN KEY (pump_id) REFERENCES pump (pump_id)
);

CREATE TABLE simulation_assumption (
  assumption_id TEXT NOT NULL,
  category TEXT NOT NULL,
  statement TEXT NOT NULL,
  value TEXT,
  unit TEXT,
  PRIMARY KEY (assumption_id)
);

CREATE TABLE simulation_run (
  simulation_run_id TEXT NOT NULL,
  facility_id TEXT NOT NULL,
  run_type TEXT NOT NULL CHECK (run_type IN ('BASELINE', 'WHATIF')),
  start_ts TIMESTAMPTZ NOT NULL,
  end_ts TIMESTAMPTZ NOT NULL,
  timestep_s INTEGER NOT NULL CHECK (timestep_s >= 60),
  steps INTEGER NOT NULL CHECK (steps >= 1),
  seed INTEGER NOT NULL,
  model_version TEXT NOT NULL,
  input_config_json TEXT NOT NULL,
  config_hash TEXT NOT NULL,
  output_hash TEXT,
  output_summary_json TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('COMPLETED', 'FAILED')),
  PRIMARY KEY (simulation_run_id),
  FOREIGN KEY (facility_id) REFERENCES facility (facility_id)
);

CREATE TABLE simulation_run_assumption (
  simulation_run_id TEXT NOT NULL,
  assumption_id TEXT NOT NULL,
  PRIMARY KEY (simulation_run_id, assumption_id),
  FOREIGN KEY (simulation_run_id) REFERENCES simulation_run (simulation_run_id),
  FOREIGN KEY (assumption_id) REFERENCES simulation_assumption (assumption_id)
);

CREATE TABLE simulation_run_mode_segment (
  simulation_run_id TEXT NOT NULL,
  segment_seq INTEGER NOT NULL CHECK (segment_seq >= 1),
  mode_id TEXT NOT NULL,
  start_step INTEGER NOT NULL CHECK (start_step >= 1),
  end_step INTEGER NOT NULL,
  start_ts TIMESTAMPTZ NOT NULL,
  end_ts TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (simulation_run_id, segment_seq),
  FOREIGN KEY (simulation_run_id) REFERENCES simulation_run (simulation_run_id),
  FOREIGN KEY (mode_id) REFERENCES operating_mode (mode_id)
);

CREATE TABLE telemetry (
  telemetry_id TEXT NOT NULL,
  simulation_run_id TEXT NOT NULL,
  sensor_id TEXT NOT NULL,
  ts_utc TIMESTAMPTZ NOT NULL,
  step INTEGER NOT NULL CHECK (step >= 1),
  value DOUBLE PRECISION NOT NULL,
  unit TEXT NOT NULL CHECK (unit IN ('m', 'm3/h', 'kPa')),
  quality_flag TEXT NOT NULL CHECK (quality_flag IN ('GOOD', 'BAD_RANGE')),
  PRIMARY KEY (telemetry_id),
  UNIQUE (simulation_run_id, sensor_id, ts_utc),
  FOREIGN KEY (simulation_run_id) REFERENCES simulation_run (simulation_run_id),
  FOREIGN KEY (sensor_id) REFERENCES sensor (sensor_id)
);

CREATE TABLE pump_state_event (
  event_id TEXT NOT NULL,
  simulation_run_id TEXT NOT NULL,
  pump_id TEXT NOT NULL,
  ts_utc TIMESTAMPTZ NOT NULL,
  from_state TEXT CHECK (from_state IN ('ON', 'OFF')),
  to_state TEXT NOT NULL CHECK (to_state IN ('ON', 'OFF')),
  reason TEXT NOT NULL CHECK (reason IN ('INITIAL', 'LEVEL_LOW_START', 'LEVEL_HIGH_STOP', 'SUCTION_LOW_STOP', 'MODE_DISABLED')),
  PRIMARY KEY (event_id),
  FOREIGN KEY (simulation_run_id) REFERENCES simulation_run (simulation_run_id),
  FOREIGN KEY (pump_id) REFERENCES pump (pump_id)
);

CREATE TABLE valve_state_event (
  event_id TEXT NOT NULL,
  simulation_run_id TEXT NOT NULL,
  valve_id TEXT NOT NULL,
  ts_utc TIMESTAMPTZ NOT NULL,
  from_opening_pct DOUBLE PRECISION,
  to_opening_pct DOUBLE PRECISION NOT NULL CHECK (to_opening_pct >= 0 AND to_opening_pct <= 100),
  from_state TEXT CHECK (from_state IN ('OPEN', 'CLOSED', 'THROTTLED')),
  to_state TEXT NOT NULL CHECK (to_state IN ('OPEN', 'CLOSED', 'THROTTLED')),
  reason TEXT NOT NULL CHECK (reason IN ('INITIAL', 'MODE_CHANGE', 'OVERRIDE')),
  PRIMARY KEY (event_id),
  FOREIGN KEY (simulation_run_id) REFERENCES simulation_run (simulation_run_id),
  FOREIGN KEY (valve_id) REFERENCES valve (valve_id)
);

CREATE TABLE component_status_event (
  event_id TEXT NOT NULL,
  component_type TEXT NOT NULL CHECK (component_type IN ('PUMP', 'TANK', 'VALVE', 'PIPE', 'SENSOR')),
  node_id TEXT,
  pipe_id TEXT,
  sensor_id TEXT,
  from_status TEXT CHECK (from_status IN ('INSTALLED', 'ACTIVE', 'MAINTENANCE', 'RETIRED')),
  to_status TEXT NOT NULL CHECK (to_status IN ('INSTALLED', 'ACTIVE', 'MAINTENANCE', 'RETIRED')),
  effective_at TIMESTAMPTZ NOT NULL,
  reason TEXT NOT NULL CHECK (reason IN ('INSTALLATION', 'COMMISSIONING', 'PLANNED_MAINTENANCE', 'RETURN_TO_SERVICE')),
  simulation_run_id TEXT,
  PRIMARY KEY (event_id),
  FOREIGN KEY (node_id) REFERENCES node (node_id),
  FOREIGN KEY (pipe_id) REFERENCES pipe (pipe_id),
  FOREIGN KEY (sensor_id) REFERENCES sensor (sensor_id),
  FOREIGN KEY (simulation_run_id) REFERENCES simulation_run (simulation_run_id),
  CHECK ((CASE WHEN node_id IS NULL THEN 0 ELSE 1 END) + (CASE WHEN pipe_id IS NULL THEN 0 ELSE 1 END) + (CASE WHEN sensor_id IS NULL THEN 0 ELSE 1 END) = 1)
);

CREATE TABLE alarm (
  alarm_id TEXT NOT NULL,
  simulation_run_id TEXT NOT NULL,
  facility_id TEXT NOT NULL,
  alarm_source TEXT NOT NULL CHECK (alarm_source IN ('DETECTOR', 'OPERATOR')),
  alarm_type TEXT NOT NULL CHECK (alarm_type IN ('LEAK_SUSPECTED', 'BLOCKAGE_SUSPECTED', 'PUMP_DEGRADATION', 'LEVEL_SENSOR_FAULT', 'FLOW_SENSOR_FAULT', 'MASS_BALANCE_UNEXPLAINED', 'DQ_LEVEL_EXCEEDS_CAPACITY')),
  severity TEXT NOT NULL CHECK (severity IN ('LOW', 'MEDIUM', 'HIGH')),
  rule_id TEXT,
  node_id TEXT,
  pipe_id TEXT,
  sensor_id TEXT,
  telemetry_id TEXT,
  suspected_components TEXT,
  raised_at TIMESTAMPTZ NOT NULL,
  cleared_at TIMESTAMPTZ,
  state TEXT NOT NULL CHECK (state IN ('ACTIVE', 'CLEARED')),
  evidence_value DOUBLE PRECISION,
  threshold_value DOUBLE PRECISION,
  evidence_unit TEXT,
  message TEXT NOT NULL,
  PRIMARY KEY (alarm_id),
  FOREIGN KEY (simulation_run_id) REFERENCES simulation_run (simulation_run_id),
  FOREIGN KEY (facility_id) REFERENCES facility (facility_id),
  FOREIGN KEY (node_id) REFERENCES node (node_id),
  FOREIGN KEY (pipe_id) REFERENCES pipe (pipe_id),
  FOREIGN KEY (sensor_id) REFERENCES sensor (sensor_id),
  FOREIGN KEY (telemetry_id) REFERENCES telemetry (telemetry_id),
  CHECK ((CASE WHEN node_id IS NULL THEN 0 ELSE 1 END) + (CASE WHEN pipe_id IS NULL THEN 0 ELSE 1 END) + (CASE WHEN sensor_id IS NULL THEN 0 ELSE 1 END) <= 1)
);

CREATE TABLE alarm_event (
  event_id TEXT NOT NULL,
  alarm_id TEXT NOT NULL,
  ts_utc TIMESTAMPTZ NOT NULL,
  event_type TEXT NOT NULL CHECK (event_type IN ('RAISED', 'CLEARED')),
  from_state TEXT CHECK (from_state IN ('ACTIVE')),
  to_state TEXT NOT NULL CHECK (to_state IN ('ACTIVE', 'CLEARED')),
  PRIMARY KEY (event_id),
  FOREIGN KEY (alarm_id) REFERENCES alarm (alarm_id)
);

CREATE TABLE alarm_review (
  review_id TEXT NOT NULL,
  alarm_id TEXT NOT NULL,
  decision TEXT NOT NULL CHECK (decision IN ('CONFIRMED', 'RECLASSIFY_AS_SENSOR_FAULT', 'UNCONFIRMED')),
  supporting_alarm_ids TEXT,
  explanation TEXT NOT NULL,
  PRIMARY KEY (review_id),
  FOREIGN KEY (alarm_id) REFERENCES alarm (alarm_id)
);

CREATE TABLE rejected_record (
  table_name TEXT NOT NULL,
  record_key TEXT NOT NULL,
  reason TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  PRIMARY KEY (table_name, record_key)
);

CREATE INDEX ix_telemetry_run_sensor ON telemetry (simulation_run_id, sensor_id, step);
CREATE INDEX ix_alarm_run ON alarm (simulation_run_id);
