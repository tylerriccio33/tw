#include "MarkerActors.h"

#include "Components/SceneComponent.h"
#include "Components/StaticMeshComponent.h"
#include "Engine/StaticMesh.h"
#include "Materials/MaterialInstanceDynamic.h"
#include "UObject/ConstructorHelpers.h"

namespace
{

/// Godot units to Unreal centimetres — `meta.world_scale` in provinces.json.
/// Every dimension below is copied straight from `Main.gd` in Godot units and
/// converted here, so the two can still be diffed side by side.
constexpr double GodotToUU = 100.0;

/// The same permutation the baker used (`ue = (-gz, gx, gy) * scale`), applied
/// to a marker's own local frame so a marker is not a mirror image of the map.
FVector GodotToUnreal(const FVector& G)
{
    return FVector(-G.Z, G.X, G.Y) * GodotToUU;
}

/// `/Engine/BasicShapes` primitives are 100 uu across, which makes the scale for
/// a shape `Size` Godot units wide exactly `Size`.
UStaticMesh* BasicShape(const TCHAR* Name)
{
    const FString Path = FString::Printf(TEXT("/Engine/BasicShapes/%s.%s"), Name, Name);
    return LoadObject<UStaticMesh>(nullptr, *Path);
}

// Colours ported from Main.gd.
const FLinearColor Stone = FLinearColor::FromSRGBColor(FColor(158, 148, 130));
const FLinearColor Steel = FLinearColor::FromSRGBColor(FColor(179, 184, 194));
const FLinearColor Skin = FLinearColor::FromSRGBColor(FColor(194, 153, 122));
const FLinearColor Pole = FLinearColor::FromSRGBColor(FColor(82, 61, 41));
/// What `picked` looked like in Godot: the marker is lerped toward gold.
const FLinearColor Gold(1.0f, 0.84f, 0.0f);

} // namespace

// ---------------------------------------------------------------------------
//  ATWMarkerActor
// ---------------------------------------------------------------------------

ATWMarkerActor::ATWMarkerActor()
{
    PrimaryActorTick.bCanEverTick = false;
    RootComponent = CreateDefaultSubobject<USceneComponent>(TEXT("Root"));
}

UStaticMeshComponent* ATWMarkerActor::AddShape(const TCHAR* Shape, const FVector& Location,
                                               const FVector& Size, const FLinearColor& Color,
                                               float Yaw, bool bGlow)
{
    UStaticMesh* Mesh = BasicShape(Shape);
    if (Mesh == nullptr)
    {
        return nullptr;
    }

    UStaticMeshComponent* Component = NewObject<UStaticMeshComponent>(this);
    Component->SetStaticMesh(Mesh);
    Component->SetupAttachment(RootComponent);
    Component->SetRelativeLocation(GodotToUnreal(Location));
    // Size is in Godot units and the primitives are 1 unit (100 uu) across, so
    // the scale IS the size. The axis swap matches GodotToUnreal.
    Component->SetRelativeScale3D(FVector(Size.Z, Size.X, Size.Y));
    Component->SetRelativeRotation(FRotator(0.0f, Yaw, 0.0f));
    // Markers are picked by ID from the snapshot, never by tracing, and the
    // ground trace must pass straight through them to reach the terrain.
    Component->SetCollisionEnabled(ECollisionEnabled::NoCollision);
    Component->RegisterComponent();

    UMaterialInstanceDynamic* Mid = Component->CreateAndSetMaterialInstanceDynamic(0);
    if (Mid != nullptr)
    {
        Mid->SetVectorParameterValue(TEXT("Color"), Color);
        Mid->SetVectorParameterValue(TEXT("BaseColor"), Color);
        if (bGlow)
        {
            Mid->SetScalarParameterValue(TEXT("Emissive"), 0.5f);
        }
    }
    return Component;
}

void ATWMarkerActor::SetFactionColor(const FLinearColor& Color, bool bGlow)
{
    const FLinearColor Tint = bGlow ? FMath::Lerp(Color, Gold, 0.55f) : Color;
    for (UStaticMeshComponent* Part : TintedParts)
    {
        if (Part == nullptr)
        {
            continue;
        }
        if (UMaterialInstanceDynamic* Mid = Cast<UMaterialInstanceDynamic>(Part->GetMaterial(0)))
        {
            Mid->SetVectorParameterValue(TEXT("Color"), Tint);
            Mid->SetVectorParameterValue(TEXT("BaseColor"), Tint);
            Mid->SetScalarParameterValue(TEXT("Emissive"), bGlow ? 0.5f : 0.0f);
        }
    }
}

// ---------------------------------------------------------------------------
//  ACityActor
// ---------------------------------------------------------------------------

void ACityActor::BuildFor(int32 InProvince, int32 Tier, int32 Walls, const FLinearColor& Color)
{
    SimId = InProvince;
    CachedTier = FMath::Clamp(Tier, 0, 3);
    CachedWalls = Walls;

    static const float Radii[] = {7.0f, 9.0f, 11.0f, 13.0f};
    static const int32 Houses[] = {3, 5, 8, 11};
    const float Radius = Radii[CachedTier];
    FRandomStream Rng = StreamFor(InProvince);

    // Base disc carries the owner's colour: at campaign zoom this is what says
    // whose city it is, since the houses themselves are stone.
    TintedParts.Add(AddShape(TEXT("Cylinder"), FVector(0, 0.6, 0),
                             FVector((Radius + 2.0f) * 2.0f, 1.2f, (Radius + 2.0f) * 2.0f), Color));

    for (int32 i = 0; i < Houses[CachedTier]; ++i)
    {
        const float Angle = Rng.FRand() * 2.0f * PI;
        const float Dist = FMath::Sqrt(Rng.FRand()) * Radius * 0.78f;
        const float H = Rng.FRandRange(4.0f, 9.0f) * (0.8f + 0.1f * CachedTier);
        const float W = Rng.FRandRange(2.4f, 4.2f);
        AddShape(TEXT("Cube"), FVector(FMath::Cos(Angle) * Dist, 1.2f + H * 0.5f, FMath::Sin(Angle) * Dist),
                 FVector(W, H, W), FMath::Lerp(Stone, Color, 0.12f),
                 FMath::RadiansToDegrees(Rng.FRand() * 2.0f * PI));
    }

    if (Walls > 0)
    {
        // Godot squashed a TorusMesh flat and stood it up to read as a curtain
        // wall. There is no torus in /Engine/BasicShapes, and a ring of panels
        // reads better anyway — it looks like masonry rather than a donut.
        const int32 Panels = 20;
        const float Height = 3.0f + Walls;
        const float PanelWidth = 2.0f * PI * Radius / Panels * 1.25f;
        for (int32 i = 0; i < Panels; ++i)
        {
            const float Angle = (2.0f * PI * i) / Panels;
            AddShape(TEXT("Cube"),
                     FVector(FMath::Cos(Angle) * Radius, 2.5f + Height * 0.5f,
                             FMath::Sin(Angle) * Radius),
                     FVector(PanelWidth, Height, 1.6f),
                     FMath::Lerp(Stone, FLinearColor::Black, 0.15f),
                     // The panel's width runs along its local +Y after the axis
                     // swap, so a quarter turn puts it tangent to the ring.
                     FMath::RadiansToDegrees(Angle) - 90.0f);
        }
    }

    // Banner — the thing that actually reads at map zoom.
    const float BannerHeight = 14.0f + 2.0f * CachedTier;
    AddShape(TEXT("Cylinder"), FVector(0, 1.2f + BannerHeight * 0.5f, 0),
             FVector(0.44f, BannerHeight, 0.44f), Pole);
    TintedParts.Add(AddShape(TEXT("Cube"), FVector(2.75f, 1.2f + BannerHeight - 2.4f, 0),
                             FVector(5.5f, 3.6f, 0.2f), Color));

    Refresh(Color, false);
}

void ACityActor::Refresh(const FLinearColor& Color, bool bSelected)
{
    SetFactionColor(Color, bSelected);
}

// ---------------------------------------------------------------------------
//  AArmyActor
// ---------------------------------------------------------------------------

namespace
{
/// Seconds a march takes, and how high it arcs (Godot units). Both from Main.gd.
constexpr float MarchTime = 0.55f;
constexpr float MarchLift = 24.0f;
} // namespace

AArmyActor::AArmyActor()
{
    PrimaryActorTick.bCanEverTick = true;
}

void AArmyActor::BuildFor(int32 InArmy, int32 Size, const FLinearColor& Color)
{
    SimId = InArmy;
    CachedSize = FMath::Clamp(Size, 0, 2);

    static const int32 ColsFor[] = {2, 3, 4};
    static const int32 RowsFor[] = {1, 2, 3};
    const int32 Cols = ColsFor[CachedSize];
    const int32 Rows = RowsFor[CachedSize];
    const float Step = 5.6f;

    for (int32 R = 0; R < Rows; ++R)
    {
        for (int32 C = 0; C < Cols; ++C)
        {
            const FVector Base((C - (Cols - 1) * 0.5f) * Step, 0.0f,
                               (R - (Rows - 1) * 0.5f) * Step);

            // A single foot soldier, built from primitives: reads as a person at
            // map zoom without costing anything.
            for (const float Lx : {-0.7f, 0.7f})
            {
                TintedParts.Add(AddShape(TEXT("Cube"), Base + FVector(Lx, 1.1f, 0),
                                         FVector(0.9f, 2.2f, 0.9f), Color));
            }
            TintedParts.Add(AddShape(TEXT("Cube"), Base + FVector(0, 3.5f, 0),
                                     FVector(2.2f, 2.6f, 1.4f), Color));
            AddShape(TEXT("Sphere"), Base + FVector(0, 5.4f, 0), FVector(1.6f, 1.6f, 1.6f), Skin);
            AddShape(TEXT("Cube"), Base + FVector(1.5f, 4.4f, 0), FVector(0.6f, 2.2f, 0.6f), Skin);
            AddShape(TEXT("Cube"), Base + FVector(2.1f, 7.4f, 0), FVector(0.3f, 4.5f, 0.5f), Steel);
            AddShape(TEXT("Cube"), Base + FVector(2.1f, 5.3f, 0), FVector(1.6f, 0.4f, 0.5f), Steel);
        }
    }

    const float BannerZ = -(Rows * Step) * 0.5f - 2.5f;
    AddShape(TEXT("Cylinder"), FVector(0, 13.0f * 0.5f, BannerZ), FVector(0.44f, 13.0f, 0.44f),
             Pole);
    TintedParts.Add(AddShape(TEXT("Cube"), FVector(2.75f, 13.0f - 2.4f, BannerZ),
                             FVector(5.5f, 3.6f, 0.2f), Color));

    Refresh(Color, false);
}

void AArmyActor::Refresh(const FLinearColor& Color, bool bSelected)
{
    SetFactionColor(Color, bSelected);
    // Godot scaled the whole marker 2x, and 2.5x when selected.
    const float S = bSelected ? 2.5f : 2.0f;
    SetActorScale3D(FVector(S, S, S));
}

void AArmyActor::MarchTo(const FVector& Target)
{
    MarchFrom = GetActorLocation();
    MarchTo_ = Target;
    MarchElapsed = 0.0f;
    bMarching = true;
}

void AArmyActor::Tick(float DeltaSeconds)
{
    Super::Tick(DeltaSeconds);
    if (!bMarching)
    {
        return;
    }

    MarchElapsed += DeltaSeconds;
    const float T = FMath::Clamp(MarchElapsed / MarchTime, 0.0f, 1.0f);
    // Sine ease in/out, as in the Godot tween.
    const float Eased = 0.5f - 0.5f * FMath::Cos(T * PI);

    FVector Position = FMath::Lerp(MarchFrom, MarchTo_, Eased);
    // Arc it off the ground so the march reads as movement rather than a slide.
    // The Godot version re-sampled terrain height each frame; here the endpoints
    // are already on the ground and the map is smooth enough between them that
    // interpolating the two is indistinguishable at campaign zoom.
    Position.Z += FMath::Sin(T * PI) * MarchLift * GodotToUU;
    SetActorLocation(Position);

    if (T >= 1.0f)
    {
        bMarching = false;
        SetActorLocation(MarchTo_);
    }
}
