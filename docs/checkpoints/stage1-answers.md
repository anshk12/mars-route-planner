# Stage 1 checkpoint: my answers

Sections marked "My words" are copied exactly as I wrote them, including typos. Anything else is labeled
as Claude's. This checkpoint is still in progress.

## My project description

*My words:*

> This project processes USGS elevation data from Jezero Crater to create terrain maps of slope, aspect, roughness, and step height, then investigates how accurately those measurements describe the terrain Perseverance actually drove across, without being intended for mission planning.

## Experiment: breaking the tile margin

**What I did:** I changed the margin in `process_terrain` to 0, ran the tests, and then changed it back.

**Result from the test output:** 4 tests failed and 29 passed. All 33 passed again after the line was restored.

| Failed test | Tile size | Cells wrong |
|---|---|---|
| `test_tiled_result_has_no_seams` | 16 | 22.5% |
| `test_tiled_result_has_no_seams` | 64 | 5.03% |
| `test_tiled_result_has_no_seams` | 97 | 3.57% |
| `test_region_edges_use_real_neighbors` | 32 | Tile edges came out as missing data |

### Why the wrong cells appear at boundaries, and why smaller tiles are worse

*My words:*

> the wrong cells appear because the tiles are 16 cells wide so their boundires occur every 16cells, the program incorrectly marks cells as missing. Tile-boundary problem rather than missing data. 2. smaller tiles create more boundaries across the same map. every time the program reaches a tile boundary the same error can happen again. smaller tiles create more oppetunties for incorrect cells

### Why the margin is 10 cells

*My words:*

> it calculates the elevation difference between nearby cells. However, required_margin() must account for all terrain calculations, not just slope.
>
> The margin is set to 10 because another calculation needs a larger neighborhood of surrounding cells.

> A margin of 10 means the calculation uses a 21-cell-wide window (10 cells on each side of the center cell). The calculation using that window needs the 10-cell margin because a cell at the tile edge needs 10 neighboring cells beyond the edge.

### Added by Claude

- **Why wrong cells come in pairs:** slope needs neighbors on both sides. At a boundary, the last cell of one
  tile and the first cell of the next are each missing a neighbor, so both become NaN.
- **Which window sets the margin:** `plane_window`, used by `step_height`, is 21 cells wide. Half of that,
  rounded down, gives 10, which is more than the other windows need.
- **Check of the failure count at tile size 16:** the test map is 317 x 230 cells, and the outer edge has no
  slope either way. There are 19 column boundaries and 14 row boundaries, each producing 2 wrong lines:
  (38 x 228) + (28 x 315) - (38 x 28) = 16,420 cells, which exactly matches the test output. This covers
  slope only, because the test stops at the first layer that fails.
