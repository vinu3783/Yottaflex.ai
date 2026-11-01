"""The validator must catch problems that were NOT deliberately labelled."""

import csv

from plantsim import validation


def _edit(path, fn):
    with path.open(newline="") as fh:
        rows = list(csv.reader(fh))
    rows = fn(rows)
    with path.open("w", newline="") as fh:
        csv.writer(fh, lineterminator="\n").writerows(rows)


def _status(data, check):
    v = validation.validate(data)
    return v["overall_status"], next(c for c in v["checks"] if c["check"] == check)


def test_duplicate_reading_is_caught(starter_copy):
    _edit(starter_copy / "csv" / "telemetry.csv", lambda r: r + [r[1][:]])
    overall, c = _status(starter_copy, "primary_key_unique")
    assert overall == "FAIL" and c["status"] == "FAIL"


def test_wrong_unit_is_caught(starter_copy):
    def f(rows):
        rows[1][6] = "kPa" if rows[1][6] != "kPa" else "m"
        return rows

    _edit(starter_copy / "csv" / "telemetry.csv", f)
    assert _status(starter_copy, "units_explicit")[1]["status"] == "FAIL"


def test_tampered_derived_capacity_is_caught(starter_copy):
    def f(rows):
        i = rows[0].index("capacity_m3")
        rows[1][i] = str(float(rows[1][i]) + 50)
        return rows

    _edit(starter_copy / "csv" / "tank.csv", f)
    assert _status(starter_copy, "arithmetic_reconciliation")[1]["status"] == "FAIL"


def test_unlabelled_dangling_pipe_is_caught(starter_copy):
    def f(rows):
        new = rows[1][:]
        new[0] = "PIPE-999"
        new[3] = "TANK-777"
        return rows + [new]

    _edit(starter_copy / "csv" / "pipe.csv", f)
    c = _status(starter_copy, "foreign_keys")[1]
    assert c["status"] == "FAIL" and "pipe:PIPE-999" in c["unexpected"]


def test_missing_labelled_case_is_also_a_failure(starter_copy):
    # Remove the deliberately bad pipe: the label then points at nothing that fails.
    _edit(starter_copy / "csv" / "pipe.csv", lambda r: [x for x in r if x[0] != "PIPE-108"])
    c = _status(starter_copy, "foreign_keys")[1]
    assert c["status"] == "FAIL" and c["labelled_not_found"] == ["pipe:PIPE-108"]


def test_broken_lifecycle_is_caught(starter_copy):
    def f(rows):
        i = rows[0].index("to_status")
        rows[2][i] = "RETIRED"  # second event of the first component: INSTALLED -> RETIRED
        return rows

    _edit(starter_copy / "csv" / "component_status_event.csv", f)
    assert _status(starter_copy, "lifecycle_transitions")[1]["status"] == "FAIL"
