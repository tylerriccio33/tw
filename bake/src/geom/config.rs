//! Presentation config for the derived geometry, loaded from `bake/config/client.yaml`.
//!
//! The engine is deliberately geography- and look-agnostic (see [`crate::mapdata`]),
//! so faction colours and the sea-plane size live here rather than in a Rust
//! `const`. Same shape as the engine's `config` module: serde structs parsed once
//! from YAML embedded with [`include_str!`], behind a [`LazyLock`]. A malformed
//! file is a content bug we control, so parsing panics rather than returning a
//! `Result`.

use std::sync::LazyLock;

use serde::Deserialize;

/// An sRGB colour, 0-255 per channel.
#[derive(Debug, Clone, Copy, Deserialize)]
pub struct Rgb {
    pub r: u8,
    pub g: u8,
    pub b: u8,
}

/// Half-extents of a plane in world units.
#[derive(Debug, Clone, Copy, Deserialize)]
pub struct Extent {
    pub w: f32,
    pub h: f32,
}

#[derive(Debug, Deserialize)]
pub struct ClientConfig {
    /// Faction fill colours, indexed by `FactionId.0` (rebels last).
    pub faction_colors: Vec<Rgb>,
    pub map_extent: Extent,
    /// Forest placement knobs (see [`crate::forests`]).
    pub forests: ForestConfig,
}

/// A single hand-placed wood: a disc in world XZ that gets filled with trees
/// (still gated on land/height/slope, so it can't spill into the sea).
#[derive(Debug, Clone, Deserialize)]
pub struct NamedForest {
    /// For readability of the config; unused by the renderer.
    #[allow(dead_code)]
    pub name: String,
    pub x: f32,
    pub z: f32,
    pub radius: f32,
    /// 0..1 chance a candidate point inside the disc becomes a tree.
    pub density: f32,
}

#[derive(Debug, Deserialize)]
pub struct ForestConfig {
    /// 0..1 scaling on the procedural cluster mask — the master "how wooded is
    /// the wild country" dial.
    pub density: f32,
    /// Hand-placed woods, forced in by the Hybrid and Manual placement modes.
    pub named: Vec<NamedForest>,
}

static CONFIG: LazyLock<ClientConfig> = LazyLock::new(|| {
    serde_yaml::from_str(include_str!("../../config/client.yaml"))
        .expect("config/client.yaml is malformed")
});

/// The client config, parsed once.
pub fn config() -> &'static ClientConfig {
    &CONFIG
}

/// Forest placement config.
pub fn forests() -> &'static ForestConfig {
    &CONFIG.forests
}
