# Scripts — assets as code

Almost everything in this project is constructed in code: the world is spawned by
`ACampaignGameMode`, the geometry is baked to JSON, the render is configured in
`Config/DefaultEngine.ini`. The one thing that resists that is a handful of assets
that genuinely need a real `.uasset` — the terrain material's Custom HLSL node,
the depth-aware water shader. Historically those were flagged in the source as
"milestone 2, needs an authored asset" and left there, because authoring in the
editor is not a step an agent can take or a reviewer can diff.

This directory closes that gap. Each script here builds an asset **programmatically**
through Unreal's editor Python API (`unreal.AssetToolsHelpers`, `MaterialFactoryNew`,
`MaterialEditingLibrary`, …) and writes it under `Content/`. The script is the
reviewable, re-runnable source of the asset — the same contract the baker gives the
geometry.

```
make unreal-pyscript SCRIPT=gen_water_material.py
```

runs one against a headless editor (`-run=pythonscript -nullrhi`). Commit the
generated `.uasset` alongside the script that produced it, and note the script in
the asset's package comment so the two never drift.

## Conventions

- One asset (or one tightly related set) per script; name it `gen_<thing>.py`.
- Idempotent: overwrite the target package rather than erroring if it exists, so a
  re-run is always safe.
- Save with `unreal.EditorAssetLibrary.save_asset` and log the package path, so the
  make target's stdout shows what it wrote.
- After generating a visual asset, re-render `make unreal-shots` — the golden loop
  is what confirms the asset actually looks the way the script intended.
