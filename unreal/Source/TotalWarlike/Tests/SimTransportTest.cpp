// Automation tests for the bridge, run with:
//
//   UnrealEditor-Cmd TotalWarlike.uproject -ExecCmds="Automation RunTests TotalWarlike.Sim" \
//                    -unattended -nullrhi -nosplash -testexit="Automation Test Queue Empty"
//
// The transport test spawns a real Python sidecar, so it needs `uv` on PATH and
// `sim/` beside the .uproject. The codec tests need neither and are the ones
// that catch a protocol mistake in a second rather than a minute — see also
// unreal/Tests/wire_test.cpp, which runs the same codec with no engine at all.

#include "Misc/AutomationTest.h"

#include "../Sim/MsgPack.h"
#include "../Sim/SimWire.h"
#include "../Sim/SocketSimTransport.h"

#if WITH_DEV_AUTOMATION_TESTS

namespace
{
// A strongly-typed enum as of 5.8, so this cannot be the int32 the older
// examples use.
constexpr EAutomationTestFlags TestFlags = EAutomationTestFlags::EditorContext |
                                           EAutomationTestFlags::ClientContext |
                                           EAutomationTestFlags::ProductFilter;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FMsgPackRoundTripTest, "TotalWarlike.Sim.MsgPackRoundTrip",
                                 TestFlags)

bool FMsgPackRoundTripTest::RunTest(const FString& Parameters)
{
    // Every width the encoder can choose, so a bad boundary shows up here rather
    // than as a corrupt frame on a campaign that happened to have 300 turns.
    const int64 Values[] = {0, 1, 127, 128, 255, 256, 65535, 65536, -1, -32, -33, -128, -129,
                            -32768, -32769, INT32_MIN, INT64_MAX};
    for (const int64 Value : Values)
    {
        const tw::FValue Decoded = tw::Unpack(tw::Pack(tw::FValue(Value)));
        TestEqual(FString::Printf(TEXT("int %lld survives"), Value), Decoded.AsInt(), Value);
    }

    const tw::FValue Nested(tw::FValue::FMap{
        {"op", tw::FValue("command")},
        {"cmd", tw::FValue(tw::FValue::FMap{{"kind", tw::FValue("move")},
                                            {"army", tw::FValue(42)},
                                            {"to", tw::FValue(7)}})},
    });
    const tw::FValue Back = tw::Unpack(tw::Pack(Nested));
    TestEqual(TEXT("op survives"), FString(Back.At("op").AsStr().c_str()), TEXT("command"));
    TestEqual(TEXT("nested int survives"), Back.At("cmd").At("army").AsInt(), (int64)42);

    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FMsgPackRejectsGarbageTest, "TotalWarlike.Sim.MsgPackRejectsGarbage",
                                 TestFlags)

bool FMsgPackRejectsGarbageTest::RunTest(const FString& Parameters)
{
    // A truncated frame must throw rather than read past the buffer.
    bool bThrew = false;
    try
    {
        tw::Unpack(std::vector<uint8_t>{0xDC, 0x00});
    }
    catch (const tw::FMsgPackError&)
    {
        bThrew = true;
    }
    TestTrue(TEXT("a truncated array is rejected"), bThrew);

    bThrew = false;
    try
    {
        // Two documents in one frame: the length prefix must have lied.
        tw::Unpack(std::vector<uint8_t>{0x01, 0x02});
    }
    catch (const tw::FMsgPackError&)
    {
        bThrew = true;
    }
    TestTrue(TEXT("trailing bytes are rejected"), bThrew);

    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(FSimTransportTest, "TotalWarlike.Sim.Transport", TestFlags)

bool FSimTransportTest::RunTest(const FString& Parameters)
{
    FSidecarConfig Config;
    Config.Mode = ESidecarMode::Spawn;

    FSocketSimTransport Transport(Config);
    if (!TestTrue(TEXT("the sidecar starts and loads britain"), Transport.Initialize(TEXT("britain"), 42)))
    {
        AddError(FString::Printf(TEXT("transport: %s"), *Transport.GetTransportError()));
        return false;
    }

    const FWorldSnapshot& Start = Transport.GetSnapshot();
    TestEqual(TEXT("britain has 12 provinces"), Start.Provinces.Num(), 12);
    TestEqual(TEXT("britain has 5 factions"), Start.Factions.Num(), 5);
    TestEqual(TEXT("a fresh campaign is on turn 1"), Start.Turn, 1);
    TestTrue(TEXT("the campaign opens on the player"), Start.IsPlayerTurn());

    // A command the rules must refuse: an army cannot march to a province it is
    // not adjacent to. This is the `ok: false` path, and it must not be
    // reported as a transport failure.
    const FArmyState* Army = Start.Armies.FindByPredicate(
        [&Start](const FArmyState& A) { return A.Owner == Start.Current; });
    if (Army != nullptr)
    {
        FSimResult Refused;
        TestTrue(TEXT("an illegal move still round-trips"),
                 Transport.SendCommand(FSimCommand::Move(Army->Id, 11), Refused));
        TestFalse(TEXT("an illegal move is refused"), Refused.bOk);
        TestFalse(TEXT("a refusal names its rule"), Refused.Rule.IsEmpty());
    }

    FSimResult Turn;
    if (!TestTrue(TEXT("EndTurn round-trips"), Transport.EndTurn(Turn)))
    {
        AddError(FString::Printf(TEXT("transport: %s"), *Transport.GetTransportError()));
        return false;
    }
    TestTrue(TEXT("EndTurn succeeds"), Turn.bOk);
    TestTrue(TEXT("EndTurn produces events"), Turn.Events.Num() > 0);
    TestEqual(TEXT("EndTurn advances the turn"), Transport.GetSnapshot().Turn, 2);
    TestTrue(TEXT("control comes back to the player"),
             Transport.GetSnapshot().IsPlayerTurn());

    return true;
}

#endif // WITH_DEV_AUTOMATION_TESTS
