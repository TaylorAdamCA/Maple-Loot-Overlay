Verdict: all four earlier findings are fixed in the code shown, the new tests exercise the exact claimed behaviour, and I found no new blocking regression. The isolation test design is sound. A few small gaps are listed at the end.

**Future timestamps verified**

- `_write_timestamp` rejects explicit values above now plus 60 seconds and is the only path used for new records and new sessions. The boundary test accepts exactly 1060 and rejects 1060.001, including via an aware datetime.
- Defaults return the larger of the clock and the latest record. The clock regression test traces correctly: records written at a clock of 990 land at 1010, duplicate retries return False, and the next session starts at 1010. Reload passes because session order is checked against the last record of the previous session, which matches what `start_session` enforces.
- Saved files use `_stored_timestamp`, which only checks type and the year 9999 range. The recovery test loads a record at year 2527, retries it idempotently both with and without the explicit time, and a default write lands at that same future value. Correct per the claim. One consequence worth knowing: after such a file loads, every explicit timestamp is rejected until the real clock passes it, because explicit values must be both ordered and under now plus 60. Only default writes work. That is acceptable but undocumented.
- Idempotent retries bypass the future bound by calling `_timestamp` rather than `_write_timestamp`, which is the right choice. Mismatched explicit timestamps still raise, and an int saved timestamp compares equal to the float retry.

**Corrections verified**

- Transfers through the closing snapshot sum into `external_transfer_net`, so a counter-transfer of -50 after +200 yields a net of 150 and zero profit against a wallet of 1150. The original record stays at 200.
- Adjustments feed only `adjustment_mesos`, `cashflow_mesos`, and `adjustment_quantities`. Profit uses wallet delta, valued inventory delta, and transfer net only. The adjustment test's expected profit of 12 and cashflow of 10 both follow from the code.
- A transfer with an item or nonzero quantity is rejected. An adjustment with a quantity but no item is rejected.

**Reader fixes verified**

- Bare known names: after both quantity patterns fail, the code tries the two unreadable patterns and then checks the whole normalized line against the alias map. "red snail shell" misses the prefix pattern's extracted name but hits the whole-line lookup, so it lands in the conflicted set. The alias form with extra whitespace normalizes and conflicts the same way. Malformed suffixes such as "x 1,00" and "x -1" conflict through the unreadable suffix pattern.
- Image consensus unions `conflicted_names` from every variant before counting agreement, so a bare name in one variant blocks two agreeing variants. The test matches the code path.
- The quantity ceiling is 10 to the 18th in both files. The length check of 19 digits plus the value comparison accepts 10 to the 18th and rejects one above it and the int64 maximum. The ledger's `_integer` uses the same bound with `abs`, so a reader proposal always fits an event quantity or snapshot count.

**Isolation claim**

The subprocess test covers the three cases correctly. The `or` in the environment lookup makes an empty override fall through to the default, and popping the variable before optionally setting it keeps the parent process's override from leaking. Setting `APPDATA` under the temp root means the default case cannot touch the real profile. The module-level override at import protects the in-process tests. I cannot see the source of `maple_exp.py`, so the claim that the legacy variable is never read rests on the test, which only detects a read if it would surface in the loaded config.

**Minor gaps, none blocking**

- Inventory mode drops "Obtained N item" lines with a warning but does not mark the item conflicted. Two variants agreeing on "Mushroom Spore x 3" plus a third reading "Obtained 4 Mushroom Spore" would still propose 3 in inventory mode. The same mode check happens before the conflict logic, so moving the conflict marking ahead of the `continue` would close it.
- A matched line whose name is not in the catalog, such as "Red Snail Shell. x 13", is not marked conflicted and does not fall back to the unreadable extraction. Two clean variants then win consensus. This is consistent with the no fuzzy matching rule but is less conservative than the bare-name handling.
- The isolation subprocess test asserts only that the legacy config bytes are unchanged. Adding an assertion that the legacy directory still contains exactly that one file would also catch a stray ledger or log written there.
- The migration test asserts the new directory does not exist but not that the old directory is untouched.
- The remaining fork tests that build a Tk root and `App` are UI territory and I left them to the separate review.

Nothing here requires another change before merging on the ledger and reader side. The first two reader gaps are small conservatism improvements if you want them.