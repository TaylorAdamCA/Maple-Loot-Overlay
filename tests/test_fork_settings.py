"""Isolation and privacy checks for the separate analytics fork."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch

_test_data = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
os.environ["MAPLE_LOOT_DATA"] = _test_data.name
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import maple_exp as m


class ForkSettingsTests(unittest.TestCase):
    def test_fork_data_override_never_reads_or_writes_legacy_directory(self):
        for override in (None, "", "custom"):
            with self.subTest(override=override), tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as folder:
                root = Path(folder)
                legacy = root / "upstream"
                legacy.mkdir()
                legacy_config = legacy / "config.json"
                legacy_config.write_text('{"upstream_marker":"must remain private"}', encoding="utf-8")
                original = legacy_config.read_bytes()
                env = os.environ.copy()
                env["APPDATA"] = str(root / "appdata")
                env["MAPLE_EXP_DATA"] = str(legacy)
                env.pop("MAPLE_LOOT_DATA", None)
                expected = root / "appdata" / "MapleLootOverlay"
                if override is not None:
                    env["MAPLE_LOOT_DATA"] = str(root / override) if override else ""
                    if override:
                        expected = root / override
                script = ("import json; import maple_exp as m; cfg = m.load_config(); "
                          "cfg['isolation_test'] = True; m.save_config(cfg); "
                          "print(json.dumps({'path': str(m.DATA_DIR), 'upstream_marker': cfg.get('upstream_marker')}))")
                result = subprocess.run([sys.executable, "-c", script], env=env,
                                        cwd=Path(__file__).resolve().parents[1],
                                        capture_output=True, text=True, check=True, timeout=30)
                observed = json.loads(result.stdout)
                self.assertIsNone(observed["upstream_marker"])
                self.assertEqual(Path(observed["path"]), expected)
                self.assertTrue(json.loads((expected / "config.json").read_text(encoding="utf-8"))["isolation_test"])
                self.assertEqual(legacy_config.read_bytes(), original)

    def test_accounting_only_integrates_without_starting_capture_or_uploads(self):
        root = tk.Tk()
        root.withdraw()
        try:
            with patch.object(m, "Hotkeys"), patch.object(m.threading, "Thread") as thread, \
                    patch.object(m.App, "check_updates") as updates:
                app = m.App(root, m.DigitTemplates.load(), capture_enabled=False)
                app.open_profit()
                first = app.profit_window
                first.window.withdraw()
                app.open_profit()
                self.assertIs(app.profit_window, first)
                self.assertEqual(first.window.state(), "normal")
                self.assertEqual(first.controller.ledger.path, m.DATA_DIR / "loot-ledger.json")
                app._pump()
                app._refresh()
                root.update_idletasks()
                self.assertIn("Accounting only", app.status.get())
                self.assertEqual(str(app.start_btn["state"]), "disabled")
                app.toggle()
                self.assertFalse(app.session.on)
                self.assertIn("Accounting only", app.status.get())
                thread.assert_not_called()
                updates.assert_not_called()
                app.close()
        finally:
            try:
                root.destroy()
            except tk.TclError:
                pass

    def test_ledger_import_failure_is_reported(self):
        app = m.App.__new__(m.App)
        app.root = object()
        app.profit_window = None
        with patch.dict(sys.modules, {"loot_ui": None}), \
                patch.object(m.messagebox, "showerror") as report, patch.object(m, "log_error") as log:
            app.open_profit()
        report.assert_called_once()
        log.assert_called_once()

    def test_region_selector_close_cancels_exactly_once(self):
        root = tk.Tk()
        root.withdraw()
        try:
            with patch.object(m.mss, "mss") as capture:
                capture.return_value.__enter__.return_value.monitors = [
                    {"left": 0, "top": 0, "width": 800, "height": 600}]
                results = []
                selector = m.RegionSelector(root, results.append)
                root.update_idletasks()
                self.assertTrue(selector.win.winfo_viewable())
                close = selector.win.protocol("WM_DELETE_WINDOW")
                self.assertTrue(close)
                selector.win.tk.call(close)
                self.assertEqual(results, [None])
                selector._finish(None)
                self.assertEqual(results, [None])
        finally:
            root.destroy()

    def test_no_unsolicited_requests_or_hotkeys_on_fresh_install(self):
        self.assertFalse(m.DEFAULTS["check_updates"])
        self.assertFalse(m.DEFAULTS["hotkeys_on"])
        self.assertFalse(m.load_config().get("share_runs", True))

    def test_fork_never_uploads_modified_statistics_to_upstream(self):
        app = m.App.__new__(m.App)
        app.cfg = {"share_runs": True}
        app._sharing = False
        with patch.object(m, "post_runs") as send, patch.object(m, "AutoLog") as log:
            app.autolog = log.return_value
            app.share_runs()
            send.assert_not_called()
            app.autolog.unshared_runs.assert_not_called()

    def test_upstream_files_are_not_migrated(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as folder:
            root = Path(folder)
            old, new = root / "old", root / "new"
            old.mkdir()
            (old / "config.json").write_text('{"share_runs":true}')
            with patch.object(m, "HOME_DIR", old), patch.object(m, "DATA_DIR", new):
                m.move_old_data()
            self.assertFalse(new.exists())


if __name__ == "__main__":
    unittest.main()
