//! Forest placement — client-only geometry, like everything else in this crate.
//!
//! The engine is coordinate-free, so trees are pure presentation: we scatter
//! low-poly instances over the terrain and hand Godot a flat list of
//! `(position, scale, yaw)` to feed a `MultiMesh` (one draw call for the lot).
//!
//! Placement is a hybrid of two masks laid over the same terrain:
//!
//! - a low-frequency fbm density mask that scatters clustered woods everywhere
//!   plausible (the wild woodland, tuned by `forests.density`), and
//! - the hand-placed named woods from `client.yaml`, forced in regardless of the
//!   mask so specific forests (Wicklow, Snowdon, the Caledonian) are guaranteed.
//!
//! Every candidate from either mask passes the same land/height/slope gate, so a
//! forest can never spill into the sea or climb onto bare peaks.

use crate::config::forests as forest_config;
use crate::geo;
use crate::mapdata::coastline;
use crate::terrain;

/// One tree, ready for a `MultiMesh` instance.
pub struct Tree {
    /// World position, `y` already seated on the terrain surface.
    pub pos: [f32; 3],
    /// Uniform scale (canopy size varies tree to tree).
    pub scale: f32,
    /// Rotation about the vertical axis, radians.
    pub yaw: f32,
}

// --- Placement gate --------------------------------------------------------

/// Lowest ground height a tree tolerates, in world units. Above the shore blend
/// (see `terrain::LAND_BASE`) so woods don't wade into the surf.
const MIN_H: f32 = 4.5;
/// Treeline, in world units. Calibrated against `terrain.rs`'s EXAG the same way
/// the shader's snow band is: ~40 units is ~1460 m, just under the snow that
/// starts blending at h=44, so forests stop where the mountains go bare.
const MAX_H: f32 = 40.0;
/// Steepest slope (1 - normal.y, world space) a tree will stand on. Post-EXAG a
/// gentle real hillside already reads steep, so this is generous — but true
/// cliffs (slope > ~0.6) stay bare rock.
const MAX_SLOPE: f32 = 0.55;

/// A small deterministic hash → [0,1), so a rebuild reproduces the same wood
/// rather than reshuffling every tree each frame.
fn hash(a: u32, b: u32) -> f32 {
    let mut h = a.wrapping_mul(0x1657_4b3f) ^ b.wrapping_mul(0x27d4_eb2d);
    h = (h ^ (h >> 13)).wrapping_mul(0x4bf5_9b2d);
    ((h ^ (h >> 16)) as f32) / (u32::MAX as f32)
}

/// Ground height and slope at a world point, or `None` if this spot fails the
/// land/height/slope gate (sea, too high, too steep). Slope is read from the
/// terrain surface exactly the way `terrain::build` derives its mesh normals.
fn seat(polys: &[Vec<(f32, f32)>], x: f32, z: f32) -> Option<f32> {
    let h = terrain::height_with(polys, x, z);
    if !(MIN_H..=MAX_H).contains(&h) {
        return None;
    }
    let eps = 2.0;
    let hl = terrain::height_with(polys, x - eps, z);
    let hr = terrain::height_with(polys, x + eps, z);
    let hd = terrain::height_with(polys, x, z - eps);
    let hu = terrain::height_with(polys, x, z + eps);
    // normal.y of the surface = 2·eps / |gradient|; slope = 1 - normal.y.
    let ny = 2.0 * eps / ((hl - hr).powi(2) + (2.0 * eps).powi(2) + (hd - hu).powi(2)).sqrt();
    if 1.0 - ny > MAX_SLOPE {
        return None;
    }
    Some(h)
}

/// Turn a seated candidate into a tree with deterministic size/spin jitter.
fn make_tree(x: f32, z: f32, h: f32, seed: u32) -> Tree {
    let s = 0.7 + hash(seed, 0x9e37) * 0.9; // 0.7 .. 1.6
    let yaw = hash(seed, 0x85eb) * std::f32::consts::TAU;
    Tree {
        pos: [x, h, z],
        scale: s,
        yaw,
    }
}

// --- Procedural mask -------------------------------------------------------

/// Grid spacing of candidate points, world units. Trees jitter within a cell, so
/// this sets density, not a visible lattice.
const STEP: f32 = 7.0;
/// Forest-cluster noise scale. Low frequency → broad woods with open country
/// between them (the Rome: Total War look), not an even sprinkle.
const CLUSTER_SCALE: f32 = 0.010;
/// Mask value above which a cell is "forest". Below it, bare.
const CLUSTER_THRESHOLD: f32 = 0.52;

/// Scatter procedural woods across the whole land extent.
fn procedural(polys: &[Vec<(f32, f32)>], out: &mut Vec<Tree>) {
    let cfg = forest_config();
    let cols = (2.0 * geo::HALF_W / STEP) as i32;
    let rows = (2.0 * geo::HALF_H / STEP) as i32;
    for r in 0..rows {
        for c in 0..cols {
            let seed = (r as u32) << 16 | c as u32;
            let jx = (hash(seed, 0x1234) - 0.5) * STEP;
            let jz = (hash(seed, 0x5678) - 0.5) * STEP;
            let x = -geo::HALF_W + c as f32 * STEP + jx;
            let z = -geo::HALF_H + r as f32 * STEP + jz;

            // Cluster mask: broad fbm lobes are the woods. Fade in over a band
            // above the threshold so a forest's edge thins out instead of ending
            // on a hard line, and drop trees stochastically within the fade.
            let mask = terrain::fbm_at(x * CLUSTER_SCALE, z * CLUSTER_SCALE, 4);
            let dens = ((mask - CLUSTER_THRESHOLD) / 0.12).clamp(0.0, 1.0) * cfg.density;
            if hash(seed, 0xabcd) > dens {
                continue;
            }
            if let Some(h) = seat(polys, x, z) {
                out.push(make_tree(x, z, h, seed));
            }
        }
    }
}

// --- Named forests ---------------------------------------------------------

/// Fill a named forest region with jittered trees, gated like everything else.
fn named(polys: &[Vec<(f32, f32)>], out: &mut Vec<Tree>) {
    for (fi, f) in forest_config().named.iter().enumerate() {
        // Grid the bounding box of the disc, jitter, keep what lands inside.
        let step = STEP * 0.85; // named woods a touch denser than the wild
        let n = (2.0 * f.radius / step).ceil() as i32;
        for r in 0..n {
            for c in 0..n {
                let seed = (fi as u32) << 24 | (r as u32) << 12 | c as u32;
                let jx = (hash(seed, 0x1111) - 0.5) * step;
                let jz = (hash(seed, 0x2222) - 0.5) * step;
                let x = f.x - f.radius + c as f32 * step + jx;
                let z = f.z - f.radius + r as f32 * step + jz;
                let d = ((x - f.x).powi(2) + (z - f.z).powi(2)).sqrt();
                if d > f.radius {
                    continue;
                }
                // Soft, denser core: thin the canopy toward the rim.
                let edge = 1.0 - (d / f.radius) * 0.5;
                if hash(seed, 0x3333) > f.density * edge {
                    continue;
                }
                if let Some(h) = seat(polys, x, z) {
                    out.push(make_tree(x, z, h, seed));
                }
            }
        }
    }
}

/// Build the full tree list: procedural wild woodland plus the named woods.
pub fn build() -> Vec<Tree> {
    let polys = coastline();
    let mut out = Vec::new();
    procedural(&polys, &mut out);
    named(&polys, &mut out);
    out
}
