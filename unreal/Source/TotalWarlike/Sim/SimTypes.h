// The wire types, as USTRUCTs.
//
// These mirror `api.WorldSnapshot` field for field — deliberately, so that
// adding a field to the simulation is a mechanical change here rather than a
// design question. They are USTRUCTs (rather than the plain C++ that MsgPack.h
// and Map/ProvinceLookup.h are) for one reason: milestone 2 puts this data in
// front of UMG, and Blueprint cannot see a bare struct.
//
// Nothing here has behaviour. The snapshot is a value the subsystem caches and
// actors read; if you find yourself wanting a method on FArmyState that decides
// something, it belongs in the Python rules instead.

#pragma once

#include "CoreMinimal.h"

#include "SimTypes.generated.h"

/// Sentinel for the `FactionId | None` and `ProvinceId | None` fields the
/// protocol sends as nil. -1 rather than a TOptional so the structs stay
/// Blueprint-friendly.
static constexpr int32 TW_NONE = -1;

/// Melee / archer / cavalry, the three regiment types. Matches `state.Force`.
USTRUCT(BlueprintType)
struct FSimForce
{
    GENERATED_BODY()

    UPROPERTY(BlueprintReadOnly, Category = "Sim") int32 Melee = 0;
    UPROPERTY(BlueprintReadOnly, Category = "Sim") int32 Archer = 0;
    UPROPERTY(BlueprintReadOnly, Category = "Sim") int32 Cav = 0;

    int32 Total() const { return Melee + Archer + Cav; }
};

USTRUCT(BlueprintType)
struct FFactionState
{
    GENERATED_BODY()

    UPROPERTY(BlueprintReadOnly, Category = "Sim") int32 Id = TW_NONE;
    UPROPERTY(BlueprintReadOnly, Category = "Sim") FString Name;
    UPROPERTY(BlueprintReadOnly, Category = "Sim") int32 Treasury = 0;
    UPROPERTY(BlueprintReadOnly, Category = "Sim") int32 Income = 0;
    UPROPERTY(BlueprintReadOnly, Category = "Sim") int32 Upkeep = 0;
    UPROPERTY(BlueprintReadOnly, Category = "Sim") bool bIsPlayer = false;
    UPROPERTY(BlueprintReadOnly, Category = "Sim") bool bIsRebel = false;
    UPROPERTY(BlueprintReadOnly, Category = "Sim") bool bAlive = true;

    /// Diplomatic status toward every faction, indexed by FactionId. The values
    /// are `DiploStatus` — see `sim/src/tw_sim/state.py`; they cross the wire as
    /// ints precisely so this can stay a plain array.
    UPROPERTY(BlueprintReadOnly, Category = "Sim") TArray<int32> Relations;
    UPROPERTY(BlueprintReadOnly, Category = "Sim") TArray<int32> Opinions;
};

USTRUCT(BlueprintType)
struct FProvinceState
{
    GENERATED_BODY()

    UPROPERTY(BlueprintReadOnly, Category = "Sim") int32 Id = TW_NONE;
    UPROPERTY(BlueprintReadOnly, Category = "Sim") FString Name;
    UPROPERTY(BlueprintReadOnly, Category = "Sim") FString City;
    UPROPERTY(BlueprintReadOnly, Category = "Sim") int32 Tier = 0;
    UPROPERTY(BlueprintReadOnly, Category = "Sim") int32 Owner = TW_NONE;
    UPROPERTY(BlueprintReadOnly, Category = "Sim") int32 Population = 0;
    UPROPERTY(BlueprintReadOnly, Category = "Sim") TArray<int32> Adjacent;
    UPROPERTY(BlueprintReadOnly, Category = "Sim") FSimForce Garrison;
    UPROPERTY(BlueprintReadOnly, Category = "Sim") int32 Walls = 0;
    UPROPERTY(BlueprintReadOnly, Category = "Sim") int32 Farm = 0;
    UPROPERTY(BlueprintReadOnly, Category = "Sim") int32 Market = 0;
    UPROPERTY(BlueprintReadOnly, Category = "Sim") int32 Barracks = 0;
    UPROPERTY(BlueprintReadOnly, Category = "Sim") FSimForce RecruitQueue;

    /// `Building` under construction, or TW_NONE when the city is idle.
    UPROPERTY(BlueprintReadOnly, Category = "Sim") int32 Construction = TW_NONE;
    UPROPERTY(BlueprintReadOnly, Category = "Sim") int32 ConstructionTurnsLeft = 0;
    /// Besieging faction, or TW_NONE.
    UPROPERTY(BlueprintReadOnly, Category = "Sim") int32 BesiegedBy = TW_NONE;
};

USTRUCT(BlueprintType)
struct FArmyState
{
    GENERATED_BODY()

    UPROPERTY(BlueprintReadOnly, Category = "Sim") int32 Id = TW_NONE;
    UPROPERTY(BlueprintReadOnly, Category = "Sim") int32 Owner = TW_NONE;
    UPROPERTY(BlueprintReadOnly, Category = "Sim") int32 Location = TW_NONE;
    UPROPERTY(BlueprintReadOnly, Category = "Sim") FSimForce Force;
    UPROPERTY(BlueprintReadOnly, Category = "Sim") int32 Regiments = 0;
    UPROPERTY(BlueprintReadOnly, Category = "Sim") int32 Mp = 0;
    UPROPERTY(BlueprintReadOnly, Category = "Sim") FString General;
    /// `ArmySize` — the band used for the marker's scale.
    UPROPERTY(BlueprintReadOnly, Category = "Sim") int32 Size = 0;
};

USTRUCT(BlueprintType)
struct FProposalState
{
    GENERATED_BODY()

    UPROPERTY(BlueprintReadOnly, Category = "Sim") int32 Id = TW_NONE;
    UPROPERTY(BlueprintReadOnly, Category = "Sim") int32 From = TW_NONE;
    UPROPERTY(BlueprintReadOnly, Category = "Sim") int32 To = TW_NONE;
    UPROPERTY(BlueprintReadOnly, Category = "Sim") int32 Treaty = 0;
};

/// The whole world in one value — a few KB at this scale, which is why there is
/// no diffing anywhere in this bridge.
USTRUCT(BlueprintType)
struct FWorldSnapshot
{
    GENERATED_BODY()

    UPROPERTY(BlueprintReadOnly, Category = "Sim") int32 Turn = 0;
    UPROPERTY(BlueprintReadOnly, Category = "Sim") int32 Current = TW_NONE;
    /// Winning faction, or TW_NONE while the campaign is live.
    UPROPERTY(BlueprintReadOnly, Category = "Sim") int32 Winner = TW_NONE;
    UPROPERTY(BlueprintReadOnly, Category = "Sim") TArray<FFactionState> Factions;
    UPROPERTY(BlueprintReadOnly, Category = "Sim") TArray<FProvinceState> Provinces;
    UPROPERTY(BlueprintReadOnly, Category = "Sim") TArray<FArmyState> Armies;
    UPROPERTY(BlueprintReadOnly, Category = "Sim") TArray<FProposalState> Proposals;

    /// True before the first successful `init` — the state the HUD should treat
    /// as "not connected yet" rather than "an empty world".
    bool IsEmpty() const { return Turn == 0; }

    bool IsPlayerTurn() const
    {
        return Factions.IsValidIndex(Current) && Factions[Current].bIsPlayer;
    }
};

/// One thing that happened, kept in its wire form on purpose.
///
/// The 22 event variants have wildly different payloads and every consumer so
/// far (the debug log in milestone 1, animation triggers later) wants at most
/// two or three fields. A USTRUCT per variant would be 22 structs and a manual
/// downcast at every use site; a tag plus a small field map is enough, and the
/// field names are exactly the ones in `sim/src/tw_sim/event.py`.
USTRUCT(BlueprintType)
struct FSimEvent
{
    GENERATED_BODY()

    /// The `kind` tag: "city_fell", "moved", ...
    UPROPERTY(BlueprintReadOnly, Category = "Sim") FString Kind;
    UPROPERTY(BlueprintReadOnly, Category = "Sim") TMap<FString, int32> Ints;
    UPROPERTY(BlueprintReadOnly, Category = "Sim") TMap<FString, FString> Strings;

    int32 Int(const FString& Field, int32 Fallback = TW_NONE) const
    {
        const int32* Found = Ints.Find(Field);
        return Found ? *Found : Fallback;
    }

    FString Str(const FString& Field) const
    {
        const FString* Found = Strings.Find(Field);
        return Found ? *Found : FString();
    }
};

/// A command on its way to the simulation.
///
/// Same reasoning as FSimEvent, from the other direction: `Move`, `Build` and
/// `Respond` share no shape, and the wire form is a tag plus flat ints. The
/// static factories below are the intended way to build one — they are the
/// place where a typo in a field name gets caught once instead of per call site.
USTRUCT(BlueprintType)
struct FSimCommand
{
    GENERATED_BODY()

    UPROPERTY(BlueprintReadOnly, Category = "Sim") FString Kind;
    UPROPERTY(BlueprintReadOnly, Category = "Sim") TMap<FString, int32> Ints;
    /// Only `respond.accept` lives here, but it must stay a real bool: the
    /// Python side rebuilds a frozen dataclass from these fields verbatim, and a
    /// 1 where a bool belongs would travel undetected into the rules.
    UPROPERTY(BlueprintReadOnly, Category = "Sim") TMap<FString, bool> Bools;

    FSimCommand& With(const FString& Field, int32 Value)
    {
        Ints.Add(Field, Value);
        return *this;
    }

    FSimCommand& WithBool(const FString& Field, bool Value)
    {
        Bools.Add(Field, Value);
        return *this;
    }

    static FSimCommand Of(const FString& InKind)
    {
        FSimCommand Out;
        Out.Kind = InKind;
        return Out;
    }

    static FSimCommand Move(int32 Army, int32 To)
    {
        return Of(TEXT("move")).With(TEXT("army"), Army).With(TEXT("to"), To);
    }

    static FSimCommand Assault(int32 Army)
    {
        return Of(TEXT("assault")).With(TEXT("army"), Army);
    }

    static FSimCommand Besiege(int32 Army)
    {
        return Of(TEXT("besiege")).With(TEXT("army"), Army);
    }

    static FSimCommand Garrison(int32 Army)
    {
        return Of(TEXT("garrison")).With(TEXT("army"), Army);
    }

    static FSimCommand Merge(int32 From, int32 Into)
    {
        return Of(TEXT("merge")).With(TEXT("from"), From).With(TEXT("into"), Into);
    }

    /// `Unit` is a `UnitType`, `Building` a `Building` — both cross the wire as
    /// their integer value.
    static FSimCommand Recruit(int32 Province, int32 Unit)
    {
        return Of(TEXT("recruit")).With(TEXT("province"), Province).With(TEXT("unit"), Unit);
    }

    static FSimCommand Build(int32 Province, int32 Building)
    {
        return Of(TEXT("build")).With(TEXT("province"), Province).With(TEXT("building"), Building);
    }

    static FSimCommand RaiseArmy(int32 Province, int32 Regiments)
    {
        return Of(TEXT("raise_army")).With(TEXT("province"), Province).With(TEXT("regiments"), Regiments);
    }

    static FSimCommand DeclareWar(int32 On)
    {
        return Of(TEXT("declare_war")).With(TEXT("on"), On);
    }

    static FSimCommand Propose(int32 To, int32 Treaty)
    {
        return Of(TEXT("propose")).With(TEXT("to"), To).With(TEXT("treaty"), Treaty);
    }

    static FSimCommand Respond(int32 Proposal, bool bAccept)
    {
        return Of(TEXT("respond")).With(TEXT("proposal"), Proposal).WithBool(TEXT("accept"), bAccept);
    }
};

/// What a request came back with. `bOk == false` carries the rule violation the
/// simulation refused on — an ordinary part of play (clicking a non-adjacent
/// province), not a transport failure.
USTRUCT(BlueprintType)
struct FSimResult
{
    GENERATED_BODY()

    UPROPERTY(BlueprintReadOnly, Category = "Sim") bool bOk = false;
    UPROPERTY(BlueprintReadOnly, Category = "Sim") FString Error;
    /// The `RuleError` variant name, e.g. "NotAdjacent". Empty on success and on
    /// transport failures.
    UPROPERTY(BlueprintReadOnly, Category = "Sim") FString Rule;
    UPROPERTY(BlueprintReadOnly, Category = "Sim") TArray<FSimEvent> Events;
};
