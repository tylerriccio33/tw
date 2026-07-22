// A draped polyline turned into flat ground geometry.
//
// Ported from `Main.gd:_ribbon`. The reason it exists is the same in both
// engines: a one-pixel line is invisible at campaign zoom, so a border thick
// enough to read has to be actual triangles lying on the terrain.
//
// The Godot original offset sideways in XZ with Y up; here it is XY with Z up.
// That is the only change — the geometry is identical.

#pragma once

#include "CoreMinimal.h"

namespace tw
{

/// Widen `Points` into a triangle strip of half-width `Width * 0.5`, lifted
/// `ZBias` centimetres so it does not z-fight the terrain it is draped on.
///
/// Emits nothing for a polyline of fewer than two points.
void BuildRibbon(const TArray<FVector>& Points, float Width, float ZBias,
                 TArray<FVector>& OutVertices, TArray<int32>& OutTriangles,
                 TArray<FVector>& OutNormals);

} // namespace tw
