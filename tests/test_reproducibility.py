import json

from plantsim import config
from plantsim.generate import build
from plantsim.repro import resimulate_all


def _hashes(path):
    m = json.loads((path / "manifest.json").read_text())
    return {f["path"]: f["sha256"] for f in m["files"]}


def test_same_seed_gives_byte_identical_files(starter_data, tmp_path):
    again = tmp_path / "again"
    build(again, config.DEFAULT_SEED, config.PROFILES["starter"].telemetry_points, "starter")
    assert _hashes(again) == _hashes(starter_data)


def test_different_seed_changes_the_data(starter_data, tmp_path):
    other = tmp_path / "other"
    build(other, config.DEFAULT_SEED + 1, config.PROFILES["starter"].telemetry_points, "starter")
    assert _hashes(other)["csv/telemetry.csv"] != _hashes(starter_data)["csv/telemetry.csv"]


def test_each_run_can_be_resimulated_from_its_stored_config(starter_data):
    results = resimulate_all(starter_data)
    ok = [r for r in results if r["reproducible"]]
    bad = [r for r in results if not r["reproducible"]]
    assert len(ok) == 15
    assert [r["simulation_run_id"] for r in bad] == ["SIM-2026-000016"]  # the labelled edge case
