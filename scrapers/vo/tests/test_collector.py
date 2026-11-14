import base64
import copy
import csv
import gzip
import io
import json
import math
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

import vw_stock as a


def car(vid="A", amount=20000):
    ident = "ESP" + vid
    return {"carid": ident, "key": base64.b64encode(ident.encode()).decode().rstrip("="),
            "cartype": {"code": "U"}, "mbvManuf": {"code": "V"},
            "model": {"value": "Volkswagen Golf"}, "mileage": {"raw_value": 15000},
            "parsedPrice": {"value": amount, "unit": "EUR", "suffix": {"label": "al contado PVP Concesionario"}},
            "initialreg": "2023-05-19T00:00:00Z",
            "dealer": {"key": "ESP001", "country": "ESP", "name": {"value": "Concesionario"}}}


def record(vid="A", amount=20000):
    value = a.normalize(car(vid, amount))
    value["observed_at"] = a.utc_now()
    return value


def page(cars, offset, total, size=2):
    return {"cars": cars, "meta": {"page": offset // size + 1, "pageItems": size,
            "pageMax": math.ceil(total / size), "resultNumber": total,
            "sort": a.SORT, "sortDirection": a.SORT_DIRECTION}}


def site_html():
    feature = {"name": "gsl_pages", "baseUrl": "https://v3-126-1.gsl.feature-app.io",
               "url": "/apps/pages/pages.umd.js", "featureAppVersion": "3.126.1", "featureAppApiKey": "fixture-only"}
    return ('<script id="spaModel" type="application/json">' + json.dumps({"nested": [feature]}) + '</script>').encode()


def site_config():
    return json.dumps({"spaAsyncConfig": {"serviceConfigEndpoint": {"endpoint": {
        "type": "publish", "country": "es", "language": "es", "content": "onehub_pkw", "envName": "prod"},
        "signature": "fixture-only"}}}).encode()


class ParserTests(unittest.TestCase):
    def test_real_response_contract(self):
        folder = Path(__file__).parent.parent / "examples"
        raw = json.loads(gzip.decompress((folder / "sample1.json.gz").read_bytes()))
        self.assertEqual(raw["meta"]["resultNumber"], 2921)
        self.assertEqual(len(raw["cars"]), 12)
        rows = [a.normalize(c) for c in raw["cars"]]
        self.assertEqual(rows[0]["business"]["price_retail_cents"], 2390000)
        self.assertEqual(rows[0]["business"]["price_new_reference_cents"], 3111800)
        self.assertEqual(rows[0]["business"]["first_registration_date"], "2024-10-31")
        self.assertEqual(rows[0]["business"]["mileage_km"], 22222)
        self.assertIsNone(rows[0]["business"]["reservation"])
        self.assertEqual(len({r["vehicle_id"] for r in rows}), 12)

    def test_real_server_caps_page_size(self):
        p = Path(__file__).parent.parent / "examples" / "sample2_24.json.gz"
        body = json.loads(gzip.decompress(p.read_bytes()))
        self.assertEqual(body["meta"]["pageItems"], 12)
        self.assertEqual(len(a.validate_page(body, 12, a.DEFAULTS)[0]), 12)
        with self.assertRaises(a.CollectorError):
            a.validate_page(body, 24, {**a.DEFAULTS, "page_size": 24})

    def test_price_precision_and_invalid_values(self):
        self.assertEqual(a.cents("14990.01"), 1499001)
        self.assertIsNone(a.cents(None))
        for value in (True, "NaN", float("inf"), -1, "12.001"):
            with self.subTest(value=value), self.assertRaises(a.CollectorError):
                a.cents(value)

    def test_operational_timestamp_does_not_generate_business_changes(self):
        first = car()
        second = {**first, "dateoffer": "2026-11-01T00:00:00Z", "dateproduction": "2024-01-01T00:00:00Z"}
        self.assertEqual(a.normalize(first)["business_hash"], a.normalize(second)["business_hash"])
        self.assertNotEqual(a.normalize(first)["raw_record_hash"], a.normalize(second)["raw_record_hash"])

    def test_identity_conflict_and_wrong_scope(self):
        for value in ({**car(), "carid": None}, {**car(), "carid": "ESPOTHER"},
                      {**car(), "cartype": {"code": "N"}}, {**car(), "mbvManuf": {"code": "A"}},
                      {**car(), "dealer": {"country": "DEU"}}):
            with self.subTest(value=value), self.assertRaises(a.CollectorError):
                a.normalize(value)

    def test_price_reference_and_finance_terms_are_separate(self):
        c = car()
        c["parsedPrice"]["list"] = {"value": 30000}
        c["hypermediaFinancing"] = {"default": {"Rate": "Cuota Mensual 237,97 €", "FinalRate": "Cuota final 12.962,02 €"}}
        b = a.normalize(c)["business"]
        self.assertEqual(b["price_retail_cents"], 2000000)
        self.assertEqual(b["price_new_reference_cents"], 3000000)
        self.assertEqual(b["financing_terms"]["Rate"], "Cuota Mensual 237,97 €")

    def test_reservation_ui_switch_is_not_reservation(self):
        for flag in (False, True):
            self.assertIsNone(a.normalize({**car(), "shouldFetchReserved": flag})["business"]["reservation"])
        self.assertTrue(a.normalize({**car(), "isReserved": True})["business"]["reservation"])

    def test_missing_mileage_and_malformed_vin_remain_unknown(self):
        c = car()
        c["mileage"] = {"value": "15.000"}
        c["vin"] = "not-a-vin"
        value = a.normalize(c)
        self.assertIsNone(value["business"]["mileage_km"])
        self.assertIsNone(value["business"]["vin"])
        self.assertEqual(value["source_vin"], "not-a-vin")


class PaginationTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {**a.DEFAULTS, "page_size": 2, "max_in_flight": 1}

    def collect(self, responses):
        it = iter(responses)
        return a.collect_pages(lambda offset, kind: (next(it), a.utc_now()), self.cfg)

    def test_complete_pagination_and_recheck(self):
        p1, p2 = page([car("A"), car("B")], 0, 3), page([car("C")], 2, 3)
        rows, expected, pages = self.collect([p1, p2, p1])
        self.assertEqual((len(rows), expected, pages), (3, 3, 2))

    def test_duplicates_not_silently_deduplicated(self):
        with self.assertRaises(a.CollectorError):
            self.collect([page([car("A"), car("B")], 0, 3), page([car("A")], 2, 3)])

    def test_truncated_response(self):
        with self.assertRaises(a.CollectorError):
            self.collect([page([car("A")], 0, 3)])

    def test_mid_capture_total_change(self):
        with self.assertRaises(a.CollectorError):
            self.collect([page([car("A"), car("B")], 0, 3), page([car("C"), car("D")], 2, 4)])

    def test_membership_changed_during_pass(self):
        with self.assertRaises(a.CollectorError):
            self.collect([page([car("A"), car("B")], 0, 2), page([car("B"), car("C")], 0, 2)])

    def test_empty_stock_is_not_mass_removal(self):
        with self.assertRaises(a.CollectorError):
            self.collect([page([], 0, 0)])

    def test_wrong_page_size_order_and_page_count(self):
        for field, value in (("page", 2), ("pageItems", 24), ("pageMax", 999), ("sort", "PRICE_SALE")):
            p = page([car("A")], 0, 1)
            p["meta"][field] = value
            with self.subTest(field=field), self.assertRaises(a.CollectorError):
                self.collect([p])

    def test_price_coverage_guard(self):
        p = page([car("A", None)], 0, 1)
        with self.assertRaises(a.CollectorError):
            self.collect([p, p])

    def test_page_limit_does_not_mark_partial_as_complete(self):
        self.cfg["max_pages"] = 1
        with self.assertRaises(a.CollectorError):
            self.collect([page([car("A"), car("B")], 0, 3)])

    def test_concurrent_capture_preserves_page_order_and_completeness(self):
        cfg = {**self.cfg, "max_in_flight": 4}
        calls = []
        def fetch(offset, kind):
            calls.append((offset, kind))
            rows = [car(str(i)) for i in range(offset, min(offset + 2, 9))]
            return page(rows, offset, 9), a.utc_now()
        rows, total, pages = a.collect_pages(fetch, cfg)
        self.assertEqual([r["vehicle_id"] for r in rows], ["ESP" + str(i) for i in range(9)])
        self.assertEqual((total, pages), (9, 5))
        self.assertEqual(calls[-1], (0, "recheck"))

    def test_failure_in_prefetched_page_aborts_capture(self):
        def fetch(offset, kind):
            if offset == 4:
                raise a.CollectorError("Simulated HTTP failure")
            return page([car(str(i)) for i in range(offset, offset + 2)], offset, 8), a.utc_now()
        with self.assertRaises(a.CollectorError):
            a.collect_pages(fetch, {**self.cfg, "max_in_flight": 4})


class AccessTests(unittest.TestCase):
    def test_gzip_response_decodes_before_json_parsing(self):
        packed = gzip.compress(b'{"ok":true}')
        response = io.BytesIO(packed)
        response.headers = {"Content-Encoding": "gzip"}
        client = a.HttpClient({**a.DEFAULTS})
        with patch.object(client.opener, "open", return_value=response):
            raw, _ = client.get("https://example.org/data")
        self.assertEqual(json.loads(raw), {"ok": True})

    def test_robots_merge_groups_and_longest_rule(self):
        rules = "User-agent: *\nDisallow: /app/\nUser-agent: *\nDisallow: /*.jpg$\nAllow: /safe/*.jpg$"
        for path, expected in (("/app/foo", False), ("/foo.jpg", False), ("/safe/foo.jpg", True), ("/stock", True)):
            self.assertEqual(a.robot_rules(rules, "https://example.org" + path, a.USER_AGENT)[0], expected)

    def test_specific_agent_and_crawl_delay(self):
        rules = "User-agent: *\nDisallow: /\nUser-agent: VWStockHistory\nAllow: /\nCrawl-delay: 3"
        self.assertEqual(a.robot_rules(rules, "https://example.org/stock", a.USER_AGENT), (True, 3))

    def test_unavailable_robots_is_recorded_but_data_401_is_not_retried(self):
        cfg = {**a.DEFAULTS}
        client = a.HttpClient(cfg)
        with patch.object(client, "get", side_effect=HTTPError("https://example.org/robots.txt", 401, "Unauthorized", {}, None)):
            self.assertEqual(a.check_robots(client, "https://example.org/data", cfg)["robots_status"], "unavailable")
        with patch.object(client.opener, "open", side_effect=HTTPError("https://example.org/data", 401, "Unauthorized", {}, None)) as call:
            with self.assertRaises(HTTPError):
                client.get("https://example.org/data")
            self.assertEqual(call.call_count, 1)

    def test_server_error_on_robots_is_not_treated_as_permission(self):
        client = a.HttpClient({**a.DEFAULTS})
        with patch.object(client, "get", side_effect=HTTPError("https://example.org/robots.txt", 503, "Unavailable", {}, None)):
            with self.assertRaises(a.CollectorError):
                a.check_robots(client, "https://example.org/data", client.cfg)

    def test_discovery_uses_public_page_and_rejects_unknown_host(self):
        self.assertEqual(a.find_feature_config(site_html().decode())["featureAppVersion"], "3.126.1")
        with self.assertRaises(a.CollectorError):
            a.find_feature_config(site_html().decode().replace("v3-126-1.gsl.feature-app.io", "unknown.invalid"))

    def test_http_date_and_age_validation(self):
        a.check_response_date({"Date": format_datetime(datetime.now(timezone.utc))}, a.DEFAULTS)
        for headers in ({}, {"Date": "not-a-date"}, {"Date": "Wed, 01 Jan 2020 00:00:00 GMT"},
                        {"Date": format_datetime(datetime.now(timezone.utc)), "Age": "90000"}):
            with self.subTest(headers=headers), self.assertRaises(a.CollectorError):
                a.check_response_date(headers, a.DEFAULTS)


class OperationalTests(unittest.TestCase):
    def test_config_validation_and_relative_directory(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.json"
            for config in ({"robots_policy": "authorized"}, {"page_size": 24}, {"request_interval_seconds": float("nan")},
                           {"timezone": 1}, {"missing_confirmations": 1}):
                p.write_text(json.dumps(config))
                with self.subTest(config=config), self.assertRaises(a.CollectorError):
                    a.load_config(p)
            p.write_text('{"data_dir": "stock"}')
            self.assertEqual(a.load_config(p)["data_dir"], str(Path(d) / "stock"))

    def test_csv_formula_safety(self):
        raw = a.csv_bytes([{"name": "=HYPERLINK(x)", "price": 100}], ["name", "price"])
        self.assertIn("'=HYPERLINK(x)", raw.decode("utf-8-sig"))


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)
        self.cfg = {**a.DEFAULTS}
        self.db = a.connect_db(self.path / "test.sqlite3")

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def snapshot(self, day, rows):
        run = a.start_run(self.db, f"2026-10-{day:02}", self.path, self.cfg)
        return run, a.commit_snapshot(self.db, run, rows, len(rows), self.cfg)

    def fleet(self, amount=20000):
        return [record(str(i), amount) for i in range(5)]

    def test_lifecycle_price_change_missing_return_removed(self):
        _, counts = self.snapshot(1, self.fleet())
        self.assertEqual(counts, {"new": 5})
        rows = self.fleet()
        rows[0] = record("0", 19900)
        _, counts = self.snapshot(2, rows)
        self.assertEqual(counts, {"changed": 1})
        event = self.db.execute("SELECT * FROM events WHERE kind='changed'").fetchone()
        self.assertEqual(event["field"], "price_retail_cents")
        self.assertEqual(json.loads(event["old_json"]), 2000000)
        self.assertEqual(json.loads(event["new_json"]), 1990000)
        self.snapshot(3, rows[1:])
        self.assertEqual(self.db.execute("SELECT status FROM vehicles WHERE vehicle_id='ESP0'").fetchone()[0], "missing")
        # A failed day must not count as a disappearance confirmation.
        failed = a.start_run(self.db, "2026-10-04", self.path, self.cfg)
        with self.db:
            self.db.execute("UPDATE runs SET status='failed' WHERE run_id=?", (failed,))
        self.snapshot(5, rows[1:])
        self.assertEqual(self.db.execute("SELECT status FROM vehicles WHERE vehicle_id='ESP0'").fetchone()[0], "missing")
        self.snapshot(6, rows[1:])
        self.assertEqual(self.db.execute("SELECT status FROM vehicles WHERE vehicle_id='ESP0'").fetchone()[0], "removed")
        _, counts = self.snapshot(7, rows)
        self.assertEqual(counts, {"returned": 1})
        self.assertEqual(self.db.execute("SELECT status FROM vehicles WHERE vehicle_id='ESP0'").fetchone()[0], "present")

    def test_anomaly_does_not_mutate_snapshot(self):
        self.snapshot(1, self.fleet())
        run = a.start_run(self.db, "2026-10-02", self.path, self.cfg)
        with self.assertRaises(a.CollectorError):
            a.commit_snapshot(self.db, run, [record("0")], 1, self.cfg)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM observations").fetchone()[0], 5)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM vehicles WHERE status='present'").fetchone()[0], 5)

    def test_churn_guard_even_when_total_is_constant(self):
        self.snapshot(1, self.fleet())
        with self.assertRaises(a.CollectorError):
            self.snapshot(2, [record(str(i)) for i in range(5, 10)])

    def test_same_day_not_rewritten(self):
        self.snapshot(1, self.fleet())
        with self.assertRaises(a.CollectorError):
            self.snapshot(1, self.fleet(19900))
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM observations").fetchone()[0], 5)

    def test_transaction_rolls_back_late_failure(self):
        self.snapshot(1, self.fleet())
        run = a.start_run(self.db, "2026-10-02", self.path, self.cfg)
        rows = self.fleet(19900)
        del rows[-1]["business_hash"]
        with self.assertRaises(KeyError):
            a.commit_snapshot(self.db, run, rows, 5, self.cfg)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM observations").fetchone()[0], 5)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM events WHERE kind='changed'").fetchone()[0], 0)

    def test_export_from_db_is_recoverable(self):
        self.snapshot(1, self.fleet())
        a.export_data(self.db, self.path)
        for file in ("runs.csv", "changes.csv", "history.csv", "stock_latest.csv", "vehicles.csv"):
            self.assertTrue((self.path / "exports" / file).is_file())
        with (self.path / "exports" / "stock_latest.csv").open(encoding="utf-8-sig") as f:
            self.assertEqual(len(list(csv.DictReader(f))), 5)

    def test_default_robot_denial_does_not_fetch_data_or_create_observations(self):
        cfg = {**self.cfg, "data_dir": str(self.path), "timezone": "Europe/Madrid"}
        with patch.object(a.HttpClient, "get", return_value=(b"User-agent: *\nDisallow: /\n", {})) as call:
            with self.assertRaises(a.RobotsDenied):
                a.live_run(cfg)
            self.assertEqual(call.call_count, 1)
            self.assertEqual(call.call_args[0][0], "https://www.volkswagen.es/robots.txt")
        actual = a.connect_db(self.path / "vw_stock.sqlite3")
        try:
            self.assertEqual(actual.execute("SELECT COUNT(*) FROM observations").fetchone()[0], 0)
            self.assertEqual(actual.execute("SELECT status FROM runs").fetchone()[0], "failed")
        finally:
            actual.close()

    def test_complete_live_workflow_with_fixture_transport_and_idempotent_second_run(self):
        cfg = {**self.cfg, "data_dir": str(self.path), "page_size": 2}
        def transport(client, url, headers=None):
            if url.endswith("robots.txt"):
                return b"User-agent: *\nDisallow:\n", {}
            if url == a.SOURCE:
                return site_html(), {}
            if url == a.CONFIG_URL:
                return site_config(), {}
            from urllib.parse import parse_qs, urlsplit
            params = parse_qs(urlsplit(url).query)
            self.assertEqual(params["t_manuf"], ["BQ"])
            self.assertEqual(params["t_cartype"], ["U"])
            self.assertEqual(params["market"], ["passenger"])
            offset = (int(params["page"][0]) - 1) * 2
            rows = [car(str(i)) for i in range(3)][offset:offset + 2]
            return json.dumps(page(rows, offset, 3)).encode(), {"Date": format_datetime(datetime.now(timezone.utc))}
        with patch.object(a.HttpClient, "get", transport):
            self.assertEqual(a.live_run(cfg), 0)
        with patch.object(a.HttpClient, "get", side_effect=AssertionError("No network on second run")):
            self.assertEqual(a.live_run(cfg), 0)
        actual = a.connect_db(self.path / "vw_stock.sqlite3")
        try:
            self.assertEqual(actual.execute("SELECT COUNT(*) FROM observations").fetchone()[0], 3)
            self.assertEqual(actual.execute("SELECT COUNT(*) FROM runs WHERE status='success'").fetchone()[0], 1)
        finally:
            actual.close()
        manifests = list((self.path / "raw").glob("*/manifest.json"))
        self.assertEqual(len(manifests), 1)
        manifest = json.loads(manifests[0].read_text())
        self.assertEqual(manifest["capture_status"], "complete")
        self.assertEqual(len(manifest["pages"]), 3)

    def test_process_lock_prevents_parallel_run(self):
        with a.process_lock(self.path / "lock"):
            with self.assertRaises(a.CollectorError):
                with a.process_lock(self.path / "lock"):
                    pass




if __name__ == "__main__":
    unittest.main()
