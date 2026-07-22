// Pan, zoom, click, end turn. The whole of milestone 1's input.
//
// The controller is the only place that turns a gesture into an FSimCommand, and
// it is a thin place on purpose: it decides *which* command to send, never
// whether the simulation will accept it. "Is that province adjacent?" is asked
// by sending the move and letting `rules.py` refuse it — the refusal path is
// already wired to the feed, and duplicating the adjacency rule here is exactly
// the kind of drift the engine/frontend split exists to prevent.
//
// The one exception is a cheap pre-filter: a move is only *offered* into a
// province the snapshot lists as adjacent, so a click on a far-off province
// reads as "select that instead" rather than as a rejected order. That is a
// question about what the click MEANT, not about what is legal.
//
// Input is bound the old way (BindKey plus polling in Tick), matching
// `Main.gd:_process`, because Enhanced Input needs Input Action assets and this
// project has no authored content.

#pragma once

#include "CoreMinimal.h"
#include "GameFramework/PlayerController.h"

#include "CampaignPlayerController.generated.h"

class ACameraActor;
class ACampaignHUD;
class ACampaignMap;
class USimSubsystem;

UCLASS()
class ACampaignPlayerController : public APlayerController
{
    GENERATED_BODY()

public:
    ACampaignPlayerController();

    virtual void BeginPlay() override;
    virtual void SetupInputComponent() override;
    virtual void PlayerTick(float DeltaTime) override;

    /// Frame a point on the ground from a given distance, as a preset shot does.
    /// The camera is the controller's, so this is the one door into it —
    /// UShotDirector drives the view through here rather than reaching for the
    /// rig, which keeps "how the campaign camera is oriented" in one place.
    void SetView(const FVector2D& GroundXY, double Distance);

    double GetViewDistance() const { return CameraDistance; }

    /// Not `GetViewTarget`: APlayerController already has one, returning the
    /// view target ACTOR — the same collision `CameraRig` works around below.
    FVector2D GetViewFocus() const { return FVector2D(CameraTarget.X, CameraTarget.Y); }

private:
    void OnLeftClick();
    void OnEndTurn();
    void OnZoomIn();
    void OnZoomOut();

    /// Move the camera rig to match `CameraTarget` and `CameraDistance`.
    void PlaceCamera();

    /// Screen point -> the province under it, or INDEX_NONE at sea.
    /// A real trace against the terrain, so hitting the mesh at all *is* the land
    /// test — which is why ProvinceLookup dropped the Rust height check.
    int32 ProvinceUnderCursor() const;

    ACampaignMap* FindMap() const;
    ACampaignHUD* GetCampaignHUD() const;
    USimSubsystem* Sim() const;

    /// Not `Camera`: APlayerController already has a member by that name.
    UPROPERTY() ACameraActor* CameraRig = nullptr;

    /// Where the camera looks, on the ground plane, in centimetres.
    FVector CameraTarget = FVector::ZeroVector;
    double CameraDistance = 78000.0;

    int32 SelectedArmy = INDEX_NONE;
    int32 SelectedProvince = INDEX_NONE;
};
