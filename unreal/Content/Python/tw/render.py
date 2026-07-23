"""Render the preset shots to unreal/Shots/current/.

Headless, fixed-camera, one PNG per preset — the frames the golden loop diffs. A
`CineCameraActor` is placed at the preset transform and used as the high-res
screenshot's view, so the output is independent of wherever an interactive
viewport happens to be pointing.
"""

from __future__ import annotations

import unreal

from . import _scene, config, presets

RES_X = 1600
RES_Y = 900


def _camera(shot: presets.Shot) -> unreal.CineCameraActor:
    cam = _scene.spawn(
        unreal.CineCameraActor,
        unreal.Vector(*shot.location),
        unreal.Rotator(shot.rotation[0], shot.rotation[1], shot.rotation[2]),
        layer="camera",
        label=f"TW_Cam_{shot.name}",
    )
    comp = cam.get_cine_camera_component()
    comp.set_editor_property("current_focal_length", 12.0)
    filmback = comp.get_editor_property("filmback")
    # Turn the desired horizontal FOV into a focal length for the 24.89mm sensor.
    import math

    sensor_w = filmback.get_editor_property("sensor_width")
    comp.set_editor_property(
        "current_focal_length", sensor_w / (2.0 * math.tan(math.radians(shot.fov) / 2.0))
    )
    return cam


def shoot(names: list[str] | None = None, res: tuple[int, int] = (RES_X, RES_Y)) -> list[str]:
    """Render the named presets (all of them by default). Returns the paths
    written. Existing PNGs for the requested presets are removed first so a
    missing output is a hard failure, never a stale frame."""
    shots = presets.all_shots() if not names else presets.by_name(names)
    config.SHOTS_CURRENT.mkdir(parents=True, exist_ok=True)

    written: list[str] = []
    for shot in shots:
        out = config.SHOTS_CURRENT / f"{shot.name}.png"
        out.unlink(missing_ok=True)
        cam = _camera(shot)
        # Make the level viewport look through this camera, then snap it.
        unreal.EditorLevelLibrary.set_level_viewport_camera_info(
            cam.get_actor_location(), cam.get_actor_rotation()
        )
        unreal.AutomationLibrary.take_high_res_screenshot(
            res[0], res[1], str(out), cam
        )
        _scene.clear("camera")
        written.append(str(out))
        unreal.log(f"[tw] shot {shot.name} -> {out}")
    return written
