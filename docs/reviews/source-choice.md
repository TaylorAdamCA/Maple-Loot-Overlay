# Narrow review: image-identity fix

**No concrete blocker.** The fix closes the Low finding, and I found no regression that changes ledger behavior.

## Fix verification

Tracing `read_text` through each branch:

- **No (new text source):** `new_pasted_source(keep_text=True)` clears `source_png` and `image_result` before parsing. So the parse branch skips the `replace(... source_png=...)` path, and `accept_result` doesn't overwrite the typed text. The new evidence gets a text-derived identity, and Preview is disabled. The 2 + 5 = 7 test shows the pickup is no longer skipped.
- **Yes (correction of image):** `image_result` is left untouched because `source_png == result.source_png`. Unchanged OCR text can therefore still reach the stored consensus result. The corrected text keeps the image identity, and the warning is added.
- **Cancel / Escape / closing the dialog:** These return `None` before `clear_review`. Evidence was already cleared by `source_edited` on the edit, so no new evidence and no ledger effect, which the test confirms. If the user re-clicks Read on an already-reviewed Yes-corrected text without editing, they get prompted again. Cancel then leaves the prior valid review intact, with `review_text` still matching. That's harmless.
- **Unchanged OCR text:** No prompt, so the original result is reused. The consensus-bypass test still holds.
- **Ordering:** Moving `clear_review()` after the prompt is the right call. It's what lets Cancel avoid side effects.

## Minor issues (Low or nit, not blockers)

1. **Correction guidance dropped where it's needed most (Low, UX).**
   - Scenario: the user confirms an image, sees OCR misread 2 when the true count was 3, corrects the text, and picks **Yes**. Exact-identity dedup returns `added == 0`.
   - The new message says "Already confirmed from this source. Use New pasted source for a different drop…". The old "Corrections use manual adjustments" hint now appears only in the `added` branch.
   - A user correcting a miscount is never pointed to manual adjustments. If they misread the message and use New pasted source, they'd record the full 3 again, for a total of 5.
   - The ledger stays correct as recorded; this is guidance, not a logic bug.
   - Suggested text: *"Already confirmed from this source. Use a manual adjustment to correct a miscount, or New pasted source only for a different drop."*

2. **Possibly misleading "Already confirmed" (nit).** The non-skip check treats `"0"` (or any value `confirm_pickups` filters without recording) as a real entry. If zero quantities are skipped rather than rejected, an all-zero first submission would show "Already confirmed from this source" even though nothing was ever confirmed. Fix by excluding values that parse to 0, or by having `confirm_pickups` report duplicate-skips separately from zero-skips.

3. **Whitespace-only edits prompt (nit).** Something like a trailing newline makes the text differ from `raw_text`, which triggers the dialog. That's acceptable and conservative; noting it only so it isn't reported as a bug later.

4. **Test gap (nit).** No test covers Yes → submit after the image was already confirmed. Adding one would pin down the status text from item 1.

## Build concern

**Resolved.** `copy_to_root` maps only `{NAME}.exe`, `app/_internal/**`, and `app/tesseract/**` (into `ROOT/_internal/tesseract`). Nothing is written outside `ROOT/{NAME}.exe` and `ROOT/_internal`, so the source launcher can't be overwritten. Stale-file deletion is also limited to `ROOT/_internal`. With the selector-over-withdrawn-root and accounting-only status checks now passing in the Tk tests, my earlier conditional concern is closed.

## Verdict

Ready to merge as far as this delta goes. I'd recommend the one-line status wording change in item 1 before release.