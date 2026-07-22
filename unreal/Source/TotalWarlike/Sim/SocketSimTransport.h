// ISimTransport over a loopback TCP socket, with the Python sidecar as a child
// process.
//
// Every call here BLOCKS — `EndTurn` for 50-500ms, since it resolves every AI
// faction. Nothing in this class may be called from the game thread; that is
// USimSubsystem's job, and it is why these methods are deliberately not
// Blueprint-callable.

#pragma once

#include "CoreMinimal.h"
#include "HAL/PlatformProcess.h"

#include "MsgPack.h"
#include "SimTransport.h"

class FSocket;

/// Where the sidecar comes from.
enum class ESidecarMode : uint8
{
    /// Spawn `uv run python -m tw_sim.server`, read the port it prints, and kill
    /// it on shutdown. What a packaged build always does.
    Spawn,
    /// Connect to a sidecar somebody else is running, on a known port. This is
    /// the hot-reload path: restart Python, keep the editor open.
    Attach,
    /// Attach if `sim/.sim-port` names a sidecar that answers, otherwise spawn.
    /// The right default in the editor, and the reason `make py-server` writes
    /// that file at all.
    Auto,
};

struct FSidecarConfig
{
    ESidecarMode Mode = ESidecarMode::Spawn;
    /// Read in Attach mode; filled in by the others once connected.
    int32 Port = 0;
    /// Where a hand-started sidecar advertises itself. Relative paths are
    /// resolved against `SimDir`.
    FString PortFile = TEXT(".sim-port");
    FString Host = TEXT("127.0.0.1");
    /// Absolute path to the `sim/` directory. Defaults to the one beside the
    /// .uproject.
    FString SimDir;
    /// How long to wait for a spawned sidecar to print its port.
    double StartupTimeoutSeconds = 20.0;
    /// How long a single request may take. `EndTurn` on a big campaign is the
    /// worst case and is nowhere near this.
    double RequestTimeoutSeconds = 30.0;
};

class FSocketSimTransport final : public ISimTransport
{
public:
    explicit FSocketSimTransport(const FSidecarConfig& InConfig);
    virtual ~FSocketSimTransport() override;

    virtual bool Initialize(const FString& Campaign, int32 Seed) override;
    virtual bool SendCommand(const FSimCommand& Cmd, FSimResult& Out) override;
    virtual bool EndTurn(FSimResult& Out) override;
    virtual const FWorldSnapshot& GetSnapshot() const override { return Snapshot; }
    virtual const FString& GetTransportError() const override { return TransportError; }

    /// Re-read the world without changing it. Not on the interface — the cached
    /// snapshot is authoritative and this exists for tests and for recovering
    /// after a reattach.
    bool RefreshSnapshot();

    /// Close the socket and, in Spawn mode, terminate the child. Safe to call
    /// twice; the destructor calls it.
    void Shutdown();

    bool IsConnected() const { return Socket != nullptr; }

private:
    /// Encode, send, wait for the reply, decode. The only place that touches the
    /// socket.
    bool Request(const tw::FValue& Body, FSimResult& Out);

    /// The port a hand-started sidecar advertised, or 0 if there is no file.
    int32 ReadPortFile() const;
    bool SpawnSidecar();
    bool Connect(int32 InPort);
    bool SendAll(const uint8* Data, int32 Count);
    bool ReceiveAll(uint8* Data, int32 Count);
    /// Records `Reason`, tears the connection down, and returns false so callers
    /// can `return Fail(...)`.
    bool Fail(const FString& Reason);

    FSidecarConfig Config;
    FSocket* Socket = nullptr;
    FProcHandle SidecarProc;
    void* SidecarStdoutRead = nullptr;
    void* SidecarStdoutWrite = nullptr;

    FWorldSnapshot Snapshot;
    FString TransportError;
};
