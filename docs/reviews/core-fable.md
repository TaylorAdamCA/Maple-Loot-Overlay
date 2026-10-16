I'll review the two modules, their tests, and the meso diff statically, then report issues by severity with reproducers.

## Verdict

No accounting blocker. The profit formula, snapshot handling, idempotence, and unknown-value propagation hold up under adversarial reading. I found one Medium robustness defect in timestamp handling, three Low issues, and several scope limits that should be labelled rather than fixed. The meso diff is internally consistent with its tests. Nothing here was executed, so test pass counts are taken from the files as written.

## Claims verified by reading

- **Profit avoids double counting.** `summary` in `loot_ledger.py` derives profit only from wallet delta, valued inventory delta, and transfers through the snapshot. Pickups, sales, purchases and recharges only feed journal totals. I recomputed the double-count test by hand and the expected -95 is correct.
- **Prices frozen per session.** `start_session` deep-copies the catalog into the session and every valuation reads the session copy. A later `set_item` cannot reach it.
- **No fallback to older profit.** `summary` keeps only the last snapshot index and rebuilds `missing` from that snapshot's own counts. There is no carry-forward of counts or wallet from earlier snapshots.
- **Idempotence.** `_append` returns False before the ordering check when ID and data match, and raises on any field difference including timestamp. Survives restart because stored timestamps round-trip as floats.
- **Persistence.** Temp file, fsync, `os.replace`, state assigned only after success, temp unlinked on failure. Correct for Windows.
- **Reader never touches the ledger.** `loot_reader.py` imports no ledger, no capture, no OS APIs. It only consumes a supplied array.
- **Test counts.** The ledger file defines 29 tests and the reader file 23, matching the report.

## Issues

**1. Medium. One future-dated timestamp makes the whole ledger file un-appendable.**
Location: `loot_ledger.py`, the `timestamp < latest` checks in `_append` and `start_session`, plus the year-9999 upper bound in `_timestamp`. Both checks compare against the latest stored record, and the ordering is also enforced on load. A mistyped Unix timestamp with one extra digit is accepted, persisted immediately, and then every default-timestamped event, snapshot, and new session is rejected until the clock catches up. Recovery requires hand-editing the JSON.

```python
ledger.record_event("typo", "pickup", None, 0, 1, timestamp=17600000000)  # year 2527, accepted
ledger.snapshot("close", 1000, counts)        # ValueError: Records must be in timestamp order
ledger.start_session("Fresh", 0, {})          # ValueError: New session cannot precede ...
```

The same check also fails after a small wall-clock step back, for example an NTP correction or VM resume, when `timestamp=None`.

Minimal fix: in the write paths, reject timestamps more than a few minutes ahead of `time.time()`, and when `timestamp` is None use `max(time.time(), latest)`. Keep the lenient bound only in `_stored_timestamp` so existing files still load.

**2. Low. Adjustments cannot correct a transfer, so profit silently uses the wrong number.**
Location: `summary`, the `external_transfer_net` sum only includes records whose kind is `transfer`. The docstring for `record_event` says adjustments "may carry signed cash", which invites using them as corrections.

```python
ledger.record_event("in", "transfer", None, 0, 200, timestamp=110)      # really 150
ledger.record_event("fix", "adjustment", None, 0, -50, note="correct in", timestamp=111)
ledger.snapshot("close", 1150, counts, timestamp=120)
ledger.summary()["profit"]   # -50, should be 0
```

Minimal fix: document that a transfer is corrected only by a counter-transfer, and say so in the adjustment docstring. Changing the formula is not needed.

**3. Low. A bare known item name does not block the item, while "name x" does.**
Location: `loot_reader.py`, `_UNREADABLE_PREFIX` requires a leading token before the name, so a line that is exactly a catalog name never reaches the conflict set.

```python
parse_item_text("Red Snail Shell x 12\nRed Snail Shell x", CATALOG).items   # ()
parse_item_text("Red Snail Shell x 12\nRed Snail Shell", CATALOG).items     # proposal for 12
```

This is inconsistent rather than unsafe, since the second reading contradicts nothing. Minimal fix: after the two unreadable patterns, also check `aliases.get(line, ())` and add those names to `conflicted` if you want name-only lines to count as unreadable.

**4. Low. Quantity ceilings disagree between reader and ledger.**
`MAX_QUANTITY` in the reader allows values up to 2^63 minus one, while `_integer` in the ledger rejects anything above 10^18. A proposal in that band is accepted by the reader and then raises at confirm time. Set the reader constant to the ledger's limit.

## Scope limits, not bugs

- **Journal totals span the whole session.** The per-kind meso totals, cash flow, and confirmed pickups include events after the latest snapshot. Profit and transfer net stop at the snapshot. A UI should not present them as reconciled when `stale` is True. The `journal_as_of` field already exposes the difference.
- **Profit is catalog-valued profit.** Loot or equipment outside the frozen catalog is invisible to the inventory delta, though cash from selling it still counts. Label this in the UI.
- **Every catalog item needs an explicit opening and closing count.** Omitted counts are unknown, not zero, which is correct, but the UI must make the zero-entry step obvious.
- **Hourly rate uses wall-clock duration** from session start to the snapshot, including AFK time.
- **OCR variants are correlated.** All three come from one image, so two-of-three agreement is a review aid, as the docstring says, not independent evidence. Two identical misreads of a digit still pass.
- **Tiny meso balances.** In the tracker, gains from a balance near zero are treated as jumps and rebased. This predates the diff.

## Meso fix diff

No blocker. The new threshold of a tenth for digit-count changes gives exactly the results the added checks assert, including the boundary cases I recomputed by hand. The symmetry argument still holds because both directions measure against the larger value, so a rollover misread and its correction cancel. Two caveats:

- The hunk does not show the branch that decides rebase versus gain. The tests only pass if that branch keys on `jump` and `old is None`, not on `digits_changed`. I am assuming that, since the diff did not change it.
- Behaviour change worth noting in the changelog: a shop spend under a tenth that crosses a digit boundary now counts as negative mesos instead of rebasing. That matches how same-digit spends were already treated.

## Evidence limits

Static read only. No tests were run, no live OCR or game footage was examined, and `tests/test_loot_ui.py`, `tests/test_fork_settings.py`, and the rest of `maple_exp.py` were not provided. Windows file-replace behaviour is reasoned from the standard library contract, not observed.