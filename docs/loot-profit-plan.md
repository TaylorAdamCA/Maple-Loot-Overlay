# Loot and profit implementation

The user approved the separate-fork scope in the October 16 review and explicitly requested implementation, an upstream meso fix, and Claude reviews. The target is the main Windows PC; no VM integration is included. Both existing helpers remain unchanged.

## Deliverables

1. An isolated upstream branch fixes legitimate small meso digit-boundary changes without accepting missing/extra-digit OCR errors. Include failing-before/passing-after regressions, independent review, and an upstream PR if the patch remains small.
2. This fork retains the existing EXP UI and reader, but owns its data directory and has networking disabled by default. Add a Loot and profit window for inventory snapshots, confirmed pickups, shop transactions, prices and session history.
3. A conservative local ledger records explicitly confirmed pickups, expenses and sales with unique event identities. Full inventory/wallet snapshots provide an independent asset-change profit calculation. Missing counts/prices prevent a complete profit claim. Unsold loot uses configured NPC value; stars and supplies use configured replacement cost. No prices are invented.
4. Drop reading is assisted by explicitly selected screen regions and user confirmation before ledger entry. A reader must preserve the original image/text and proposed values, reject ambiguity and never interpret a hidden or empty panel as zero. Unknown item identities remain reviewable. A future automatic commit requires calibrated fixtures; this version makes the review step visible.
5. Tests cover the actual ledger and reader path; saved/synthetic frames are distinct from live calibration. Claude reviews inspect the bug fix and fork, with their concrete findings reproduced and addressed. No native input or NGS analysis.

## User workflow

Keep the existing EXP overlay. Open Loot and profit to start a separate outing, configure NPC prices and supply costs, and record opening quantities and wallet. Add confirmed pickups or review text captured from a selected visible inventory/loot area. Record actual star recharges and purchases, plus permitted or other user-selected item sales for accounting only. End or checkpoint the outing with a complete inventory and wallet snapshot. Display confirmed cash income, realized expenses, item counts, and snapshot-based profit separately, with coverage and timestamp.

The snapshot calculation is wallet change plus change in valued loot and supplies, adjusted for explicit external transfers. Sales and purchases already reflected in cash/inventory are not added or deducted again. The event journal supplies the category breakdown and confirmed drop counts, not a second addition to this result.

## Integration constraints

The initial item catalog names Red Snail Shell, Blue Snail Shell, Mushroom Spore, Subi Throwing-Stars, potion and pet-food entries. Unit prices default to unknown; users enter observed prices. All custom items are supported. Quantity is item units, including individual stars rather than number of stacks. A snapshot must account for every catalog item that participates in the session.

Screen-reading assists user accounting only. The app does not move the character, pick up loot, sell or recharge. The game and both helpers may remain closed throughout implementation and validation.

## Verification

Run upstream logic and image suites plus new ledger/reader tests in an isolated environment. Verify the fork's data isolation and no unsolicited network startup. Review UI with synthetic data without capturing the game. Record exact model IDs, source revisions, test results and limitations in docs/reviews.md.
