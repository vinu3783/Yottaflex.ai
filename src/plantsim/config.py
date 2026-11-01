"""Generator settings. Every value that changes the generated data lives here."""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_SEED = 20261022

# Time base
STEP_SECONDS = 900  # telemetry interval: 15 minutes
SUBSTEPS = 15  # integration substeps per interval (60 s each)
RUN_START_UTC = "2026-03-02T00:00:00Z"  # first simulation run
RUN_SPACING_HOURS = 72  # runs are placed 3 days apart (no overlap)
REFERENCE_DATE = "2026-10-22"  # "as of" date for master data; keeps output reproducible

# Master data layout: (name, facility_type, region, pumps, tanks, valves, redundant level sensors)
FACILITIES = (
    ("North Water Treatment Works", "WATER_TREATMENT", "North", 7, 10, 20, 4),
    ("East Process Plant", "PROCESS_PLANT", "East", 7, 10, 20, 3),
    ("South Utility Plant", "UTILITY", "South", 6, 10, 20, 3),
)

# One run per scenario per facility. Order matters: it fixes run IDs.
SCENARIOS = ("NORMAL", "LEAK", "BLOCKAGE", "PUMP_DEGRADATION", "SENSOR_FAULT")

# Fault severities per facility index (documented, not random), see docs/SIMULATION_MODEL.md
LEAK_FRACTIONS = (0.20, 0.12, 0.03)  # share of transfer flow lost; 0.03 is the below-tolerance case
SENSOR_FAULT_TYPES = ("STUCK", "BIAS", "DRIFT")  # level stuck, flow over-read, level drift
FLOW_BIAS_FRACTION = 0.25
LEVEL_DRIFT_M_PER_H = 0.06
DEGRADATION_FINAL_HEALTH = (0.72, 0.80)  # range of final shut-off head multiplier
BLOCKAGE_FLOW_RATIO = (0.50, 0.65)  # blocked flow / nominal flow at typical levels

# Data-quality injections
LEVEL_OVER_CAPACITY_CASES = 6  # telemetry rows with level above tank height

# Control logic (simple level control, evaluated at every telemetry boundary)
PUMP_START_FRAC = 0.45  # start pump when destination tank falls below this
PUMP_STOP_FRAC = 0.85  # stop pump when destination tank rises above this
SUCTION_STOP_FRAC = 0.10  # dry-run protection on the source tank
SUCTION_RESTART_FRAC = 0.20
GRAVITY_CLOSE_FRAC = 0.85  # gravity control valve fully closed at this level
GRAVITY_BAND_FRAC = 0.25  # ... and fully open this far below it
INLET_CLOSE_FRAC = 0.75
INLET_BAND_FRAC = 0.30

# Sensor noise (1 sigma) and low-flow cut-offs
FLOW_NOISE_REL, FLOW_NOISE_ABS = 0.005, 0.2  # m3/h
LEVEL_NOISE_ABS = 0.004  # m
DP_NOISE_REL, DP_NOISE_ABS = 0.004, 0.3  # kPa
FLOW_CUTOFF = 0.5  # m3/h; meters read 0 below this
DP_CUTOFF = 1.0  # kPa

# Demand profile
DIURNAL_AMPLITUDE = 0.25
DIURNAL_PEAK_HOUR = 14.0


@dataclass(frozen=True)
class Profile:
    name: str
    telemetry_points: int


PROFILES = {
    "starter": Profile("starter", 10_000),
    "full": Profile("full", 100_000),
}


def total_sensors() -> int:
    """Sensors per facility follow from the layout (see masterdata.build_sensors)."""
    total = 0
    for _, _, _, pumps, tanks, valves, redundant in FACILITIES:
        transfers = tanks - 1
        gravity = transfers - pumps
        outlets = valves - 2 * pumps - gravity - 1
        total += tanks + redundant + transfers + pumps + 1 + outlets
    return total


def steps_for(telemetry_points: int) -> int:
    """Telemetry = steps x (runs per facility) x sensors. The target must divide exactly."""
    per_step = len(SCENARIOS) * total_sensors()
    if telemetry_points <= 0 or telemetry_points % per_step:
        raise ValueError(
            f"telemetry_points must be a positive multiple of {per_step} "
            f"({len(SCENARIOS)} runs per facility x {total_sensors()} sensors)"
        )
    steps = telemetry_points // per_step
    if steps < 8:
        raise ValueError("need at least 8 steps per run for fault onset and detection windows")
    return steps
