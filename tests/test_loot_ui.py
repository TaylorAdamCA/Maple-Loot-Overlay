"""Workflow and withdrawn Tk smoke tests. All images/readers are synthetic."""
import importlib
from dataclasses import replace
import io
import json
import sys
import tempfile
import threading
import time
import tkinter as tk
import unittest
from unittest.mock import patch
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loot_ledger import LootLedger
from loot_reader import parse_item_text


class WorkflowFixture:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "ledger.json"
        try:
            self.ui = importlib.import_module("loot_ui")
        except ModuleNotFoundError:
            self.fail("The confirmed loot UI workflow has not been implemented")
        self.controller = self.ui.LootController(self.path)

    def start(self):
        for item_id, item in self.controller.ledger.catalog.items():
            self.controller.set_item(item_id, item["name"], item["kind"], "2")
        self.controller.start("Outing", "100", dict.fromkeys(self.controller.ledger.catalog, "0"), confirmed=True)


class WorkflowTests(WorkflowFixture, unittest.TestCase):
    def test_opening_requires_confirmation_and_keeps_unknown_distinct_from_zero(self):
        with self.assertRaises(ValueError):
            self.controller.start("Outing", "100", {"red_shell": "0"}, confirmed=False)
        self.assertFalse(self.path.exists())
        self.controller.start("Outing", "100", {"red_shell": "0", "blue_shell": "?"}, confirmed=True)
        opening = LootLedger(self.path).active_session["opening"]
        self.assertEqual(opening["wallet"], 100)
        self.assertEqual(opening["counts"]["red_shell"], 0)
        self.assertIsNone(opening["counts"]["blue_shell"])
        self.assertIsNone(opening["counts"]["subi_stars"])

    def test_inventory_proposals_fill_only_visible_items_and_never_journal(self):
        self.start()
        result = parse_item_text("Red Snail Shell x 3", ["Red Snail Shell"], mode="inventory")
        evidence = self.controller.prepare_read(result)
        counts = self.controller.snapshot_defaults(evidence)
        self.assertEqual(counts["red_shell"], "3")
        self.assertEqual(counts["subi_stars"], "?")
        with self.assertRaises(ValueError):
            self.controller.confirm_pickups(evidence, {"red_shell": "3"}, confirmed=True)
        self.controller.snapshot("110", counts, confirmed=True, evidence=evidence)
        self.assertIsNone(self.controller.ledger.summary()["profit"])
        self.assertEqual(self.controller.ledger.summary()["confirmed_pickups"], {})
        self.assertTrue(any("subi_stars" in reason for reason in self.controller.ledger.summary()["missing"]))

    def test_duplicate_evidence_and_confirmed_quantity_survive_reload(self):
        self.start()
        result = parse_item_text("Obtained 3 Red Snail Shell", ["Red Snail Shell"])
        evidence = self.controller.prepare_read(result)
        with self.assertRaises(ValueError):
            self.controller.confirm_pickups(evidence, {"red_shell": "3"}, confirmed=False)
        self.assertEqual(self.controller.ledger.summary()["confirmed_pickups"], {})
        self.assertEqual(self.controller.confirm_pickups(evidence, {"red_shell": "3"}, confirmed=True), 1)
        reloaded = self.ui.LootController(self.path)
        same = reloaded.prepare_read(result)
        self.assertEqual(reloaded.confirm_pickups(same, {"red_shell": "3"}, confirmed=True), 0)
        self.assertEqual(reloaded.ledger.summary()["confirmed_pickups"], {"red_shell": 3})
        evidence_files = list(reloaded.evidence_dir.glob("*.json"))
        self.assertEqual(len(evidence_files), 1)
        stored = json.loads(evidence_files[0].read_text(encoding="utf-8"))
        self.assertEqual(stored["raw_text"], "Obtained 3 Red Snail Shell")
        self.assertEqual(stored["items"][0]["quantity"], 3)

    def test_old_review_cannot_write_into_new_session(self):
        self.start()
        evidence = self.controller.prepare_read(parse_item_text("Red Snail Shell x 2", ["Red Snail Shell"]))
        self.controller.start("Next", "100", {}, confirmed=True)
        with self.assertRaises(ValueError):
            self.controller.confirm_pickups(evidence, {"red_shell": "2"}, confirmed=True)
        self.assertEqual(self.controller.ledger.summary()["confirmed_pickups"], {})

    def test_journal_and_absolute_snapshot_do_not_count_sales_twice(self):
        self.start()
        self.controller.record("pickup", "red_shell", "4", "10", "Confirmed drop")
        self.controller.record("sale", "red_shell", "2", "4", "NPC sale")
        self.controller.record("purchase", "hp_potion", "1", "2", "Actual cost")
        self.controller.record("recharge", "subi_stars", "1", "2", "Actual recharge")
        self.controller.record("transfer", None, "0", "20", "External deposit")
        self.controller.record("adjustment", "red_shell", "-1", "-1", "Correct journal only")
        counts = dict.fromkeys(self.controller.ledger.catalog, "0")
        counts.update(red_shell="2", hp_potion="1", subi_stars="1")
        self.controller.snapshot("129", counts, confirmed=True)
        summary = self.controller.ledger.summary()
        self.assertEqual(summary["profit"], Decimal("17"))
        self.assertEqual(summary["confirmed_pickups"], {"red_shell": 4})
        self.assertEqual(summary["sales_mesos"], 4)
        self.assertEqual(summary["purchase_mesos"], 2)
        self.assertEqual(summary["recharge_mesos"], 2)
        self.assertEqual(summary["cashflow_mesos"], 29)

    def test_future_catalog_edits_do_not_reprice_existing_session(self):
        self.start()
        self.controller.set_item("red_shell", "Red Snail Shell", "loot", "50")
        self.controller.set_item("custom", "Custom drop", "loot", "?")
        session = self.controller.ledger.active_session
        self.assertEqual(session["catalog"]["red_shell"]["unit_value"], Decimal("2"))
        self.assertNotIn("custom", session["catalog"])
        self.assertIsNone(self.controller.ledger.catalog["custom"]["unit_value"])

    def test_all_review_quantities_validate_before_first_record(self):
        self.start()
        evidence = self.controller.prepare_read(parse_item_text(
            "Red Snail Shell x 2\nBlue Snail Shell x 3", ["Red Snail Shell", "Blue Snail Shell"]))
        with self.assertRaises(ValueError):
            self.controller.confirm_pickups(evidence, {"red_shell": "2", "blue_shell": "bad"}, confirmed=True)
        self.assertEqual(self.controller.ledger.summary()["confirmed_pickups"], {})

    def test_small_unit_prices_are_not_displayed_as_zero(self):
        self.assertEqual(self.ui.shown(Decimal("0.0001")), "0.0001")
        self.assertEqual(self.ui.shown(Decimal("1000")), "1,000")

    def test_numeric_entry_rejects_unreadable_and_negative_counts(self):
        self.start()
        for value in ("O", "1.5", "-1", "1,00", "9999999999999999999"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.controller.snapshot("100", {"red_shell": value}, confirmed=True)
        self.assertEqual(self.controller.ledger.active_session["records"], [])

    def test_conflicting_duplicate_is_checked_before_any_new_pickup(self):
        self.start()
        evidence = self.controller.prepare_read(parse_item_text(
            "Red Snail Shell x 2\nBlue Snail Shell x 3", ["Red Snail Shell", "Blue Snail Shell"]))
        self.controller.confirm_pickups(evidence, {"red_shell": "2"}, confirmed=True)
        with self.assertRaises(ValueError):
            self.controller.confirm_pickups(evidence, {"blue_shell": "3", "red_shell": "4"}, confirmed=True)
        self.assertEqual(self.controller.ledger.summary()["confirmed_pickups"], {"red_shell": 2})

    def test_same_image_keeps_evidence_and_does_not_repeat_changed_ocr_text(self):
        self.start()
        from PIL import Image
        encoded = io.BytesIO()
        Image.new("RGB", (4, 3), "red").save(encoded, format="PNG")
        result = replace(parse_item_text("Red Snail Shell x 2", ["Red Snail Shell"]), source_png=encoded.getvalue())
        first = self.controller.prepare_read(result)
        self.controller.confirm_pickups(first, {"red_shell": "2"}, confirmed=True)
        second = self.controller.prepare_read(replace(result, raw_text="Red Snail Shell x 2\n"))
        self.assertEqual(self.controller.confirm_pickups(second, {"red_shell": "2"}, confirmed=True), 0)
        self.assertEqual(self.controller.ledger.summary()["confirmed_pickups"], {"red_shell": 2})
        self.assertEqual(list(self.controller.evidence_dir.glob("*.png"))[0].read_bytes(), encoded.getvalue())
        self.assertEqual(len(list(self.controller.evidence_dir.glob("*.json"))), 2)

    def test_repeated_inventory_source_can_record_a_new_absolute_checkpoint(self):
        self.controller.start("Outing", "100", {}, confirmed=True, timestamp=100)
        evidence = self.controller.prepare_read(parse_item_text("Red Snail Shell x 2", ["Red Snail Shell"], mode="inventory"))
        self.controller.snapshot("110", {"red_shell": "2"}, confirmed=True, evidence=evidence, timestamp=200)
        try:
            self.controller.snapshot("120", {"red_shell": "2"}, confirmed=True, evidence=evidence, timestamp=300)
        except ValueError as error:
            self.fail(f"Repeated absolute readings must allow a later checkpoint: {error}")
        records = self.controller.ledger.active_session["records"]
        self.assertEqual([row["wallet"] for row in records], [110, 120])
        self.assertNotEqual(records[0]["id"], records[1]["id"])
        self.assertTrue(all(evidence.identity in row["id"] for row in records))

    def test_unrenderable_timestamp_has_a_readable_fallback(self):
        try:
            rendered = self.ui.local_time(10 ** 30)
        except (ValueError, OverflowError, OSError):
            self.fail("Timestamp rendering must preserve an invalid value without crashing")
        self.assertIn(str(10 ** 30), rendered)


class WindowTests(WorkflowFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.window = self.ui.ProfitWindow(self.root, self.path, wallet=lambda: 123)
        self.window.window.withdraw()

    def test_opening_dialog_needs_same_time_confirmation(self):
        dialog = self.window.new_session()
        self.assertEqual(dialog.wallet_var.get(), "123")
        self.assertFalse(dialog.confirm_var.get())
        self.assertTrue(all(variable.get() == "?" for variable in dialog.count_vars.values()))
        dialog.name_var.set("Smoke outing")
        dialog.confirm_var.set(True)
        dialog.submit()
        self.assertEqual(LootLedger(self.path).active_session["opening"]["wallet"], 123)
        self.assertIn("unknown", self.window.profit_var.get().lower())

    def test_full_snapshot_ui_shows_profit_then_stale_after_manual_event(self):
        self.window.controller = self.controller
        self.start()
        self.window.refresh()
        dialog = self.window.new_snapshot()
        self.assertTrue(all(variable.get() == "?" for variable in dialog.count_vars.values()))
        dialog.wallet_var.set("110")
        for variable in dialog.count_vars.values():
            variable.set("0")
        dialog.confirm_var.set(True)
        dialog.submit()
        self.assertIn("10", self.window.profit_var.get())
        self.controller.record("pickup", "red_shell", "1", "0", "After snapshot")
        self.window.refresh()
        self.assertIn("stale", self.window.coverage_var.get().lower())

    def test_ocr_worker_does_not_block_tk_and_returns_review_only(self):
        self.window.controller = self.controller
        self.start()
        self.window.refresh()
        gate = threading.Event()
        entered = threading.Event()
        main_thread = threading.get_ident()
        worker_threads = []

        def reader(image, items, *, mode):
            worker_threads.append(threading.get_ident())
            entered.set()
            gate.wait(2)
            return parse_item_text("Red Snail Shell x 3", items, mode=mode)

        self.window.image_reader = reader
        dialog = self.window.read_items()
        dialog.launch_image(lambda: object())
        self.assertTrue(entered.wait(1))
        heartbeat = []
        self.root.after(0, lambda: heartbeat.append(True))
        self.root.update()
        self.assertTrue(heartbeat)
        self.assertNotEqual(worker_threads, [main_thread])
        gate.set()
        deadline = time.monotonic() + 3
        while dialog.evidence is None and time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.01)
        self.assertIsNotNone(dialog.evidence)
        self.assertEqual(self.controller.ledger.summary()["confirmed_pickups"], {})
        self.assertFalse(dialog.confirm_var.get())

    def test_cancelled_region_selection_restores_owned_windows(self):
        self.window.controller = self.controller
        self.start()
        self.window.refresh()
        self.window.window.deiconify()
        self.window.select_region = lambda root, callback: callback(None)
        dialog = self.window.read_items()
        dialog.select_area()
        self.root.update()
        self.assertNotEqual(self.window.window.state(), "withdrawn")
        self.assertNotEqual(dialog.window.state(), "withdrawn")
        self.assertEqual(self.root.state(), "withdrawn")
        self.assertIsNone(dialog.evidence)

    def test_catalog_changed_after_opening_form_requires_review_again(self):
        dialog = self.window.new_session()
        self.window.controller.set_item("red_shell", "Red Snail Shell", "loot", "5")
        dialog.confirm_var.set(True)
        dialog.submit()
        self.assertIsNone(self.window.controller.ledger.active_session)
        self.assertTrue(dialog.status_var.get())

    def test_parent_close_cancels_reader_poll_and_delayed_capture(self):
        self.window.controller = self.controller
        self.start()
        self.window.refresh()
        dialog = self.window.read_items()
        captures = []
        self.window.capture = lambda region: captures.append(region)
        dialog.area_selected({"left": 0, "top": 0, "width": 20, "height": 20})
        self.window.window.destroy()
        callbacks = self.root.tk.call("after", "info")
        self.assertEqual(tuple(callbacks), ())
        self.assertEqual(captures, [])

    def test_failed_image_read_restores_and_keeps_manual_paste_available(self):
        self.window.controller = self.controller
        self.start()
        self.window.refresh()

        def broken_source():
            raise OSError("No image decoder")

        dialog = self.window.read_items()
        dialog.hidden = True
        dialog.window.withdraw()
        dialog.launch_image(broken_source, restore=True)
        deadline = time.monotonic() + 3
        while dialog.busy and time.monotonic() < deadline:
            self.root.update()
            time.sleep(0.01)
        self.assertFalse(dialog.busy)
        self.assertNotEqual(dialog.window.state(), "withdrawn")
        self.assertIn("manual", dialog.status_var.get().lower())
        dialog.text.insert("1.0", "Red Snail Shell x 2")
        dialog.read_text()
        self.assertIsNotNone(dialog.evidence)
        self.assertEqual(self.controller.ledger.summary()["confirmed_pickups"], {})

    def test_inventory_review_opens_unknown_full_snapshot_and_needs_new_confirmation(self):
        self.window.controller = self.controller
        self.start()
        self.window.refresh()
        dialog = self.window.read_items()
        dialog.mode_var.set("inventory")
        dialog.text.insert("1.0", "Red Snail Shell x 2")
        dialog.read_text()
        dialog.count_vars["red_shell"].set("3")
        dialog.confirm_var.set(True)
        dialog.submit()
        snapshot = self.window.dialogs[-1]
        self.assertEqual(snapshot.count_vars["red_shell"].get(), "3")
        self.assertEqual(snapshot.count_vars["subi_stars"].get(), "?")
        self.assertFalse(snapshot.confirm_var.get())
        self.assertEqual(self.controller.ledger.active_session["records"], [])

    def test_manual_journal_button_and_catalog_edit_persist(self):
        self.window.catalog_name.set("Another item")
        self.window.catalog_price.set("1.25")
        self.window.save_catalog()
        self.assertTrue(any(item["name"] == "Another item" for item in LootLedger(self.path).catalog.values()))
        self.window.controller.start("UI outing", "100", {}, confirmed=True)
        self.window.refresh()
        event = self.window.record_event()
        event.quantity_var.set("5")
        event.mesos_var.set("7")
        event.submit()
        summary = LootLedger(self.path).summary()
        self.assertEqual(summary["confirmed_pickups"], {"red_shell": 5})
        self.assertEqual(summary["pickup_mesos"], 7)

    def test_source_image_can_be_reviewed_without_recording(self):
        self.window.controller = self.controller
        self.start()
        self.window.refresh()
        from PIL import Image
        encoded = io.BytesIO()
        Image.new("RGB", (4, 3), "red").save(encoded, format="PNG")
        result = replace(parse_item_text("Red Snail Shell x 2", ["Red Snail Shell"]), source_png=encoded.getvalue())
        dialog = self.window.read_items()
        dialog.accept_result(result)
        self.root.update()
        self.assertIsNotNone(dialog.evidence)
        self.assertTrue(hasattr(dialog, "view_source"), "The user needs to inspect the image behind the proposal")
        preview = dialog.view_source()
        self.assertEqual((preview.image.width(), preview.image.height()), (4, 3))
        self.assertEqual(self.controller.ledger.active_session["records"], [])

    def test_editing_reviewed_text_requires_reviewing_new_source(self):
        self.window.controller = self.controller
        self.start()
        self.window.refresh()
        dialog = self.window.read_items()
        dialog.text.insert("1.0", "Red Snail Shell x 2")
        dialog.read_text()
        dialog.confirm_var.set(True)
        dialog.text.delete("1.0", "end")
        dialog.text.insert("1.0", "Red Snail Shell x 5")
        self.root.update()
        self.assertIsNone(dialog.evidence)
        self.assertFalse(dialog.confirm_var.get())
        self.assertTrue(dialog.confirm_button.instate(["disabled"]))
        dialog.submit()
        self.assertEqual(self.controller.ledger.active_session["records"], [])
        dialog.read_text()
        self.root.update()
        self.assertEqual(dialog.count_vars["red_shell"].get(), "5")
        dialog.confirm_var.set(True)
        dialog.submit()
        self.assertEqual(self.controller.ledger.summary()["confirmed_pickups"], {"red_shell": 5})

    def test_confirm_rechecks_text_before_pending_edit_callback(self):
        self.window.controller = self.controller
        self.start()
        self.window.refresh()
        dialog = self.window.read_items()
        dialog.text.insert("1.0", "Red Snail Shell x 2")
        dialog.read_text()
        dialog.confirm_var.set(True)
        dialog.text.insert("end", " changed source")
        dialog.submit()
        self.assertEqual(self.controller.ledger.active_session["records"], [])
        self.assertIsNone(dialog.evidence)

    def test_long_reader_warnings_leave_confirmation_accessible(self):
        self.window.controller = self.controller
        self.start()
        self.window.refresh()
        self.window.window.deiconify()
        dialog = self.window.read_items()
        result = replace(parse_item_text("Red Snail Shell x 2", ["Red Snail Shell"]),
                         warnings=tuple("Unreadable item source; review the original image. " * 6 for _ in range(8)))
        dialog.accept_result(result)
        self.root.update()
        self.assertTrue(dialog.confirm_button.winfo_ismapped())
        self.assertLessEqual(dialog.confirm_button.winfo_y() + dialog.confirm_button.winfo_height(), dialog.body.winfo_height())

    def image_reading(self):
        from PIL import Image
        encoded = io.BytesIO()
        Image.new("RGB", (4, 3), "red").save(encoded, format="PNG")
        return replace(parse_item_text("Red Snail Shell x 2", ["Red Snail Shell"]), source_png=encoded.getvalue())

    def test_rereviewing_image_text_does_not_duplicate_confirmed_image(self):
        self.window.controller = self.controller
        self.start()
        dialog = self.window.read_items()
        dialog.accept_result(self.image_reading())
        dialog.confirm_var.set(True)
        dialog.submit()
        dialog.read_text()
        dialog.confirm_var.set(True)
        dialog.submit()
        self.assertEqual(self.controller.ledger.summary()["confirmed_pickups"], {"red_shell": 2})
        self.assertIn("already confirmed from this source", dialog.status_var.get().lower())
        self.assertIn("New pasted source", dialog.status_var.get())

    def test_unedited_image_text_cannot_bypass_missing_ocr_consensus(self):
        self.window.controller = self.controller
        self.start()
        dialog = self.window.read_items()
        result = replace(self.image_reading(), items=(), warnings=("No OCR consensus for Red Snail Shell",))
        dialog.accept_result(result)
        dialog.read_text()
        self.assertEqual(dialog.evidence.result.items, ())
        self.assertEqual(dialog.count_vars, {})

    def test_corrected_image_text_retains_image_identity_and_preview(self):
        self.window.controller = self.controller
        self.start()
        dialog = self.window.read_items()
        result = self.image_reading()
        dialog.accept_result(result)
        identity = dialog.evidence.identity
        dialog.text.delete("1.0", "end")
        dialog.text.insert("1.0", "Red Snail Shell x 3")
        self.root.update()
        self.assertFalse(dialog.preview_button.instate(["disabled"]))
        with patch("tkinter.messagebox.askyesnocancel", return_value=True):
            dialog.read_text()
        self.assertEqual(dialog.evidence.identity, identity)
        self.assertEqual(dialog.evidence.result.source_png, result.source_png)
        self.assertEqual(dialog.count_vars["red_shell"].get(), "3")

    def test_new_paste_choice_preserves_text_and_releases_image_identity(self):
        self.window.controller = self.controller
        self.start()
        dialog = self.window.read_items()
        dialog.accept_result(self.image_reading())
        identity = dialog.evidence.identity
        dialog.confirm_var.set(True)
        dialog.submit()
        dialog.text.delete("1.0", "end")
        dialog.text.insert("1.0", "Red Snail Shell x 5")
        self.root.update()
        with patch("tkinter.messagebox.askyesnocancel", return_value=False):
            dialog.read_text()
        self.assertEqual(dialog.text.get("1.0", "end-1c"), "Red Snail Shell x 5")
        self.assertNotEqual(dialog.evidence.identity, identity)
        self.assertIsNone(dialog.source_png)
        self.assertIsNone(dialog.evidence.result.source_png)
        self.assertTrue(dialog.preview_button.instate(["disabled"]))
        dialog.confirm_var.set(True)
        dialog.submit()
        self.assertEqual(self.controller.ledger.summary()["confirmed_pickups"], {"red_shell": 7})

    def test_cancel_source_choice_keeps_typed_text_image_and_ledger_unchanged(self):
        self.window.controller = self.controller
        self.start()
        dialog = self.window.read_items()
        result = self.image_reading()
        dialog.accept_result(result)
        dialog.text.delete("1.0", "end")
        dialog.text.insert("1.0", "Red Snail Shell x 5")
        self.root.update()
        saved_evidence = list(self.controller.evidence_dir.glob("*.json"))
        with patch("tkinter.messagebox.askyesnocancel", return_value=None):
            dialog.read_text()
        self.assertIsNone(dialog.evidence)
        self.assertEqual(dialog.text.get("1.0", "end-1c"), "Red Snail Shell x 5")
        self.assertEqual(dialog.source_png, result.source_png)
        self.assertIs(dialog.image_result, result)
        self.assertFalse(dialog.confirm_var.get())
        self.assertEqual(self.controller.ledger.active_session["records"], [])
        self.assertEqual(list(self.controller.evidence_dir.glob("*.json")), saved_evidence)

    def test_stale_inventory_evidence_leaves_no_half_constructed_dialog(self):
        self.window.controller = self.controller
        self.start()
        evidence = self.controller.prepare_read(parse_item_text("Red Snail Shell x 2", ["Red Snail Shell"], mode="inventory"))
        self.controller.start("Next", "100", {}, confirmed=True)
        before = len(self.window.dialogs)
        with self.assertRaises(ValueError):
            self.window.new_snapshot(evidence)
        self.assertEqual(len(self.window.dialogs), before)

    def test_failed_wallet_suggestion_still_allows_manual_opening(self):
        def unavailable():
            raise AttributeError("wallet not yet available")
        self.window.wallet = unavailable
        try:
            dialog = self.window.new_session()
        except AttributeError as error:
            self.fail(f"A failed suggestion must fall back to manual entry: {error}")
        self.assertEqual(dialog.wallet_var.get(), "?")
        self.assertFalse(dialog.confirm_var.get())

    def test_second_opening_form_cannot_supersede_newly_started_outing(self):
        first = self.window.new_session()
        second = self.window.new_session()
        first.confirm_var.set(True)
        first.submit()
        second.confirm_var.set(True)
        second.submit()
        self.assertEqual(len(self.window.controller.ledger.sessions), 1)
        self.assertTrue(second.status_var.get())

    def test_older_snapshot_shows_latest_record_time_without_writing(self):
        self.window.controller = self.controller
        self.controller.start("Outing", "100", {}, confirmed=True, timestamp=1700000100)
        self.controller.ledger.record_event("transfer", "transfer", None, 0, 20, timestamp=1700000200)
        dialog = self.window.new_snapshot()
        dialog.time_var.set(self.ui.local_time(1700000150))
        dialog.confirm_var.set(True)
        dialog.submit()
        self.assertEqual(len(self.controller.ledger.active_session["records"]), 1)
        self.assertIn(self.ui.local_time(1700000200), dialog.status_var.get())

    def test_snapshot_journal_rows_distinguish_wallet_from_cash_activity(self):
        self.window.controller = self.controller
        self.start()
        self.controller.snapshot("120", {}, confirmed=True)
        self.window.refresh()
        for row in self.window.journal.get_children():
            values = self.window.journal.item(row, "values")
            self.assertEqual(values[3], "")
            self.assertTrue(values[4].startswith("wallet "))

    def test_record_details_can_render_decimal_metadata(self):
        self.window.controller = self.controller
        self.start()
        self.window.refresh()
        self.window.records["0"]["valuation"] = Decimal("0.25")
        self.window.journal.selection_set("0")
        try:
            self.window.show_record()
        except TypeError:
            self.fail("Record details should render decimal metadata as text")

    def test_selection_hides_overlay_and_restores_its_original_state(self):
        self.window.controller = self.controller
        self.start()
        self.root.deiconify()
        self.window.window.deiconify()
        dialog = self.window.read_items()
        observed = []

        def cancel(root, callback):
            observed.append((root.state(), self.window.window.state(), dialog.window.state()))
            callback(None)

        self.window.select_region = cancel
        dialog.select_area()
        self.assertEqual(observed, [("withdrawn", "withdrawn", "withdrawn")])
        self.assertEqual(self.root.state(), "normal")
        self.assertEqual(self.window.window.state(), "normal")
        self.assertEqual(dialog.window.state(), "normal")

    def test_meowdb_suggestion_requires_save_and_preserves_outing_price(self):
        self.window.controller = self.controller
        self.start()
        self.window.refresh()
        self.window.catalog_table.selection_set("red_shell")
        self.root.update()
        before = self.path.read_bytes()
        self.assertTrue(hasattr(self.window, "suggestion_button"), "Catalog needs a price suggestion control")
        self.window.suggestion_button.invoke()
        self.assertEqual(self.window.catalog_price.get(), "4")
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.controller.ledger.catalog["red_shell"]["unit_value"], Decimal("2"))
        status = self.window.status_var.get()
        self.assertIn("NPC sell-back per item", status)
        self.assertIn("2026-10-16", status)
        self.assertIn("https://meowdb.com/msclassic/item-db/351", status)
        self.window.save_catalog()
        saved = LootLedger(self.path)
        self.assertEqual(saved.catalog["red_shell"]["unit_value"], Decimal("4"))
        self.assertEqual(saved.active_session["catalog"]["red_shell"]["unit_value"], Decimal("2"))

    def test_subi_fractional_recharge_price_saves_and_reloads(self):
        self.window.controller = self.controller
        self.window.refresh()
        self.window.catalog_table.selection_set("subi_stars")
        self.root.update()
        self.window.suggestion_button.invoke()
        self.assertEqual(self.window.catalog_price.get(), "0.3")
        self.assertIn("Recharge per individual star", self.window.status_var.get())
        self.assertFalse(self.path.exists())
        self.window.save_catalog()
        saved = LootLedger(self.path)
        self.assertEqual(saved.catalog["subi_stars"]["unit_value"], Decimal("0.3"))
        self.assertEqual(saved.catalog["subi_stars"]["unit_value"] * 500, Decimal("150"))

    def test_unverified_price_suggestion_keeps_entered_price(self):
        self.assertTrue(hasattr(self.window, "suggestion_button"), "Catalog needs a price suggestion control")
        for name, kind in (("Unknown item", "loot"), ("Red Snail Shell", "supply"),
                           ("HP Potion", "supply"), ("MP Potion", "supply")):
            with self.subTest(name=name, kind=kind):
                self.window.catalog_name.set(name)
                self.window.catalog_kind.set(kind)
                self.window.catalog_price.set("37.5")
                self.window.suggestion_button.invoke()
                self.assertEqual(self.window.catalog_price.get(), "37.5")
                self.assertIn("No verified", self.window.status_var.get())
                self.assertFalse(self.path.exists())


if __name__ == "__main__":
    unittest.main()
