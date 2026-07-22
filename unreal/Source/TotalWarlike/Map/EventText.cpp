#include "EventText.h"

namespace tw
{
namespace
{

FString FactionName(const FWorldSnapshot& Snapshot, int32 Id)
{
    return Snapshot.Factions.IsValidIndex(Id) ? Snapshot.Factions[Id].Name : TEXT("someone");
}

/// The Godot feed named provinces by their settlement, not their region.
FString ProvinceName(const FWorldSnapshot& Snapshot, int32 Id)
{
    return Snapshot.Provinces.IsValidIndex(Id) ? Snapshot.Provinces[Id].City : TEXT("somewhere");
}

/// `ProposalKind.label` from `sim/src/tw_sim/state.py`. The wire sends the enum
/// as an int (see wire.py), where the Godot client received a pre-rendered
/// string, so the wording lives here now.
FString TreatyLabel(int32 Treaty)
{
    switch (Treaty)
    {
    case 0:
        return TEXT("peace");
    case 1:
        return TEXT("trade agreement");
    case 2:
        return TEXT("alliance");
    default:
        return TEXT("treaty");
    }
}

} // namespace

FString FeedLineFor(const FSimEvent& E, const FWorldSnapshot& Snapshot)
{
    const FString& Kind = E.Kind;

    if (Kind == TEXT("turn_began"))
    {
        return FString::Printf(TEXT("— %s, turn %d —"),
                               *FactionName(Snapshot, E.Int(TEXT("faction"))), E.Int(TEXT("turn")));
    }
    if (Kind == TEXT("field_battle"))
    {
        const bool bAttackerWon = E.Int(TEXT("attacker_won"), 0) != 0;
        const int32 Winner = bAttackerWon ? E.Int(TEXT("attacker")) : E.Int(TEXT("defender"));
        const int32 Loser = bAttackerWon ? E.Int(TEXT("defender")) : E.Int(TEXT("attacker"));
        return FString::Printf(TEXT("⚔ Battle at %s — %s beat %s"),
                               *ProvinceName(Snapshot, E.Int(TEXT("province"))),
                               *FactionName(Snapshot, Winner), *FactionName(Snapshot, Loser));
    }
    if (Kind == TEXT("assaulted"))
    {
        if (E.Int(TEXT("attacker_won"), 0) != 0)
        {
            return FString::Printf(TEXT("⚔ %s stormed %s"),
                                   *FactionName(Snapshot, E.Int(TEXT("attacker"))),
                                   *ProvinceName(Snapshot, E.Int(TEXT("province"))));
        }
        return FString::Printf(TEXT("⚔ %s repelled from %s"),
                               *ProvinceName(Snapshot, E.Int(TEXT("province"))),
                               *FactionName(Snapshot, E.Int(TEXT("attacker"))));
    }
    if (Kind == TEXT("siege_started"))
    {
        return FString::Printf(TEXT("⌂ %s laid siege to %s"),
                               *FactionName(Snapshot, E.Int(TEXT("faction"))),
                               *ProvinceName(Snapshot, E.Int(TEXT("province"))));
    }
    if (Kind == TEXT("siege_lifted"))
    {
        return FString::Printf(TEXT("⌂ Siege of %s lifted"),
                               *ProvinceName(Snapshot, E.Int(TEXT("province"))));
    }
    if (Kind == TEXT("city_fell"))
    {
        return FString::Printf(TEXT("▲ %s took %s from %s"),
                               *FactionName(Snapshot, E.Int(TEXT("to"))),
                               *ProvinceName(Snapshot, E.Int(TEXT("province"))),
                               *FactionName(Snapshot, E.Int(TEXT("from"))));
    }
    if (Kind == TEXT("war_declared"))
    {
        return FString::Printf(TEXT("✦ %s declared war on %s"),
                               *FactionName(Snapshot, E.Int(TEXT("by"))),
                               *FactionName(Snapshot, E.Int(TEXT("on"))));
    }
    if (Kind == TEXT("proposal_accepted"))
    {
        return FString::Printf(TEXT("✦ %s and %s agreed to a %s"),
                               *FactionName(Snapshot, E.Int(TEXT("from"))),
                               *FactionName(Snapshot, E.Int(TEXT("to"))),
                               *TreatyLabel(E.Int(TEXT("treaty"))));
    }
    if (Kind == TEXT("proposal_rejected"))
    {
        return FString::Printf(TEXT("✦ %s rejected %s's offer of a %s"),
                               *FactionName(Snapshot, E.Int(TEXT("to"))),
                               *FactionName(Snapshot, E.Int(TEXT("from"))),
                               *TreatyLabel(E.Int(TEXT("treaty"))));
    }
    if (Kind == TEXT("ally_joined_war"))
    {
        return FString::Printf(TEXT("✦ %s joined the war on %s"),
                               *FactionName(Snapshot, E.Int(TEXT("ally"))),
                               *FactionName(Snapshot, E.Int(TEXT("against"))));
    }
    if (Kind == TEXT("army_raised"))
    {
        // The event names the province, not the faction; the Godot client looked
        // the owner up, and so does this. The snapshot has already been updated
        // by the time the feed sees the event, so the owner is the new one.
        const int32 Province = E.Int(TEXT("province"));
        const int32 Owner = Snapshot.Provinces.IsValidIndex(Province)
                                ? Snapshot.Provinces[Province].Owner
                                : TW_NONE;
        return FString::Printf(TEXT("✚ %s mustered an army at %s"), *FactionName(Snapshot, Owner),
                               *ProvinceName(Snapshot, Province));
    }
    if (Kind == TEXT("desertion"))
    {
        // Only a total disbandment is worth a line; men trickling away is noise.
        if (E.Int(TEXT("remaining"), 0) == 0)
        {
            return TEXT("✚ An unpaid army disbanded");
        }
        return FString();
    }
    if (Kind == TEXT("faction_destroyed"))
    {
        return FString::Printf(TEXT("☠ %s was wiped from the map"),
                               *FactionName(Snapshot, E.Int(TEXT("faction"))));
    }
    if (Kind == TEXT("game_won"))
    {
        return FString::Printf(TEXT("★ %s has won the campaign"),
                               *FactionName(Snapshot, E.Int(TEXT("faction"))));
    }

    return FString();
}

FLinearColor FeedColorFor(const FSimEvent& E, TFunctionRef<FLinearColor(int32)> PaletteFor)
{
    // Main.gd's INK_LIGHT: the parchment default for lines with no single owner.
    const FLinearColor InkLight = FLinearColor::FromSRGBColor(FColor(240, 227, 199));
    const FString& Kind = E.Kind;

    if (Kind == TEXT("turn_began"))
    {
        return FMath::Lerp(PaletteFor(E.Int(TEXT("faction"))), FLinearColor::White, 0.3f);
    }
    if (Kind == TEXT("field_battle"))
    {
        return PaletteFor(E.Int(TEXT("attacker_won"), 0) != 0 ? E.Int(TEXT("attacker"))
                                                              : E.Int(TEXT("defender")));
    }
    if (Kind == TEXT("assaulted"))
    {
        return PaletteFor(E.Int(TEXT("attacker")));
    }
    if (Kind == TEXT("siege_started"))
    {
        return PaletteFor(E.Int(TEXT("faction")));
    }
    if (Kind == TEXT("city_fell"))
    {
        return PaletteFor(E.Int(TEXT("to")));
    }
    if (Kind == TEXT("war_declared"))
    {
        return PaletteFor(E.Int(TEXT("by")));
    }
    if (Kind == TEXT("proposal_accepted"))
    {
        return PaletteFor(E.Int(TEXT("from")));
    }
    if (Kind == TEXT("proposal_rejected"))
    {
        return PaletteFor(E.Int(TEXT("to")));
    }
    if (Kind == TEXT("ally_joined_war"))
    {
        return PaletteFor(E.Int(TEXT("ally")));
    }
    if (Kind == TEXT("faction_destroyed"))
    {
        return PaletteFor(E.Int(TEXT("faction")));
    }
    return InkLight;
}

} // namespace tw
