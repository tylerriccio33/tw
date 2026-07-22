#include "CampaignHUD.h"

#include "Engine/Canvas.h"
#include "Engine/Engine.h"
#include "Engine/Font.h"
#include "EngineUtils.h"
#include "Kismet/GameplayStatics.h"

#include "../Sim/SimSubsystem.h"
#include "CampaignMap.h"
#include "EventText.h"

void ACampaignHUD::BeginPlay()
{
    Super::BeginPlay();

    USimSubsystem* Subsystem = Sim();
    if (Subsystem == nullptr)
    {
        return;
    }

    EventsHandle = Subsystem->OnSimEvents.AddUObject(this, &ACampaignHUD::HandleEvents);
    RejectHandle = Subsystem->OnCommandRejected.AddLambda(
        [this](const FString& Message, const FString& Rule) {
            // A refused command is ordinary play — the player clicked somewhere
            // they cannot go — so it reads as a note, not an error.
            Note(FString::Printf(TEXT("✗ %s"), *Message), FLinearColor(0.9f, 0.7f, 0.4f));
        });
    ErrorHandle = Subsystem->OnTransportError.AddLambda([this](const FString& Message) {
        // Milestone 1 stops here on purpose: there is no reconnect, because a
        // half-applied campaign is worse than a clear stop.
        Note(FString::Printf(TEXT("‼ simulation disconnected: %s"), *Message),
             FLinearColor(1.0f, 0.35f, 0.3f));
    });
}

void ACampaignHUD::EndPlay(const EEndPlayReason::Type Reason)
{
    if (USimSubsystem* Subsystem = Sim())
    {
        Subsystem->OnSimEvents.Remove(EventsHandle);
        Subsystem->OnCommandRejected.Remove(RejectHandle);
        Subsystem->OnTransportError.Remove(ErrorHandle);
    }
    Super::EndPlay(Reason);
}

USimSubsystem* ACampaignHUD::Sim() const
{
    const UGameInstance* GameInstance = UGameplayStatics::GetGameInstance(this);
    return GameInstance != nullptr ? GameInstance->GetSubsystem<USimSubsystem>() : nullptr;
}

ACampaignMap* ACampaignHUD::FindMap() const
{
    // There is exactly one, spawned by ACampaignGameMode::BeginPlay.
    TActorIterator<ACampaignMap> It(GetWorld());
    return It ? *It : nullptr;
}

void ACampaignHUD::Note(const FString& Text, const FLinearColor& Color)
{
    Feed.Add({Text, Color});
    while (Feed.Num() > MaxFeedLines)
    {
        Feed.RemoveAt(0);
    }
}

void ACampaignHUD::HandleEvents(const TArray<FSimEvent>& Events)
{
    USimSubsystem* Subsystem = Sim();
    const ACampaignMap* Map = FindMap();
    if (Subsystem == nullptr || Map == nullptr)
    {
        return;
    }
    const FWorldSnapshot& Snapshot = Subsystem->GetSnapshot();

    for (const FSimEvent& Event : Events)
    {
        const FString Line = tw::FeedLineFor(Event, Snapshot);
        if (Line.IsEmpty())
        {
            // Not every event is narrated — see EventText.h.
            continue;
        }
        Note(Line, tw::FeedColorFor(Event, [Map](int32 Faction) {
                 return Map->GetMapData().ColorFor(Faction);
             }));
    }
}

void ACampaignHUD::DrawHUD()
{
    Super::DrawHUD();

    if (Canvas == nullptr)
    {
        return;
    }

    UFont* Font = GEngine != nullptr ? GEngine->GetMediumFont() : nullptr;
    if (Font == nullptr)
    {
        return;
    }

    constexpr float LineHeight = 16.0f;
    constexpr float Margin = 16.0f;

    // Status: turn, whose turn, and whether we are waiting on Python.
    if (const USimSubsystem* Subsystem = Sim())
    {
        const FWorldSnapshot& Snapshot = Subsystem->GetSnapshot();
        FString Status;
        if (!Subsystem->IsConnected())
        {
            Status = TEXT("connecting to the simulation...");
        }
        else if (Subsystem->IsBusy())
        {
            Status = TEXT("resolving turn...");
        }
        else if (Snapshot.Winner != TW_NONE)
        {
            Status = FString::Printf(
                TEXT("%s has won"), Snapshot.Factions.IsValidIndex(Snapshot.Winner)
                                        ? *Snapshot.Factions[Snapshot.Winner].Name
                                        : TEXT("someone"));
        }
        else
        {
            const FString Who = Snapshot.Factions.IsValidIndex(Snapshot.Current)
                                    ? Snapshot.Factions[Snapshot.Current].Name
                                    : TEXT("-");
            Status = FString::Printf(TEXT("turn %d — %s%s"), Snapshot.Turn, *Who,
                                     Snapshot.IsPlayerTurn() ? TEXT("  [Space] end turn")
                                                             : TEXT(""));
        }
        Canvas->SetDrawColor(FColor(240, 227, 199));
        Canvas->DrawText(Font, Status, Margin, Margin);
    }

    // The feed, newest at the bottom, hugging the bottom-left.
    float Y = Canvas->SizeY - Margin - LineHeight * Feed.Num();
    for (const FFeedLine& Line : Feed)
    {
        Canvas->SetDrawColor(Line.Color.ToFColor(/*bSRGB=*/true));
        Canvas->DrawText(Font, Line.Text, Margin, Y);
        Y += LineHeight;
    }
}
