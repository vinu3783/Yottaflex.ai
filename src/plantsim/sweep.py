"""Robustness sweep: regenerate with other seeds and report detector metrics per seed.

The official dataset uses one seed; this shows the metrics are not an accident of it.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from plantsim.generate import build


def sweep(seeds: list[int], points: int, profile: str, work: Path) -> dict:
    rows = []
    for s in seeds:
        out = work / f"seed-{s}"
        res = build(out, s, points, profile)
        ev = json.loads((out / "reports" / "evaluation.json").read_text("utf-8"))
        rows.append(
            {
                "seed": s,
                "validation": res["validation"],
                **res["evaluation"],
                "false_alarm_details": ev["false_alarm_details"],
                "missed": [f for f in ev["faults"] if f["outcome"] in ("MISSED", "FALSE_ALARM")],
            }
        )
        shutil.rmtree(out)
    n = len(rows)
    total_faults = sum(int(r["faults_detected"].split("/")[1]) for r in rows)
    detected = sum(int(r["faults_detected"].split("/")[0]) for r in rows)
    return {
        "profile": profile,
        "telemetry_points": points,
        "seeds": seeds,
        "validation_pass": sum(r["validation"] == "PASS" for r in rows),
        "faults_detected": f"{detected}/{total_faults}",
        "recall": round(detected / total_faults, 4),
        "false_alarms_total": sum(r["false_alarms"] for r in rows),
        "false_alarms_in_normal_runs_total": sum(r["false_alarms_in_normal_runs"] for r in rows),
        "edge_cases_all_handled": sum(
            r["edge_cases_handled"].split("/")[0] == r["edge_cases_handled"].split("/")[1] for r in rows
        ),
        "runs": n,
        "per_seed": rows,
    }
