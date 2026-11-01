"""Score detector alarms against ground truth.

A detector alarm is *correct* when its type matches an injected fault in the same run, it
was raised at or after fault onset, and the faulty component is the alarm's component or
on its suspected path. Everything else is a false alarm.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

from plantsim import io
from plantsim.simulator import parse_ts


def _located(alarm: dict, component: str) -> bool:
    suspects = (alarm["suspected_components"] or "").split(";")
    return component in suspects or component in (
        alarm["node_id"],
        alarm["pipe_id"],
        alarm["sensor_id"],
    )


def _ratio(a: int, b: int):
    return round(a / b, 4) if b else None


def evaluate(data_dir: Path) -> dict:
    t = io.load_dataset(data_dir)
    gt = json.loads((data_dir / "ground_truth" / "ground_truth.json").read_text("utf-8"))
    labels = gt["labels"]
    scenario = {r["simulation_run_id"]: r["scenario"] for r in gt["runs"]}
    det = [a for a in t["alarm"] if a["alarm_source"] == "DETECTOR"]
    proc = [a for a in det if a["alarm_type"] != "DQ_LEVEL_EXCEEDS_CAPACITY"]
    by_run = defaultdict(list)
    for a in proc:
        by_run[a["simulation_run_id"]].append(a)

    correct_ids: set[str] = set()
    faults = []
    for g in labels:
        if g["category"] not in ("PROCESS_FAULT", "SENSOR_FAULT"):
            continue
        run_alarms = by_run.get(g["simulation_run_id"], [])
        onset = parse_ts(g["onset_ts"])
        row = {
            "simulation_run_id": g["simulation_run_id"],
            "issue_type": g["issue_type"],
            "component_id": g["component_id"],
            "expected_decision": g["expected_decision"],
            "expected_alarm_type": g["expected_alarm_type"],
            "alarm_types_raised": sorted({a["alarm_type"] for a in run_alarms}),
        }
        if g["expected_decision"] == "ALARM":
            hits = [
                a
                for a in run_alarms
                if a["alarm_type"] == g["expected_alarm_type"]
                and parse_ts(a["raised_at"]) >= onset
                and _located(a, g["component_id"])
            ]
            correct_ids |= {a["alarm_id"] for a in hits}
            row["detected"] = bool(hits)
            row["outcome"] = "TRUE_POSITIVE" if hits else "MISSED"
            row["delay_min"] = (
                min((parse_ts(a["raised_at"]) - onset).total_seconds() / 60 for a in hits) if hits else None
            )
        else:
            row["detected"] = False
            row["outcome"] = "TRUE_NEGATIVE" if not run_alarms else "FALSE_ALARM"
            row["delay_min"] = None
        faults.append(row)

    false_alarms = [a for a in proc if a["alarm_id"] not in correct_ids]
    expected = [f for f in faults if f["expected_decision"] == "ALARM"]
    tp = sum(f["detected"] for f in expected)
    delays = [f["delay_min"] for f in expected if f["delay_min"] is not None]

    per_type = {}
    for atype in sorted({f["expected_alarm_type"] for f in expected}):
        exp = [f for f in expected if f["expected_alarm_type"] == atype]
        raised = [a for a in proc if a["alarm_type"] == atype]
        good = [a for a in raised if a["alarm_id"] in correct_ids]
        per_type[atype] = {
            "faults": len(exp),
            "detected": sum(f["detected"] for f in exp),
            "alarms_raised": len(raised),
            "correct_alarms": len(good),
            "recall": _ratio(sum(f["detected"] for f in exp), len(exp)),
            "precision": _ratio(len(good), len(raised)),
        }

    normal_runs = [r for r, s in scenario.items() if s == "NORMAL"]
    runs = {r["simulation_run_id"]: r for r in t["simulation_run"]}
    normal_hours = sum(runs[r]["steps"] * runs[r]["timestep_s"] / 3600 for r in normal_runs)
    normal_false = [a for a in false_alarms if a["simulation_run_id"] in normal_runs]

    # Data quality: level above capacity
    dq_expected = {g["record_id"] for g in labels if g["issue_type"] == "TANK_LEVEL_EXCEEDS_CAPACITY"}
    dq_alarms = [a for a in det if a["alarm_type"] == "DQ_LEVEL_EXCEEDS_CAPACITY"]
    faulty = {
        (g["simulation_run_id"], g["sensor_id"]): g["onset_ts"]
        for g in labels
        if g["category"] == "SENSOR_FAULT"
    }
    dq_explained = {
        a["telemetry_id"]
        for a in dq_alarms
        if (a["simulation_run_id"], a["sensor_id"]) in faulty
        and a["raised_at"] >= faulty[(a["simulation_run_id"], a["sensor_id"])]
    }
    dq_found = {a["telemetry_id"] for a in dq_alarms} - dq_explained

    # Operator alarm reviews
    reviews = {r["alarm_id"]: r for r in t["alarm_review"]}
    label_checks = []
    for g in labels:
        if g["record_table"] == "alarm":
            got = reviews.get(g["record_id"], {}).get("decision")
            label_checks.append(
                {
                    "alarm_id": g["record_id"],
                    "issue_type": g["issue_type"],
                    "expected": g["expected_decision"],
                    "got": got,
                    "correct": got == g["expected_decision"],
                }
            )

    vsum = json.loads((data_dir / "reports" / "validation_summary.json").read_text("utf-8"))
    vstatus = {c["check"]: c for c in vsum["checks"]}
    sql = (data_dir / "sql" / "seed.sql").read_text("utf-8")

    def flow_case(issue):
        rows = [g for g in labels if g["issue_type"] == issue]
        ok = []
        for g in rows:
            f = next(
                x
                for x in faults
                if x["simulation_run_id"] == g["simulation_run_id"] and x["issue_type"] == "LEAK"
            )
            ok.append(f["outcome"] in ("TRUE_POSITIVE", "TRUE_NEGATIVE"))
        return rows, ok

    fc_rows, fc_ok = flow_case("FLOW_CONSERVATION_VIOLATED")
    ft_rows, ft_ok = flow_case("FLOW_CONSERVATION_WITHIN_TOLERANCE")
    pipe_gt = next(g for g in labels if g["issue_type"] == "PIPE_REFERENCES_MISSING_NODE")
    sim_gt = next(g for g in labels if g["issue_type"] == "SIMULATION_OUTPUT_WITHOUT_INPUT_CONFIG")
    mis = [c for c in label_checks if c["issue_type"] == "SENSOR_FAULT_LABELLED_AS_PROCESS_FAULT"]
    edge = [
        {
            "edge_case": "Pipe references missing node",
            "records": [pipe_gt["record_id"]],
            "handled": vstatus["foreign_keys"]["status"] == "EXPECTED_EXCEPTIONS"
            and vstatus["topology_dangling_pipes"]["status"] == "EXPECTED_EXCEPTIONS"
            and f"'pipe', '{pipe_gt['record_id']}'" in sql,
            "how": "foreign_keys + topology_dangling_pipes flag it; SQL load moves it to rejected_record; "
            "topology builder ignores it",
        },
        {
            "edge_case": "Tank level exceeds capacity",
            "records": sorted(dq_expected),
            "handled": dq_expected == dq_found
            and vstatus["level_within_capacity"]["status"] == "EXPECTED_EXCEPTIONS",
            "how": f"DQ-001 raised {len(dq_found & dq_expected)}/{len(dq_expected)} alarms; validation "
            "level_within_capacity lists exactly these readings",
        },
        {
            "edge_case": "Flow conservation violated beyond tolerance",
            "records": [g["record_id"] for g in fc_rows] + [g["record_id"] for g in ft_rows],
            "handled": all(fc_ok) and all(ft_ok) and bool(fc_rows),
            "how": f"MB-001 detected {sum(fc_ok)}/{len(fc_rows)} violations above tolerance and stayed "
            f"silent on {sum(ft_ok)}/{len(ft_rows)} within-tolerance case(s)",
        },
        {
            "edge_case": "Simulation output lacks input configuration",
            "records": [sim_gt["record_id"]],
            "handled": vstatus["required_fields"]["status"] == "EXPECTED_EXCEPTIONS"
            and vstatus["run_reproducibility_inputs"]["status"] == "EXPECTED_EXCEPTIONS",
            "how": "required_fields + run_reproducibility_inputs flag it; detector skips it; SQL rejects it",
        },
        {
            "edge_case": "Sensor fault labelled as process fault",
            "records": [c["alarm_id"] for c in mis],
            "handled": bool(mis) and all(c["correct"] for c in mis),
            "how": "alarm_review reclassifies the operator LEAK alarm because the detector attributes the "
            "imbalance to a flow-meter fault (SNS-003)",
        },
    ]

    balance = []
    for r in normal_runs:
        s = json.loads(runs[r]["output_summary_json"])
        truth = next(x["true_outcomes"] for x in gt["runs"] if x["simulation_run_id"] == r)
        balance.append(
            {
                "simulation_run_id": r,
                "measured_residual_pct": s["facility_balance_residual_pct"],
                "true_balance_error_m3": truth["balance_error_m3"],
            }
        )

    headline = {
        "fault_recall": _ratio(tp, len(expected)),
        "alarm_precision": _ratio(len(correct_ids), len(proc)),
        "faults_detected": f"{tp}/{len(expected)}",
        "false_alarms": len(false_alarms),
        "false_alarms_in_normal_runs": len(normal_false),
        "mean_detection_delay_min": round(sum(delays) / len(delays), 1) if delays else None,
        "edge_cases_handled": f"{sum(e['handled'] for e in edge)}/{len(edge)}",
        "data_quality_recall": _ratio(len(dq_expected & dq_found), len(dq_expected)),
        "data_quality_precision": _ratio(len(dq_expected & dq_found), len(dq_found)),
        "dq_alarms_from_labelled_sensor_faults": len(dq_explained),
    }
    return {
        "headline": headline,
        "faults": faults,
        "per_alarm_type": per_type,
        "false_alarm_details": [
            {k: a[k] for k in ("alarm_id", "simulation_run_id", "alarm_type", "rule_id", "message")}
            | {"scenario": scenario.get(a["simulation_run_id"])}
            for a in false_alarms
        ],
        "false_alarm_rate_per_day_normal": round(len(normal_false) / normal_hours * 24, 4)
        if normal_hours
        else None,
        "edge_cases": edge,
        "operator_alarm_reviews": label_checks,
        "mass_balance_normal_runs": balance,
        "alarm_counts": dict(sorted(Counter(a["alarm_type"] for a in det).items())),
    }


def to_markdown(e: dict) -> str:
    h = e["headline"]
    lines = [
        "# Detection evaluation",
        "",
        "| Metric | Value |",
        "|---|---|",
    ]
    lines += [f"| {k.replace('_', ' ')} | {v} |" for k, v in h.items()]
    lines += [
        "",
        "## Injected faults",
        "",
        "| Run | Fault | Component | Expected | Outcome | Delay (min) | Alarm types raised in run |",
        "|---|---|---|---|---|---:|---|",
    ]
    for f in e["faults"]:
        lines.append(
            f"| {f['simulation_run_id']} | {f['issue_type']} | {f['component_id']} | "
            f"{f['expected_alarm_type'] or 'no alarm'} | {f['outcome']} | "
            f"{'' if f['delay_min'] is None else round(f['delay_min'])} | {', '.join(f['alarm_types_raised']) or '-'} |"  # noqa: E501
        )
    lines += [
        "",
        "## Per alarm type",
        "",
        "| Type | Faults | Detected | Alarms | Correct | Recall | Precision |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for k, v in e["per_alarm_type"].items():
        lines.append(
            f"| {k} | {v['faults']} | {v['detected']} | {v['alarms_raised']} | {v['correct_alarms']} | {v['recall']} | {v['precision']} |"  # noqa: E501
        )
    lines += ["", "## Required edge cases", "", "| Edge case | Handled | How |", "|---|---|---|"]
    for x in e["edge_cases"]:
        lines.append(f"| {x['edge_case']} | {'yes' if x['handled'] else 'NO'} | {x['how']} |")
    lines += ["", "## False alarms", ""]
    if e["false_alarm_details"]:
        lines += [
            f"- {a['alarm_id']} ({a['scenario']} run {a['simulation_run_id']}): {a['alarm_type']} - {a['message']}"  # noqa: E501
            for a in e["false_alarm_details"]
        ]
    else:
        lines.append("None.")
    lines += [
        "",
        "## Facility mass balance on normal runs (measured)",
        "",
        "| Run | Residual % of inflow | True balance error m3 |",
        "|---|---:|---:|",
    ]
    lines += [
        f"| {b['simulation_run_id']} | {b['measured_residual_pct']} | {b['true_balance_error_m3']} |"
        for b in e["mass_balance_normal_runs"]
    ]
    return "\n".join(lines) + "\n"
