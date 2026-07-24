//! Offline geometry bake: run the derived-geometry generators **once** and write
//! standard assets into `unreal/Content/Map/`.
//!
//! ```text
//! cargo run -p tw-bake --bin bake      # or: make bake
//! ```
//!
//! Nothing here computes geometry. It calls `terrain`, `regions`, `rivers` and
//! `forests` — the generators inherited from the retired Godot client, now
//! living in `src/geom/` — converts the
//! result into Unreal's coordinate system, and writes it out. That code is
//! deterministic, it works, and it is throwaway once this has run — so it is
//! reused rather than ported. The point is that none of it ever runs at runtime
//! again: an unoptimized `terrain.rs` takes ~70 s just for the 960x720 mesh.
//!
//! # Why this is its own crate
//!
//! The geometry files import no engine or renderer types at all — they are pure
//! computation over `geo.rs` + `elev.bin`. Keeping them in a standalone crate
//! means the bake links nothing but serde, and the whole thing builds and runs
//! in ~12 s from cold.
//!
//! # Coordinate systems
//!
//! Godot is Y-up, -Z forward, right-handed, with the map spanning +-620 x +-470
//! world units. Unreal is Z-up, X forward, **left-handed**, and 1 unit = 1 cm.
//! The conversion happens here, once, rather than at load:
//!
//! ```text
//! ue.x = -godot.z * SCALE
//! ue.y =  godot.x * SCALE
//! ue.z =  godot.y * SCALE
//! ```
//!
//! That mapping has determinant -1, which is exactly the right-to-left-handed
//! flip. It also inverts triangle orientation for free, so the indices are
//! emitted in their original order and come out right-hand-rule consistent with
//! the normals — asserted by `check_winding`, not assumed.

// The inherited files carry a few helpers that only the retired Godot client
// called (`faction_color`, `province_at`, `sea_extent`, ...). Pruning them would
// mean editing generated/ported geometry code for no functional gain.
#![allow(dead_code)]

// The geometry generators, declared at the crate root so the `crate::geo` /
// `crate::terrain` paths inside these files resolve unchanged.
#[path = "geom/config.rs"]
mod config;
#[path = "geom/forests.rs"]
mod forests;
#[path = "geom/geo.rs"]
mod geo;
#[path = "geom/mapdata.rs"]
mod mapdata;
#[path = "geom/regions.rs"]
mod regions;
#[path = "geom/rivers.rs"]
mod rivers;
#[path = "geom/terrain.rs"]
mod terrain;

use std::fs::{self, File};
use std::io::{BufWriter, Write};
use std::path::{Path, PathBuf};

use serde_json::{json, Value};

use config::config;
use mapdata::{coastline, map_extent};

/// Unreal units per Godot unit. Godot's 1240 x 940 map at 1 uu = 1 cm would be a
/// 12.4 m diorama; x100 makes it 1.24 km across, so a province reads as the
/// kilometres of ground it is meant to be.
const SCALE: f32 = 100.0;

/// Province names in engine order, for readable assertion failures. Mirrors the
/// table in `mapdata.rs`'s tests.
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

/// Godot world XZ(Y) -> Unreal world XYZ, in centimetres.
fn to_ue(p: [f32; 3]) -> [f32; 3] {
    [-p[2] * SCALE, p[0] * SCALE, p[1] * SCALE]
}

/// A direction (normal) rides the same basis change. It carries no translation
/// and the map is orthogonal up to the uniform scale, so this stays unit-length.
fn dir_to_ue(n: [f32; 3]) -> [f32; 3] {
    [-n[2], n[0], n[1]]
}

/// A ground-plane point, for things that only have XZ.
fn xz_to_ue(x: f32, z: f32) -> [f32; 2] {
    [-z * SCALE, x * SCALE]
}

fn main() {
    let out = out_dir();
    fs::create_dir_all(&out).expect("could not create the output directory");
    println!("baking into {}", out.display());

    let polys = coastline();
    check_coastline(&polys);
    check_cities_on_land();

    bake_terrain(&out);
    bake_heightmap(&out);
    bake_borders(&out);
    bake_rivers(&out);
    bake_forests(&out);
    bake_provinces(&out);

    println!("done.");
}

/// `unreal/Content/Map/`, resolved relative to this crate rather than to
/// whatever directory cargo was invoked from.
fn out_dir() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .expect("bake has a parent directory")
        .join("unreal/Content/Map")
}

// --- assertions ------------------------------------------------------------

/// Every province must sit on dry land — the terrain surface at the city is
/// above sea level. Ported from `mapdata.rs`'s `every_city_is_on_land` test as a
/// hard assertion, because after this bake there is no test suite left to run
/// it: a city that has slipped into the sea would ship as an unreachable
/// settlement frozen into a static asset.
fn check_cities_on_land() {
    assert_eq!(
        geo::POS.len(),
        NAMES.len(),
        "position table and province table disagree in length"
    );
    for (p, &name) in NAMES.iter().enumerate() {
        let (x, z) = geo::POS[p];
        let h = terrain::height(x, z);
        assert!(
            h > 0.0,
            "{name} (province {p}) is under water at ({x:.1}, {z:.1}), height {h:.2}"
        );
    }
    println!("  all {} cities are on dry land", NAMES.len());
}

/// The coastline mask must be well-formed: real loops with finite points.
fn check_coastline(polys: &[Vec<(f32, f32)>]) {
    assert!(!polys.is_empty(), "no coastline rings");
    for (i, ring) in polys.iter().enumerate() {
        assert!(ring.len() >= 3, "ring {i} has fewer than 3 points");
        for &(x, z) in ring {
            assert!(
                x.is_finite() && z.is_finite(),
                "ring {i} has non-finite point"
            );
        }
    }
}

/// After the basis change, a triangle's right-hand-rule normal must agree with
/// the per-vertex normals we emit alongside it.
///
/// This is the one thing about the conversion that is easy to get backwards and
/// impossible to notice in a diff: get it wrong and the mesh imports with every
/// face pointing into the hill, which reads as a lighting bug three phases
/// later. The determinant of the Godot-to-Unreal map is -1, so the flip happens
/// whether or not you think about it — assert the result instead of reasoning
/// about the convention.
fn check_winding(t: &terrain::TerrainData) {
    let mut checked = 0;
    for tri in t.indices.chunks_exact(3) {
        let [a, b, c] = [
            to_ue(t.positions[tri[0] as usize]),
            to_ue(t.positions[tri[1] as usize]),
            to_ue(t.positions[tri[2] as usize]),
        ];
        let u = [b[0] - a[0], b[1] - a[1], b[2] - a[2]];
        let v = [c[0] - a[0], c[1] - a[1], c[2] - a[2]];
        let g = [
            u[1] * v[2] - u[2] * v[1],
            u[2] * v[0] - u[0] * v[2],
            u[0] * v[1] - u[1] * v[0],
        ];
        let n = dir_to_ue(t.normals[tri[0] as usize]);
        let dot = g[0] * n[0] + g[1] * n[1] + g[2] * n[2];
        // Degenerate triangles (flat sea floor slivers) have a zero-length
        // cross product and nothing to say either way.
        if dot.abs() > 1e-3 {
            assert!(
                dot > 0.0,
                "triangle winding disagrees with the emitted normals: every \
                 face would import inside-out"
            );
            checked += 1;
        }
        if checked >= 10_000 {
            break;
        }
    }
    println!("  winding agrees with normals over {checked} triangles");
}

/// Serialise a row-major `cols` x `rows` terrain grid as Wavefront OBJ.
///
/// Split out from `bake_terrain` so the vertex/face formatting is testable
/// without a full bake — the format is load-bearing for the Unreal import and
/// has broken it silently once already (see the `vt` comment below).
fn write_obj<W: Write>(
    w: &mut W,
    t: &terrain::TerrainData,
    cols: usize,
    rows: usize,
) -> std::io::Result<()> {
    writeln!(w, "# Baked by `cargo run -p tw-bake`. Do not edit; regenerate.")?;
    writeln!(
        w,
        "# {}x{} grid, Unreal space (Z-up, left-handed), 1 unit = 1 cm, \
         vertical exaggeration EXAG={}.",
        cols,
        rows,
        terrain::EXAG
    )?;
    writeln!(w, "o TerrainBritain")?;
    for p in &t.positions {
        let [x, y, z] = to_ue(*p);
        writeln!(w, "v {x:.2} {y:.2} {z:.2}")?;
    }
    for n in &t.normals {
        let [x, y, z] = dir_to_ue(*n);
        writeln!(w, "vn {x:.4} {y:.4} {z:.4}")?;
    }
    // A UV channel is not optional. UE 5.8's Interchange OBJ translator ensures
    // `UVs.IsValidIndex(VertexData.UVIndex)` while building the mesh
    // description, so a `f v//vn` mesh — legal OBJ, no `vt` — fails the import
    // via a *handled ensure*: logged, no exception raised into Python, no asset
    // produced. The terrain then silently never lands in the level. Vertices are
    // row-major over the grid, so a planar UV is the normalised grid position.
    for i in 0..t.positions.len() {
        let (r, c) = (i / cols, i % cols);
        writeln!(
            w,
            "vt {:.6} {:.6}",
            c as f32 / (cols - 1) as f32,
            r as f32 / (rows - 1) as f32
        )?;
    }
    // Winding is *not* reversed. The source order is clockwise for Godot's
    // front face, and the right-to-left-handed flip already inverts triangle
    // orientation, so the indices come out the other side right-hand-rule
    // consistent with the `vn` normals — which is what an OBJ consumer expects
    // and what the bake asserts below. Reversing here as well would flip it
    // back and leave every face pointing into the hill.
    for tri in t.indices.chunks_exact(3) {
        let (a, b, c) = (tri[0] + 1, tri[1] + 1, tri[2] + 1);
        writeln!(w, "f {a}/{a}/{a} {b}/{b}/{b} {c}/{c}/{c}")?;
    }
    Ok(())
}

// --- the five outputs ------------------------------------------------------

fn bake_terrain(out: &Path) {
    let t = terrain::build();
    let expected = terrain::COLS * terrain::ROWS;
    assert_eq!(
        t.positions.len(),
        expected,
        "terrain grid is not {}x{}",
        terrain::COLS,
        terrain::ROWS
    );
    assert_eq!(t.normals.len(), t.positions.len());
    assert_eq!(t.indices.len() % 3, 0);

    check_winding(&t);

    let path = out.join("terrain.obj");
    let mut w = BufWriter::with_capacity(1 << 20, File::create(&path).unwrap());
    write_obj(&mut w, &t, terrain::COLS, terrain::ROWS).unwrap();
    w.flush().unwrap();
    report(
        &path,
        &format!(
            "{} vertices, {} triangles",
            t.positions.len(),
            t.indices.len() / 3
        ),
    );
}

/// The Landscape heightmap. The OBJ above is a static mesh; the Unreal frontend
/// instead imports the terrain as an `ALandscape` (LODs, tessellation, layered
/// material). Unreal's native landscape heightmap is **raw 16-bit, little-endian,
/// row-major** — so that is exactly what we write, no PNG encoder and no new
/// dependency. `terrain_meta.json` alongside it carries everything `landscape.py`
/// needs to place the landscape in the same Unreal-cm space as the borders,
/// rivers and markers, and to key the terrain material's height bands.
///
/// The grid is sampled directly from `terrain::height_with` in **Unreal
/// orientation** (local X = the short N–S axis, local Y = the long E–W axis), so
/// the Python side needs only an axis-aligned transform, not a handedness puzzle.
/// Both dimensions are `k*63 + 1`, a valid Unreal landscape size (components of
/// 63 quads), so the import never has to resample.
fn bake_heightmap(out: &Path) {
    // Unreal-space footprint (see `map_extent`): X is the short axis (Godot Z,
    // half-height), Y the long axis (Godot X, half-width). Both scaled to cm.
    const HALF_X: f32 = geo::HALF_H; // Godot half-height -> Unreal X
    const HALF_Y: f32 = geo::HALF_W; // Godot half-width  -> Unreal Y
    const WIDTH: usize = 757; // 12*63 + 1, along Unreal X (the short axis)
    const HEIGHT: usize = 1009; // 16*63 + 1, along Unreal Y (the long axis)

    let polys = coastline();
    let mut heights_cm = vec![0.0f32; WIDTH * HEIGHT];
    let (mut min_cm, mut max_cm) = (f32::MAX, f32::MIN);
    for iy in 0..HEIGHT {
        // Unreal Y across the long axis -> Godot X.
        let gx = -HALF_Y + iy as f32 / (HEIGHT - 1) as f32 * 2.0 * HALF_Y;
        for ix in 0..WIDTH {
            // Unreal X across the short axis -> Godot Z is -ue.x, so gz = +that.
            let ue_x = -HALF_X + ix as f32 / (WIDTH - 1) as f32 * 2.0 * HALF_X;
            let gz = -ue_x;
            let h_cm = terrain::height_with(&polys, gx, gz) * SCALE;
            heights_cm[iy * WIDTH + ix] = h_cm;
            min_cm = min_cm.min(h_cm);
            max_cm = max_cm.max(h_cm);
        }
    }

    // Normalise [min,max] cm -> full 16-bit range. `landscape.py` inverts this
    // with the actor Z-scale, so the world Z of a texel comes back out in cm.
    let span = (max_cm - min_cm).max(1e-3);
    let path = out.join("heightmap.r16");
    let mut w = BufWriter::with_capacity(1 << 20, File::create(&path).unwrap());
    for &h in &heights_cm {
        let u = (((h - min_cm) / span) * 65535.0)
            .round()
            .clamp(0.0, 65535.0) as u16;
        w.write_all(&u.to_le_bytes()).unwrap();
    }
    w.flush().unwrap();
    report(&path, &format!("{WIDTH}x{HEIGHT} 16-bit raw"));

    // The band anchors the terrain material keys off, carried across the language
    // boundary in cm so a drift in EXAG is at least visible on the Unreal side.
    // world-unit anchors (see terrain.rs): h=30 ~1060 m, h=46 snow line, h=72 peak.
    let meta = json!({
        "heightmap": {
            "file": "heightmap.r16",
            "width": WIDTH,
            "height": HEIGHT,
            "bit_depth": 16,
            "byte_order": "little",
            "encoding": "raw",
            "row_major": true,
            "orientation": "unreal: local X = short (N-S) axis, local Y = long (E-W) axis",
        },
        // Unreal-cm footprint, same convention as provinces.json map_extent.
        "extent_cm": { "x": 2.0 * HALF_X * SCALE, "y": 2.0 * HALF_Y * SCALE },
        // Inverse of the normalisation above: worldZ_cm = min + (u16/65535)*span.
        "height_cm": { "min": min_cm, "max": max_cm, "span": span },
        "world_scale": SCALE,
        "terrain_exag": terrain::EXAG,
        // Height bands the terrain material blends on, in Unreal cm (world-unit
        // anchor * SCALE). Retune EXAG and these move; see the map-exag coupling.
        "bands_cm": { "land": 30.0 * SCALE, "snow": 46.0 * SCALE, "peak": 72.0 * SCALE },
    });
    let mpath = out.join("terrain_meta.json");
    write_json(&mpath, &meta);
    report(&mpath, &format!("range {min_cm:.0}..{max_cm:.0} cm"));
}

fn bake_borders(out: &Path) {
    let borders = regions::borders();
    let v: Vec<Value> = borders
        .iter()
        .map(|b| {
            json!({
                "a": b.a,
                "b": b.b,
                "points": b.points.iter().map(|&p| to_ue(p).to_vec()).collect::<Vec<_>>(),
            })
        })
        .collect();
    let path = out.join("province_borders.json");
    write_json(&path, &Value::Array(v));
    report(&path, &format!("{} border runs", borders.len()));
}

fn bake_rivers(out: &Path) {
    let rivers = rivers::build();
    let v: Vec<Value> = rivers
        .iter()
        .map(|r| {
            json!({
                "points": r.points.iter().map(|&p| to_ue(p).to_vec()).collect::<Vec<_>>(),
                // A half-width is a length, so it takes the scale but not the
                // basis change.
                "width": r.width * SCALE,
            })
        })
        .collect();
    let path = out.join("rivers.json");
    write_json(&path, &Value::Array(v));
    report(&path, &format!("{} rivers", rivers.len()));
}

fn bake_forests(out: &Path) {
    let trees = forests::build();
    let v: Vec<Value> = trees
        .iter()
        .map(|t| {
            json!({
                "pos": to_ue(t.pos).to_vec(),
                // Uniform scale is dimensionless: the tree mesh is authored in
                // Unreal units, so it must not pick up the world scale.
                "scale": t.scale,
                // Yaw is about Godot's +Y / Unreal's +Z — the same axis — but
                // the handedness flip reverses its sense.
                "yaw_deg": -t.yaw.to_degrees(),
            })
        })
        .collect();
    let path = out.join("forests.json");
    write_json(&path, &Value::Array(v));
    report(&path, &format!("{} trees", trees.len()));
}

/// Province positions, the map extent, the faction palette — and the bake's own
/// metadata, so the next person to touch any of it can see what it was made with.
fn bake_provinces(out: &Path) {
    let c = config();
    let (ew, eh) = map_extent();
    let provinces: Vec<Value> = NAMES
        .iter()
        .enumerate()
        .map(|(i, &name)| {
            let (x, z) = geo::POS[i];
            json!({ "id": i, "name": name, "pos": xz_to_ue(x, z).to_vec() })
        })
        .collect();
    let colors: Vec<Value> = c
        .faction_colors
        .iter()
        .map(|c| json!({ "r": c.r, "g": c.g, "b": c.b }))
        .collect();

    let doc = json!({
        "meta": {
            "generator": "tw-bake",
            "space": "unreal",
            "units": "cm",
            "godot_to_unreal": "ue = (-gz, gx, gy) * scale",
            "world_scale": SCALE,
            // EXAG is why the terrain has any readable relief at all, and every
            // height and slope threshold in the terrain material is calibrated
            // against it. Carried here so the coupling is visible on the Unreal
            // side of the language boundary instead of only in a Rust const.
            "terrain_exag": terrain::EXAG,
            "terrain_grid": [terrain::COLS, terrain::ROWS],
        },
        // Sea-plane half-extents, converted: Godot's half-width runs along X and
        // its half-height along Z, which swap under the basis change.
        "map_extent": { "x": eh * SCALE, "y": ew * SCALE },
        "provinces": provinces,
        "faction_colors": colors,
    });
    let path = out.join("provinces.json");
    write_json(&path, &doc);
    report(
        &path,
        &format!("{} provinces, {} colours", provinces.len(), colors.len()),
    );
}

// --- plumbing --------------------------------------------------------------

fn write_json(path: &Path, v: &Value) {
    let mut w = BufWriter::with_capacity(1 << 20, File::create(path).unwrap());
    serde_json::to_writer(&mut w, v).unwrap();
    w.flush().unwrap();
}

fn report(path: &Path, what: &str) {
    let bytes = fs::metadata(path).map(|m| m.len()).unwrap_or(0);
    println!(
        "  {:<22} {what} ({:.1} MB)",
        path.file_name().unwrap().to_string_lossy(),
        bytes as f64 / 1e6
    );
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A 2x2 grid: 4 vertices, 2 triangles.
    fn quad() -> terrain::TerrainData {
        terrain::TerrainData {
            positions: vec![
                [0.0, 0.0, 0.0],
                [1.0, 0.0, 0.0],
                [0.0, 0.0, 1.0],
                [1.0, 0.0, 1.0],
            ],
            normals: vec![[0.0, 1.0, 0.0]; 4],
            indices: vec![0, 1, 2, 1, 3, 2],
        }
    }

    fn write(t: &terrain::TerrainData) -> String {
        let mut buf = Vec::new();
        write_obj(&mut buf, t, 2, 2).unwrap();
        String::from_utf8(buf).unwrap()
    }

    /// Regression: the OBJ must carry a UV channel. Without `vt` lines UE 5.8's
    /// Interchange OBJ translator trips a handled ensure and produces no asset —
    /// no exception, no terrain in the level, all-black shots.
    #[test]
    fn obj_has_a_uv_per_vertex() {
        let t = quad();
        let out = write(&t);
        let vts: Vec<&str> = out.lines().filter(|l| l.starts_with("vt ")).collect();
        assert_eq!(
            vts.len(),
            t.positions.len(),
            "expected one vt per vertex, got {vts:?}"
        );
        // Corners of the unit UV square, row-major.
        assert_eq!(vts[0], "vt 0.000000 0.000000");
        assert_eq!(vts[3], "vt 1.000000 1.000000");
    }

    /// Regression: faces must reference the UV channel (`v/vt/vn`). A `v//vn`
    /// face is what tripped the ensure even once `vt` lines existed.
    #[test]
    fn obj_faces_reference_uvs() {
        let out = write(&quad());
        let faces: Vec<&str> = out.lines().filter(|l| l.starts_with("f ")).collect();
        assert_eq!(faces.len(), 2);
        for f in &faces {
            assert!(
                !f.contains("//"),
                "face omits the UV index (v//vn), which fails the UE import: {f}"
            );
            for vert in f.split_whitespace().skip(1) {
                let idx: Vec<&str> = vert.split('/').collect();
                assert_eq!(idx.len(), 3, "expected v/vt/vn triples, got {vert}");
                assert!(idx.iter().all(|s| !s.is_empty()), "empty index in {vert}");
            }
        }
        assert_eq!(faces[0], "f 1/1/1 2/2/2 3/3/3");
    }
}
