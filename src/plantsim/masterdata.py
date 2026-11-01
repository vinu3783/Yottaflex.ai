"""Master data: facilities, the topology graph and every component, generated parent-first.

Each facility is a tree of tanks rooted at an intake tank. Every tree edge is a *transfer*:
  pumped:  tank -> SUCTION valve -> pump -> DISCHARGE valve -> tank   (4 pipes)
  gravity: tank -> GRAVITY_CONTROL valve -> lower tank                (2 pipes)
The intake tank is fed through an INLET valve and every leaf tank delivers through an
OUTLET valve. Component sizes are derived from downstream demand, so the network is
hydraulically consistent rather than randomly sized.
"""

from __future__ import annotations

import math
import random
from datetime import date, timedelta

from plantsim import config, ids, physics

# Installs end early enough that commissioning (+7..45 days) always precedes the
# earliest historical maintenance window (from 2025-07-01).
LAST_INSTALL = date(2025, 5, 15)

DN_SIZES = (50, 65, 80, 100, 125, 150, 200, 250, 300, 350, 400)


def _d(s: str) -> date:
    return date.fromisoformat(s)


def _rand_date(rng: random.Random, start: date, end: date) -> date:
    return start + timedelta(days=rng.randrange((end - start).days + 1))


def _dn_for(flow_m3h: float, velocity: float = 1.8) -> int:
    needed_mm = 1000 * math.sqrt(4 * flow_m3h / 3600 / (math.pi * velocity))
    for dn in DN_SIZES:
        if dn >= needed_mm:
            return dn
    return DN_SIZES[-1]


def _tree(rng: random.Random, n_tanks: int, n_leaves: int) -> list[int | None]:
    """Random recursive tree with exactly n_leaves leaves (deterministic for a seeded rng)."""
    for _ in range(10_000):
        parents: list[int | None] = [None] + [rng.randrange(i) for i in range(1, n_tanks)]
        children = {p for p in parents if p is not None}
        if n_tanks - len(children) == n_leaves:
            return parents
    raise RuntimeError("could not build a tree with the requested number of leaves")


def build_master(seed: int) -> dict[str, list[dict]]:
    rng = random.Random(f"master-{seed}")
    c = ids.Counter()
    t: dict[str, list[dict]] = {
        k: []
        for k in (
            "facility",
            "node",
            "tank",
            "pump",
            "valve",
            "pipe",
            "sensor",
            "operating_mode",
            "operating_mode_valve_setting",
            "operating_mode_pump_setting",
            "component_status_event",
        )
    }
    lifecycle: list[tuple] = []  # (effective_at, component_type, id, from, to, reason)

    for spec in config.FACILITIES:
        name, ftype, region, n_p, n_t, n_v, n_redundant = spec
        fac_id = c.next("FAC")
        commissioned = _rand_date(rng, _d("2015-01-01"), _d("2019-12-31"))
        t["facility"].append(
            {
                "facility_id": fac_id,
                "name": name,
                "facility_type": ftype,
                "region": region,
                "timezone": "UTC",
                "commissioned_date": commissioned.isoformat(),
                "status": "ACTIVE",
            }
        )
        n_transfers = n_t - 1
        n_gravity = n_transfers - n_p
        n_outlets = n_v - 2 * n_p - n_gravity - 1
        parents = _tree(rng, n_t, n_outlets)
        gravity_edges = sorted(rng.sample(range(1, n_t), n_gravity))
        children = {i: [j for j in range(n_t) if parents[j] == i] for i in range(n_t)}
        leaves = [i for i in range(n_t) if not children[i]]

        # Demand at leaves, accumulated up the tree (children always have larger index)
        demand = [0.0] * n_t
        base_demand = {}
        for leaf in leaves:
            base_demand[leaf] = round(rng.uniform(12, 40) * 2) / 2
            demand[leaf] = base_demand[leaf]
        for i in range(n_t - 1, 0, -1):
            demand[parents[i]] += demand[i]

        # Design flow into each tank
        design_in = [0.0] * n_t
        design_in[0] = round(demand[0] * 1.6, 1)
        for i in range(1, n_t):
            factor = 1.3 if i in gravity_edges else 1.5
            design_in[i] = max(10.0, round(demand[i] * factor / 5) * 5)

        # Tanks
        z = [0.0] * n_t
        z[0] = round(rng.uniform(40, 60), 1)
        for i in range(1, n_t):
            if i in gravity_edges:
                z[i] = round(z[parents[i]] - rng.uniform(8, 14), 1)
            else:
                z[i] = round(z[parents[i]] + rng.uniform(4, 12), 1)
        tank_ids = []
        tank_height = []
        for i in range(n_t):
            tid = c.next("TANK")
            tank_ids.append(tid)
            height = round(rng.uniform(5, 10), 1)
            capacity_target = max(120.0, design_in[i] * rng.uniform(3.2, 5.0))
            area = round(capacity_target / height, 2)
            role = "INTAKE" if i == 0 else ("DISTRIBUTION" if i in leaves else "INTERMEDIATE")
            install = _rand_date(rng, commissioned, LAST_INSTALL)
            tank_height.append(height)
            t["tank"].append(
                {
                    "tank_id": tid,
                    "facility_id": fac_id,
                    "tank_role": role,
                    "height_m": height,
                    "area_m2": area,
                    "capacity_m3": round(area * height, 2),
                    "base_elevation_m": z[i],
                    "low_level_m": round(0.10 * height, 3),
                    "high_level_m": round(0.90 * height, 3),
                    "install_date": install.isoformat(),
                    "status": "ACTIVE",
                }
            )
            t["node"].append({"node_id": tid, "node_type": "TANK", "facility_id": fac_id})
            lifecycle += _install_events(rng, "TANK", tid, install)

        def add_valve(role: str, vtype: str, flow: float, design_flow=None) -> str:
            vid = c.next("VALVE")
            install = _rand_date(rng, commissioned, LAST_INSTALL)
            t["valve"].append(
                {
                    "valve_id": vid,
                    "facility_id": fac_id,
                    "valve_type": vtype,
                    "valve_role": role,
                    "size_mm": _dn_for(flow),
                    "kv_m3h": float(round(flow * rng.uniform(5.0, 7.0))),
                    "normal_state": "OPEN",
                    "design_flow_m3h": design_flow,
                    "install_date": install.isoformat(),
                    "status": "ACTIVE",
                }
            )
            t["node"].append({"node_id": vid, "node_type": "VALVE", "facility_id": fac_id})
            lifecycle.extend(_install_events(rng, "VALVE", vid, install))
            return vid

        def add_pipe(a: str, b: str, flow: float, lmin: float, lmax: float) -> dict:
            pid = c.next("PIPE")
            length = round(rng.uniform(lmin, lmax), 1)
            dn = _dn_for(flow)
            f = round(rng.uniform(0.018, 0.024), 4)
            install = _rand_date(rng, commissioned, LAST_INSTALL)
            row = {
                "pipe_id": pid,
                "facility_id": fac_id,
                "from_node_id": a,
                "to_node_id": b,
                "length_m": length,
                "diameter_mm": dn,
                "darcy_friction_factor": f,
                "resistance_m_per_m3h2": round(physics.pipe_resistance(length, dn, f), 12),
                "install_date": install.isoformat(),
                "status": "ACTIVE",
            }
            t["pipe"].append(row)
            lifecycle.extend(_install_events(rng, "PIPE", pid, install))
            return row

        sensors_plan: list[tuple] = []  # (kind, attached_node, attached_pipe, role, span_max)

        # Inlet
        iv = add_valve("INLET", "CONTROL", design_in[0], design_flow=design_in[0])
        p_in = add_pipe(iv, tank_ids[0], design_in[0], 5, 30)
        sensors_plan.append(("FLOW", None, p_in["pipe_id"], "PRIMARY", 2.5 * design_in[0]))

        pumps_here = []
        for i in range(1, n_t):
            src, dst = tank_ids[parents[i]], tank_ids[i]
            q = design_in[i]
            if i in gravity_edges:
                cv = add_valve("GRAVITY_CONTROL", "CONTROL", q)
                add_pipe(src, cv, q, 5, 30)
                p2 = add_pipe(cv, dst, q, 20, 200)
                sensors_plan.append(("FLOW", None, p2["pipe_id"], "PRIMARY", 2.5 * q))
                continue
            sv = add_valve("SUCTION", "GATE", q)
            pump_id = c.next("PUMP")
            dv = add_valve("DISCHARGE", "GATE", q)
            pipes = [
                add_pipe(src, sv, q, 5, 20),
                add_pipe(sv, pump_id, q, 2, 5),
                add_pipe(pump_id, dv, q, 2, 5),
                add_pipe(dv, dst, q, 20, 200),
            ]
            r_total = sum(p["resistance_m_per_m3h2"] for p in pipes)
            for v in (sv, dv):
                kv = next(x["kv_m3h"] for x in t["valve"] if x["valve_id"] == v)
                r_total += physics.valve_resistance(kv, 1.0)
            h_src, h_dst = tank_height[parents[i]], tank_height[i]
            static_worst = (z[i] + 0.9 * h_dst) - (z[parents[i]] + 0.1 * h_src)
            # Size so that 70 % of rated flow is still delivered at the worst static head.
            h0 = (static_worst + 0.49 * r_total * q * q) / 0.902
            h0 = round(max(h0, 8.0), 1)
            rated_head = round(0.8 * h0, 1)
            eta = round(rng.uniform(0.65, 0.82), 3)
            motor = math.ceil(1.15 * physics.hydraulic_power_kw(q, rated_head) / eta)
            install = _rand_date(rng, commissioned, LAST_INSTALL)
            t["pump"].append(
                {
                    "pump_id": pump_id,
                    "facility_id": fac_id,
                    "model_code": f"CP-{_dn_for(q)}-{int(round(h0))}",
                    "pump_type": "CENTRIFUGAL",
                    "rated_flow_m3h": q,
                    "rated_head_m": rated_head,
                    "shutoff_head_m": h0,
                    "curve_k_m_per_m3h2": round(physics.curve_k(h0, rated_head, q), 12),
                    "bep_efficiency": eta,
                    "motor_rated_kw": float(motor),
                    "has_vfd": True,
                    "install_date": install.isoformat(),
                    "status": "ACTIVE",
                }
            )
            t["node"].append({"node_id": pump_id, "node_type": "PUMP", "facility_id": fac_id})
            lifecycle += _install_events(rng, "PUMP", pump_id, install)
            pumps_here.append(pump_id)
            sensors_plan.append(("FLOW", None, pipes[2]["pipe_id"], "PRIMARY", 2.5 * q))
            sensors_plan.append(("DIFF_PRESSURE", pump_id, None, "PRIMARY", 1.5 * h0 * physics.KPA_PER_M))

        # Outlets on leaf tanks
        for leaf in leaves:
            ov = add_valve("OUTLET", "CONTROL", base_demand[leaf], design_flow=base_demand[leaf])
            p_out = add_pipe(tank_ids[leaf], ov, base_demand[leaf], 5, 30)
            sensors_plan.append(("FLOW", None, p_out["pipe_id"], "PRIMARY", 2.5 * base_demand[leaf]))

        # Past maintenance on two pumps (history only; all are back in service)
        for pump_id in sorted(rng.sample(pumps_here, 2)):
            start = _rand_date(rng, _d("2025-07-01"), _d("2025-12-15"))
            lifecycle.append(_ev(start, "PUMP", pump_id, "ACTIVE", "MAINTENANCE", "PLANNED_MAINTENANCE"))
            back = start + timedelta(days=rng.randint(2, 10))
            lifecycle.append(_ev(back, "PUMP", pump_id, "MAINTENANCE", "ACTIVE", "RETURN_TO_SERVICE"))

        installed = {
            r[k]: _d(r["install_date"])
            for tbl, k in (("tank", "tank_id"), ("pump", "pump_id"), ("pipe", "pipe_id"))
            for r in t[tbl]
        }
        # Sensors: level (primary + redundant), then the planned flow/pressure sensors
        redundant_tanks = set(rng.sample(range(n_t), n_redundant))
        level_plan = []
        for i in range(n_t):
            level_plan.append(("LEVEL", tank_ids[i], None, "PRIMARY", 1.25 * tank_height[i]))
            if i in redundant_tanks:
                level_plan.append(("LEVEL", tank_ids[i], None, "REDUNDANT", 1.25 * tank_height[i]))
        for kind, node_id, pipe_id, role, span in level_plan + sensors_plan:
            sid = c.next("SNS")
            unit = {"LEVEL": "m", "FLOW": "m3/h", "DIFF_PRESSURE": "kPa"}[kind]
            parent_install = installed[node_id or pipe_id]
            install = _rand_date(rng, max(parent_install, _d("2023-01-01")), _d("2025-12-31"))
            t["sensor"].append(
                {
                    "sensor_id": sid,
                    "facility_id": fac_id,
                    "measurement_type": kind,
                    "unit": unit,
                    "attached_node_id": node_id,
                    "attached_pipe_id": pipe_id,
                    "sensor_role": role,
                    "range_min": 0.0,
                    "range_max": round(span, 2),
                    "accuracy_pct": {"LEVEL": 0.25, "FLOW": 0.5, "DIFF_PRESSURE": 0.4}[kind],
                    "install_date": install.isoformat(),
                    "calibration_due_date": _rand_date(rng, _d("2026-11-01"), _d("2027-06-30")).isoformat(),
                    "status": "ACTIVE",
                }
            )
            lifecycle += _install_events(rng, "SENSOR", sid, install)

        # Operating modes and their settings (bridge tables)
        maint_pump = rng.choice(pumps_here)
        mode_defs = (
            ("NORMAL", "Design operation", 1.0, None),
            ("HIGH_DEMAND", "Peak demand, pumps at full speed", 1.3, None),
            ("LOW_DEMAND", "Night/low demand, reduced speed and inlet throttled", 0.7, None),
            ("MAINTENANCE", f"{maint_pump} isolated for maintenance", 0.9, maint_pump),
        )
        fac_valves = [v for v in t["valve"] if v["facility_id"] == fac_id]
        fac_pumps = [p for p in t["pump"] if p["facility_id"] == fac_id]
        maint_valves = _pump_valves(t["pipe"], maint_pump)
        for code, desc, factor, mp in mode_defs:
            mid = c.next("MODE")
            t["operating_mode"].append(
                {
                    "mode_id": mid,
                    "facility_id": fac_id,
                    "mode_code": code,
                    "description": desc,
                    "demand_factor": factor,
                    "maintenance_pump_id": mp,
                    "effective_from": "2026-01-01",
                    "effective_to": None,
                }
            )
            for v in fac_valves:
                opening = 100.0
                if code == "MAINTENANCE" and v["valve_id"] in maint_valves:
                    opening = 0.0
                if code == "LOW_DEMAND" and v["valve_role"] == "INLET":
                    opening = 80.0
                t["operating_mode_valve_setting"].append(
                    {
                        "mode_id": mid,
                        "valve_id": v["valve_id"],
                        "opening_pct": opening,
                        "valve_state": valve_state(opening),
                    }
                )
            for p in fac_pumps:
                t["operating_mode_pump_setting"].append(
                    {
                        "mode_id": mid,
                        "pump_id": p["pump_id"],
                        "enabled": not (code == "MAINTENANCE" and p["pump_id"] == mp),
                        "speed_pct": {"LOW_DEMAND": 92.0}.get(code, 100.0),
                    }
                )

    t["component_status_event"] = _number_lifecycle(lifecycle)
    return t


def valve_state(opening_pct: float) -> str:
    if opening_pct <= 0:
        return "CLOSED"
    if opening_pct >= 100:
        return "OPEN"
    return "THROTTLED"


def _pump_valves(pipes: list[dict], pump_id: str) -> set[str]:
    out = set()
    for p in pipes:
        if p["to_node_id"] == pump_id:
            out.add(p["from_node_id"])
        if p["from_node_id"] == pump_id:
            out.add(p["to_node_id"])
    return out


def _ev(day: date, ctype: str, cid: str, frm, to, reason, run_id=None, ts=None) -> tuple:
    return (ts or f"{day.isoformat()}T08:00:00Z", ctype, cid, frm, to, reason, run_id)


def _install_events(rng: random.Random, ctype: str, cid: str, install: date) -> list[tuple]:
    active = install + timedelta(days=rng.randint(7, 45))
    return [
        _ev(install, ctype, cid, None, "INSTALLED", "INSTALLATION"),
        _ev(active, ctype, cid, "INSTALLED", "ACTIVE", "COMMISSIONING"),
    ]


def _number_lifecycle(events: list[tuple]) -> list[dict]:
    rows = []
    order = {"INSTALLATION": 0, "COMMISSIONING": 1}
    for n, (ts, ctype, cid, frm, to, reason, run_id) in enumerate(
        sorted(events, key=lambda e: (e[0], e[2], order.get(e[5], 2))), start=1
    ):
        rows.append(status_event_row(ids.make("CSE", n), ctype, cid, frm, to, ts, reason, run_id))
    return rows


def status_event_row(eid, ctype, cid, frm, to, ts, reason, run_id) -> dict:
    return {
        "event_id": eid,
        "component_type": ctype,
        "node_id": cid if ctype in ("PUMP", "TANK", "VALVE") else None,
        "pipe_id": cid if ctype == "PIPE" else None,
        "sensor_id": cid if ctype == "SENSOR" else None,
        "from_status": frm,
        "to_status": to,
        "effective_at": ts,
        "reason": reason,
        "simulation_run_id": run_id,
    }
