"""Find rover routes with A* search, using a coarse pass to speed up long routes.

Each move's cost is its ground length multiplied by a cost per meter:

    distance weight
    + energy weight x (energy to drive this way / energy on flat ground)
    + hazard weight x (penalty for ledges and roughness)

Off-limits and unknown cells can't be entered at all, and the rover can't cut diagonally past them. Ledges only add
cost, following the decision in docs/decisions.md. The weights are provisional: Stage 5 estimates them from real drives.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field, replace

import numpy as np
from numpy.typing import NDArray
from scipy import ndimage

from mars_rover.rover.energy import RoverPhysics
from mars_rover.rover.hazards import HazardClass, HazardLimits

_MOVES = ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1))
SMOOTHING_ALLOWANCE = 0.002  # A straightened section can cost up to 0.2% more.


@dataclass(frozen=True)
class RoutePreferences:
    """How much the planner values each thing. Costs are in equivalent meters."""

    distance: float = 1.0
    energy: float = 1.0
    hazard: float = 1.0

    def __post_init__(self) -> None:
        if min(self.distance, self.energy, self.hazard) < 0:
            raise ValueError("preference weights can't be negative")
        if self.distance + self.energy + self.hazard == 0:
            raise ValueError("at least one preference weight must be positive")


SHORTEST = RoutePreferences(distance=1.0, energy=0.0, hazard=0.0)
BALANCED = RoutePreferences(distance=1.0, energy=1.0, hazard=1.0)
CAUTIOUS = RoutePreferences(distance=1.0, energy=0.5, hazard=5.0)


@dataclass(frozen=True)
class PlanningGrid:
    """Everything a search needs to know about one area of terrain."""

    passable: NDArray[np.bool_]
    dz_east: NDArray[np.float32]  # Height gained per meter driven east.
    dz_north: NDArray[np.float32]  # Height gained per meter driven north.
    penalty: NDArray[np.float32]  # Hazard cost per meter of ledges and roughness.
    dx_m: NDArray[np.float64]  # True ground width of a cell, per row.
    dy_m: float
    rolling_resistance: float = RoverPhysics().rolling_resistance
    origin: tuple[int, int] = (0, 0)  # This grid's top-left cell on the full map.

    @property
    def shape(self) -> tuple[int, int]:
        return self.passable.shape


@dataclass(frozen=True)
class Route:
    """The result of a search. ``cells`` is empty if no route exists."""

    found: bool
    cells: list[tuple[int, int]] = field(default_factory=list)
    cost: float = math.inf
    length_m: float = 0.0
    nodes_expanded: int = 0
    method: str = "fine"
    reason: str = ""


def build_grid(
    classes: NDArray,
    slope_deg: NDArray,
    aspect_deg: NDArray,
    worst_step_m: NDArray,
    worst_roughness_m: NDArray,
    dx_m: NDArray,
    dy_m: float,
    limits: HazardLimits = HazardLimits(),
    physics: RoverPhysics = RoverPhysics(),
    origin: tuple[int, int] = (0, 0),
) -> PlanningGrid:
    """Combine terrain layers into a grid the search can use."""
    passable = (classes == HazardClass.SAFE) | (classes == HazardClass.RISKY)
    tan_slope = np.tan(np.radians(np.nan_to_num(slope_deg, nan=0.0)))
    aspect = np.radians(np.nan_to_num(aspect_deg, nan=0.0))
    # Aspect faces downhill, so driving in that direction loses height.
    dz_east = -tan_slope * np.sin(aspect)
    dz_north = -tan_slope * np.cos(aspect)

    step = np.nan_to_num(np.clip(worst_step_m, 0.0, None), nan=0.0, posinf=0.0, neginf=0.0)
    rough = np.nan_to_num(np.clip(worst_roughness_m, 0.0, None), nan=0.0, posinf=0.0, neginf=0.0)
    # Squared, so hazards near a limit cost much more than small ones.
    penalty = (step / limits.step_risky_m) ** 2 + (rough / limits.roughness_risky_m) ** 2

    return PlanningGrid(
        passable=passable,
        dz_east=dz_east.astype(np.float32),
        dz_north=dz_north.astype(np.float32),
        penalty=penalty.astype(np.float32),
        dx_m=np.broadcast_to(np.asarray(dx_m, dtype=np.float64), (classes.shape[0],)).copy(),
        dy_m=float(dy_m),
        rolling_resistance=physics.rolling_resistance,
        origin=origin,
    )


def search_grid(
    grid: PlanningGrid,
    start: tuple[int, int],
    goal: tuple[int, int],
    prefs: RoutePreferences = BALANCED,
    allowed: NDArray[np.bool_] | None = None,
) -> Route:
    """Run A* between two cells, optionally limited to ``allowed`` cells.

    Moves are checked with plain Python for clarity, using memoryviews to keep lookups fast. The heuristic assumes every
    meter costs only the distance weight, which never overestimates, so results on a grid are optimal.
    """
    height, width = grid.shape
    for name, (row, col) in (("start", start), ("goal", goal)):
        if not (0 <= row < height and 0 <= col < width):
            return Route(False, reason=f"{name} is outside the search area")
        if not grid.passable[row, col]:
            return Route(False, reason=f"{name} is on off-limits or unknown ground")

    usable = grid.passable if allowed is None else grid.passable & allowed
    passable = memoryview(usable.astype(np.uint8).ravel())
    dz_east = memoryview(np.asarray(grid.dz_east, dtype=np.float32).ravel())
    dz_north = memoryview(np.asarray(grid.dz_north, dtype=np.float32).ravel())
    penalty = memoryview(np.asarray(grid.penalty, dtype=np.float32).ravel())
    dx_by_row = grid.dx_m.tolist()
    dy = grid.dy_m
    resistance = grid.rolling_resistance
    w_distance, w_energy, w_hazard = prefs.distance, prefs.energy, prefs.hazard

    best = np.full(height * width, np.inf, dtype=np.float64)
    best_view = memoryview(best)
    came_from = np.full(height * width, -1, dtype=np.int8)
    came_view = memoryview(came_from)
    closed = bytearray(height * width)

    goal_row, goal_col = goal
    goal_index = goal_row * width + goal_col
    start_index = start[0] * width + start[1]

    # The grid's narrowest cell gives a distance estimate that can't exceed the true distance.
    unit = w_distance * min(float(grid.dx_m.min()), dy)
    diagonal_extra = math.sqrt(2.0) - 2.0

    def estimate(row: int, col: int) -> float:
        d_row = abs(row - goal_row)
        d_col = abs(col - goal_col)
        return unit * (d_row + d_col + diagonal_extra * min(d_row, d_col))

    best_view[start_index] = 0.0
    heap = [(estimate(*start), 0.0, start_index)]
    expanded = 0

    while heap:
        _, cost_so_far, index = heapq.heappop(heap)
        if closed[index]:
            continue
        closed[index] = 1
        expanded += 1
        if index == goal_index:
            break

        row, col = divmod(index, width)
        east_here = dz_east[index]
        north_here = dz_north[index]
        penalty_here = penalty[index]
        for move, (d_row, d_col) in enumerate(_MOVES):
            next_row = row + d_row
            next_col = col + d_col
            if not (0 <= next_row < height and 0 <= next_col < width):
                continue
            next_index = next_row * width + next_col
            if closed[next_index] or not passable[next_index]:
                continue
            # A diagonal move can't squeeze between blocked corner cells.
            if d_row and d_col and not (
                passable[row * width + next_col] and passable[next_row * width + col]
            ):
                continue

            east_m = d_col * dx_by_row[row]
            north_m = -d_row * dy
            length = math.hypot(east_m, north_m)
            per_meter = w_distance
            if w_energy:
                rise = (east_here * east_m + north_here * north_m) / length
                cos_grade = 1.0 / math.sqrt(1.0 + rise * rise)
                force = resistance * cos_grade + rise * cos_grade
                if force > 0.0:
                    per_meter += w_energy * force / resistance
            if w_hazard:
                per_meter += w_hazard * 0.5 * (penalty_here + penalty[next_index])

            new_cost = cost_so_far + per_meter * length
            if new_cost < best_view[next_index]:
                best_view[next_index] = new_cost
                came_view[next_index] = move
                heapq.heappush(heap, (new_cost + estimate(next_row, next_col), new_cost, next_index))

    if not closed[goal_index]:
        return Route(False, nodes_expanded=expanded, reason="no connected route exists")

    cells = [(goal_row, goal_col)]
    index = goal_index
    length_m = 0.0
    while index != start_index:
        d_row, d_col = _MOVES[came_view[index]]
        row, col = divmod(index, width)
        previous = (row - d_row, col - d_col)
        length_m += math.hypot(d_col * dx_by_row[previous[0]], d_row * dy)
        cells.append(previous)
        index = previous[0] * width + previous[1]
    cells.reverse()
    return Route(True, cells, float(best_view[goal_index]), length_m, expanded)


def _coarsen(grid: PlanningGrid, factor: int, start: tuple[int, int], goal: tuple[int, int]) -> PlanningGrid:
    """Shrink a grid by averaging blocks of cells. Mostly blocked blocks can't be entered."""
    height, width = grid.shape
    new_height = height // factor + (height % factor > 0)
    new_width = width // factor + (width % factor > 0)
    labels = (
        (np.arange(height)[:, None] // factor) * new_width + np.arange(width)[None, :] // factor
    ).ravel()

    def block_mean(layer: NDArray) -> NDArray:
        total = np.bincount(labels, weights=layer.ravel().astype(np.float64), minlength=new_height * new_width)
        count = np.bincount(labels, minlength=new_height * new_width)
        return (total / np.maximum(count, 1)).reshape(new_height, new_width)

    share_open = block_mean(grid.passable)
    passable = share_open >= 0.5
    passable[start[0] // factor, start[1] // factor] = True
    passable[goal[0] // factor, goal[1] // factor] = True
    return replace(
        grid,
        passable=passable,
        dz_east=block_mean(grid.dz_east).astype(np.float32),
        dz_north=block_mean(grid.dz_north).astype(np.float32),
        penalty=block_mean(grid.penalty).astype(np.float32),
        dx_m=np.add.reduceat(grid.dx_m, np.arange(0, height, factor)) / np.diff(
            np.append(np.arange(0, height, factor), height)
        ) * factor,
        dy_m=grid.dy_m * factor,
    )


def plan_route(
    grid: PlanningGrid,
    start: tuple[int, int],
    goal: tuple[int, int],
    prefs: RoutePreferences = BALANCED,
    coarse_factor: int = 8,
    corridor_m: int = 120,
    direct_search_cells: int = 1_000_000,
) -> Route:
    """Plan a route, using a coarse pass first when the area is large.

    The coarse route marks a corridor, and the fine search stays inside it. This is much faster on long routes, but
    the result can be a little worse than the best possible route. If the corridor fails, the corridor gets wider,
    and finally the search uses the whole grid, so a route is only reported missing after a full search.
    """
    height, width = grid.shape
    if height * width <= direct_search_cells:
        return search_grid(grid, start, goal, prefs)
    for name, (row, col) in (("start", start), ("goal", goal)):
        if not (0 <= row < height and 0 <= col < width):
            return Route(False, reason=f"{name} is outside the search area")
        if not grid.passable[row, col]:
            return Route(False, reason=f"{name} is on off-limits or unknown ground")

    coarse = _coarsen(grid, coarse_factor, start, goal)
    coarse_start = (start[0] // coarse_factor, start[1] // coarse_factor)
    coarse_goal = (goal[0] // coarse_factor, goal[1] // coarse_factor)
    outline = search_grid(coarse, coarse_start, coarse_goal, prefs)
    expanded = outline.nodes_expanded

    if outline.found:
        path_mask = np.zeros(coarse.shape, dtype=bool)
        rows, cols = zip(*outline.cells)
        path_mask[list(rows), list(cols)] = True
        for widening in (1, 3):
            radius = max(1, corridor_m * widening // (coarse_factor * max(1, round(grid.dy_m))))
            structure = ndimage.generate_binary_structure(2, 2)
            coarse_corridor = ndimage.binary_dilation(path_mask, structure=structure, iterations=radius)
            allowed = np.kron(coarse_corridor, np.ones((coarse_factor, coarse_factor), dtype=bool))[:height, :width]
            route = search_grid(grid, start, goal, prefs, allowed=allowed)
            expanded += route.nodes_expanded
            if route.found:
                return replace(route, nodes_expanded=expanded, method=f"corridor {corridor_m * widening} m")

    route = search_grid(grid, start, goal, prefs)
    return replace(route, nodes_expanded=expanded + route.nodes_expanded, method="full fallback")


def move_costs(grid: PlanningGrid, rows: NDArray, cols: NDArray, prefs: RoutePreferences) -> NDArray[np.float64]:
    """Return the cost of each move along a path, using the same rules as the search."""
    rows = np.asarray(rows, dtype=np.int64)
    cols = np.asarray(cols, dtype=np.int64)
    east = np.diff(cols) * grid.dx_m[rows[:-1]]
    north = -np.diff(rows) * grid.dy_m
    length = np.hypot(east, north)
    per_meter = np.full(length.shape, float(prefs.distance))
    if prefs.energy:
        safe_length = np.where(length > 0, length, 1.0)
        rise = (
            grid.dz_east[rows[:-1], cols[:-1]].astype(np.float64) * east
            + grid.dz_north[rows[:-1], cols[:-1]].astype(np.float64) * north
        ) / safe_length
        cos_grade = 1.0 / np.sqrt(1.0 + rise**2)
        force = np.clip(grid.rolling_resistance * cos_grade + rise * cos_grade, 0.0, None)
        per_meter += prefs.energy * force / grid.rolling_resistance
    if prefs.hazard:
        penalty = grid.penalty.astype(np.float64)
        per_meter += prefs.hazard * 0.5 * (penalty[rows[:-1], cols[:-1]] + penalty[rows[1:], cols[1:]])
    return per_meter * length


def _line_cells(start: tuple[int, int], goal: tuple[int, int]) -> tuple[NDArray, NDArray]:
    steps = max(abs(goal[0] - start[0]), abs(goal[1] - start[1]), 1) + 1
    rows = np.rint(np.linspace(start[0], goal[0], steps)).astype(np.int64)
    cols = np.rint(np.linspace(start[1], goal[1], steps)).astype(np.int64)
    return rows, cols


def _line_is_clear(grid: PlanningGrid, rows: NDArray, cols: NDArray) -> bool:
    """Check a line of cells follows the search's rules, including no squeezing past corners."""
    if not grid.passable[rows, cols].all():
        return False
    diagonal = (rows[:-1] != rows[1:]) & (cols[:-1] != cols[1:])
    if diagonal.any():
        side_a = grid.passable[rows[:-1][diagonal], cols[1:][diagonal]]
        side_b = grid.passable[rows[1:][diagonal], cols[:-1][diagonal]]
        return bool(side_a.all() and side_b.all())
    return True


def smooth_route(grid: PlanningGrid, route: Route, prefs: RoutePreferences) -> Route:
    """Straighten a grid route wherever a direct line is clear and doesn't cost more.

    Searching in 8 directions gives staircases, and it treats many different paths as equally short. Smoothing removes
    those side effects. It never crosses blocked ground, and it raises a route's cost by at most SMOOTHING_ALLOWANCE.
    """
    if not route.found or len(route.cells) < 3:
        return route
    rows = np.array([cell[0] for cell in route.cells], dtype=np.int64)
    cols = np.array([cell[1] for cell in route.cells], dtype=np.int64)
    running_cost = np.r_[0.0, np.cumsum(move_costs(grid, rows, cols, prefs))]

    out_rows, out_cols = [rows[:1]], [cols[:1]]
    anchor = 0
    last = rows.size - 1
    while anchor < last:
        reach = last - anchor
        accepted = None
        # Try the longest shortcut first, then halve it until one works.
        while reach > 1:
            target = anchor + reach
            line_rows, line_cols = _line_cells((int(rows[anchor]), int(cols[anchor])), (int(rows[target]), int(cols[target])))
            if _line_is_clear(grid, line_rows, line_cols):
                line_cost = float(move_costs(grid, line_rows, line_cols, prefs).sum())
                # Without a small allowance, tiny cell width differences would reject straight lines of nearly
                # equal cost, leaving routes shaped by an effect that doesn't matter to a rover.
                section_cost = running_cost[target] - running_cost[anchor]
                if line_cost <= section_cost * (1.0 + SMOOTHING_ALLOWANCE) + 1e-6:
                    accepted = (target, line_rows, line_cols)
                    break
            reach //= 2
        if accepted is None:
            out_rows.append(rows[anchor + 1 : anchor + 2])
            out_cols.append(cols[anchor + 1 : anchor + 2])
            anchor += 1
        else:
            target, line_rows, line_cols = accepted
            out_rows.append(line_rows[1:])
            out_cols.append(line_cols[1:])
            anchor = target

    new_rows = np.concatenate(out_rows)
    new_cols = np.concatenate(out_cols)
    costs = move_costs(grid, new_rows, new_cols, prefs)
    east = np.diff(new_cols) * grid.dx_m[new_rows[:-1]]
    north = -np.diff(new_rows) * grid.dy_m
    return replace(
        route,
        cells=[(int(r), int(c)) for r, c in zip(new_rows, new_cols)],
        cost=float(costs.sum()),
        length_m=float(np.hypot(east, north).sum()),
        method=f"{route.method}, smoothed",
    )


def nearest_passable(
    grid: PlanningGrid, cell: tuple[int, int], max_distance: int = 10
) -> tuple[int, int] | None:
    """Return the closest usable cell, for a start or goal that lands on blocked ground."""
    row, col = cell
    height, width = grid.shape
    top, bottom = max(row - max_distance, 0), min(row + max_distance + 1, height)
    left, right = max(col - max_distance, 0), min(col + max_distance + 1, width)
    rows, cols = np.nonzero(grid.passable[top:bottom, left:right])
    if rows.size == 0:
        return None
    rows, cols = rows + top, cols + left
    distance = np.hypot(rows - row, cols - col)
    nearest = int(np.argmin(distance))
    if distance[nearest] > max_distance:
        return None
    return int(rows[nearest]), int(cols[nearest])
