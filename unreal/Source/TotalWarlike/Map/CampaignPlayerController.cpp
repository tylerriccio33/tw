#include "CampaignPlayerController.h"

#include "Camera/CameraActor.h"
#include "Camera/CameraComponent.h"
#include "Components/InputComponent.h"
#include "EngineUtils.h"
#include "Kismet/GameplayStatics.h"

#include "../Sim/SimSubsystem.h"
#include "CampaignHUD.h"
#include "CampaignMap.h"

namespace
{

/// Godot units to centimetres, so the camera constants below can stay the ones
/// that were tuned in `Main.gd`.
constexpr double GodotToUU = 100.0;

/// CAM_DIR from Main.gd — a low, cinematic ~37 degrees above the ground —
/// permuted into Unreal space by `ue = (-gz, gx, gy)`.
const FVector CamDir = FVector(-0.80, 0.0, 0.60).GetSafeNormal();

constexpr double MinDistance = 320.0 * GodotToUU;
constexpr double MaxDistance = 1400.0 * GodotToUU;
constexpr double ZoomStep = 40.0 * GodotToUU;

/// Main.gd panned at 0.9 * cam_dist per second, so the map moves under the
/// cursor at the same apparent rate however far out you are zoomed.
constexpr double PanRate = 0.9;

} // namespace

ACampaignPlayerController::ACampaignPlayerController()
{
    PrimaryActorTick.bCanEverTick = true;
    bShowMouseCursor = true;
    // A campaign map is a pointing game; nothing here wants mouse-look.
    DefaultMouseCursor = EMouseCursor::Default;
}

void ACampaignPlayerController::BeginPlay()
{
    Super::BeginPlay();

    FActorSpawnParameters Params;
    Params.SpawnCollisionHandlingOverride = ESpawnActorCollisionHandlingMethod::AlwaysSpawn;
    CameraRig = GetWorld()->SpawnActor<ACameraActor>(Params);
    if (CameraRig != nullptr)
    {
        CameraRig->GetCameraComponent()->SetFieldOfView(45.0f);
        SetViewTarget(CameraRig);
    }

    // Open over the middle of the map. Main.gd started at the origin with the
    // same distance; the baked world is centred on the same point.
    CameraTarget = FVector(0.0, 0.0, 30.0 * GodotToUU);
    PlaceCamera();

    SetInputMode(FInputModeGameAndUI().SetHideCursorDuringCapture(false));
}

void ACampaignPlayerController::SetupInputComponent()
{
    Super::SetupInputComponent();

    // BindKey rather than an axis mapping: axis mappings live in DefaultInput.ini
    // and these are the only four bindings the slice has.
    InputComponent->BindKey(EKeys::LeftMouseButton, IE_Pressed, this,
                            &ACampaignPlayerController::OnLeftClick);
    InputComponent->BindKey(EKeys::SpaceBar, IE_Pressed, this,
                            &ACampaignPlayerController::OnEndTurn);
    InputComponent->BindKey(EKeys::MouseScrollUp, IE_Pressed, this,
                            &ACampaignPlayerController::OnZoomIn);
    InputComponent->BindKey(EKeys::MouseScrollDown, IE_Pressed, this,
                            &ACampaignPlayerController::OnZoomOut);
}

void ACampaignPlayerController::PlayerTick(float DeltaTime)
{
    Super::PlayerTick(DeltaTime);

    // WASD / arrows, polled exactly as Main.gd polled them in _process. Screen
    // "up" is +X in Unreal after the baker's permutation, and screen "right"
    // is -Y.
    FVector2D Direction = FVector2D::ZeroVector;
    if (IsInputKeyDown(EKeys::W) || IsInputKeyDown(EKeys::Up))
    {
        Direction.X += 1.0;
    }
    if (IsInputKeyDown(EKeys::S) || IsInputKeyDown(EKeys::Down))
    {
        Direction.X -= 1.0;
    }
    if (IsInputKeyDown(EKeys::A) || IsInputKeyDown(EKeys::Left))
    {
        Direction.Y -= 1.0;
    }
    if (IsInputKeyDown(EKeys::D) || IsInputKeyDown(EKeys::Right))
    {
        Direction.Y += 1.0;
    }

    if (!Direction.IsNearlyZero())
    {
        Direction.Normalize();
        const double Step = PanRate * CameraDistance * DeltaTime;
        CameraTarget.X += Direction.X * Step;
        CameraTarget.Y += Direction.Y * Step;
        PlaceCamera();
    }
}

void ACampaignPlayerController::PlaceCamera()
{
    if (CameraRig == nullptr)
    {
        return;
    }
    const FVector Location = CameraTarget + CamDir * CameraDistance;
    CameraRig->SetActorLocation(Location);
    CameraRig->SetActorRotation((CameraTarget - Location).Rotation());
}

void ACampaignPlayerController::SetView(const FVector2D& GroundXY, double Distance)
{
    // Same Z as BeginPlay's opening view: the ground plane the camera orbits is
    // nominal, not the traced terrain height, so a preset over a mountain and a
    // preset over a plain are framed the same way.
    CameraTarget = FVector(GroundXY.X, GroundXY.Y, 30.0 * GodotToUU);
    CameraDistance = FMath::Clamp(Distance, MinDistance, MaxDistance);
    PlaceCamera();
}

void ACampaignPlayerController::OnZoomIn()
{
    CameraDistance = FMath::Clamp(CameraDistance - ZoomStep, MinDistance, MaxDistance);
    PlaceCamera();
}

void ACampaignPlayerController::OnZoomOut()
{
    CameraDistance = FMath::Clamp(CameraDistance + ZoomStep, MinDistance, MaxDistance);
    PlaceCamera();
}

ACampaignMap* ACampaignPlayerController::FindMap() const
{
    // There is exactly one, spawned by ACampaignGameMode::BeginPlay.
    TActorIterator<ACampaignMap> It(GetWorld());
    return It ? *It : nullptr;
}

ACampaignHUD* ACampaignPlayerController::GetCampaignHUD() const
{
    return Cast<ACampaignHUD>(GetHUD());
}

USimSubsystem* ACampaignPlayerController::Sim() const
{
    const UGameInstance* GameInstance = UGameplayStatics::GetGameInstance(this);
    return GameInstance != nullptr ? GameInstance->GetSubsystem<USimSubsystem>() : nullptr;
}

int32 ACampaignPlayerController::ProvinceUnderCursor() const
{
    ACampaignMap* Map = FindMap();
    if (Map == nullptr)
    {
        return INDEX_NONE;
    }

    FHitResult Hit;
    if (!GetHitResultUnderCursorByChannel(UEngineTypes::ConvertToTraceType(ECC_WorldStatic),
                                          /*bTraceComplex=*/true, Hit))
    {
        // Nothing under the cursor means open sea or off the map.
        return INDEX_NONE;
    }
    return Map->ProvinceAt(Hit.ImpactPoint.X, Hit.ImpactPoint.Y);
}

void ACampaignPlayerController::OnLeftClick()
{
    ACampaignMap* Map = FindMap();
    USimSubsystem* Subsystem = Sim();
    if (Map == nullptr || Subsystem == nullptr)
    {
        return;
    }

    // The control bar sits on top of the map, so a click over one of its buttons
    // is a button press, not an order to the province underneath. Ask the HUD
    // first; a hit consumes the click and never falls through to a world trace.
    if (ACampaignHUD* Hud = GetCampaignHUD())
    {
        float MouseX = 0.0f;
        float MouseY = 0.0f;
        if (GetMousePosition(MouseX, MouseY))
        {
            switch (Hud->ControlActionAt(FVector2D(MouseX, MouseY)))
            {
            case ACampaignHUD::EControlAction::EndTurn:
                OnEndTurn();
                return;
            case ACampaignHUD::EControlAction::Construct:
                OnConstruct();
                return;
            case ACampaignHUD::EControlAction::Recruit:
                OnRecruit();
                return;
            case ACampaignHUD::EControlAction::TabBuildings:
                Hud->SelectTab(0);
                return;
            case ACampaignHUD::EControlAction::TabCharacters:
                Hud->SelectTab(1);
                return;
            case ACampaignHUD::EControlAction::TabMilitary:
                Hud->SelectTab(2);
                return;
            case ACampaignHUD::EControlAction::None:
                break;
            }
        }
    }

    // A click while a turn is resolving, or while a marker is still marching,
    // would race the snapshot it is being interpreted against.
    if (Subsystem->IsBusy() || Map->IsAnimating())
    {
        return;
    }

    const FWorldSnapshot& Snapshot = Subsystem->GetSnapshot();
    if (Snapshot.Winner != TW_NONE)
    {
        return;
    }

    const int32 Province = ProvinceUnderCursor();
    if (Province == INDEX_NONE)
    {
        SelectedArmy = INDEX_NONE;
        SelectedProvince = INDEX_NONE;
        Map->SetSelection(SelectedArmy, SelectedProvince);
        return;
    }

    // 1. Order a selected army into an adjacent province.
    if (SelectedArmy != INDEX_NONE)
    {
        const FArmyState* Army = Snapshot.Armies.FindByPredicate(
            [this](const FArmyState& A) { return A.Id == SelectedArmy; });
        if (Army != nullptr && Snapshot.Provinces.IsValidIndex(Army->Location) &&
            Snapshot.Provinces[Army->Location].Adjacent.Contains(Province))
        {
            SelectedProvince = Province;
            Map->SetSelection(SelectedArmy, SelectedProvince);
            // Whether this is a march, a battle or a siege is the simulation's
            // call; the click is always just "go there".
            Subsystem->SendCommand(FSimCommand::Move(SelectedArmy, Province));
            return;
        }
    }

    // 2. Select one of the player's own armies standing here.
    if (Snapshot.IsPlayerTurn())
    {
        for (const FArmyState& Army : Snapshot.Armies)
        {
            if (Army.Location == Province && Snapshot.Factions.IsValidIndex(Army.Owner) &&
                Snapshot.Factions[Army.Owner].bIsPlayer)
            {
                SelectedArmy = Army.Id;
                SelectedProvince = Province;
                Map->SetSelection(SelectedArmy, SelectedProvince);
                return;
            }
        }
    }

    // 3. Inspect.
    SelectedArmy = INDEX_NONE;
    SelectedProvince = Province;
    Map->SetSelection(SelectedArmy, SelectedProvince);
}

void ACampaignPlayerController::OnEndTurn()
{
    USimSubsystem* Subsystem = Sim();
    ACampaignMap* Map = FindMap();
    if (Subsystem == nullptr || Subsystem->IsBusy() || (Map != nullptr && Map->IsAnimating()))
    {
        return;
    }
    if (Subsystem->GetSnapshot().Winner != TW_NONE)
    {
        return;
    }

    // One call resolves the player's turn AND every AI faction's — the "Unreal
    // does not simulate anything" model, one press to one round-trip.
    if (!Subsystem->EndTurn())
    {
        if (ACampaignHUD* Hud = GetCampaignHUD())
        {
            Hud->Note(TEXT("… still resolving"), FLinearColor(0.7f, 0.7f, 0.7f));
        }
    }
}

void ACampaignPlayerController::OnRecruit()
{
    USimSubsystem* Subsystem = Sim();
    if (Subsystem == nullptr || Subsystem->IsBusy() || SelectedProvince == INDEX_NONE)
    {
        return;
    }
    // The bar has one Recruit button, not a unit menu yet, so it raises the basic
    // regiment (UnitType.MELEE == 0). An unaffordable or off-turn order comes back
    // as a refusal on the feed, exactly as a bad move does.
    constexpr int32 UnitMelee = 0;
    Subsystem->SendCommand(FSimCommand::Recruit(SelectedProvince, UnitMelee));
}

void ACampaignPlayerController::OnConstruct()
{
    USimSubsystem* Subsystem = Sim();
    if (Subsystem == nullptr || Subsystem->IsBusy() || SelectedProvince == INDEX_NONE)
    {
        return;
    }
    const FWorldSnapshot& Snapshot = Subsystem->GetSnapshot();
    if (!Snapshot.Provinces.IsValidIndex(SelectedProvince))
    {
        return;
    }
    const FProvinceState& Prov = Snapshot.Provinces[SelectedProvince];

    // One Construct button, no build menu yet, so it puts money where the town is
    // weakest: the lowest of its four buildings. Ties resolve in enum order
    // (Farm, Market, Barracks, Walls). `rules.py` refuses a level already at cap.
    const int32 Levels[4] = {Prov.Farm, Prov.Market, Prov.Barracks, Prov.Walls};
    int32 Building = 0;
    for (int32 i = 1; i < 4; ++i)
    {
        if (Levels[i] < Levels[Building])
        {
            Building = i;
        }
    }
    Subsystem->SendCommand(FSimCommand::Build(SelectedProvince, Building));
}
