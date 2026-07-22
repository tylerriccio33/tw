// The whole visible world, and the only thing that turns a snapshot into actors.
//
// WHY THERE IS NO CONTENT/ TO SPEAK OF
// ------------------------------------
// `Main.gd` built its entire scene in code — no .tscn, nothing to drift out of
// step with the engine. This keeps that property: the terrain, borders, rivers,
// forests and markers are all constructed at BeginPlay from the baked JSON in
// Content/Map/, so the repo holds data files and source, not binary assets. The
// single exception is the terrain material, which needs a Custom HLSL node and
// therefore a real .uasset; see Shaders/README.md. It is looked up softly and
// falls back to a flat colour, so the slice runs without it.
//
// LAYERS
// ------
// Static geography (terrain, rivers, forests) is built once and never touched
// again. Only the ownership-dependent layers rebuild: border colours, which
// depend on whether the two sides of a line share an owner, and the markers.
//
// Markers are synced by DIFFING ID SETS, not by clearing and rebuilding. That is
// the difference between an army that glides to its new province and one that
// blinks, and it is also what makes "no actor growth over 10 turns" a property
// rather than a hope.

#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"

#include "../Sim/SimTypes.h"
#include "MapData.h"

#include "CampaignMap.generated.h"

class AArmyActor;
class ACityActor;
class UHierarchicalInstancedStaticMeshComponent;
class UMaterialInterface;
class UProceduralMeshComponent;
class USimSubsystem;

UCLASS()
class ACampaignMap : public AActor
{
    GENERATED_BODY()

public:
    ACampaignMap();

    virtual void BeginPlay() override;

    /// Ground height in centimetres at a world XY, by tracing down onto the
    /// terrain. Returns false over open sea (where nothing was hit).
    ///
    /// A trace rather than an interpolation of the baked grid, deliberately:
    /// the click path already needs a trace, and one mechanism cannot disagree
    /// with itself about where the ground is.
    bool GroundAt(double X, double Y, double& OutZ) const;

    /// The province whose territory contains a world XY, or INDEX_NONE at sea.
    /// Thin wrapper over `tw::ProvinceAt` — see Map/ProvinceLookup.h for why the
    /// hit test is the one piece of regions.rs that was ported rather than baked.
    int32 ProvinceAt(double X, double Y) const;

    const FMapData& GetMapData() const { return Map; }

    /// What the player has picked. Purely presentational: it tints markers and
    /// lightens the provinces a selected army can reach. The controller owns the
    /// decision; the map owns the drawing of it.
    void SetSelection(int32 InArmy, int32 InProvince);

    int32 GetSelectedArmy() const { return SelectedArmy; }
    int32 GetSelectedProvince() const { return SelectedProvince; }

    /// True while any marker is mid-march. The controller blocks clicks on it.
    bool IsAnimating() const;

    /// Where a marker for `Province` sits, ground height included.
    FVector MarkerLocationFor(int32 Province) const;

private:
    void BuildStaticGeography();
    void BuildTerrain();
    void BuildRivers();
    void BuildForests();

    /// Border colour depends on ownership, so this runs on every snapshot.
    /// Two sections — frontier and internal — rather than one per segment,
    /// because 30-odd draw calls for map ink is 30 too many.
    void RebuildBorders(const FWorldSnapshot& Snapshot);

    void SyncMarkers(const FWorldSnapshot& Snapshot);

    void HandleSnapshot(const FWorldSnapshot& Snapshot);

    UPROPERTY() UProceduralMeshComponent* Terrain = nullptr;
    UPROPERTY() UProceduralMeshComponent* Borders = nullptr;
    UPROPERTY() UProceduralMeshComponent* Rivers = nullptr;
    UPROPERTY() UHierarchicalInstancedStaticMeshComponent* TreeTrunks = nullptr;
    UPROPERTY() UHierarchicalInstancedStaticMeshComponent* TreeCanopies = nullptr;

    UPROPERTY() TMap<int32, ACityActor*> Cities;
    UPROPERTY() TMap<int32, AArmyActor*> Armies;

    /// Where each army was standing at the last snapshot, so a move can be
    /// animated from the right place. Keyed by ArmyId; entries are dropped with
    /// their actors.
    TMap<int32, int32> ArmyLocations;

    FMapData Map;

    int32 SelectedArmy = INDEX_NONE;
    int32 SelectedProvince = INDEX_NONE;

    /// The last snapshot, kept so SetSelection can re-tint without a round-trip.
    FWorldSnapshot Latest;
    bool bHaveSnapshot = false;

    FDelegateHandle SnapshotHandle;

    USimSubsystem* Sim() const;
};
