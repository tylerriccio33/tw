//! Buildings: a small fixed catalog of level-1-only construction projects a
//! faction can queue in one of its cities. Mirrors the shape of
//! `model::IncomeSource` - a fixed enum, a `spec()`/`ALL` pair, iterated
//! rather than hardcoded wherever the full set matters - but buildings also
//! carry a one-time gold cost, a build time, and a population gate, and once
//! built they can themselves add a new resource stream to a city.
//!
//! There are deliberately no upgrade tiers: every `BuildingKind` here *is*
//! its own level 1, and there's no level 2 to grow into.

/// A resource a completed building can add to its city's output. Kept
/// separate from `model::IncomeSource` (which is a *faction*-level income
/// stream) because a building produces at the city, in whatever resource
/// fits it - only `ResourceKind::Gold` currently feeds the treasury (via
/// `IncomeSource::BuildingProduction`); `Food` is tracked but not consumed by
/// anything yet, same as tier/tax before buildings existed.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ResourceKind {
    Gold,
    Food,
}

/// One of the fixed, level-1-only building types a city can construct.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum BuildingKind {
    Farm,
    Mine,
    Market,
    Barracks,
}

/// The static definition of a `BuildingKind`: what it costs, how long it
/// takes, what it produces (if anything), and the population a city needs
/// before it can be built there.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct BuildingSpec {
    pub kind: BuildingKind,
    pub name: &'static str,
    pub gold_cost: i32,
    pub build_time_turns: u32,
    /// `Some((resource, amount))` for buildings that add per-turn output once
    /// complete; `None` for buildings (like Barracks) that don't produce a
    /// resource at all.
    pub production: Option<(ResourceKind, i32)>,
    pub required_population: u32,
}

/// Population a settlement is assumed to have, derived from its tier - there
/// is no separate mutable population field on `City`, since level-1-only
/// buildings never need population to grow over time, only to gate whether a
/// given settlement is big enough to support a building at all.
pub const POPULATION_PER_TIER: u32 = 500;

pub fn population_for_tier(tier: u32) -> u32 {
    tier * POPULATION_PER_TIER
}

impl BuildingKind {
    /// Every building type, so callers can list the full catalog without
    /// hardcoding which types exist.
    pub const ALL: [BuildingKind; 4] = [
        BuildingKind::Farm,
        BuildingKind::Mine,
        BuildingKind::Market,
        BuildingKind::Barracks,
    ];

    /// This building's fixed level-1 definition.
    pub fn spec(self) -> BuildingSpec {
        match self {
            BuildingKind::Farm => BuildingSpec {
                kind: self,
                name: "Farm",
                gold_cost: 80,
                build_time_turns: 1,
                production: Some((ResourceKind::Food, 15)),
                required_population: 0,
            },
            BuildingKind::Mine => BuildingSpec {
                kind: self,
                name: "Mine",
                gold_cost: 150,
                build_time_turns: 2,
                production: Some((ResourceKind::Gold, 20)),
                required_population: 300,
            },
            BuildingKind::Market => BuildingSpec {
                kind: self,
                name: "Market",
                gold_cost: 250,
                build_time_turns: 3,
                production: Some((ResourceKind::Gold, 35)),
                required_population: 500,
            },
            BuildingKind::Barracks => BuildingSpec {
                kind: self,
                name: "Barracks",
                gold_cost: 200,
                build_time_turns: 2,
                production: None,
                required_population: 800,
            },
        }
    }

    pub fn key(self) -> &'static str {
        match self {
            BuildingKind::Farm => "farm",
            BuildingKind::Mine => "mine",
            BuildingKind::Market => "market",
            BuildingKind::Barracks => "barracks",
        }
    }

    pub fn from_key(key: &str) -> Option<BuildingKind> {
        BuildingKind::ALL.into_iter().find(|k| k.key() == key)
    }
}

/// A building under construction in a city: what's being built and how many
/// more of the owner's turns it needs before it completes. One construction
/// project per city at a time - see `Campaign::start_construction`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Construction {
    pub kind: BuildingKind,
    pub turns_remaining: u32,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn catalog_costs_times_and_production_are_fixed() {
        let farm = BuildingKind::Farm.spec();
        assert_eq!(farm.gold_cost, 80);
        assert_eq!(farm.build_time_turns, 1);
        assert_eq!(farm.production, Some((ResourceKind::Food, 15)));
        assert_eq!(farm.required_population, 0);

        let mine = BuildingKind::Mine.spec();
        assert_eq!(mine.gold_cost, 150);
        assert_eq!(mine.build_time_turns, 2);
        assert_eq!(mine.production, Some((ResourceKind::Gold, 20)));
        assert_eq!(mine.required_population, 300);

        let market = BuildingKind::Market.spec();
        assert_eq!(market.gold_cost, 250);
        assert_eq!(market.build_time_turns, 3);
        assert_eq!(market.production, Some((ResourceKind::Gold, 35)));
        assert_eq!(market.required_population, 500);

        let barracks = BuildingKind::Barracks.spec();
        assert_eq!(barracks.gold_cost, 200);
        assert_eq!(barracks.build_time_turns, 2);
        assert_eq!(barracks.production, None);
        assert_eq!(barracks.required_population, 800);
    }

    #[test]
    fn every_building_kind_has_a_spec_matching_its_own_kind() {
        for kind in BuildingKind::ALL {
            assert_eq!(kind.spec().kind, kind);
        }
    }

    #[test]
    fn key_round_trips_through_from_key() {
        for kind in BuildingKind::ALL {
            assert_eq!(BuildingKind::from_key(kind.key()), Some(kind));
        }
        assert_eq!(BuildingKind::from_key("nonsense"), None);
    }

    #[test]
    fn population_scales_with_tier() {
        assert_eq!(population_for_tier(0), 0);
        assert_eq!(population_for_tier(1), 500);
        assert_eq!(population_for_tier(4), 2000);
    }
}
