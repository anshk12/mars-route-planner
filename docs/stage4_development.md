# Stage 4: comparing planned routes with real drives

Written by Claude. **These are development set results only.** The test set hasn't been evaluated.

## How the test is set up

- **Locked test set:** 13 of the route's 43 blocks, covering 13.2 of 46.0 km, are held back in
  [stage4_split.json](stage4_split.json). The split was committed before any Stage 4 comparison ran.
- **Limit of the lock:** the 25 degree limit was chosen using the whole route, including these blocks, so the test set
  is only fully independent for choices made from now on, such as preference weights.
- **Baseline:** a planned route only earns credit by beating a straight line between the same two points.
- **Distance measure:** the average distance between a route and the real path, measured from both sides.
- **Uncertainty:** ranges resample whole blocks, because segments in one block aren't independent.
- **Skipped:** 25 of 248 segments have an endpoint with no drivable ground within 10 m.

## Does the planner beat a straight line?

"Gain" is how much closer to the real path a route gets, compared with a straight line on the same segment, and positive
means closer. Routes here are smoothed.

| Segments | Count | Straight line distance | Balanced gain (95% range) | Cautious gain (95% range) |
|---|---|---|---|---|
| Cut at long pauses | 73 | 13.8 m | +0.2 m (-0.5 to +1.1) | +1.0 m (-0.3 to +2.3) |
| Cut every 200 m | 150 | 8.9 m | -0.1 m (-0.4 to +0.2) | -0.5 m (-1.2 to +0.4) |
| Long pauses, straight line blocked | 7 | 28.9 m | -1.0 m (-12.2 to +13.7) | -0.2 m (-12.9 to +43.7) |
| 200 m, straight line blocked | 11 | 10.0 m | -2.9 m (-4.9 to +2.2) | +1.7 m (-5.8 to +3.3) |
| Long pauses, fairly direct drives | 33 | 10.8 m | +0.4 m (-1.5 to +1.1) | +1.4 m (-2.0 to +2.3) |
| 200 m, fairly direct drives | 80 | 6.4 m | -0.3 m (-1.4 to +0.8) | -1.0 m (-2.4 to -0.1) |

**With these provisional weights, the planner doesn't clearly beat a straight line in any of these groups.** Most ranges
include zero, and the one that doesn't favors the straight line. Only 18 segments have a straight line that hits blocked
ground, too few to judge that group.

## Do real routes avoid what the model measures?

| Segments | Measure | Real route | Straight line | Real is lower in | Median difference (95% range) |
|---|---|---|---|---|---|
| Long pauses | Hazard penalty | 0.120 | 0.158 | 66% | -0.017 (-0.037 to -0.003) |
| Long pauses | Energy vs. flat | 1.083 | 1.133 | 67% | -0.026 (-0.039 to -0.013) |
| Every 200 m | Hazard penalty | 0.102 | 0.121 | 56% | -0.006 (-0.017 to +0.001) |
| Every 200 m | Energy vs. flat | 1.069 | 1.088 | 63% | -0.010 (-0.021 to -0.002) |

Real routes tend to score slightly lower than straight lines, so drivers may partly avoid what the model measures. The
differences are small, and they're clearer on the longer pause segments. This doesn't show why drivers chose those
routes, and several comparisons were checked, so one or two could be chance.

## Problems found and fixed along the way

- **Comparison bug:** the first run's segment labels weren't unique, so 200 m segments on the same sol got mixed up in
  paired comparisons. Every segment now has its own ID, and the first run's numbers were discarded.
- **Route shape:** cell width changes very slightly with latitude. The search exploited this, so "shortest" routes took
  odd shapes and landed farther from the real path than straight lines of equal length. Smoothing now allows up to 0.2%
  extra cost to remove this effect. "Shortest" now ties a straight line on nearly every segment, as it should.

## What this does and doesn't mean

- **Not a final answer:** these weights were guessed. Stage 5 estimates them from real drives, and could improve the match.
- **The straight line is hard to beat:** many segments have little terrain to avoid, so there's little for a planner to do.
- **Unmeasured reasons:** science targets, small rocks, wheel wear, recent images and rover software could all shape real
  routes, and none of them are in this model. A close match may not be reachable from orbit alone.
- **Position error:** the real path has its own uncertainty, which limits how small the distance can get.
