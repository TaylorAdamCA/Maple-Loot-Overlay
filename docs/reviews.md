# Review and verification evidence

This fork targets the main Windows PC. Its base is upstream commit `fc9b1ac4e48406bf0e3325966efb6a9ab8dbdfb6`. The meso fix was submitted separately as [upstream PR #1](https://github.com/beanguri/Maple-Classic-Exp-Overlay/pull/1).

## Claude reviews

Reviews used the logged-in Claude CLI, high reasoning effort, no tools, no MCP servers, no persistent session and source supplied on stdin. Model names below are the actual IDs returned in `modelUsage`, not aliases inferred from requested names.

| Review | Returned model | Result |
|---|---|---|
| [Meso fix](reviews/meso-opus.md) | `claude-opus-5-5` | No blocking finding. Added exact 10% boundary checks in both directions. |
| [Ledger, reader and meso diff](reviews/core-fable.md) | `claude-fable-5-1` | Found future-timestamp lockout, unclear transfer correction, bare-name OCR inconsistency and mismatched quantity ceilings. Reproduced and addressed all four. |
| [UI and integration](reviews/ui-opus.md) | `claude-opus-5-5` | Found source-identity overlap, inventory checkpoint identity, data-directory isolation and UI/release issues. Corrected and regression-tested; follow-up confirmed the original findings resolved. |
| [UI corrections follow-up](reviews/ui-followup.md) | `claude-opus-5-5` | All original findings resolved; one Low source-choice usability issue was subsequently corrected and tested. |
| [Core corrections follow-up](reviews/core-followup.md) | `claude-fable-5-1` | Verified all four earlier core findings were fixed; no new blocking regression. Listed small reader conservatism and test-coverage limits. |
| [Final source-choice review](reviews/source-choice.md) | `claude-opus-5-5` | Verified the correction/new-source/cancel branches; no concrete blocker. Confirmed the build cannot overwrite the source launcher. Applied its suggested duplicate-status guidance. |

A larger combined Opus review exceeded its seven-minute limit without a completed report. It is not counted as a completed review. The completed UI and source-choice follow-ups confirmed the corrections. The final review's conditional zero-quantity concern does not apply: zero pickup quantities raise before saving, with regression coverage. Changed quantities for already-confirmed sources also raise with manual-adjustment guidance; the duplicate status now repeats that guidance.

The raw reviews describe the source they received. They retain comments that have since been resolved. These are static source reviews, not evidence of live-game behavior.

## Findings addressed

- New explicit timestamps more than 60 seconds ahead are rejected before saving. Default timestamps tolerate a clock moving backward. Loading old saved timestamps and retrying the same record remain supported.
- Transfer mistakes use a signed counter-transfer. Journal adjustments do not change checkpoint profit or holdings. A new snapshot corrects holdings.
- A known item name without a quantity blocks that item's proposal, including OCR consensus. Reader and ledger quantities share the `10**18` ceiling.
- Editing pasted source invalidates its old proposals. Submission checks the displayed source again before saving.
- Warning text scrolls within a bounded area, leaving confirmation controls visible.
- Dialog destruction cancels delayed capture and polling. Stale-form checks cover opening, snapshot, event and reading submissions. Selected-area capture hides and restores the main overlay and owned dialogs.
- Image identity remains attached through text correction. Unchanged OCR text keeps its original consensus result. Exact pickup retries deduplicate, including after restart. Absolute inventory snapshots get unique identities. Failed writes retain previous in-memory and saved state.

## UI findings and verification

The original review was static. Corrections below were checked with executable regressions; a packaged executable and live game were not tested.

| Finding | Resolution or current status |
|---|---|
| M1: image re-reviewed as text can duplicate pickups or lose the image link | Preserved image identity and original consensus through re-review; regressions cover duplicates, edited text, and rejected OCR. Changed image text asks whether it is an image correction or a new pasted source, preserving typed text for the latter. Cancel leaves evidence unchanged. |
| M2: identical inventory evidence collides at a later checkpoint | Each absolute checkpoint has a new record identity while retaining the evidence hash. Repeated evidence with a changed wallet is tested. |
| M3: upstream `MAPLE_EXP_DATA` override can select shared data | Removed fallback; subprocess tests cover unset, empty and explicit fork overrides and verify legacy config bytes remain unchanged. |
| M4: older snapshot timestamp may misorder transfers | Already rejected by ledger append and load validation. The exact transfer reproducer left history unchanged. UI now names the latest allowed time and preserves default timestamp precision/DST identity. |
| L1-L4: hidden-window recovery, failed dialog construction, concurrent opening forms, and visible launch errors | Existing window is deiconified; dialog preconditions run before construction; opening forms are bound to their active outing; import/initialization errors are logged and shown. Regression-tested. |
| L5-L6: accounting labels and display of unusual persisted values | Journal totals and checkpoint totals are labelled separately; window rendering tolerates unusual dates and Decimal values. Regression-tested. |
| L7: overlapping pickup sources | UI and README warn that cumulative text and recaptured images can count a pickup again. Semantic deduplication across different sources is not claimed. |
| L8: source/packaged launchers and mode switching | README and generated READ ME FIRST explain that the current instance must close before switching modes. `build.py` now includes a packaged accounting launcher. Archive-generation verification is separate from an executable smoke test; no executable build was performed. |

The README's `docs/reviews.md` target exists. An independent integration reviewer found no blocker and reran all seven fork tests. Accounting-only mode disables continuous-capture controls and ignores their hotkeys; closing the selector cancels exactly once.

## Tests

Final integrated run after UI and integration corrections, on Windows with Python 3.13 and `requirements-lock.txt`:

| Command | Result |
|---|---:|
| `python -B tests/test_logic.py` | 143 checks passed |
| `python -B tests/test_screen.py` | 19 checks passed, including Tesseract class checks |
| `python -B tests/test_fork_settings.py` | 7 tests passed |
| `python -B -m unittest discover -s tests -p "test_loot*.py"` | 99 tests passed |

The 268 checks include 35 ledger, 26 reader and 38 controller/Tk tests. The full suites passed after the original UI corrections; after the final source-choice adjustment, all 99 loot tests and seven integration tests passed again. New behavior had failing-before regressions. Image tests distinguish synthetic inputs from the upstream saved game frames. Actual Tesseract also read three generated item lines correctly through the new reader. That sample does not establish real-game item OCR accuracy.

Host execution resolved the sandbox's temporary-directory and native-hotkey restrictions. No tests were removed to hide those environment failures. A desktop visual inspection could not finish because Computer Use app approval timed out. The automated Tk tests ran; the short-lived demo used synthetic data and no capture. A portable executable was not built or released; the prepared source launchers are the delivery for this PC.

## Local OCR runtime

The prepared checkout contains Tesseract `5.5.3.20260724`, English data and required libraries in the ignored `tesseract` folder. The installer was extracted, never run, and no global installation was made.

Installer source: [official Tesseract release](https://github.com/tesseract-ocr/tesseract/releases/tag/5.5.3). SHA-256 `bee9e3434bd94fd65387d9be28cd467a41f61b1275383b55b0f59a1331270ae4` matched the release API digest. English data is from `tesseract-ocr/tessdata_fast` commit `87416418657359cb625c412a48b6e1d6d41c29bd`, SHA-256 `7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2`.

Windows Authenticode reported a certificate-validity error for the installer. A release hash match is verified; a valid Authenticode result is not claimed. Runtime binaries and model data are local dependencies, not committed or published as a release.

## Isolation and limits

Before/after SHA-256 comparisons found no changes in 1,204 checked MaplePatrolVM source/script files or 322 MapleFramework files. Neither helper was imported, launched, stopped, deployed or edited by this work.

New settings and journal data use `%APPDATA%\MapleLootOverlay`. Automated uploads stay disabled. Default update checks and hotkeys are off. The integration test opens the actual fork UI with continuous capture disabled and asserts that it starts neither capture nor update-check threads.

The item workflow is assisted. Users supply complete counts, observed prices and confirmation. Icon-only inventories, dropped-item sprites and repeated chat lines across different images have not been calibrated for automatic accounting. OCR variants share one image and can repeat the same error. Overlapping text or recaptured images may refer to pickups already in the journal; source hashing cannot establish that they are new events. Real screen-region/DPI behavior and the frozen executable remain unverified. Missing information stays unknown. Profit values the configured catalog at the outing's frozen prices and does not infer omitted items or transfers.
