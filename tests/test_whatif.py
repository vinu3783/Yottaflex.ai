import json

import pytest

from plantsim.whatif import WhatIfError, run_whatif


def test_whatif_writes_a_new_run_and_never_overwrites(starter_copy):
    a = run_whatif(starter_copy, "SIM-2026-000001", disabled=["PUMP-002"])
    b = run_whatif(starter_copy, "SIM-2026-000001", disabled=["PUMP-002"])
    assert a["folder"] != b["folder"]
    assert a["comparison"]["whatif_run"] == "SIM-2026-000017"
    assert b["comparison"]["whatif_run"] == "SIM-2026-000018"
    # Same change, same seed -> same outcome
    assert a["comparison"]["true_outcomes"] == b["comparison"]["true_outcomes"]


def test_no_change_reproduces_the_baseline(starter_copy):
    res = run_whatif(starter_copy, "SIM-2026-000002")
    assert all(v["delta"] == 0 for v in res["comparison"]["true_outcomes"].values())


def test_higher_demand_increases_delivered_volume(starter_copy):
    res = run_whatif(starter_copy, "SIM-2026-000001", demand_multiplier=1.4)
    assert res["comparison"]["true_outcomes"]["delivered_m3"]["delta"] > 0


def test_whatif_config_is_recorded_with_its_hash(starter_copy):
    res = run_whatif(starter_copy, "SIM-2026-000001", pump_speed={"PUMP-001": 85})
    run_csv = (starter_copy / "whatif" / res["comparison"]["whatif_run"] / "simulation_run.csv").read_text()
    assert "WHATIF" in run_csv and "PUMP-001" in run_csv
    cmp = json.loads(
        (starter_copy / "whatif" / res["comparison"]["whatif_run"] / "comparison.json").read_text()
    )
    assert cmp["change"]["pump_speed_pct"] == {"PUMP-001": 85.0}


@pytest.mark.parametrize(
    "kwargs",
    [
        {"disabled": ["PUMP-099"]},
        {"pump_speed": {"PUMP-001": 120}},
        {"valve_opening": {"VALVE-999": 50}},
        {"mode_code": "TURBO"},
    ],
)
def test_invalid_changes_are_rejected(starter_copy, kwargs):
    with pytest.raises(WhatIfError):
        run_whatif(starter_copy, "SIM-2026-000001", **kwargs)


def test_run_without_config_cannot_be_a_baseline(starter_copy):
    with pytest.raises(WhatIfError):
        run_whatif(starter_copy, "SIM-2026-000016")
