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


def _is_commandlet() -> bool:
    """True under `UnrealEditor-Cmd -run=pythonscript`. There is no level
    viewport in a commandlet, so `set_level_viewport_camera_info` — which
    reaches into the level-editor viewport client — is a fatal engine assert
    there (same class of bug as the Landscape actor-factory spawn); the
    high-res screenshot only needs the camera actor anyway, so it's skipped."""
    return "-run=pythonscript" in unreal.SystemLibrary.get_command_line()


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


_RT_PACKAGE = f"{config.GENERATED_PACKAGE}/RenderTargets"


def _render_target(res: tuple[int, int]) -> unreal.TextureRenderTarget2D:
    """A real (asset-backed) render target, not `RenderingLibrary
    .create_render_target2d`'s transient one — the transient object is
    unrooted, so the engine's GC can free it between `capture_scene()` and
    the export call (silently: 'render target has been released', no
    exception, no file). Re-created and overwritten per shot; not meant to be
    checked in — same spirit as the other `Generated/` assets-as-code."""
    rt_path = f"{_RT_PACKAGE}/RT_Shot"
    rt = unreal.load_asset(rt_path)
    if rt is None:
        # `create_asset` on an already-existing package would prompt to
        # overwrite; unattended runs can't answer that and it silently
        # returns None instead — only create once per process, reuse after.
        asset_tools = unreal.AssetToolsHelpers.get_asset_tools()
        rt = asset_tools.create_asset(
            "RT_Shot", _RT_PACKAGE, unreal.TextureRenderTarget2D, unreal.TextureRenderTargetFactoryNew()
        )
    rt.set_editor_property("render_target_format", unreal.TextureRenderTargetFormat.RTF_RGBA8)
    # set_editor_property on size_x/y alone doesn't (re)create the GPU
    # resource; ResizeTarget does, and is what the earlier transient
    # create_render_target2d() path was implicitly missing too.
    unreal.RenderingLibrary.resize_render_target2d(rt, res[0], res[1])
    return rt


def _snap(cam: unreal.CineCameraActor, out, res: tuple[int, int]) -> None:
    """Render `cam`'s view to `out` via a scene capture, not the automation
    testing screenshot path. `AutomationLibrary.take_high_res_screenshot`
    reaches into the level-editor viewport client for its capture, which
    doesn't exist in a commandlet — a fatal SIGSEGV there, not a Python
    exception (same class of bug as the Landscape actor-factory spawn and the
    old `set_level_viewport_camera_info` call). A `SceneCaptureComponent2D`
    renders directly from the camera actor's own transform, so it works both
    headless and live."""
    world = unreal.EditorLevelLibrary.get_editor_world()
    rt = _render_target(res)
    actor = _scene.spawn(
        unreal.SceneCapture2D,
        cam.get_actor_location(),
        cam.get_actor_rotation(),
        layer="camera",
        label="TW_Capture",
    )
    capture = actor.capture_component2d
    capture.set_editor_property("texture_target", rt)
    capture.set_editor_property("capture_source", unreal.SceneCaptureSource.SCS_FINAL_COLOR_LDR)
    cam_comp = cam.get_cine_camera_component()
    capture.set_editor_property("fov_angle", cam_comp.get_editor_property("field_of_view"))
    capture.capture_scene()
    unreal.RenderingLibrary.export_render_target(world, rt, str(out.parent), out.name)


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
        if not _is_commandlet():
            # Make the level viewport look through this camera too, for parity
            # when eyeballing a `make live` session.
            unreal.EditorLevelLibrary.set_level_viewport_camera_info(
                cam.get_actor_location(), cam.get_actor_rotation()
            )
        _snap(cam, out, res)
        _scene.clear("camera")
        written.append(str(out))
        unreal.log(f"[tw] shot {shot.name} -> {out}")
    return written
