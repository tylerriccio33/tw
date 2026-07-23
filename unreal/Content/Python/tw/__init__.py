"""tw — the Python toolkit that builds and drives the campaign map inside Unreal.

Import submodules explicitly (``from tw import world`` etc.). This top level is
deliberately empty of engine imports so the engine-free pieces — the sim bridge
and its msgpack codec, the config paths — can be imported and unit-tested by a
plain Python interpreter outside the editor.
"""

__all__ = [
    "config",
    "simbridge",
    "landscape",
    "water",
    "forests",
    "borders",
    "markers",
    "lighting",
    "render",
    "presets",
    "world",
    "campaign",
    "materials",
]
