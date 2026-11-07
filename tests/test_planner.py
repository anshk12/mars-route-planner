"""Check the route planner on terrain where the right answer is known."""

import math

import numpy as np
import pytest

from mars_rover.planner.search import (
    BALANCED,
    CAUTIOUS,
    SHORTEST,
    PlanningGrid,
    RoutePreferences,
    build_grid,
    nearest_passable,
    plan_route,
    search_grid,
)
from mars_rover.rover.energy import RoverPhysics, energy_per_meter
from mars_rover.rover.hazards import HazardClass


def flat_grid(height=40, width=40, dx=1.0, dy=1.0):
    return PlanningGrid(
        passable=np.ones((height, width), dtype=bool),
        dz_east=np.zeros((height, width), dtype=np.float32),
        dz_north=np.zeros((height, width), dtype=np.float32),
        penalty=np.zeros((height, width), dtype=np.float32),
        dx_m=np.full(height, dx, dtype=np.float64),
        dy_m=dy,
    )


def assert_moves_are_connected(cells):
    for (r1, c1), (r2, c2) in zip(cells, cells[1:]):
        assert max(abs(r1 - r2), abs(c1 - c2)) == 1


def test_straight_line_on_flat_ground():
    route = search_grid(flat_grid(), (5, 2), (5, 22), SHORTEST)
    assert route.found
    assert route.length_m == pytest.approx(20.0)
    assert all(row == 5 for row, _ in route.cells)
    assert_moves_are_connected(route.cells)


def test_diagonal_length_is_correct():
    route = search_grid(flat_grid(), (0, 0), (10, 10), SHORTEST)
    assert route.length_m == pytest.approx(10 * math.sqrt(2))


def test_narrow_cells_change_the_measured_length():
    route = search_grid(flat_grid(dx=0.95), (5, 0), (5, 20), SHORTEST)
    assert route.length_m == pytest.approx(19.0)


def test_route_goes_around_a_wall_through_its_gap():
    grid = flat_grid(30, 30)
    grid.passable[:, 15] = False
    grid.passable[25, 15] = True
    route = search_grid(grid, (5, 5), (5, 25), SHORTEST)
    assert route.found
    assert (25, 15) in route.cells


def test_off_limits_cells_are_never_entered():
    grid = flat_grid(30, 30)
    grid.passable[10:20, 10:20] = False
    route = search_grid(grid, (15, 2), (15, 28), BALANCED)
    assert route.found
    assert all(grid.passable[cell] for cell in route.cells)


def test_full_wall_reports_no_route():
    grid = flat_grid(20, 20)
    grid.passable[:, 10] = False
    route = search_grid(grid, (5, 2), (5, 18))
    assert not route.found
    assert route.cells == []
    assert "no connected route" in route.reason


def test_blocked_start_explains_the_problem():
    grid = flat_grid()
    grid.passable[3, 3] = False
    route = search_grid(grid, (3, 3), (10, 10))
    assert not route.found
    assert "start" in route.reason


def test_outside_goal_explains_the_problem():
    route = search_grid(flat_grid(), (3, 3), (100, 10))
    assert not route.found
    assert "goal" in route.reason


def test_no_cutting_diagonally_past_blocked_corners():
    grid = flat_grid(10, 10)
    grid.passable[4, 5] = False
    grid.passable[5, 4] = False
    route = search_grid(grid, (5, 5), (4, 4), SHORTEST)
    for (r1, c1), (r2, c2) in zip(route.cells, route.cells[1:]):
        if r1 != r2 and c1 != c2:
            assert grid.passable[r1, c2] and grid.passable[r2, c1]
    assert route.length_m > math.sqrt(2)


def test_hazard_weight_moves_the_route_around_ledges():
    grid = flat_grid(30, 30)
    grid.penalty[5:25, 15] = 20.0
    direct = search_grid(grid, (15, 2), (15, 28), SHORTEST)
    careful = search_grid(grid, (15, 2), (15, 28), CAUTIOUS)
    assert (15, 15) in direct.cells
    assert all(not (5 <= row < 25 and col == 15) for row, col in careful.cells)
    assert careful.length_m > direct.length_m


def test_ledges_can_still_be_crossed_when_there_is_no_other_way():
    """The author's decision: ledges are a penalty, never a wall."""
    grid = flat_grid(20, 20)
    grid.penalty[:, 10] = 100.0
    route = search_grid(grid, (10, 2), (10, 18), CAUTIOUS)
    assert route.found


def test_uphill_costs_more_than_downhill():
    grid = flat_grid(20, 40)
    grid.dz_east[:] = 0.1
    uphill = search_grid(grid, (10, 5), (10, 30), RoutePreferences(0, 1, 0))
    downhill = search_grid(grid, (10, 30), (10, 5), RoutePreferences(0, 1, 0))
    assert uphill.cost > downhill.cost


def test_energy_cost_agrees_with_the_energy_model():
    physics = RoverPhysics()
    slope = math.degrees(math.atan(0.1))
    grid = flat_grid(10, 30)
    grid.dz_east[:] = 0.1
    route = search_grid(grid, (5, 2), (5, 22), RoutePreferences(0, 1, 0))
    # Rising eastward means the ground faces west (270), and driving east is uphill (heading 90).
    energy = float(energy_per_meter(slope, 270.0, 90.0, physics))
    flat = float(energy_per_meter(0.0, np.nan, 90.0, physics))
    assert route.cost == pytest.approx(20.0 * energy / flat, rel=1e-5)


def test_coarse_and_fine_searches_find_similar_routes():
    rng = np.random.default_rng(5)
    size = 400
    grid = flat_grid(size, size)
    field = rng.normal(size=(size, size))
    from scipy import ndimage

    smooth = ndimage.gaussian_filter(field, 6)
    grid.passable[:] = smooth < np.percentile(smooth, 80)
    grid.penalty[:] = np.abs(ndimage.gaussian_filter(rng.normal(size=(size, size)), 3)) * 8
    # Start and end in the largest connected area, so a route is sure to exist.
    labels, _ = ndimage.label(grid.passable, structure=np.ones((3, 3), dtype=bool))
    largest = np.argmax(np.bincount(labels.ravel())[1:]) + 1
    rows, cols = np.nonzero(labels == largest)
    start_pick = np.argmin(rows + cols)
    goal_pick = np.argmax(rows + cols)
    start = (int(rows[start_pick]), int(cols[start_pick]))
    goal = (int(rows[goal_pick]), int(cols[goal_pick]))

    exact = search_grid(grid, start, goal, BALANCED)
    quick = plan_route(grid, start, goal, BALANCED, coarse_factor=8, corridor_m=60, direct_search_cells=1000)
    assert exact.found and quick.found
    assert exact.cost <= quick.cost * (1 + 1e-6)
    assert quick.cost <= exact.cost * 1.10
    assert quick.nodes_expanded < exact.nodes_expanded * 1.5
    assert all(grid.passable[cell] for cell in quick.cells)


def test_small_areas_skip_the_coarse_pass():
    route = plan_route(flat_grid(), (2, 2), (30, 30), SHORTEST)
    assert route.method == "fine"


def test_big_area_falls_back_when_coarse_route_is_blocked():
    """A one-cell gap vanishes when shrunk, so the search must still find it."""
    grid = flat_grid(200, 200)
    grid.passable[:, 100] = False
    grid.passable[150, 100] = True
    route = plan_route(grid, (20, 20), (20, 180), SHORTEST, direct_search_cells=1000)
    assert route.found
    assert (150, 100) in route.cells


def test_nearest_passable_finds_closest_cell():
    grid = flat_grid(20, 20)
    grid.passable[8:13, 8:13] = False
    assert nearest_passable(grid, (10, 12)) == (10, 13)
    grid.passable[:] = False
    assert nearest_passable(grid, (10, 12)) is None


def test_build_grid_converts_terrain_layers():
    classes = np.array(
        [[HazardClass.SAFE, HazardClass.RISKY, HazardClass.OFF_LIMITS, HazardClass.UNKNOWN]], dtype=np.uint8
    )
    # Facing west (270) means height is gained going east.
    slope = np.array([[math.degrees(math.atan(0.1)), 0.0, 40.0, np.nan]])
    aspect = np.array([[270.0, np.nan, 90.0, np.nan]])
    step = np.array([[0.525, 0.0, -np.inf, -np.inf]])
    rough = np.array([[0.0, 0.3, 0.0, np.nan]])
    grid = build_grid(classes, slope, aspect, step, rough, 1.0, 1.0)
    assert grid.passable.tolist() == [[True, True, False, False]]
    assert grid.dz_east[0, 0] == pytest.approx(0.1)
    assert grid.dz_north[0, 0] == pytest.approx(0.0, abs=1e-6)
    assert grid.penalty[0, 0] == pytest.approx(1.0)
    assert grid.penalty[0, 1] == pytest.approx(1.0)
    assert np.isfinite(grid.penalty).all()


@pytest.mark.parametrize("weights", [(-1, 0, 0), (0, 0, 0)])
def test_bad_preferences_are_rejected(weights):
    with pytest.raises(ValueError):
        RoutePreferences(*weights)
