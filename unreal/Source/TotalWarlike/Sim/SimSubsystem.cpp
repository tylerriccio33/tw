#include "SimSubsystem.h"

#include "Async/Async.h"

DEFINE_LOG_CATEGORY_STATIC(LogSimSubsystem, Log, All);

void USimSubsystem::Initialize(FSubsystemCollectionBase& Collection)
{
    Super::Initialize(Collection);
    // No connection here on purpose. Spawning a Python process as a side effect
    // of the game instance coming up would fire in the asset editor, in
    // commandlets, and in tests; the map asks for a campaign when it wants one.
}

void USimSubsystem::Deinitialize()
{
    if (Transport.IsValid())
    {
        // A worker may still be mid-request. It holds its own reference to the
        // transport, so closing the socket here just makes its call fail — the
        // reply is dropped because `Complete` checks the weak pointer.
        Transport->Shutdown();
        Transport.Reset();
    }
    Super::Deinitialize();
}

void USimSubsystem::StartCampaign(const FSidecarConfig& InConfig, const FString& Campaign,
                                  int32 Seed)
{
    if (bBusy)
    {
        UE_LOG(LogSimSubsystem, Warning, TEXT("StartCampaign while a request is in flight"));
        return;
    }

    if (Transport.IsValid())
    {
        Transport->Shutdown();
    }
    Transport = MakeShared<FSocketSimTransport, ESPMode::ThreadSafe>(InConfig);

    RunAsync([Campaign, Seed](FSocketSimTransport& Sim, FSimResult& Out) {
        if (!Sim.Initialize(Campaign, Seed))
        {
            return false;
        }
        // Initialize already cached the snapshot; the empty result is enough to
        // make Complete publish it with no events attached.
        Out.bOk = true;
        return true;
    });
}

bool USimSubsystem::SendCommand(const FSimCommand& Cmd)
{
    if (bBusy || !IsConnected())
    {
        return false;
    }
    RunAsync([Cmd](FSocketSimTransport& Sim, FSimResult& Out) { return Sim.SendCommand(Cmd, Out); });
    return true;
}

bool USimSubsystem::EndTurn()
{
    if (bBusy || !IsConnected())
    {
        return false;
    }
    RunAsync([](FSocketSimTransport& Sim, FSimResult& Out) { return Sim.EndTurn(Out); });
    return true;
}

void USimSubsystem::RunAsync(TFunction<bool(FSocketSimTransport&, FSimResult&)> Work)
{
    check(IsInGameThread());
    check(Transport.IsValid());

    bBusy = true;

    TWeakObjectPtr<USimSubsystem> WeakThis(this);
    TSharedPtr<FSocketSimTransport, ESPMode::ThreadSafe> Pinned = Transport;

    AsyncTask(ENamedThreads::AnyBackgroundThreadNormalTask,
              [WeakThis, Pinned, Work = MoveTemp(Work)]() {
                  FSimResult Result;
                  const bool bTransportOk = Work(*Pinned, Result);
                  if (!bTransportOk)
                  {
                      Result.Error = Pinned->GetTransportError();
                  }

                  // Back to the game thread before anything touches an actor.
                  AsyncTask(ENamedThreads::GameThread, [WeakThis, Pinned, bTransportOk, Result]() {
                      if (USimSubsystem* Self = WeakThis.Get())
                      {
                          // A campaign restarted mid-request would land this
                          // reply on the wrong transport.
                          if (Self->Transport == Pinned)
                          {
                              Self->Complete(bTransportOk, Result);
                          }
                      }
                  });
              });
}

void USimSubsystem::Complete(bool bTransportOk, const FSimResult& Result)
{
    check(IsInGameThread());
    bBusy = false;

    if (!bTransportOk)
    {
        UE_LOG(LogSimSubsystem, Error, TEXT("transport error: %s"), *Result.Error);
        OnTransportError.Broadcast(Result.Error);
        return;
    }

    // The snapshot first: an event handler animating a fallen city should be
    // able to read the province's new owner straight away.
    Snapshot = Transport->GetSnapshot();
    OnSnapshotChanged.Broadcast(Snapshot);

    if (!Result.bOk)
    {
        // A refused command. The world did not change and this is not an error
        // in any real sense — the player clicked somewhere they cannot go.
        OnCommandRejected.Broadcast(Result.Error, Result.Rule);
        return;
    }

    if (Result.Events.Num() > 0)
    {
        OnSimEvents.Broadcast(Result.Events);
    }
}
