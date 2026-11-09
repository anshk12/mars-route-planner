# Final test result

The one-time test ran on 2026-11-08, following [the plan](stage5_test_plan.md) committed beforehand. The full output is
in [stage5_test_result.json](stage5_test_result.json).

## Main result: no clear gain

| | Long-pause segments (main) | 200 m segments (secondary) |
|---|---|---|
| Segments measured | 33 of 35, in 11 blocks | 64 of 67, in 11 blocks |
| Median gain over a straight line | +1.47 m | -0.13 m |
| 95% range | **-5.96 to +3.34 m** | -2.08 to +0.49 m |
| Closer than a straight line | 55% | 45% |
| Verdict under the plan | **No clear gain** | No clear gain |

On long-pause segments, the median gain of +1.47 m is close to the development set's +1.72 m. But the range includes zero
and stretches far below it, so under the rule set in advance, **the test doesn't show that the planner beats a straight
line.**

## Other details

- **Some segments go badly:** the median planned route is 17.4 m from the real path, compared with 12.4 m for straight lines.
  The median gain is positive, so the planner is slightly better on a typical segment, but much worse on some.
- **Hard cases:** only 5 long-pause and 4 200 m segments have a straight line crossing blocked ground, with median gains of
  +0.02 m and +0.21 m. These are too few to judge.
- **Skipped:** 2 long-pause and 3 200 m segments, where an endpoint had no drivable ground nearby.

## What this means

- **The planner hasn't shown it's better:** with these weights and only orbital terrain, routes don't reliably get closer to
  Perseverance's real path than straight lines do.
- **It isn't proof of no effect:** with 11 blocks, the test can only detect a fairly large gain. The typical gain looks similar
  to the development set's, but it's too uncertain to rely on.
- **Possible reasons, untested:** science targets, small rocks invisible from orbit, newer rover images and the rover's own
  navigation software all shape real routes. Errors in the map and in recorded positions limit how closely any route can match.
- **This answers the project's main question for now:** orbital terrain explains, at most, a small part of how these routes
  differ from straight lines.

## Rules followed

The weights, segments and rule for a gain weren't changed after seeing the result, and the test won't be rerun. The 25
degree slope limit was chosen before the split, using the whole route.
