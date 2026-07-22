// Automation tests for the baked-map reader. Run headless with:
//   UnrealEditor-Cmd TotalWarlike.uproject -ExecCmds="Automation RunTests TotalWarlike.Map" \
//       -unattended -nullrhi -nosplash -nopause
// or `make unreal-test`, which runs everything under TotalWarlike.

#include "Misc/AutomationTest.h"
#include "Misc/Paths.h"

#include "../Map/MapData.h"

#if WITH_DEV_AUTOMATION_TESTS

namespace
{
// A strongly-typed enum as of 5.8, so this cannot be the int32 the older
// examples use — same trap as SimTransportTest.cpp. Named apart from that
// file's TestFlags so the two cannot collide if adaptive unity ever folds
// both into one translation unit.
constexpr EAutomationTestFlags MapTestFlags = EAutomationTestFlags::EditorContext |
                                              EAutomationTestFlags::ClientContext |
                                              EAutomationTestFlags::ProductFilter;

/// The baked map is generated (`make bake`) and gitignored, so a fresh clone has
/// no Content/Map to read. Absent data is not a failure; a silent pass would be,
/// so say so.
bool LoadBakedMap(FAutomationTestBase& Test, FMapData& Out)
{
    const FString MapDir = FPaths::Combine(FPaths::ProjectContentDir(), TEXT("Map"));
    FString Error;
    if (!Out.Load(MapDir, Error))
    {
        Test.AddInfo(FString::Printf(
            TEXT("skipped: no baked map at %s (%s) — run `make bake`"), *MapDir, *Error));
        return false;
    }
    return true;
}

} // namespace

/// The bug this exists for: terrain.obj winds counter-clockwise about the
/// right-hand-rule normal, which is what OBJ means and what the baker asserts
/// (`check_winding` in bake/src/main.rs). UProceduralMeshComponent takes the
/// CLOCKWISE order as front-facing. Feeding the OBJ order straight to
/// CreateMeshSection made every terrain triangle back-face, and the map rendered
/// as open sea — with no warning, because collision traces ignore winding, so
/// clicking and marker placement went on working. There was no symptom but
/// absence, which is exactly the kind of thing that needs a test rather than an
/// eye. ParseObj therefore reverses each face, and this pins that down: the
/// geometric normal of the stored order must oppose the shading normal.
IMPLEMENT_SIMPLE_AUTOMATION_TEST(FTerrainWindingTest, "TotalWarlike.Map.TerrainWinding",
                                 MapTestFlags)

bool FTerrainWindingTest::RunTest(const FString& Parameters)
{
    FMapData Map;
    if (!LoadBakedMap(*this, Map))
    {
        return true;
    }

    TestTrue(TEXT("terrain has triangles"), Map.TerrainIndices.Num() >= 3);
    TestEqual(TEXT("terrain indices are whole triangles"), Map.TerrainIndices.Num() % 3, 0);
    TestEqual(TEXT("one normal per vertex"), Map.TerrainNormals.Num(), Map.TerrainVertices.Num());

    // The test is purely geometric, and deliberately does NOT compare against the
    // per-vertex shading normals. Those are averaged across neighbours, so at a
    // sharp fold — a 99cm drop over the 130cm grid is real terrain here — a vertex
    // normal can sit more than 90 degrees off its own face and look like a winding
    // error when nothing is wrong. Two triangles in the current bake do exactly
    // that. Projected area is the honest question: the mesh is a heightfield, so
    // every face has sky on one side, and after ParseObj's reversal every one of
    // them must present that side to a camera looking down.
    int32 BackFacing = 0;
    int32 Degenerate = 0;
    double WorstMagnitude = TNumericLimits<double>::Max();
    for (int32 i = 0; i + 2 < Map.TerrainIndices.Num(); i += 3)
    {
        const int32 IA = Map.TerrainIndices[i];
        const int32 IB = Map.TerrainIndices[i + 1];
        const int32 IC = Map.TerrainIndices[i + 2];
        if (!Map.TerrainVertices.IsValidIndex(IA) || !Map.TerrainVertices.IsValidIndex(IB) ||
            !Map.TerrainVertices.IsValidIndex(IC))
        {
            AddError(FString::Printf(TEXT("face %d indexes outside the vertex array"), i / 3));
            return false;
        }

        const FVector A = Map.TerrainVertices[IA];
        const FVector U = Map.TerrainVertices[IB] - A;
        const FVector V = Map.TerrainVertices[IC] - A;
        // Z of the cross product, i.e. twice the signed area seen from above.
        const double SignedArea = U.X * V.Y - U.Y * V.X;

        if (SignedArea == 0.0)
        {
            ++Degenerate;
            continue;
        }
        if (SignedArea > 0.0)
        {
            ++BackFacing;
        }
        WorstMagnitude = FMath::Min(WorstMagnitude, FMath::Abs(SignedArea));
    }

    if (BackFacing > 0)
    {
        AddError(FString::Printf(
            TEXT("%d of %d terrain triangles are wound counter-clockwise seen from above. That is "
                 "OBJ's convention, not UProceduralMeshComponent's — those faces are back-facing, "
                 "so the terrain renders as open sea while still taking clicks, because collision "
                 "traces ignore winding. ParseObj must reverse each face; see MapData.cpp."),
            BackFacing, Map.TerrainIndices.Num() / 3));
        return false;
    }

    // A grid this regular has no slivers at all, so a degenerate face means the
    // baker emitted something it did not mean to.
    TestEqual(TEXT("no degenerate terrain triangles"), Degenerate, 0);

    AddInfo(FString::Printf(
        TEXT("%d terrain triangles all front-face for Unreal (min |projected area| %.0f cm2)"),
        Map.TerrainIndices.Num() / 3, WorstMagnitude));
    return true;
}

#endif // WITH_DEV_AUTOMATION_TESTS
