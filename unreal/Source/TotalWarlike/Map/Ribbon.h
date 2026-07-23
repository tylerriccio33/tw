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
/// `LateralOffset` shifts the whole centreline sideways (flat, along the same
/// heading normal the width uses) — positive is left of travel. It exists so a
/// frontier can be drawn as two parallel ribbons, one per owning faction's
/// colour, each hugging its own side of the line.
///
/// Emits nothing for a polyline of fewer than two points.
void BuildRibbon(const TArray<FVector>& Points, float Width, float ZBias,
                 TArray<FVector>& OutVertices, TArray<int32>& OutTriangles,
                 TArray<FVector>& OutNormals, float LateralOffset = 0.0f);

} // namespace tw
