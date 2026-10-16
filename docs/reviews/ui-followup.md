# Follow-up review: loot UI and fork integration

**Verdict:** Every previous finding (M1–M4, L1–L8) is resolved in the source shown, and the new tests cover each fix. The M1 fix introduced one Low regression. One build-script item is conditional on code I wasn't shown. I found no Medium or High issues.

## Resolution check

| Finding | Status | Evidence |
|---|---|---|
| M1: image confirmed twice | Resolved | If the text is unedited, `read_text` reuses `image_result`, so OCR consensus and conflict blocking are kept. Edited text is re-keyed to `source_png`, so the identity and preview survive and changed values hit the "different values" rejection. `<<Modified>>` is queued, not synchronous, so rewriting the text box in `accept_result` doesn't clear the new evidence. |
| M2: snapshot ID collision | Resolved | IDs now get a UUID suffix and still contain the evidence identity. |
| M3: `MAPLE_EXP_DATA` fallback | Resolved | The fallback is gone. The subprocess test covers unset, empty and custom overrides. |
| M4: snapshots earlier than existing records | Resolved | `timestamp < latest` is rejected with the latest record's time. The same-second bump is kept. |
| L1: hidden ledger window | Resolved | `deiconify()` was added. The selector's close button now cancels exactly once. Selection restores each window's original state. |
| L2: half-built dialogs | Resolved | All three dialogs fetch their data before `Dialog.__init__`. A failing wallet suggestion falls back to `?`. |
| L3: opening form not bound to its outing | Resolved | The form checks its session ID on submit, and the warning names the outing being closed. |
| L4: silent `open_profit` failure | Resolved in code | The import is inside the `try`, `Exception` is caught, and the error is logged and shown. |
| L5: labels | Resolved | All the listed labels were changed. |
| L6: crashes from loaded data | Resolved | `local_time` has a fallback and `json.dumps` uses `default=str`. |
| L7: overlapping sources | Resolved | The warning is in the Read dialog and the README. |
| L8: launcher and release | Resolved | The packaged launcher and READ ME text exist, and mode switching is documented. `docs/reviews.md` exists, so the link works. |

## Remaining issues

### Low (journal only): unrelated pasted text inherits the image's identity
After an image is loaded, `self.source_png` stays set until **New pasted source** or a mode change. Any later text is treated as an edit of that image. That covers the common workflow of select-all and paste fresh chat text into the same box.

**Reproducer (pickup mode):**
1. Load a screenshot. The proposal is Red Snail Shell 2. Confirm it (1 saved).
2. Select all the text in the box and paste a new, unrelated chat line: `Red Snail Shell x 2`. This is a genuinely new pickup. Click **Review pasted text** and confirm.
3. The status reads "Saved 0 pickup entries. Repeated evidence is counted once." `confirmed_pickups` stays `{"red_shell": 2}`.
4. With `x 5` instead of `x 2`, saving is rejected with "already confirmed with different values… use a manual adjustment".

In both cases the evidence JSON is filed under the image's identity with the "manually edited from an image" warning, and **View original source image** shows an unrelated screenshot.

**Why only Low:**
- It fails toward undercounting or rejection, never inflation.
- Snapshot profit is unaffected.
- A warning line is shown.

However, the "counted once" message actively reassures the user that the drop is correct.

**Fix:**
- In `read_text`, when an image is linked and the text differs from `image_result.raw_text`, ask: "Is this text a correction of the loaded image?" Answer **No** runs `new_pasted_source()`, keeping the typed text, then parses it as plain text.
- When `added == 0`, say "already confirmed from this source" in the status, instead of the generic "counted once" line.

**Test to add:** confirm an image, replace the text with an unrelated paste, answer **No**, and assert the evidence identity changes.

### Low, conditional: `build.py` may overwrite the source launcher
`build.py` writes the exe launcher (`start "...{NAME}.exe" --accounting-only`) into `app` and then calls `copy_to_root(app)`. I wasn't shown `copy_to_root`.

If it copies loose files into the checkout root, a build replaces the source-checkout `Start accounting.cmd` with one that points at an exe. That launcher fails if the exe isn't copied too, and it changes the documented "source launchers are the current delivery" behaviour.

**Check:** run a build and diff the root `Start accounting.cmd`. If it changed, exclude the launcher from `copy_to_root`.

### Process: the new files may still be untracked
The git snapshot from the start of this session shows these still as `??`:
- `loot_*.py`
- the four new test files
- `requirements-lock.txt`
- `Start accounting.cmd`
- `docs/reviews*`

I can't tell their current state without tools. If any are left out of the commit:
- the button breaks, though now with a visible error instead of silently;
- CI fails at `requirements-lock.txt`.

`git status` before committing would settle it.

## Integration points I couldn't confirm (no code change implied)
- **Selector visibility with the root withdrawn.** `select_area` now withdraws the root overlay. If the real `RegionSelector` makes its window `transient(root)`, Windows hides it as well. Every window would then be invisible, and the only way to cancel is Esc with keyboard focus. The new selector test builds the selector over a withdrawn root but doesn't check that it is visible.
  - **Manual check:** once, on the live overlay, confirm the selector appears, cancels and captures.
- **Overlay code re-showing the root.** Something like a topmost re-assert, compact toggle or show hotkey could re-show the root while it is withdrawn, which would put the overlay back inside the capture.
- **Accounting-only status after startup.** The test checks the status before the event loop runs `_refresh` (500 ms) and `_pump` (200 ms). Whether they overwrite "Accounting only. Screen capture is off." is still unverified.
  - **Test fix:** call `root.update()` after `root.after(600, ...)` in the test before asserting.
- **Selector region coordinates under DPI scaling, and live OCR accuracy.** Both are uncalibrated, as the README says.
- **Frozen build and packaged launcher.** Both are unbuilt and unverified, as documented.

## Limitations
- This was a static review with no execution.
- The ledger and reader were taken on the stated contract.
- I wasn't shown `copy_to_root`, `more_buttons`, `_refresh`/`_pump` or the window-focus lookup.
- I checked that the new tests exercise each fix, but I didn't run them.