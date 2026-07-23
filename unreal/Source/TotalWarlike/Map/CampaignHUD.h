// The milestone-1 event log, and nothing else.
//
// The plan calls for "a debug UTextBlock fed by FOnSimEvents". This is an AHUD
// drawing the same lines with Canvas text instead, for one reason: a UMG widget
// needs a Widget Blueprint asset, and this project deliberately has no authored
// content (see CampaignMap.h). AHUD::DrawHUD needs nothing.
//
// Everything the real HUD will eventually be — the sidebar, build and recruit
// menus, unit cards, the diplomacy screen, the codex, banners, and the paced
// FEED_PAUSE reveal — is milestone 2. This exists to prove the loop, so it shows
// whose turn it is, whether the sim is busy, and what just happened.

#pragma once

#include "CoreMinimal.h"
#include "GameFramework/HUD.h"

#include "../Sim/SimTypes.h"

#include "CampaignHUD.generated.h"

class ACampaignMap;
class USimSubsystem;

UCLASS()
class ACampaignHUD : public AHUD
{
    GENERATED_BODY()

public:
    virtual void BeginPlay() override;
    virtual void EndPlay(const EEndPlayReason::Type Reason) override;
    virtual void DrawHUD() override;

    /// A one-off line that is not a simulation event — a refused command, a
    /// transport failure. Same feed, different source.
    void Note(const FString& Text, const FLinearColor& Color);

    /// The control-bar buttons, in the vocabulary the controller speaks: a
    /// gesture, not a command. The controller turns the choice into the actual
    /// FSimCommand, so the "input decides which, rules decide whether" split holds
    /// for a button press exactly as it does for a click on the map.
    enum class EControlAction : uint8
    {
        None,
        EndTurn,
        Construct,
        Recruit,
    };

    /// Which button, if any, sits under a screen point — the bar's hit-test
    /// against the rectangles it drew last frame. Returns None for a click that
    /// missed every button (including one below the bar's own rows).
    EControlAction ControlActionAt(const FVector2D& Screen) const;

private:
    void HandleEvents(const TArray<FSimEvent>& Events);

    /// The milestone-2 chrome: a stone control bar pinned to the bottom edge,
    /// showing the selected army's regiments, the selected city's buildings, and
    /// a right-hand cluster of treasury / turn / action buttons. Purely visual —
    /// nothing here sends a command yet.
    void DrawControlBar();

    /// Floating settlement name tags, projected from each province's site onto
    /// the map: a dark plate with the owning faction's colour as a stripe, the
    /// city name in stone text. This is the reference screenshot's Burgos /
    /// Pamplona labels — the one thing that makes the keeps legible as places.
    void DrawSettlementLabels();

    struct FFeedLine
    {
        FString Text;
        FLinearColor Color = FLinearColor::White;
    };

    /// Newest last. Capped so a 400-turn campaign does not grow without bound.
    TArray<FFeedLine> Feed;
    static constexpr int32 MaxFeedLines = 24;

    /// A clickable region of the control bar, recorded as it is drawn so the
    /// controller can hit-test it. Rebuilt every DrawControlBar, since the bar's
    /// layout depends on the canvas size.
    struct FControlHit
    {
        FBox2D Rect = FBox2D(ForceInit);
        EControlAction Action = EControlAction::None;
    };
    TArray<FControlHit> ControlHits;

    ACampaignMap* FindMap() const;
    USimSubsystem* Sim() const;

    FDelegateHandle EventsHandle;
    FDelegateHandle RejectHandle;
    FDelegateHandle ErrorHandle;
};
