//! Rivers — client-only geometry, traced from the same baked DEM the land is
//! shaped from, so a river always sits in a real valley and runs downhill to the
//! sea instead of being painted on by hand.
//!
//! The method is the textbook hydrology pipeline over [`geo::ELEV`]:
//!
//! 1. **Flow direction (D8):** every land cell drains to its lowest of eight
//!    neighbours. Cells with no lower neighbour (pits, the sea) drain nowhere.
//! 2. **Flow accumulation:** processing cells high-to-low, each passes its
//!    tally downstream, so a cell's accumulation is the size of the catchment
//!    draining through it. Big numbers are trunks; small ones are headwaters.
//! 3. **Channels:** cells whose accumulation clears [`ACCUM_THRESHOLD`] are
//!    "wet". Tracing each headwater downstream until it merges into an
//!    already-drawn channel (or reaches the coast) yields tributary polylines
//!    that join into trunks, exactly like a real river network.
//!
//! Everything is then draped on the terrain surface the frontend renders, the
//! same way borders are, and handed over as plain point lists — the shader/
//! material owns the water's colour, this owns only its course.

use crate::geo;
use crate::mapdata::coastline;
use crate::terrain;

const W: usize = geo::ELEV_W;
const H: usize = geo::ELEV_H;

/// Catchment size (in DEM cells) a channel must drain before it reads as a
/// river. Lower spawns a denser, brookier network; higher keeps only the
/// trunks. Tuned so Britain carries a handful of recognisable rivers rather
/// than a spider's web of every rain gully.
const ACCUM_THRESHOLD: u32 = 45;

/// Drop rivers shorter than this many draped points — stubs that clear the
/// accumulation threshold for only a cell or two are DEM noise, not rivers.
const MIN_POINTS: usize = 5;

/// Height above the terrain a river floats, purely to beat z-fighting with the
/// ground it lies on (mirrors `regions::DRAPE_LIFT`).
const DRAPE_LIFT: f32 = 0.45;

/// A river must stay on land above this height, matching the border code's
/// `LAND_MIN`: the visible waterline is where the terrain surface *crosses*
/// zero, so a course drawn below this is already drowned by the sea plane. The
/// trace keeps the first point that dips under it, so the river visibly touches
/// the coast, then stops.
const LAND_MIN: f32 = 1.2;

/// One drawn river: a draped course plus the width it should carry (trunks run
/// wider than headwater tributaries).
pub struct River {
    /// World-space points, already seated on the terrain surface.
    pub points: Vec<[f32; 3]>,
    /// Ribbon half-width hint, in world units, from the channel's flow.
    pub width: f32,
}

/// Real elevation in metres at a DEM cell.
fn elev(c: usize, r: usize) -> f32 {
    let i = (r * W + c) * 2;
    i16::from_le_bytes([geo::ELEV[i], geo::ELEV[i + 1]]) as f32
}

/// DEM cell centre → world XZ (inverse of `terrain::dem_meters`'s sampling).
fn cell_to_world(c: usize, r: usize) -> (f32, f32) {
    let x = -geo::HALF_W + c as f32 / (W - 1) as f32 * 2.0 * geo::HALF_W;
    let z = -geo::HALF_H + r as f32 / (H - 1) as f32 * 2.0 * geo::HALF_H;
    (x, z)
}

/// Ribbon half-width for a channel of the given accumulation: trunks (huge
/// catchments) run wider, headwaters stay thin. Log so the widest rivers don't
/// balloon past the map's scale.
fn width_for(accum: u32) -> f32 {
    let t = ((accum as f32 / ACCUM_THRESHOLD as f32).ln() / 4.0).clamp(0.0, 1.0);
    2.2 + t * 3.0
}

pub fn build() -> Vec<River> {
    let n = W * H;

    // D8 flow direction: the flat index of each cell's lowest neighbour, or
    // usize::MAX where nothing is lower (a pit, or an all-sea neighbourhood).
    let mut down = vec![usize::MAX; n];
    for r in 0..H {
        for c in 0..W {
            if elev(c, r) <= 0.0 {
                continue; // sea drains nowhere
            }
            let mut best = elev(c, r);
            let mut bi = usize::MAX;
            for dr in -1i32..=1 {
                for dc in -1i32..=1 {
                    if dr == 0 && dc == 0 {
                        continue;
                    }
                    let (nc, nr) = (c as i32 + dc, r as i32 + dr);
                    if nc < 0 || nr < 0 || nc >= W as i32 || nr >= H as i32 {
                        continue;
                    }
                    let ne = elev(nc as usize, nr as usize);
                    if ne < best {
                        best = ne;
                        bi = nr as usize * W + nc as usize;
                    }
                }
            }
            down[r * W + c] = bi;
        }
    }

    // Flow accumulation: seed every land cell with 1, then, sweeping high-to-low
    // so a cell is always settled before whatever it drains into, push each
    // cell's tally downstream. Height order makes this a single linear pass with
    // no iteration to convergence.
    let mut land: Vec<usize> = (0..n).filter(|&i| elev(i % W, i / W) > 0.0).collect();
    land.sort_by(|&a, &b| elev(b % W, b / W).partial_cmp(&elev(a % W, a / W)).unwrap());
    let mut accum = vec![1u32; n];
    for &i in &land {
        let d = down[i];
        if d != usize::MAX {
            accum[d] += accum[i];
        }
    }

    let is_river = |i: usize| accum[i] >= ACCUM_THRESHOLD;

    // Headwaters: a wet cell with no wet cell draining into it. Everything
    // downstream of one is reached by tracing, so these are the only trace
    // starts we need. (A cell fed only by dry slopes is where a river is born.)
    let mut fed = vec![false; n];
    for &i in &land {
        let d = down[i];
        if is_river(i) && d != usize::MAX && is_river(d) {
            fed[d] = true;
        }
    }

    let polys = coastline();
    let mut visited = vec![false; n];
    let mut rivers = Vec::new();

    for &start in &land {
        if !is_river(start) || fed[start] {
            continue;
        }
        // Follow the flow downstream, draping each cell on the terrain, until we
        // merge into an already-drawn channel or the course reaches the coast.
        let mut points: Vec<[f32; 3]> = Vec::new();
        let mut max_accum = accum[start];
        let mut i = start;
        loop {
            let (x, z) = cell_to_world(i % W, i / W);
            let h = terrain::height_with(&polys, x, z);
            points.push([x, h.max(0.0) + DRAPE_LIFT, z]);
            max_accum = max_accum.max(accum[i]);

            // Reached the sea/shore: keep this touching point, then stop.
            if h < LAND_MIN {
                break;
            }
            let d = down[i];
            if d == usize::MAX || !is_river(d) {
                break;
            }
            // Merging into a trunk another headwater already traced: draw one
            // step in so the tributary visibly joins, then let the trunk own the
            // rest of the course.
            if visited[i] && i != start {
                break;
            }
            visited[i] = true;
            i = d;
        }

        if points.len() >= MIN_POINTS {
            rivers.push(River {
                points,
                width: width_for(max_accum),
            });
        }
    }

    rivers
}
