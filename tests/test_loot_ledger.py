"""Pure local accounting tests. Run: .venv/Scripts/python tests/test_loot_ledger.py."""
import importlib
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class LootLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "ledger.json"
        try:
            self.module = importlib.import_module("loot_ledger")
        except ModuleNotFoundError:
            self.fail("The local loot ledger has not been implemented")
        self.ledger = self.module.LootLedger(self.path)

    def priced_session(self, wallet=1000, counts=None, timestamp=100):
        for item_id, item in self.ledger.catalog.items():
            price = "0.25" if item_id == "subi_stars" else "2"
            self.ledger.set_item(item_id, item["name"], item["kind"], price)
        opening = dict.fromkeys(self.ledger.catalog, 0)
        opening.update(counts or {})
        return self.ledger.start_session("Outing", wallet, opening, timestamp=timestamp)

    def test_default_catalog_does_not_invent_prices(self):
        self.assertEqual(len(self.ledger.catalog), 7)
        self.assertTrue(all(item["unit_value"] is None for item in self.ledger.catalog.values()))
        self.assertEqual(self.ledger.catalog["subi_stars"]["kind"], "supply")
        self.assertIsNone(self.ledger.active_session)
        self.assertIsNone(self.ledger.summary()["profit"])

    def test_session_freezes_catalog_prices_and_preserves_older_sessions(self):
        first_id = self.priced_session()
        self.ledger.set_item("red_shell", "Renamed shell", "loot", "100")
        self.assertEqual(self.ledger.active_session["catalog"]["red_shell"]["unit_value"], Decimal("2"))
        self.assertEqual(self.ledger.active_session["catalog"]["red_shell"]["name"], "Red Snail Shell")
        second_id = self.ledger.start_session("Next outing", 0, {}, timestamp=200)
        self.assertNotEqual(first_id, second_id)
        self.assertEqual(len(self.ledger.sessions), 2)
        self.assertEqual(self.ledger.active_session["catalog"]["red_shell"]["unit_value"], Decimal("100"))
        self.assertEqual(self.ledger.summary(first_id)["session_id"], first_id)

    def test_missing_opening_counts_remain_unknown(self):
        self.ledger.start_session("Outing", 0, {"red_shell": 0}, timestamp=100)
        self.assertIsNone(self.ledger.active_session["opening"]["counts"]["blue_shell"])
        self.assertEqual(self.ledger.active_session["opening"]["counts"]["red_shell"], 0)

    def test_public_copies_cannot_rewrite_history(self):
        self.priced_session()
        self.ledger.catalog["red_shell"]["unit_value"] = Decimal("999")
        self.ledger.active_session["opening"]["counts"]["red_shell"] = 100
        self.ledger.sessions.clear()
        self.assertEqual(self.ledger.catalog["red_shell"]["unit_value"], Decimal("2"))
        self.assertEqual(self.ledger.active_session["opening"]["counts"]["red_shell"], 0)
        self.assertEqual(len(self.ledger.sessions), 1)

    def test_rejects_unsafe_prices(self):
        for value in [True, 1.5, "-1", "NaN", "Infinity", "1e999999", object()]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.ledger.set_item("test", "Test item", "loot", value)
        self.assertNotIn("test", self.ledger.catalog)

    def test_rejects_invalid_session_counts_wallet_and_unknown_items(self):
        for wallet, counts in [(True, {}), (1.0, {}), (-1, {}), (0, {"red_shell": True}),
                               (0, {"red_shell": 1.0}), (0, {"red_shell": -1}), (0, {"typo": 1})]:
            with self.subTest(wallet=wallet, counts=counts), self.assertRaises(ValueError):
                self.ledger.start_session("Outing", wallet, counts, timestamp=100)
        self.assertIsNone(self.ledger.active_session)

    def test_validates_timestamp_and_accepts_aware_datetime(self):
        for timestamp in [True, float("nan"), float("inf"), "today", datetime(2026, 1, 1)]:
            with self.subTest(timestamp=timestamp), self.assertRaises(ValueError):
                self.ledger.start_session("Outing", 0, {}, timestamp=timestamp)
        self.ledger.start_session("Outing", 0, {}, timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc))
        self.assertEqual(self.ledger.active_session["opening"]["timestamp"], 1767225600.0)

    def test_restart_preserves_session_and_decimal_prices(self):
        session_id = self.priced_session(counts={"subi_stars": 1001})
        restored = self.module.LootLedger(self.path)
        self.assertEqual(restored.active_session["id"], session_id)
        self.assertEqual(restored.active_session["catalog"]["subi_stars"]["unit_value"], Decimal("0.25"))
        self.assertEqual(restored.active_session["opening"]["counts"]["subi_stars"], 1001)

    def test_failed_replace_preserves_memory_and_existing_file(self):
        self.priced_session()
        original = self.path.read_bytes()
        with patch("os.replace", side_effect=OSError("Disk unavailable")):
            with self.assertRaises(OSError):
                self.ledger.set_item("red_shell", "Changed", "loot", "99")
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(self.ledger.catalog["red_shell"]["unit_value"], Decimal("2"))
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def closing_counts(self, **changes):
        counts = dict.fromkeys(self.ledger.active_session["catalog"], 0)
        counts.update(changes)
        return counts

    def test_snapshot_does_not_double_count_sales_purchases_or_recharges(self):
        self.priced_session(counts={"red_shell": 10, "subi_stars": 1000, "hp_potion": 10})
        self.ledger.record_event("drop", "pickup", "red_shell", 20, 0, timestamp=110)
        self.ledger.record_event("sale", "sale", "red_shell", 15, 30, timestamp=120)
        self.ledger.record_event("buy", "purchase", "hp_potion", 10, 20, timestamp=130)
        self.ledger.record_event("stars", "recharge", "subi_stars", 400, 100, timestamp=140)
        self.ledger.snapshot("close", 910, self.closing_counts(red_shell=15, subi_stars=900, hp_potion=15), timestamp=3700)
        result = self.ledger.summary()
        self.assertEqual(result["wallet_delta"], -90)
        self.assertEqual(result["inventory_delta_value"], Decimal("-5"))
        self.assertEqual(result["profit"], Decimal("-95"))
        self.assertEqual(result["confirmed_pickups"], {"red_shell": 20})
        self.assertEqual(result["sales_mesos"], 30)
        self.assertEqual(result["purchase_mesos"], 20)
        self.assertEqual(result["recharge_mesos"], 100)
        self.assertEqual(result["cashflow_mesos"], -90)
        self.assertEqual(result["elapsed_seconds"], 3600)
        self.assertEqual(result["profit_per_hour"], Decimal("-95"))

    def test_fractional_star_unit_cost_is_exact(self):
        self.priced_session(counts={"subi_stars": 2000})
        self.ledger.snapshot("close", 1000, self.closing_counts(subi_stars=1999), timestamp=101)
        self.assertEqual(self.ledger.summary()["profit"], Decimal("-0.25"))

    def test_unknown_unchanged_price_cancels_but_changed_price_blocks(self):
        self.ledger.start_session("Outing", 1000, dict.fromkeys(self.ledger.catalog, 0), timestamp=100)
        self.ledger.snapshot("unchanged", 1100, self.closing_counts(), timestamp=200)
        self.assertEqual(self.ledger.summary()["profit"], Decimal("100"))
        self.ledger.snapshot("changed", 1100, self.closing_counts(red_shell=1), timestamp=300)
        result = self.ledger.summary()
        self.assertIsNone(result["profit"])
        self.assertTrue(any("red_shell" in reason and "price" in reason.lower() for reason in result["missing"]))

    def test_unknown_supply_cost_blocks_consumption_and_cannot_be_retroactively_changed(self):
        opening = dict.fromkeys(self.ledger.catalog, 0)
        opening["hp_potion"] = 2
        self.ledger.start_session("Outing", 100, opening, timestamp=100)
        self.ledger.snapshot("close", 100, self.closing_counts(hp_potion=1), timestamp=200)
        self.ledger.set_item("hp_potion", "HP Potion", "supply", "50")
        self.assertIsNone(self.ledger.summary()["profit"])

    def test_unknown_opening_closing_counts_and_wallet_block_profit(self):
        self.ledger.start_session("Outing", 1000, {"red_shell": 1}, timestamp=100)
        self.ledger.snapshot("close", None, {"blue_shell": 0}, timestamp=200)
        result = self.ledger.summary()
        self.assertIsNone(result["profit"])
        self.assertIsNone(result["wallet_delta"])
        self.assertIsNone(result["inventory_delta_value"])
        self.assertTrue(any("Opening count" in reason for reason in result["missing"]))
        self.assertTrue(any("Closing count" in reason for reason in result["missing"]))
        self.assertTrue(any("wallet" in reason for reason in result["missing"]))

    def test_external_transfers_are_signed_and_limited_to_snapshot_time(self):
        self.priced_session()
        self.ledger.record_event("in", "transfer", None, 0, 200, timestamp=110)
        self.ledger.snapshot("first", 1200, self.closing_counts(), timestamp=120)
        self.ledger.record_event("out", "transfer", None, 0, -50, timestamp=130)
        stale = self.ledger.summary()
        self.assertEqual(stale["profit"], Decimal("0"))
        self.assertEqual(stale["external_transfer_net"], 200)
        self.assertTrue(stale["stale"])
        self.assertEqual(stale["as_of"], 120)
        self.assertEqual(stale["elapsed_seconds"], 20)
        self.ledger.snapshot("second", 1150, self.closing_counts(), timestamp=140)
        result = self.ledger.summary()
        self.assertEqual(result["profit"], Decimal("0"))
        self.assertEqual(result["external_transfer_net"], 150)
        self.assertFalse(result["stale"])

    def test_duplicate_event_and_snapshot_ids_are_idempotent_including_after_restart(self):
        self.priced_session()
        self.assertTrue(self.ledger.record_event("a", "pickup", "red_shell", 1, 10, note="manual", timestamp=110))
        self.assertFalse(self.ledger.record_event("a", "pickup", "red_shell", 1, 10, note="manual"))
        self.assertTrue(self.ledger.snapshot("b", 1010, self.closing_counts(red_shell=1), timestamp=120))
        self.assertFalse(self.ledger.snapshot("b", 1010, self.closing_counts(red_shell=1)))
        self.ledger = self.module.LootLedger(self.path)
        self.assertFalse(self.ledger.record_event("a", "pickup", "red_shell", 1, 10, note="manual", timestamp=110))
        self.assertEqual(self.ledger.summary()["confirmed_pickups"], {"red_shell": 1})
        self.assertEqual(self.ledger.summary()["pickup_mesos"], 10)
        self.assertFalse(self.ledger.summary()["stale"])

    def test_conflicting_duplicate_ids_preserve_original_evidence(self):
        self.priced_session()
        self.ledger.record_event("a", "pickup", "red_shell", 1, 0, note="first", timestamp=110)
        before = self.path.read_bytes()
        for changes in [{"quantity": 2}, {"note": "rewritten"}, {"timestamp": 111}]:
            arguments = dict(event_id="a", kind="pickup", item_id="red_shell", quantity=1, mesos=0, note="first", timestamp=110)
            arguments.update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.ledger.record_event(**arguments)
        with self.assertRaises(ValueError):
            self.ledger.snapshot("a", 1000, self.closing_counts(), timestamp=110)
        self.assertEqual(self.path.read_bytes(), before)

    def test_events_after_snapshot_make_it_stale_even_at_same_timestamp(self):
        self.priced_session()
        self.ledger.snapshot("close", 1000, self.closing_counts(), timestamp=100)
        self.assertIsNone(self.ledger.summary()["profit_per_hour"])
        self.ledger.record_event("late", "pickup", None, 0, 5, timestamp=100)
        result = self.ledger.summary()
        self.assertTrue(result["stale"])
        self.assertEqual(result["profit"], Decimal("0"))
        self.assertEqual(result["as_of"], 100)
        self.assertEqual(result["cashflow_mesos"], 5)

    def test_backdated_events_snapshots_and_sessions_are_rejected(self):
        self.priced_session()
        self.ledger.record_event("later", "pickup", None, 0, 5, timestamp=200)
        with self.assertRaises(ValueError):
            self.ledger.record_event("old", "pickup", None, 0, 5, timestamp=199)
        with self.assertRaises(ValueError):
            self.ledger.snapshot("old", 1000, self.closing_counts(), timestamp=199)
        with self.assertRaises(ValueError):
            self.ledger.start_session("Old", 0, {}, timestamp=199)
        self.assertEqual(len(self.ledger.active_session["records"]), 1)

    def test_invalid_event_values_leave_journal_unchanged(self):
        self.priced_session()
        bad_values = [{"quantity": True}, {"quantity": 1.0}, {"quantity": -1},
                      {"mesos": True}, {"mesos": 1.5}, {"mesos": -1}, {"mesos": float("nan")},
                      {"kind": "automatic"}, {"item_id": "unknown"}, {"item_id": None},
                      {"timestamp": float("nan")}]
        for changes in bad_values:
            arguments = dict(event_id="bad", kind="pickup", item_id="red_shell", quantity=1, mesos=0, timestamp=110)
            arguments.update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.ledger.record_event(**arguments)
        self.assertEqual(self.ledger.active_session["records"], [])

    def test_adjustment_preserves_prior_record_and_updates_journal_only(self):
        self.priced_session()
        self.ledger.record_event("picked", "pickup", "red_shell", 2, 20, timestamp=110)
        self.ledger.record_event("correction", "adjustment", "red_shell", -1, -10, note="Correction of picked", timestamp=120)
        self.ledger.snapshot("close", 1010, self.closing_counts(red_shell=1), timestamp=130)
        result = self.ledger.summary()
        self.assertEqual(result["confirmed_pickups"], {"red_shell": 2})
        self.assertEqual(result["cashflow_mesos"], 10)
        self.assertEqual(result["profit"], Decimal("12"))
        self.assertEqual(self.ledger.active_session["records"][0]["quantity"], 2)

    def test_new_catalog_items_wait_until_next_session(self):
        self.priced_session()
        self.ledger.set_item("custom", "Custom item", "loot", "1")
        with self.assertRaises(ValueError):
            self.ledger.record_event("new", "pickup", "custom", 1, 0, timestamp=110)
        with self.assertRaises(ValueError):
            self.ledger.snapshot("new", 1000, {"custom": 1}, timestamp=110)

    def test_save_failure_does_not_leave_phantom_events_or_snapshots(self):
        self.priced_session()
        before = self.path.read_bytes()
        with patch("os.replace", side_effect=OSError("Disk full")):
            with self.assertRaises(OSError):
                self.ledger.record_event("lost", "pickup", None, 0, 5, timestamp=110)
            with self.assertRaises(OSError):
                self.ledger.snapshot("lost", 1005, self.closing_counts(), timestamp=110)
        self.assertEqual(self.ledger.active_session["records"], [])
        self.assertEqual(self.path.read_bytes(), before)

    def test_invalid_saved_state_is_rejected_without_rewriting_file(self):
        self.priced_session()
        self.ledger.record_event("first", "pickup", "red_shell", 1, 1, timestamp=110)
        self.ledger.snapshot("close", 1001, self.closing_counts(red_shell=1), timestamp=120)
        original = self.path.read_text(encoding="utf-8")

        def corrupt(change):
            data = json.loads(original)
            change(data)
            text = json.dumps(data)
            self.path.write_text(text, encoding="utf-8")
            with self.assertRaises(ValueError):
                self.module.LootLedger(self.path)
            self.assertEqual(self.path.read_text(encoding="utf-8"), text)

        changes = [
            lambda data: data.update(version=True),
            lambda data: data.update(version=999),
            lambda data: data.update(sessions={}),
            lambda data: data.update(active_session_id="missing"),
            lambda data: data.update(unrecognized="cannot silently discard"),
            lambda data: data["catalog"]["red_shell"].update(unit_value="NaN"),
            lambda data: data["catalog"]["red_shell"].update(unit_value=0.5),
            lambda data: data["catalog"]["red_shell"].update(kind="unknown"),
            lambda data: data["sessions"][0]["opening"].update(wallet=True),
            lambda data: data["sessions"][0]["opening"]["counts"].pop("red_shell"),
            lambda data: data["sessions"][0]["opening"]["counts"].update(red_shell=-1),
            lambda data: data["sessions"][0].update(started_at=99),
            lambda data: data["sessions"][0]["records"][0].update(quantity=True),
            lambda data: data["sessions"][0]["records"][0].update(mesos=1.5),
            lambda data: data["sessions"][0]["records"][0].update(item_id="missing"),
            lambda data: data["sessions"][0]["records"][0].update(kind="unknown"),
            lambda data: data["sessions"][0]["records"][1].update(id="first"),
            lambda data: data["sessions"][0]["records"][1].update(timestamp=99),
            lambda data: data["sessions"][0]["records"][1].update(timestamp=None),
            lambda data: data["sessions"].append(data["sessions"][0]),
        ]
        for index, change in enumerate(changes):
            with self.subTest(corruption=index):
                corrupt(change)

    def test_malformed_json_and_duplicate_keys_never_reset_history(self):
        for text in ['{"version": 1', '{"version":1,"version":2}', '{"version":NaN}', '[]']:
            with self.subTest(text=text):
                self.path.write_text(text, encoding="utf-8")
                with self.assertRaises(ValueError):
                    self.module.LootLedger(self.path)
                self.assertEqual(self.path.read_text(encoding="utf-8"), text)

    def test_event_requires_active_session_and_invalid_snapshots_cannot_erase_latest(self):
        with self.assertRaises(ValueError):
            self.ledger.record_event("none", "pickup", None, 0, 1)
        with self.assertRaises(ValueError):
            self.ledger.snapshot("none", 0, {})
        self.priced_session()
        self.ledger.snapshot("good", 1000, self.closing_counts(), timestamp=200)
        before = self.path.read_bytes()
        for wallet, counts in [(True, {}), (-1, {}), (1.0, {}), (1000, {"red_shell": True}),
                               (1000, {"red_shell": 1.5}), (1000, {"red_shell": -1})]:
            with self.subTest(wallet=wallet, counts=counts), self.assertRaises(ValueError):
                self.ledger.snapshot("bad", wallet, counts, timestamp=300)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.ledger.summary()["as_of"], 200)

    def test_save_creates_parent_directory_and_preserves_exact_prices(self):
        nested = self.path.parent / "nested" / "ledger.json"
        ledger = self.module.LootLedger(nested)
        ledger.set_item("star", "Exact star", "supply", Decimal("0.000000000000000001"))
        ledger.save()
        self.assertEqual(self.module.LootLedger(nested).catalog["star"]["unit_value"], Decimal("0.000000000000000001"))

    def test_custom_item_ids_matching_schema_words_are_supported(self):
        self.ledger.set_item("unit_value", "Custom item", "loot", "2.5")
        self.ledger.start_session("Custom outing", 0, dict.fromkeys(self.ledger.catalog, 0), timestamp=100)
        self.ledger.snapshot("close", 0, self.closing_counts(unit_value=2), timestamp=200)
        self.assertEqual(self.ledger.active_session["opening"]["counts"]["unit_value"], 0)
        self.assertIs(type(self.ledger.active_session["opening"]["counts"]["unit_value"]), int)
        self.assertEqual(self.ledger.summary()["profit"], Decimal("5"))

    def test_unknown_price_pickups_do_not_imply_known_estimated_loot_value(self):
        self.ledger.start_session("Outing", 100, dict.fromkeys(self.ledger.catalog, 0), timestamp=100)
        self.ledger.record_event("drop", "pickup", "red_shell", 1, 0, timestamp=110)
        self.assertIsNone(self.ledger.summary()["confirmed_pickup_value"])
        self.assertIsNone(self.ledger.summary()["profit"])

    def test_new_writes_reject_explicit_future_timestamps_without_mutation(self):
        self.priced_session()
        original = self.path.read_bytes()
        original_session = self.ledger.active_session
        actions = [
            lambda: self.ledger.record_event("future-event", "pickup", None, 0, 1, timestamp=1060.001),
            lambda: self.ledger.snapshot("future-snapshot", 1000, self.closing_counts(), timestamp=1060.001),
            lambda: self.ledger.start_session("Future", 0, {}, timestamp=1060.001),
        ]
        with patch("loot_ledger.time.time", return_value=1000):
            for index, action in enumerate(actions):
                with self.subTest(write=index), self.assertRaises(ValueError):
                    action()
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(self.ledger.active_session, original_session)

    def test_explicit_future_allowance_has_a_sixty_second_boundary(self):
        self.priced_session()
        with patch("loot_ledger.time.time", return_value=1000):
            self.assertTrue(self.ledger.record_event("boundary", "pickup", None, 0, 1, timestamp=1060))
            with self.assertRaises(ValueError):
                self.ledger.record_event("beyond", "pickup", None, 0, 1,
                                         timestamp=datetime.fromtimestamp(1060.001, tz=timezone.utc))
        self.assertEqual(len(self.ledger.active_session["records"]), 1)

    def test_default_timestamps_follow_clock_forward_and_clamp_clock_regression(self):
        with patch("loot_ledger.time.time", return_value=1000):
            self.priced_session(timestamp=None)
        self.assertEqual(self.ledger.active_session["started_at"], 1000)
        with patch("loot_ledger.time.time", return_value=1010):
            self.ledger.record_event("first", "pickup", None, 0, 1)
        with patch("loot_ledger.time.time", return_value=990):
            self.ledger.record_event("after-clock-change", "pickup", None, 0, 1)
            self.assertFalse(self.ledger.record_event("first", "pickup", None, 0, 1))
            self.ledger.snapshot("close", 1002, self.closing_counts())
            self.assertFalse(self.ledger.snapshot("close", 1002, self.closing_counts()))
            self.assertEqual([record["timestamp"] for record in self.ledger.active_session["records"]],
                             [1010, 1010, 1010])
            self.ledger.start_session("Next", 1002, self.closing_counts())
        self.assertEqual(self.ledger.active_session["started_at"], 1010)
        self.assertEqual(len(self.module.LootLedger(self.path).sessions), 2)

    def test_explicit_historical_times_remain_allowed_but_must_be_ordered(self):
        with patch("loot_ledger.time.time", return_value=1000):
            self.priced_session(timestamp=900)
            self.ledger.record_event("past", "pickup", None, 0, 1, timestamp=910)
            self.ledger.snapshot("past-close", 1001, self.closing_counts(), timestamp=920)
            original = self.path.read_bytes()
            with self.assertRaises(ValueError):
                self.ledger.record_event("older", "pickup", None, 0, 1, timestamp=919)
            with self.assertRaises(ValueError):
                self.ledger.start_session("Older", 1001, self.closing_counts(), timestamp=919)
            self.assertEqual(self.path.read_bytes(), original)

    def test_future_timestamps_in_existing_files_still_load_and_allow_default_recovery(self):
        self.priced_session()
        self.ledger.record_event("saved", "pickup", None, 0, 1, timestamp=110)
        data = json.loads(self.path.read_text(encoding="utf-8"))
        data["sessions"][0]["records"][0]["timestamp"] = 17600000000
        self.path.write_text(json.dumps(data), encoding="utf-8")
        original = self.path.read_bytes()
        with patch("loot_ledger.time.time", return_value=1000):
            self.ledger = self.module.LootLedger(self.path)
            self.assertEqual(self.path.read_bytes(), original)
            self.assertFalse(self.ledger.record_event("saved", "pickup", None, 0, 1))
            self.assertFalse(self.ledger.record_event("saved", "pickup", None, 0, 1, timestamp=17600000000))
            self.ledger.record_event("recovery", "pickup", None, 0, 1)
        self.assertEqual(self.ledger.active_session["records"][-1]["timestamp"], 17600000000)

    def test_counter_transfer_corrects_external_net_without_rewriting_original(self):
        self.priced_session()
        self.ledger.record_event("transfer", "transfer", None, 0, 200, timestamp=110)
        self.ledger.record_event("counter-transfer", "transfer", None, 0, -50,
                                 note="Correct transfer amount to 150", timestamp=111)
        self.ledger.snapshot("close", 1150, self.closing_counts(), timestamp=120)
        result = self.ledger.summary()
        self.assertEqual(result["external_transfer_net"], 150)
        self.assertEqual(result["profit"], Decimal("0"))
        self.assertEqual(self.ledger.active_session["records"][0]["mesos"], 200)


if __name__ == "__main__":
    unittest.main()
