"""Reproducibility: same seed -> byte-identical files; stored config -> same simulation output."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from plantsim import io, topology
from plantsim.generate import build, load_ground_truth
from plantsim.scenarios import config_hash
from plantsim.simulator import mode_settings, simulate


def resimulate_all(data_dir: Path) -> list[dict]:
    """Re-run every simulation from its published config + ground-truth fault list."""
    t = io.load_dataset(data_dir)
    gt = {r["simulation_run_id"]: r for r in load_ground_truth(data_dir)["runs"]}
    modes = mode_settings(t)
    plants = {}
    out = []
    for run in t["simulation_run"]:
        rid = run["simulation_run_id"]
        if not run["input_config_json"]:
            out.append({"simulation_run_id": rid, "reproducible": False, "reason": "no input config"})
            continue
        fac = run["facility_id"]
        plants.setdefault(fac, topology.build(t, fac))
        cfg = json.loads(run["input_config_json"])
        res = simulate(plants[fac], modes, cfg, gt[rid]["faults"], run["seed"])
        out.append(
            {
                "simulation_run_id": rid,
                "reproducible": res.output_hash == run["output_hash"]
                and config_hash(run["input_config_json"]) == run["config_hash"],
                "stored_output_hash": run["output_hash"],
                "recomputed_output_hash": res.output_hash,
            }
        )
    return out


def _files(manifest: dict) -> dict[str, tuple]:
    return {f["path"]: (f["rows"], f["sha256"]) for f in manifest["files"]}


def repro_check(work: Path, seed: int, points: int, profile: str) -> dict:
    if work.exists():
        shutil.rmtree(work)
    a = build(work / "run_a", seed, points, profile)["manifest"]
    b = build(work / "run_b", seed, points, profile)["manifest"]
    fa, fb = _files(a), _files(b)
    mismatched = sorted(p for p in set(fa) | set(fb) if fa.get(p) != fb.get(p))
    other = build(work / "other_seed", seed + 1, points, profile)["manifest"]
    tel = "csv/telemetry.csv"
    sims = resimulate_all(work / "run_a")
    with_cfg = [s for s in sims if "recomputed_output_hash" in s]
    result = {
        "seed": seed,
        "profile": profile,
        "telemetry_points": points,
        "identical": not mismatched and all(s["reproducible"] for s in with_cfg),
        "files_compared": len(set(fa) | set(fb)),
        "mismatched_files": mismatched,
        "row_counts": {p: fa[p][0] for p in sorted(fa) if fa[p][0] is not None},
        "sha256_run_a": {p: fa[p][1] for p in sorted(fa)},
        "sha256_run_b": {p: fb[p][1] for p in sorted(fb)},
        "different_seed_changes_telemetry": _files(other)[tel][1] != fa[tel][1],
        "runs_resimulated": len(with_cfg),
        "runs_matching_output_hash": sum(s["reproducible"] for s in with_cfg),
        "runs_not_reproducible": [s for s in sims if not s["reproducible"]],
    }
    shutil.rmtree(work / "other_seed")
    return result
