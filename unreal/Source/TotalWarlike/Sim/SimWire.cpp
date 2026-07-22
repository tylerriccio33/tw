#include "SimWire.h"

namespace tw
{
namespace
{

FString ToFString(const std::string& In)
{
    return FString(UTF8_TO_TCHAR(In.c_str()));
}

std::string ToStdString(const FString& In)
{
    return std::string(TCHAR_TO_UTF8(*In));
}

/// The `[melee, archer, cav]` triples: `force`, `garrison`, `recruit_queue`.
FSimForce ParseForce(const FValue& Value)
{
    const FValue::FArray& Items = Value.AsArray();
    if (Items.size() != 3)
    {
        throw FMsgPackError("force must be [melee, archer, cav]");
    }
    FSimForce Out;
    Out.Melee = static_cast<int32>(Items[0].AsInt());
    Out.Archer = static_cast<int32>(Items[1].AsInt());
    Out.Cav = static_cast<int32>(Items[2].AsInt());
    return Out;
}

TArray<int32> ParseIntArray(const FValue& Value)
{
    TArray<int32> Out;
    for (const FValue& Item : Value.AsArray())
    {
        Out.Add(static_cast<int32>(Item.AsInt()));
    }
    return Out;
}

int32 Int(const FValue& Map, const char* Key)
{
    return static_cast<int32>(Map.At(Key).AsInt());
}

FFactionState ParseFaction(const FValue& In)
{
    FFactionState Out;
    Out.Id = Int(In, "id");
    Out.Name = ToFString(In.At("name").AsStr());
    Out.Treasury = Int(In, "treasury");
    Out.Income = Int(In, "income");
    Out.Upkeep = Int(In, "upkeep");
    Out.bIsPlayer = In.At("is_player").AsBool();
    Out.bIsRebel = In.At("is_rebel").AsBool();
    Out.bAlive = In.At("alive").AsBool();
    Out.Relations = ParseIntArray(In.At("relations"));
    Out.Opinions = ParseIntArray(In.At("opinions"));
    return Out;
}

FProvinceState ParseProvince(const FValue& In)
{
    FProvinceState Out;
    Out.Id = Int(In, "id");
    Out.Name = ToFString(In.At("name").AsStr());
    Out.City = ToFString(In.At("city").AsStr());
    Out.Tier = Int(In, "tier");
    Out.Owner = Int(In, "owner");
    Out.Population = Int(In, "population");
    Out.Adjacent = ParseIntArray(In.At("adjacent"));
    Out.Garrison = ParseForce(In.At("garrison"));
    Out.Walls = Int(In, "walls");
    Out.Farm = Int(In, "farm");
    Out.Market = Int(In, "market");
    Out.Barracks = Int(In, "barracks");
    Out.RecruitQueue = ParseForce(In.At("recruit_queue"));

    // `construction` is either nil or the pair [building, turns_left]. It is the
    // only field on the wire that changes *shape* rather than just value.
    const FValue& Construction = In.At("construction");
    if (Construction.IsNil())
    {
        Out.Construction = TW_NONE;
        Out.ConstructionTurnsLeft = 0;
    }
    else
    {
        const FValue::FArray& Pair = Construction.AsArray();
        if (Pair.size() != 2)
        {
            throw FMsgPackError("construction must be [building, turns_left]");
        }
        Out.Construction = static_cast<int32>(Pair[0].AsInt());
        Out.ConstructionTurnsLeft = static_cast<int32>(Pair[1].AsInt());
    }

    Out.BesiegedBy = static_cast<int32>(In.IntOr("besieged_by", TW_NONE));
    return Out;
}

FArmyState ParseArmy(const FValue& In)
{
    FArmyState Out;
    Out.Id = Int(In, "id");
    Out.Owner = Int(In, "owner");
    Out.Location = Int(In, "location");
    Out.Force = ParseForce(In.At("force"));
    Out.Regiments = Int(In, "regiments");
    Out.Mp = Int(In, "mp");
    Out.General = ToFString(In.At("general").AsStr());
    Out.Size = Int(In, "size");
    return Out;
}

FProposalState ParseProposal(const FValue& In)
{
    FProposalState Out;
    Out.Id = Int(In, "id");
    Out.From = Int(In, "from");
    Out.To = Int(In, "to");
    Out.Treaty = Int(In, "treaty");
    return Out;
}

} // namespace

FValue MakeInitRequest(const FString& Campaign, int32 Seed)
{
    return FValue(FValue::FMap{
        {"op", FValue("init")},
        {"campaign", FValue(ToStdString(Campaign))},
        {"seed", FValue(static_cast<int64_t>(Seed))},
    });
}

FValue MakeCommandRequest(const FSimCommand& Cmd)
{
    FValue::FMap Payload{{"kind", FValue(ToStdString(Cmd.Kind))}};
    for (const TPair<FString, int32>& Field : Cmd.Ints)
    {
        Payload.emplace(ToStdString(Field.Key), FValue(static_cast<int64_t>(Field.Value)));
    }
    for (const TPair<FString, bool>& Field : Cmd.Bools)
    {
        Payload.emplace(ToStdString(Field.Key), FValue(Field.Value));
    }
    return FValue(FValue::FMap{{"op", FValue("command")}, {"cmd", FValue(std::move(Payload))}});
}

FValue MakeEndTurnRequest()
{
    return FValue(FValue::FMap{{"op", FValue("end_turn")}});
}

FValue MakeSnapshotRequest()
{
    return FValue(FValue::FMap{{"op", FValue("snapshot")}});
}

FSimEvent ParseEvent(const FValue& Event)
{
    FSimEvent Out;
    Out.Kind = ToFString(Event.At("kind").AsStr());
    for (const auto& [Key, Value] : Event.AsMap())
    {
        if (Key == "kind")
        {
            continue;
        }
        const FString Field = ToFString(Key);
        switch (Value.GetType())
        {
        case FValue::EType::Int:
            Out.Ints.Add(Field, static_cast<int32>(Value.AsInt()));
            break;
        case FValue::EType::Bool:
            // Booleans join the ints as 0/1: no consumer distinguishes them, and
            // a third map for two fields is not worth carrying.
            Out.Ints.Add(Field, Value.AsBool() ? 1 : 0);
            break;
        case FValue::EType::Str:
            Out.Strings.Add(Field, ToFString(Value.AsStr()));
            break;
        case FValue::EType::Nil:
            // An absent optional — `from` on a rebel-raised army, say. Leaving
            // the key out is what makes FSimEvent::Int's fallback correct.
            break;
        default:
            throw FMsgPackError("event field '" + Key + "' has an unexpected type");
        }
    }
    return Out;
}

FWorldSnapshot ParseSnapshot(const FValue& Snapshot)
{
    FWorldSnapshot Out;
    Out.Turn = Int(Snapshot, "turn");
    Out.Current = Int(Snapshot, "current");
    Out.Winner = static_cast<int32>(Snapshot.IntOr("winner", TW_NONE));

    for (const FValue& Item : Snapshot.At("factions").AsArray())
    {
        Out.Factions.Add(ParseFaction(Item));
    }
    for (const FValue& Item : Snapshot.At("provinces").AsArray())
    {
        Out.Provinces.Add(ParseProvince(Item));
    }
    for (const FValue& Item : Snapshot.At("armies").AsArray())
    {
        Out.Armies.Add(ParseArmy(Item));
    }
    if (const FValue* Proposals = Snapshot.Find("proposals"))
    {
        for (const FValue& Item : Proposals->AsArray())
        {
            Out.Proposals.Add(ParseProposal(Item));
        }
    }
    return Out;
}

FSimResult ParseResult(const FValue& Reply, FWorldSnapshot& OutSnapshot)
{
    FSimResult Out;
    Out.bOk = Reply.At("ok").AsBool();

    if (const FValue* Error = Reply.Find("error"))
    {
        Out.Error = ToFString(Error->AsStr());
    }
    if (const FValue* Rule = Reply.Find("rule"))
    {
        Out.Rule = ToFString(Rule->AsStr());
    }
    if (const FValue* Events = Reply.Find("events"))
    {
        for (const FValue& Event : Events->AsArray())
        {
            Out.Events.Add(ParseEvent(Event));
        }
    }
    if (const FValue* Snapshot = Reply.Find("snapshot"))
    {
        OutSnapshot = ParseSnapshot(*Snapshot);
    }
    return Out;
}

} // namespace tw
