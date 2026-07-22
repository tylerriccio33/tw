// The one piece of `bake/src/geom/regions.rs` that could not be baked.
//
// Province territory is a Voronoi over settlement positions, so "which province
// is under this point" is just "which settlement is nearest". The *borders* of
// those cells are static and ship as `province_borders.json`, but the hit test
// itself has to run on every click, so it is ported rather than baked.
//
// The Rust original also returned `None` at sea, via `terrain::height(x, z) <= 0`.
// That check does not come across: in Unreal the click is a line trace, so
// hitting the terrain mesh at all *is* the land test, and the sea is a separate
// plane the trace can be told to ignore. Feed this only points that hit land.
//
// Deliberately free of Unreal types so it can be unit-tested standalone; the
// actor layer converts to/from FVector.

#pragma once

#include <cstddef>
#include <vector>

namespace tw
{
/// A settlement's position on the ground plane, in Unreal world centimetres.
/// Loaded from the `provinces` array of `Content/Map/provinces.json`, in
/// ProvinceId order — the same order as the simulation's province table.
struct FProvinceSite
{
    double X = 0.0;
    double Y = 0.0;
};

/// Index of the province whose territory contains (X, Y), or `kNoProvince` if
/// there are no sites loaded.
///
/// Ties break toward the lower ProvinceId, matching the Rust `min_by`, which
/// keeps the first of equal elements.
constexpr std::size_t kNoProvince = static_cast<std::size_t>(-1);

std::size_t ProvinceAt(const std::vector<FProvinceSite>& Sites, double X, double Y);

} // namespace tw
