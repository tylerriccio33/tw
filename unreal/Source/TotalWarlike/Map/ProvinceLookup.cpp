#include "ProvinceLookup.h"

namespace tw
{

std::size_t ProvinceAt(const std::vector<FProvinceSite>& Sites, double X, double Y)
{
    std::size_t Best = kNoProvince;
    double BestSq = 0.0;
    for (std::size_t i = 0; i < Sites.size(); ++i)
    {
        const double dx = Sites[i].X - X;
        const double dy = Sites[i].Y - Y;
        const double Sq = dx * dx + dy * dy;
        // Strictly less-than, so a tie keeps the earlier province.
        if (Best == kNoProvince || Sq < BestSq)
        {
            Best = i;
            BestSq = Sq;
        }
    }
    return Best;
}

} // namespace tw
