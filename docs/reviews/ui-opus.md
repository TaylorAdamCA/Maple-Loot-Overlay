# Adversarial review: loot UI and fork integration

**Verdict:** I found no confirmed blocker. One finding (M4) becomes **High** if the ledger accepts snapshots out of timestamp order. The contract doesn't say whether it does, so check that first. I didn't re-audit the core ledger or reader. Where a finding depends on their behaviour, it is marked *conditional*.

---

## Medium

### M1. One screenshot can be confirmed twice: once as an image, once as its OCR text
`ReadDialog.accept_result` puts the OCR `raw_text` into the text box. `read_text` then makes a result with `source_png=None`. `prepare_read` derives the identity from `"text\0"+raw_text` instead of from the image. The pickup event IDs therefore differ, and the duplicate check never fires.

**Reproducer (pickup mode):**
1. Load a screenshot. The proposal is Red Snail Shell 3. Confirm it (1 saved).
2. Without editing, click **Review pasted text**. The same item is proposed again. Confirm it (1 saved).
3. `confirmed_pickups` is now `{"red_shell": 6}`.

Related problems:
- Re-reviewing the single `raw_text` skips the reader's 2-of-3 OCR consensus and conflict blocking. A quantity the image reader blocked can come back as a proposal.
- Correcting an OCR typo and re-reviewing cuts the link to the image. `clear_review` disables **View original source image**, and the new evidence JSON doesn't reference the PNG.

**Impact:** Journal pickups are inflated. Snapshot profit is not affected.

**Minimal fix:**
- In `accept_result`, keep `self.source_png = result.source_png`. Clear it only when a new image is loaded, the mode changes, or the user presses an explicit "new pasted source" control.
- In `read_text`, if `self.source_png` is set, use `replace(result, source_png=self.source_png)`. The identity then stays keyed to the image, and a re-review with different values hits the existing "already confirmed with different values" check.
- Keep the preview button enabled while the image link is kept.

### M2. Inventory snapshot ID is deterministic, so the same reading at a later checkpoint collides
`LootController.snapshot` uses `"reading:" + evidence.identity`. That identity is a hash of session, mode and source text or PNG. Identical inventory text at two checkpoints is realistic: for example, a narrow paste like "Subi … x 500" when nothing changed, or the same saved screenshot. The second snapshot then reuses the first ID with a different timestamp or wallet.
- If the ledger rejects this, the reader cannot record that checkpoint, and the error is confusing.
- If the ledger treats it as an identical retry, the new checkpoint is silently dropped.

Snapshots are absolute, so deduplicating them gains nothing.

**Fix:** `identity = f"reading:{evidence.identity}:{uuid.uuid4().hex}"`. This keeps the evidence link and makes each ID unique.

### M3. `MAPLE_EXP_DATA` fallback breaks fork isolation
`DATA_DIR = MAPLE_LOOT_DATA or MAPLE_EXP_DATA or %APPDATA%\MapleLootOverlay`. `MAPLE_EXP_DATA` is upstream's documented override.

**Reproducer:** Set `MAPLE_EXP_DATA=D:\Maple` and run both upstream and the fork.
- Both use `D:\Maple\config.json`, `exp_log.*` and `mesos_digits.npz`.
- The mutex names now differ (`MapleEXP-…` vs `MapleLootOverlay-…`), so both run at once and write the same files concurrently.
- `{**DEFAULTS, **load_config()}` inherits upstream's saved `hotkeys_on: true` and `check_updates: true`. The fork then registers conflicting global hotkeys.

This contradicts the README ("does not migrate upstream settings"). `test_fork_settings.py` hides the problem because it sets `MAPLE_LOOT_DATA`.

**Fix:**
- Drop the `MAPLE_EXP_DATA` fallback.
- Add a test that sets only `MAPLE_EXP_DATA` (subprocess or `importlib.reload`) and asserts `DATA_DIR` ignores it.

### M4. Snapshots can be saved with timestamps earlier than existing records (conditional, Medium to High)
`SnapshotDialog.submit` only corrects the case where the typed time is in the *same second* as the latest record. Any earlier second goes straight to the ledger.

**Reproducers:**
- (a) Open **New snapshot** at 12:00:00. In **Record activity**, record a transfer of +20,000 (timestamp about 12:00:30, clamped to now). Then save the snapshot form, still showing 12:00:00. A snapshot with an earlier timestamp is appended after the transfer.
- (b) Type an older time by hand.
- (c) During the DST fall-back hour, the default displayed time parses (`fold=0`) to one hour earlier.

The contract only defines "external cash inflows before that snapshot" and "stale when later journal". If the ledger accepts out-of-order snapshots and uses append position, a transfer the snapshot wallet never saw is subtracted and profit is understated. If it uses timestamps, stale and cutoff behaviour no longer match the journal order shown in the UI. If the ledger rejects these, this drops to Low (a confusing error).

**Fix:**
- In `submit`, raise `ValueError` when `timestamp < latest`, with a message naming the latest record time.
- Better still, enforce this in `LootLedger.snapshot` and add a test.

---

## Low

### L1. A hidden or minimized ledger window can't be brought back
`App.open_profit` calls only `.lift()` on an existing window.
- If the user minimizes the Loot window and clicks **Loot + profit**, nothing visible happens.
- `select_area` withdraws `ProfitWindow` and all its transient dialogs. If the real `RegionSelector` ever exits without calling the callback (Alt+F4, an exception after creation), the window stays withdrawn until restart. The button can't recover it.

**Fix:** In `open_profit`, call `deiconify()` before `lift()`.

### L2. Half-built dialogs are left behind when constructors fail
`Dialog.__init__` creates and registers the Toplevel before `SnapshotDialog` calls `session()`, `snapshot_defaults()` or `owner.wallet()`.

**Reproducer:**
1. Read inventory text.
2. Start a new outing from another form.
3. Return, tick the box and click **Review full inventory snapshot**.

`ReadDialog` shows the error, but an empty "Inventory and wallet snapshot" window remains. An `AttributeError` from `wallet=lambda: self.meso.value` would do the same, and it wouldn't be caught.

**Fix:**
- Compute the catalog, session ID and defaults before `super().__init__`, or wrap the body and `self.window.destroy()` on exception.
- Wrap `owner.wallet()` so any failure falls back to `"?"`.

### L3. The opening form isn't bound to the outing active when it opened
- Double-clicking **New outing** (or opening it twice) creates two forms. Submitting both starts two outings, and the first is immediately superseded.
- Starting an outing ends the current one without saying so.

**Fix:**
- Store the active session ID when the opening form opens, and reject if it has changed.
- Add the active outing's name to the confirmation text.

### L4. `open_profit` can fail silently, and the new files are untracked
- `from loot_ui import ProfitWindow` sits outside the `try`.
- Only `ValueError` and `OSError` are caught.
- Under `--windowed`, an `ImportError` or any other exception (for example `OverflowError`, see L6) makes the button do nothing visibly.

This matters now because `loot_*.py`, the tests, `requirements-lock.txt` and `Start accounting.cmd` are all untracked (`??`). A commit that misses them breaks CI at `pip install -r requirements-lock.txt` and ships a dead button.

**Fix:**
- Move the import into the `try`.
- Catch `Exception`, call `log_error`, and show a messagebox.
- Make sure all the new files are added before committing.

### L5. Accounting labels
- **"net recorded cash"** includes transfers and adjustments. The test gives 29 = 10 + 4 − 2 − 2 + **20** − **1**. It appears in the "Journal:" line after pickups, sales, purchases and recharges, which invites reading it as earnings. Rename it to "net recorded cash incl. transfers/adjustments" and show the session-wide transfer total next to it.
- **"Pickups"** in the inventory table covers the whole session journal, while Latest, Change and Value change are as of the snapshot. Label it "Pickups (journal, whole outing)".
- **"Profit at catalog prices"** conflicts with the tab "Catalog for future outings". Use "at this outing's prices".
- **Journal "Mesos" column** shows the absolute wallet for opening and snapshot rows next to signed transaction amounts. "Units" shows `?` for snapshot rows. Render these as `wallet 1,234` and leave Units blank.
- **`conflicted_names`** is never shown in the review UI unless the reader also puts it in `warnings`.
- **Read dialog checkbox** says "? skips a pickup proposal" in inventory mode as well.

### L6. Permissively loaded data can break `refresh`
- `local_time` → `datetime.fromtimestamp` raises `OSError` (negative timestamps on Windows) or `OverflowError`. Persisted timestamps load permissively, so one bad record makes the window impossible to open.
- `show_record` → `json.dumps` has no `default=` and raises `TypeError` if any record value is a `Decimal`.

**Fix:**
- Guard `local_time` and show the raw value on failure.
- Use `json.dumps(..., default=str)`.

### L7. Overlapping pickup sources double count
Only byte-identical sources deduplicate. Re-pasting a cumulative chat log that has new lines, or recapturing the same chat area, gives a new identity. The old lines are then confirmed again. This is journal-only and partly documented ("identical text").

**Fix:** Add one line to the Read dialog: "Paste only lines not already confirmed; overlapping text is counted again."

### L8. Launcher and release details
- The release zip has no `run.bat` or `Start accounting.cmd`, and `READ ME FIRST` doesn't mention `--accounting-only`.
- If an accounting-only instance is running, `run.bat` hits the mutex and only focuses that capture-off instance. If the normal overlay is running, `Start accounting.cmd` focuses it but doesn't open the ledger.
- The README links `docs/reviews.md`, but the untracked material is `docs/reviews/`. Check the link target.

---

## Integration points I couldn't confirm from the given source
- **Real `RegionSelector`:** that it is called as `(root, callback)`, calls `callback(None)` on every exit, and returns `left`/`top`/`width`/`height` in the same physical pixels as mss. Tests only use lambdas.
- **Wallet suggestion:** that `self.meso.value` is the absolute wallet, not a session gain, and always exists.
- **Topmost overlay:** the root overlay isn't hidden during capture, so it can end up inside a selected inventory region.
- **Accounting-only status:** whether `_refresh`/`_pump` overwrite the "Accounting only. Screen capture is off." status, and whether the overlay's Start controls look active while doing nothing.
- **Reader items:** that `result.items` never repeats a name. `proposed_counts` keeps the last one silently.
- **Frozen build:** the PyInstaller output (lazy `loot_ui` import, `PIL.ImageTk` / `_imagingtk`) is built but never smoke-tested. Running the exe with `--accounting-only` and opening **View original source image** would cover it.

## Limitations
- This was a static review with no execution, and line numbers weren't available.
- The ledger and reader internals were taken on contract, so M2 and M4 depend on how the ledger handles ID reuse and timestamp ordering.
- The tests are synthetic and say nothing about live OCR accuracy or real DPI and selector behaviour.