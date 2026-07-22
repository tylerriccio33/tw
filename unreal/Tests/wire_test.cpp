// The bridge, tested without Unreal.
//
// `make cpp-test` compiles this against Sim/MsgPack.cpp and Map/ProvinceLookup.cpp
// with plain clang++ and runs it against a real sidecar. That is the whole
// reason those two files are free of Unreal types: a protocol mistake shows up
// in about two seconds here, where the equivalent automation test costs a full
// editor build first.
//
// It lives outside Source/ deliberately. UBT compiles every .cpp under a
// module's directory, and this one has a main() and talks to POSIX sockets
// directly — in the module it would fight the engine for the entry point.
//
// What this cannot cover is Sim/SimWire.cpp, which speaks FString and TArray.
// So it checks the next best thing: that every key SimWire reads is actually
// present, with the type it expects, in a snapshot from the live simulation.
// That is where a port bug would really live — a renamed field, not a broken
// switch — and it fails loudly the moment the two sides drift apart.

#include "../Source/TotalWarlike/Map/ProvinceLookup.h"
#include "../Source/TotalWarlike/Sim/MsgPack.h"

#include <arpa/inet.h>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <string>
#include <csignal>
#include <sys/socket.h>
#include <sys/wait.h>
#include <unistd.h>
#include <vector>

using tw::FValue;

namespace
{

int Failures = 0;

void Check(bool Condition, const std::string& What)
{
    if (Condition)
    {
        std::cout << "  ok   " << What << "\n";
    }
    else
    {
        std::cout << "  FAIL " << What << "\n";
        ++Failures;
    }
}

template <typename T>
void CheckEq(const T& Got, const T& Want, const std::string& What)
{
    if (Got == Want)
    {
        std::cout << "  ok   " << What << "\n";
    }
    else
    {
        std::cout << "  FAIL " << What << " (got " << Got << ", want " << Want << ")\n";
        ++Failures;
    }
}

/// Assert a map has `Key` and that reading it as `Read` does not throw. This is
/// the SimWire field-name check.
void CheckField(const FValue& Map, const char* Key, FValue::EType Want)
{
    const FValue* Found = Map.Find(Key);
    if (Found == nullptr)
    {
        std::cout << "  FAIL missing field '" << Key << "'\n";
        ++Failures;
        return;
    }
    // Optional fields arrive as nil, which is a shape the parser handles.
    if (Found->GetType() != Want && !Found->IsNil())
    {
        std::cout << "  FAIL field '" << Key << "' has the wrong type\n";
        ++Failures;
    }
}

// ---------------------------------------------------------------- codec ----

void TestCodec()
{
    std::cout << "codec\n";

    const long long Values[] = {0, 1, 127, 128, 255, 256, 65535, 65536, 4294967296LL,
                                -1, -32, -33, -128, -129, -32768, -32769, -2147483649LL};
    bool bAllSurvive = true;
    for (const long long Value : Values)
    {
        bAllSurvive &= tw::Unpack(tw::Pack(FValue(static_cast<int64_t>(Value)))).AsInt() == Value;
    }
    Check(bAllSurvive, "every integer width round-trips");

    Check(tw::Unpack(tw::Pack(FValue(true))).AsBool(), "bool round-trips");
    Check(tw::Unpack(tw::Pack(FValue())).IsNil(), "nil round-trips");
    CheckEq(tw::Unpack(tw::Pack(FValue("britain"))).AsStr(), std::string("britain"),
            "string round-trips");

    // A string long enough to need str8 rather than fixstr.
    const std::string Long(300, 'x');
    CheckEq(tw::Unpack(tw::Pack(FValue(Long))).AsStr(), Long, "a 300-byte string round-trips");

    bool bThrew = false;
    try
    {
        tw::Unpack(std::vector<uint8_t>{0xDC, 0x00});
    }
    catch (const tw::FMsgPackError&)
    {
        bThrew = true;
    }
    Check(bThrew, "a truncated frame is rejected");

    bThrew = false;
    try
    {
        tw::Unpack(std::vector<uint8_t>{0x01, 0x02});
    }
    catch (const tw::FMsgPackError&)
    {
        bThrew = true;
    }
    Check(bThrew, "trailing bytes are rejected");

    bThrew = false;
    try
    {
        tw::Unpack(tw::Pack(FValue(int64_t{1}))).AsStr();
    }
    catch (const tw::FMsgPackError&)
    {
        bThrew = true;
    }
    Check(bThrew, "reading an int as a string is rejected");
}

void TestProvinceLookup()
{
    std::cout << "province lookup\n";

    const std::vector<tw::FProvinceSite> Sites{{0.0, 0.0}, {100.0, 0.0}, {0.0, 100.0}};
    CheckEq(tw::ProvinceAt(Sites, 5.0, 5.0), std::size_t{0}, "nearest site wins");
    CheckEq(tw::ProvinceAt(Sites, 95.0, 1.0), std::size_t{1}, "nearest site wins to the east");
    CheckEq(tw::ProvinceAt(Sites, 50.0, 0.0), std::size_t{0}, "a tie breaks to the lower id");
    CheckEq(tw::ProvinceAt({}, 0.0, 0.0), tw::kNoProvince, "no sites means no province");
}

// -------------------------------------------------------------- sidecar ----

/// A blocking client for the length-prefixed framing, deliberately written the
/// long way so it shares no code with the implementation under test.
class FClient
{
public:
    explicit FClient(int InFd) : Fd(InFd) {}
    ~FClient()
    {
        if (Fd >= 0) close(Fd);
    }

    FValue Call(const FValue& Request)
    {
        const std::vector<uint8_t> Body = tw::Pack(Request);
        std::vector<uint8_t> Frame(4 + Body.size());
        const uint32_t N = static_cast<uint32_t>(Body.size());
        Frame[0] = static_cast<uint8_t>(N >> 24);
        Frame[1] = static_cast<uint8_t>(N >> 16);
        Frame[2] = static_cast<uint8_t>(N >> 8);
        Frame[3] = static_cast<uint8_t>(N);
        std::memcpy(Frame.data() + 4, Body.data(), Body.size());
        WriteAll(Frame.data(), Frame.size());

        uint8_t Header[4];
        ReadAll(Header, 4);
        const uint32_t Len = (static_cast<uint32_t>(Header[0]) << 24) |
                             (static_cast<uint32_t>(Header[1]) << 16) |
                             (static_cast<uint32_t>(Header[2]) << 8) | Header[3];
        std::vector<uint8_t> Reply(Len);
        ReadAll(Reply.data(), Len);
        return tw::Unpack(Reply);
    }

private:
    void WriteAll(const uint8_t* Data, std::size_t Count)
    {
        std::size_t Sent = 0;
        while (Sent < Count)
        {
            const ssize_t Wrote = write(Fd, Data + Sent, Count - Sent);
            if (Wrote <= 0) throw std::runtime_error("short write to the sidecar");
            Sent += static_cast<std::size_t>(Wrote);
        }
    }

    void ReadAll(uint8_t* Data, std::size_t Count)
    {
        std::size_t Got = 0;
        while (Got < Count)
        {
            const ssize_t Read = read(Fd, Data + Got, Count - Got);
            if (Read <= 0) throw std::runtime_error("the sidecar closed the connection");
            Got += static_cast<std::size_t>(Read);
        }
    }

    int Fd = -1;
};

int ConnectTo(int Port)
{
    const int Fd = socket(AF_INET, SOCK_STREAM, 0);
    sockaddr_in Addr{};
    Addr.sin_family = AF_INET;
    Addr.sin_port = htons(static_cast<uint16_t>(Port));
    Addr.sin_addr.s_addr = inet_addr("127.0.0.1");
    if (connect(Fd, reinterpret_cast<sockaddr*>(&Addr), sizeof(Addr)) != 0)
    {
        close(Fd);
        throw std::runtime_error("could not connect to the sidecar");
    }
    const int One = 1;
    setsockopt(Fd, IPPROTO_TCP, TCP_NODELAY, &One, sizeof(One));
    return Fd;
}

/// The fields Sim/SimWire.cpp reads out of a snapshot. Kept as a literal list on
/// purpose: it is a second, independent statement of the schema, so a rename on
/// one side cannot quietly agree with itself.
void CheckSnapshotSchema(const FValue& Snapshot)
{
    CheckField(Snapshot, "turn", FValue::EType::Int);
    CheckField(Snapshot, "current", FValue::EType::Int);
    CheckField(Snapshot, "winner", FValue::EType::Int);
    CheckField(Snapshot, "factions", FValue::EType::Array);
    CheckField(Snapshot, "provinces", FValue::EType::Array);
    CheckField(Snapshot, "armies", FValue::EType::Array);
    CheckField(Snapshot, "proposals", FValue::EType::Array);

    const FValue& Faction = Snapshot.At("factions").AsArray().at(0);
    for (const char* Key : {"id", "treasury", "income", "upkeep"})
        CheckField(Faction, Key, FValue::EType::Int);
    CheckField(Faction, "name", FValue::EType::Str);
    for (const char* Key : {"is_player", "is_rebel", "alive"})
        CheckField(Faction, Key, FValue::EType::Bool);
    for (const char* Key : {"relations", "opinions"})
        CheckField(Faction, Key, FValue::EType::Array);

    const FValue& Province = Snapshot.At("provinces").AsArray().at(0);
    for (const char* Key : {"id", "tier", "owner", "population", "walls", "farm", "market",
                            "barracks", "besieged_by"})
        CheckField(Province, Key, FValue::EType::Int);
    for (const char* Key : {"name", "city"})
        CheckField(Province, Key, FValue::EType::Str);
    for (const char* Key : {"adjacent", "garrison", "recruit_queue", "construction"})
        CheckField(Province, Key, FValue::EType::Array);
    CheckEq(Province.At("garrison").AsArray().size(), std::size_t{3},
            "garrison is [melee, archer, cav]");

    const FValue& Army = Snapshot.At("armies").AsArray().at(0);
    for (const char* Key : {"id", "owner", "location", "regiments", "mp", "size"})
        CheckField(Army, Key, FValue::EType::Int);
    CheckField(Army, "general", FValue::EType::Str);
    CheckField(Army, "force", FValue::EType::Array);
}

void TestAgainstSidecar(int Port)
{
    std::cout << "sidecar\n";
    FClient Client(ConnectTo(Port));

    const FValue Init = Client.Call(FValue(FValue::FMap{{"op", FValue("init")},
                                                        {"campaign", FValue("britain")},
                                                        {"seed", FValue(42)}}));
    Check(Init.At("ok").AsBool(), "init succeeds");

    const FValue& Snapshot = Init.At("snapshot");
    CheckEq(Snapshot.At("turn").AsInt(), int64_t{1}, "a fresh campaign is on turn 1");
    CheckEq(Snapshot.At("provinces").AsArray().size(), std::size_t{12}, "britain has 12 provinces");
    CheckEq(Snapshot.At("factions").AsArray().size(), std::size_t{5}, "britain has 5 factions");
    Check(Snapshot.At("winner").IsNil(), "nobody has won yet");
    CheckSnapshotSchema(Snapshot);

    // The refusal path. Province 11 is not adjacent to England's opening
    // position, so the rules must say no — and say which rule.
    const int64_t Army = Snapshot.At("armies").AsArray().at(0).At("id").AsInt();
    const FValue Refused = Client.Call(FValue(FValue::FMap{
        {"op", FValue("command")},
        {"cmd", FValue(FValue::FMap{{"kind", FValue("move")},
                                    {"army", FValue(Army)},
                                    {"to", FValue(11)}})}}));
    Check(!Refused.At("ok").AsBool(), "an illegal move is refused");
    Check(Refused.Find("rule") != nullptr, "a refusal names its rule");
    std::cout << "       rule: " << Refused.At("rule").AsStr() << "\n";

    const FValue Turn = Client.Call(FValue(FValue::FMap{{"op", FValue("end_turn")}}));
    Check(Turn.At("ok").AsBool(), "end_turn succeeds");
    Check(!Turn.At("events").AsArray().empty(), "end_turn produces events");
    CheckEq(Turn.At("snapshot").At("turn").AsInt(), int64_t{2}, "end_turn advances the turn");

    // Every event must carry a `kind`, which is what the parser dispatches on.
    bool bAllTagged = true;
    for (const FValue& Event : Turn.At("events").AsArray())
    {
        bAllTagged &= Event.Find("kind") != nullptr &&
                      Event.At("kind").GetType() == FValue::EType::Str;
    }
    Check(bAllTagged, "every event carries a kind tag");

    const FValue Bad = Client.Call(FValue(FValue::FMap{{"op", FValue("nonsense")}}));
    Check(!Bad.At("ok").AsBool(), "an unknown op is refused rather than ignored");
}

/// Start `python -m tw_sim.server` and read the port it prints.
///
/// fork/exec rather than popen: the sidecar serves until it is killed, and
/// pclose would wait for an exit that never comes.
pid_t SidecarPid = -1;
FILE* SidecarOut = nullptr;

int StartSidecar(const std::string& SimDir)
{
    int Pipe[2];
    if (pipe(Pipe) != 0)
    {
        throw std::runtime_error("could not create a pipe");
    }

    SidecarPid = fork();
    if (SidecarPid < 0)
    {
        throw std::runtime_error("could not fork");
    }
    if (SidecarPid == 0)
    {
        close(Pipe[0]);
        dup2(Pipe[1], STDOUT_FILENO);
        close(Pipe[1]);
        // Its own process group, so killing it cannot take the test with it.
        setpgid(0, 0);
        const std::string Command = "cd '" + SimDir + "' && exec uv run python -m tw_sim.server";
        execl("/bin/sh", "sh", "-c", Command.c_str(), nullptr);
        _exit(127);
    }

    close(Pipe[1]);
    SidecarOut = fdopen(Pipe[0], "r");
    char Line[64] = {};
    if (fgets(Line, sizeof(Line), SidecarOut) == nullptr)
    {
        throw std::runtime_error("the sidecar exited without printing a port");
    }
    return std::atoi(Line);
}

void StopSidecar()
{
    if (SidecarPid > 0)
    {
        kill(-SidecarPid, SIGTERM);
        int Status = 0;
        waitpid(SidecarPid, &Status, 0);
        SidecarPid = -1;
    }
    if (SidecarOut != nullptr)
    {
        fclose(SidecarOut);
        SidecarOut = nullptr;
    }
}

} // namespace

int main(int argc, char** argv)
{
    const std::string SimDir = argc > 1 ? argv[1] : "../sim";

    TestCodec();
    TestProvinceLookup();

    try
    {
        const int Port = StartSidecar(SimDir);
        std::cout << "sidecar listening on " << Port << "\n";
        TestAgainstSidecar(Port);
    }
    catch (const std::exception& Error)
    {
        std::cout << "  FAIL " << Error.what() << "\n";
        ++Failures;
    }

    StopSidecar();

    std::cout << (Failures == 0 ? "\nall checks passed\n"
                                : "\n" + std::to_string(Failures) + " check(s) failed\n");
    return Failures == 0 ? 0 : 1;
}
