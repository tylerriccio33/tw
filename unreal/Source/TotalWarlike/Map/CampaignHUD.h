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

private:
    void HandleEvents(const TArray<FSimEvent>& Events);

    struct FFeedLine
    {
        FString Text;
        FLinearColor Color = FLinearColor::White;
    };

    /// Newest last. Capped so a 400-turn campaign does not grow without bound.
    TArray<FFeedLine> Feed;
    static constexpr int32 MaxFeedLines = 24;

    ACampaignMap* FindMap() const;
    USimSubsystem* Sim() const;

    FDelegateHandle EventsHandle;
    FDelegateHandle RejectHandle;
    FDelegateHandle ErrorHandle;
};
