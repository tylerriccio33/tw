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

    // Status: turn, whose turn, and whether we are waiting on Python.
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
            Status = FString::Printf(TEXT("turn %d — %s%s"), Snapshot.Turn, *Who,
                                     Snapshot.IsPlayerTurn() ? TEXT("  [Space] end turn")
                                                             : TEXT(""));
        }
        Canvas->SetDrawColor(FColor(240, 227, 199));
        Canvas->DrawText(Font, Status, Margin, Margin);
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

} // namespace

void ACampaignHUD::DrawControlBar()
{
    UFont* Font = GEngine != nullptr ? GEngine->GetMediumFont() : nullptr;
    if (Canvas == nullptr || Font == nullptr)
    {
        return;
    }

    const USimSubsystem* Subsystem = Sim();
    const ACampaignMap* Map = FindMap();
    if (Subsystem == nullptr || Map == nullptr)
    {
        return;
    }
    const FWorldSnapshot& Snapshot = Subsystem->GetSnapshot();

    // The bar is redrawn every frame; its clickable regions are too.
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

    constexpr float Pad = 12.0f;
    const float ContentY = BarY + Pad;
    const float ContentH = BarH - Pad * 2.0f;

    // ------------------------------------------------------------------ army
    // Left panel: the regiments in the selected army, one card per unit type.
    const float ArmyX = Pad;
    const float ArmyW = 300.0f;
    Panel(Canvas, ArmyX, ContentY, ArmyW, ContentH, PanelDark);
    Label(Canvas, Font, TEXT("ARMY"), ArmyX + 8.0f, ContentY + 6.0f, GoldText);

    const int32 SelArmy = Map->GetSelectedArmy();
    const FArmyState* Army =
        SelArmy != TW_NONE
            ? Snapshot.Armies.FindByPredicate([SelArmy](const FArmyState& A) { return A.Id == SelArmy; })
            : nullptr;
    if (Army != nullptr)
    {
        const TCHAR* Names[3] = {TEXT("Melee"), TEXT("Archers"), TEXT("Cavalry")};
        const int32 Counts[3] = {Army->Force.Melee, Army->Force.Archer, Army->Force.Cav};
        constexpr float CardW = 88.0f;
        constexpr float CardGap = 6.0f;
        const float CardY = ContentY + 30.0f;
        const float CardH = ContentH - 54.0f;
        for (int32 i = 0; i < 3; ++i)
        {
            const float CX = ArmyX + 8.0f + i * (CardW + CardGap);
            Panel(Canvas, CX, CardY, CardW, CardH, PanelInset);
            Label(Canvas, Font, Names[i], CX + 8.0f, CardY + 8.0f, StoneText);
            Label(Canvas, Font, FString::FromInt(Counts[i]), CX + 8.0f, CardY + CardH - 30.0f,
                  GoldText, 1.6f);
        }
        Label(Canvas, Font,
              FString::Printf(TEXT("%d regiments  -  %d mp"), Army->Regiments, Army->Mp),
              ArmyX + 8.0f, ContentY + ContentH - 18.0f, DimText);
    }
    else
    {
        Label(Canvas, Font, TEXT("no army selected"), ArmyX + 8.0f, ContentY + ContentH * 0.5f,
              DimText);
    }

    // ------------------------------------------------------------------ city
    // Centre panel: the buildings in the selected city, one row per building.
    const float CityX = ArmyX + ArmyW + 16.0f;
    const float CityW = 260.0f;
    Panel(Canvas, CityX, ContentY, CityW, ContentH, PanelDark);
    Label(Canvas, Font, TEXT("CITY"), CityX + 8.0f, ContentY + 6.0f, GoldText);

    const int32 SelProv = Map->GetSelectedProvince();
    const FProvinceState* Prov =
        (SelProv != TW_NONE && Snapshot.Provinces.IsValidIndex(SelProv)) ? &Snapshot.Provinces[SelProv]
                                                                         : nullptr;
    if (Prov != nullptr && !Prov->City.IsEmpty())
    {
        Label(Canvas, Font, Prov->City, CityX + 60.0f, ContentY + 6.0f, StoneText);
        const TCHAR* BNames[4] = {TEXT("Farm"), TEXT("Market"), TEXT("Barracks"), TEXT("Walls")};
        const int32 Levels[4] = {Prov->Farm, Prov->Market, Prov->Barracks, Prov->Walls};
        const float RowY = ContentY + 30.0f;
        constexpr float RowH = 20.0f;
        for (int32 i = 0; i < 4; ++i)
        {
            const float RY = RowY + i * RowH;
            Panel(Canvas, CityX + 8.0f, RY, CityW - 16.0f, RowH - 3.0f, PanelInset);
            Label(Canvas, Font, BNames[i], CityX + 16.0f, RY + 3.0f, StoneText);
            Label(Canvas, Font, FString::Printf(TEXT("lv %d"), Levels[i]),
                  CityX + CityW - 60.0f, RY + 3.0f, GoldText);
        }
    }
    else
    {
        Label(Canvas, Font, TEXT("no city selected"), CityX + 8.0f, ContentY + ContentH * 0.5f,
              DimText);
    }

    // --------------------------------------------------------------- actions
    // Right cluster, laid out from the right edge inward: two action buttons,
    // then treasury and turn readouts, then the end-turn button.
    auto Button = [&](float X, float Y, float BW, float BH, const FString& Text,
                      EControlAction Action, bool bEnabled) {
        Panel(Canvas, X, Y, BW, BH, PanelInset);
        Fill(Canvas, X, Y, BW, 3.0f, StoneLight);
        Label(Canvas, Font, Text, X + 10.0f, Y + BH * 0.5f - 8.0f, bEnabled ? GoldText : DimText);
        if (bEnabled)
        {
            ControlHits.Add({FBox2D(FVector2D(X, Y), FVector2D(X + BW, Y + BH)), Action});
        }
    };

    // A button only acts when the press could actually turn into a command: on
    // the player's turn, with the sim idle and the game unwon. Off-turn or mid-
    // resolution it greys out and stops taking the click, so the bar never offers
    // an order the rules would only refuse.
    const bool bReady = Snapshot.IsPlayerTurn() && !Subsystem->IsBusy() &&
                        Snapshot.Winner == TW_NONE && !Map->IsAnimating();
    const bool bHasCity = Prov != nullptr && !Prov->City.IsEmpty();

    const float EndW = 150.0f;
    const float EndX = W - Pad - EndW;
    const float RowTop = ContentY;

    // End turn — the biggest target, top-right.
    const float EndH = 44.0f;
    Button(EndX, RowTop, EndW, EndH, TEXT("End Turn  >>"), EControlAction::EndTurn, bReady);

    // Treasury and turn, stacked below the buttons.
    const FFactionState* Player =
        Snapshot.Factions.FindByPredicate([](const FFactionState& F) { return F.bIsPlayer; });
    const int32 Treasury = Player != nullptr ? Player->Treasury : 0;
    const int32 Income = Player != nullptr ? (Player->Income - Player->Upkeep) : 0;

    const float ReadY = RowTop + EndH + 8.0f;
    Panel(Canvas, EndX, ReadY, EndW, 30.0f, PanelDark);
    Label(Canvas, Font, FString::Printf(TEXT("Gold %d"), Treasury), EndX + 8.0f, ReadY + 7.0f,
          GoldText);
    Label(Canvas, Font, FString::Printf(TEXT("%+d"), Income), EndX + EndW - 54.0f, ReadY + 7.0f,
          Income >= 0 ? FColor(150, 210, 140) : FColor(215, 120, 110));

    Panel(Canvas, EndX, ReadY + 34.0f, EndW, 26.0f, PanelDark);
    Label(Canvas, Font, FString::Printf(TEXT("Turn %d"), Snapshot.Turn), EndX + 8.0f,
          ReadY + 39.0f, StoneText);

    // Construction / Recruit, to the left of the readouts.
    const float ActW = 130.0f;
    const float ActX = EndX - 12.0f - ActW;
    Button(ActX, RowTop, ActW, 40.0f, TEXT("Construct"), EControlAction::Construct,
           bReady && bHasCity);
    Button(ActX, RowTop + 48.0f, ActW, 40.0f, TEXT("Recruit"), EControlAction::Recruit,
           bReady && bHasCity);
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

        // Dark plate, faction stripe down the left edge, name in stone text. A
        // selected settlement gets a gold outline instead of the plain dark one.
        Fill(Canvas, PlateX, PlateY, PlateW, PlateH,
             FLinearColor(0.10f, 0.09f, 0.08f, 0.85f));
        Fill(Canvas, PlateX, PlateY, StripeW, PlateH, Faction);
        Outline(Canvas, PlateX, PlateY, PlateW, PlateH, bSelected ? GoldLine : PanelDark);

        Label(Canvas, Font, Name, PlateX + StripeW + PadX, PlateY + 3.0f,
              bSelected ? GoldText : StoneText);
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
