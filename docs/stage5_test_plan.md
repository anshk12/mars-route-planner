# Final test plan

Committed before the test ran. The test set is the 13 locked blocks in [stage4_split.json](stage4_split.json), which no
earlier comparison or weight search has used. The rules below were proposed by Claude and approved by me on 2026-11-08.

## Main question

On the locked blocks, cut into **long-pause segments** in the same way as the development set, do routes planned with
**distance 1, energy 1 and hazard 30**, then smoothed, get closer to Perseverance's real path than a straight line between
the same two points?

- **Measure:** gain = straight line distance from the real path - planned route distance from the real path, using the
  same two-sided average distance as Stage 4.
- **Counts as a gain if:** the 95% range of the median gain, from 5,000 resamples of whole blocks with seed 2020, is
  entirely above zero.
- **Counts as no clear gain if:** the range includes zero. With only 13 blocks, this wouldn't show that there's no effect.
- **Counts as worse than a straight line if:** the range is entirely below zero.

## Also reported, whatever the outcome

- The same weights on **200 m segments**.
- The number of segments and blocks, and how many were skipped because an endpoint had no drivable ground nearby.
- Segments where a straight line crosses blocked ground, described only, since there are too few for a reliable range.

## Rules

- **One run:** the script refuses to run again once a result exists.
- **No changes:** weights, limits, segment rules and code stay as committed here.
- **Full reporting:** every result above is reported, including if the main result shows no gain.
- **Known limit:** the 25 degree slope limit was chosen before the split using the whole route, including these blocks.
