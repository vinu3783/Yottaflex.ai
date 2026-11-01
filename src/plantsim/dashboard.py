"""Build a self-contained operator dashboard from an exported dataset."""

# ruff: noqa: E501

from __future__ import annotations

import html
import json
from pathlib import Path

from plantsim import io
from plantsim.schema import TABLE_BY_NAME

_COLORS = {
    "LEAK": "#fb7185",
    "BLOCKAGE": "#fbbf24",
    "PUMP_DEGRADATION": "#a78bfa",
    "SENSOR_STUCK": "#38bdf8",
    "SENSOR_BIAS": "#38bdf8",
    "SENSOR_DRIFT": "#38bdf8",
}


def _e(value: object) -> str:
    return html.escape(str(value if value is not None else "—"), quote=True)


def _metric(value: object, suffix: str = "") -> str:
    return "—" if value is None else f"{float(value) * 100:.0f}{suffix}"


def _response_chart(faults: list[dict]) -> str:
    points = [f for f in faults if f.get("delay_min") is not None]
    if not points:
        return '<p class="empty">No detected-fault response times are available.</p>'

    width, height = 760, 230
    left, right, top, bottom = 54, 18, 18, 42
    max_delay = max(60, max(float(f["delay_min"]) for f in points))
    plot_width, plot_height = width - left - right, height - top - bottom
    ticks = sorted({0, round(max_delay / 2 / 15) * 15, round(max_delay / 15) * 15})
    ticks = [tick for tick in ticks if tick <= max_delay]
    elements = []
    for tick in ticks:
        y = top + plot_height * (1 - tick / max_delay)
        elements.append(
            f'<line class="grid" x1="{left}" y1="{y:.1f}" x2="{width-right}" y2="{y:.1f}"/>'
            f'<text class="axis" x="{left-10}" y="{y+4:.1f}" text-anchor="end">{tick}m</text>'
        )
    for index, fault in enumerate(points):
        x = left + plot_width * (index / max(1, len(points) - 1))
        delay = float(fault["delay_min"])
        y = top + plot_height * (1 - delay / max_delay)
        color = _COLORS.get(fault.get("issue_type"), "#34d399")
        title = _e(f'{fault.get("component_id", "Unknown")}: {delay:g} min')
        elements.append(
            f'<line class="stem" x1="{x:.1f}" y1="{top+plot_height}" x2="{x:.1f}" y2="{y:.1f}"/>'
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="6" fill="{color}">'
            f"<title>{title}</title></circle>"
        )
    labels = [
        f'<text class="axis" x="{left + plot_width * (i / max(1, len(points)-1)):.1f}" '
        f'y="{height-14}" text-anchor="middle">{_e(fault.get("component_id"))}</text>'
        for i, fault in enumerate(points)
    ]
    return (
        f'<svg viewBox="0 0 {width} {height}" role="img" '
        'aria-label="Detection delay in minutes for each detected fault">'
        + "".join(elements + labels)
        + "</svg>"
    )


def build_dashboard(data_dir: Path, output: Path) -> Path:
    """Write a standalone HTML dashboard based on reports and exported alarm records."""
    evaluation_path = data_dir / "reports" / "evaluation.json"
    manifest_path = data_dir / "manifest.json"
    for required in (evaluation_path, manifest_path, data_dir / "csv" / "alarm.csv"):
        if not required.is_file():
            raise FileNotFoundError(f"dashboard input not found: {required}")

    evaluation = json.loads(evaluation_path.read_text("utf-8"))
    manifest = json.loads(manifest_path.read_text("utf-8"))
    alarms = io.read_csv(data_dir / "csv" / "alarm.csv", TABLE_BY_NAME["alarm"])
    headline = evaluation["headline"]
    detected_faults = evaluation["faults"]
    active_count = sum(alarm["state"] == "ACTIVE" for alarm in alarms)
    telemetry_count = manifest["telemetry_points"]
    profile = _e(manifest["profile"])

    alarm_rows = []
    for alarm in sorted(alarms, key=lambda row: (row["raised_at"] or "", row["alarm_id"])):
        state = alarm["state"] or "UNKNOWN"
        alarm_rows.append(
            "<tr "
            f'data-state="{_e(state)}" data-type="{_e(alarm["alarm_type"])}">'
            f'<td class="mono">{_e(alarm["raised_at"])}</td>'
            f'<td><span class="severity {_e((alarm["severity"] or "").lower())}">'
            f"{_e(alarm['severity'])}</span></td>"
            f"<td>{_e(alarm['alarm_type'])}</td>"
            f'<td class="mono">{_e(alarm["facility_id"])}</td>'
            f'<td class="mono">{_e(alarm["simulation_run_id"])}</td>'
            f'<td><span class="state {_e(state.lower())}">{_e(state)}</span></td>'
            f"<td>{_e(alarm['message'])}</td></tr>"
        )
    if not alarm_rows:
        alarm_rows.append('<tr><td colspan="7" class="empty">No alarms in this dataset.</td></tr>')

    fault_ratio = min(1.0, max(0.0, float(headline["fault_recall"] or 0)))
    ring_end = fault_ratio * 360
    html_doc = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark">
<title>Plant Ops Center · YottaFlex</title>
<style>
:root{{--bg:#08111d;--panel:#101d2b;--panel2:#142437;--line:#24364a;--text:#e5edf5;
--muted:#91a4b8;--green:#34d399;--blue:#38bdf8;--red:#fb7185;--amber:#fbbf24}}
*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(ellipse at 75% -10%,#15334a 0,transparent 42%),var(--bg);
color:var(--text);font:14px/1.5 ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}
.wrap{{max-width:1240px;margin:auto;padding:38px 28px 56px}}header{{display:flex;justify-content:space-between;
align-items:flex-start;gap:24px;margin-bottom:28px}}.eyebrow,.label{{color:var(--muted);font-size:11px;
letter-spacing:.14em;text-transform:uppercase;font-weight:700}}h1{{font-size:clamp(28px,4vw,42px);letter-spacing:-.04em;
margin:7px 0 5px}}.sub{{color:var(--muted);margin:0}}.live{{border:1px solid #23664f;color:#75efbd;
background:#0d2b25;border-radius:999px;padding:7px 12px;font-size:12px;white-space:nowrap}}
.live:before{{content:"";display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--green);
margin-right:8px;box-shadow:0 0 12px var(--green)}}.grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));
gap:14px;margin-bottom:16px}}.card,.panel{{border:1px solid var(--line);background:linear-gradient(145deg,#122234,var(--panel));
border-radius:16px}}.card{{padding:19px 20px;min-height:121px}}.value{{font-size:34px;font-weight:720;
letter-spacing:-.05em;margin:8px 0 1px}}.hint{{color:var(--muted);font-size:12px}}.accent-green{{color:var(--green)}}
.accent-blue{{color:var(--blue)}}.accent-amber{{color:var(--amber)}}.layout{{display:grid;grid-template-columns:1.15fr .85fr;
gap:16px;margin-bottom:16px}}.panel{{padding:21px 22px;min-width:0}}.panel-head{{display:flex;justify-content:space-between;
align-items:flex-start;gap:12px;margin-bottom:16px}}h2{{font-size:16px;margin:0 0 3px;letter-spacing:-.02em}}
.panel-note{{color:var(--muted);font-size:12px}}.legend{{display:flex;gap:12px;flex-wrap:wrap;color:var(--muted);
font-size:11px}}.dot{{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:5px}}
svg{{width:100%;height:auto;overflow:visible}}.grid line{{stroke:var(--line);stroke-dasharray:3 5}}
.axis{{fill:var(--muted);font-size:10px}}.stem{{stroke:#29425a;stroke-width:2}}.empty{{color:var(--muted);
padding:22px;text-align:center}}.score{{display:flex;align-items:center;gap:20px;margin:8px 0 18px}}
.ring{{width:92px;height:92px;border-radius:50%;display:grid;place-items:center;flex:none;
background:conic-gradient(var(--green) 0deg 360deg,#203247 360deg);position:relative}}
.ring:after{{content:"";position:absolute;inset:7px;border-radius:50%;background:var(--panel)}}
.ring span{{position:relative;z-index:1;font-size:23px;font-weight:750}}.score-copy strong{{font-size:17px}}
.score-copy p{{color:var(--muted);margin:4px 0 0}}.mini-stats{{display:grid;grid-template-columns:1fr 1fr;
gap:9px}}.mini{{background:var(--panel2);border-radius:10px;padding:11px 12px}}.mini b{{display:block;font-size:17px}}
.mini span{{font-size:11px;color:var(--muted)}}.table-panel{{padding:0;overflow:hidden}}.table-top{{padding:21px 22px 14px;
display:flex;justify-content:space-between;align-items:center;gap:12px}}select{{background:#0b1724;color:var(--text);
border:1px solid var(--line);border-radius:8px;padding:8px 10px;font:inherit}}.scroll{{overflow:auto}}
table{{border-collapse:collapse;width:100%;min-width:860px}}th{{text-align:left;color:var(--muted);font-size:10px;
text-transform:uppercase;letter-spacing:.1em;padding:11px 13px;border-top:1px solid var(--line);border-bottom:1px solid var(--line)}}
td{{padding:12px 13px;border-bottom:1px solid #1c2b3c;vertical-align:top;font-size:12px}}tr:last-child td{{border-bottom:0}}
.mono{{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11px;white-space:nowrap}}
.severity,.state{{display:inline-block;border-radius:999px;padding:3px 8px;font-size:10px;font-weight:750;letter-spacing:.04em}}
.high{{color:#ff9eac;background:#46202b}}.medium{{color:#d2b7ff;background:#30264b}}.low{{color:#8edbff;background:#14364b}}
.active{{color:#ff9eac;background:#46202b}}.cleared{{color:#81e7ba;background:#153b31}}
footer{{color:var(--muted);font-size:11px;margin-top:17px;display:flex;justify-content:space-between;gap:12px}}
@media(max-width:850px){{.grid{{grid-template-columns:repeat(2,minmax(0,1fr))}}.layout{{grid-template-columns:1fr}}}}
@media(max-width:520px){{.wrap{{padding:25px 15px 40px}}header{{display:block}}.live{{display:inline-block;margin-top:14px}}
.grid{{gap:9px}}.card{{padding:14px;min-height:110px}}.value{{font-size:27px}}.panel{{padding:16px}}.table-top{{padding:16px;align-items:flex-start;flex-direction:column}}}}
</style>
</head>
<body>
<main class="wrap">
<header><div><div class="eyebrow">YottaFlex · Synthetic plant intelligence</div>
<h1>Plant Ops Center</h1><p class="sub">A physics-first view of detection performance and facility health.</p></div>
<div class="live">OFFLINE DATA · {profile.upper()} PROFILE</div></header>
<section class="grid" aria-label="Dataset summary">
<article class="card"><div class="label">Telemetry analyzed</div><div class="value accent-blue">{telemetry_count:,}</div>
<div class="hint">deterministic sensor readings</div></article>
<article class="card"><div class="label">Fault recall</div><div class="value accent-green">{_metric(headline["fault_recall"])}</div>
<div class="hint">{_e(headline["faults_detected"])} injected process/sensor faults</div></article>
<article class="card"><div class="label">Alarm precision</div><div class="value accent-green">{_metric(headline["alarm_precision"])}</div>
<div class="hint">{_e(headline["false_alarms"])} false process alarms</div></article>
<article class="card"><div class="label">Live alarm queue</div><div class="value accent-amber">{active_count}</div>
<div class="hint">active of {len(alarms)} recorded alarms</div></article>
</section>
<section class="layout">
<article class="panel"><div class="panel-head"><div><h2>Detection response</h2>
<div class="panel-note">Minutes from fault onset to first correct detection · lower is faster</div></div></div>
{_response_chart(detected_faults)}
<div class="legend"><span><i class="dot" style="background:#fb7185"></i>Leak</span>
<span><i class="dot" style="background:#fbbf24"></i>Blockage</span>
<span><i class="dot" style="background:#a78bfa"></i>Pump degradation</span>
<span><i class="dot" style="background:#38bdf8"></i>Sensor fault</span></div></article>
<article class="panel"><div class="panel-head"><div><h2>Evaluation snapshot</h2>
<div class="panel-note">Ground-truth-scored outcome summary</div></div></div>
<div class="score"><div class="ring" style="background:conic-gradient(var(--green) 0deg {ring_end:.1f}deg,#203247 {ring_end:.1f}deg 360deg)">
<span>{_e(headline["faults_detected"])}</span></div>
<div class="score-copy"><strong>Faults correctly handled</strong>
<p>Includes the below-tolerance leak that correctly raised no alarm.</p></div></div>
<div class="mini-stats"><div class="mini"><b>{_e(headline["edge_cases_handled"])}</b><span>edge cases handled</span></div>
<div class="mini"><b>{_metric(headline["data_quality_recall"])}</b><span>data-quality recall</span></div>
<div class="mini"><b>{_e(headline["false_alarms_in_normal_runs"])}</b><span>false alarms on normal runs</span></div>
<div class="mini"><b>{_e(headline["mean_detection_delay_min"])} min</b><span>mean detection delay</span></div>
</div></article></section>
<section class="panel table-panel"><div class="table-top"><div><h2>Alarm register</h2>
<div class="panel-note">Exported detector and operator alarms · select a state to filter</div></div>
<label class="label" for="state-filter">Show <select id="state-filter"><option value="ALL">All states</option>
<option value="ACTIVE">Active</option><option value="CLEARED">Cleared</option></select></label></div>
<div class="scroll"><table><thead><tr><th>Raised at</th><th>Severity</th><th>Alarm type</th>
<th>Facility</th><th>Run</th><th>State</th><th>Evidence</th></tr></thead>
<tbody id="alarm-rows">{''.join(alarm_rows)}</tbody></table></div></section>
<footer><span>Generated from exported alarms and evaluation reports; no external assets or runtime services.</span>
<span>Seed {_e(manifest["seed"])} · {profile} dataset</span></footer>
</main>
<script>
document.getElementById("state-filter").addEventListener("change", function (event) {{
  for (const row of document.querySelectorAll("#alarm-rows tr[data-state]")) {{
    row.hidden = event.target.value !== "ALL" && row.dataset.state !== event.target.value;
  }}
}});
</script>
</body>
</html>
"""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html_doc, encoding="utf-8")
    return output
