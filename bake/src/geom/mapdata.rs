//! Geography that the pure engine deliberately omits: province world
//! positions, faction colours, and the Britain coastline used as the terrain
//! land mask.
//!
//! Positions and coastline are derived from **real geography** — Natural Earth
//! 50m land polygons (public domain), clipped to a Britain & Ireland bounding
//! box and projected to world XZ. The generated data lives in [`crate::geo`];
//! regenerate it with `bake/gen_geo.py`. Faction colours and the
//! sea-plane size are editable config in [`crate::config`], not baked here.

use crate::config::config;
use crate::geo;

/// World-space half-extents used to size the sea plane.
pub fn map_extent() -> (f32, f32) {
    let e = config().map_extent;
    (e.w, e.h)
}

/// A faction's fill colour (sRGB u8), indexed by `FactionId.0`; rebels last.
pub fn faction_color(f: usize) -> (u8, u8, u8) {
    let c = config().faction_colors[f];
    (c.r, c.g, c.b)
}

/// A province's ground-plane XZ position (already in world space).
pub fn province_xz(p: usize) -> (f32, f32) {
    geo::POS[p]
}

/// Coastline loops in world XZ, used as the terrain land mask.
pub fn coastline() -> Vec<Vec<(f32, f32)>> {
    geo::RAW_GEO.iter().map(|poly| poly.to_vec()).collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::terrain;

    /// Every province must sit on dry land — i.e. the terrain surface at the
    /// city is above sea level. This guards the real-geography pipeline: if a
    /// projection tweak or coastline regeneration drops a city into the sea,
    /// this fails instead of shipping an unreachable settlement.
    #[test]
    fn every_city_is_on_land() {
        for (p, &name) in NAMES.iter().enumerate() {
            let (x, z) = province_xz(p);
            let h = terrain::height(x, z);
            assert!(
                h > 0.0,
                "{name} (province {p}) is under water at ({x:.1}, {z:.1}), height {h:.2}"
            );
        }
    }

    /// The coastline mask must be well-formed: real loops with finite points.
    #[test]
    fn coastline_is_well_formed() {
        let rings = coastline();
        assert!(!rings.is_empty(), "no coastline rings");
        for (i, ring) in rings.iter().enumerate() {
            assert!(ring.len() >= 3, "ring {i} has fewer than 3 points");
            for &(x, z) in ring {
                assert!(
                    x.is_finite() && z.is_finite(),
                    "ring {i} has non-finite point"
                );
            }
        }
    }

    /// Province table and position table must agree in length.
    #[test]
    fn positions_match_province_count() {
        assert_eq!(geo::POS.len(), NAMES.len());
    }

    // Province names in engine order, for readable test failures.
    const NAMES: [&str; 12] = [
        "London",
        "York",
        "Norwich",
        "Exeter",
        "Chester",
        "Cardiff",
        "Caernarfon",
        "Lothian",
        "Stirling",
        "Highlands",
        "Leinster",
        "Munster",
    ];
}
