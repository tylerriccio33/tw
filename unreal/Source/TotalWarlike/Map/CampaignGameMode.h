// Brings the campaign up.
//
// There is no .umap. The game mode spawns the world — sun, sky, fog, sea and the
// campaign map — and then asks USimSubsystem to start a campaign. That is what
// lets `make play` run against any stock level (the project points at
// /Engine/Maps/Entry) with nothing authored in Content/.
//
// It is also where the sidecar policy lives: Auto in the editor, so a running
// `make py-server` is reused and Python can be restarted without closing the
// editor; Spawn in a packaged build, where the game owns the process.

#pragma once

#include "CoreMinimal.h"
#include "GameFramework/GameModeBase.h"

#include "CampaignGameMode.generated.h"

UCLASS()
class ACampaignGameMode : public AGameModeBase
{
    GENERATED_BODY()

public:
    ACampaignGameMode();

    virtual void BeginPlay() override;

    /// The campaign to load, matching a file in `sim/campaign/`.
    UPROPERTY(EditDefaultsOnly, Category = "Sim")
    FString Campaign = TEXT("britain");

    /// Determinism is de-scoped, so this seeds `random.Random` and nothing more:
    /// the same seed gives a similar campaign, not an identical one.
    UPROPERTY(EditDefaultsOnly, Category = "Sim")
    int32 Seed = 42;

private:
    void SpawnLighting();
    void SpawnSea();
};
