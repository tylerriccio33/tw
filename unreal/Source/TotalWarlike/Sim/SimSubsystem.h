// The game's single point of contact with the simulation.
//
// Everything in Unreal reads `GetSnapshot()`, which is a cached value, and
// reacts to two delegates. NOTHING calls the transport per-frame; the transport
// is not even reachable from outside this class.
//
// Requests run on a worker thread and their replies are marshalled back to the
// game thread before any delegate fires, so subscribers never have to think
// about threading — a subscriber may spawn and destroy actors freely.

#pragma once

#include "CoreMinimal.h"
#include "Subsystems/GameInstanceSubsystem.h"

#include "SocketSimTransport.h"

#include "SimSubsystem.generated.h"

/// Everything that happened during the last request, in order. Fired on the
/// game thread after the snapshot has been updated, so a handler animating a
/// `city_fell` can already read the new owner.
DECLARE_MULTICAST_DELEGATE_OneParam(FOnSimEvents, const TArray<FSimEvent>&);

/// The world changed. Actor sync hangs off this.
DECLARE_MULTICAST_DELEGATE_OneParam(FOnSnapshotChanged, const FWorldSnapshot&);

/// A command the rules refused — the normal "you can't march there" case.
/// Carries the message and the RuleError variant name.
DECLARE_MULTICAST_DELEGATE_TwoParams(FOnCommandRejected, const FString& /*Message*/, const FString& /*Rule*/);

/// The connection itself broke. Milestone 1 shows this and stops; there is no
/// reconnect, because a half-applied campaign is worse than a clear stop.
DECLARE_MULTICAST_DELEGATE_OneParam(FOnSimTransportError, const FString&);

UCLASS()
class USimSubsystem : public UGameInstanceSubsystem
{
    GENERATED_BODY()

public:
    virtual void Initialize(FSubsystemCollectionBase& Collection) override;
    virtual void Deinitialize() override;

    /// Start a campaign. Connects (spawning the sidecar unless told to attach)
    /// and loads the campaign, off the game thread. `OnSnapshotChanged` fires
    /// when the world is ready.
    void StartCampaign(const FSidecarConfig& InConfig, const FString& Campaign = TEXT("britain"),
                       int32 Seed = 42);

    /// Queue a player command. Returns false if a request is already in flight —
    /// the protocol is serial and the UI should be showing a busy state anyway.
    UFUNCTION(BlueprintCallable, Category = "Sim")
    bool SendCommand(const FSimCommand& Cmd);

    /// Resolve the player's turn and all AI turns. Same serial rule as above.
    UFUNCTION(BlueprintCallable, Category = "Sim")
    bool EndTurn();

    /// The world as of the last reply. Safe to call every frame; it is a member
    /// read, not a round-trip. C++ callers get the reference and should keep it
    /// local — it is replaced wholesale on every reply.
    const FWorldSnapshot& GetSnapshot() const { return Snapshot; }

    /// The Blueprint-facing copy. Deliberately by value: UHT cannot express a
    /// borrowed struct, and a Blueprint holding a stale reference into a
    /// snapshot we have since replaced is a crash rather than a wrong number.
    UFUNCTION(BlueprintCallable, Category = "Sim", DisplayName = "Get Snapshot")
    FWorldSnapshot GetSnapshotCopy() const { return Snapshot; }

    /// True while a request is outstanding. Drive the "resolving turn" state and
    /// input blocking off this.
    UFUNCTION(BlueprintCallable, Category = "Sim")
    bool IsBusy() const { return bBusy; }

    UFUNCTION(BlueprintCallable, Category = "Sim")
    bool IsConnected() const { return Transport.IsValid() && Transport->IsConnected(); }

    FOnSimEvents OnSimEvents;
    FOnSnapshotChanged OnSnapshotChanged;
    FOnCommandRejected OnCommandRejected;
    FOnSimTransportError OnTransportError;

private:
    /// Run `Work` on a background thread and hand its result back on the game
    /// thread. The single choke point for every request, which is what keeps
    /// the serial guarantee honest.
    void RunAsync(TFunction<bool(FSocketSimTransport&, FSimResult&)> Work);

    /// Game thread. Publishes the new snapshot, then the events.
    void Complete(bool bTransportOk, const FSimResult& Result);

    /// Shared rather than unique so an in-flight worker keeps it alive even if
    /// the subsystem is torn down underneath it.
    TSharedPtr<FSocketSimTransport, ESPMode::ThreadSafe> Transport;

    FWorldSnapshot Snapshot;
    bool bBusy = false;
};
