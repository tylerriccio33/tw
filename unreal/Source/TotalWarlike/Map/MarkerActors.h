// The two things on the map that belong to the simulation.
//
// Both actors STORE ONLY AN ID. They do not read rules, do not decide anything,
// and never call the transport. `ACampaignMap` spawns them, refreshes them from
// a snapshot, and destroys them; if you ever find yourself wanting an
// `AArmyActor::CanMoveTo`, that question belongs to `sim/src/tw_sim/rules.py`.
//
// The meshes are engine primitives assembled in code, exactly as `Main.gd`
// assembled its markers from BoxMesh/CylinderMesh/TorusMesh. That keeps the
// project free of authored content assets and keeps the silhouette — a huddle
// of houses, ranks of soldiers under a banner — recognisable from the Godot
// build.

#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"

#include "MarkerActors.generated.h"

class UStaticMeshComponent;
class UMaterialInstanceDynamic;

/// Shared plumbing: a root, a bag of primitive components, and one dynamic
/// material whose colour is re-set on every refresh rather than rebuilt.
UCLASS(Abstract)
class ATWMarkerActor : public AActor
{
    GENERATED_BODY()

public:
    ATWMarkerActor();

    /// The simulation id this marker stands for. The only piece of game state
    /// the actor is allowed to hold.
    UPROPERTY(VisibleAnywhere, BlueprintReadOnly, Category = "Sim")
    int32 SimId = INDEX_NONE;

protected:
    /// Spawn one primitive from `/Engine/BasicShapes`, tinted and parented to
    /// the root. `Shape` is "Cube", "Cylinder", "Cone", "Sphere" or "Plane".
    UStaticMeshComponent* AddShape(const TCHAR* Shape, const FVector& Location,
                                   const FVector& Scale, const FLinearColor& Color,
                                   float Yaw = 0.0f, bool bGlow = false);

    /// Re-tint everything built by `AddShape` that was flagged as faction-coloured.
    void SetFactionColor(const FLinearColor& Color, bool bGlow);

    /// Deterministic per-marker randomness. The Godot client seeded from the id
    /// for a reason: the scene is rebuilt whenever anything changes, and an
    /// unseeded RNG would reshuffle every town's houses each time an army moved.
    FRandomStream StreamFor(int32 Id) const { return FRandomStream(GetTypeHash(Id)); }

    /// Components that follow the owner's colour, as opposed to the stone and
    /// steel ones that do not.
    UPROPERTY()
    TArray<UStaticMeshComponent*> TintedParts;
};

/// A settlement: a faction-coloured base disc, a huddle of houses, a wall ring
/// if it has walls, and a banner. Size follows the simulation's SettlementTier.
UCLASS()
class ACityActor : public ATWMarkerActor
{
    GENERATED_BODY()

public:
    /// Build the geometry. Called once, on spawn; `Tier` and `Walls` are fixed
    /// enough in practice that a wall level changing mid-campaign triggers a
    /// full rebuild rather than an incremental edit.
    void BuildFor(int32 InProvince, int32 Tier, int32 Walls, const FLinearColor& Color);

    /// Cheap per-snapshot update — colour only.
    void Refresh(const FLinearColor& Color, bool bSelected);

    int32 GetTier() const { return CachedTier; }
    int32 GetWalls() const { return CachedWalls; }

private:
    int32 CachedTier = 0;
    int32 CachedWalls = 0;
};

/// An army: ranks of soldier blocks under a hedge of spears, with a banner.
UCLASS()
class AArmyActor : public ATWMarkerActor
{
    GENERATED_BODY()

public:
    AArmyActor();

    void BuildFor(int32 InArmy, int32 Size, const FLinearColor& Color);
    void Refresh(const FLinearColor& Color, bool bSelected);

    int32 GetSize() const { return CachedSize; }

    /// Start gliding to `Target`. Mirrors `Main.gd:_march_army` — the simulation
    /// has ALREADY applied the move and the snapshot already reports the
    /// destination, so this is presentation catching up, never a source of truth.
    /// A marker that is interrupted mid-march simply snaps, because the snapshot
    /// is what is correct.
    void MarchTo(const FVector& Target);

    /// True while a march is playing. The controller blocks input on it, the
    /// same way `Main.gd` set `playing`.
    bool IsMarching() const { return bMarching; }

    virtual void Tick(float DeltaSeconds) override;

private:
    int32 CachedSize = 0;

    bool bMarching = false;
    float MarchElapsed = 0.0f;
    FVector MarchFrom = FVector::ZeroVector;
    FVector MarchTo_ = FVector::ZeroVector;
};
