//! Terrain built from **real elevation** (terrarium DEM, baked into
//! [`crate::geo::ELEV`]) constrained to the Natural Earth coastline mask, emitted
//! as raw mesh arrays that the Godot side assembles into an `ArrayMesh`. Pure
//! Rust, no Godot types here. Colour is the shader's job — we ship geometry only.

use crate::geo;
use crate::mapdata::{coastline, map_extent};

const SEA_FLOOR: f32 = -10.0;
const HALF_W: f32 = geo::HALF_W;
const HALF_H: f32 = geo::HALF_H;
pub const COLS: usize = 960;
pub const ROWS: usize = 720;

/// Vertical exaggeration, world units per metre of real elevation.
///
/// Europe is ~3000 km across a ~1240-unit map, so one unit is ~2.4 km. At true
/// scale the Alps would stand 1.6 units tall — invisible. This is ~60x, chosen
/// so the highest sampled peak (2765 m, after `gen_geo.py`'s low-pass) reaches
/// ~72 units, the top of the shader's snow band.
///
/// Every height threshold in `Main.gd`'s shader is downstream of this number:
/// h=30 is ~1060 m, h=46 (snow line) ~1700 m, h=64 ~2420 m. Retune this and the
/// bands must move with it, or the palette goes dead again.
pub const EXAG: f32 = 0.025;

/// Floor for dry land, in world units. Real lowlands (London 46 m, the Po valley
/// 41 m) are essentially at sea level and would otherwise sit in the shader's
/// beach band, painting half of Europe as sand. This lifts them clear of it, so
/// sand survives only in the shore blend where height actually passes through 0.
const LAND_BASE: f32 = 3.5;

/// Raw, Godot-agnostic mesh data.
pub struct TerrainData {
    pub positions: Vec<[f32; 3]>,
    pub normals: Vec<[f32; 3]>,
    pub indices: Vec<i32>,
}

fn hash2(ix: i32, iz: i32) -> f32 {
    let mut h = (ix as u32).wrapping_mul(0x1657_4b3f) ^ (iz as u32).wrapping_mul(0x27d4_eb2d);
    h = (h ^ (h >> 13)).wrapping_mul(0x4bf5_9b2d);
    ((h ^ (h >> 16)) as f32) / (u32::MAX as f32)
}

fn vnoise(x: f32, z: f32) -> f32 {
    let (x0, z0) = (x.floor(), z.floor());
    let (fx, fz) = (x - x0, z - z0);
    let (ix, iz) = (x0 as i32, z0 as i32);
    let sx = fx * fx * (3.0 - 2.0 * fx);
    let sz = fz * fz * (3.0 - 2.0 * fz);
    let n00 = hash2(ix, iz);
    let n10 = hash2(ix + 1, iz);
    let n01 = hash2(ix, iz + 1);
    let n11 = hash2(ix + 1, iz + 1);
    let a = n00 + (n10 - n00) * sx;
    let b = n01 + (n11 - n01) * sx;
    a + (b - a) * sz
}

/// The terrain's own value-noise fbm, exposed so forest placement can share the
/// exact field the land is shaped from (its clusters then track the landform).
pub fn fbm_at(x: f32, z: f32, octaves: u32) -> f32 {
    fbm(x, z, octaves)
}

fn fbm(mut x: f32, mut z: f32, octaves: u32) -> f32 {
    let (mut amp, mut sum, mut norm) = (0.5, 0.0, 0.0);
    for _ in 0..octaves {
        sum += amp * vnoise(x, z);
        norm += amp;
        amp *= 0.5;
        x *= 2.0;
        z *= 2.0;
    }
    sum / norm
}

fn smoothstep(a: f32, b: f32, x: f32) -> f32 {
    let t = ((x - a) / (b - a)).clamp(0.0, 1.0);
    t * t * (3.0 - 2.0 * t)
}

fn inside(polys: &[Vec<(f32, f32)>], px: f32, pz: f32) -> bool {
    let mut c = false;
    for poly in polys {
        let n = poly.len();
        let mut j = n - 1;
        for i in 0..n {
            let (ax, az) = poly[i];
            let (bx, bz) = poly[j];
            if (az > pz) != (bz > pz) {
                let t = (pz - az) / (bz - az);
                if px < ax + t * (bx - ax) {
                    c = !c;
                }
            }
            j = i;
        }
    }
    c
}

fn dist_to_coast(polys: &[Vec<(f32, f32)>], px: f32, pz: f32) -> f32 {
    let mut best = f32::MAX;
    for poly in polys {
        let n = poly.len();
        for i in 0..n {
            let (ax, az) = poly[i];
            let (bx, bz) = poly[(i + 1) % n];
            let (ex, ez) = (bx - ax, bz - az);
            let len2 = (ex * ex + ez * ez).max(1e-6);
            let t = (((px - ax) * ex + (pz - az) * ez) / len2).clamp(0.0, 1.0);
            let (cx, cz) = (ax + ex * t, az + ez * t);
            let d = (px - cx) * (px - cx) + (pz - cz) * (pz - cz);
            if d < best {
                best = d;
            }
        }
    }
    best.sqrt()
}

/// Real elevation in metres at a grid cell.
fn elev_px(c: usize, r: usize) -> f32 {
    let i = (r * geo::ELEV_W + c) * 2;
    i16::from_le_bytes([geo::ELEV[i], geo::ELEV[i + 1]]) as f32
}

/// Bilinearly sampled real elevation, in metres, at a world XZ point.
fn dem_meters(x: f32, z: f32) -> f32 {
    let fx = ((x + HALF_W) / (2.0 * HALF_W) * (geo::ELEV_W - 1) as f32)
        .clamp(0.0, (geo::ELEV_W - 1) as f32);
    let fz = ((z + HALF_H) / (2.0 * HALF_H) * (geo::ELEV_H - 1) as f32)
        .clamp(0.0, (geo::ELEV_H - 1) as f32);
    let (c0, r0) = (fx.floor() as usize, fz.floor() as usize);
    let (c1, r1) = ((c0 + 1).min(geo::ELEV_W - 1), (r0 + 1).min(geo::ELEV_H - 1));
    let (tx, tz) = (fx - c0 as f32, fz - r0 as f32);
    let a = elev_px(c0, r0) + (elev_px(c1, r0) - elev_px(c0, r0)) * tx;
    let b = elev_px(c0, r1) + (elev_px(c1, r1) - elev_px(c0, r1)) * tx;
    a + (b - a) * tz
}

/// Elevation at a world point, against a coastline the caller already has.
/// [`height`] is the convenient form; this is the one to use in a loop, where
/// rebuilding the coastline per sample would dominate the cost.
pub fn height_with(polys: &[Vec<(f32, f32)>], x: f32, z: f32) -> f32 {
    // Signed distance to the coastline: positive inland, negative offshore.
    // Keeping height a *continuous* function of position means the waterline
    // (where the surface crosses y=0) is a smooth contour instead of snapping
    // to grid cells — that's what kills the stair-stepped coast.
    let d = dist_to_coast(polys, x, z);
    let signed = if inside(polys, x, z) { d } else { -d };

    // The land profile this point would have if it were dry: real landform from
    // the DEM, with fbm demoted to a *detail* layer that breaks up the 512x384
    // grid without inventing mountains. Detail grows with elevation so plains
    // stay plate-flat (the Po valley should read as flat) and peaks stay rugged.
    let real = dem_meters(x, z).max(0.0) * EXAG;
    let detail = (fbm(x * 0.05, z * 0.05, 4) - 0.5) * 1.6 * (1.0 + real * 0.08);
    let land = LAND_BASE + real + detail;

    // Anchor the waterline *on* the coastline: h is exactly 0 at signed == 0,
    // rising to full land just inland and falling to the sea floor offshore.
    //
    // The obvious formulation — lerping from SEA_FLOOR to land across a band
    // straddling the coast — quietly drowns real coastal cities. It drags land
    // toward -10, and real lowlands are tiny (Stockholm is ~10 m, i.e. 3.75
    // units); the old *fake* terrain averaged ~14 units and always won that tug
    // of war, so the guard in mapdata never fired. Interpolating from zero
    // instead makes "inside the coastline implies above water" structural rather
    // than a coincidence of amplitudes. Both smoothsteps are flat at signed == 0,
    // so the surface stays C1 across the waterline and the coast reads smooth.
    let h = if signed >= 0.0 {
        land * smoothstep(0.0, 7.0, signed)
    } else {
        SEA_FLOOR * smoothstep(0.0, -22.0, signed)
    };

    // Sink whatever land reaches the mesh border back into the sea. Europe does
    // not stop at x=620 — without this the mesh edge slices the continent off
    // mid-air and the map reads as a slab. Drowning it instead means the world
    // ends in open ocean, which fog can then dissolve. The fade is perturbed by
    // noise because a purely axis-aligned one drowns land along a suspiciously
    // straight meridian, trading a cliff for an equally artificial coastline.
    // Kept tight against the border on purpose: reaching 150 units inward put
    // the fade on top of Stockholm (z=-374, i.e. 230 km from the edge) and sank
    // it. Anything that widens this band has to clear the northernmost city.
    let wobble = (fbm(x * 0.004, z * 0.004, 3) - 0.5) * 40.0;
    let edge = (1.0 - smoothstep(HALF_W - 70.0, HALF_W - 10.0, x.abs() - wobble))
        .min(1.0 - smoothstep(HALF_H - 70.0, HALF_H - 10.0, z.abs() + wobble));
    SEA_FLOOR + (h - SEA_FLOOR) * edge
}

/// Elevation at a world point (for seating settlements and armies).
pub fn height(x: f32, z: f32) -> f32 {
    height_with(&coastline(), x, z)
}

/// Build the land mesh as raw arrays. Geometry only — the shader colours it.
pub fn build() -> TerrainData {
    let polys = coastline();
    let n = COLS * ROWS;
    let mut positions = Vec::with_capacity(n);
    let mut normals = Vec::with_capacity(n);

    let dx = (2.0 * HALF_W) / (COLS - 1) as f32;
    let dz = (2.0 * HALF_H) / (ROWS - 1) as f32;
    let eps = dx.min(dz) * 0.5;

    for r in 0..ROWS {
        for c in 0..COLS {
            let x = -HALF_W + c as f32 * dx;
            let z = -HALF_H + r as f32 * dz;
            let h = height_with(&polys, x, z);
            positions.push([x, h, z]);
            let hl = height_with(&polys, x - eps, z);
            let hr = height_with(&polys, x + eps, z);
            let hd = height_with(&polys, x, z - eps);
            let hu = height_with(&polys, x, z + eps);
            let inv = 1.0 / (((hl - hr).powi(2) + (2.0 * eps).powi(2) + (hd - hu).powi(2)).sqrt());
            normals.push([(hl - hr) * inv, 2.0 * eps * inv, (hd - hu) * inv]);
        }
    }

    let mut indices = Vec::with_capacity((COLS - 1) * (ROWS - 1) * 6);
    for r in 0..ROWS - 1 {
        for c in 0..COLS - 1 {
            let i = (r * COLS + c) as i32;
            let right = i + 1;
            let down = i + COLS as i32;
            let dr = down + 1;
            // Clockwise winding for Godot's default front face.
            indices.extend([i, right, down, right, dr, down]);
        }
    }

    TerrainData {
        positions,
        normals,
        indices,
    }
}

/// Full sea-plane extent (for the water quad). Deliberately far larger than the
/// land: the quad's edge must fall beyond the fog's reach, or it reads as the
/// rim of a floating slab. Two triangles — the size costs nothing.
pub fn sea_extent() -> (f32, f32) {
    let (w, h) = map_extent();
    (w * 6.0, h * 6.0)
}
