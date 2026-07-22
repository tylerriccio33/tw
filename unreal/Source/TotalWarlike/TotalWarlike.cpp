#include "TotalWarlike.h"

#include "Misc/Paths.h"
#include "Modules/ModuleManager.h"
#include "ShaderCore.h"

// The sim connection is still started by the map, not by the module coming up
// (see USimSubsystem::Initialize). The one thing that must happen this early is
// making sure Shaders/ is reachable as a virtual shader directory: the terrain
// material's Custom node includes `/Project/TerrainCommon.ush`, and that mapping
// has to exist before any material is compiled.
//
// It usually already does — the engine maps `/Project` to <project>/Shaders on
// its own — and AddShaderSourceDirectoryMapping ASSERTS on a duplicate rather
// than overwriting, which is a hard crash during module load with a backtrace
// that points here. So this checks first, and exists only to cover the case
// where the engine's own registration goes away.
class FTotalWarlikeModule : public FDefaultGameModuleImpl
{
public:
    virtual void StartupModule() override
    {
        if (AllShaderSourceDirectoryMappings().Contains(TEXT("/Project")))
        {
            return;
        }
        const FString ShaderDir = FPaths::Combine(FPaths::ProjectDir(), TEXT("Shaders"));
        AddShaderSourceDirectoryMapping(TEXT("/Project"), ShaderDir);
    }
};

IMPLEMENT_PRIMARY_GAME_MODULE(FTotalWarlikeModule, TotalWarlike, "TotalWarlike");
