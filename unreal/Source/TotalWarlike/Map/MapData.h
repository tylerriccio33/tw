// The baked geometry, loaded once.
//
// `bake/` wrote `Content/Map/*.json` + `terrain.obj` already in Unreal space
// (centimetres, Z up), so nothing here transforms anything — it parses. That is
// the whole point of doing the conversion in the baker: if a coordinate looks
// wrong on screen, there is exactly one place it could have gone wrong, and it
// is not this file.
//
// This is geography. It never changes during a campaign and is never rebuilt
// from a snapshot; only ownership-dependent things (border colours, markers) are.

#pragma once

#include "CoreMinimal.h"

/// One border segment between two provinces, draped on the terrain.
struct FBorderSegment
{
    int32 A = INDEX_NONE;
    int32 B = INDEX_NONE;
    TArray<FVector> Points;
};

struct FRiverPolyline
{
    TArray<FVector> Points;
    /// Centimetres. Widens downstream, straight from the flow-accumulation pass.
    float Width = 0.0f;
};

struct FForestInstance
{
    FVector Position = FVector::ZeroVector;
    float Scale = 1.0f;
    float YawDegrees = 0.0f;
};

/// A settlement's baked ground position. The simulation knows this province only
/// as an id; the position lives here and nowhere in Python.
struct FProvinceSite3D
{
    int32 Id = INDEX_NONE;
    FString Name;
    FVector Position = FVector::ZeroVector;
};

/// Everything `bake/` produced, in one value.
struct FMapData
{
    /// Load from a directory holding the five baked files. Returns false and
    /// fills `OutError` if any of them is missing or malformed — authored
    /// content is trusted, so a bad bake should stop the map cold rather than
    /// render half a world.
    bool Load(const FString& MapDir, FString& OutError);

    /// Terrain, as parsed from `terrain.obj`.
    TArray<FVector> TerrainVertices;
    TArray<FVector> TerrainNormals;
    TArray<int32> TerrainIndices;

    TArray<FBorderSegment> Borders;
    TArray<FRiverPolyline> Rivers;
    TArray<FForestInstance> Forests;
    TArray<FProvinceSite3D> Provinces;

    /// Per-faction colour, by FactionId, from `client.yaml`.
    TArray<FLinearColor> FactionColors;

    /// Half-extents of the sea plane, in centimetres.
    FVector2D MapExtent = FVector2D::ZeroVector;

    /// Godot units per centimetre-of-Unreal, i.e. 100. Every constant ported
    /// from `Main.gd` is in Godot units and has to be divided through by this.
    double WorldScale = 100.0;

    /// `terrain.rs::EXAG`. Carried purely so the terrain material's comments can
    /// name the number its height thresholds are calibrated against — see
    /// Shaders/TerrainCommon.ush.
    double TerrainExag = 0.025;

    /// Colour for `Faction`, falling back to grey for TW_NONE and for a faction
    /// the bake did not know about.
    FLinearColor ColorFor(int32 Faction) const;

    /// Ground position of a province's settlement, or the origin if unknown.
    FVector SiteOf(int32 Province) const;
};
