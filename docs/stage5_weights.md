# Stage 5: estimating weights from real drives

Written by Claude. **These are development set results only.** The locked test set hasn't been evaluated.

## Method

From `python -m scripts.stage5_fit_weights`. 15 sets of weights planned routes on every development segment, with the
distance weight fixed at 1. A route's gain is how much closer it gets to the real path than a straight line does.

To avoid rewarding lucky weights, weights were chosen on most development blocks and scored on the remaining ones, with
each of 5 groups of blocks held out in turn. This is cross-validation.

## Results

| Segments | Count | Straight line distance | Cross-validated gain (95% range) | Closer than straight | Weights chosen |
|---|---|---|---|---|---|
| Cut at long pauses | 73 | 13.8 m | **+1.72 m (+0.30 to +2.78)** | 62% | Energy 1, hazard 30, in all 5 folds |
| Cut every 200 m | 150 | 8.9 m | -0.06 m (-0.55 to +0.30) | 46% | Mostly energy 3, hazard 1, varying by fold |

### Top weights on each kind of segment

| Weights | Long pauses | Every 200 m |
|---|---|---|
| Energy 1, hazard 30 | +1.72 m (+0.30 to +2.78) | -0.89 m (-2.03 to -0.21) |
| Energy 0, hazard 3 | +1.49 m (-0.05 to +2.38) | -0.28 m (-1.03 to +0.27) |
| Energy 0, hazard 1 | +1.37 m (+0.01 to +2.21) | -0.15 m (-0.60 to +0.47) |
| Energy 3, hazard 1 | +0.29 m (-0.35 to +1.12) | +0.10 m (-0.25 to +0.55) |
| Energy 1, hazard 1 (the original guess) | +0.23 m (-0.15 to +1.13) | -0.05 m (-0.43 to +0.20) |

## What this shows

- **Long-pause segments show a small gain:** routes get about 1.7 m closer than straight lines, on a baseline of 13.8 m.
  That's about 12%, and most of the difference between the real path and a straight line remains unexplained.
- **200 m segments show no gain.**
- **The kinds of segments disagree:** the best weights for long pauses are among the worst for 200 m segments. One possible
  reason is that a longer segment gives terrain more room to affect the route, but this hasn't been tested.
- **Avoiding hazards seems to matter more than saving energy:** on long-pause segments, weights with hazard above 0 lead the
  list, and adding energy alone doesn't help. This is suggestive, because weights this similar are hard to tell apart.

## Limits

- **Picking segments after seeing results:** long-pause segments look better, but choosing them now and reporting only them
  would overstate the evidence. The final test has to be fixed before it runs.
- **The landscape is noisy:** energy 1, hazard 10 gains +0.43 m while energy 1, hazard 30 gains +1.72 m. Some of the winner's
  lead may be luck, and cross-validation can't fully remove that when the same weights win in every fold.
- **Edge of the search:** hazard 30 is the highest value tried. Results near it don't rise steadily, so searching higher
  could just chase noise.
- **Few hard cases:** only 7 long-pause and 11 200 m segments have a blocked straight line, too few for reliable ranges.
- **25 of 248 segments were skipped** because an endpoint had no drivable ground nearby.
- **A gain doesn't explain why:** routes matching better doesn't show that drivers chose them for these reasons.

## The final test

The locked test blocks can only give one fair answer. Before running it, the following should be committed:

- **Main question:** do routes on long-pause segments, using energy 1 and hazard 30, get closer to the real path than a
  straight line?
- **What counts as a gain:** the 95% range of the median gain, resampling whole blocks, is entirely above zero.
- **Secondary result:** the same weights on 200 m segments, reported whatever the outcome.

The test set is small, with 13 blocks, so an inconclusive result wouldn't prove there's no effect.
