# Verdict: PASS

I found no actionable regression. Everything below is either behavior that already existed before this PR or a non-blocking note.

## Why the main risk (phantom gains from a missing or extra digit) is closed

A digit change is now counted only if `|Δ| ≤ 0.1·max(old, new)` (`maple_exp.py:1978-1979`). A single OCR insertion or deletion can never land inside that band:

- **Extra digit.** For N → M with one more digit, N ≥ 0.9M ≥ 0.9·10^d forces N to start with 9. M < N/0.9 < 1.112·10^d forces M to start with 1. So the 1 must be inserted in front, giving M = 19… ≥ 1.9·10^d. That is a contradiction. In practice this case falls into `jump`, or into `tenfold` (8 identical readings).
- **Missing digit.** M ≥ 0.9N forces M to start with 9. N < 1.112·10^(d−1) forces N to start with 10 or 11. Deleting a digit leaves M starting with 1, or with 0, which collapses to even fewer digits. Again a contradiction.
- **Substitution** can't change the digit count.

So a counted rollover needs at least two simultaneous OCR errors, read identically three times in a row. That is a stricter gate than ordinary within-digit changes, which need 2 agreeing readings (`n`), not identical ones.

## Other checks

| Area | Result |
|---|---|
| **Ordinary tracking** | Unchanged. When `digits_changed` is False, the `jump` threshold and the `old is None or jump or digits_changed` gate reduce exactly to the old code. |
| **Symmetry** | `digits_changed`, `top` and `jump` are symmetric in (old, new). A confirmed misread and its correction are classified the same way and cancel. |
| **Fluctuating readings** | `same` resets on any differing reading, including a reading equal to the confirmed value (line 1967). Alternating 10010/10020 never confirms. |
| **Low balances** | 0→1 and 0→10 rebase (Δ > 0.5 and Δ > 1 respectively). 9→10 counts +1. 8→10 and 89→100 rebase. All correct. |
| **Tenfold** | Any ratio in 9–11 (or its inverse) has Δ well over 10%, so it is always a `jump` and still needs 8 identical readings. |
| **Pause/resume** | A rollover confirmed while `running=False` updates `value` without adding to `gained`. On resume, `counting` restarts without counting a gain. |
| **Reversals** | Spending across a boundary (10,050 → 9,950) is now subtracted. Before, it was rebased. This matches how within-digit spending is already handled, so it is intended. |
| **Tests** | I traced each new case by hand against the code, including `per_hour`/`per_5min` at t=60 assuming `ROLLING_WINDOW=300`. Expected values match. |

## Preexisting, not a regression (Low): path dependence after a stable misread

Input sequence, starting from A = 9980 confirmed while running:
1. 10020 ×3: counted +40. This needs a stable two-error misread, which is implausible.
2. The player spends; the real value is 5000. Reading 5000 ×3 from 10020: `digits_changed`, Δ = 5020 > 1002, so it's a `jump` and is rebased.

- Reported gain: +40. True gain: −4980.
- Old code: both steps rebased, so it reported 0. The error was +4980 then and is +5020 now.

The new code can only differ from the old on paths that already contain an old-code rebase. Its extra error is bounded by the small rollover delta (≤10% of balance). The same mechanism already exists for ordinary within-digit misreads followed by a jump, and for a misread confirmed while running but corrected while paused.

Minimal repair if wanted: none within this PR's scope. Fixing it means remembering the pre-misread baseline across jumps, which is a policy change.

## Non-blocking notes

1. **Test gap, exact boundary.** 9000→10000 (Δ exactly 10% of top) counts as a gain because the check is `>`. No test pins this. The 8999→10000 test only covers the rebase side. Consider adding `(9000, 10000, 1000)` to the rollover tuple.
2. **Lag near a boundary.** If mesos keep arriving while the reading sits just past a boundary, no value repeats 3 times, so confirmation waits. If the total drifts beyond 10% (for example 9,980 → over 11,089) before 3 identical readings occur, the step rebases and that legitimate delta is lost. The old code always lost it, so this is not a regression.
3. **Large balances.** Near 1B, a counted rollover can be up to about 100M. The ordinary path already counts up to 50% with 2 agreeing readings, so this adds no new exposure.
4. **Evidence limits.** The 137 logic and 15 screen checks exercise synthetic sequences only. With the Tesseract class checks unavailable, nothing here shows how often real game OCR produces stable two-edit misreads across a digit boundary. The argument above shows that single-edit misreads can't produce a counted rollover. It doesn't measure real-world rates.