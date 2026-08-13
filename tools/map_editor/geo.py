"""Shared geo-calibration: the one lon/lat <-> pixel transform for a map.

A map package's *georef* is the single source of truth for how real-world
degrees map onto the pixel canvas. It lives in map.json under a "georef"
key holding a "projection" ("equirectangular") and a "bbox"
([lon_min, lat_min, lon_max, lat_max]).

Every geo-driven import reads it from there rather than re-passing a bbox on
the command line. The coastline (geo_import.py), the cities (add_cities.py),
and any later geo layer thus land on the same grid. Add a layer by reading
the georef here -- never by re-deriving the transform.

The projection is a plain equirectangular (plate carree) fit. The campaign
map isn't a survey product; it just needs points to land where you'd expect
against the coastline. lon is linear in x, lat is linear in y.
"""

from __future__ import annotations

from dataclasses import dataclass

BBox = tuple[float, float, float, float]  # lon_min, lat_min, lon_max, lat_max

SUPPORTED_PROJECTIONS = ("equirectangular",)


class GeoRefError(ValueError):
    """The manifest's georef block is missing or malformed."""


@dataclass(frozen=True)
class GeoRef:
    """A pixel canvas tied to a real-world lon/lat bounding box."""

    bbox: BBox
    size: tuple[int, int]  # pixel width, height
    projection: str = "equirectangular"

    def __post_init__(self) -> None:
        if self.projection not in SUPPORTED_PROJECTIONS:
            raise GeoRefError(
                f"unsupported projection {self.projection!r}; "
                f"supported: {', '.join(SUPPORTED_PROJECTIONS)}"
            )
        lon_min, lat_min, lon_max, lat_max = self.bbox
        if lon_max <= lon_min or lat_max <= lat_min:
            raise GeoRefError(
                f"bbox must be [lon_min, lat_min, lon_max, lat_max] with "
                f"max > min, got {self.bbox}"
            )
        w, h = self.size
        if w <= 0 or h <= 0:
            raise GeoRefError(f"size must be positive, got {self.size}")

    def lonlat_to_pixel(self, lon: float, lat: float) -> tuple[float, float]:
        """Real-world degrees -> pixel coords (image y grows downward)."""
        lon_min, lat_min, lon_max, lat_max = self.bbox
        w, h = self.size
        x = (lon - lon_min) / (lon_max - lon_min) * w
        y = (lat_max - lat) / (lat_max - lat_min) * h
        return x, y

    def pixel_to_lonlat(self, x: float, y: float) -> tuple[float, float]:
        """Pixel coords -> real-world degrees. Inverse of lonlat_to_pixel."""
        lon_min, lat_min, lon_max, lat_max = self.bbox
        w, h = self.size
        lon = lon_min + x / w * (lon_max - lon_min)
        lat = lat_max - y / h * (lat_max - lat_min)
        return lon, lat

    def contains_lonlat(self, lon: float, lat: float) -> bool:
        """True if the point falls inside the calibrated bbox."""
        lon_min, lat_min, lon_max, lat_max = self.bbox
        return lon_min <= lon <= lon_max and lat_min <= lat <= lat_max

    @classmethod
    def from_manifest(cls, manifest: dict, size: tuple[int, int]) -> GeoRef:
        """Build from a map.json dict. `size` is the manifest's pixel size
        (passed explicitly so this stays independent of how it's read)."""
        georef = manifest.get("georef")
        if not georef:
            raise GeoRefError(
                "map.json has no 'georef' block -- run geo_import.py with "
                "--bbox once to calibrate it, or add it by hand: "
                '{"projection": "equirectangular", "bbox": [lon_min, lat_min, '
                "lon_max, lat_max]}"
            )
        try:
            bbox = tuple(float(v) for v in georef["bbox"])
        except (KeyError, TypeError, ValueError) as exc:
            raise GeoRefError(f"georef.bbox is malformed: {georef!r}") from exc
        if len(bbox) != 4:
            raise GeoRefError(f"georef.bbox needs 4 numbers, got {georef['bbox']!r}")
        projection = georef.get("projection", "equirectangular")
        return cls(bbox=bbox, size=size, projection=projection)  # type: ignore[arg-type]

    def to_manifest_block(self) -> dict:
        """The georef dict to write back into map.json (size lives in `size`)."""
        return {"projection": self.projection, "bbox": list(self.bbox)}
