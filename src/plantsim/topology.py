"""Build a hydraulic model of one facility from the master tables.

The simulator and the detector both use this, so the detector only ever sees what is in
the published tables. Pipes whose nodes do not exist are set aside as `invalid_pipes`.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field


class TopologyError(ValueError):
    pass


@dataclass
class TankM:
    tank_id: str
    area: float
    height: float
    capacity: float
    z: float


@dataclass
class Transfer:
    kind: str  # PUMP or GRAVITY
    src: str
    dst: str
    pump_id: str | None
    valves: list[str]
    pipes: list[str]  # in flow order
    flow_sensor: str | None = None
    pressure_sensor: str | None = None
    downstream_of_sensor: list[str] = field(default_factory=list)  # pipes/valves after meter


@dataclass
class Boundary:
    valve_id: str
    tank_id: str
    pipe_id: str
    design_flow: float
    flow_sensor: str | None = None


@dataclass
class PlantModel:
    facility_id: str
    tanks: dict[str, TankM]
    pumps: dict[str, dict]
    valves: dict[str, dict]
    pipes: dict[str, dict]
    sensors: dict[str, dict]
    transfers: list[Transfer]
    inlets: list[Boundary]
    outlets: list[Boundary]
    level_sensors: dict[str, list[str]]  # tank -> [primary, redundant...]
    invalid_pipes: list[dict]

    def transfer_into(self, tank_id: str) -> Transfer | None:
        for tr in self.transfers:
            if tr.dst == tank_id:
                return tr
        return None

    def pipe_resistance(self, pipe_ids: list[str], multipliers: dict[str, float] | None = None):
        m = multipliers or {}
        return sum(self.pipes[p]["resistance_m_per_m3h2"] * m.get(p, 1.0) for p in pipe_ids)


def build(tables: dict[str, list[dict]], facility_id: str) -> PlantModel:
    nodes = {n["node_id"]: n for n in tables["node"] if n["facility_id"] == facility_id}
    tanks = {
        r["tank_id"]: TankM(
            r["tank_id"], r["area_m2"], r["height_m"], r["capacity_m3"], r["base_elevation_m"]
        )
        for r in tables["tank"]
        if r["facility_id"] == facility_id
    }
    pumps = {r["pump_id"]: r for r in tables["pump"] if r["facility_id"] == facility_id}
    valves = {r["valve_id"]: r for r in tables["valve"] if r["facility_id"] == facility_id}
    pipes, invalid = {}, []
    for p in tables["pipe"]:
        if p["facility_id"] != facility_id:
            continue
        if p["from_node_id"] in nodes and p["to_node_id"] in nodes:
            pipes[p["pipe_id"]] = p
        else:
            invalid.append(p)

    out_edges: dict[str, list[dict]] = defaultdict(list)
    in_edges: dict[str, list[dict]] = defaultdict(list)
    for p in sorted(pipes.values(), key=lambda r: r["pipe_id"]):
        out_edges[p["from_node_id"]].append(p)
        in_edges[p["to_node_id"]].append(p)

    def single(edges: list[dict], what: str) -> dict:
        if len(edges) != 1:
            raise TopologyError(f"{facility_id}: expected one {what}, found {len(edges)}")
        return edges[0]

    transfers: list[Transfer] = []
    for pump_id in sorted(pumps):
        p_in = single(in_edges[pump_id], f"pipe into {pump_id}")
        sv = p_in["from_node_id"]
        p_src = single(in_edges[sv], f"pipe into {sv}")
        p_out = single(out_edges[pump_id], f"pipe out of {pump_id}")
        dv = p_out["to_node_id"]
        p_dst = single(out_edges[dv], f"pipe out of {dv}")
        src, dst = p_src["from_node_id"], p_dst["to_node_id"]
        if src not in tanks or dst not in tanks:
            raise TopologyError(f"{pump_id}: transfer must run tank -> tank")
        transfers.append(
            Transfer(
                "PUMP",
                src,
                dst,
                pump_id,
                [sv, dv],
                [p_src["pipe_id"], p_in["pipe_id"], p_out["pipe_id"], p_dst["pipe_id"]],
            )
        )
    inlets, outlets = [], []
    for vid in sorted(valves):
        role = valves[vid]["valve_role"]
        if role == "GRAVITY_CONTROL":
            p1 = single(in_edges[vid], f"pipe into {vid}")
            p2 = single(out_edges[vid], f"pipe out of {vid}")
            transfers.append(
                Transfer(
                    "GRAVITY",
                    p1["from_node_id"],
                    p2["to_node_id"],
                    None,
                    [vid],
                    [p1["pipe_id"], p2["pipe_id"]],
                )
            )
        elif role == "INLET":
            p = single(out_edges[vid], f"pipe out of {vid}")
            inlets.append(Boundary(vid, p["to_node_id"], p["pipe_id"], valves[vid]["design_flow_m3h"]))
        elif role == "OUTLET":
            p = single(in_edges[vid], f"pipe into {vid}")
            outlets.append(Boundary(vid, p["from_node_id"], p["pipe_id"], valves[vid]["design_flow_m3h"]))
    transfers.sort(key=lambda tr: (tr.dst, tr.src))

    sensors = {s["sensor_id"]: s for s in tables["sensor"] if s["facility_id"] == facility_id}
    level: dict[str, list[str]] = defaultdict(list)
    by_pipe = {}
    for sid in sorted(sensors):
        s = sensors[sid]
        if s["measurement_type"] == "LEVEL":
            level[s["attached_node_id"]].append(sid)
        elif s["measurement_type"] == "FLOW":
            by_pipe[s["attached_pipe_id"]] = sid
    for tank_id in level:
        level[tank_id].sort(key=lambda x: (sensors[x]["sensor_role"] != "PRIMARY", x))
    for tr in transfers:
        for i, pid in enumerate(tr.pipes):
            if pid in by_pipe:
                tr.flow_sensor = by_pipe[pid]
                after = tr.pipes[i + 1 :]
                tr.downstream_of_sensor = _downstream(pipes, valves, after)
        if tr.pump_id:
            for sid, s in sensors.items():
                if s["measurement_type"] == "DIFF_PRESSURE" and s["attached_node_id"] == tr.pump_id:
                    tr.pressure_sensor = sid
    for b in inlets + outlets:
        b.flow_sensor = by_pipe.get(b.pipe_id)

    return PlantModel(
        facility_id,
        tanks,
        pumps,
        valves,
        pipes,
        sensors,
        transfers,
        inlets,
        outlets,
        dict(level),
        invalid,
    )


def _downstream(pipes: dict[str, dict], valves: dict, pipes_after: list[str]) -> list[str]:
    """Valves and pipes between the flow meter and the destination tank, in flow order."""
    out = []
    for pid in pipes_after:
        start = pipes[pid]["from_node_id"]
        if start in valves:
            out.append(start)
        out.append(pid)
    return out
