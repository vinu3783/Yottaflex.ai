"""Steady-state hydraulic relations used by both the simulator and the detector.

Units: flow Q in m3/h, head H in metres of water, pressure in kPa, resistance in m/(m3/h)^2.
Water at 1000 kg/m3 is assumed throughout.
"""

from __future__ import annotations

import math

G = 9.80665  # m/s2
KPA_PER_M = 9.80665  # 1 m of water column = 9.80665 kPa (rho = 1000 kg/m3)
M_PER_BAR = 100.0 / KPA_PER_M  # 10.197 m of water per bar
INF = float("inf")


def pipe_resistance(length_m: float, diameter_mm: float, friction: float) -> float:
    """Darcy-Weisbach head loss h = f (L/D) v^2 / 2g, written as h = r Q^2 with Q in m3/h."""
    d = diameter_mm / 1000.0
    area = math.pi * d * d / 4.0
    return friction * (length_m / d) / (2.0 * G) / (area * 3600.0) ** 2


def valve_resistance(kv_m3h: float, opening: float) -> float:
    """Kv definition: Q = Kv sqrt(dp_bar). Linear trim: effective Kv = Kv x opening."""
    if opening <= 0.0:
        return INF
    return M_PER_BAR / (kv_m3h * opening) ** 2


def pump_head(h0: float, k: float, q: float, speed: float = 1.0, health: float = 1.0) -> float:
    """Quadratic pump curve with affinity scaling: H = H0 * health * s^2 - k Q^2."""
    return h0 * health * speed * speed - k * q * q


def curve_k(shutoff_head: float, rated_head: float, rated_flow: float) -> float:
    return (shutoff_head - rated_head) / (rated_flow * rated_flow)


def pump_flow(h0_eff: float, k: float, static_head: float, resistance: float) -> float:
    """Operating point where the pump curve meets the system curve H = Hs + R Q^2."""
    if resistance == INF:
        return 0.0
    surplus = h0_eff - static_head
    if surplus <= 0.0:
        return 0.0
    return math.sqrt(surplus / (k + resistance))


def gravity_flow(head_difference: float, resistance: float) -> float:
    """Flow between two tanks under gravity; a check valve prevents reverse flow."""
    if head_difference <= 0.0 or resistance == INF or resistance <= 0.0:
        return 0.0
    return math.sqrt(head_difference / resistance)


def pump_efficiency(q: float, rated_flow: float, bep_efficiency: float) -> float:
    """Parabolic efficiency around the best efficiency point, floored at 20 %."""
    x = (q - rated_flow) / rated_flow
    return max(0.20, bep_efficiency * (1.0 - x * x))


def hydraulic_power_kw(q_m3h: float, head_m: float) -> float:
    return KPA_PER_M * (q_m3h / 3600.0) * head_m  # rho g Q H / 1000
