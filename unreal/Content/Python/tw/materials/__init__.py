"""Assets-as-code: the material graphs, built through
`unreal.MaterialEditingLibrary` and saved under ``/Game/Generated/Materials``.

Materials are the one thing that genuinely needs a ``.uasset``; keeping them as a
re-runnable Python graph — rather than hand-authored in the editor — is what gives
them a reviewable, diffable source, and what puts a look change in a PR. Run all
of them with ``twctl assets`` (or `tw.materials.build_all()`).
"""

from __future__ import annotations

from . import border, terrain, water


def build_all() -> None:
    terrain.build()
    water.build()
    border.build()
