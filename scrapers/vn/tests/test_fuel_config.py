"""Scope isolation and compatibility with the frozen 1.0.0 schema/normalizer."""
import copy
import hashlib
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import vw_vn_stock as a
import prepare_schedule
from test_collector import car, page, site_config, site_html


FIXTURES = Path(__file__).parent / "fixtures"
LEGACY = json.loads((FIXTURES / "legacy_v100_synthetic.json").read_text())
LEGACY_SCOPE = "volkswagen-es-new-BQ-N-fuel-B-D-passenger-v1"
OTHER_SCOPE = "volkswagen-es-new-BQ-N-fuel-E-H-passenger-v1"


def legacy_database(path):
    """Build from the actual frozen 1.0.0 SQL, never from the new SCHEMA constant."""
    db = sqlite3.connect(path)
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript((FIXTURES / "legacy_v100_schema.sql").read_text())
    r = LEGACY["record"]
    with db:
        db.executemany("INSERT INTO metadata VALUES(?,?)", [("scope", LEGACY_SCOPE), ("schema_version", "1")])
        db.execute("INSERT INTO runs VALUES(?,?,?,?,?,?,?,?,?,?)", (
            "legacy-run", "2026-01-01", r["observed_at"], r["observed_at"], "success", 1, 1,
            None, "synthetic-raw", a.json_text(LEGACY["config"])))
        db.execute("INSERT INTO vehicles VALUES(?,?,?,NULL,0,'present',?,?)", (
            r["vehicle_id"], r["observed_at"], r["observed_at"], a.json_text(r["business"]), r["business_hash"]))
        db.execute("INSERT INTO observations VALUES(?,?,?,?)", (
            "legacy-run", r["vehicle_id"], r["observed_at"], a.json_text(r)))
        db.execute("INSERT INTO events(run_id,vehicle_id,detected_at,kind,old_json,new_json) VALUES(?,?,?,'new','null','null')", (
            "legacy-run", r["vehicle_id"], r["observed_at"]))
    db.close()


class FuelConfigTests(unittest.TestCase):
    def test_default_scope_is_exactly_the_legacy_namespace(self):
        self.assertEqual(a.scope_from_config(), LEGACY_SCOPE)
        self.assertEqual(a.scope_from_config(LEGACY["config"]), LEGACY_SCOPE)
        self.assertEqual(a.scope_from_config({"fuel_codes": ["B", "D"]}), LEGACY_SCOPE)

    def test_order_case_whitespace_and_duplicates_do_not_change_scope(self):
        cfg = {"fuel_codes": [" d ", "b", "D"]}
        self.assertEqual(a.selected_fuel_codes(cfg), ["B", "D"])
        self.assertEqual(a.scope_from_config(cfg), LEGACY_SCOPE)
        self.assertEqual(a.scope_from_config({"fuel_codes": ["H", "E"]}), OTHER_SCOPE)

    def test_missing_fuels_in_an_old_config_load_as_b_d(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder) / "config.json"
            p.write_text(json.dumps(LEGACY["config"]))
            loaded = a.load_config(p)
            self.assertEqual(loaded["fuel_codes"], ["B", "D"])
            self.assertEqual(a.scope_from_config(loaded), LEGACY_SCOPE)

    def test_explicit_empty_null_scalar_or_invalid_codes_fail(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder) / "config.json"
            for codes in ([], None, "B,D", True, [None], [1], [""], [" "], ["B-D"], ["B&D"], ["E/H"], ["*"]):
                p.write_text(json.dumps({"fuel_codes": codes}))
                with self.subTest(codes=codes), self.assertRaises(a.CollectorError):
                    a.load_config(p)

    def test_source_and_api_filters_share_the_canonical_selection(self):
        cfg = {"fuel_codes": ["H", "E", "E"]}
        query = parse_qs(urlsplit(a.source_url(cfg)).query)
        self.assertEqual(query["t_petr-app"], ["E", "H"])
        self.assertEqual(query["t_cartype-app"], ["N"])
        self.assertEqual(a.filters_from_config(cfg)["t_petr"], ["E", "H"])
        self.assertEqual(a.source_url(), a.SOURCE_BASE + "/__app/search/cars.app?pageIndex-app=1&t_manuf-app=BQ&t_cartype-app=N&t_petr-app=B&t_petr-app=D&sort-app=DATE_OFFER&sortdirection-app=DESC")

    def test_other_fuels_accepted_only_in_their_config(self):
        others = {"fuel_codes": ["E", "H"]}
        for fuel in ("B", "D", "E", "H"):
            row = car()
            row["motor"]["fuel"]["code"] = fuel
            cfg = others if fuel in ("E", "H") else {}
            self.assertEqual(a.normalize(row, cfg)["business"]["fuel_code"], fuel)
            with self.assertRaises(a.CollectorError):
                a.normalize(row, {} if cfg else others)

    def test_no_mutable_scope_or_filter_state_leaks_between_configurations(self):
        others = {"fuel_codes": ["H", "E"]}
        result = a.filters_from_config(others)
        result["t_petr"].append("X")
        self.assertEqual(others["fuel_codes"], ["H", "E"])
        self.assertEqual(a.filters_from_config()["t_petr"], ["B", "D"])
        self.assertEqual(a.scope_from_config(), LEGACY_SCOPE)
        self.assertEqual(a.scope_from_config(others), OTHER_SCOPE)

    def test_normalized_record_and_business_hash_match_version_100(self):
        current = a.normalize(LEGACY["car"], LEGACY["config"])
        expected = {k: v for k, v in LEGACY["record"].items() if k != "observed_at"}
        self.assertEqual(current, expected)

    def test_task_names_preserve_legacy_and_separate_other_fuels(self):
        self.assertEqual(prepare_schedule.task_name_for_config({}), "vw-vn-stock")
        self.assertEqual(prepare_schedule.task_name_for_config({"fuel_codes": ["D", "B"]}), "vw-vn-stock")
        self.assertEqual(prepare_schedule.task_name_for_config({"fuel_codes": ["H", "E"]}), "vw-vn-stock-e-h")


class LegacyDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.path = self.folder / "vw_vn_stock.sqlite3"
        legacy_database(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def digest(self):
        return hashlib.sha256(self.path.read_bytes()).hexdigest()

    def test_legacy_database_opens_and_closes_without_changing_any_bytes(self):
        before = self.digest()
        db = a.connect_db(self.path, LEGACY["config"])
        self.assertEqual(db.execute("SELECT value FROM metadata WHERE key='scope'").fetchone()[0], LEGACY_SCOPE)
        self.assertEqual(json.loads(db.execute("SELECT data_json FROM observations").fetchone()[0]), LEGACY["record"])
        self.assertNotIn("fuel_codes", json.loads(db.execute("SELECT config_json FROM runs").fetchone()[0]))
        db.close()
        self.assertEqual(before, self.digest())

    def test_wrong_fuels_fail_without_rewriting_the_legacy_database(self):
        before = self.digest()
        for codes in (["E", "H"], ["B"], ["B", "D", "E", "H"]):
            with self.subTest(codes=codes), self.assertRaises(a.CollectorError):
                a.connect_db(self.path, {"fuel_codes": codes})
            self.assertEqual(before, self.digest())

    def test_wrong_scope_run_and_export_fail_before_network_or_exports(self):
        before = self.digest()
        cfg = self.folder / "others.json"
        cfg.write_text(json.dumps({"data_dir": str(self.folder), "fuel_codes": ["E", "H"]}))
        with patch.object(a.HttpClient, "get", side_effect=AssertionError("Must not access the network")):
            self.assertEqual(a.main(["run", "--config", str(cfg)]), 1)
            self.assertEqual(a.main(["export", "--config", str(cfg)]), 1)
        self.assertFalse((self.folder / "exports").exists())
        self.assertEqual(before, self.digest())

    def test_other_database_gets_its_own_scope_and_never_relabels_legacy(self):
        before = self.digest()
        db = a.connect_db(self.folder / "data_otros/vw_vn_stock.sqlite3", {"fuel_codes": ["E", "H"]})
        self.assertEqual(db.execute("SELECT value FROM metadata WHERE key='scope'").fetchone()[0], OTHER_SCOPE)
        db.close()
        self.assertEqual(before, self.digest())

    def test_legacy_history_continues_without_false_new_or_changed_events(self):
        cfg = {**LEGACY["config"], "data_dir": str(self.folder)}
        def transport(client, url, headers=None):
            if url.endswith("robots.txt"):
                return b"User-agent: *\nDisallow:\n", {}
            if url == a.source_url(cfg):
                return site_html(), {}
            if url == a.CONFIG_URL:
                return site_config(), {}
            params = parse_qs(urlsplit(url).query)
            self.assertEqual(params["t_petr"], ["B", "D"])
            return json.dumps(page([LEGACY["car"]], 0, 1, size=12)).encode(), {"Date": format_datetime(datetime.now(timezone.utc))}
        with patch.object(a.HttpClient, "get", transport):
            self.assertEqual(a.live_run(cfg), 0)
        db = a.connect_db(self.path, cfg)
        try:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM observations").fetchone()[0], 2)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT first_seen_at FROM vehicles").fetchone()[0], LEGACY["record"]["observed_at"])
            self.assertEqual(db.execute("SELECT value FROM metadata WHERE key='scope'").fetchone()[0], LEGACY_SCOPE)
        finally:
            db.close()

    def test_other_fuels_end_to_end_preserves_both_query_values_and_scope(self):
        cfg = {**a.DEFAULTS, "data_dir": str(self.folder / "otros"), "fuel_codes": ["H", "E"]}
        electric, hybrid = car("E"), car("H")
        electric["motor"]["fuel"] = {"code": "E", "value": "Eléctrico"}
        hybrid["motor"]["fuel"] = {"code": "H", "value": "Híbrido"}
        def transport(client, url, headers=None):
            if url.endswith("robots.txt"):
                return b"User-agent: *\nDisallow:\n", {}
            if url == a.source_url(cfg):
                self.assertEqual(parse_qs(urlsplit(url).query)["t_petr-app"], ["E", "H"])
                return site_html(), {}
            if url == a.CONFIG_URL:
                return site_config(), {}
            self.assertEqual(parse_qs(urlsplit(url).query)["t_petr"], ["E", "H"])
            self.assertEqual(headers["Referer"], a.source_url(cfg))
            return json.dumps(page([electric, hybrid], 0, 2, size=12)).encode(), {"Date": format_datetime(datetime.now(timezone.utc))}
        with patch.object(a.HttpClient, "get", transport):
            self.assertEqual(a.live_run(cfg), 0)
        with patch.object(a.HttpClient, "get", side_effect=AssertionError("Same-day capture must not repeat")):
            self.assertEqual(a.live_run(cfg), 0)
        db = a.connect_db(Path(cfg["data_dir"]) / "vw_vn_stock.sqlite3", cfg)
        try:
            self.assertEqual(db.execute("SELECT value FROM metadata WHERE key='scope'").fetchone()[0], OTHER_SCOPE)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM observations").fetchone()[0], 2)
        finally:
            db.close()
        manifest = json.loads(next((Path(cfg["data_dir"]) / "raw").glob("*/manifest.json")).read_text())
        self.assertEqual(manifest["scope"], OTHER_SCOPE)
        self.assertEqual(manifest["filters"]["t_petr"], ["E", "H"])


if __name__ == "__main__":
    unittest.main()
