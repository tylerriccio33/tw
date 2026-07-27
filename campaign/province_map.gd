extends Node2D
## Political background of the campaign map: a flat-colored region bitmap
## (campaign/map_data/region_map.png, one solid color per province) turned
## into real polygon geometry at load time, exactly as Thomas Holtvedt's
## grand-strategy-simple tutorial does it - scan every pixel color, trace
## each color's opaque region into polygons with BitMap.opaque_to_polygons(),
## and give each polygon an Area2D so it can hover-highlight and take clicks.
## No shader, no Voronoi cells - the map's provinces are exactly the blobs of
## color the source image already draws.

signal region_clicked(region_name: String)

const RegionArea := preload("res://campaign/region_area.gd")
const MAP_TEXTURE := preload("res://campaign/map_data/region_map.png")
const REGIONS_FILE := "res://campaign/map_data/regions.txt"

## region name -> centroid of its polygon points, in the source image's own
## pixel space (top-left origin, +y down). The caller decides how to place
## that into world space.
var region_centers: Dictionary = {}
var map_size := Vector2.ZERO


func setup() -> void:
	var image := MAP_TEXTURE.get_image()
	map_size = image.get_size()

	var pixel_color_dict := _get_pixel_color_dict(image)
	var regions_by_color := _import_regions(REGIONS_FILE)

	var regions_root := Node2D.new()
	regions_root.name = "Regions"
	add_child(regions_root)

	for region_color in regions_by_color:
		if not pixel_color_dict.has(region_color):
			continue

		var region := Area2D.new()
		region.set_script(RegionArea)
		region.region_name = regions_by_color[region_color]
		region.name = region_color
		region.clicked.connect(func(name: String): region_clicked.emit(name))
		regions_root.add_child(region)

		var polygons := _get_polygons(image, region_color, pixel_color_dict)
		var centroid_sum := Vector2.ZERO
		var centroid_count := 0
		for polygon in polygons:
			var collision := CollisionPolygon2D.new()
			var fill := Polygon2D.new()
			collision.polygon = polygon
			fill.polygon = polygon
			region.add_child(collision)
			region.add_child(fill)
			for p in polygon:
				centroid_sum += p
				centroid_count += 1

		if centroid_count > 0:
			region_centers[region.region_name] = centroid_sum / centroid_count

	var sprite := Sprite2D.new()
	sprite.name = "Sprite2D"
	sprite.texture = MAP_TEXTURE
	sprite.centered = false
	add_child(sprite)
	move_child(sprite, 0)


func _get_pixel_color_dict(image: Image) -> Dictionary:
	var pixel_color_dict := {}
	for y in range(image.get_height()):
		for x in range(image.get_width()):
			var pixel_color := "#" + image.get_pixel(x, y).to_html(false)
			if not pixel_color_dict.has(pixel_color):
				pixel_color_dict[pixel_color] = []
			pixel_color_dict[pixel_color].append(Vector2(x, y))
	return pixel_color_dict


func _get_polygons(image: Image, region_color: String, pixel_color_dict: Dictionary) -> Array:
	var target_image := Image.create(
		image.get_size().x, image.get_size().y, false, Image.FORMAT_RGBA8
	)
	for value in pixel_color_dict[region_color]:
		target_image.set_pixel(int(value.x), int(value.y), Color.WHITE)

	var bitmap := BitMap.new()
	bitmap.create_from_image_alpha(target_image)
	return bitmap.opaque_to_polygons(Rect2(Vector2.ZERO, bitmap.get_size()), 0.1)


## Imports regions.txt - a JSON object of "#hexcolor": "Region Name" pairs
## (with underscores standing in for spaces, same as the tutorial's format).
func _import_regions(filepath: String) -> Dictionary:
	var file := FileAccess.open(filepath, FileAccess.READ)
	if file == null:
		printerr("province_map: failed to open ", filepath)
		return {}
	return JSON.parse_string(file.get_as_text().replace("_", " "))
