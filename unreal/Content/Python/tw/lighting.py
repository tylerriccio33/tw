"""Lighting and the code-owned cinematic grade.

The sun, sky atmosphere, sky light and height fog give the warm, hazy midday of
target-state.png; an **unbound** post-process volume carries the whole-map grade
(manual exposure, filmic contrast/saturation, a little bloom and vignette).
Keeping the grade in code — not an authored volume — is what puts it in every
`twctl shot` and lets it diff in a PR.
"""

from __future__ import annotations

import unreal

from . import _scene


def build() -> None:
    _scene.clear("lighting")

    sun = _scene.spawn(
        unreal.DirectionalLight,
        unreal.Vector(0, 0, 50_000),
        unreal.Rotator(-42.0, 30.0, 0.0),  # pitch down from the SE
        layer="lighting",
        label="TW_Sun",
    )
    sun.light_component.set_intensity(6.0)
    sun.light_component.set_light_color(unreal.LinearColor(255 / 255, 246 / 255, 224 / 255, 1.0))
    sun.light_component.set_editor_property("atmosphere_sun_light", True)

    _scene.spawn(unreal.SkyAtmosphere, layer="lighting", label="TW_SkyAtmosphere")

    skylight = _scene.spawn(unreal.SkyLight, layer="lighting", label="TW_SkyLight")
    skylight.light_component.set_editor_property("real_time_capture", True)
    skylight.light_component.set_intensity(1.0)

    fog = _scene.spawn(
        unreal.ExponentialHeightFog, layer="lighting", label="TW_Fog"
    )
    fc = fog.component
    fc.set_editor_property("fog_density", 0.012)
    fc.set_editor_property("fog_height_falloff", 0.06)
    fc.set_fog_inscattering_color(unreal.LinearColor(0.42, 0.51, 0.62, 1.0))

    _build_grade()


def _build_grade() -> unreal.Actor:
    ppv = _scene.spawn(
        unreal.PostProcessVolume, layer="lighting", label="TW_Grade"
    )
    ppv.set_editor_property("unbound", True)  # affects the whole map
    settings = unreal.PostProcessSettings()

    settings.set_editor_property("override_auto_exposure_method", True)
    settings.set_editor_property("auto_exposure_method", unreal.AutoExposureMethod.AEM_MANUAL)
    settings.set_editor_property("override_auto_exposure_bias", True)
    settings.set_editor_property("auto_exposure_bias", 11.0)  # manual EV100

    settings.set_editor_property("override_color_saturation", True)
    settings.set_editor_property("color_saturation", unreal.Vector4(1.12, 1.12, 1.12, 1.0))
    settings.set_editor_property("override_color_contrast", True)
    settings.set_editor_property("color_contrast", unreal.Vector4(1.06, 1.06, 1.06, 1.0))

    settings.set_editor_property("override_bloom_intensity", True)
    settings.set_editor_property("bloom_intensity", 0.35)
    settings.set_editor_property("override_vignette_intensity", True)
    settings.set_editor_property("vignette_intensity", 0.35)

    ppv.set_editor_property("settings", settings)
    return ppv
