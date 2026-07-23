#include "Ribbon.h"

namespace tw
{

void BuildRibbon(const TArray<FVector>& Points, float Width, float ZBias,
                 TArray<FVector>& OutVertices, TArray<int32>& OutTriangles,
                 TArray<FVector>& OutNormals, float LateralOffset)
{
    const int32 N = Points.Num();
    if (N < 2)
    {
        return;
    }

    const int32 Base = OutVertices.Num();
    const FVector Lift(0.0, 0.0, ZBias);

    for (int32 i = 0; i < N; ++i)
    {
        // Offset sideways from the direction of travel, measured flat: the
        // ribbon lies on the ground, so only the XY heading steers it.
        FVector Forward;
        if (i == 0)
        {
            Forward = Points[1] - Points[0];
        }
        else if (i == N - 1)
        {
            Forward = Points[N - 1] - Points[N - 2];
        }
        else
        {
            Forward = Points[i + 1] - Points[i - 1];
        }
        Forward.Z = 0.0;
        if (Forward.Size() < 0.0001)
        {
            Forward = FVector::ForwardVector;
        }

        const FVector SideDir =
            FVector::CrossProduct(Forward.GetSafeNormal(), FVector::UpVector);
        const FVector Side = SideDir * (Width * 0.5f);
        const FVector Centre = Points[i] + SideDir * LateralOffset + Lift;
        OutVertices.Add(Centre - Side);
        OutVertices.Add(Centre + Side);
        OutNormals.Add(FVector::UpVector);
        OutNormals.Add(FVector::UpVector);
    }

    for (int32 i = 0; i < N - 1; ++i)
    {
        const int32 B = Base + i * 2;
        OutTriangles.Append({B, B + 2, B + 1, B + 1, B + 2, B + 3});
    }
}

} // namespace tw
