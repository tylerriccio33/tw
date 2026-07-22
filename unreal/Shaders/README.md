# Terrain material

`TerrainCommon.ush` is the campaign terrain shader, ported line-for-line from
`godot/Main.gd:_terrain_shader()`. It is the authority; the material asset that
calls it is a four-node wrapper.

The wrapper is the one thing in this project that cannot be produced from a
shell — a `UMaterial` with a Custom node is a binary `.uasset`. Everything else
(the terrain mesh, borders, rivers, forests, markers, HUD) is built in C++ at
runtime, exactly as `Main.gd` built its scene in code. `ACampaignMap` looks the
material up by soft path and falls back to a flat green if it is missing, so the
vertical slice runs either way; it just looks like clay until you do this once.

## One-time setup in the editor

1. Content Browser → `Content/Map/` → new **Material**, named `M_Terrain`.
   (`ACampaignMap::TerrainMaterialPath` expects `/Game/Map/M_Terrain`.)
2. Material settings: Shading Model **Default Lit**, Two Sided **off**,
   Tangent Space Normal **off** — `TWTerrainShade` returns a world-space normal.
3. Add a **Texture Object** node holding the baked fbm noise (below), and a
   **Scalar Parameter** `WorldScale` defaulting to `100`.
4. Add a **Custom** node:
   - Output Type: `CMOT Float3`
   - Inputs: `NoiseTex` (the texture object), `WorldScale`
   - Additional Defines / Include File Paths: `/Project/TerrainCommon.ush`
   - Code:
     ```
     float3 N; float R;
     float3 C = TWTerrainShade(GetWorldPosition(Parameters), Parameters.WorldNormal,
                               WorldScale, NoiseTex, NoiseTexSampler, N, R);
     return C;
     ```
   Wire its output to **Base Color**. Repeat with two more Custom nodes (or one
   node returning float4 and a second for the normal) for **Normal** and
   **Roughness** — `TWTerrainShade` computes all three in one pass, so prefer a
   single node writing to a `MaterialFloat3` local if you are comfortable with
   Custom node scoping.

`/Project/` maps to this directory. The engine sets that mapping up itself for
`<project>/Shaders`; `FTotalWarlikeModule::StartupModule` only re-adds it if it
is somehow absent, because `AddShaderSourceDirectoryMapping` asserts on a
duplicate instead of overwriting.

## The noise texture

`Main.gd` bakes a 1024px seamless 5-octave value-noise fbm (lacunarity 2, gain
0.5, frequency 0.01), normalized, with mipmaps, wrap-repeat, linear-mipmap
filtering. Mipmaps are not optional: the camera looks across the map at a low
oblique angle, and without them the minified texels alias into swimming static.

Any equivalent seamless fbm will do. The shader only reads `.r`.

## If you retune EXAG

`bake/`'s `EXAG` (0.025) silently calibrates every height *and slope* threshold
in `TerrainCommon.ush`. The header comment there explains the arithmetic. There
is no way for the compiler to catch this; it is a comment and a habit.
