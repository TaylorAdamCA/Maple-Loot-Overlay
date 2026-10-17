# Maple Loot Overlay

A separate Windows PC fork of [beanguri/Maple-Classic-Exp-Overlay](https://github.com/beanguri/Maple-Classic-Exp-Overlay), with the original EXP/wallet reader, a meso rollover fix, confirmed drop tracking, and a local profit ledger.

This version uses assisted item accounting. You review text/image proposals or enter confirmed activity; the app never records pickups automatically. It does not identify items from inventory icons. Complete wallet and inventory checkpoints establish profit. No game inputs, selling, recharging, memory reads, or VM setup are included.

## Start on your PC

For this prepared checkout, double-click **run.bat** for the overlay, or **Start accounting.cmd** for the ledger without continuous game capture. The second option is useful for reviewing history or entering a completed outing. Use **Loot + profit** in the main overlay to open the new window.

Close the current instance before switching between these modes. The fork runs one instance per data folder, so opening the other launcher only focuses the existing instance; it does not change capture mode or open the ledger. The current delivery is these source launchers. No portable executable has been built or released for this fork.

From a fresh source checkout on Windows with Python 3.13:

```powershell
py -3.13 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.\run.bat
```

Image OCR also needs Tesseract with English data. Put it in `tesseract\tesseract.exe` with its DLLs and `tessdata`, or install it from the [Windows distribution linked by Tesseract](https://github.com/UB-Mannheim/tesseract/wiki). Text entry and the ledger work without it. Runtime binaries and personal data are not committed to Git.

The fork stores its settings and ledger in `%APPDATA%\MapleLootOverlay`. It does not migrate upstream settings or either existing helper. Automatic update checks, global hotkeys and run uploads are off by default. Run uploads remain disabled even if an old config enables them. You can enable hotkeys or update checks in Settings. **Best maps** opens the upstream website only when clicked.

## Track drops and profit

The Catalog has **Use MeowDB suggestion** for a small [sourced price set](docs/price-sources.md), including shells, spores, Subi recharge, pet food and named potions. Select an item, apply the suggestion, check its date and basis, then **Save item / price**. This fills the editor only. It does not overwrite saved prices or existing outings automatically. Generic HP/MP Potion entries need the actual potion name. Vendor prices can differ; observed costs take precedence.

1. Open **Loot + profit**, then **Catalog for future outings**. Set observed NPC sale values for loot and replacement cost per unit for supplies. Add or rename specific potions/custom drops before starting. For stars, enter the price per individual star, not per stack. Prices remain fixed within each outing.
2. Choose **New outing**. Record the wallet and total inventory counts at the same checkpoint. Count all stacks. Seven full stacks of 500 stars means 3,500 stars. Enter `0` only for a confirmed absence. `?` or blank means unknown.
3. Use **Record activity** for confirmed pickups, sales, purchases, star recharges and external cash transfers. Shop amounts are actual transaction totals. These entries describe activity and do not send any inputs to MapleStory.
4. **Read items** proposes quantities from pasted text, an image file, or a screen region you select. Choose whether the reading describes newly collected items or total inventory counts. Review the preserved source, warnings and proposed quantities before confirming. Inventory readings fill a checkpoint; they are never counted as pickups.
5. Choose **New snapshot** after training or a shop trip. Enter the current wallet and complete counts from the same checkpoint. Profit and profit/hour are calculated through that timestamp, including travel and shop time. Start a new outing to begin another session; earlier outings remain in the history dropdown.

Recognized text has an exact catalog name and quantity, for example:

```text
Red Snail Shell x 12
Obtained 3 Mushroom Spore
```

The reader does not infer icon identities or sum indistinguishable stacks. Conflicting quantities remain unknown. OCR needs agreement across image variants, followed by your confirmation. Screenshots and OCR text stay in the local `loot-evidence` folder.

Paste only pickup lines that have not already been confirmed. Exact source repeats are checked, but changed text, another capture, or a cumulative chat excerpt can describe the same pickup under a different source identity. Overlapping sources can therefore count pickups again. Check the journal before confirming. If identical text describes a new pickup, record that activity manually.

Live item OCR and screen-region selection across DPI settings have not been calibrated. Multiple image variants can repeat the same OCR mistake. Compare every proposal with the original source, and keep the overlay and other windows outside the selected game region because they can appear in the capture.

## What profit means

```text
profit = wallet change + valued inventory change - external net cash inflows
```

Loot is valued at its configured NPC price. Unused stars and supplies retain their replacement value. A shell sale moves value from inventory to cash; a recharge moves cash into stars. These transactions are not added or subtracted a second time. Consumed stars and potions reduce inventory value and therefore reduce profit.

For example, an outing starts with 1,000 mesos and 500 stars worth 0.2 mesos each. It ends with 1,200 mesos, 100 stars and 10 shells worth 2 mesos each. Profit is `200 - 80 + 20 = 140 mesos`. The prices in this example are illustrative, not game price defaults.

This is inventory-valued profit, not merely cash collected. Untracked items or external item transfers can make it incomplete. Track all items relevant to the outing. Record cash transfers so gifts/storage withdrawals are not mistaken for earnings. An item adjustment corrects the activity journal only; fix actual holdings with a new snapshot.

Missing required counts or prices show **profit unknown**. The latest incomplete snapshot does not fall back to an older complete result. Activity recorded after a snapshot marks it stale. The overlay wallet is only a suggestion for a checkpoint, because its last confirmed reading may be old. Check it against the game.

The original EXP and meso/hour figures still use the upstream active-farming clock. They are separate from the new outing profit calculation. See the [upstream guide](https://github.com/beanguri/Maple-Classic-Exp-Overlay#readme) for EXP-reader controls; its downloads, upload settings and data-directory instructions apply to upstream, not this fork.

## Meso bug fix

A small legitimate wallet change across a digit boundary, such as `9,980 -> 10,020`, previously reset the baseline and lost the 40-meso gain. This fork counts confirmed small rollovers while retaining stronger checks for large and tenfold OCR jumps. The isolated [upstream PR](https://github.com/beanguri/Maple-Classic-Exp-Overlay/pull/1) and its regressions are separate from the new ledger.

## Development and verification

```powershell
.venv\Scripts\python.exe -B tests/test_logic.py
.venv\Scripts\python.exe -B tests/test_screen.py
.venv\Scripts\python.exe -B tests/test_fork_settings.py
.venv\Scripts\python.exe -B -m unittest discover -s tests -p "test_loot*.py"
```

Some upstream checks use Windows hotkeys and require an ordinary desktop session. Tests use separate temporary data. Reader and UI tests use synthetic inputs; passing them is not a live-game OCR accuracy claim. See [review and verification evidence](docs/reviews.md).

To build a portable Windows folder after installing PyInstaller in the venv, set `TESSERACT_DIR` if needed and run `.venv\Scripts\python.exe build.py`. The generated folder includes the executable, its runtime files, **Start accounting.cmd**, and **READ ME FIRST.txt**. The packaged accounting launcher passes `--accounting-only` to the executable. This packaging path has not been verified with a built executable; source launchers are the current delivery. Building does not install a service or touch either helper.

Original project copyright and MIT license are retained in [LICENSE](LICENSE).
