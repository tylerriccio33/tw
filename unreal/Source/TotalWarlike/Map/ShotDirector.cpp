#include "ShotDirector.h"

#include "EngineUtils.h"
#include "Engine/GameInstance.h"
#include "Engine/World.h"
#include "HAL/IConsoleManager.h"
#include "HAL/PlatformMisc.h"
#include "Kismet/GameplayStatics.h"
#include "Misc/CommandLine.h"
#include "Misc/FileHelper.h"
#include "Misc/Parse.h"
#include "Misc/Paths.h"
#include "UnrealClient.h"

#include "../Sim/SimSubsystem.h"
#include "CampaignMap.h"
#include "CampaignPlayerController.h"

DEFINE_LOG_CATEGORY_STATIC(LogShotDirector, Log, All);

namespace
{

constexpr double GodotToUU = 100.0;

/// The shot set. Chosen to cover the things the terrain material and the map
/// layers actually differ on — sea/land edge, snow line, slope shading, faction
/// ink — so that a change nobody meant to make shows up in at least one frame.
///
/// Anchored by province name (see FShotPreset), so these survive a rebake.
const FShotPreset Presets[] = {
    {TEXT("overview"), TEXT(""), 1400.0 * GodotToUU, TEXT(""),
     TEXT("the whole island — silhouette, sea colour, fog, faction ink")},
    {TEXT("lowlands"), TEXT("London"), 420.0 * GodotToUU, TEXT(""),
     TEXT("low green terrain, rivers, a settlement marker up close")},
    {TEXT("coast"), TEXT("Exeter"), 520.0 * GodotToUU, TEXT(""),
     TEXT("the land/sea edge — the coastline blend and the water plane")},
    {TEXT("mountain"), TEXT("Highlands"), 520.0 * GodotToUU, TEXT(""),
     TEXT("the height and slope thresholds: rock, snow line, steep faces")},
    {TEXT("border"), TEXT("Lothian"), 620.0 * GodotToUU, TEXT(""),
     TEXT("a contested frontier — border colours and army markers")},
    {TEXT("hud"), TEXT("London"), 520.0 * GodotToUU, TEXT("London"),
     TEXT("the control bar populated — London selected, its army and buildings")},
};

/// Where shots go when nothing said otherwise. Repo-relative via the project
/// dir, so `make unreal-shots` and a console `TWShot` land in the same place.
FString DefaultShotDir()
{
    return FPaths::ConvertRelativePathToFull(FPaths::ProjectDir() / TEXT("Shots") / TEXT("current"));
}

} // namespace

void UShotDirector::Initialize(FSubsystemCollectionBase& Collection)
{
    Super::Initialize(Collection);

    ShotDir = DefaultShotDir();
    FString Override;
    if (FParse::Value(FCommandLine::Get(), TEXT("-TWShotDir="), Override))
    {
        ShotDir = FPaths::ConvertRelativePathToFull(Override);
    }
    FParse::Value(FCommandLine::Get(), TEXT("-TWShotSettle="), SettleFrames);

    // The ticker runs whether or not a batch was requested: the console path
    // needs it too, and an idle tick is a single enum compare.
    TickerHandle = FTSTicker::GetCoreTicker().AddTicker(
        FTickerDelegate::CreateUObject(this, &UShotDirector::Tick));

    FString List;
    // bShouldStopOnSeparator=false, or FParse stops at the first comma and a
    // five-preset batch silently becomes a one-preset batch.
    if (FParse::Value(FCommandLine::Get(), TEXT("-TWShots="), List, /*bShouldStopOnSeparator=*/false) &&
        !List.IsEmpty())
    {
        List.ParseIntoArray(Pending, TEXT(","), /*CullEmpty=*/true);
        for (FString& Name : Pending)
        {
            Name.TrimStartAndEndInline();
        }
        // A batch run is headless and unattended by definition — if it did not
        // exit, `make unreal-shots` would hang forever on a machine where disk
        // is the binding constraint.
        bQuitWhenDone = true;
        Index = 0;
        Phase = EPhase::WaitForWorld;
        MakeExposureDeterministic();
        UE_LOG(LogShotDirector, Display, TEXT("shot batch: %s -> %s"), *List, *ShotDir);
    }
}

void UShotDirector::Deinitialize()
{
    if (TickerHandle.IsValid())
    {
        FTSTicker::GetCoreTicker().RemoveTicker(TickerHandle);
        TickerHandle.Reset();
    }
    Super::Deinitialize();
}

const FShotPreset* UShotDirector::FindPreset(const FString& Name)
{
    for (const FShotPreset& Preset : Presets)
    {
        if (Name.Equals(Preset.Name, ESearchCase::IgnoreCase))
        {
            return &Preset;
        }
    }
    return nullptr;
}

ACampaignMap* UShotDirector::FindMap() const
{
    UWorld* World = GetGameInstance() != nullptr ? GetGameInstance()->GetWorld() : nullptr;
    if (World == nullptr)
    {
        return nullptr;
    }
    TActorIterator<ACampaignMap> It(World);
    return It ? *It : nullptr;
}

ACampaignPlayerController* UShotDirector::FindController() const
{
    UWorld* World = GetGameInstance() != nullptr ? GetGameInstance()->GetWorld() : nullptr;
    return World != nullptr ? Cast<ACampaignPlayerController>(World->GetFirstPlayerController())
                            : nullptr;
}

bool UShotDirector::WorldIsReady() const
{
    const USimSubsystem* Sim =
        GetGameInstance() != nullptr ? GetGameInstance()->GetSubsystem<USimSubsystem>() : nullptr;
    if (Sim == nullptr || Sim->GetSnapshot().Provinces.Num() == 0)
    {
        // No snapshot yet: the markers and border colours do not exist, so a
        // frame taken now would be of a half-built map.
        return false;
    }
    const ACampaignMap* Map = FindMap();
    return Map != nullptr && Map->GetMapData().Provinces.Num() > 0 && FindController() != nullptr;
}

bool UShotDirector::ApplyCurrentPreset()
{
    if (!Pending.IsValidIndex(Index))
    {
        return false;
    }
    const FString& Name = Pending[Index];
    const FShotPreset* Preset = FindPreset(Name);
    ACampaignPlayerController* Controller = FindController();
    ACampaignMap* Map = FindMap();
    if (Preset == nullptr || Controller == nullptr || Map == nullptr)
    {
        UE_LOG(LogShotDirector, Error, TEXT("unknown preset '%s' — skipping"), *Name);
        return false;
    }

    FVector2D Target = FVector2D::ZeroVector;
    if (FCString::Strlen(Preset->Province) > 0)
    {
        const FMapData& Data = Map->GetMapData();
        const FProvinceSite3D* Site = Data.Provinces.FindByPredicate(
            [Preset](const FProvinceSite3D& P) { return P.Name.Equals(Preset->Province); });
        if (Site == nullptr)
        {
            // A rebake that renamed or dropped a province: loud, because a shot
            // silently falling back to the origin would look like a visual
            // regression in every future diff.
            UE_LOG(LogShotDirector, Error, TEXT("preset '%s' wants province '%s', which the bake "
                                                "does not have — skipping"),
                   *Name, Preset->Province);
            return false;
        }
        Target = FVector2D(Site->Position.X, Site->Position.Y);
    }

    Controller->SetView(Target, Preset->Distance);

    // Establish a selection for HUD presets, so the control bar's army and city
    // panels have something to draw. Terrain presets leave Select empty and the
    // current selection untouched.
    if (FCString::Strlen(Preset->Select) > 0)
    {
        const USimSubsystem* Sim =
            GetGameInstance() != nullptr ? GetGameInstance()->GetSubsystem<USimSubsystem>() : nullptr;
        if (Sim != nullptr)
        {
            const FWorldSnapshot& Snapshot = Sim->GetSnapshot();
            // Province ids ARE snapshot array indices — the HUD reads the
            // selected province the same way (see CampaignHUD::DrawControlBar).
            const int32 ProvId = Snapshot.Provinces.IndexOfByPredicate(
                [Preset](const FProvinceState& P) { return P.Name.Equals(Preset->Select); });
            if (ProvId == INDEX_NONE)
            {
                UE_LOG(LogShotDirector, Error,
                       TEXT("preset '%s' wants to select province '%s', which the snapshot does "
                            "not have — leaving selection empty"),
                       *Name, Preset->Select);
            }
            else
            {
                // A player army standing on the province fills the army panel; a
                // garrison alone is not an army and leaves that panel empty.
                const FArmyState* Army = Snapshot.Armies.FindByPredicate(
                    [ProvId, &Snapshot](const FArmyState& A) {
                        return A.Location == ProvId && Snapshot.Factions.IsValidIndex(A.Owner) &&
                               Snapshot.Factions[A.Owner].bIsPlayer;
                    });
                Map->SetSelection(Army != nullptr ? Army->Id : INDEX_NONE, ProvId);
            }
        }
    }
    return true;
}

void UShotDirector::MakeExposureDeterministic()
{
    if (bExposureFixed)
    {
        return;
    }
    // Eye adaptation ramps over roughly a second after the level loads, so the
    // FIRST shot of a batch is taken mid-ramp and every later one is not. That
    // showed up as `overview` differing from itself by a mean of 22/255 across
    // 99% of pixels between two identical runs — a whole-frame brightness shift
    // that would drown out any real change being looked for.
    //
    // Turning adaptation off makes exposure a constant, which is what a
    // comparable screenshot needs. Only the shot path does this; a normal
    // `make unreal-play` keeps the adaptive exposure the game actually ships.
    if (IConsoleVariable* Quality =
            IConsoleManager::Get().FindConsoleVariable(TEXT("r.EyeAdaptationQuality")))
    {
        Quality->Set(0, ECVF_SetByCode);
    }
    bExposureFixed = true;
}

void UShotDirector::RequestCapture()
{
    const FString Name = Pending.IsValidIndex(Index) ? Pending[Index] : TEXT("shot");
    PendingFile = ShotDir / (Name + TEXT(".png"));

    // Overwrite rather than accumulate: the whole point is that `current/` is
    // comparable against `golden/` file for file.
    IFileManager::Get().Delete(*PendingFile, /*RequireExists=*/false, /*EvenReadOnly=*/true);
    IFileManager::Get().MakeDirectory(*ShotDir, /*Tree=*/true);

    // bAddFilenameSuffix=false is the reason this writes to a predictable path
    // instead of the usual Saved/Screenshots/…/ScreenShot00003.png.
    FScreenshotRequest::RequestScreenshot(PendingFile, /*bInShowUI=*/false,
                                          /*bAddFilenameSuffix=*/false);
    FramesWaited = 0;
}

void UShotDirector::Finish()
{
    Phase = EPhase::Done;
    Pending.Reset();
    Index = 0;
    if (bQuitWhenDone)
    {
        UE_LOG(LogShotDirector, Display, TEXT("shots complete — exiting"));
        FPlatformMisc::RequestExit(/*Force=*/false);
    }
    bAdHoc = false;
}

bool UShotDirector::Tick(float DeltaTime)
{
    switch (Phase)
    {
    case EPhase::Idle:
    case EPhase::Done:
        break;

    case EPhase::WaitForWorld:
        if (WorldIsReady())
        {
            if (!bWarmedUp)
            {
                // Not a settle: this is once-per-process, and it is what makes
                // the first shot of a batch as warm as the last.
                if (++FramesWaited < WarmUpFrames)
                {
                    break;
                }
                bWarmedUp = true;
                FramesWaited = 0;
            }
            if (!Pending.IsValidIndex(Index))
            {
                Finish();
                break;
            }
            if (!ApplyCurrentPreset())
            {
                // Skip the bad preset rather than abandoning the batch; the
                // error is already logged and the other shots are still useful.
                ++Index;
                break;
            }
            FramesWaited = 0;
            Phase = EPhase::Settle;
        }
        break;

    case EPhase::Settle:
        if (++FramesWaited >= SettleFrames)
        {
            RequestCapture();
            Phase = EPhase::Capture;
        }
        break;

    case EPhase::Capture:
        ++FramesWaited;
        if (IFileManager::Get().FileExists(*PendingFile))
        {
            UE_LOG(LogShotDirector, Display, TEXT("wrote %s"), *PendingFile);
            if (bAdHoc)
            {
                Phase = EPhase::Idle;
                bAdHoc = false;
                break;
            }
            ++Index;
            Phase = Pending.IsValidIndex(Index) ? EPhase::WaitForWorld : EPhase::Done;
            if (Phase == EPhase::Done)
            {
                Finish();
            }
        }
        else if (FramesWaited > SettleFrames + 300)
        {
            // Five-ish seconds with no file. Better to fail the batch than to
            // leave a headless run spinning on a machine this disk-constrained.
            UE_LOG(LogShotDirector, Error, TEXT("screenshot never appeared: %s"), *PendingFile);
            Finish();
        }
        break;
    }
    return true;
}

void UShotDirector::TWView(const FString& Preset)
{
    if (Preset.IsEmpty())
    {
        TWShotsList();
        return;
    }
    Pending = {Preset};
    Index = 0;
    if (!ApplyCurrentPreset())
    {
        TWShotsList();
    }
    Pending.Reset();
}

void UShotDirector::TWShot(const FString& Name)
{
    Pending = {Name.IsEmpty() ? TEXT("shot") : Name};
    Index = 0;
    // Deliberately does NOT move the camera: this captures whatever you are
    // looking at, which is what you want after a `TWView` plus a manual nudge.
    bAdHoc = true;
    MakeExposureDeterministic();
    FramesWaited = 0;
    Phase = EPhase::Settle;
}

void UShotDirector::TWShots(const FString& InPresets)
{
    Pending.Reset();
    FString List = InPresets;
    if (List.IsEmpty())
    {
        for (const FShotPreset& Preset : Presets)
        {
            Pending.Add(Preset.Name);
        }
    }
    else
    {
        List.ParseIntoArray(Pending, TEXT(","), /*CullEmpty=*/true);
        for (FString& Name : Pending)
        {
            Name.TrimStartAndEndInline();
        }
    }
    Index = 0;
    bAdHoc = false;
    // A console-driven batch is someone watching; do not pull the world out
    // from under them when it finishes.
    bQuitWhenDone = false;
    MakeExposureDeterministic();
    Phase = EPhase::WaitForWorld;
}

void UShotDirector::TWShotsList()
{
    UE_LOG(LogShotDirector, Display, TEXT("shots -> %s"), *ShotDir);
    for (const FShotPreset& Preset : Presets)
    {
        UE_LOG(LogShotDirector, Display, TEXT("  %-10s %s"), Preset.Name, Preset.Purpose);
    }
}
