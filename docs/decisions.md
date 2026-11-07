# Research decisions

Sections marked "My words" are copied exactly as I wrote them. The evidence behind them is in
[stage2_evidence.md](stage2_evidence.md).

## Decision 1: hazard limits (2026-11-06)

*My words:*

> Slope: I'm choosing 25° because it provides a safe middle ground above any tilt recorded at a stop while avoiding excessive blockage of the real route. Ledges: Ledges should be a penalty because a hard limit would incorrectly block ground due to map errors or small bumps the rover can navigate around.

**How the model applies this:**

- Ground is off-limits if the steepest map slope in a rover-sized 3 x 3 m square is above 25 degrees.
- Ledges never make ground off-limits. A large one marks the ground as risky, so the planner can avoid it when
  it's worth doing.

**Still open:** this treats map slope as a stand-in for rover tilt, and that assumption hasn't been justified yet.
The real drives should help test it.

## Decision 2: keeping 25 degrees after investigating real drives (2026-11-06)

*My words:*

> Map slope overstating rover tilt is the strongest explanation because recorded tilt at the stops (9–16°) ran well below the map's 28.6° estimate, showing that the map's 3x3 window captures sharp local variations that the rover's suspension smooths out. This doesn't change my 25° decision; I'm keeping it because map slope naturally runs higher than actual rover tilt, making 25° a reliable, conservative buffer for the planner.

**Update (noted by Claude):** a later test didn't support this reasoning as written. The best match to tilt came from
a window of about 15 m, not a rover-sized one, and map slope doesn't run far above tilt on average. See "Testing the
suspension idea" in [stage2_evidence.md](stage2_evidence.md). I still need to review this decision. 25 degrees also
hasn't been checked on drives held back from choosing it.

## Decision 3: new reasoning for 25 degrees (2026-11-06)

*My words:*

> The 15 m window likely matches better because broader averaging smooths out local DEM noise, interpolation artifacts, and minor rover position tracking errors, capturing the broader regional topography rather than isolated rocky bumps. I will keep the 25° limit on the 3×3 m footprint for now, updating the reasoning: rather than assuming map slope runs higher on average, the 3×3 m window serves as a conservative peak-risk detector that catches sharp, localized hazards (like the outcrop edges), while holding off on any major structural retuning until the fair test in Stages 4–5.

**Still to test (noted by Claude):** a 3 x 3 m window that responds to sharp hazards also responds to map noise and
flaws, so some of what it catches may be false alarms. The explanation for the 15 m result is plausible but
untested.

## Provisional settings chosen by Claude

These aren't my decisions yet. They're needed to run the model and should be reviewed.

| Setting | Value | Reason |
|---|---|---|
| Footprint | 3 x 3 m | About the size of the rover, 3 m long and 2.7 m wide |
| Ledge counts as risky above | 0.525 m | NASA's wheel-size obstacle rule of thumb |
| Roughness counts as risky above | 0.3 m | No published limit found. About 1 in 100 nearby cells exceed it |
| Missing data | Unknown, not drivable | Missing data must never look safe |
| Rolling resistance | 0.15 | Assumed. No published Perseverance value found |
| Drivetrain efficiency | 50% | Assumed. No published Perseverance value found |
| Energy recovered going downhill | None | Assumed |
