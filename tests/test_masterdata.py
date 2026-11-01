from collections import Counter

from plantsim import config, ids, masterdata, topology


def master():
    return masterdata.build_master(config.DEFAULT_SEED)


def test_required_volumes():
    t = master()
    assert len(t["facility"]) == 3
    assert len(t["pump"]) == 20
    assert len(t["tank"]) == 30
    assert len(t["valve"]) == 60
    assert len(t["sensor"]) == config.total_sensors() == 100


def test_ids_follow_the_convention_and_are_sequential():
    t = master()
    assert [f["facility_id"] for f in t["facility"]] == ["FAC-001", "FAC-002", "FAC-003"]
    assert sorted(p["pump_id"] for p in t["pump"]) == [ids.make("PUMP", n) for n in range(1, 21)]
    assert sorted(s["sensor_id"] for s in t["sensor"])[0] == "SNS-001"
    assert all(ids.matches("VALVE", v["valve_id"]) for v in t["valve"])


def test_every_component_belongs_to_a_facility_and_every_pipe_joins_two_nodes():
    t = master()
    facs = {f["facility_id"] for f in t["facility"]}
    nodes = {n["node_id"]: n["facility_id"] for n in t["node"]}
    for tbl in ("tank", "pump", "valve", "pipe", "sensor"):
        assert all(r["facility_id"] in facs for r in t[tbl])
    for p in t["pipe"]:
        assert nodes[p["from_node_id"]] == nodes[p["to_node_id"]] == p["facility_id"]


def test_topology_builds_for_every_facility():
    t = master()
    for f in t["facility"]:
        plant = topology.build(t, f["facility_id"])
        assert len(plant.transfers) == len(plant.tanks) - 1  # a tree of tanks
        assert len(plant.inlets) == 1
        assert all(tr.flow_sensor for tr in plant.transfers)
        assert all(tr.pressure_sensor for tr in plant.transfers if tr.kind == "PUMP")
        assert all(len(s) in (1, 2) for s in plant.level_sensors.values())


def test_derived_sizes_are_consistent():
    t = master()
    for tk in t["tank"]:
        assert abs(tk["capacity_m3"] - tk["area_m2"] * tk["height_m"]) < 0.011
    for p in t["pump"]:
        assert p["shutoff_head_m"] > p["rated_head_m"] > 0


def test_master_data_is_deterministic():
    assert master() == master()
    assert masterdata.build_master(1) != master()


def test_each_mode_has_a_setting_for_every_valve_and_pump():
    t = master()
    per_mode = Counter(s["mode_id"] for s in t["operating_mode_valve_setting"])
    assert set(per_mode.values()) == {20}
    maint = [m for m in t["operating_mode"] if m["mode_code"] == "MAINTENANCE"]
    assert all(m["maintenance_pump_id"] for m in maint)


def test_lifecycle_history_is_valid_for_many_seeds():
    # Regression: seed 11 once produced maintenance before commissioning.
    from collections import defaultdict

    from plantsim.validation import ALLOWED_TRANSITIONS

    for seed in range(1, 41):
        by_comp = defaultdict(list)
        for e in masterdata.build_master(seed)["component_status_event"]:
            by_comp[e["node_id"] or e["pipe_id"] or e["sensor_id"]].append(e)
        for evs in by_comp.values():
            prev = None
            for e in sorted(evs, key=lambda e: (e["effective_at"], e["event_id"])):
                assert e["from_status"] == prev, (seed, e)
                assert (prev, e["to_status"]) in ALLOWED_TRANSITIONS
                prev = e["to_status"]
