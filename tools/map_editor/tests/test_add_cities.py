"""Bulk city import: gazetteer -> georef-projected, deduped, managed points."""

import add_cities
from add_cities import MANAGED_PREFIX
from geo import GeoRef

BBOX = (-11.0, 49.0, 3.0, 61.0)
SIZE = (5656, 8000)


def georef():
    return GeoRef(BBOX, SIZE)


def write_csv(path, rows):
    lines = ["city,lat,lng,population"]
    lines += [f"{n},{lat},{lon},{pop}" for n, lat, lon, pop in rows]
    path.write_text("\n".join(lines) + "\n")
    return path


def test_normalize_name_folds_punct_and_case():
    assert add_cities.normalize_name("Bury St. Edmunds") == "burystedmunds"
    assert add_cities.normalize_name("bury st edmunds") == "burystedmunds"


def test_tier_from_population_is_monotonic():
    tiers = [
        add_cities.tier_for_population(p) for p in (10_000, 60_000, 200_000, 3_000_000)
    ]
    assert tiers == sorted(tiers)
    assert tiers[0] == 1 and tiers[-1] == 5


def test_min_pop_floor_and_name_dedup(tmp_path):
    csv = write_csv(
        tmp_path / "c.csv",
        [
            ("Big", 52.0, -1.0, 100_000),
            ("Small", 52.5, -1.5, 5_000),  # below floor
            ("Big", 52.0, -1.0, 40_000),  # duplicate name, lower pop
        ],
    )
    cands = add_cities.read_candidates(csv, min_pop=30_000)
    assert [c.name for c in cands] == ["Big"]
    assert cands[0].pop == 100_000  # kept the most-populous 'Big'


def test_merge_adds_managed_ids_and_keeps_manual(tmp_path):
    points = {"p1": {"x": 10.0, "y": 10.0, "name": "OldTown", "tier": 3}}
    csv = write_csv(tmp_path / "c.csv", [("Newville", 52.0, -1.0, 100_000)])
    cands = add_cities.read_candidates(csv, min_pop=0)
    added, _ = add_cities.merge(points, cands, georef(), dedup_radius=10, top=None)
    assert added == ["Newville"]
    assert "p1" in points and points["p1"]["name"] == "OldTown"
    managed = [pid for pid in points if pid.startswith(MANAGED_PREFIX)]
    assert len(managed) == 1


def test_proximity_dedup_skips_near_existing(tmp_path):
    # A candidate landing right on top of a hand-placed city drops out,
    # even though the name differs (stylized existing names).
    g = georef()
    x, y = g.lonlat_to_pixel(-1.0, 52.0)
    points = {"p1": {"x": x, "y": y, "name": "Stylized", "tier": 2}}
    csv = write_csv(tmp_path / "c.csv", [("Realname", 52.0, -1.0, 100_000)])
    cands = add_cities.read_candidates(csv, min_pop=0)
    added, skipped = add_cities.merge(points, cands, g, dedup_radius=50, top=None)
    assert added == []
    assert any("px of" in reason for _, reason in skipped)


def test_reimport_is_idempotent(tmp_path):
    csv = write_csv(
        tmp_path / "c.csv",
        [("A", 52.0, -1.0, 100_000), ("B", 53.0, -2.0, 90_000)],
    )
    cands = lambda: add_cities.read_candidates(csv, min_pop=0)
    points = {"p1": {"x": 10.0, "y": 10.0, "name": "Manual", "tier": 1}}

    add_cities.merge(points, cands(), georef(), dedup_radius=5, top=None)
    first = {pid: dict(p) for pid, p in points.items()}
    add_cities.merge(points, cands(), georef(), dedup_radius=5, top=None)

    assert points == first  # same ids, same coords, manual untouched


def test_top_caps_by_population(tmp_path):
    csv = write_csv(
        tmp_path / "c.csv",
        [
            ("A", 52.0, -1.0, 100_000),
            ("B", 53.0, -2.0, 90_000),
            ("C", 54.0, -3.0, 80_000),
        ],
    )
    cands = add_cities.read_candidates(csv, min_pop=0)
    added, _ = add_cities.merge({}, cands, georef(), dedup_radius=5, top=2)
    assert added == ["A", "B"]  # most populous first
