// Deterministic screenshots of the campaign map, for iterating on visuals.
//
// WHY THIS EXISTS
// ---------------
// Editing the look of this game is a loop: change a shader/colour/material, look
// at the result, change it again. Before this file the "look at the result" step
// was `osascript ... activate` plus a full-screen `screencapture` of whatever
// window happened to be frontmost, from wherever the camera happened to be
// pointing. That is not a loop anyone — human or agent — can iterate in: two of
// the three steps are unreliable, and no two captures were comparable.
//
// So: named camera PRESETS, captured headlessly, to a fixed path. The same
// preset always frames the same thing, which is the property that makes two PNGs
// worth diffing.
//
// TWO WAYS IN
// -----------
//   1. Command line — the batch path, used by `make unreal-shots`:
//        -TWShots=overview,mountain -TWShotDir=<dir> [-TWShotSettle=12] [-TWShotQuit]
//      Deliberately NOT `-ExecCmds`: those fire during engine init, long before
//      the game instance, the campaign map or the first snapshot exist. Parsing
//      our own switch lets the director wait for the world to actually be ready.
//
//   2. Console — the live path, for iterating without paying a relaunch:
//        TWView mountain      move the camera to a preset
//        TWShot foo           capture the current view to <ShotDir>/foo.png
//        TWShots a,b,c        run a sequence
//      Combined with `recompileshaders changed` this is the ~2s edit/view loop
//      the ~20s relaunch used to be.
//
// The director never blocks. It is a small state machine on the core ticker,
// because a screenshot is only correct once the snapshot has arrived AND the
// camera move has been rendered — both of which are several frames away from the
// call that asked for them.

#pragma once

#include "CoreMinimal.h"
#include "Containers/Ticker.h"
#include "Subsystems/GameInstanceSubsystem.h"

#include "ShotDirector.generated.h"

class ACampaignMap;
class ACampaignPlayerController;

/// A named camera framing. Anchored to a province by NAME rather than to raw
/// coordinates so a rebake that shifts the coastline moves the shot with it —
/// "the Highlands" stays the Highlands.
struct FShotPreset
{
    const TCHAR* Name;
    /// Province to centre on; empty centres on the map origin.
    const TCHAR* Province;
    /// Camera distance in centimetres, clamped to the controller's zoom range.
    double Distance;
    /// Province to SELECT (by name) before the shot, so the HUD's army and city
    /// panels are populated rather than empty. Empty leaves the selection alone —
    /// which is what the terrain presets want, so their diffs stay about terrain.
    /// A player army standing on the province is selected too, when one is there.
    const TCHAR* Select;
    /// What this preset is for, printed by `TWView` with no argument.
    const TCHAR* Purpose;
};

UCLASS()
class UShotDirector : public UGameInstanceSubsystem
{
    GENERATED_BODY()

public:
    virtual void Initialize(FSubsystemCollectionBase& Collection) override;
    virtual void Deinitialize() override;

    /// Point the camera at a named preset. Returns false for an unknown name.
    UFUNCTION(Exec)
    void TWView(const FString& Preset);

    /// Capture the current view to `<ShotDir>/<Name>.png`.
    UFUNCTION(Exec)
    void TWShot(const FString& Name);

    /// Capture a comma-separated list of presets, one file each.
    UFUNCTION(Exec)
    void TWShots(const FString& Presets);

    /// List the presets and where shots are being written.
    UFUNCTION(Exec)
    void TWShotsList();

private:
    enum class EPhase : uint8
    {
        Idle,
        /// Waiting for the map, the controller and the first snapshot.
        WaitForWorld,
        /// Camera is placed; letting the frame settle (shaders, streaming, fog).
        Settle,
        /// Screenshot requested; waiting for the file to appear on disk.
        Capture,
        /// Every shot taken. Quits if we were driven by the command line.
        Done,
    };

    bool Tick(float DeltaTime);

    /// True once the sim has answered and the map has geometry to photograph.
    bool WorldIsReady() const;

    /// Apply `Pending[Index]` to the camera. False if the preset is unknown.
    bool ApplyCurrentPreset();

    /// Pin exposure before any capture, so two runs are comparable.
    void MakeExposureDeterministic();

    void RequestCapture();
    void Finish();

    static const FShotPreset* FindPreset(const FString& Name);

    ACampaignMap* FindMap() const;
    ACampaignPlayerController* FindController() const;

    FTSTicker::FDelegateHandle TickerHandle;

    EPhase Phase = EPhase::Idle;

    /// Preset names still to shoot, and where we are in that list.
    TArray<FString> Pending;
    int32 Index = 0;

    /// Frames to hold a placed camera before capturing. The default is generous
    /// on purpose: a cold shader compile or a fog/exposure ramp caught mid-way
    /// is a silent, confusing diff in the output PNG.
    int32 SettleFrames = 12;
    int32 FramesWaited = 0;

    /// Absolute directory the PNGs land in.
    FString ShotDir;

    /// Where the current capture is being written, so we can watch for it.
    FString PendingFile;

    /// Set by `-TWShotQuit` (implied by `-TWShots`): exit when the list is done.
    bool bQuitWhenDone = false;

    /// True once a single ad-hoc `TWShot` is in flight, so we do not exit after it.
    bool bAdHoc = false;

    /// Whether eye adaptation has already been pinned; see MakeExposureDeterministic.
    bool bExposureFixed = false;

    /// Frames to burn once, after the world is ready, before the FIRST shot.
    /// Shots 2..n are taken by a renderer that has already been running for a
    /// second; without this the first one is not, and it alone diffs against
    /// itself between runs. Cheap insurance at ~0.7s of a ~10s batch.
    int32 WarmUpFrames = 45;
    bool bWarmedUp = false;
};
