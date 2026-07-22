//! Province territory: the land each settlement holds, and the lines between.
//!
//! The engine models provinces as a pure adjacency graph and deliberately owns
//! no geometry, so territory is derived here, the same way world positions are.
//! Each province claims the land nearer to its settlement than to any other —
//! a Voronoi diagram over [`geo::POS`] — and a border is the seam between two
//! neighbouring claims, cut back to the coastline so lines stop at the water.
//!
//! Voronoi neighbours are *not* the engine's adjacency list and are not meant to
//! be: the engine links London to Flanders across the Channel, and no border is
//! drawn there because the seam between them lies at sea. Borders describe the
//! shape of the land; the adjacency graph decides where armies may march.

use crate::geo;
use crate::mapdata::coastline;
use crate::terrain;

/// Spacing, in world units, at which a border is sampled against the terrain.
/// Fine enough that the line follows a hillside without visibly cutting through
/// it, coarse enough that ~60 borders stay a one-off cost at load.
const DRAPE_STEP: f32 = 4.0;

/// Height above the surface at which a border floats. Purely to beat z-fighting
/// with the terrain it is drawn on.
const DRAPE_LIFT: f32 = 0.6;

/// A border is kept only where both it and the land are above water. The
/// threshold sits above 0 rather than at it because the waterline is where the
/// terrain surface *crosses* zero — a line drawn exactly there is half-drowned
/// by the sea plane at every wave of the coast.
const LAND_MIN: f32 = 1.2;

/// One continuous stretch of border between provinces `a` and `b`, draped over
/// the terrain. A single seam that runs in and out of the sea (Denmark's, say)
/// yields several of these, all with the same `a` and `b`.
pub struct Border {
    pub a: usize,
    pub b: usize,
    /// World-space points, already seated on the terrain surface.
    pub points: Vec<[f32; 3]>,
}

/// Tag on a cell edge: the province across the seam, or [`RECT`] for an edge
/// that is merely the map's bounding box rather than a real neighbour.
const RECT: isize = -1;

/// A world XZ point.
type Pt = (f32, f32);
/// A cell vertex and the tag of the edge leaving it.
type TaggedVert = (Pt, isize);
/// A straight seam `(start, end)` and the province it faces.
type TaggedEdge = ((Pt, Pt), isize);

fn sq_dist(a: Pt, b: Pt) -> f32 {
    let (dx, dz) = (a.0 - b.0, a.1 - b.1);
    dx * dx + dz * dz
}

/// Clip `poly` to the half-plane of points at least as close to `si` as to `sj`,
/// tagging any edge introduced by the cut with `tag`.
///
/// Sutherland–Hodgman, carrying an edge tag alongside each vertex so that the
/// finished cell remembers which neighbour produced each of its sides. The tag
/// on a vertex describes the edge *leaving* it.
fn clip_halfplane(poly: &[TaggedVert], si: Pt, sj: Pt, tag: isize) -> Vec<TaggedVert> {
    // Points nearer si than sj satisfy dot(p, dir) <= dot(mid, dir).
    let dir = (sj.0 - si.0, sj.1 - si.1);
    let mid = ((si.0 + sj.0) * 0.5, (si.1 + sj.1) * 0.5);
    let limit = mid.0 * dir.0 + mid.1 * dir.1;
    let side = |p: Pt| p.0 * dir.0 + p.1 * dir.1 - limit;

    let n = poly.len();
    let mut out = Vec::with_capacity(n + 2);
    for k in 0..n {
        let (cur, t) = poly[k];
        let (nxt, _) = poly[(k + 1) % n];
        let (dc, dn) = (side(cur), side(nxt));
        let cross = || {
            let u = dc / (dc - dn);
            (cur.0 + (nxt.0 - cur.0) * u, cur.1 + (nxt.1 - cur.1) * u)
        };
        match (dc <= 0.0, dn <= 0.0) {
            (true, true) => out.push((cur, t)),
            // Leaving: the edge from the crossing point runs along the new cut.
            (true, false) => {
                out.push((cur, t));
                out.push((cross(), tag));
            }
            // Re-entering: the edge from the crossing point is the rest of `cur`'s.
            (false, true) => out.push((cross(), t)),
            (false, false) => {}
        }
    }
    out
}

/// The Voronoi cell of province `i`, as vertices tagged with the neighbour each
/// outgoing edge faces.
fn cell(i: usize) -> Vec<TaggedVert> {
    let (w, h) = (geo::HALF_W, geo::HALF_H);
    let mut poly = vec![
        ((-w, -h), RECT),
        ((w, -h), RECT),
        ((w, h), RECT),
        ((-w, h), RECT),
    ];
    let si = geo::POS[i];
    for (j, &sj) in geo::POS.iter().enumerate() {
        if j == i {
            continue;
        }
        poly = clip_halfplane(&poly, si, sj, j as isize);
        if poly.len() < 3 {
            break;
        }
    }
    poly
}

/// Every province border on the map, draped over the terrain and cut at the
/// coast. Built once at load: cells depend only on geography, never on who owns
/// what, so ownership changes recolour these lines rather than rebuild them.
pub fn borders() -> Vec<Border> {
    let polys = coastline();
    let mut out = Vec::new();
    for i in 0..geo::POS.len() {
        for (v, tag) in edges_of(i) {
            // Each seam is found twice, once from either side; keep one.
            if tag <= i as isize {
                continue;
            }
            out.extend(drape(&polys, i, tag as usize, v.0, v.1));
        }
    }
    out
}

/// The cell edges of province `i` as `((start, end), tag)`, dropping the ones
/// that only touch the bounding box.
fn edges_of(i: usize) -> Vec<TaggedEdge> {
    let poly = cell(i);
    let n = poly.len();
    (0..n)
        .filter(|&k| poly[k].1 != RECT)
        .map(|k| ((poly[k].0, poly[(k + 1) % n].0), poly[k].1))
        .collect()
}

/// Walk a straight seam, seating it on the terrain and breaking it wherever it
/// crosses water, so a border never floats over the sea.
fn drape(polys: &[Vec<Pt>], a: usize, b: usize, from: Pt, to: Pt) -> Vec<Border> {
    let len = sq_dist(from, to).sqrt();
    let steps = (len / DRAPE_STEP).ceil().max(1.0) as usize;
    let mut out = Vec::new();
    let mut run: Vec<[f32; 3]> = Vec::new();
    for s in 0..=steps {
        let u = s as f32 / steps as f32;
        let (x, z) = (from.0 + (to.0 - from.0) * u, from.1 + (to.1 - from.1) * u);
        let h = terrain::height_with(polys, x, z);
        if h > LAND_MIN {
            run.push([x, h + DRAPE_LIFT, z]);
        } else if run.len() > 1 {
            out.push(Border {
                a,
                b,
                points: std::mem::take(&mut run),
            });
        } else {
            run.clear();
        }
    }
    if run.len() > 1 {
        out.push(Border { a, b, points: run });
    }
    out
}

/// The province whose territory contains a world XZ point — the nearest
/// settlement, which is what "Voronoi cell" means. `None` at sea, where no
/// province holds the ground.
pub fn province_at(x: f32, z: f32) -> Option<usize> {
    if terrain::height(x, z) <= 0.0 {
        return None;
    }
    (0..geo::POS.len()).min_by(|&i, &j| {
        sq_dist(geo::POS[i], (x, z))
            .partial_cmp(&sq_dist(geo::POS[j], (x, z)))
            .unwrap()
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Every province must hold the ground its own settlement stands on. This
    /// is the load-bearing property of the whole scheme — cells are defined by
    /// nearest-settlement, so a province that does not contain its own city
    /// means the geometry and the engine's province table have desynced.
    #[test]
    fn each_city_lies_in_its_own_province() {
        for i in 0..geo::POS.len() {
            let (x, z) = geo::POS[i];
            assert_eq!(province_at(x, z), Some(i), "province {i} lost its own city");
        }
    }

    /// Borders must be real, finite polylines between two distinct provinces.
    #[test]
    fn borders_are_well_formed() {
        let bs = borders();
        assert!(!bs.is_empty(), "no borders were generated");
        for b in &bs {
            assert_ne!(b.a, b.b, "a province cannot border itself");
            assert!(
                b.a < b.b,
                "borders must be emitted once, in canonical order"
            );
            assert!(b.points.len() >= 2, "a border needs at least two points");
            for p in &b.points {
                assert!(
                    p.iter().all(|v| v.is_finite()),
                    "border {}-{} has a non-finite point",
                    b.a,
                    b.b
                );
            }
        }
    }

    /// No border may be drawn across water: a line hovering over the Channel
    /// reads as a bug, and culling at the coast is what prevents it.
    #[test]
    fn borders_stay_on_land() {
        for b in borders() {
            for p in &b.points {
                assert!(
                    p[1] > 0.0,
                    "border {}-{} is over water at ({:.1}, {:.1})",
                    b.a,
                    b.b,
                    p[0],
                    p[2]
                );
            }
        }
    }

    /// Neighbouring land provinces should actually share a drawn border. Paris
    /// and Burgundy are inland neighbours in the engine graph; if the Voronoi
    /// seam between them vanished, cells are not tiling the map.
    #[test]
    fn inland_neighbours_share_a_border() {
        let bs = borders();
        let found = bs.iter().any(|b| (b.a, b.b) == (4, 7));
        assert!(found, "Paris and Burgundy have no border between them");
    }
}
