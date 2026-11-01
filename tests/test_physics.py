import math

import pytest

from plantsim import config, physics


def test_pipe_resistance_matches_darcy_weisbach_by_hand():
    # DN100, 50 m, f = 0.02, Q = 60 m3/h: v = 2.122 m/s, h = f L/D v^2/2g = 2.295 m
    r = physics.pipe_resistance(50, 100, 0.02)
    v = 60 / 3600 / (math.pi * 0.1**2 / 4)
    h = 0.02 * (50 / 0.1) * v * v / (2 * 9.80665)
    assert r * 60**2 == pytest.approx(h, rel=1e-12)


def test_operating_point_is_where_pump_and_system_curves_meet():
    h0, k, hs, r = 40.0, 0.002, 18.0, 0.001
    q = physics.pump_flow(h0, k, hs, r)
    assert physics.pump_head(h0, k, q) == pytest.approx(hs + r * q * q)


def test_pump_cannot_beat_static_head_and_closed_valve_blocks_flow():
    assert physics.pump_flow(10.0, 0.001, 12.0, 0.001) == 0.0
    assert physics.valve_resistance(100, 0.0) == physics.INF
    assert physics.pump_flow(40.0, 0.001, 10.0, physics.INF) == 0.0


def test_gravity_flow_never_reverses():
    assert physics.gravity_flow(-1.0, 0.001) == 0.0
    assert physics.gravity_flow(4.0, 0.001) == pytest.approx(math.sqrt(4000))


def test_affinity_law_speed_scaling():
    assert physics.pump_head(40, 0.0, 0, speed=0.5) == pytest.approx(10.0)


def test_valve_kv_definition():
    # Q = Kv at 1 bar drop: head loss = 10.197 m
    assert physics.valve_resistance(80, 1.0) * 80**2 == pytest.approx(physics.M_PER_BAR)


def test_telemetry_target_must_divide_evenly():
    assert config.steps_for(100_000) == 200
    with pytest.raises(ValueError):
        config.steps_for(100_001)
