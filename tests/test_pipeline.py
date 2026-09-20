"""Offline tests. All complaint records and JEV responses here are SYNTHETIC fixtures."""
from __future__ import annotations

import argparse
import copy
import csv
import importlib.util
import json
import math
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
from pyproj import Transformer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import download_dataset as dl
import generate_heatmap as hm


def response_fixture():
    return {
        "model": "jev-1.13.0",
        "answers": {
            "severity": {"type": "score", "score": 2.5, "confidence": 0.8,
                         "legend": {str(i): f"SYNTHETIC level {i}" for i in range(5)},
                         "probabilities": {"0": 0.05, "1": 0.1, "2": 0.25, "3": 0.5, "4": 0.1}},
            "safety_concern": {"type": "noul", "noul": 0.7},
            "category": {"type": "choice", "choice": "infrastructure", "confidence": 0.7,
                         "probabilities": {key: 0.7 if key == "infrastructure" else 0.05 for key in hm.CATEGORIES}},
        },
        "usage": {"input_tokens": 123, "output_tokens": 0},
    }


def rows_fixture():
    inverse = Transformer.from_crs("EPSG:32618", "EPSG:4326", always_xy=True)
    rows = []
    for index in range(12):
        east = 583_250 + (index // 4) * 1_000
        north = 4_508_250 + (index // 4) * 500
        longitude, latitude = inverse.transform(east, north)
        rows.append({
            "unique_key": str(index + 1), "created_date": "2026-09-01T12:00:00.000",
            "agency": "DOT", "complaint_type": "SYNTHETIC TEST street condition",
            "descriptor": "SYNTHETIC TEST pothole" if index < 8 else "SYNTHETIC TEST cracked pavement",
            "location_type": "Street", "status": "Open", "borough": "MANHATTAN",
            "latitude": str(latitude), "longitude": str(longitude),
        })
    return rows


def write_fixture(path: Path, rows=None):
    rows = rows_fixture() if rows is None else rows
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)
        self.input = self.path / "synthetic.csv"
        write_fixture(self.input)

    def tearDown(self):
        self.temp.cleanup()

    def args(self, *extra):
        return hm.parser().parse_args([
            "--input", str(self.input), "--output-dir", str(self.path / "output"),
            "--cache", str(self.path / "cache.sqlite"), "--dpi", "60", *extra,
        ])

    def download_args(self, *extra):
        return dl.parser().parse_args([
            "--start", "2026-09-01", "--end", "2026-09-02",
            "--output", str(self.path / "download.csv"), "--page-size", "2", *extra,
        ])

    def test_date_bounds_are_inclusive_exclusive(self):
        text = dl.build_where(date(2026, 9, 1), date(2026, 9, 2), "QUEENS")
        self.assertIn("created_date >= '2026-09-01T00:00:00'", text)
        self.assertIn("created_date < '2026-09-02T00:00:00'", text)
        self.assertIn("borough = 'QUEENS'", text)

    def test_invalid_date_range_and_archive(self):
        with self.assertRaises(ValueError):
            dl.build_where(date(2026, 9, 2), date(2026, 9, 1))
        with self.assertRaises(ValueError):
            dl.build_where(date(2019, 1, 1), date(2026, 9, 1))

    def test_sql_escaping(self):
        self.assertEqual(dl.sql_text("a'b"), "'a''b'")

    def test_pagination_has_tie_breaker(self):
        text = dl.cursor_where("BASE", ("2026-09-01T12:00:00.000", "2"))
        self.assertIn("unique_key > '2'", text)
        self.assertIn("created_date =", text)

    def test_download_pagination_and_hash(self):
        rows = rows_fixture()[:3]
        with patch.object(dl, "get_rows", side_effect=[[{"total": "3"}], rows[:2], rows[2:]]) as get:
            args = self.download_args()
            metadata = dl.download(args, MagicMock())
        self.assertEqual(metadata["downloaded_rows"], 3)
        self.assertEqual(metadata["page_count"], 2)
        self.assertEqual(metadata["sha256"], dl.sha256_file(args.output))
        self.assertIn("unique_key > '2'", get.call_args_list[2].args[1]["$where"])
        self.assertEqual(len(pd.read_csv(args.output)), 3)

    def test_download_rejects_budget_before_fetching_rows(self):
        with patch.object(dl, "get_rows", return_value=[{"total": "10"}]) as get:
            with self.assertRaisesRegex(ValueError, "exceed"):
                dl.download(self.download_args("--max-rows", "2"), MagicMock())
        self.assertEqual(get.call_count, 1)
        self.assertFalse((self.path / "download.csv").exists())

    def test_download_exact_page_limit_and_empty_last_page(self):
        with patch.object(dl, "get_rows", side_effect=[[{"total": "2"}], rows_fixture()[:2], []]):
            metadata = dl.download(self.download_args("--max-rows", "2"), MagicMock())
        self.assertEqual(metadata["downloaded_rows"], 2)

    def test_download_does_not_publish_partial_after_source_growth(self):
        with patch.object(dl, "get_rows", side_effect=[[{"total": "2"}], rows_fixture()[:2], rows_fixture()[2:3]]):
            with self.assertRaisesRegex(ValueError, "grew"):
                dl.download(self.download_args("--max-rows", "2"), MagicMock())
        self.assertFalse((self.path / "download.csv").exists())
        self.assertFalse(list(self.path.glob("*.part")))

    def test_duplicate_download_ids_stop_without_publishing(self):
        row = rows_fixture()[0]
        with patch.object(dl, "get_rows", side_effect=[[{"total": "2"}], [row, row]]):
            with self.assertRaisesRegex(ValueError, "Duplicate"):
                dl.download(self.download_args(), MagicMock())
        self.assertFalse((self.path / "download.csv").exists())

    def test_parse_nested_answers_and_zero_based_score(self):
        parsed = hm.parse_response(response_fixture())
        self.assertAlmostEqual(parsed["severity"], 2.5 / 4)
        self.assertAlmostEqual(parsed["safety_probability"], 0.7)
        self.assertAlmostEqual(parsed["ambiguity"], (0.2 + 0.3 + 0.84) / 3)

    def test_parse_score_optional_probabilities(self):
        data = response_fixture()
        del data["answers"]["severity"]["probabilities"]
        self.assertEqual(hm.parse_response(data)["severity_raw"], 2.5)

    def test_invalid_probability_rejected(self):
        data = response_fixture()
        data["answers"]["safety_concern"]["noul"] = 1.7
        with self.assertRaises(ValueError):
            hm.parse_response(data)

    def test_nan_and_bool_rejected(self):
        for value in [float("nan"), True, "0.5"]:
            with self.assertRaises(ValueError):
                hm.bounded_number(value, "test")

    def test_missing_answer_rejected(self):
        data = response_fixture()
        del data["answers"]["severity"]
        with self.assertRaises(ValueError):
            hm.parse_response(data)

    def test_wrong_distribution_rejected(self):
        data = response_fixture()
        data["answers"]["category"]["probabilities"]["other"] = 0.8
        with self.assertRaises(ValueError):
            hm.parse_response(data)

    def test_states_exclude_locations_and_resolution(self):
        row = rows_fixture()[0]
        state = hm.semantic_state(row)
        for key in ("latitude", "longitude", "borough", "unique_key", "created_date", "status"):
            self.assertNotIn(key, state["complaint"])
        other = dict(row, latitude="40.90", borough="QUEENS", status="Closed")
        self.assertEqual(state, hm.semantic_state(other))

    def test_cache_key_changes_with_model_and_prompt(self):
        state = hm.semantic_state(rows_fixture()[0])
        key = hm.cache_key(state, "jev-1.13.0")
        self.assertNotEqual(key, hm.cache_key(state, "jev-other"))
        changed = copy.deepcopy(hm.QUESTIONS)
        changed["severity"]["instructions"] += " Changed rubric."
        with patch.object(hm, "QUESTIONS", changed):
            self.assertNotEqual(key, hm.cache_key(state, "jev-1.13.0"))

    def test_cache_roundtrip_and_alias_expiry(self):
        cache = hm.Cache(self.path / "test.sqlite")
        try:
            cache.put("a", {"state": "SYNTHETIC TEST"}, response_fixture())
            self.assertIsNotNone(cache.get("a", "jev-1.13.0", 0))
            self.assertIsNone(cache.get("a", "jev-latest", 0))
        finally:
            cache.close()

    def test_quality_filter(self):
        rows = rows_fixture()[:4]
        rows[0]["latitude"] = ""
        rows[1]["longitude"] = "0"
        rows[2]["unique_key"] = rows[3]["unique_key"]
        write_fixture(self.input, rows)
        frame, stats = hm.read_records(self.input)
        self.assertEqual(len(frame), 1)
        self.assertEqual(stats["duplicate_ids_removed_keep_last"], 1)
        self.assertEqual(stats["invalid_or_missing_coordinates_excluded"], 1)
        self.assertEqual(stats["outside_broad_nyc_extent_excluded"], 1)

    def test_old_model_columns_are_ignored(self):
        frame = pd.read_csv(self.input)
        frame["severity"] = 1
        frame.to_csv(self.input, index=False)
        clean, _ = hm.read_records(self.input)
        self.assertNotIn("severity", clean)

    def test_dry_run_makes_no_api_calls(self):
        frame, _ = hm.read_records(self.input)
        with patch.object(hm.JevClient, "evaluate", side_effect=AssertionError("API call forbidden")):
            _, stats = hm.score_records(frame, self.args("--dry-run"))
        self.assertEqual(stats["unique_descriptive_states"], 2)
        self.assertEqual(stats["minimum_new_api_calls"], 2)

    def test_budget_preflight_makes_no_api_calls(self):
        frame, _ = hm.read_records(self.input)
        with patch.object(hm.JevClient, "evaluate", side_effect=AssertionError("API call forbidden")):
            with self.assertRaisesRegex(ValueError, "Need at least"):
                hm.score_records(frame, self.args("--max-api-calls", "1"))

    def test_missing_key_does_not_fall_back_to_mock_scores(self):
        frame, _ = hm.read_records(self.input)
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(ValueError, "TYPESAFE_API_KEY"):
                hm.score_records(frame, self.args())

    def test_repeated_states_scored_once_and_cached_rerun(self):
        frame, _ = hm.read_records(self.input)
        args = self.args()
        with patch.dict("os.environ", {"TYPESAFE_API_KEY": "SYNTHETIC-TEST-NOT-A-REAL-KEY"}):
            with patch.object(hm.JevClient, "evaluate", return_value=response_fixture()) as evaluate:
                scored, _ = hm.score_records(frame, args)
        self.assertEqual(evaluate.call_count, 2)
        self.assertEqual(scored.severity.notna().sum(), 12)
        with patch.dict("os.environ", {}, clear=True):
            with patch.object(hm.JevClient, "evaluate", side_effect=AssertionError("API call forbidden")):
                _, stats = hm.score_records(frame, self.args("--max-api-calls", "0"))
        self.assertEqual(stats["cached_states_before_run"], 2)

    def test_http_request_shape_and_budget(self):
        client = hm.JevClient("SYNTHETIC-TEST-KEY", max_calls=1, rps=20, max_attempts=3)
        response = MagicMock(status_code=200)
        response.json.return_value = response_fixture()
        body = {"model": hm.DEFAULT_MODEL, "state": {"SYNTHETIC": "TEST"}, "questions": hm.QUESTIONS}
        try:
            with patch.object(client.session, "post", return_value=response) as post:
                result = client.evaluate(body)
                self.assertEqual(result["model"], hm.DEFAULT_MODEL)
                self.assertEqual(post.call_args.kwargs["json"], body)
                self.assertFalse(post.call_args.kwargs["allow_redirects"])
                with self.assertRaisesRegex(RuntimeError, "max-api-calls"):
                    client.evaluate(body)
            self.assertEqual(client.calls, 1)
        finally:
            client.close()

    def test_retry_is_bounded_and_counted(self):
        client = hm.JevClient("SYNTHETIC-TEST-KEY", max_calls=2, rps=20, max_attempts=3)
        error = MagicMock(status_code=429, headers={"Retry-After": "0"})
        success = MagicMock(status_code=200)
        success.json.return_value = response_fixture()
        try:
            with patch.object(client.session, "post", side_effect=[error, success]), patch.object(hm.time, "sleep"):
                client.evaluate({"SYNTHETIC": "TEST"})
            self.assertEqual(client.calls, 2)
        finally:
            client.close()

    def test_auth_failure_is_not_retried(self):
        client = hm.JevClient("SYNTHETIC-TEST-KEY", max_calls=5, rps=20, max_attempts=3)
        try:
            with patch.object(client.session, "post", return_value=MagicMock(status_code=401)) as post:
                with self.assertRaisesRegex(RuntimeError, "401"):
                    client.evaluate({})
            self.assertEqual(post.call_count, 1)
        finally:
            client.close()

    def test_mean_severity_not_density_and_category_mass_conserved(self):
        frame, _ = hm.read_records(self.input)
        parsed = hm.parse_response(response_fixture())
        for key, value in parsed.items():
            frame[key] = value
        _, grid = hm.aggregate_grid(frame, 500, "jev")
        self.assertEqual(grid.raw_count.sum(), len(frame))
        self.assertTrue(np.allclose(grid.severity_mean, 0.625))
        cat_mass = grid[[f"category_mass_{c}" for c in hm.CATEGORIES]].sum(axis=1)
        self.assertTrue(np.allclose(cat_mass, grid.scored_count))
        self.assertTrue(grid.composite.between(0, 1).all())
        self.assertTrue(grid.scored_fraction.eq(1).all())

    def test_sparse_cells_suppressed_only_for_semantic_maps(self):
        frame, _ = hm.read_records(self.input)
        for key, value in hm.parse_response(response_fixture()).items():
            frame[key] = value
        _, grid = hm.aggregate_grid(frame, 500, "jev")
        args = self.args("--min-count", "5")
        self.assertEqual(len(hm.eligible_cells(grid, "severity_mean", args)), 0)
        self.assertEqual(len(hm.eligible_cells(grid, "raw_count", args)), 3)

    def test_input_metadata_mismatch_stops(self):
        self.input.with_suffix(".csv.metadata.json").write_text(json.dumps({"sha256": "bad"}))
        with self.assertRaisesRegex(ValueError, "metadata hash"):
            hm.run(self.args("--mode", "baseline"))

    def test_full_baseline_render_without_api(self):
        with patch.object(hm.JevClient, "evaluate", side_effect=AssertionError("API call forbidden")):
            result = hm.run(self.args("--mode", "baseline"))
        out = self.path / "output"
        for name in ["index.html", "raw_count.png", "raw_count.html", "grid.csv", "grid.geojson", "run_metadata.json"]:
            self.assertTrue((out / name).exists(), name)
        self.assertEqual(result["mode"], "baseline")
        self.assertEqual(len(json.loads((out / "grid.geojson").read_text())["features"]), 3)
        self.assertNotIn("severity_mean.png", [p.name for p in out.iterdir()])

    def test_full_jev_render_uses_explicit_mock_only_in_test(self):
        with patch.dict("os.environ", {"TYPESAFE_API_KEY": "SYNTHETIC-TEST-NOT-A-REAL-KEY"}):
            with patch.object(hm.JevClient, "evaluate", return_value=response_fixture()):
                result = hm.run(self.args("--category-maps"))
        out = self.path / "output"
        self.assertEqual(len(result["rendered_metrics"]), 13)
        self.assertEqual(result["scoring"]["unique_descriptive_states"], 2)
        self.assertEqual(len(list(out.glob("*.png"))), 13)
        self.assertIn("not validated risk", (out / "index.html").read_text())
        self.assertIn("JEV", (out / "severity_mean.html").read_text())


if __name__ == "__main__":
    unittest.main(verbosity=2)
