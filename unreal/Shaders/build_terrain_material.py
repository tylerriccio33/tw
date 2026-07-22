"""One-time headless setup for Content/Map/M_Terrain. Run via:
UnrealEditor <uproject> -run=pythonscript -script=unreal/Shaders/build_terrain_material.py -unattended -nosplash -nullrhi
See unreal/Shaders/README.md for what this recreates from the editor GUI.
"""
import unreal

NOISE_SRC = "/Users/tylerriccio/Desktop/tw/unreal/Shaders/terrain_noise_src.png"
TEX_PACKAGE_PATH = "/Game/Map"
TEX_NAME = "T_TerrainNoise"
MAT_NAME = "M_Terrain"

asset_tools = unreal.AssetToolsHelpers.get_asset_tools()

# --- Import the noise texture ---
tex_path = f"{TEX_PACKAGE_PATH}/{TEX_NAME}"
if unreal.EditorAssetLibrary.does_asset_exist(tex_path):
    unreal.EditorAssetLibrary.delete_asset(tex_path)

import_task = unreal.AssetImportTask()
import_task.filename = NOISE_SRC
import_task.destination_path = TEX_PACKAGE_PATH
import_task.destination_name = TEX_NAME
import_task.replace_existing = True
import_task.automated = True
import_task.save = True
import_task.factory = unreal.TextureFactory()

unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([import_task])

texture = unreal.load_asset(tex_path)
assert texture, "texture import failed"
texture.set_editor_property("srgb", False)
texture.set_editor_property("compression_settings", unreal.TextureCompressionSettings.TC_GRAYSCALE)
texture.set_editor_property("mip_gen_settings", unreal.TextureMipGenSettings.TMGS_FROM_TEXTURE_GROUP)
sampler = texture.get_editor_property("address_x")
texture.set_editor_property("address_x", unreal.TextureAddress.TA_WRAP)
texture.set_editor_property("address_y", unreal.TextureAddress.TA_WRAP)
texture.set_editor_property("filter", unreal.TextureFilter.TF_TRILINEAR)
unreal.EditorAssetLibrary.save_asset(tex_path)

# --- Create the material ---
mat_path = f"{TEX_PACKAGE_PATH}/{MAT_NAME}"
if unreal.EditorAssetLibrary.does_asset_exist(mat_path):
    unreal.EditorAssetLibrary.delete_asset(mat_path)

mat_factory = unreal.MaterialFactoryNew()
material = asset_tools.create_asset(MAT_NAME, TEX_PACKAGE_PATH, unreal.Material, mat_factory)
assert material, "material creation failed"

material.set_editor_property("two_sided", False)
material.set_editor_property("tangent_space_normal", False)

mel = unreal.MaterialEditingLibrary

# Texture Object node
tex_obj = mel.create_material_expression(material, unreal.MaterialExpressionTextureObject, -600, -100)
tex_obj.set_editor_property("texture", texture)

# WorldScale scalar parameter
world_scale = mel.create_material_expression(material, unreal.MaterialExpressionScalarParameter, -600, 100)
world_scale.set_editor_property("parameter_name", "WorldScale")
world_scale.set_editor_property("default_value", 100.0)

# Custom node calling TWTerrainShade
custom = mel.create_material_expression(material, unreal.MaterialExpressionCustom, -300, 0)
custom.set_editor_property("output_type", unreal.CustomMaterialOutputType.CMOT_FLOAT3)
custom.set_editor_property("include_file_paths", ["/Project/TerrainCommon.ush"])
# Parameters.TangentToWorld[2] is the interpolated *vertex* normal, and it is the
# only normal readable here. This node feeds the Normal pin, and the Normal chain
# runs before Parameters.WorldNormal is assigned (MaterialTemplate.ush: the normal
# block at ~4340, the assignment at ~4373), so Parameters.WorldNormal is still the
# zero the struct was initialised with. Normalising that yields NaN, which lands on
# OutNormal and then comes back through Parameters.WorldNormal when the Base Color
# chain runs after it — so the whole shade goes NaN and saturates to black. This is
# what VertexNormalWS lowers to, and it is what TWTerrainShade wants to perturb.
custom.set_editor_property("code",
    "float3 C = TWTerrainShade(LWCToFloat(GetWorldPosition(Parameters)), "
    "Parameters.TangentToWorld[2], "
    "WorldScale, NoiseTex, NoiseTexSampler, OutNormal, OutRoughness);\n"
    "return C;"
)

in_noise = unreal.CustomInput()
in_noise.set_editor_property("input_name", "NoiseTex")

in_scale = unreal.CustomInput()
in_scale.set_editor_property("input_name", "WorldScale")

custom.set_editor_property("inputs", [in_noise, in_scale])

mel.connect_material_expressions(tex_obj, "", custom, "NoiseTex")
mel.connect_material_expressions(world_scale, "", custom, "WorldScale")

out_normal = unreal.CustomOutput()
out_normal.set_editor_property("output_name", "OutNormal")
out_normal.set_editor_property("output_type", unreal.CustomMaterialOutputType.CMOT_FLOAT3)
out_rough = unreal.CustomOutput()
out_rough.set_editor_property("output_name", "OutRoughness")
out_rough.set_editor_property("output_type", unreal.CustomMaterialOutputType.CMOT_FLOAT1)
custom.set_editor_property("additional_outputs", [out_normal, out_rough])

mel.connect_material_property(custom, "", unreal.MaterialProperty.MP_BASE_COLOR)
mel.connect_material_property(custom, "OutNormal", unreal.MaterialProperty.MP_NORMAL)
mel.connect_material_property(custom, "OutRoughness", unreal.MaterialProperty.MP_ROUGHNESS)

mel.recompile_material(material)
unreal.EditorAssetLibrary.save_asset(mat_path)

print("M_Terrain build complete:", mat_path)
