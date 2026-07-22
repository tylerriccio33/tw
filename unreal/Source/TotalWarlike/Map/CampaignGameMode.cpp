#include "CampaignGameMode.h"

#include "Components/DirectionalLightComponent.h"
#include "Components/ExponentialHeightFogComponent.h"
#include "Components/SkyLightComponent.h"
#include "Components/StaticMeshComponent.h"
#include "Engine/DirectionalLight.h"
#include "Engine/ExponentialHeightFog.h"
#include "Engine/SkyLight.h"
#include "Engine/StaticMesh.h"
#include "Engine/StaticMeshActor.h"
#include "Engine/World.h"
#include "Materials/MaterialInstanceDynamic.h"
#include "Materials/MaterialInterface.h"

#include "../Sim/SimSubsystem.h"
#include "CampaignHUD.h"
#include "CampaignMap.h"
#include "CampaignPlayerController.h"

DEFINE_LOG_CATEGORY_STATIC(LogCampaignGameMode, Log, All);

ACampaignGameMode::ACampaignGameMode()
{
    PlayerControllerClass = ACampaignPlayerController::StaticClass();
    HUDClass = ACampaignHUD::StaticClass();
    // The controller owns and drives its own camera actor, so there is nothing
    // for a pawn to be.
    DefaultPawnClass = nullptr;
}

void ACampaignGameMode::BeginPlay()
{
    Super::BeginPlay();

    SpawnLighting();

    ACampaignMap* Map = GetWorld()->SpawnActor<ACampaignMap>();
    if (Map == nullptr)
    {
        UE_LOG(LogCampaignGameMode, Fatal, TEXT("could not spawn the campaign map"));
        return;
    }
    SpawnSea();

    USimSubsystem* Sim = GetGameInstance()->GetSubsystem<USimSubsystem>();
    if (Sim == nullptr)
    {
        UE_LOG(LogCampaignGameMode, Error, TEXT("no USimSubsystem — cannot start a campaign"));
        return;
    }

    FSidecarConfig Config;
#if WITH_EDITOR
    // Reuse a sidecar started by `make py-server` if one is answering. This is
    // the hot-reload path: restart Python, keep the editor open.
    Config.Mode = ESidecarMode::Auto;
#else
    Config.Mode = ESidecarMode::Spawn;
#endif
    Sim->StartCampaign(Config, Campaign, Seed);
}

void ACampaignGameMode::SpawnLighting()
{
    UWorld* World = GetWorld();

    // Warm, low afternoon sun, angled to give the exaggerated terrain form.
    // The Godot rotation was (-27, 55, 0) in its own axes; here it is pitch and
    // yaw directly.
    if (ADirectionalLight* Sun = World->SpawnActor<ADirectionalLight>())
    {
        Sun->SetActorRotation(FRotator(-27.0f, 55.0f, 0.0f));
        UDirectionalLightComponent* Light = Sun->GetComponent();
        Light->SetIntensity(4.0f);
        Light->SetLightColor(FLinearColor(1.0f, 0.90f, 0.74f));
        Light->SetMobility(EComponentMobility::Movable);
        // The map is ~124 km across at this scale. Unreal's default shadow
        // distance is a few thousand centimetres — far too near — and the same
        // mistake the Godot client made before its directional_shadow_max_distance
        // was raised.
        Light->DynamicShadowDistanceMovableLight = 3.0e6f;
        Light->CascadeDistributionExponent = 3.0f;
    }

    if (ASkyLight* Sky = World->SpawnActor<ASkyLight>())
    {
        // Let the sun create contrast; the ambient is there to keep the shadowed
        // north faces from going to black, not to flood the map.
        Sky->GetLightComponent()->SetMobility(EComponentMobility::Movable);
        Sky->GetLightComponent()->SetIntensity(0.6f);
    }

    if (AExponentialHeightFog* Fog = World->SpawnActor<AExponentialHeightFog>())
    {
        // Godot used depth fog with hand-picked begin/end distances so the far
        // sea dissolved into the horizon while the map itself stayed clear.
        // Unreal only has height/exponential fog here, so this is tuned for the
        // same *effect* rather than ported number-for-number: thin enough not to
        // touch the continent, dense enough to hide the water plane's edge.
        UExponentialHeightFogComponent* Component = Fog->GetComponent();
        Component->SetFogDensity(0.000004f);
        Component->SetFogInscatteringColor(FLinearColor(0.72f, 0.80f, 0.90f));
        Component->SetStartDistance(150000.0f);
        Component->SetFogHeightFalloff(0.00002f);
    }
}

void ACampaignGameMode::SpawnSea()
{
    // A flat plane at z=0. The Godot client shaded it with a depth-aware water
    // shader (pale over the shelf, teal offshore); that is milestone 2, and like
    // the terrain material it would need an authored asset. A flat blue slab is
    // enough to read as sea and to make the coastline legible.
    UStaticMesh* Plane = LoadObject<UStaticMesh>(nullptr, TEXT("/Engine/BasicShapes/Plane.Plane"));
    UMaterialInterface* Base = LoadObject<UMaterialInterface>(
        nullptr, TEXT("/Engine/BasicShapes/BasicShapeMaterial.BasicShapeMaterial"));
    if (Plane == nullptr || Base == nullptr)
    {
        return;
    }

    AStaticMeshActor* Sea = GetWorld()->SpawnActor<AStaticMeshActor>();
    if (Sea == nullptr)
    {
        return;
    }
    Sea->SetMobility(EComponentMobility::Movable);
    UStaticMeshComponent* Mesh = Sea->GetStaticMeshComponent();
    Mesh->SetStaticMesh(Plane);
    // Well past the map extent, so its edge never enters frame.
    Mesh->SetWorldScale3D(FVector(6000.0, 6000.0, 1.0));
    // The sea must not swallow clicks or ground traces: hitting the terrain mesh
    // IS the land test (see ProvinceLookup.h).
    Mesh->SetCollisionEnabled(ECollisionEnabled::NoCollision);

    UMaterialInstanceDynamic* Water = UMaterialInstanceDynamic::Create(Base, Sea);
    Water->SetVectorParameterValue(TEXT("Color"), FLinearColor(0.16f, 0.31f, 0.45f));
    Water->SetVectorParameterValue(TEXT("BaseColor"), FLinearColor(0.16f, 0.31f, 0.45f));
    Mesh->SetMaterial(0, Water);
}
