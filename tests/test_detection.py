import inspect

from plantsim import detector, evaluation, io


def test_every_detectable_fault_is_found_and_normal_runs_stay_quiet(full_data):
    e = evaluation.evaluate(full_data)
    h = e["headline"]
    assert h["fault_recall"] == 1.0
    assert h["false_alarms_in_normal_runs"] == 0
    assert h["edge_cases_handled"] == "5/5"


def test_below_tolerance_leak_raises_no_alarm(full_data):
    e = evaluation.evaluate(full_data)
    boundary = [f for f in e["faults"] if f["expected_decision"] == "NO_ALARM"]
    assert boundary and all(f["outcome"] == "TRUE_NEGATIVE" for f in boundary)


def test_operator_leak_alarm_caused_by_meter_bias_is_reclassified(full_data):
    reviews = io.load_dataset(full_data)["alarm_review"]
    decisions = sorted(r["decision"] for r in reviews)
    assert decisions == ["CONFIRMED", "RECLASSIFY_AS_SENSOR_FAULT"]


def test_detector_does_not_read_ground_truth():
    src = inspect.getsource(detector)
    for forbidden in (
        "ground_truth.json",
        "ground_truth.csv",
        "load_ground_truth",
        "open(",
        "Path(",
    ):
        assert forbidden not in src
    assert not hasattr(detector, "load_ground_truth")


def test_detector_rerun_from_csv_reproduces_stored_alarms(full_data):
    t = io.load_dataset(full_data)
    stored = [a for a in t["alarm"] if a["alarm_source"] == "DETECTOR"]
    t["alarm"] = [a for a in t["alarm"] if a["alarm_source"] == "OPERATOR"]
    again, _, _ = detector.detect(t)
    assert again == stored


def test_alarms_are_localised_to_the_faulty_component(full_data):
    e = evaluation.evaluate(full_data)
    for f in e["faults"]:
        if f["expected_decision"] == "ALARM":
            assert f["outcome"] == "TRUE_POSITIVE", f
