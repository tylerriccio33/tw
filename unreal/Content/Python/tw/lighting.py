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
        # Keyword these: unreal.Rotator's positional order is (roll, pitch, yaw),
        # so (-42, 30, 0) read as roll=-42/pitch=+30 aimed the sun 30 degrees at
        # the *sky* — the terrain got no direct light and every shot came back a
        # black silhouette under a lit fog gradient.
        unreal.Rotator(pitch=-42.0, yaw=30.0, roll=0.0),  # down from the SE
        layer="lighting",
        label="TW_Sun",
    )
    sun.light_component.set_intensity(9.0)
    sun.light_component.set_light_color(unreal.LinearColor(255 / 255, 246 / 255, 224 / 255, 1.0))
    sun.light_component.set_editor_property("atmosphere_sun_light", True)

    _scene.spawn(unreal.SkyAtmosphere, layer="lighting", label="TW_SkyAtmosphere")

    skylight = _scene.spawn(unreal.SkyLight, layer="lighting", label="TW_SkyLight")
    skylight.light_component.set_editor_property("real_time_capture", True)
    # Sky ambient has to stay well under the sun or the terrain gets lit evenly
    # from every direction and the relief flattens out — at 1.0 against a 9 lux
    # sun there was no readable slope shading at all.
    skylight.light_component.set_intensity(0.55)

    fog = _scene.spawn(
        unreal.ExponentialHeightFog, layer="lighting", label="TW_Fog"
    )
    fc = fog.component
    # The map is ~1 km across and the cameras sit a few hundred metres up, so fog
    # density is a blunt instrument here: 0.012 with a 0.06 falloff put the whole
    # island behind a wall of blue-grey inscattering. The reference has crisp
    # terrain to the horizon with only a light warm haze in the far distance.
    fc.set_editor_property("fog_density", 0.0006)
    fc.set_editor_property("fog_height_falloff", 0.2)
    fc.set_fog_inscattering_color(unreal.LinearColor(0.42, 0.47, 0.55, 1.0))

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
    # Exposure-compensation EV applied on the manual exposure — in UE *higher is
    # brighter* (it is added, not an EV100 target). 11 with the old near-black
    # palette was fine; at the terrain's true albedo the scene wants ~2.5 stops
    # less or the whole map clips to a milky white-green.
    settings.set_editor_property("auto_exposure_bias", 10.5)

    settings.set_editor_property("override_color_saturation", True)
    settings.set_editor_property("color_saturation", unreal.Vector4(1.06, 1.06, 1.06, 1.0))
    settings.set_editor_property("override_color_contrast", True)
    settings.set_editor_property("color_contrast", unreal.Vector4(1.12, 1.12, 1.12, 1.0))

    settings.set_editor_property("override_bloom_intensity", True)
    settings.set_editor_property("bloom_intensity", 0.20)
    settings.set_editor_property("override_vignette_intensity", True)
    settings.set_editor_property("vignette_intensity", 0.18)

    ppv.set_editor_property("settings", settings)
    return ppv
