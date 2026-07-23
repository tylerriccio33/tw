#include "CampaignMap.h"

#include "Components/HierarchicalInstancedStaticMeshComponent.h"
#include "Engine/StaticMesh.h"
#include "Engine/World.h"
#include "Kismet/GameplayStatics.h"
#include "Materials/MaterialInstanceDynamic.h"
#include "Materials/MaterialInterface.h"
#include "Misc/Paths.h"
#include "ProceduralMeshComponent.h"

#include "../Sim/SimSubsystem.h"
#include "MarkerActors.h"
#include "ProvinceLookup.h"
#include "Ribbon.h"

DEFINE_LOG_CATEGORY_STATIC(LogCampaignMap, Log, All);

namespace
{

/// Godot units to centimetres. Widths ported from Main.gd are in Godot units.
constexpr double GodotToUU = 100.0;

/// Main.gd's BORDER_FRONTIER_W / BORDER_INTERNAL_W, and its INK colour.
constexpr float BorderFrontierWidth = 3.6f * GodotToUU;
constexpr float BorderInternalWidth = 1.3f * GodotToUU;

/// Lift the ink and the water off the ground so they do not z-fight the terrain
/// they are draped on. The DEM is exaggerated 60x, so a few centimetres is
/// nothing in world terms but comfortably more than the depth buffer's slop.
constexpr float InkZBias = 25.0f;
constexpr float RiverZBias = 18.0f;

/// The one authored asset in the project, and the one thing that cannot be built
/// at runtime — a Custom HLSL node needs a real material. Absent, the terrain
/// renders flat green and everything else still works. See Shaders/README.md.
const TCHAR* TerrainMaterialPath = TEXT("/Game/Map/M_Terrain.M_Terrain");

UMaterialInterface* BasicMaterial()
{
    return LoadObject<UMaterialInterface>(
        nullptr, TEXT("/Engine/BasicShapes/BasicShapeMaterial.BasicShapeMaterial"));
}

/// A tinted instance of the engine's basic material. Used for everything that is
/// not the terrain: borders, rivers, foliage.
UMaterialInstanceDynamic* TintedMaterial(UObject* Outer, const FLinearColor& Color)
{
    UMaterialInterface* Base = BasicMaterial();
    if (Base == nullptr)
    {
        return nullptr;
    }
    UMaterialInstanceDynamic* Mid = UMaterialInstanceDynamic::Create(Base, Outer);
    Mid->SetVectorParameterValue(TEXT("Color"), Color);
    Mid->SetVectorParameterValue(TEXT("BaseColor"), Color);
    return Mid;
}

const FLinearColor Ink = FLinearColor::FromSRGBColor(FColor(26, 20, 18));
const FLinearColor Water(0.28f, 0.47f, 0.62f, 0.85f);
const FLinearColor Bark = FLinearColor::FromSRGBColor(FColor(61, 48, 33));
const FLinearColor Canopy = FLinearColor::FromSRGBColor(FColor(51, 77, 36));
const FLinearColor Gold(1.0f, 0.84f, 0.0f);

} // namespace

ACampaignMap::ACampaignMap()
{
    PrimaryActorTick.bCanEverTick = false;
    RootComponent = CreateDefaultSubobject<USceneComponent>(TEXT("Root"));

    Terrain = CreateDefaultSubobject<UProceduralMeshComponent>(TEXT("Terrain"));
    Terrain->SetupAttachment(RootComponent);
    // The terrain is the only thing in the world with collision: every click and
    // every marker placement is a trace onto it.
    Terrain->bUseAsyncCooking = false;
    Terrain->SetCollisionEnabled(ECollisionEnabled::QueryOnly);
    Terrain->SetCollisionObjectType(ECC_WorldStatic);
    Terrain->SetCollisionResponseToAllChannels(ECR_Block);

    Borders = CreateDefaultSubobject<UProceduralMeshComponent>(TEXT("Borders"));
    Borders->SetupAttachment(RootComponent);
    Borders->SetCollisionEnabled(ECollisionEnabled::NoCollision);
    Borders->SetCastShadow(false);

    Rivers = CreateDefaultSubobject<UProceduralMeshComponent>(TEXT("Rivers"));
    Rivers->SetupAttachment(RootComponent);
    Rivers->SetCollisionEnabled(ECollisionEnabled::NoCollision);
    Rivers->SetCastShadow(false);

    TreeTrunks =
        CreateDefaultSubobject<UHierarchicalInstancedStaticMeshComponent>(TEXT("TreeTrunks"));
    TreeTrunks->SetupAttachment(RootComponent);
    TreeCanopies =
        CreateDefaultSubobject<UHierarchicalInstancedStaticMeshComponent>(TEXT("TreeCanopies"));
    TreeCanopies->SetupAttachment(RootComponent);
    for (UHierarchicalInstancedStaticMeshComponent* Wood : {TreeTrunks, TreeCanopies})
    {
        // Trees are dense and small; a shadow from every one is costly and just
        // muddies the ground. The canopy still receives the sun's shading.
        Wood->SetCastShadow(false);
        Wood->SetCollisionEnabled(ECollisionEnabled::NoCollision);
    }
}

USimSubsystem* ACampaignMap::Sim() const
{
    const UGameInstance* GameInstance = UGameplayStatics::GetGameInstance(this);
    return GameInstance != nullptr ? GameInstance->GetSubsystem<USimSubsystem>() : nullptr;
}

void ACampaignMap::BeginPlay()
{
    Super::BeginPlay();

    const FString MapDir = FPaths::Combine(FPaths::ProjectContentDir(), TEXT("Map"));
    FString Error;
    if (!Map.Load(MapDir, Error))
    {
        // Authored content is trusted and a bad bake is a loud failure, exactly
        // as `data.py` treats a bad campaign YAML.
        UE_LOG(LogCampaignMap, Fatal, TEXT("could not load baked map from %s: %s"), *MapDir,
               *Error);
        return;
    }

    BuildStaticGeography();

    if (USimSubsystem* Subsystem = Sim())
    {
        SnapshotHandle = Subsystem->OnSnapshotChanged.AddUObject(this, &ACampaignMap::HandleSnapshot);
        // A campaign may already be running — the editor's attach path reuses a
        // live sidecar across a PIE restart.
        if (!Subsystem->GetSnapshot().IsEmpty())
        {
            HandleSnapshot(Subsystem->GetSnapshot());
        }
    }
}

// ---------------------------------------------------------------------------
//  Static geography
// ---------------------------------------------------------------------------

void ACampaignMap::BuildStaticGeography()
{
    BuildTerrain();
    BuildRivers();
    BuildForests();
}

void ACampaignMap::BuildTerrain()
{
    // CreateMeshSection wants its own arrays even where we have nothing to say.
    const TArray<FVector2D> NoUVs;
    const TArray<FColor> NoColors;
    const TArray<FProcMeshTangent> NoTangents;

    Terrain->CreateMeshSection(0, Map.TerrainVertices, Map.TerrainIndices, Map.TerrainNormals,
                               NoUVs, NoColors, NoTangents, /*bCreateCollision=*/true);

    if (UMaterialInterface* Authored =
            LoadObject<UMaterialInterface>(nullptr, TerrainMaterialPath))
    {
        UMaterialInstanceDynamic* Mid = UMaterialInstanceDynamic::Create(Authored, this);
        Mid->SetScalarParameterValue(TEXT("WorldScale"), Map.WorldScale);
        Terrain->SetMaterial(0, Mid);
    }
    else
    {
        UE_LOG(LogCampaignMap, Warning,
               TEXT("%s not found — terrain will render flat. See unreal/Shaders/README.md."),
               TerrainMaterialPath);
        Terrain->SetMaterial(0, TintedMaterial(this, FLinearColor(0.30f, 0.42f, 0.13f)));
    }
}

void ACampaignMap::BuildRivers()
{
    // Rivers are draped ribbons traced downhill through the DEM by rivers.rs and
    // baked; each carries its own width, trunks wider than headwaters. One mesh
    // section for the whole network — they are all the same water.
    TArray<FVector> Vertices;
    TArray<int32> Triangles;
    TArray<FVector> Normals;
    for (const FRiverPolyline& River : Map.Rivers)
    {
        tw::BuildRibbon(River.Points, River.Width, RiverZBias, Vertices, Triangles, Normals);
    }
    if (Triangles.Num() == 0)
    {
        return;
    }

    Rivers->CreateMeshSection(0, Vertices, Triangles, Normals, TArray<FVector2D>(),
                              TArray<FColor>(), TArray<FProcMeshTangent>(),
                              /*bCreateCollision=*/false);
    Rivers->SetMaterial(0, TintedMaterial(this, Water));
}

void ACampaignMap::BuildForests()
{
    UStaticMesh* Cylinder =
        LoadObject<UStaticMesh>(nullptr, TEXT("/Engine/BasicShapes/Cylinder.Cylinder"));
    UStaticMesh* Cone = LoadObject<UStaticMesh>(nullptr, TEXT("/Engine/BasicShapes/Cone.Cone"));
    if (Cylinder == nullptr || Cone == nullptr)
    {
        return;
    }

    TreeTrunks->SetStaticMesh(Cylinder);
    TreeTrunks->SetMaterial(0, TintedMaterial(this, Bark));
    TreeCanopies->SetStaticMesh(Cone);
    TreeCanopies->SetMaterial(0, TintedMaterial(this, Canopy));

    // A tapered trunk under a conifer canopy, in Godot units, from _tree_mesh().
    // The two-tier canopy collapses to one cone here: at the density these are
    // scattered the second tier was never legible, and it doubled the instance
    // count of the heaviest component on the map.
    constexpr double TrunkHeight = 3.0;
    constexpr double TrunkRadius = 0.55;
    constexpr double CanopyHeight = 8.0;
    constexpr double CanopyRadius = 2.6;

    for (const FForestInstance& Tree : Map.Forests)
    {
        const FRotator Rotation(0.0f, Tree.YawDegrees, 0.0f);

        FTransform Trunk(Rotation,
                         Tree.Position + FVector(0, 0, TrunkHeight * 0.5 * GodotToUU * Tree.Scale),
                         FVector(TrunkRadius * 2.0, TrunkRadius * 2.0, TrunkHeight) * Tree.Scale);
        TreeTrunks->AddInstance(Trunk, /*bWorldSpace=*/true);

        FTransform Canopy_(
            Rotation,
            Tree.Position + FVector(0, 0, (TrunkHeight + CanopyHeight * 0.5) * GodotToUU * Tree.Scale),
            FVector(CanopyRadius * 2.0, CanopyRadius * 2.0, CanopyHeight) * Tree.Scale);
        TreeCanopies->AddInstance(Canopy_, /*bWorldSpace=*/true);
    }
}

// ---------------------------------------------------------------------------
//  Ownership-dependent layers
// ---------------------------------------------------------------------------

void ACampaignMap::RebuildBorders(const FWorldSnapshot& Snapshot)
{
    // A line between two provinces with different owners is a frontier; an
    // internal line is thin and faint. Frontiers now carry the owning factions'
    // colours: each owner gets a ribbon offset onto its own side of the line, so
    // a contested border reads as two parallel faction stripes the way it does
    // in Total War. That is why borders rebuild while rivers do not.
    struct FMeshBuffers
    {
        TArray<FVector> Verts, Normals;
        TArray<int32> Tris;
    };

    FMeshBuffers Internal;
    // Frontier geometry accumulates per owning faction so each faction's stretch
    // of border can be drawn in its own colour with one mesh section.
    TMap<int32, FMeshBuffers> ByFaction;

    // Half the frontier width — how far each owner's stripe is nudged off centre.
    const float StripeOffset = BorderFrontierWidth * 0.5f;

    for (const FBorderSegment& Segment : Map.Borders)
    {
        const int32 OwnerA = Snapshot.Provinces.IsValidIndex(Segment.A)
                                 ? Snapshot.Provinces[Segment.A].Owner
                                 : TW_NONE;
        const int32 OwnerB = Snapshot.Provinces.IsValidIndex(Segment.B)
                                 ? Snapshot.Provinces[Segment.B].Owner
                                 : TW_NONE;

        if (OwnerA == OwnerB)
        {
            tw::BuildRibbon(Segment.Points, BorderInternalWidth, InkZBias, Internal.Verts,
                            Internal.Tris, Internal.Normals);
            continue;
        }

        // Which physical side is A's? Compare the segment's own left-of-travel
        // normal against the direction to A's settlement, so A's stripe always
        // lands on the terrain A actually owns. B takes the opposite sign.
        float SideForA = +1.0f;
        if (Segment.Points.Num() >= 2)
        {
            const FVector Mid = Segment.Points[Segment.Points.Num() / 2];
            FVector Forward = Segment.Points.Last() - Segment.Points[0];
            Forward.Z = 0.0;
            const FVector SideDir =
                FVector::CrossProduct(Forward.GetSafeNormal(), FVector::UpVector);
            FVector ToA = Map.SiteOf(Segment.A) - Mid;
            ToA.Z = 0.0;
            if (FVector::DotProduct(SideDir, ToA) < 0.0)
            {
                SideForA = -1.0f;
            }
        }

        auto EmitStripe = [&](int32 Owner, float Sign)
        {
            if (Owner == TW_NONE)
            {
                return;
            }
            FMeshBuffers& Buf = ByFaction.FindOrAdd(Owner);
            tw::BuildRibbon(Segment.Points, StripeOffset, InkZBias, Buf.Verts, Buf.Tris,
                            Buf.Normals, Sign * StripeOffset * 0.5f);
        };
        EmitStripe(OwnerA, SideForA);
        EmitStripe(OwnerB, -SideForA);
    }

    Borders->ClearAllMeshSections();

    FLinearColor InternalColor = Ink;
    InternalColor.A = 0.3f;

    int32 Section = 0;
    if (Internal.Tris.Num() > 0)
    {
        Borders->CreateMeshSection(Section, Internal.Verts, Internal.Tris, Internal.Normals,
                                   TArray<FVector2D>(), TArray<FColor>(),
                                   TArray<FProcMeshTangent>(), false);
        Borders->SetMaterial(Section, TintedMaterial(this, InternalColor));
        ++Section;
    }

    for (const TPair<int32, FMeshBuffers>& Pair : ByFaction)
    {
        const FMeshBuffers& Buf = Pair.Value;
        if (Buf.Tris.Num() == 0)
        {
            continue;
        }
        FLinearColor Color = Map.ColorFor(Pair.Key);
        Color.A = 0.95f;
        Borders->CreateMeshSection(Section, Buf.Verts, Buf.Tris, Buf.Normals,
                                   TArray<FVector2D>(), TArray<FColor>(),
                                   TArray<FProcMeshTangent>(), false);
        Borders->SetMaterial(Section, TintedMaterial(this, Color));
        ++Section;
    }
}

void ACampaignMap::SyncMarkers(const FWorldSnapshot& Snapshot)
{
    UWorld* World = GetWorld();
    if (World == nullptr)
    {
        return;
    }

    // Which provinces a selected army could march into. Purely a highlight.
    TSet<int32> MoveTargets;
    if (SelectedArmy != INDEX_NONE)
    {
        for (const FArmyState& Army : Snapshot.Armies)
        {
            if (Army.Id == SelectedArmy && Snapshot.Provinces.IsValidIndex(Army.Location))
            {
                MoveTargets.Append(Snapshot.Provinces[Army.Location].Adjacent);
            }
        }
    }

    // --- Cities. Provinces never appear or disappear, so this is an update.
    for (const FProvinceState& Province : Snapshot.Provinces)
    {
        ACityActor** Existing = Cities.Find(Province.Id);
        ACityActor* City = Existing != nullptr ? *Existing : nullptr;
        if (City == nullptr || City->GetWalls() != Province.Walls)
        {
            // Walls change the silhouette, so a completed wall is the one case
            // that rebuilds rather than re-tints.
            if (City != nullptr)
            {
                City->Destroy();
            }
            FActorSpawnParameters Params;
            Params.SpawnCollisionHandlingOverride =
                ESpawnActorCollisionHandlingMethod::AlwaysSpawn;
            City = World->SpawnActor<ACityActor>(MarkerLocationFor(Province.Id), FRotator::ZeroRotator,
                                                 Params);
            City->BuildFor(Province.Id, Province.Tier, Province.Walls,
                           Map.ColorFor(Province.Owner));
            Cities.Add(Province.Id, City);
        }

        FLinearColor Color = Map.ColorFor(Province.Owner);
        if (Province.BesiegedBy != TW_NONE)
        {
            Color = FMath::Lerp(Color, FLinearColor::Black, 0.35f);
        }
        if (MoveTargets.Contains(Province.Id))
        {
            Color = FMath::Lerp(Color, FLinearColor::White, 0.5f);
        }
        City->Refresh(Color, Province.Id == SelectedProvince);
    }

    // --- Armies. These do come and go, and the diff is what keeps actor count
    // flat over a long campaign.
    TSet<int32> Live;
    for (const FArmyState& Army : Snapshot.Armies)
    {
        Live.Add(Army.Id);

        AArmyActor** Existing = Armies.Find(Army.Id);
        AArmyActor* Actor = Existing != nullptr ? *Existing : nullptr;
        const FVector Destination = MarkerLocationFor(Army.Location);

        if (Actor == nullptr || Actor->GetSize() != Army.Size)
        {
            if (Actor != nullptr)
            {
                Actor->Destroy();
            }
            FActorSpawnParameters Params;
            Params.SpawnCollisionHandlingOverride =
                ESpawnActorCollisionHandlingMethod::AlwaysSpawn;
            Actor = World->SpawnActor<AArmyActor>(Destination, FRotator::ZeroRotator, Params);
            Actor->BuildFor(Army.Id, Army.Size, Map.ColorFor(Army.Owner));
            Armies.Add(Army.Id, Actor);
        }
        else
        {
            const int32* Was = ArmyLocations.Find(Army.Id);
            if (Was != nullptr && *Was != Army.Location)
            {
                // The simulation has already applied the move — this is the
                // marker catching up, never a source of truth.
                Actor->MarchTo(Destination);
            }
            else if (!Actor->IsMarching())
            {
                Actor->SetActorLocation(Destination);
            }
        }

        ArmyLocations.Add(Army.Id, Army.Location);
        Actor->Refresh(Map.ColorFor(Army.Owner), Army.Id == SelectedArmy);
    }

    for (auto It = Armies.CreateIterator(); It; ++It)
    {
        if (!Live.Contains(It.Key()))
        {
            if (It.Value() != nullptr)
            {
                It.Value()->Destroy();
            }
            ArmyLocations.Remove(It.Key());
            It.RemoveCurrent();
        }
    }
}

void ACampaignMap::HandleSnapshot(const FWorldSnapshot& Snapshot)
{
    Latest = Snapshot;
    bHaveSnapshot = true;

    // A selected army that died or was merged away stops being selected before
    // anything tries to draw a highlight on it.
    if (SelectedArmy != INDEX_NONE)
    {
        const bool bStillThere = Snapshot.Armies.ContainsByPredicate(
            [this](const FArmyState& A) { return A.Id == SelectedArmy; });
        if (!bStillThere)
        {
            SelectedArmy = INDEX_NONE;
        }
    }

    RebuildBorders(Snapshot);
    SyncMarkers(Snapshot);
}

void ACampaignMap::SetSelection(int32 InArmy, int32 InProvince)
{
    SelectedArmy = InArmy;
    SelectedProvince = InProvince;
    if (bHaveSnapshot)
    {
        // No round-trip: selection is presentation, and the cached snapshot is
        // all it needs.
        SyncMarkers(Latest);
    }
}

bool ACampaignMap::IsAnimating() const
{
    for (const TPair<int32, AArmyActor*>& Entry : Armies)
    {
        if (Entry.Value != nullptr && Entry.Value->IsMarching())
        {
            return true;
        }
    }
    return false;
}

// ---------------------------------------------------------------------------
//  Queries
// ---------------------------------------------------------------------------

bool ACampaignMap::GroundAt(double X, double Y, double& OutZ) const
{
    const UWorld* World = GetWorld();
    if (World == nullptr)
    {
        return false;
    }

    // Well above the highest peak and well below the sea floor. The terrain is
    // the only collider in the world, so this cannot hit anything else.
    const FVector Start(X, Y, 500000.0);
    const FVector End(X, Y, -500000.0);

    FCollisionQueryParams Params(SCENE_QUERY_STAT(TWGroundAt), /*bTraceComplex=*/true);
    FHitResult Hit;
    if (!World->LineTraceSingleByChannel(Hit, Start, End, ECC_WorldStatic, Params))
    {
        return false;
    }
    OutZ = Hit.ImpactPoint.Z;
    return true;
}

int32 ACampaignMap::ProvinceAt(double X, double Y) const
{
    // Built once per call rather than cached: 12 sites, and the alternative is a
    // second copy of the province table that can fall out of step with the bake.
    std::vector<tw::FProvinceSite> Sites;
    Sites.reserve(Map.Provinces.Num());
    for (const FProvinceSite3D& Site : Map.Provinces)
    {
        Sites.push_back({Site.Position.X, Site.Position.Y});
    }
    const std::size_t Found = tw::ProvinceAt(Sites, X, Y);
    if (Found == tw::kNoProvince)
    {
        return INDEX_NONE;
    }
    // The hit test returns a position in the array; the simulation wants the id.
    // They happen to match in the current bake, and relying on that silently is
    // how a reordered campaign file becomes an afternoon of confusion.
    return Map.Provinces[static_cast<int32>(Found)].Id;
}

FVector ACampaignMap::MarkerLocationFor(int32 Province) const
{
    FVector Site = Map.SiteOf(Province);
    double Z = 0.0;
    if (GroundAt(Site.X, Site.Y, Z))
    {
        Site.Z = Z;
    }
    return Site;
}
