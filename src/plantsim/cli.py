"""Command line: plantsim <command> [options]."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from plantsim import config


def _profile_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--profile", choices=sorted(config.PROFILES), default="full")
    p.add_argument("--telemetry-points", type=int, help="override the profile's telemetry volume")
    p.add_argument("--seed", type=int, default=config.DEFAULT_SEED)


def _resolve(args) -> tuple[str, int]:
    if args.telemetry_points:
        return "custom", args.telemetry_points
    return args.profile, config.PROFILES[args.profile].telemetry_points


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="plantsim", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("generate", help="generate, detect, validate and evaluate a dataset")
    _profile_args(g)
    g.add_argument("--out", type=Path, help="output folder (default data/<profile>)")

    v = sub.add_parser("validate", help="re-run validation on an existing dataset")
    v.add_argument("--data", type=Path, default=Path("data/full"))

    e = sub.add_parser("evaluate", help="re-score detector alarms against ground truth")
    e.add_argument("--data", type=Path, default=Path("data/full"))

    r = sub.add_parser("repro-check", help="generate twice and re-simulate every run; compare checksums")
    _profile_args(r)
    r.add_argument("--work", type=Path, default=Path("out/repro"))
    r.add_argument("--report", type=Path, help="where to write the JSON report")

    w = sub.add_parser("whatif", help="simulate an operating change against a baseline run")
    w.add_argument("--data", type=Path, default=Path("data/full"))
    w.add_argument("--base-run", required=True, help="baseline SIM id to start from")
    w.add_argument("--mode", help="mode_code to use for the whole run (e.g. HIGH_DEMAND)")
    w.add_argument("--pump-speed", action="append", default=[], metavar="PUMP-ID=PCT")
    w.add_argument("--valve-opening", action="append", default=[], metavar="VALVE-ID=PCT")
    w.add_argument("--disable-pump", action="append", default=[], metavar="PUMP-ID")
    w.add_argument("--demand-multiplier", type=float, default=1.0)

    sw = sub.add_parser("sweep", help="robustness: regenerate with other seeds, report metrics")
    _profile_args(sw)
    sw.add_argument("--seeds", default="1-20", help="e.g. 1-20 or 3,5,8")
    sw.add_argument("--work", type=Path, default=Path("out/sweep"))
    sw.add_argument("--report", type=Path)

    sub.add_parser("docs", help="regenerate docs/DATA_DICTIONARY.md from the schema")

    args = ap.parse_args(argv)
    t0 = time.perf_counter()

    if args.cmd == "generate":
        from plantsim.generate import build

        profile, points = _resolve(args)
        out = args.out or Path("data") / profile
        result = build(out, args.seed, points, profile)
        print(
            json.dumps(
                {
                    "out": str(out),
                    "validation": result["validation"],
                    "evaluation": result["evaluation"],
                },
                indent=2,
            )
        )
        print(f"done in {time.perf_counter() - t0:.1f} s")
        return 0 if result["validation"] == "PASS" else 1

    if args.cmd == "validate":
        from plantsim import io, validation

        res = validation.validate(args.data)
        io.dump_json(args.data / "reports" / "validation_summary.json", res)
        (args.data / "reports" / "validation_summary.md").write_text(validation.to_markdown(res), "utf-8")
        for c in res["checks"]:
            print(
                f"{c['status']:<20} {c['check']:<28} violations={c['violations']} labelled={c['expected_labelled']}"  # noqa: E501
            )
        print("OVERALL", res["overall_status"])
        return 0 if res["overall_status"] == "PASS" else 1

    if args.cmd == "evaluate":
        from plantsim import evaluation, io

        res = evaluation.evaluate(args.data)
        io.dump_json(args.data / "reports" / "evaluation.json", res)
        (args.data / "reports" / "evaluation.md").write_text(evaluation.to_markdown(res), "utf-8")
        print(json.dumps(res["headline"], indent=2))
        return 0

    if args.cmd == "repro-check":
        from plantsim.repro import repro_check

        profile, points = _resolve(args)
        res = repro_check(args.work, args.seed, points, profile)
        report = args.report or Path("data") / profile / "reports" / "reproducibility.json"
        from plantsim import io

        io.dump_json(report, res)
        print(
            json.dumps(
                {
                    k: res[k]
                    for k in (
                        "identical",
                        "files_compared",
                        "runs_resimulated",
                        "runs_matching_output_hash",
                    )
                },
                indent=2,
            )
        )
        print(f"report: {report}")
        return 0 if res["identical"] else 1

    if args.cmd == "whatif":
        from plantsim.whatif import run_whatif

        def kv(items):
            out = {}
            for it in items:
                k, _, val = it.partition("=")
                out[k] = float(val)
            return out

        res = run_whatif(
            args.data,
            args.base_run,
            mode_code=args.mode,
            pump_speed=kv(args.pump_speed),
            valve_opening=kv(args.valve_opening),
            disabled=args.disable_pump,
            demand_multiplier=args.demand_multiplier,
        )
        print(json.dumps(res["comparison"], indent=2))
        print(f"written to {res['folder']}")
        return 0

    if args.cmd == "sweep":
        from plantsim import io
        from plantsim.sweep import sweep

        if "-" in args.seeds:
            lo, hi = map(int, args.seeds.split("-"))
            seeds = list(range(lo, hi + 1))
        else:
            seeds = [int(x) for x in args.seeds.split(",")]
        profile, points = _resolve(args)
        res = sweep(seeds, points, profile, args.work)
        report = args.report or Path("data") / profile / "reports" / "robustness_sweep.json"
        io.dump_json(report, res)
        print(json.dumps({k: v for k, v in res.items() if k != "per_seed"}, indent=2))
        print(f"report: {report}")
        return 0

    if args.cmd == "docs":
        from plantsim.docsgen import write_data_dictionary

        path = write_data_dictionary(Path("docs/DATA_DICTIONARY.md"))
        print(f"wrote {path}")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
