#!/usr/bin/env python3
"""Download a bounded, ordered NYC 311 dataset slice. Python 3.10+.

Source: https://data.cityofnewyork.us/d/erm2-nwe9
API: https://dev.socrata.com/docs/queries/
No TypeSafe credential is needed. SOCRATA_APP_TOKEN is optional.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import os
import sys
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

LOG = logging.getLogger("download_311")
ENDPOINT = "https://data.cityofnewyork.us/resource/erm2-nwe9.json"
DATASET_URL = "https://data.cityofnewyork.us/d/erm2-nwe9"
FIELDS = [
    "unique_key", "created_date", "closed_date", "agency", "agency_name",
    "complaint_type", "descriptor", "location_type", "status", "borough",
    "latitude", "longitude",
]
BOROUGHS = ["BRONX", "BROOKLYN", "MANHATTAN", "QUEENS", "STATEN ISLAND"]


def sql_text(value: str) -> str:
    """Escape a SoQL string literal (not a general-purpose SQL interface)."""
    return "'" + value.replace("'", "''") + "'"


def build_where(start: date, end: date, borough: str | None = None) -> str:
    if start < date(2020, 1, 1):
        raise ValueError("This dataset starts in 2020; use the separate archive for 2010-2019.")
    if end <= start:
        raise ValueError("--end must be later than --start; the end date is exclusive.")
    if borough is not None and borough not in BOROUGHS:
        raise ValueError("Unsupported borough.")
    conditions = [
        f"created_date >= {sql_text(start.isoformat() + 'T00:00:00')}",
        f"created_date < {sql_text(end.isoformat() + 'T00:00:00')}",
    ]
    if borough:
        conditions.append(f"borough = {sql_text(borough)}")
    return " AND ".join(conditions)


def cursor_where(base: str, cursor: tuple[str, str] | None) -> str:
    if cursor is None:
        return base
    created, key = map(sql_text, cursor)
    return (
        f"({base}) AND (created_date > {created} OR "
        f"(created_date = {created} AND unique_key > {key}))"
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def make_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=4, connect=4, read=4, backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"], respect_retry_after_header=True,
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers["User-Agent"] = "jev-311-heatmap-example/1.0"
    token = os.getenv("SOCRATA_APP_TOKEN", "").strip()
    if token:
        session.headers["X-App-Token"] = token
    return session


def get_rows(session: requests.Session, params: dict[str, Any]) -> list[dict[str, Any]]:
    response = session.get(ENDPOINT, params=params, timeout=(15, 120))
    response.raise_for_status()
    result = response.json()
    if not isinstance(result, list) or any(not isinstance(row, dict) for row in result):
        raise ValueError("Socrata returned an unexpected response; expected a list of records.")
    return result


def download(args: argparse.Namespace, session: requests.Session) -> dict[str, Any]:
    """Count first, then paginate by (created_date, unique_key), never silently truncate."""
    base = build_where(args.start, args.end, args.borough)
    if not 1 <= args.page_size <= 50_000 or args.max_rows < 1:
        raise ValueError("--page-size must be 1..50000 and --max-rows must be positive.")
    output = Path(args.output)
    metadata_path = output.with_suffix(output.suffix + ".metadata.json")
    if not args.force and (output.exists() or metadata_path.exists()):
        raise ValueError(f"Output already exists: {output}. Use another path or --force.")
    counts = get_rows(session, {"$select": "count(*) AS total", "$where": base})
    if len(counts) != 1:
        raise ValueError("Socrata did not return a single count record.")
    expected = int(counts[0]["total"])
    LOG.info("Matching requests: %s", f"{expected:,}")
    if expected == 0:
        raise ValueError("No requests matched. Check the date range and borough.")
    if expected > args.max_rows:
        raise ValueError(
            f"{expected:,} requests exceed --max-rows {args.max_rows:,}. "
            "Choose a shorter date range/one borough, or explicitly raise --max-rows. "
            "No chronologically truncated sample was downloaded."
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    temp_name: str | None = None
    cursor: tuple[str, str] | None = None
    seen: set[str] = set()
    started = datetime.now(timezone.utc).isoformat()
    pages = 0
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", newline="", dir=output.parent,
            prefix=output.name + ".", suffix=".part", delete=False,
        ) as handle:
            temp_name = handle.name
            writer = csv.DictWriter(handle, fieldnames=FIELDS, extrasaction="ignore")
            writer.writeheader()
            while True:
                # One extra row detects growth past the budget after the preflight count.
                limit = min(args.page_size, args.max_rows - len(seen) + 1)
                batch = get_rows(session, {
                    "$select": ",".join(FIELDS),
                    "$where": cursor_where(base, cursor),
                    "$order": "created_date ASC,unique_key ASC",
                    "$limit": limit,
                })
                if not batch:
                    break
                pages += 1
                for row in batch:
                    key, created = str(row.get("unique_key", "")), str(row.get("created_date", ""))
                    if not key or not created:
                        raise ValueError("A source row is missing its pagination key/date.")
                    if key in seen:
                        raise ValueError("Duplicate request ID during pagination; rerun for a stable period.")
                    seen.add(key)
                    if len(seen) > args.max_rows:
                        raise ValueError("The live dataset grew past --max-rows during download; rerun.")
                    writer.writerow({field: row.get(field, "") for field in FIELDS})
                next_cursor = (str(batch[-1]["created_date"]), str(batch[-1]["unique_key"]))
                if next_cursor == cursor:
                    raise ValueError("Pagination did not advance; download stopped.")
                cursor = next_cursor
                LOG.info("Downloaded %s requests", f"{len(seen):,}")
                if len(batch) < limit:
                    break
        if not seen:
            raise ValueError("No rows were returned after the count query; rerun.")
        changed = len(seen) != expected
        if changed:
            LOG.warning("Source count changed during download (%s -> %s). See metadata.", expected, len(seen))
        metadata = {
            "dataset_url": DATASET_URL, "api_endpoint": ENDPOINT,
            "start_inclusive": args.start.isoformat(), "end_exclusive": args.end.isoformat(),
            "timestamp_interpretation": "Source floating timestamps; interpreted as NYC local time, not converted to UTC.",
            "borough": args.borough, "where": base, "fields": FIELDS,
            "order": "created_date ASC,unique_key ASC", "pagination": "keyset",
            "row_budget": args.max_rows, "expected_count_before_download": expected,
            "downloaded_rows": len(seen), "page_count": pages,
            "count_changed_during_download": changed,
            "snapshot_note": "Live API is not an atomic snapshot; equal counts do not guarantee no edits.",
            "started_at_utc": started, "finished_at_utc": datetime.now(timezone.utc).isoformat(),
            "sha256": sha256_file(Path(temp_name)),
        }
        os.replace(temp_name, output)
        temp_name = None
        metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        LOG.info("Saved %s and %s", output, metadata_path)
        return metadata
    finally:
        if temp_name:
            Path(temp_name).unlink(missing_ok=True)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--start", required=True, type=date.fromisoformat, help="Inclusive YYYY-MM-DD, NYC source time.")
    p.add_argument("--end", required=True, type=date.fromisoformat, help="Exclusive YYYY-MM-DD, NYC source time.")
    p.add_argument("--borough", type=str.upper, choices=BOROUGHS)
    p.add_argument("--output", type=Path, default=Path("data/nyc311.csv"))
    p.add_argument("--max-rows", type=int, default=100_000, help="Abort rather than truncate above this budget.")
    p.add_argument("--page-size", type=int, default=5_000)
    p.add_argument("--force", action="store_true", help="Allow replacing the output CSV and metadata.")
    return p


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    args = parser().parse_args(argv)
    try:
        with make_session() as session:
            download(args, session)
        return 0
    except (requests.RequestException, ValueError, KeyError, OSError) as exc:
        LOG.error("%s", exc)
        return 1
    except KeyboardInterrupt:
        LOG.error("Interrupted. The previous completed output, if any, was not replaced.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
