"""Check the hazard rules and energy physics on cases with known answers."""

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from mars_rover.rover.energy import RoverPhysics, along_track_grade_deg, energy_per_meter
from mars_rover.rover.hazards import HazardClass, HazardLimits, classify, process_hazards


def flat_layers(size=9):
    """Return perfectly safe slope, step and roughness layers."""
    return np.zeros((size, size)), np.zeros((size, size)), np.zeros((size, size))


def test_gentle_ground_is_safe():
    assert (classify(*flat_layers()) == HazardClass.SAFE)[1:-1, 1:-1].all()


def test_map_edge_is_unknown():
    result = classify(*flat_layers())
    assert (result[0] == HazardClass.UNKNOWN).all()
    assert (result[:, -1] == HazardClass.UNKNOWN).all()


def test_steep_cell_blocks_the_whole_rover_footprint():
    slope, step, rough = flat_layers()
    slope[4, 4] = 25.1
    result = classify(slope, step, rough)
    assert (result[3:6, 3:6] == HazardClass.OFF_LIMITS).all()
    assert result[4, 2] == HazardClass.SAFE
    assert result[2, 4] == HazardClass.SAFE


def test_exactly_at_the_limit_is_allowed():
    slope, step, rough = flat_layers()
    slope[4, 4] = 25.0
    assert classify(slope, step, rough)[4, 4] == HazardClass.SAFE


def test_big_ledge_is_risky_but_never_off_limits():
    """The author's decision: ledges are a penalty, because the map could be wrong."""
    slope, step, rough = flat_layers()
    step[4, 4] = 1.30
    result = classify(slope, step, rough)
    assert result[4, 4] == HazardClass.RISKY
    assert not (result == HazardClass.OFF_LIMITS).any()


def test_rough_ground_is_risky():
    slope, step, rough = flat_layers()
    rough[4, 4] = 0.31
    assert classify(slope, step, rough)[4, 4] == HazardClass.RISKY


@pytest.mark.parametrize("layer_index", [0, 1, 2])
def test_missing_data_in_any_layer_is_unknown(layer_index):
    layers = list(flat_layers())
    layers[layer_index][4, 4] = np.nan
    result = classify(*layers)
    assert (result[3:6, 3:6] == HazardClass.UNKNOWN).all()
    assert result[4, 2] == HazardClass.SAFE


def test_known_steep_ground_beats_a_nearby_gap():
    slope, step, rough = flat_layers()
    slope[4, 4] = 40.0
    slope[4, 5] = np.nan
    assert classify(slope, step, rough)[4, 4] == HazardClass.OFF_LIMITS


def test_footprint_must_be_odd():
    with pytest.raises(ValueError):
        HazardLimits(footprint_cells=2)


def test_limits_can_be_changed_for_experiments():
    slope, step, rough = flat_layers()
    slope[4, 4] = 22.0
    assert classify(slope, step, rough)[4, 4] == HazardClass.SAFE
    strict = HazardLimits(slope_off_limits_deg=20.0)
    assert classify(slope, step, rough, strict)[4, 4] == HazardClass.OFF_LIMITS


@pytest.mark.parametrize(
    "heading, expected",
    [
        (90.0, -10.0),  # Heading the way the ground faces is downhill.
        (270.0, 10.0),  # The opposite heading is uphill.
        (0.0, 0.0),  # Across the slope is level.
        (180.0, 0.0),
    ],
)
def test_grade_depends_on_direction(heading, expected):
    assert along_track_grade_deg(10.0, 90.0, heading) == pytest.approx(expected, abs=1e-9)


def test_flat_ground_with_no_aspect_has_no_grade():
    assert along_track_grade_deg(0.0, np.nan, 45.0) == 0.0


def test_flat_energy_matches_hand_calculation():
    physics = RoverPhysics(mass_kg=1000, gravity_m_s2=4, rolling_resistance=0.1, drivetrain_efficiency=0.5)
    # 1000 kg x 4 m/s^2 x 0.1 / 0.5 = 800 joules per meter.
    assert energy_per_meter(0.0, np.nan, 0.0, physics) == pytest.approx(800.0)


def test_uphill_costs_more_than_flat_and_downhill_less():
    uphill = energy_per_meter(5.0, 90.0, 270.0)
    flat = energy_per_meter(0.0, np.nan, 270.0)
    downhill = energy_per_meter(5.0, 90.0, 90.0)
    assert uphill > flat > downhill


def test_steep_downhill_needs_no_energy_and_recovers_none():
    assert energy_per_meter(25.0, 90.0, 90.0) == 0.0


def test_missing_slope_stays_missing():
    assert np.isnan(energy_per_meter(np.nan, 90.0, 0.0))


def write_layer(path, data):
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=data.shape[0],
        width=data.shape[1],
        count=1,
        dtype="float32",
        crs="+proj=eqc +lat_ts=0 +lat_0=0 +lon_0=0 +R=3396190 +units=m +no_defs",
        transform=from_origin(0, 0, 1.0, 1.0),
        nodata=float("nan"),
    ) as dst:
        dst.write(data.astype("float32"), 1)


@pytest.mark.parametrize("tile_size", [16, 37])
def test_tiled_hazard_map_has_no_seams(tmp_path, tile_size):
    rng = np.random.default_rng(3)
    slope = rng.uniform(0, 30, (120, 150))
    step = rng.uniform(0, 0.8, (120, 150))
    rough = rng.uniform(0, 0.4, (120, 150))
    slope[40:44, 60:70] = np.nan
    write_layer(tmp_path / "slope_deg.tif", slope)
    write_layer(tmp_path / "step_m.tif", step)
    write_layer(tmp_path / "roughness_m.tif", rough)

    path = process_hazards(tmp_path, tile_size=tile_size)
    with rasterio.open(path) as dataset:
        tiled = dataset.read(1)
    whole = classify(slope.astype("float32"), step.astype("float32"), rough.astype("float32"))
    np.testing.assert_array_equal(tiled, whole)
