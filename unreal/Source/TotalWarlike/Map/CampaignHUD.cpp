#include "CampaignHUD.h"

#include "CanvasItem.h"
#include "CanvasTypes.h"
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

    // Status: turn, whose turn, and whether we are waiting on Python. The top-left
    // corner now belongs to the resource bar, so the status line floats along the
    // top edge just right of it.
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
            Status = FString::Printf(TEXT("turn %d - %s%s"), Snapshot.Turn, *Who,
                                     Snapshot.IsPlayerTurn() ? TEXT("  [Space] end turn")
                                                             : TEXT(""));
        }
        float TextW = 0.0f;
        float TextH = 0.0f;
        Canvas->TextSize(Font, Status, TextW, TextH);
        Canvas->SetDrawColor(FColor(240, 227, 199));
        Canvas->DrawText(Font, Status, (Canvas->SizeX - TextW) * 0.5f, Margin);
    }

    // The control bar owns the bottom edge; the feed floats just above it.
    constexpr float BarHeight = 150.0f;

    // The feed, newest at the bottom, stacked just above the control bar.
    float Y = Canvas->SizeY - BarHeight - Margin - LineHeight * Feed.Num();
    for (const FFeedLine& Line : Feed)
    {
        Canvas->SetDrawColor(Line.Color.ToFColor(/*bSRGB=*/true));
        Canvas->DrawText(Font, Line.Text, Margin, Y);
        Y += LineHeight;
    }

    DrawSettlementLabels();
    DrawControlBar();
    DrawTopResourceBar();
    DrawCommandRing();
    DrawSettlementPanel();
}

namespace
{

// The bar's palette, meant to read like the carved-stone-and-gilt frame of the
// reference screenshot: a warm stone field, dark inset panels, a gold rule.
const FLinearColor StoneField(0.40f, 0.37f, 0.31f);
const FLinearColor StoneLight(0.52f, 0.48f, 0.41f);
const FLinearColor PanelDark(0.16f, 0.14f, 0.12f);
const FLinearColor PanelInset(0.23f, 0.21f, 0.18f);
const FLinearColor GoldLine(0.72f, 0.58f, 0.28f);
const FColor GoldText(232, 198, 120);
const FColor StoneText(228, 216, 190);
const FColor DimText(150, 140, 122);

// The reference's settlement inspector reads cooler than the carved bar: a slate
// navy body under a brighter blue caption, with values called out in near-white
// rather than gold. These sit alongside the stone palette, used only there.
const FLinearColor SlateField(0.11f, 0.13f, 0.19f, 0.94f);
const FLinearColor HeaderBlue(0.19f, 0.31f, 0.50f);
const FColor BrightText(242, 240, 233);

/// A filled rectangle.
void Fill(UCanvas* Canvas, float X, float Y, float W, float H, const FLinearColor& Color)
{
    FCanvasTileItem Tile(FVector2D(X, Y), FVector2D(W, H), Color);
    Tile.BlendMode = SE_BLEND_Translucent;
    Canvas->DrawItem(Tile);
}

/// A one-pixel outline.
void Outline(UCanvas* Canvas, float X, float Y, float W, float H, const FLinearColor& Color)
{
    FCanvasBoxItem Box(FVector2D(X, Y), FVector2D(W, H));
    Box.SetColor(Color);
    Canvas->DrawItem(Box);
}

/// An inset panel: dark fill, gold outline.
void Panel(UCanvas* Canvas, float X, float Y, float W, float H, const FLinearColor& Fill_)
{
    Fill(Canvas, X, Y, W, H, Fill_);
    Outline(Canvas, X, Y, W, H, GoldLine);
}

void Label(UCanvas* Canvas, UFont* Font, const FString& Text, float X, float Y,
           const FColor& Color, float Scale = 1.0f)
{
    Canvas->SetDrawColor(Color);
    Canvas->DrawText(Font, Text, X, Y, Scale, Scale);
}

/// A filled disc, centred at (CX, CY). Canvas ships no circle primitive; an
/// n-gon with enough sides reads as round at button scale.
void Disc(UCanvas* Canvas, float CX, float CY, float Radius, const FLinearColor& Color)
{
    FCanvasNGonItem Ngon(FVector2D(CX, CY), FVector2D(Radius, Radius), 24, Color);
    Canvas->DrawItem(Ngon);
}

/// A round button: stone disc, gold ring, and a small emblem mark at its centre
/// so the ring of buttons reads as distinct faces without any authored icon art.
void RingButton(UCanvas* Canvas, float CX, float CY, float Radius, int32 Emblem)
{
    Disc(Canvas, CX, CY, Radius, PanelDark);
    Disc(Canvas, CX, CY, Radius - 2.0f, StoneField);
    Disc(Canvas, CX, CY, Radius - 4.0f, PanelInset);
    // A tiny gold glyph, varied per button, standing in for the faction/menu icon.
    const float M = Radius * 0.42f;
    switch (Emblem % 4)
    {
    case 0: // diamond
        Fill(Canvas, CX - M * 0.5f, CY - M * 0.5f, M, M, GoldLine);
        break;
    case 1: // ring
        Disc(Canvas, CX, CY, M, GoldLine);
        Disc(Canvas, CX, CY, M - 2.0f, PanelInset);
        break;
    case 2: // bar
        Fill(Canvas, CX - M, CY - 2.0f, M * 2.0f, 4.0f, GoldLine);
        break;
    default: // dot
        Disc(Canvas, CX, CY, M * 0.6f, GoldLine);
        break;
    }
}

/// A small drawn stat icon — a coloured swatch with a lighter inner mark, keyed
/// by kind. Stands in for the reference screenshot's row icons without needing an
/// asset or any non-ASCII glyph.
void StatIcon(UCanvas* Canvas, float X, float Y, float Size, const FLinearColor& Tint)
{
    Fill(Canvas, X, Y, Size, Size, Tint);
    Outline(Canvas, X, Y, Size, Size, PanelDark);
    Fill(Canvas, X + Size * 0.3f, Y + Size * 0.3f, Size * 0.4f, Size * 0.4f,
         FLinearColor(1.0f, 1.0f, 1.0f, 0.35f));
}

/// Human-readable population/resource count: 361, 18.3K, 204.0K, 1.2M.
FString FormatK(int32 Value)
{
    const float V = static_cast<float>(Value);
    if (Value >= 1'000'000)
    {
        return FString::Printf(TEXT("%.1fM"), V / 1'000'000.0f);
    }
    if (Value >= 10'000)
    {
        return FString::Printf(TEXT("%.1fK"), V / 1'000.0f);
    }
    return FString::FromInt(Value);
}

} // namespace

void ACampaignHUD::DrawControlBar()
{
    if (Canvas == nullptr || GEngine == nullptr || GEngine->GetMediumFont() == nullptr)
    {
        return;
    }

    // The bar is redrawn every frame; its clickable regions are too. This is the
    // one reset for the whole bottom bar — the tab strip and end-turn button both
    // append their hits into the same list.
    ControlHits.Reset();

    const float W = Canvas->SizeX;
    const float H = Canvas->SizeY;
    constexpr float BarH = 150.0f;
    const float BarY = H - BarH;

    // The stone field, its lighter cap, and the gold rule that separates the bar
    // from the map above it.
    Fill(Canvas, 0.0f, BarY, W, BarH, StoneField);
    Fill(Canvas, 0.0f, BarY, W, 6.0f, StoneLight);
    Fill(Canvas, 0.0f, BarY, W, 2.0f, GoldLine);

    DrawTabbedPanel();
    DrawEndTurnButton();
}

void ACampaignHUD::DrawTabbedPanel()
{
    UFont* Font = GEngine != nullptr ? GEngine->GetMediumFont() : nullptr;
    const USimSubsystem* Subsystem = Sim();
    const ACampaignMap* Map = FindMap();
    if (Canvas == nullptr || Font == nullptr || Subsystem == nullptr || Map == nullptr)
    {
        return;
    }
    const FWorldSnapshot& Snapshot = Subsystem->GetSnapshot();

    const float W = Canvas->SizeX;
    const float H = Canvas->SizeY;
    constexpr float BarH = 150.0f;
    const float BarY = H - BarH;
    constexpr float Pad = 12.0f;

    // The panel occupies the centre of the bar, leaving the end-turn button its
    // own room on the right.
    const float PanelX = Pad;
    const float PanelW = W - Pad - 200.0f - PanelX;
    const float StripY = BarY + 34.0f;
    const float StripH = BarH - 34.0f - Pad;

    const bool bReady = Snapshot.IsPlayerTurn() && !Subsystem->IsBusy() &&
                        Snapshot.Winner == TW_NONE && !Map->IsAnimating();

    const int32 SelProv = Map->GetSelectedProvince();
    const FProvinceState* Prov =
        (SelProv != TW_NONE && Snapshot.Provinces.IsValidIndex(SelProv))
            ? &Snapshot.Provinces[SelProv]
            : nullptr;
    const bool bHasCity = Prov != nullptr && !Prov->City.IsEmpty();

    // ------------------------------------------------------------------- tabs
    // A row of three tabs riding the top edge of the strip; the active one is
    // lifted into gold and joined to the panel below it.
    const TCHAR* TabNames[3] = {TEXT("Buildings"), TEXT("Characters"), TEXT("Military")};
    const EControlAction TabActs[3] = {EControlAction::TabBuildings,
                                       EControlAction::TabCharacters,
                                       EControlAction::TabMilitary};
    constexpr float TabW = 96.0f;
    constexpr float TabH = 24.0f;
    const float TabY = BarY + 8.0f;
    for (int32 i = 0; i < 3; ++i)
    {
        const float TX = PanelX + i * (TabW + 4.0f);
        const bool bActive = ActiveTab == i;
        Fill(Canvas, TX, TabY, TabW, TabH, bActive ? PanelInset : PanelDark);
        Fill(Canvas, TX, TabY, TabW, 2.0f, bActive ? GoldLine : StoneLight);
        Outline(Canvas, TX, TabY, TabW, TabH, bActive ? GoldLine : PanelDark);
        Label(Canvas, Font, TabNames[i], TX + 10.0f, TabY + 5.0f,
              bActive ? GoldText : StoneText);
        ControlHits.Add({FBox2D(FVector2D(TX, TabY), FVector2D(TX + TabW, TabY + TabH)),
                         TabActs[i]});
    }

    // The strip body the tabs sit on.
    Panel(Canvas, PanelX, StripY, PanelW, StripH, PanelDark);

    if (!bHasCity && ActiveTab != 2)
    {
        Label(Canvas, Font, TEXT("no settlement selected"), PanelX + 12.0f,
              StripY + StripH * 0.5f - 6.0f, DimText);
        return;
    }

    switch (ActiveTab)
    {
    case 0: // Buildings — a strip of thumbnail cards, one per building slot.
    {
        const TCHAR* BNames[4] = {TEXT("Farmland"), TEXT("Market"), TEXT("Barracks"),
                                  TEXT("Walls")};
        const int32 Levels[4] = {Prov->Farm, Prov->Market, Prov->Barracks, Prov->Walls};
        const FLinearColor Tints[4] = {FLinearColor(0.45f, 0.55f, 0.30f),
                                       FLinearColor(0.55f, 0.45f, 0.25f),
                                       FLinearColor(0.50f, 0.30f, 0.28f),
                                       FLinearColor(0.42f, 0.42f, 0.46f)};
        constexpr float CardW = 96.0f;
        constexpr float CardGap = 6.0f;
        const float CardY = StripY + 8.0f;
        const float CardH = StripH - 16.0f;
        for (int32 i = 0; i < 4; ++i)
        {
            const float CX = PanelX + 8.0f + i * (CardW + CardGap);
            Panel(Canvas, CX, CardY, CardW, CardH, PanelInset);
            // The "thumbnail": a tinted plate standing in for the building art.
            const float ThumbH = CardH - 22.0f;
            Fill(Canvas, CX + 3.0f, CardY + 3.0f, CardW - 6.0f, ThumbH, Tints[i]);
            Outline(Canvas, CX + 3.0f, CardY + 3.0f, CardW - 6.0f, ThumbH, PanelDark);
            // Level chip, top-left over the art.
            Fill(Canvas, CX + 3.0f, CardY + 3.0f, 42.0f, 16.0f,
                 FLinearColor(0.10f, 0.09f, 0.08f, 0.85f));
            Label(Canvas, Font, FString::Printf(TEXT("Lv.%d"), Levels[i]), CX + 6.0f,
                  CardY + 4.0f, GoldText);
            Label(Canvas, Font, BNames[i], CX + 6.0f, CardY + CardH - 16.0f, StoneText);
        }
        // The whole strip is the "construct" target: clicking it upgrades the
        // town, exactly as the old Construct button did. Greyed off-turn.
        if (bReady)
        {
            ControlHits.Add({FBox2D(FVector2D(PanelX, StripY),
                                    FVector2D(PanelX + PanelW, StripY + StripH)),
                             EControlAction::Construct});
            Label(Canvas, Font, TEXT("click to construct"), PanelX + PanelW - 150.0f,
                  StripY + StripH - 16.0f, DimText);
        }
        break;
    }
    case 1: // Characters — the governor / general standing in the settlement.
    {
        Panel(Canvas, PanelX + 8.0f, StripY + 8.0f, 64.0f, StripH - 16.0f, PanelInset);
        Fill(Canvas, PanelX + 14.0f, StripY + 14.0f, 52.0f, 40.0f,
             FLinearColor(0.35f, 0.30f, 0.42f));
        Label(Canvas, Font, TEXT("Gov."), PanelX + 20.0f, StripY + StripH - 24.0f, StoneText);
        const int32 SelArmy = Map->GetSelectedArmy();
        const FArmyState* Army =
            SelArmy != TW_NONE ? Snapshot.Armies.FindByPredicate(
                                     [SelArmy](const FArmyState& A) { return A.Id == SelArmy; })
                               : nullptr;
        const FString Name = Army != nullptr && !Army->General.IsEmpty()
                                 ? Army->General
                                 : FString::Printf(TEXT("Governor of %s"), *Prov->City);
        Label(Canvas, Font, Name, PanelX + 84.0f, StripY + 14.0f, GoldText);
        Label(Canvas, Font, TEXT("Loyalty  steady"), PanelX + 84.0f, StripY + 36.0f, StoneText);
        Label(Canvas, Font, TEXT("Traits   -"), PanelX + 84.0f, StripY + 56.0f, DimText);
        break;
    }
    default: // Military — the garrison / selected army and a recruit action.
    {
        const int32 SelArmy = Map->GetSelectedArmy();
        const FArmyState* Army =
            SelArmy != TW_NONE ? Snapshot.Armies.FindByPredicate(
                                     [SelArmy](const FArmyState& A) { return A.Id == SelArmy; })
                               : nullptr;
        if (Army != nullptr)
        {
            const TCHAR* Names[3] = {TEXT("Melee"), TEXT("Archers"), TEXT("Cavalry")};
            const int32 Counts[3] = {Army->Force.Melee, Army->Force.Archer, Army->Force.Cav};
            constexpr float CardW = 96.0f;
            const float CardY = StripY + 8.0f;
            const float CardH = StripH - 16.0f;
            for (int32 i = 0; i < 3; ++i)
            {
                const float CX = PanelX + 8.0f + i * (CardW + 6.0f);
                Panel(Canvas, CX, CardY, CardW, CardH, PanelInset);
                Label(Canvas, Font, Names[i], CX + 8.0f, CardY + 8.0f, StoneText);
                Label(Canvas, Font, FString::FromInt(Counts[i]), CX + 8.0f, CardY + CardH - 30.0f,
                      GoldText, 1.6f);
            }
        }
        else
        {
            Label(Canvas, Font, TEXT("no army selected"), PanelX + 12.0f,
                  StripY + StripH * 0.5f - 6.0f, DimText);
        }
        if (bReady && bHasCity)
        {
            const float BW = 120.0f;
            const float BX = PanelX + PanelW - BW - 8.0f;
            const float BYc = StripY + StripH * 0.5f - 18.0f;
            Panel(Canvas, BX, BYc, BW, 36.0f, PanelInset);
            Fill(Canvas, BX, BYc, BW, 3.0f, StoneLight);
            Label(Canvas, Font, TEXT("Recruit"), BX + 12.0f, BYc + 10.0f, GoldText);
            ControlHits.Add(
                {FBox2D(FVector2D(BX, BYc), FVector2D(BX + BW, BYc + 36.0f)),
                 EControlAction::Recruit});
        }
        break;
    }
    }
}

void ACampaignHUD::DrawEndTurnButton()
{
    UFont* Font = GEngine != nullptr ? GEngine->GetMediumFont() : nullptr;
    const USimSubsystem* Subsystem = Sim();
    const ACampaignMap* Map = FindMap();
    if (Canvas == nullptr || Font == nullptr || Subsystem == nullptr || Map == nullptr)
    {
        return;
    }
    const FWorldSnapshot& Snapshot = Subsystem->GetSnapshot();

    const float W = Canvas->SizeX;
    const float H = Canvas->SizeY;
    constexpr float BarH = 150.0f;
    const float BarY = H - BarH;
    constexpr float Pad = 12.0f;

    const bool bReady = Snapshot.IsPlayerTurn() && !Subsystem->IsBusy() &&
                        Snapshot.Winner == TW_NONE && !Map->IsAnimating();

    // An ornate framed button: a gilt double frame around a warm red field, the
    // legend in gold, the turn number folded in. Bottom-right, the largest target.
    const float BW = 176.0f;
    const float BH = 56.0f;
    const float BX = W - Pad - BW;
    const float BYb = BarY + (BarH - BH) * 0.5f;

    const FLinearColor RedField = bReady ? FLinearColor(0.42f, 0.12f, 0.10f)
                                         : FLinearColor(0.24f, 0.20f, 0.18f);

    // Outer gilt frame, inner bevel, then the field.
    Fill(Canvas, BX - 4.0f, BYb - 4.0f, BW + 8.0f, BH + 8.0f, GoldLine);
    Fill(Canvas, BX - 2.0f, BYb - 2.0f, BW + 4.0f, BH + 4.0f, PanelDark);
    Fill(Canvas, BX, BYb, BW, BH, RedField);
    Fill(Canvas, BX, BYb, BW, 3.0f, FLinearColor(0.85f, 0.35f, 0.28f));
    Outline(Canvas, BX + 2.0f, BYb + 2.0f, BW - 4.0f, BH - 4.0f, GoldLine);

    Label(Canvas, Font, TEXT("END TURN"), BX + 20.0f, BYb + 10.0f,
          bReady ? GoldText : DimText, 1.3f);
    Label(Canvas, Font, FString::Printf(TEXT("Turn %d"), Snapshot.Turn), BX + 20.0f,
          BYb + BH - 18.0f, StoneText);

    if (bReady)
    {
        ControlHits.Add(
            {FBox2D(FVector2D(BX, BYb), FVector2D(BX + BW, BYb + BH)), EControlAction::EndTurn});
    }
}

void ACampaignHUD::DrawTopResourceBar()
{
    UFont* Font = GEngine != nullptr ? GEngine->GetMediumFont() : nullptr;
    const USimSubsystem* Subsystem = Sim();
    if (Canvas == nullptr || Font == nullptr || Subsystem == nullptr)
    {
        return;
    }
    const FWorldSnapshot& Snapshot = Subsystem->GetSnapshot();
    const FFactionState* Player =
        Snapshot.Factions.FindByPredicate([](const FFactionState& F) { return F.bIsPlayer; });
    if (Player == nullptr)
    {
        return;
    }

    const int32 Treasury = Player->Treasury;
    const int32 Income = Player->Income - Player->Upkeep;
    // A second resource stands in for the reference's faith/influence readout,
    // derived from the realm so it moves as the campaign does.
    const int32 Standing = Snapshot.Turn + Treasury / 100;
    const int32 StandingDelta = FMath::Max(0, Income / 20);

    constexpr float X = 12.0f;
    constexpr float Y = 8.0f;
    constexpr float H = 26.0f;

    // Two inset readouts, side by side, each a swatch + value + delta.
    auto Readout = [&](float RX, const FLinearColor& Tint, int32 Value, int32 Delta) -> float {
        const FString ValueText = FormatK(Value);
        const FString DeltaText = FString::Printf(TEXT("(%+d)"), Delta);
        float VW = 0.0f, VH = 0.0f, DW = 0.0f, DH = 0.0f;
        Canvas->TextSize(Font, ValueText, VW, VH);
        Canvas->TextSize(Font, DeltaText, DW, DH);
        const float BoxW = 22.0f + VW + 8.0f + DW + 14.0f;
        Panel(Canvas, RX, Y, BoxW, H, PanelDark);
        StatIcon(Canvas, RX + 6.0f, Y + 6.0f, 14.0f, Tint);
        Label(Canvas, Font, ValueText, RX + 26.0f, Y + 5.0f, GoldText);
        Label(Canvas, Font, DeltaText, RX + 26.0f + VW + 8.0f, Y + 5.0f,
              Delta >= 0 ? FColor(150, 210, 140) : FColor(215, 120, 110));
        return RX + BoxW + 8.0f;
    };

    float Cursor = X;
    Cursor = Readout(Cursor, FLinearColor(0.80f, 0.66f, 0.28f), Treasury, Income);
    Readout(Cursor, FLinearColor(0.40f, 0.60f, 0.78f), Standing, StandingDelta);
}

void ACampaignHUD::DrawCommandRing()
{
    UFont* Font = GEngine != nullptr ? GEngine->GetMediumFont() : nullptr;
    if (Canvas == nullptr || Font == nullptr)
    {
        return;
    }

    // A row of round faction/menu buttons pinned to the top-right corner. They are
    // drawn but issue no command — the diplomacy, technology and mission screens
    // behind them do not exist yet, so they are deliberately not hit-registered.
    constexpr int32 Count = 7;
    constexpr float Radius = 17.0f;
    constexpr float Gap = 6.0f;
    const float Y = 8.0f + Radius;
    float CX = Canvas->SizeX - 12.0f - Radius;
    for (int32 i = 0; i < Count; ++i)
    {
        RingButton(Canvas, CX, Y, Radius, i);
        CX -= (Radius * 2.0f + Gap);
    }
}

void ACampaignHUD::DrawSettlementPanel()
{
    UFont* Font = GEngine != nullptr ? GEngine->GetMediumFont() : nullptr;
    const USimSubsystem* Subsystem = Sim();
    const ACampaignMap* Map = FindMap();
    if (Canvas == nullptr || Font == nullptr || Subsystem == nullptr || Map == nullptr)
    {
        return;
    }
    const FWorldSnapshot& Snapshot = Subsystem->GetSnapshot();
    const FMapData& MapData = Map->GetMapData();

    const int32 SelProv = Map->GetSelectedProvince();
    const FProvinceState* Prov =
        (SelProv != TW_NONE && Snapshot.Provinces.IsValidIndex(SelProv))
            ? &Snapshot.Provinces[SelProv]
            : nullptr;
    if (Prov == nullptr || Prov->City.IsEmpty())
    {
        return; // The panel is a settlement inspector; nothing selected, nothing shown.
    }

    // The panel floats on the left, above the bottom bar.
    constexpr float PanelW = 218.0f;
    const float X = 12.0f;
    constexpr float BarH = 150.0f;

    // Rows: derive a plausible population breakdown from the single Population
    // figure, and Food / Region wealth / Income from the buildings and treasury.
    const int32 Peasants = Prov->Population;
    const int32 Townsfolk = Prov->Population / 11;
    const int32 Nobles = FMath::Max(1, Prov->Population / 565);
    const int32 Food = Prov->Farm * 5'000 + 2'000;
    const int32 Wealth = Prov->Market * 4'000 + Prov->Population / 20;
    const FFactionState* Owner =
        Snapshot.Factions.IsValidIndex(Prov->Owner) ? &Snapshot.Factions[Prov->Owner] : nullptr;
    const int32 Income = Owner != nullptr ? (Owner->Income - Owner->Upkeep) : 0;

    struct FRow
    {
        const TCHAR* Name;
        FString Value;
        FLinearColor Tint;
    };
    const FRow Rows[6] = {
        {TEXT("Nobles"), FormatK(Nobles), FLinearColor(0.72f, 0.60f, 0.28f)},
        {TEXT("Townsfolk"), FormatK(Townsfolk), FLinearColor(0.60f, 0.45f, 0.30f)},
        {TEXT("Peasants"), FormatK(Peasants), FLinearColor(0.45f, 0.55f, 0.30f)},
        {TEXT("Food"), FormatK(Food), FLinearColor(0.50f, 0.62f, 0.32f)},
        {TEXT("Region wealth"), FormatK(Wealth), FLinearColor(0.55f, 0.45f, 0.25f)},
        {TEXT("Income"), FString::Printf(TEXT("%+d"), Income), FLinearColor(0.40f, 0.60f, 0.78f)},
    };

    constexpr float HeaderH = 24.0f;
    constexpr float GovH = 44.0f;
    constexpr float RowH = 22.0f;
    const float PanelH = HeaderH + GovH + 6.0f * RowH + 16.0f;
    const float Y = Canvas->SizeY - BarH - 12.0f - PanelH;

    // Frame.
    Panel(Canvas, X, Y, PanelW, PanelH, SlateField);

    // Header: city name in bright white, over a blue caption bar with a lighter
    // top rule; the faction colour flashes on the right.
    Fill(Canvas, X, Y, PanelW, HeaderH, HeaderBlue);
    Fill(Canvas, X, Y, PanelW, 2.0f, FLinearColor(0.34f, 0.48f, 0.68f));
    Fill(Canvas, X + PanelW - 22.0f, Y, 22.0f, HeaderH, MapData.ColorFor(Prov->Owner));
    Outline(Canvas, X, Y, PanelW, HeaderH, GoldLine);
    {
        float TW = 0.0f, TH = 0.0f;
        Canvas->TextSize(Font, Prov->City, TW, TH);
        Label(Canvas, Font, Prov->City, X + (PanelW - 22.0f - TW) * 0.5f, Y + 5.0f, BrightText);
    }

    // Governor portrait box + perk points.
    const float GovY = Y + HeaderH + 4.0f;
    Panel(Canvas, X + 6.0f, GovY, 40.0f, GovH - 6.0f, PanelInset);
    Fill(Canvas, X + 11.0f, GovY + 5.0f, 30.0f, GovH - 20.0f, FLinearColor(0.35f, 0.30f, 0.42f));
    Label(Canvas, Font, TEXT("Gov"), X + 13.0f, GovY + 5.0f, StoneText);
    Label(Canvas, Font, TEXT("Perk pts"), X + 54.0f, GovY + 10.0f, StoneText);
    Label(Canvas, Font, TEXT("0"), X + PanelW - 24.0f, GovY + 10.0f, GoldText);

    // Stat rows.
    float RowY = GovY + GovH + 2.0f;
    for (const FRow& Row : Rows)
    {
        StatIcon(Canvas, X + 8.0f, RowY + 3.0f, 14.0f, Row.Tint);
        Label(Canvas, Font, Row.Name, X + 28.0f, RowY + 3.0f, StoneText);
        float VW = 0.0f, VH = 0.0f;
        Canvas->TextSize(Font, Row.Value, VW, VH);
        Label(Canvas, Font, Row.Value, X + PanelW - 12.0f - VW, RowY + 3.0f, BrightText);
        RowY += RowH;
    }
}

void ACampaignHUD::DrawSettlementLabels()
{
    UFont* Font = GEngine != nullptr ? GEngine->GetMediumFont() : nullptr;
    if (Canvas == nullptr || Font == nullptr)
    {
        return;
    }

    const USimSubsystem* Subsystem = Sim();
    ACampaignMap* Map = FindMap();
    if (Subsystem == nullptr || Map == nullptr)
    {
        return;
    }
    const FWorldSnapshot& Snapshot = Subsystem->GetSnapshot();
    const FMapData& MapData = Map->GetMapData();
    const int32 Selected = Map->GetSelectedProvince();

    // Labels sit above the map; the control bar draws over them, so keep them out
    // of its band entirely.
    constexpr float BarHeight = 150.0f;
    const float BottomLimit = Canvas->SizeY - BarHeight;

    // How far above the keep the tag floats, in world centimetres.
    constexpr float LabelLift = 1500.0f;

    for (const FProvinceState& Province : Snapshot.Provinces)
    {
        const FString& Name = Province.City.IsEmpty() ? Province.Name : Province.City;
        if (Name.IsEmpty())
        {
            continue;
        }

        FVector World = Map->MarkerLocationFor(Province.Id);
        World.Z += LabelLift;
        const FVector Screen = Project(World);
        if (Screen.Z <= 0.0f)
        {
            continue; // Behind the camera.
        }

        float TextW = 0.0f;
        float TextH = 0.0f;
        Canvas->TextSize(Font, Name, TextW, TextH);

        constexpr float PadX = 8.0f;
        constexpr float StripeW = 5.0f;
        const float PlateW = TextW + PadX * 2.0f + StripeW;
        const float PlateH = TextH + 6.0f;
        const float PlateX = Screen.X - PlateW * 0.5f;
        const float PlateY = Screen.Y - PlateH;

        if (PlateX < 0.0f || PlateX + PlateW > Canvas->SizeX || PlateY < 0.0f ||
            PlateY + PlateH > BottomLimit)
        {
            continue; // Fully off-screen or under the control bar.
        }

        const bool bSelected = Province.Id == Selected;
        FLinearColor Faction = MapData.ColorFor(Province.Owner);

        // The selected settlement lifts into a solid gold plate with dark text,
        // the way the reference calls out the active town; the rest stay as dark
        // plates with a faction stripe and stone text.
        Fill(Canvas, PlateX, PlateY, PlateW, PlateH,
             bSelected ? FLinearColor(0.86f, 0.68f, 0.22f, 0.95f)
                       : FLinearColor(0.10f, 0.09f, 0.08f, 0.85f));
        Fill(Canvas, PlateX, PlateY, StripeW, PlateH, Faction);
        Outline(Canvas, PlateX, PlateY, PlateW, PlateH, bSelected ? GoldLine : PanelDark);

        Label(Canvas, Font, Name, PlateX + StripeW + PadX, PlateY + 3.0f,
              bSelected ? FColor(28, 22, 12) : StoneText);
    }
}

ACampaignHUD::EControlAction ACampaignHUD::ControlActionAt(const FVector2D& Screen) const
{
    for (const FControlHit& Hit : ControlHits)
    {
        if (Hit.Rect.bIsValid && Hit.Rect.IsInside(Screen))
        {
            return Hit.Action;
        }
    }
    return EControlAction::None;
}
