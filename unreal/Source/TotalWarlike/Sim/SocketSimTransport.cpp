#include "SocketSimTransport.h"

#include "Common/TcpSocketBuilder.h"
#include "HAL/PlatformFileManager.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "SimWire.h"
#include "SocketSubsystem.h"
#include "Sockets.h"

DEFINE_LOG_CATEGORY_STATIC(LogSim, Log, All);

namespace
{
/// 4-byte big-endian length prefix, then that many bytes of msgpack — the frame
/// format in `sim/src/tw_sim/server.py`.
constexpr int32 HeaderSize = 4;
/// Matches the sidecar's MAX_FRAME. A prefix larger than this means the stream
/// desynced, and we should say so rather than try to allocate it.
constexpr uint32 MaxFrame = 1u << 20;

void WriteHeader(uint8* Out, uint32 Length)
{
    Out[0] = static_cast<uint8>(Length >> 24);
    Out[1] = static_cast<uint8>(Length >> 16);
    Out[2] = static_cast<uint8>(Length >> 8);
    Out[3] = static_cast<uint8>(Length);
}

uint32 ReadHeader(const uint8* In)
{
    return (static_cast<uint32>(In[0]) << 24) | (static_cast<uint32>(In[1]) << 16) |
           (static_cast<uint32>(In[2]) << 8) | static_cast<uint32>(In[3]);
}
} // namespace

FSocketSimTransport::FSocketSimTransport(const FSidecarConfig& InConfig) : Config(InConfig)
{
    if (Config.SimDir.IsEmpty())
    {
        Config.SimDir = FPaths::ConvertRelativePathToFull(
            FPaths::Combine(FPaths::ProjectDir(), TEXT(".."), TEXT("sim")));
    }
}

FSocketSimTransport::~FSocketSimTransport()
{
    Shutdown();
}

bool FSocketSimTransport::Initialize(const FString& Campaign, int32 Seed)
{
    TransportError.Empty();

    switch (Config.Mode)
    {
    case ESidecarMode::Attach:
        if (!Connect(Config.Port))
        {
            return false;
        }
        break;

    case ESidecarMode::Auto:
        // A failed attach is not an error: a stale port file just means the
        // sidecar it named is gone, so fall through to spawning our own.
        if (const int32 Advertised = ReadPortFile(); Advertised > 0 && Connect(Advertised))
        {
            TransportError.Empty();
            UE_LOG(LogSim, Log, TEXT("attached to the sidecar already on port %d"), Advertised);
            break;
        }
        TransportError.Empty();
        if (!SpawnSidecar())
        {
            return false;
        }
        break;

    case ESidecarMode::Spawn:
        if (!SpawnSidecar())
        {
            return false;
        }
        break;
    }

    FSimResult Result;
    if (!Request(tw::MakeInitRequest(Campaign, Seed), Result))
    {
        return false;
    }
    if (!Result.bOk)
    {
        // `init` has no rule failures — the campaign name is authored content
        // and a bad one panics the sidecar loudly by design — so an `ok: false`
        // here means something is wrong with the protocol, not with play.
        return Fail(FString::Printf(TEXT("init refused: %s"), *Result.Error));
    }

    UE_LOG(LogSim, Log, TEXT("simulation ready: campaign '%s', seed %d, turn %d"), *Campaign,
           Seed, Snapshot.Turn);
    return true;
}

bool FSocketSimTransport::SendCommand(const FSimCommand& Cmd, FSimResult& Out)
{
    return Request(tw::MakeCommandRequest(Cmd), Out);
}

bool FSocketSimTransport::EndTurn(FSimResult& Out)
{
    return Request(tw::MakeEndTurnRequest(), Out);
}

bool FSocketSimTransport::RefreshSnapshot()
{
    FSimResult Result;
    return Request(tw::MakeSnapshotRequest(), Result);
}

bool FSocketSimTransport::Request(const tw::FValue& Body, FSimResult& Out)
{
    Out = FSimResult();

    if (Socket == nullptr)
    {
        Out.Error = TEXT("not connected to the simulation");
        return Fail(Out.Error);
    }

    std::vector<uint8_t> Payload;
    try
    {
        Payload = tw::Pack(Body);
    }
    catch (const tw::FMsgPackError& Error)
    {
        // Encoding our own request cannot fail on well-formed input, so this is
        // a bug on this side rather than a broken peer.
        Out.Error = FString(UTF8_TO_TCHAR(Error.what()));
        return Fail(Out.Error);
    }

    TArray<uint8> Frame;
    Frame.SetNumUninitialized(HeaderSize + Payload.size());
    WriteHeader(Frame.GetData(), static_cast<uint32>(Payload.size()));
    FMemory::Memcpy(Frame.GetData() + HeaderSize, Payload.data(), Payload.size());

    if (!SendAll(Frame.GetData(), Frame.Num()))
    {
        Out.Error = TransportError;
        return false;
    }

    uint8 Header[HeaderSize];
    if (!ReceiveAll(Header, HeaderSize))
    {
        Out.Error = TransportError;
        return false;
    }

    const uint32 Length = ReadHeader(Header);
    if (Length > MaxFrame)
    {
        Out.Error = FString::Printf(TEXT("reply claims %u bytes, over the %u limit"), Length,
                                    MaxFrame);
        return Fail(Out.Error);
    }

    std::vector<uint8_t> ReplyBytes(Length);
    if (Length > 0 && !ReceiveAll(ReplyBytes.data(), static_cast<int32>(Length)))
    {
        Out.Error = TransportError;
        return false;
    }

    try
    {
        Out = tw::ParseResult(tw::Unpack(ReplyBytes), Snapshot);
    }
    catch (const tw::FMsgPackError& Error)
    {
        // A reply we cannot read means the two sides disagree about the
        // protocol. Dropping the connection is right: there is no recovering a
        // stream we have lost our place in.
        Out = FSimResult();
        Out.Error = FString::Printf(TEXT("malformed reply: %s"), UTF8_TO_TCHAR(Error.what()));
        return Fail(Out.Error);
    }

    return true;
}

int32 FSocketSimTransport::ReadPortFile() const
{
    const FString Path = FPaths::IsRelative(Config.PortFile)
                             ? FPaths::Combine(Config.SimDir, Config.PortFile)
                             : Config.PortFile;
    FString Contents;
    if (!FFileHelper::LoadFileToString(Contents, *Path))
    {
        return 0;
    }
    return FCString::Atoi(*Contents.TrimStartAndEnd());
}

bool FSocketSimTransport::SpawnSidecar()
{
    if (!FPlatformProcess::CreatePipe(SidecarStdoutRead, SidecarStdoutWrite))
    {
        return Fail(TEXT("could not create a pipe for the sidecar's stdout"));
    }

    // Through a login shell on purpose: `uv` lives in the user's PATH, and an
    // app launched from Finder inherits almost none of it.
    const FString Command =
        FString::Printf(TEXT("cd %s && exec uv run python -m tw_sim.server"), *Config.SimDir);
    const FString Shell = TEXT("/bin/sh");
    const FString Params = FString::Printf(TEXT("-lc \"%s\""), *Command.Replace(TEXT("\""), TEXT("\\\"")));

    SidecarProc = FPlatformProcess::CreateProc(*Shell, *Params, /*bLaunchDetached=*/false,
                                               /*bLaunchHidden=*/true,
                                               /*bLaunchReallyHidden=*/true, nullptr, 0,
                                               *Config.SimDir, SidecarStdoutWrite);
    if (!SidecarProc.IsValid())
    {
        return Fail(TEXT("could not start the simulation sidecar"));
    }

    // The sidecar prints the port it bound on stdout, then nothing else. Read
    // until we have a full line or the timeout expires.
    const double Deadline = FPlatformTime::Seconds() + Config.StartupTimeoutSeconds;
    FString Buffered;
    while (FPlatformTime::Seconds() < Deadline)
    {
        Buffered += FPlatformProcess::ReadPipe(SidecarStdoutRead);

        FString Line;
        FString Rest;
        if (Buffered.Split(TEXT("\n"), &Line, &Rest))
        {
            const int32 Port = FCString::Atoi(*Line.TrimStartAndEnd());
            if (Port <= 0)
            {
                return Fail(FString::Printf(TEXT("sidecar printed '%s', not a port"), *Line));
            }
            return Connect(Port);
        }

        if (!FPlatformProcess::IsProcRunning(SidecarProc))
        {
            return Fail(FString::Printf(TEXT("sidecar exited before printing a port: %s"),
                                        *Buffered));
        }
        FPlatformProcess::Sleep(0.02f);
    }

    return Fail(TEXT("timed out waiting for the sidecar to report its port"));
}

bool FSocketSimTransport::Connect(int32 InPort)
{
    if (InPort <= 0)
    {
        return Fail(TEXT("no port to connect to"));
    }

    ISocketSubsystem* Subsystem = ISocketSubsystem::Get(PLATFORM_SOCKETSUBSYSTEM);
    if (Subsystem == nullptr)
    {
        return Fail(TEXT("no socket subsystem"));
    }

    bool bParsed = false;
    const TSharedRef<FInternetAddr> Addr = Subsystem->CreateInternetAddr();
    Addr->SetIp(*Config.Host, bParsed);
    Addr->SetPort(InPort);
    if (!bParsed)
    {
        return Fail(FString::Printf(TEXT("'%s' is not an address"), *Config.Host));
    }

    Socket = FTcpSocketBuilder(TEXT("TwSim")).AsBlocking().Build();
    if (Socket == nullptr)
    {
        return Fail(TEXT("could not create a socket"));
    }

    // Nagle would sit on the small request frames waiting for company; every
    // frame here is a complete message that the other side is blocked on.
    Socket->SetNoDelay(true);

    if (!Socket->Connect(*Addr))
    {
        return Fail(FString::Printf(TEXT("could not connect to %s:%d"), *Config.Host, InPort));
    }

    Config.Port = InPort;
    return true;
}

bool FSocketSimTransport::SendAll(const uint8* Data, int32 Count)
{
    int32 Sent = 0;
    while (Sent < Count)
    {
        int32 Wrote = 0;
        if (!Socket->Send(Data + Sent, Count - Sent, Wrote) || Wrote <= 0)
        {
            return Fail(TEXT("the connection to the simulation dropped while sending"));
        }
        Sent += Wrote;
    }
    return true;
}

bool FSocketSimTransport::ReceiveAll(uint8* Data, int32 Count)
{
    const FTimespan Timeout = FTimespan::FromSeconds(Config.RequestTimeoutSeconds);
    const double Deadline = FPlatformTime::Seconds() + Config.RequestTimeoutSeconds;

    int32 Read = 0;
    while (Read < Count)
    {
        if (!Socket->Wait(ESocketWaitConditions::WaitForRead, Timeout))
        {
            return Fail(TEXT("the simulation did not reply in time"));
        }

        int32 Got = 0;
        if (!Socket->Recv(Data + Read, Count - Read, Got) || Got <= 0)
        {
            // Zero bytes on a readable socket is a clean close from the other
            // end — the sidecar died or was killed.
            return Fail(TEXT("the simulation closed the connection"));
        }
        Read += Got;

        if (FPlatformTime::Seconds() > Deadline)
        {
            return Fail(TEXT("the simulation did not finish replying in time"));
        }
    }
    return true;
}

bool FSocketSimTransport::Fail(const FString& Reason)
{
    TransportError = Reason;
    UE_LOG(LogSim, Error, TEXT("sim transport: %s"), *Reason);
    Shutdown();
    return false;
}

void FSocketSimTransport::Shutdown()
{
    if (Socket != nullptr)
    {
        Socket->Close();
        if (ISocketSubsystem* Subsystem = ISocketSubsystem::Get(PLATFORM_SOCKETSUBSYSTEM))
        {
            Subsystem->DestroySocket(Socket);
        }
        Socket = nullptr;
    }

    if (SidecarProc.IsValid())
    {
        // Only ours to kill in Spawn mode; in Attach mode the sidecar outlives
        // the editor session on purpose, which is the whole point of that mode.
        FPlatformProcess::TerminateProc(SidecarProc, /*KillTree=*/true);
        FPlatformProcess::CloseProc(SidecarProc);
        SidecarProc.Reset();
    }

    if (SidecarStdoutRead != nullptr || SidecarStdoutWrite != nullptr)
    {
        FPlatformProcess::ClosePipe(SidecarStdoutRead, SidecarStdoutWrite);
        SidecarStdoutRead = nullptr;
        SidecarStdoutWrite = nullptr;
    }
}
