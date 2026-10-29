"""Deterministic synthetic dataset generator.

Produces the *source feed* served by the mock API plus a separate *oracle*
(expected results) used only by verification - the pipeline never reads it.

Feed model
----------
Every row the source exposes has:

* ``source_record_id`` - unique id of that row in the source's change feed
  (``ORDV-...`` / ``EVTV-...``). Several rows can describe the same business
  entity (versions of an order, re-deliveries of an event).
* ``source_updated_at`` - when the source last modified that row. The API
  pages in ``(source_updated_at, source_record_id)`` order.
* ``release_phase`` (server-side only, never served) - the mock only exposes
  rows whose phase <= its current phase. Phase 2 contains normal new changes
  *and* late-arriving rows whose timestamp is older than phase 1's watermark.

Same ``seed`` + same parameters  ==> byte-identical output.
"""

from __future__ import annotations

import json
import random
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from .models import CURRENCIES
from .transformations import iso

BASE_TIME = datetime(2026, 1, 1, tzinfo=UTC)
LATE_OFFSET = timedelta(minutes=20)  # recovered by the default 1h lookback
VERY_LATE_OFFSET = timedelta(days=2)  # beyond lookback -> needs a backfill
UNKNOWN_ORDER_ID = "ORD-99999999"

STATUS_PATHS = [
    ["pending", "paid", "shipped", "delivered"],
    ["pending", "paid", "shipped"],
    ["pending", "paid"],
    ["pending", "cancelled"],
    ["pending", "paid", "shipped", "delivered", "refunded"],
    ["pending"],
]
STATUS_EVENT = {
    "pending": "order_created",
    "paid": "payment_authorized",
    "shipped": "order_shipped",
    "delivered": "order_delivered",
    "cancelled": "order_cancelled",
    "refunded": "order_refunded",
}


@dataclass
class FeedRow:
    entity: str
    source_record_id: str
    source_updated_at: datetime
    release_phase: int
    payload: Any

    def to_json(self) -> dict[str, Any]:
        return {
            "feed": {
                "source_record_id": self.source_record_id,
                "source_updated_at": iso(self.source_updated_at),
                "release_phase": self.release_phase,
            },
            "payload": self.payload,
        }


@dataclass
class Label:
    entity: str
    source_record_id: str
    scenario: str
    expected_outcome: str  # outcome on FIRST delivery (record_outcomes.outcome)
    expected_error_code: str | None = None
    note: str = ""


@dataclass
class Dataset:
    params: dict[str, Any]
    orders: list[FeedRow] = field(default_factory=list)
    order_events: list[FeedRow] = field(default_factory=list)
    labels: list[Label] = field(default_factory=list)
    # oracle for the final target state after ALL phases are fully ingested
    expected_orders: dict[str, str] = field(default_factory=dict)  # order_id -> source_record_id
    expected_events: dict[str, str] = field(default_factory=dict)  # event_id -> event_type

    def feed(self, entity: str) -> list[FeedRow]:
        return self.orders if entity == "orders" else self.order_events

    def manifest(self) -> dict[str, Any]:
        def by_phase(rows: list[FeedRow]) -> dict[str, int]:
            out: dict[str, int] = {}
            for r in rows:
                out[str(r.release_phase)] = out.get(str(r.release_phase), 0) + 1
            return out

        quarantine: dict[str, dict[str, int]] = {}
        for lab in self.labels:
            if lab.expected_outcome == "quarantined":
                q = quarantine.setdefault(lab.entity, {})
                q[lab.expected_error_code or "?"] = q.get(lab.expected_error_code or "?", 0) + 1
        return {
            "params": self.params,
            "source_rows": {"orders": len(self.orders), "order_events": len(self.order_events)},
            "source_rows_by_phase": {
                "orders": by_phase(self.orders),
                "order_events": by_phase(self.order_events),
            },
            "expected_target_rows": {
                "orders": len(self.expected_orders),
                "order_events": len(self.expected_events),
            },
            "expected_quarantine_by_code": quarantine,
            "labelled_rows": len(self.labels),
        }


class _Builder:
    def __init__(self, seed: int, order_count: int, edge_cases: bool, phases: bool) -> None:
        self.rng = random.Random(seed)
        self.n = order_count
        self.edge_cases = edge_cases
        self.phases = phases
        self.ds = Dataset(
            params={
                "seed": seed,
                "order_count": order_count,
                "edge_cases": edge_cases,
                "phases": phases,
            }
        )
        self._ordv = 0
        self._evtv = 0
        self._evt = 0
        # clean versions per order: list of (row, status)
        self.versions: dict[str, list[FeedRow]] = {}
        self.events_by_order: dict[str, list[FeedRow]] = {}

    # ------------------------------------------------------------ id helpers
    def ordv(self) -> str:
        self._ordv += 1
        return f"ORDV-{self._ordv:09d}"

    def evtv(self) -> str:
        self._evtv += 1
        return f"EVTV-{self._evtv:09d}"

    def event_id(self) -> str:
        self._evt += 1
        return f"EVT-{self._evt:010d}"

    def label(
        self, row: FeedRow, scenario: str, outcome: str, code: str | None = None, note: str = ""
    ) -> None:
        self.ds.labels.append(
            Label(row.entity, row.source_record_id, scenario, outcome, code, note)
        )

    # ------------------------------------------------------------ clean data
    def money(self) -> str:
        return str(Decimal(self.rng.randint(100, 200_000)) / 100)

    def clean(self) -> None:
        customers = max(10, self.n // 5)
        for i in range(self.n):
            order_id = f"ORD-{i + 1:08d}"
            created = BASE_TIME + timedelta(seconds=30 * i + self.rng.randint(0, 20))
            customer = f"CUST-{self.rng.randint(1, customers):06d}"
            amount = self.money()
            currency = self.rng.choice(CURRENCIES)
            path = self.rng.choice(STATUS_PATHS)
            updated = created
            rows: list[FeedRow] = []
            for step, status in enumerate(path):
                if step:
                    updated = updated + timedelta(minutes=self.rng.randint(5, 240))
                # Second precision on purpose: equal timestamps (ties) are common,
                # which exercises the composite (timestamp, id) cursor.
                src_ts = updated + timedelta(seconds=self.rng.randint(0, 3))
                payload = {
                    "order_id": order_id,
                    "customer_id": customer,
                    "order_status": status,
                    "order_amount": amount,
                    "currency": currency,
                    "created_at": iso(created),
                    "updated_at": iso(updated),
                    "source_updated_at": iso(src_ts),
                    "source_record_id": self.ordv(),
                }
                rows.append(FeedRow("orders", payload["source_record_id"], src_ts, 1, payload))
                # event for this transition
                ev_ts = updated
                ev_src = max(ev_ts, src_ts) + timedelta(seconds=self.rng.randint(0, 5))
                ev = {
                    "event_id": self.event_id(),
                    "order_id": order_id,
                    "event_type": STATUS_EVENT[status],
                    "event_timestamp": iso(ev_ts),
                    "source_updated_at": iso(ev_src),
                    "source_record_id": self.evtv(),
                }
                self.events_by_order.setdefault(order_id, []).append(
                    FeedRow("order_events", ev["source_record_id"], ev_src, 1, ev)
                )
            self.versions[order_id] = rows

    def assign_phases(self) -> datetime:
        all_rows = [r for rows in self.versions.values() for r in rows]
        all_rows += [r for rows in self.events_by_order.values() for r in rows]
        if not all_rows:
            return BASE_TIME
        stamps = sorted(r.source_updated_at for r in all_rows)
        cutoff = stamps[int(len(stamps) * 0.8)] if self.phases else stamps[-1]
        for r in all_rows:
            r.release_phase = 1 if r.source_updated_at <= cutoff else 2
        phase1 = [r.source_updated_at for r in all_rows if r.release_phase == 1]
        return max(phase1) if phase1 else BASE_TIME

    # ------------------------------------------------------------ edge cases
    def standalone_order(self, ts: datetime, phase: int, **overrides: Any) -> FeedRow:
        order_id = f"ORD-{self.n + 1 + len(self.extra_ids):08d}"
        self.extra_ids.append(order_id)
        created = ts - timedelta(minutes=1)
        payload: dict[str, Any] = {
            "order_id": order_id,
            "customer_id": f"CUST-{self.rng.randint(1, 999):06d}",
            "order_status": "paid",
            "order_amount": self.money(),
            "currency": "USD",
            "created_at": iso(created),
            "updated_at": iso(ts),
            "source_updated_at": iso(ts),
            "source_record_id": self.ordv(),
        }
        payload.update(overrides)
        return FeedRow("orders", payload["source_record_id"], ts, phase, payload)

    def random_phase1_ts(self, watermark: datetime) -> datetime:
        span = max(1, int((watermark - BASE_TIME).total_seconds()))
        return BASE_TIME + timedelta(seconds=self.rng.randint(0, span))

    def edge(self, watermark: datetime) -> list[FeedRow]:
        self.extra_ids: list[str] = []
        extra: list[FeedRow] = []
        if self.n == 0:
            return extra
        k = max(2, self.n // 200)  # rows per scenario
        pool = [oid for oid, rows in self.versions.items() if rows[0].release_phase == 1]
        self.rng.shuffle(pool)
        take = iter(pool)

        # 1. invalid standalone orders (one payload mutation each)
        invalid_cases: list[tuple[str, dict[str, Any], str | None]] = [
            ("missing_customer_id", {"customer_id": None}, "MISSING_REQUIRED_FIELD"),
            ("missing_order_id", {"order_id": None}, "MISSING_REQUIRED_FIELD"),
            ("null_status", {"order_status": None}, "NULL_NOT_ALLOWED"),
            ("unknown_status", {"order_status": "teleported"}, "INVALID_ENUM"),
            ("negative_amount", {"order_amount": "-15.00"}, "NEGATIVE_AMOUNT"),
            ("unparsable_amount", {"order_amount": "12.3.4"}, "INVALID_AMOUNT"),
            ("amount_precision", {"order_amount": "10.123"}, "INVALID_AMOUNT_PRECISION"),
            ("amount_too_large", {"order_amount": "10000000000.00"}, "AMOUNT_OUT_OF_RANGE"),
            ("amount_wrong_type", {"order_amount": {"value": "10.00"}}, "INVALID_TYPE"),
            ("bad_currency", {"currency": "XYZ"}, "INVALID_CURRENCY"),
            ("bad_timestamp", {"created_at": "31/01/2026 10:00"}, "INVALID_TIMESTAMP"),
            ("naive_timestamp", {"created_at": "2026-01-01T00:00:00"}, "INVALID_TIMESTAMP"),
            ("updated_before_created", None, "TIMESTAMP_ORDER"),
            ("bad_identifier", {"customer_id": "customer#1"}, "INVALID_IDENTIFIER"),
        ]
        for _ in range(k):
            for scenario, mutation, code in invalid_cases:
                ts = self.random_phase1_ts(watermark)
                row = self.standalone_order(ts, 1)
                if mutation is None:  # updated_at before created_at
                    row.payload["created_at"] = iso(ts + timedelta(hours=1))
                else:
                    for key, value in mutation.items():
                        if value is None and scenario.startswith("missing_"):
                            del row.payload[key]
                        else:
                            row.payload[key] = value
                self.label(row, f"invalid:{scenario}", "quarantined", code)
                extra.append(row)
            # a non-object element inside the data array
            ts = self.random_phase1_ts(watermark)
            rid = self.ordv()
            row = FeedRow("orders", rid, ts, 1, f"corrupted-record-{rid}")
            self.label(row, "invalid:not_an_object", "quarantined", "NOT_AN_OBJECT")
            extra.append(row)

        # 2. boundary values that ARE valid
        for _ in range(k):
            for scenario, overrides in [
                ("zero_amount", {"order_amount": "0.00"}),
                ("max_amount", {"order_amount": "9999999999.99"}),
                ("json_number_amount", {"order_amount": 149.5}),
                ("messy_enum_case", {"order_status": "  SHIPPED ", "currency": "eur"}),
                ("offset_timestamp", {}),
            ]:
                ts = self.random_phase1_ts(watermark)
                row = self.standalone_order(ts, 1, **overrides)
                if scenario == "offset_timestamp":  # same instant written as +05:30
                    created = ts - timedelta(minutes=1)
                    row.payload["created_at"] = (created + timedelta(hours=5, minutes=30)).strftime(
                        "%Y-%m-%dT%H:%M:%S+05:30"
                    )
                self.label(row, f"boundary:{scenario}", "inserted")
                self.ds.expected_orders[row.payload["order_id"]] = row.source_record_id
                extra.append(row)

        for _ in range(k):
            # 3. exact duplicate delivery of an existing version (new feed row id)
            oid = next(take)
            src = self.versions[oid][0]
            dup = dict(src.payload, source_record_id=self.ordv())
            row = FeedRow("orders", dup["source_record_id"], src.source_updated_at, 1, dup)
            self.label(
                row, "duplicate:identical", "duplicate", note=f"copy of {src.source_record_id}"
            )
            extra.append(row)

            # 4. conflicting duplicate: same order + same version timestamp, different content
            oid = next(take)
            src = self.versions[oid][0]
            amount = str(Decimal(src.payload["order_amount"]) + Decimal("1.00"))
            dup = dict(src.payload, source_record_id=self.ordv(), order_amount=amount)
            row = FeedRow("orders", dup["source_record_id"], src.source_updated_at, 1, dup)
            self.label(
                row,
                "duplicate:conflicting",
                "quarantined",
                "CONFLICTING_DUPLICATE",
                note=f"conflicts with {src.source_record_id}",
            )
            extra.append(row)

            # 5. invalid later version of a valid order -> quarantined, previous state kept
            oid = next(take)
            last = self.versions[oid][-1]
            ts = last.source_updated_at + timedelta(seconds=1)
            bad = dict(
                last.payload,
                source_record_id=self.ordv(),
                order_status="lost_in_space",
                source_updated_at=iso(ts),
            )
            row = FeedRow("orders", bad["source_record_id"], ts, last.release_phase, bad)
            self.label(row, "invalid:later_version", "quarantined", "INVALID_ENUM")
            extra.append(row)

            # 6. additive schema drift: unknown fields on an otherwise normal version
            oid = next(take)
            self.versions[oid][0].payload.update({"loyalty_tier": "gold", "sales_channel": "web"})
            self.label(self.versions[oid][0], "schema:unknown_fields", "inserted")

        # 7. late-arriving rows (released in phase 2 with timestamps < phase-1 watermark)
        if self.phases:
            late_ts = watermark - LATE_OFFSET
            for _ in range(k):
                row = self.standalone_order(late_ts, 2)
                self.label(row, "late:new_order_within_lookback", "inserted")
                self.ds.expected_orders[row.payload["order_id"]] = row.source_record_id
                extra.append(row)

                row = self.standalone_order(watermark - VERY_LATE_OFFSET, 2)
                self.label(
                    row,
                    "late:new_order_beyond_lookback",
                    "inserted",
                    note="missed by incremental runs; recovered by backfill",
                )
                self.ds.expected_orders[row.payload["order_id"]] = row.source_record_id
                extra.append(row)

                # an OLDER version of an order that already has a newer version
                # An OLDER version of an order whose newest version is already in
                # phase 1, delivered late (phase 2) one second before that version.
                candidates = [
                    o
                    for o, rows in self.versions.items()
                    if len(rows) >= 2
                    and rows[-1].release_phase == 1
                    and rows[-1].source_updated_at > late_ts
                    and o not in self.used_for_stale
                ]
                if candidates:
                    oid = candidates[0]
                    self.used_for_stale.add(oid)
                    newest = self.versions[oid][-1]
                    old_ts = newest.source_updated_at - timedelta(seconds=1)
                    old = dict(
                        newest.payload,
                        source_record_id=self.ordv(),
                        order_status="pending",
                        updated_at=newest.payload["created_at"],
                        source_updated_at=iso(old_ts),
                    )
                    row = FeedRow("orders", old["source_record_id"], old_ts, 2, old)
                    # Only "stale" when delivered AFTER the newer version (incremental
                    # runs); a single full load sees rows in timestamp order instead.
                    self.label(
                        row,
                        "late:older_version",
                        "stale",
                        note="order-dependent: stale only after the newer version",
                    )
                    extra.append(row)
        return extra

    def event_edges(self) -> list[FeedRow]:
        extra: list[FeedRow] = []
        if self.n == 0:
            return extra
        k = max(2, self.n // 200)
        phase1_events = [
            r for rows in self.events_by_order.values() for r in rows if r.release_phase == 1
        ]
        self.rng.shuffle(phase1_events)
        take = iter(phase1_events)
        for _ in range(k):
            src = next(take)  # exact re-delivery
            dup = dict(src.payload, source_record_id=self.evtv())
            row = FeedRow("order_events", dup["source_record_id"], src.source_updated_at, 1, dup)
            self.label(row, "duplicate:identical_event", "duplicate")
            extra.append(row)

            src = next(take)  # same event_id, different content
            other = (
                "order_cancelled"
                if src.payload["event_type"] != "order_cancelled"
                else "order_refunded"
            )
            dup = dict(src.payload, source_record_id=self.evtv(), event_type=other)
            row = FeedRow("order_events", dup["source_record_id"], src.source_updated_at, 1, dup)
            self.label(row, "duplicate:conflicting_event", "quarantined", "CONFLICTING_DUPLICATE")
            extra.append(row)

            src = next(take)  # invalid reference key
            ev = dict(
                src.payload,
                event_id=self.event_id(),
                source_record_id=self.evtv(),
                order_id=UNKNOWN_ORDER_ID,
            )
            row = FeedRow("order_events", ev["source_record_id"], src.source_updated_at, 1, ev)
            self.label(row, "invalid:unknown_order_id", "quarantined", "UNKNOWN_ORDER_ID")
            extra.append(row)

            src = next(take)  # unknown event type
            ev = dict(
                src.payload,
                event_id=self.event_id(),
                source_record_id=self.evtv(),
                event_type="order_teleported",
            )
            row = FeedRow("order_events", ev["source_record_id"], src.source_updated_at, 1, ev)
            self.label(row, "invalid:event_type", "quarantined", "INVALID_ENUM")
            extra.append(row)

            src = next(take)  # missing required field
            ev = dict(src.payload, event_id=self.event_id(), source_record_id=self.evtv())
            del ev["event_timestamp"]
            row = FeedRow("order_events", ev["source_record_id"], src.source_updated_at, 1, ev)
            self.label(
                row, "invalid:missing_event_timestamp", "quarantined", "MISSING_REQUIRED_FIELD"
            )
            extra.append(row)

            src = next(take)  # out-of-order: occurred long ago, delivered now
            ev = dict(
                src.payload,
                event_id=self.event_id(),
                source_record_id=self.evtv(),
                event_type="payment_authorized",
                event_timestamp=iso(src.source_updated_at - timedelta(days=1)),
            )
            row = FeedRow("order_events", ev["source_record_id"], src.source_updated_at, 1, ev)
            self.label(
                row,
                "late:out_of_order_event",
                "inserted",
                note="event_timestamp earlier than the order's other events",
            )
            self.ds.expected_events[ev["event_id"]] = ev["event_type"]
            extra.append(row)
        return extra

    def build(self) -> Dataset:
        self.used_for_stale: set[str] = set()
        self.clean()
        watermark = self.assign_phases()
        extra_orders = self.edge(watermark) if self.edge_cases else []
        extra_events = self.event_edges() if self.edge_cases else []

        labelled = {lab.source_record_id for lab in self.ds.labels}
        for oid, rows in self.versions.items():
            self.ds.expected_orders[oid] = rows[-1].source_record_id
            for i, r in enumerate(rows):
                if r.source_record_id not in labelled:
                    self.label(r, "clean:version", "inserted" if i == 0 else "updated")
        for rows in self.events_by_order.values():
            for r in rows:
                self.ds.expected_events[r.payload["event_id"]] = r.payload["event_type"]
                self.label(r, "clean:event", "inserted")

        orders = [r for rows in self.versions.values() for r in rows] + extra_orders
        events = [r for rows in self.events_by_order.values() for r in rows] + extra_events
        key = lambda r: (r.source_updated_at, r.source_record_id)  # noqa: E731
        self.ds.orders = sorted(orders, key=key)
        self.ds.order_events = sorted(events, key=key)
        self.ds.labels.sort(key=lambda lab: (lab.entity, lab.source_record_id))
        return self.ds


def generate(
    seed: int = 42, order_count: int = 200, *, edge_cases: bool = True, phases: bool = True
) -> Dataset:
    if order_count < 0:
        raise ValueError("order_count must be >= 0")
    if edge_cases and 0 < order_count < 50:
        raise ValueError("edge cases need at least 50 orders (use --no-edge-cases)")
    return _Builder(seed, order_count, edge_cases, phases).build()


# ------------------------------------------------------------------ files
def write_dataset(ds: Dataset, out_dir: Path) -> None:
    """Layout:
    source/{orders,order_events}.jsonl  - what the mock API serves (+ feed metadata)
    expected/manifest.json              - oracle summary (counts)
    expected/labels.jsonl               - per-row scenario labels
    expected/final_state.json           - expected target keys after full ingestion
    """
    (out_dir / "source").mkdir(parents=True, exist_ok=True)
    (out_dir / "expected").mkdir(parents=True, exist_ok=True)
    for entity in ("orders", "order_events"):
        with open(out_dir / "source" / f"{entity}.jsonl", "w", encoding="utf-8") as fh:
            for row in ds.feed(entity):
                fh.write(json.dumps(row.to_json(), sort_keys=True) + "\n")
    with open(out_dir / "expected" / "labels.jsonl", "w", encoding="utf-8") as fh:
        for lab in ds.labels:
            fh.write(json.dumps(asdict(lab), sort_keys=True) + "\n")
    (out_dir / "expected" / "manifest.json").write_text(
        json.dumps(ds.manifest(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (out_dir / "expected" / "final_state.json").write_text(
        json.dumps(
            {"orders": ds.expected_orders, "order_events": ds.expected_events}, sort_keys=True
        )
        + "\n",
        encoding="utf-8",
    )


def load_feed(source_dir: Path) -> dict[str, list[FeedRow]]:
    feeds: dict[str, list[FeedRow]] = {}
    for entity in ("orders", "order_events"):
        rows = []
        path = source_dir / f"{entity}.jsonl"
        if path.exists():
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    obj = json.loads(line)
                    meta = obj["feed"]
                    rows.append(
                        FeedRow(
                            entity,
                            meta["source_record_id"],
                            datetime.fromisoformat(meta["source_updated_at"]),
                            int(meta["release_phase"]),
                            obj["payload"],
                        )
                    )
        feeds[entity] = rows
    return feeds


def load_expected(dataset_dir: Path) -> dict[str, Any]:
    exp = dataset_dir / "expected"
    labels = [
        json.loads(line)
        for line in (exp / "labels.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    return {
        "manifest": json.loads((exp / "manifest.json").read_text(encoding="utf-8")),
        "final_state": json.loads((exp / "final_state.json").read_text(encoding="utf-8")),
        "labels": labels,
    }
