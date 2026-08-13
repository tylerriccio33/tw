//! Pure game logic for the campaign map, independent of Godot.

use rand::Rng;
use std::collections::HashMap;

pub type FactionId = u32;
pub type CityId = u32;
pub type ArmyId = u32;
pub type ProvinceId = u32;

/// Move points an army gets back at the start of each of its owner's turns.
/// Move points are spent 1:1 in world units of straight-line distance - there
/// is deliberately no terrain cost, so a mountain and a road cost the same and
/// the ocean is walkable. See `Campaign::move_army`.
pub const DEFAULT_MOVE_POINTS: f32 = 450.0;

/// Provinces a garrisoned-or-marching army can hop across in one of its
/// owner's turns, once the campaign has territory attached
/// (`Campaign::with_provinces`). Each hop is a whole move onto an adjacent
/// province - see `Campaign::move_army`'s province branch - so with the
/// default of 2 an army can push two provinces deep in a single turn.
pub const DEFAULT_MOVES: u32 = 2;

/// How close two opposing armies must end up before they fight a field battle.
const ENGAGE_RADIUS: f32 = 60.0;

/// How close an army must get to a city to besiege it (or to garrison in it).
/// Sized off `config/world.json`'s `cities.radius` (92) so "on the city" means
/// anywhere in its built-up area rather than exactly on the centre point.
const CITY_RADIUS: f32 = 110.0;

fn distance(a: (f32, f32), b: (f32, f32)) -> f32 {
    let (dx, dy) = (b.0 - a.0, b.1 - a.1);
    (dx * dx + dy * dy).sqrt()
}

#[derive(Debug, Clone)]
pub struct City {
    pub id: CityId,
    pub name: String,
    /// Total money this city contributes to its owner each turn. Right now the
    /// only source is Ordinary Tax (`ordinary_tax`), keyed off `tier`, so a
    /// city built from a map package has `income == ordinary_tax(tier)`. Kept
    /// as its own field (rather than always recomputing from `tier`) so future
    /// income sources can be folded in here without every reader learning about
    /// them.
    pub income: i32,
    /// Settlement size class from the map's city layer (1 = smallest). Drives
    /// the Ordinary Tax obligation via `ordinary_tax`; higher tiers owe more.
    pub tier: u32,
    pub position: (f32, f32),
    pub owner: FactionId,
    /// The province this city sits in, when the campaign was built from a map
    /// package. Taking the city takes the province with it.
    pub province: Option<ProvinceId>,
}

/// Money a single unowned-of-source obligation - the "Ordinary Tax" the faction
/// head levies on each settlement - is worth per tier level. Tier 1 owes this
/// much, tier 2 twice as much, and so on, so bigger settlements shoulder a
/// proportionally bigger share of the treasury.
pub const ORDINARY_TAX_PER_TIER: i32 = 25;

/// The Ordinary Tax a settlement of `tier` owes its faction each turn. A tier
/// of 0 (an untiered settlement) owes nothing.
pub fn ordinary_tax(tier: u32) -> i32 {
    tier as i32 * ORDINARY_TAX_PER_TIER
}

/// A value read off a map layer, whatever that layer happens to describe.
///
/// Deliberately untyped: the map format lets anyone add a layer (roads,
/// climate, culture) by dropping in a PNG and a JSON legend, and the whole
/// point is that doing so needs no change here. Naming the tags in the type
/// system would put that promise back in Rust's hands.
#[derive(Debug, Clone, PartialEq)]
pub enum TagValue {
    Str(String),
    Num(f64),
    List(Vec<String>),
}

impl TagValue {
    pub fn as_str(&self) -> Option<&str> {
        match self {
            TagValue::Str(s) => Some(s),
            _ => None,
        }
    }

    pub fn as_num(&self) -> Option<f64> {
        match self {
            TagValue::Num(n) => Some(*n),
            _ => None,
        }
    }

    pub fn as_list(&self) -> Option<&[String]> {
        match self {
            TagValue::List(items) => Some(items),
            _ => None,
        }
    }
}

/// One province: the atomic unit of territory.
///
/// Geometry lives in the map package and never crosses into Rust - a province
/// here is an id, who holds it, what it borders, and whatever its layers said
/// about it.
#[derive(Debug, Clone)]
pub struct Province {
    pub id: ProvinceId,
    pub key: String,
    pub name: String,
    pub centroid: (f32, f32),
    pub neighbors: Vec<ProvinceId>,
    pub tags: HashMap<String, TagValue>,
    /// Seeded from the map's `starting_owner`, then owned by the simulation.
    /// `None` means nobody holds it.
    pub owner: Option<FactionId>,
}

impl Province {
    pub fn tag(&self, name: &str) -> Option<&TagValue> {
        self.tags.get(name)
    }

    pub fn borders(&self, other: ProvinceId) -> bool {
        self.neighbors.contains(&other)
    }
}

#[derive(Debug, Clone)]
pub struct Faction {
    pub id: FactionId,
    pub name: String,
    pub money: i32,
    pub alive: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct AttackOutcome {
    pub attacker_won: bool,
    pub defender_eliminated: bool,
}

#[derive(Debug, Clone)]
pub struct Army {
    pub id: ArmyId,
    pub name: String,
    pub owner: FactionId,
    pub position: (f32, f32),
    /// Move points left this turn; refilled to `max_movement` when the owner's
    /// turn starts.
    pub movement: f32,
    pub max_movement: f32,
    /// Provinces this army can still hop across this turn, when the campaign
    /// has territory attached. Refilled to `max_moves` alongside `movement`.
    /// Unused (stays at `max_moves`) on a campaign built without provinces,
    /// which keeps the continuous `movement` pool as its only move rule.
    pub moves_left: u32,
    pub max_moves: u32,
    /// `Some(city)` while the army sits inside a friendly city. Purely a state
    /// flag: a garrisoned army defends that city against sieges and cannot move
    /// until it sallies out (any move clears it).
    pub garrisoned: Option<CityId>,
    pub alive: bool,
    /// Set once `move_army` succeeds for this army this turn; cleared by
    /// `refresh_movement` when its owner's next turn starts. Each army gets
    /// exactly one move order per turn, regardless of how much of its
    /// `movement` pool that order actually spent.
    pub moved_this_turn: bool,
    /// Unit composition. No casualties/attrition model yet - these counts
    /// only feed `points()`, which decides who wins a battle.
    pub archers: u32,
    pub melee: u32,
    pub cavalry: u32,
}

/// Point value of a single unit of each type, used by `Army::points`.
pub const ARCHER_POINTS: u32 = 1;
pub const MELEE_POINTS: u32 = 2;
pub const CAVALRY_POINTS: u32 = 3;

impl Army {
    /// Total battle strength: archers*1 + melee*2 + cavalry*3. Used to decide
    /// field battles and sieges (see `resolve_field_battle`/`resolve_siege`)
    /// instead of a pure coin flip.
    pub fn points(&self) -> u32 {
        self.archers * ARCHER_POINTS + self.melee * MELEE_POINTS + self.cavalry * CAVALRY_POINTS
    }
}

/// What an army ran into at the end of a move.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BattleKind {
    /// Two armies met in the open field. The loser is destroyed.
    Field { defender_army: ArmyId },
    /// An army reached an enemy city. Won by the attacker, the city changes
    /// hands (and its garrison, if any, is destroyed); lost, the attacker is.
    Siege {
        city: CityId,
        /// The garrison that defended, if the city had one.
        defender_army: Option<ArmyId>,
    },
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct BattleReport {
    pub kind: BattleKind,
    pub attacker_army: ArmyId,
    pub attacker_faction: FactionId,
    pub defender_faction: FactionId,
    pub attacker_won: bool,
    /// True when the defending *faction* lost its last city and is out.
    pub defender_eliminated: bool,
}

/// The result of one `move_army` call: where the army actually ended up (which
/// is not necessarily where it was told to go - see `move_army`) and whatever
/// battle that arrival triggered.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct MoveReport {
    pub army_id: ArmyId,
    pub from: (f32, f32),
    pub to: (f32, f32),
    pub spent: f32,
    pub movement_left: f32,
    pub battle: Option<BattleReport>,
}

#[derive(Debug)]
pub struct Campaign {
    pub factions: Vec<Faction>,
    pub cities: Vec<City>,
    pub armies: Vec<Army>,
    /// Territory, when the campaign was built from a map package. Empty for a
    /// campaign made from bare positions.
    pub provinces: Vec<Province>,
    province_index: HashMap<ProvinceId, usize>,
    /// Half-width of the playable square; army positions are clamped to it so
    /// a random AI walk can't wander off to infinity.
    pub map_extent: f32,
    next_army_id: ArmyId,
    /// Count of individual faction-turns played so far, starting at 1 for the
    /// first faction's first turn. The game ends once this exceeds `max_turns`.
    pub turn: u32,
    /// Count of full cycles through every alive faction, starting at 1.
    /// Increments once per `end_turn()` call that wraps back around to the
    /// start of the faction order, i.e. once per "round" of play regardless
    /// of how many factions are in the game. This is what the UI should
    /// display as the round/turn number, since `turn` itself increments once
    /// per faction-turn (so with 4 factions, one player "End Turn" press —
    /// which internally calls `end_turn()` once for the player and once per
    /// AI faction — advances `turn` by 4 but `round` by exactly 1).
    pub round: u32,
    pub max_turns: u32,
    /// Index into `factions` for whoever acts next.
    pub current_faction: usize,
    pub game_over: bool,
    pub winner: Option<FactionId>,
}

impl Campaign {
    pub fn new(factions: Vec<Faction>, cities: Vec<City>, max_turns: u32) -> Self {
        let mut campaign = Self {
            factions,
            cities,
            armies: Vec::new(),
            provinces: Vec::new(),
            province_index: HashMap::new(),
            map_extent: 2048.0,
            next_army_id: 0,
            turn: 1,
            round: 1,
            max_turns,
            current_faction: 0,
            game_over: false,
            winner: None,
        };
        campaign.collect_income(campaign.current_faction_id());
        campaign.check_game_over();
        campaign
    }

    /// Sets the square half-extent army positions are clamped to.
    pub fn with_map_extent(mut self, map_extent: f32) -> Self {
        self.map_extent = map_extent;
        self
    }

    /// Attaches the territory read from a map package.
    pub fn with_provinces(mut self, provinces: Vec<Province>) -> Self {
        self.province_index = provinces
            .iter()
            .enumerate()
            .map(|(index, province)| (province.id, index))
            .collect();
        self.provinces = provinces;
        self
    }

    pub fn province(&self, id: ProvinceId) -> Option<&Province> {
        self.province_index
            .get(&id)
            .and_then(|index| self.provinces.get(*index))
    }

    fn province_mut(&mut self, id: ProvinceId) -> Option<&mut Province> {
        let index = *self.province_index.get(&id)?;
        self.provinces.get_mut(index)
    }

    pub fn provinces_owned_by(&self, faction_id: FactionId) -> Vec<&Province> {
        self.provinces
            .iter()
            .filter(|p| p.owner == Some(faction_id))
            .collect()
    }

    pub fn set_province_owner(&mut self, id: ProvinceId, owner: FactionId) {
        if let Some(province) = self.province_mut(id) {
            province.owner = Some(owner);
        }
    }

    pub fn neighbors_of(&self, id: ProvinceId) -> Vec<&Province> {
        match self.province(id) {
            Some(province) => province
                .neighbors
                .iter()
                .filter_map(|neighbor| self.province(*neighbor))
                .collect(),
            None => Vec::new(),
        }
    }

    pub fn province_tag(&self, id: ProvinceId, name: &str) -> Option<&TagValue> {
        self.province(id).and_then(|p| p.tag(name))
    }

    pub fn province_nearest_to(&self, position: (f32, f32)) -> Option<&Province> {
        self.provinces.iter().min_by(|a, b| {
            distance(a.centroid, position)
                .partial_cmp(&distance(b.centroid, position))
                .unwrap_or(std::cmp::Ordering::Equal)
        })
    }

    /// Whichever province a city stands in changes hands with it. Called from
    /// every path that transfers a city, so the political map and the province
    /// table can't drift apart.
    fn capture_province_of(&mut self, city_id: CityId, owner: FactionId) {
        let province = self
            .cities
            .iter()
            .find(|c| c.id == city_id)
            .and_then(|c| c.province);
        if let Some(province_id) = province {
            self.set_province_owner(province_id, owner);
        }
    }

    pub fn current_faction_id(&self) -> FactionId {
        self.factions[self.current_faction].id
    }

    pub fn faction(&self, id: FactionId) -> Option<&Faction> {
        self.factions.iter().find(|f| f.id == id)
    }

    fn faction_mut(&mut self, id: FactionId) -> Option<&mut Faction> {
        self.factions.iter_mut().find(|f| f.id == id)
    }

    pub fn cities_owned_by(&self, faction_id: FactionId) -> Vec<&City> {
        self.cities
            .iter()
            .filter(|c| c.owner == faction_id)
            .collect()
    }

    fn city_mut(&mut self, id: CityId) -> Option<&mut City> {
        self.cities.iter_mut().find(|c| c.id == id)
    }

    fn alive_count(&self) -> usize {
        self.factions.iter().filter(|f| f.alive).count()
    }

    fn collect_income(&mut self, faction_id: FactionId) {
        let income: i32 = self
            .cities_owned_by(faction_id)
            .iter()
            .map(|c| c.income)
            .sum();
        if let Some(f) = self.faction_mut(faction_id) {
            f.money += income;
        }
    }

    /// Resolves a 50/50 battle for `target_city_id`, attacked by `attacker_id`.
    /// The winner takes the city; if the defender is left with no cities, it is eliminated.
    pub fn attack(
        &mut self,
        attacker_id: FactionId,
        target_city_id: CityId,
        rng: &mut impl Rng,
    ) -> Result<AttackOutcome, String> {
        if self.game_over {
            return Err("game is already over".into());
        }
        if self.current_faction_id() != attacker_id {
            return Err("it is not this faction's turn".into());
        }
        let defender_id = self
            .cities
            .iter()
            .find(|c| c.id == target_city_id)
            .map(|c| c.owner)
            .ok_or_else(|| "no such city".to_string())?;
        if defender_id == attacker_id {
            return Err("cannot attack your own city".into());
        }
        match self.faction(attacker_id) {
            Some(f) if f.alive => {}
            _ => return Err("attacking faction is not alive".into()),
        }

        let attacker_won = rng.gen_bool(0.5);
        let mut defender_eliminated = false;

        if attacker_won {
            if let Some(city) = self.city_mut(target_city_id) {
                city.owner = attacker_id;
            }
            self.capture_province_of(target_city_id, attacker_id);
            if self.cities_owned_by(defender_id).is_empty() {
                if let Some(f) = self.faction_mut(defender_id) {
                    f.alive = false;
                }
                defender_eliminated = true;
            }
        }

        self.check_game_over();

        Ok(AttackOutcome {
            attacker_won,
            defender_eliminated,
        })
    }

    // -----------------------------------------------------------------------
    // Armies
    // -----------------------------------------------------------------------

    /// Adds an army for `owner` at `position` with a full pool of move points.
    /// If the position is inside one of that faction's own cities the army
    /// starts garrisoned there.
    pub fn spawn_army(&mut self, owner: FactionId, name: String, position: (f32, f32)) -> ArmyId {
        let id = self.next_army_id;
        self.next_army_id += 1;
        let garrisoned = self
            .cities
            .iter()
            .find(|c| c.owner == owner && distance(c.position, position) <= CITY_RADIUS)
            .map(|c| c.id);
        self.armies.push(Army {
            id,
            name,
            owner,
            position: self.clamp_to_map(position),
            movement: DEFAULT_MOVE_POINTS,
            max_movement: DEFAULT_MOVE_POINTS,
            moves_left: DEFAULT_MOVES,
            max_moves: DEFAULT_MOVES,
            garrisoned,
            alive: true,
            moved_this_turn: false,
            archers: 0,
            melee: 0,
            cavalry: 0,
        });
        id
    }

    /// Recruits a small, randomly-split batch of units (1-3 total) into the
    /// friendly army currently garrisoned at `city_id`. Fails if no friendly
    /// army is garrisoned there. Doesn't need balancing or cost - just a
    /// scaffold for a future recruitment system.
    pub fn recruit_at_city(&mut self, city_id: CityId, rng: &mut impl Rng) -> Result<(), String> {
        let army_id = self
            .garrison_of(city_id)
            .map(|a| a.id)
            .ok_or_else(|| "no friendly army garrisoned at that city".to_string())?;
        let total = rng.gen_range(1..=3u32);
        let army = self
            .army_mut(army_id)
            .expect("garrison_of returned a living army");
        for _ in 0..total {
            match rng.gen_range(0..3u32) {
                0 => army.archers += 1,
                1 => army.melee += 1,
                _ => army.cavalry += 1,
            }
        }
        Ok(())
    }

    pub fn army(&self, id: ArmyId) -> Option<&Army> {
        self.armies.iter().find(|a| a.id == id && a.alive)
    }

    fn army_mut(&mut self, id: ArmyId) -> Option<&mut Army> {
        self.armies.iter_mut().find(|a| a.id == id && a.alive)
    }

    pub fn armies_owned_by(&self, faction_id: FactionId) -> Vec<&Army> {
        self.armies
            .iter()
            .filter(|a| a.alive && a.owner == faction_id)
            .collect()
    }

    /// The living army garrisoning `city_id`, if any.
    pub fn garrison_of(&self, city_id: CityId) -> Option<&Army> {
        self.armies
            .iter()
            .find(|a| a.alive && a.garrisoned == Some(city_id))
    }

    fn clamp_to_map(&self, p: (f32, f32)) -> (f32, f32) {
        let e = self.map_extent;
        (p.0.clamp(-e, e), p.1.clamp(-e, e))
    }

    /// Orders `army_id` toward `target`. Behaviour depends on whether the
    /// campaign has provinces attached (`Campaign::with_provinces`):
    ///
    /// - **With provinces** (the real game): this is a discrete hop. The
    ///   order is rejected unless `target` lands in the army's current
    ///   province or one of its direct neighbors (`Province::borders`), and
    ///   costs exactly one of the army's `moves_left` regardless of the
    ///   distance actually covered - the army lands on the target province's
    ///   city (or its centroid, if it has none) rather than stopping
    ///   partway. `moves_left` lets an army spend more than one hop in a
    ///   turn, chained through separate orders.
    /// - **Without provinces** (bare-position campaigns, used by tests): the
    ///   old continuous rule - one move point spent per world unit of
    ///   straight-line distance, terrain ignored, and exactly one order
    ///   allowed per turn (`moved_this_turn`). An order past the remaining
    ///   pool travels as far as it can along that heading rather than
    ///   failing outright.
    ///
    /// Either way, arriving next to an enemy army or on an enemy city
    /// resolves a battle immediately (see `resolve_arrival`).
    pub fn move_army(
        &mut self,
        army_id: ArmyId,
        target: (f32, f32),
        rng: &mut impl Rng,
    ) -> Result<MoveReport, String> {
        if self.game_over {
            return Err("game is already over".into());
        }
        let (owner, from) = {
            let army = self
                .army(army_id)
                .ok_or_else(|| "no such army".to_string())?;
            (army.owner, army.position)
        };
        if owner != self.current_faction_id() {
            return Err("it is not this faction's turn".into());
        }

        let target = self.clamp_to_map(target);
        if distance(from, target) <= f32::EPSILON {
            return Err("army is already there".into());
        }

        if self.provinces.is_empty() {
            self.move_army_continuous(army_id, from, target, rng)
        } else {
            self.move_army_by_province(army_id, from, target, rng)
        }
    }

    /// The old free-roam rule, kept for campaigns with no territory attached.
    fn move_army_continuous(
        &mut self,
        army_id: ArmyId,
        from: (f32, f32),
        target: (f32, f32),
        rng: &mut impl Rng,
    ) -> Result<MoveReport, String> {
        let (movement, moved_this_turn) = {
            let army = self.army(army_id).expect("army checked by move_army");
            (army.movement, army.moved_this_turn)
        };
        if moved_this_turn {
            return Err("army has already moved this turn".into());
        }
        if movement <= 0.0 {
            return Err("army has no move points left".into());
        }

        let requested = distance(from, target);
        let spent = requested.min(movement);
        let t = spent / requested;
        let to = (
            from.0 + (target.0 - from.0) * t,
            from.1 + (target.1 - from.1) * t,
        );

        {
            let army = self.army_mut(army_id).expect("army checked above");
            army.position = to;
            army.movement = (army.movement - spent).max(0.0);
            army.moved_this_turn = true;
            // Any movement leaves the city walls behind; re-garrisoning is an
            // explicit order (`garrison_army`) once the army has arrived.
            army.garrisoned = None;
        }

        let battle = self.resolve_arrival(army_id, rng);
        let movement_left = self.army(army_id).map_or(0.0, |a| a.movement);

        Ok(MoveReport {
            army_id,
            from,
            to,
            spent,
            movement_left,
            battle,
        })
    }

    /// The discrete province-hop rule used once a campaign has territory
    /// attached. One order = one hop onto the current-or-neighboring
    /// province, landing on that province's city (or its centroid, if it has
    /// none) regardless of where in it `target` actually pointed - clicking
    /// anywhere on a reachable province marches the army all the way there.
    fn move_army_by_province(
        &mut self,
        army_id: ArmyId,
        from: (f32, f32),
        target: (f32, f32),
        rng: &mut impl Rng,
    ) -> Result<MoveReport, String> {
        let moves_left = self
            .army(army_id)
            .expect("army checked by move_army")
            .moves_left;
        if moves_left == 0 {
            return Err("army has no moves left".into());
        }

        let from_id = self
            .province_nearest_to(from)
            .map(|p| p.id)
            .ok_or_else(|| "army is not standing in any province".to_string())?;
        let to_id = self
            .province_nearest_to(target)
            .map(|p| p.id)
            .ok_or_else(|| "target is not in any province".to_string())?;
        let adjacent = from_id == to_id || self.province(from_id).is_some_and(|p| p.borders(to_id));
        if !adjacent {
            return Err("target province is not adjacent to the army's current province".into());
        }

        let to = self.province_landing_point(to_id).unwrap_or(target);

        {
            let army = self.army_mut(army_id).expect("army checked above");
            army.position = to;
            army.moves_left -= 1;
            // Any movement leaves the city walls behind; re-garrisoning is an
            // explicit order (`garrison_army`) once the army has arrived.
            army.garrisoned = None;
        }

        let battle = self.resolve_arrival(army_id, rng);
        let moves_left = self.army(army_id).map_or(0, |a| a.moves_left);

        Ok(MoveReport {
            army_id,
            from,
            to,
            spent: distance(from, to),
            movement_left: moves_left as f32,
            battle,
        })
    }

    /// Where an army lands when it hops into `province_id`: that province's
    /// city if it has one, else its centroid.
    fn province_landing_point(&self, province_id: ProvinceId) -> Option<(f32, f32)> {
        self.cities
            .iter()
            .find(|c| c.province == Some(province_id))
            .map(|c| c.position)
            .or_else(|| self.province(province_id).map(|p| p.centroid))
    }

    /// Every province `army_id` could hop into with its remaining moves, up
    /// to `moves_left` hops deep through the neighbor graph (breadth-first,
    /// so a 2-hop budget reaches neighbors-of-neighbors too). Empty for a
    /// campaign with no provinces attached, an unknown army, or an army with
    /// no moves left. Used purely for highlighting - `move_army` is the
    /// source of truth for what a click actually resolves to.
    pub fn reachable_provinces(&self, army_id: ArmyId) -> Vec<ProvinceId> {
        let Some(army) = self.army(army_id) else {
            return Vec::new();
        };
        if army.moves_left == 0 {
            return Vec::new();
        }
        let Some(start) = self.province_nearest_to(army.position).map(|p| p.id) else {
            return Vec::new();
        };

        let mut visited: Vec<ProvinceId> = vec![start];
        let mut frontier: Vec<ProvinceId> = vec![start];
        for _ in 0..army.moves_left {
            let mut next_frontier = Vec::new();
            for id in &frontier {
                for neighbor in self.neighbors_of(*id) {
                    if !visited.contains(&neighbor.id) {
                        visited.push(neighbor.id);
                        next_frontier.push(neighbor.id);
                    }
                }
            }
            frontier = next_frontier;
        }
        visited.retain(|id| *id != start);
        visited
    }

    /// Garrisons `army_id` in whichever friendly city it is standing in.
    /// Returns the city it entered.
    pub fn garrison_army(&mut self, army_id: ArmyId) -> Result<CityId, String> {
        let (owner, position) = {
            let army = self
                .army(army_id)
                .ok_or_else(|| "no such army".to_string())?;
            (army.owner, army.position)
        };
        let city_id = self
            .cities
            .iter()
            .filter(|c| c.owner == owner && distance(c.position, position) <= CITY_RADIUS)
            .min_by(|a, b| {
                distance(a.position, position)
                    .partial_cmp(&distance(b.position, position))
                    .expect("distances are finite")
            })
            .map(|c| c.id)
            .ok_or_else(|| "army is not standing in a friendly city".to_string())?;
        if self.garrison_of(city_id).is_some() {
            return Err("that city already has a garrison".into());
        }
        // Snap to the city centre so a garrisoned army reads as "inside".
        let centre = self
            .cities
            .iter()
            .find(|c| c.id == city_id)
            .map(|c| c.position)
            .expect("city found above");
        if let Some(army) = self.army_mut(army_id) {
            army.garrisoned = Some(city_id);
            army.position = centre;
        }
        Ok(city_id)
    }

    /// Checks what `army_id` landed on and fights the first thing it finds:
    /// an enemy city it is standing in takes priority over a nearby enemy
    /// army, since sieges are the decisive action.
    fn resolve_arrival(&mut self, army_id: ArmyId, rng: &mut impl Rng) -> Option<BattleReport> {
        let (owner, position) = {
            let army = self.army(army_id)?;
            (army.owner, army.position)
        };

        let enemy_city = self
            .cities
            .iter()
            .filter(|c| c.owner != owner && distance(c.position, position) <= CITY_RADIUS)
            .min_by(|a, b| {
                distance(a.position, position)
                    .partial_cmp(&distance(b.position, position))
                    .expect("distances are finite")
            })
            .map(|c| c.id);
        if let Some(city_id) = enemy_city {
            return self.resolve_siege(army_id, city_id, rng);
        }

        let enemy_army = self
            .armies
            .iter()
            .filter(|a| {
                a.alive && a.owner != owner && distance(a.position, position) <= ENGAGE_RADIUS
            })
            .map(|a| a.id)
            .next();
        enemy_army.map(|defender| self.resolve_field_battle(army_id, defender, rng))
    }

    /// A coin-flip field battle. The loser is destroyed outright; the winner
    /// holds the ground.
    fn resolve_field_battle(
        &mut self,
        attacker_army: ArmyId,
        defender_army: ArmyId,
        rng: &mut impl Rng,
    ) -> BattleReport {
        let attacker = self.army(attacker_army).expect("attacker alive");
        let defender = self.army(defender_army).expect("defender alive");
        let attacker_faction = attacker.owner;
        let defender_faction = defender.owner;
        let (attacker_points, defender_points) = (attacker.points(), defender.points());
        let attacker_won = match attacker_points.cmp(&defender_points) {
            std::cmp::Ordering::Greater => true,
            std::cmp::Ordering::Less => false,
            std::cmp::Ordering::Equal => rng.gen_bool(0.5),
        };

        let loser = if attacker_won {
            defender_army
        } else {
            attacker_army
        };
        if let Some(a) = self.army_mut(loser) {
            a.alive = false;
            a.garrisoned = None;
        }

        self.check_game_over();
        BattleReport {
            kind: BattleKind::Field { defender_army },
            attacker_army,
            attacker_faction,
            defender_faction,
            attacker_won,
            // A field loss never eliminates a faction on its own - holding
            // cities is what keeps a faction alive.
            defender_eliminated: false,
        }
    }

    /// A coin-flip siege. On a win the city changes hands and its garrison is
    /// destroyed; on a loss the attacking army is destroyed.
    fn resolve_siege(
        &mut self,
        attacker_army: ArmyId,
        city_id: CityId,
        rng: &mut impl Rng,
    ) -> Option<BattleReport> {
        let attacker_faction = self.army(attacker_army)?.owner;
        let defender_faction = self.cities.iter().find(|c| c.id == city_id)?.owner;
        let defender_army = self.garrison_of(city_id).map(|a| a.id);

        // Only compare points when there's an actual defending army to
        // compare against; an ungarrisoned city has no defender points, so
        // keep the coin flip in that case (existing behavior).
        let attacker_won = match defender_army.and_then(|id| self.army(id)) {
            Some(defender) => {
                let attacker_points = self.army(attacker_army)?.points();
                let defender_points = defender.points();
                match attacker_points.cmp(&defender_points) {
                    std::cmp::Ordering::Greater => true,
                    std::cmp::Ordering::Less => false,
                    std::cmp::Ordering::Equal => rng.gen_bool(0.5),
                }
            }
            None => rng.gen_bool(0.5),
        };
        let mut defender_eliminated = false;

        if attacker_won {
            if let Some(garrison) = defender_army {
                if let Some(a) = self.army_mut(garrison) {
                    a.alive = false;
                    a.garrisoned = None;
                }
            }
            if let Some(city) = self.city_mut(city_id) {
                city.owner = attacker_faction;
            }
            self.capture_province_of(city_id, attacker_faction);
            if self.cities_owned_by(defender_faction).is_empty() {
                if let Some(f) = self.faction_mut(defender_faction) {
                    f.alive = false;
                }
                defender_eliminated = true;
                // A faction with no cities is out of the game, so its armies
                // disband rather than lingering as unownable pieces.
                for a in self
                    .armies
                    .iter_mut()
                    .filter(|a| a.owner == defender_faction)
                {
                    a.alive = false;
                    a.garrisoned = None;
                }
            }
        } else if let Some(a) = self.army_mut(attacker_army) {
            a.alive = false;
            a.garrisoned = None;
        }

        self.check_game_over();
        Some(BattleReport {
            kind: BattleKind::Siege {
                city: city_id,
                defender_army,
            },
            attacker_army,
            attacker_faction,
            defender_faction,
            attacker_won,
            defender_eliminated,
        })
    }

    /// Plays the current faction's armies for it. Movement is deliberately
    /// random for now: each army either charges the nearest enemy city/army it
    /// could actually reach, or wanders off randomly, decided by a coin flip
    /// - once per hop when the campaign has provinces attached (so an army
    ///   can spend its whole `moves_left` budget in one AI turn), once total
    ///   otherwise. Returns one report per successful move.
    pub fn run_ai_turn(&mut self, rng: &mut impl Rng) -> Vec<MoveReport> {
        let faction = self.current_faction_id();
        let mut reports = Vec::new();
        let ids: Vec<ArmyId> = self.armies_owned_by(faction).iter().map(|a| a.id).collect();
        let by_province = !self.provinces.is_empty();

        for id in ids {
            if self.game_over {
                break;
            }
            if by_province {
                self.ai_wander_by_province(id, faction, rng, &mut reports);
            } else {
                self.ai_wander_continuous(id, faction, rng, &mut reports);
            }
        }
        reports
    }

    /// One AI move for a bare-position campaign: charge the nearest reachable
    /// enemy, or wander a random heading, spending the whole continuous
    /// `movement` pool.
    fn ai_wander_continuous(
        &mut self,
        id: ArmyId,
        faction: FactionId,
        rng: &mut impl Rng,
        reports: &mut Vec<MoveReport>,
    ) {
        let Some(army) = self.army(id) else { return };
        let (from, range) = (army.position, army.movement);
        if range <= 0.0 {
            return;
        }

        let aggressive = rng.gen_bool(0.5);
        let target = self
            .nearest_reachable_target(faction, from, range)
            .filter(|_| aggressive)
            .unwrap_or_else(|| {
                let angle = rng.gen_range(0.0..std::f32::consts::TAU);
                (from.0 + angle.cos() * range, from.1 + angle.sin() * range)
            });

        if let Ok(report) = self.move_army(id, target, rng) {
            reports.push(report);
        }
    }

    /// Repeatedly hops `id` into a neighboring province - toward the nearest
    /// enemy-held one if aggressive and one borders it, otherwise a random
    /// neighbor - until its `moves_left` budget runs out or it has nowhere
    /// left to go.
    fn ai_wander_by_province(
        &mut self,
        id: ArmyId,
        faction: FactionId,
        rng: &mut impl Rng,
        reports: &mut Vec<MoveReport>,
    ) {
        loop {
            if self.game_over {
                break;
            }
            let Some(army) = self.army(id) else { break };
            if army.moves_left == 0 {
                break;
            }
            let Some(from_id) = self.province_nearest_to(army.position).map(|p| p.id) else {
                break;
            };
            let neighbor_ids: Vec<ProvinceId> = self
                .neighbors_of(from_id)
                .into_iter()
                .map(|p| p.id)
                .collect();
            if neighbor_ids.is_empty() {
                break;
            }

            let aggressive = rng.gen_bool(0.5);
            let enemy_held = |province_id: ProvinceId| -> bool {
                self.cities
                    .iter()
                    .any(|c| c.owner != faction && c.province == Some(province_id))
                    || self.armies.iter().any(|a| {
                        a.alive
                            && a.owner != faction
                            && self
                                .province_nearest_to(a.position)
                                .is_some_and(|p| p.id == province_id)
                    })
            };
            let target_id = neighbor_ids
                .iter()
                .copied()
                .find(|id| enemy_held(*id))
                .filter(|_| aggressive)
                .unwrap_or(neighbor_ids[rng.gen_range(0..neighbor_ids.len())]);

            let Some(target) = self.province_landing_point(target_id) else {
                break;
            };

            match self.move_army(id, target, rng) {
                Ok(report) => reports.push(report),
                Err(_) => break,
            }
        }
    }

    /// Closest enemy city or army within `range` of `from`, as a move target.
    fn nearest_reachable_target(
        &self,
        faction: FactionId,
        from: (f32, f32),
        range: f32,
    ) -> Option<(f32, f32)> {
        let cities = self
            .cities
            .iter()
            .filter(|c| c.owner != faction)
            .map(|c| c.position);
        let armies = self
            .armies
            .iter()
            .filter(|a| a.alive && a.owner != faction)
            .map(|a| a.position);
        cities
            .chain(armies)
            .filter(|p| distance(from, *p) <= range)
            .min_by(|a, b| {
                distance(from, *a)
                    .partial_cmp(&distance(from, *b))
                    .expect("distances are finite")
            })
    }

    /// Refills move points for every living army of `faction_id`.
    fn refresh_movement(&mut self, faction_id: FactionId) {
        for army in self
            .armies
            .iter_mut()
            .filter(|a| a.alive && a.owner == faction_id)
        {
            army.movement = army.max_movement;
            army.moved_this_turn = false;
            army.moves_left = army.max_moves;
        }
    }

    /// Advances to the next alive faction's turn, crediting its city income,
    /// and counts one more faction-turn against `max_turns`.
    pub fn end_turn(&mut self) {
        if self.game_over {
            return;
        }
        self.turn += 1;
        let n = self.factions.len();
        let start = self.current_faction;
        loop {
            self.current_faction = (self.current_faction + 1) % n;
            if self.factions[self.current_faction].alive || self.current_faction == start {
                break;
            }
        }
        // A "round" completes whenever advancing wraps back around to (or
        // past) the faction we started this end_turn() from, rather than
        // strictly continuing forward through the order. With one alive
        // faction the loop always lands back on `start`, so every end_turn()
        // is correctly its own round.
        if self.current_faction <= start {
            self.round += 1;
        }
        let next = self.current_faction_id();
        self.collect_income(next);
        self.refresh_movement(next);
        self.check_game_over();
    }

    fn check_game_over(&mut self) {
        if self.game_over {
            return;
        }
        if self.alive_count() <= 1 || self.turn > self.max_turns {
            self.game_over = true;
            self.winner = self.determine_winner();
        }
    }

    /// Most cities held wins; ties break toward the lowest faction id.
    fn determine_winner(&self) -> Option<FactionId> {
        self.factions
            .iter()
            .map(|f| (f.id, self.cities_owned_by(f.id).len()))
            .max_by(|a, b| a.1.cmp(&b.1).then(b.0.cmp(&a.0)))
            .map(|(id, _)| id)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use rand::{rngs::SmallRng, SeedableRng};

    fn sample_campaign() -> Campaign {
        let factions = vec![
            Faction {
                id: 0,
                name: "Red".into(),
                money: 0,
                alive: true,
            },
            Faction {
                id: 1,
                name: "Blue".into(),
                money: 0,
                alive: true,
            },
        ];
        let cities = vec![
            City {
                id: 0,
                name: "Redhold".into(),
                income: 10,
                tier: 1,
                position: (0.0, 0.0),
                owner: 0,
                province: None,
            },
            City {
                id: 1,
                name: "Bluehold".into(),
                income: 10,
                tier: 1,
                position: (1.0, 0.0),
                owner: 1,
                province: None,
            },
        ];
        Campaign::new(factions, cities, 10)
    }

    #[test]
    fn ordinary_tax_scales_with_tier() {
        assert_eq!(ordinary_tax(0), 0);
        assert_eq!(ordinary_tax(1), ORDINARY_TAX_PER_TIER);
        assert_eq!(ordinary_tax(4), 4 * ORDINARY_TAX_PER_TIER);
        assert!(ordinary_tax(3) > ordinary_tax(2));
    }

    /// A faction's turn income is the sum of the Ordinary Tax owed by every
    /// settlement it holds, so higher-tier cities pay proportionally more.
    #[test]
    fn faction_income_sums_ordinary_tax_across_cities() {
        let factions = vec![Faction {
            id: 0,
            name: "Red".into(),
            money: 0,
            alive: true,
        }];
        let cities = vec![
            City {
                id: 0,
                name: "Big".into(),
                income: ordinary_tax(4),
                tier: 4,
                position: (0.0, 0.0),
                owner: 0,
                province: None,
            },
            City {
                id: 1,
                name: "Small".into(),
                income: ordinary_tax(1),
                tier: 1,
                position: (100.0, 0.0),
                owner: 0,
                province: None,
            },
        ];
        // Campaign::new credits the starting faction's income once.
        let c = Campaign::new(factions, cities, 10);
        assert_eq!(
            c.faction(0).unwrap().money,
            ordinary_tax(4) + ordinary_tax(1)
        );
    }

    /// Ending a turn credits the next faction its Ordinary Tax, and doing so
    /// again a full cycle later credits it a second time - i.e. the tax is a
    /// recurring per-turn income, not a one-off at game start.
    #[test]
    fn ending_a_turn_credits_the_next_factions_ordinary_tax() {
        let factions = vec![
            Faction {
                id: 0,
                name: "Red".into(),
                money: 0,
                alive: true,
            },
            Faction {
                id: 1,
                name: "Blue".into(),
                money: 0,
                alive: true,
            },
        ];
        let cities = vec![
            City {
                id: 0,
                name: "Redhold".into(),
                income: ordinary_tax(2),
                tier: 2,
                position: (0.0, 0.0),
                owner: 0,
                province: None,
            },
            City {
                id: 1,
                name: "Bluehold".into(),
                income: ordinary_tax(3),
                tier: 3,
                position: (1.0, 0.0),
                owner: 1,
                province: None,
            },
        ];
        let mut c = Campaign::new(factions, cities, 100);
        // Red credited once at game start.
        assert_eq!(c.faction(0).unwrap().money, ordinary_tax(2));
        c.end_turn(); // Blue's turn: credit Blue.
        assert_eq!(c.faction(1).unwrap().money, ordinary_tax(3));
        c.end_turn(); // Back to Red: credit Red a second time.
        assert_eq!(c.faction(0).unwrap().money, ordinary_tax(2) * 2);
    }

    #[test]
    fn new_game_credits_first_faction_income() {
        let c = sample_campaign();
        assert_eq!(c.faction(0).unwrap().money, 10);
        assert_eq!(c.faction(1).unwrap().money, 0);
    }

    #[test]
    fn end_turn_cycles_and_credits_income() {
        let mut c = sample_campaign();
        c.end_turn();
        assert_eq!(c.current_faction_id(), 1);
        assert_eq!(c.faction(1).unwrap().money, 10);
        assert_eq!(c.turn, 2);
        c.end_turn();
        assert_eq!(c.current_faction_id(), 0);
        assert_eq!(c.turn, 3);
    }

    /// Every alive faction must get exactly one turn per round, in a stable
    /// rotation, and a faction that dies mid-round must be skipped on all
    /// subsequent rounds without desyncing the rotation for anyone else.
    #[test]
    fn end_turn_visits_every_alive_faction_exactly_once_per_round() {
        let factions = vec![
            Faction {
                id: 0,
                name: "Red".into(),
                money: 0,
                alive: true,
            },
            Faction {
                id: 1,
                name: "Blue".into(),
                money: 0,
                alive: true,
            },
            Faction {
                id: 2,
                name: "Green".into(),
                money: 0,
                alive: true,
            },
        ];
        let cities = vec![
            City {
                id: 0,
                name: "Redhold".into(),
                income: 10,
                tier: 1,
                position: (0.0, 0.0),
                owner: 0,
                province: None,
            },
            City {
                id: 1,
                name: "Bluehold".into(),
                income: 10,
                tier: 1,
                position: (1.0, 0.0),
                owner: 1,
                province: None,
            },
            City {
                id: 2,
                name: "Greenhold".into(),
                income: 10,
                tier: 1,
                position: (2.0, 0.0),
                owner: 2,
                province: None,
            },
        ];
        let mut c = Campaign::new(factions, cities, 100);

        // First round: current_faction starts at 0 (Red); three end_turn
        // calls should visit Blue, Green, then wrap back to Red -- every
        // faction id appearing in current_faction exactly once.
        let mut seen = Vec::new();
        for _ in 0..3 {
            c.end_turn();
            seen.push(c.current_faction_id());
        }
        seen.sort();
        assert_eq!(seen, vec![0, 1, 2]);
        assert_eq!(c.current_faction_id(), 0);

        // Eliminate Blue mid-round: strip its only city (mirrors what
        // resolve_siege does) and mark it dead directly.
        c.cities.iter_mut().find(|city| city.id == 1).unwrap().owner = 0;
        c.factions[1].alive = false;

        // From here, the rotation must skip Blue forever: Red -> Green ->
        // Red -> Green ..., never landing on the dead faction, and never
        // getting stuck re-selecting Red every call (which would silently
        // deny Green its turn).
        for _ in 0..6 {
            c.end_turn();
            let current = c.current_faction_id();
            assert_ne!(current, 1, "dead faction must never be selected");
        }
        let mut round_trip = Vec::new();
        for _ in 0..2 {
            c.end_turn();
            round_trip.push(c.current_faction_id());
        }
        round_trip.sort();
        assert_eq!(
            round_trip,
            vec![0, 2],
            "post-elimination rotation must still cycle through both remaining alive factions"
        );
    }

    /// Regression for a GDScript-side hang: `campaign_ui.gd::_run_ai_factions`
    /// loops `while current_faction_id() != PLAYER_FACTION`, relying on the
    /// round-robin eventually landing back on faction 0. But a dead faction's
    /// index is *permanently* skipped (see end_turn), and `game_over` only
    /// fires once at most one faction remains alive - so if faction 0 (the
    /// player) is eliminated while 2+ other factions are still alive,
    /// `current_faction_id()` can never equal 0 again and `game_over` never
    /// becomes true. This asserts the two facts that combine into that trap,
    /// so a future change to either one gets caught here instead of only
    /// surfacing as a frozen GDScript loop: the round-robin must permanently
    /// exclude a dead faction 0, and the campaign must NOT consider itself
    /// over while 2+ factions remain alive, even if one of them was the
    /// "first" faction.
    #[test]
    fn end_turn_never_revisits_an_eliminated_faction_even_when_others_remain_alive() {
        let mut c = end_turn_test_campaign();

        // Eliminate faction 0 (mirrors what resolve_siege does): strip its
        // city and mark it dead directly.
        c.cities.iter_mut().find(|city| city.id == 0).unwrap().owner = 1;
        c.factions[0].alive = false;

        for _ in 0..10 {
            c.end_turn();
            assert_ne!(
                c.current_faction_id(),
                0,
                "an eliminated faction's index must never be selected again"
            );
        }

        // Two factions (1 and 2) are still alive, so the campaign must not
        // be over - if this were ever true instead, is_game_over() would be
        // the thing that saves the GDScript loop from spinning forever.
        assert!(
            !c.game_over,
            "campaign must not end just because a non-last faction (e.g. the player) died"
        );
    }

    fn end_turn_test_campaign() -> Campaign {
        let factions = vec![
            Faction {
                id: 0,
                name: "Red".into(),
                money: 0,
                alive: true,
            },
            Faction {
                id: 1,
                name: "Blue".into(),
                money: 0,
                alive: true,
            },
            Faction {
                id: 2,
                name: "Green".into(),
                money: 0,
                alive: true,
            },
        ];
        let cities = vec![
            City {
                id: 0,
                name: "Redhold".into(),
                income: 10,
                tier: 1,
                position: (0.0, 0.0),
                owner: 0,
                province: None,
            },
            City {
                id: 1,
                name: "Bluehold".into(),
                income: 10,
                tier: 1,
                position: (1.0, 0.0),
                owner: 1,
                province: None,
            },
            City {
                id: 2,
                name: "Greenhold".into(),
                income: 10,
                tier: 1,
                position: (2.0, 0.0),
                owner: 2,
                province: None,
            },
        ];
        Campaign::new(factions, cities, 1000)
    }

    #[test]
    fn attack_transfers_city_on_win() {
        let mut c = sample_campaign();
        let mut rng = SmallRng::seed_from_u64(1); // first gen_bool(0.5) call is true for this seed
        let outcome = c.attack(0, 1, &mut rng).unwrap();
        if outcome.attacker_won {
            assert_eq!(c.cities.iter().find(|city| city.id == 1).unwrap().owner, 0);
        } else {
            assert_eq!(c.cities.iter().find(|city| city.id == 1).unwrap().owner, 1);
        }
    }

    #[test]
    fn losing_last_city_eliminates_faction() {
        // Force a win by retrying with different seeds until we observe an attacker win.
        let mut seed = 0u64;
        loop {
            let mut rng = SmallRng::seed_from_u64(seed);
            let mut trial = sample_campaign();
            let outcome = trial.attack(0, 1, &mut rng).unwrap();
            if outcome.attacker_won {
                assert!(outcome.defender_eliminated);
                assert!(!trial.faction(1).unwrap().alive);
                assert_eq!(trial.alive_count(), 1);
                assert!(trial.game_over);
                assert_eq!(trial.winner, Some(0));
                break;
            }
            seed += 1;
            assert!(seed < 100, "no attacker win observed in 100 seeds");
        }
    }

    #[test]
    fn cannot_attack_own_city() {
        let mut c = sample_campaign();
        let mut rng = SmallRng::seed_from_u64(1);
        assert!(c.attack(0, 0, &mut rng).is_err());
    }

    #[test]
    fn cannot_act_out_of_turn() {
        let mut c = sample_campaign();
        let mut rng = SmallRng::seed_from_u64(1);
        assert!(c.attack(1, 0, &mut rng).is_err());
    }

    #[test]
    fn game_ends_after_max_turns_with_most_cities_winning() {
        let factions = vec![
            Faction {
                id: 0,
                name: "Red".into(),
                money: 0,
                alive: true,
            },
            Faction {
                id: 1,
                name: "Blue".into(),
                money: 0,
                alive: true,
            },
        ];
        let cities = vec![
            City {
                id: 0,
                name: "A".into(),
                income: 5,
                tier: 1,
                position: (0.0, 0.0),
                owner: 0,
                province: None,
            },
            City {
                id: 1,
                name: "B".into(),
                income: 5,
                tier: 1,
                position: (1.0, 0.0),
                owner: 0,
                province: None,
            },
            City {
                id: 2,
                name: "C".into(),
                income: 5,
                tier: 1,
                position: (2.0, 0.0),
                owner: 1,
                province: None,
            },
        ];
        let mut c = Campaign::new(factions, cities, 1);
        assert!(!c.game_over);
        c.end_turn(); // turn -> 2, exceeds max_turns(1)
        assert!(c.game_over);
        assert_eq!(c.winner, Some(0));
    }

    /// A campaign with two far-apart cities and one army each, both garrisoned.
    fn army_campaign() -> Campaign {
        let mut c = sample_campaign_with_positions((0.0, 0.0), (1000.0, 0.0));
        c.spawn_army(0, "Red Host".into(), (0.0, 0.0));
        c.spawn_army(1, "Blue Host".into(), (1000.0, 0.0));
        c
    }

    fn sample_campaign_with_positions(a: (f32, f32), b: (f32, f32)) -> Campaign {
        let factions = vec![
            Faction {
                id: 0,
                name: "Red".into(),
                money: 0,
                alive: true,
            },
            Faction {
                id: 1,
                name: "Blue".into(),
                money: 0,
                alive: true,
            },
        ];
        let cities = vec![
            City {
                id: 0,
                name: "Redhold".into(),
                income: 10,
                tier: 1,
                position: a,
                owner: 0,
                province: None,
            },
            City {
                id: 1,
                name: "Bluehold".into(),
                income: 10,
                tier: 1,
                position: b,
                owner: 1,
                province: None,
            },
        ];
        Campaign::new(factions, cities, 100).with_map_extent(4000.0)
    }

    #[test]
    fn spawned_army_starts_garrisoned_with_full_movement() {
        let c = army_campaign();
        let army = c.army(0).unwrap();
        assert_eq!(army.garrisoned, Some(0));
        assert_eq!(army.movement, DEFAULT_MOVE_POINTS);
        assert_eq!(c.garrison_of(0).map(|a| a.id), Some(0));
    }

    #[test]
    fn moving_within_range_spends_exactly_the_distance() {
        let mut c = army_campaign();
        let mut rng = SmallRng::seed_from_u64(1);
        let report = c.move_army(0, (100.0, 0.0), &mut rng).unwrap();
        assert_eq!(report.spent, 100.0);
        assert_eq!(report.to, (100.0, 0.0));
        assert_eq!(report.movement_left, DEFAULT_MOVE_POINTS - 100.0);
        assert!(report.battle.is_none());
        // Leaving the walls drops the garrison flag.
        assert_eq!(c.army(0).unwrap().garrisoned, None);
        assert!(c.garrison_of(0).is_none());
    }

    #[test]
    fn moving_beyond_range_stops_short_along_the_same_heading() {
        let mut c = army_campaign();
        let mut rng = SmallRng::seed_from_u64(1);
        // Bluehold is 1000 away but the army only has DEFAULT_MOVE_POINTS.
        let report = c.move_army(0, (1000.0, 0.0), &mut rng).unwrap();
        assert_eq!(report.spent, DEFAULT_MOVE_POINTS);
        assert_eq!(report.to, (DEFAULT_MOVE_POINTS, 0.0));
        assert_eq!(report.movement_left, 0.0);
        assert!(report.battle.is_none());
        // Pool is empty, so a second order this turn is refused.
        assert!(c.move_army(0, (500.0, 0.0), &mut rng).is_err());
    }

    #[test]
    fn end_turn_refills_move_points() {
        let mut c = army_campaign();
        let mut rng = SmallRng::seed_from_u64(1);
        c.move_army(0, (100.0, 0.0), &mut rng).unwrap();
        assert!(c.army(0).unwrap().movement < DEFAULT_MOVE_POINTS);
        c.end_turn(); // Blue's turn
        c.end_turn(); // back to Red
        assert_eq!(c.army(0).unwrap().movement, DEFAULT_MOVE_POINTS);
    }

    #[test]
    fn cannot_move_another_factions_army() {
        let mut c = army_campaign();
        let mut rng = SmallRng::seed_from_u64(1);
        // Army 1 is Blue's, and it is Red's turn.
        assert!(c.move_army(1, (900.0, 0.0), &mut rng).is_err());
    }

    #[test]
    fn positions_are_clamped_to_the_map() {
        let mut c = army_campaign();
        let mut rng = SmallRng::seed_from_u64(1);
        c.move_army(0, (99999.0, 0.0), &mut rng).unwrap();
        assert!(c.army(0).unwrap().position.0 <= c.map_extent);
    }

    #[test]
    fn armies_that_meet_fight_a_field_battle_and_one_dies() {
        // Both armies start out of their cities and one step apart.
        let mut c = sample_campaign_with_positions((-2000.0, 0.0), (2000.0, 0.0));
        c.spawn_army(0, "Red Host".into(), (0.0, 0.0));
        c.spawn_army(1, "Blue Host".into(), (100.0, 0.0));
        let mut rng = SmallRng::seed_from_u64(7);

        let report = c.move_army(0, (100.0, 0.0), &mut rng).unwrap();
        let battle = report.battle.expect("meeting an enemy army must fight");
        assert_eq!(battle.kind, BattleKind::Field { defender_army: 1 });
        assert_eq!(battle.attacker_faction, 0);
        assert_eq!(battle.defender_faction, 1);
        // Exactly one of the two is destroyed.
        assert_eq!(c.armies.iter().filter(|a| a.alive).count(), 1);
        let survivor = if battle.attacker_won { 0 } else { 1 };
        assert!(c.army(survivor).is_some());
    }

    #[test]
    fn reaching_an_enemy_city_besieges_it() {
        // Bluehold is inside Red's army's move range, with a Blue garrison.
        let mut c = sample_campaign_with_positions((0.0, 0.0), (300.0, 0.0));
        c.spawn_army(0, "Red Host".into(), (0.0, 0.0));
        c.spawn_army(1, "Blue Garrison".into(), (300.0, 0.0));
        let mut rng = SmallRng::seed_from_u64(3);

        let report = c.move_army(0, (300.0, 0.0), &mut rng).unwrap();
        let battle = report.battle.expect("reaching an enemy city must fight");
        assert_eq!(
            battle.kind,
            BattleKind::Siege {
                city: 1,
                defender_army: Some(1),
            }
        );

        if battle.attacker_won {
            assert_eq!(c.cities.iter().find(|x| x.id == 1).unwrap().owner, 0);
            assert!(c.army(1).is_none(), "garrison dies with the city");
            assert!(battle.defender_eliminated, "Blue lost its only city");
            assert!(!c.faction(1).unwrap().alive);
        } else {
            assert_eq!(c.cities.iter().find(|x| x.id == 1).unwrap().owner, 1);
            assert!(c.army(0).is_none(), "the failed besieger is destroyed");
        }
    }

    #[test]
    fn eliminating_a_faction_disbands_its_armies() {
        // Retry seeds until the attacker wins the siege.
        for seed in 0..100u64 {
            let mut c = sample_campaign_with_positions((0.0, 0.0), (300.0, 0.0));
            c.spawn_army(0, "Red Host".into(), (0.0, 0.0));
            c.spawn_army(1, "Blue Garrison".into(), (300.0, 0.0));
            c.spawn_army(1, "Blue Field Army".into(), (-1500.0, 900.0));
            let mut rng = SmallRng::seed_from_u64(seed);
            let battle = c
                .move_army(0, (300.0, 0.0), &mut rng)
                .unwrap()
                .battle
                .unwrap();
            if battle.attacker_won {
                assert!(battle.defender_eliminated);
                assert!(c.armies_owned_by(1).is_empty(), "Blue's armies disband");
                assert_eq!(c.armies_owned_by(0).len(), 1);
                return;
            }
        }
        panic!("no attacker win observed in 100 seeds");
    }

    #[test]
    fn garrisoning_requires_standing_in_a_friendly_city() {
        let mut c = army_campaign();
        let mut rng = SmallRng::seed_from_u64(1);
        c.move_army(0, (200.0, 0.0), &mut rng).unwrap();
        assert!(
            c.garrison_army(0).is_err(),
            "out in the open, nothing to enter"
        );

        c.end_turn();
        c.end_turn();
        c.move_army(0, (0.0, 0.0), &mut rng).unwrap();
        assert_eq!(c.garrison_army(0).unwrap(), 0);
        assert_eq!(c.army(0).unwrap().garrisoned, Some(0));
        // Snapped to the city centre.
        assert_eq!(c.army(0).unwrap().position, (0.0, 0.0));
    }

    #[test]
    fn ai_turn_moves_every_army_it_owns() {
        let mut c = sample_campaign_with_positions((-1500.0, 0.0), (1500.0, 0.0));
        c.spawn_army(0, "A".into(), (-1500.0, 0.0));
        c.spawn_army(0, "B".into(), (-1400.0, 300.0));
        c.spawn_army(1, "C".into(), (1500.0, 0.0));
        let before: Vec<(f32, f32)> = c.armies.iter().map(|a| a.position).collect();

        let mut rng = SmallRng::seed_from_u64(11);
        let reports = c.run_ai_turn(&mut rng);

        assert_eq!(
            reports.len(),
            2,
            "both of Red's armies act, Blue's does not"
        );
        for r in &reports {
            assert!(r.spent > 0.0);
            assert_eq!(r.movement_left, 0.0, "a random walk uses the whole pool");
        }
        // Blue's army is untouched.
        assert_eq!(c.army(2).unwrap().position, before[2]);
    }

    #[test]
    fn ai_stays_inside_the_map_over_many_turns() {
        let mut c = sample_campaign_with_positions((0.0, 0.0), (1500.0, 0.0));
        c.spawn_army(0, "A".into(), (0.0, 0.0));
        let mut rng = SmallRng::seed_from_u64(5);
        for _ in 0..50 {
            if c.game_over {
                break;
            }
            c.run_ai_turn(&mut rng);
            c.end_turn();
        }
        for a in c.armies.iter().filter(|a| a.alive) {
            assert!(a.position.0.abs() <= c.map_extent);
            assert!(a.position.1.abs() <= c.map_extent);
        }
    }

    #[test]
    fn alive_count_two_survives_full_battle_loop() {
        let mut c = sample_campaign();
        let mut rng = SmallRng::seed_from_u64(2);
        let outcome = c.attack(0, 1, &mut rng);
        assert!(outcome.is_ok());
    }

    // ----------------------------------------------------------------
    // provinces
    // ----------------------------------------------------------------

    /// Battles are a coin flip, so retry seeds until the attacker wins - the
    /// same idiom the city-capture tests use.
    fn attack_until_won(campaign: &mut Campaign) {
        for seed in 0..100u64 {
            let mut rng = SmallRng::seed_from_u64(seed);
            let mut trial = province_campaign();
            if trial.attack(0, 1, &mut rng).unwrap().attacker_won {
                *campaign = trial;
                return;
            }
        }
        panic!("no attacker win observed in 100 seeds");
    }

    fn province(id: ProvinceId, owner: Option<FactionId>, neighbors: Vec<ProvinceId>) -> Province {
        Province {
            id,
            key: format!("p{id}"),
            name: format!("Province {id}"),
            centroid: (id as f32 * 100.0, 0.0),
            neighbors,
            tags: HashMap::new(),
            owner,
        }
    }

    /// Two provinces, each with the city that stands in it, one per faction.
    fn province_campaign() -> Campaign {
        let factions = vec![
            Faction {
                id: 0,
                name: "Red".into(),
                money: 0,
                alive: true,
            },
            Faction {
                id: 1,
                name: "Blue".into(),
                money: 0,
                alive: true,
            },
        ];
        let cities = vec![
            City {
                id: 0,
                name: "Redhold".into(),
                income: 10,
                tier: 1,
                position: (0.0, 0.0),
                owner: 0,
                province: Some(1),
            },
            City {
                id: 1,
                name: "Bluehold".into(),
                income: 10,
                tier: 1,
                position: (100.0, 0.0),
                owner: 1,
                province: Some(2),
            },
        ];
        Campaign::new(factions, cities, 10).with_provinces(vec![
            province(1, Some(0), vec![2]),
            province(2, Some(1), vec![1]),
        ])
    }

    #[test]
    fn provinces_are_looked_up_by_id_not_position() {
        let c = province_campaign();
        assert_eq!(c.province(2).unwrap().key, "p2");
        assert!(c.province(99).is_none());
    }

    #[test]
    fn taking_a_city_takes_its_province_with_it() {
        // The whole point of ownership being simulation state: a province
        // changes hands mid-game, with no file on disk changing.
        let mut c = province_campaign();
        assert_eq!(c.province(2).unwrap().owner, Some(1));

        attack_until_won(&mut c);

        assert_eq!(c.cities[1].owner, 0);
        assert_eq!(
            c.province(2).unwrap().owner,
            Some(0),
            "province ownership must follow its city, or the map and the \
             table disagree about who holds what"
        );
    }

    #[test]
    fn a_failed_attack_leaves_the_province_alone() {
        let mut seed = 0u64;
        loop {
            let mut rng = SmallRng::seed_from_u64(seed);
            let mut trial = province_campaign();
            if !trial.attack(0, 1, &mut rng).unwrap().attacker_won {
                assert_eq!(trial.province(2).unwrap().owner, Some(1));
                assert_eq!(trial.cities[1].owner, 1);
                break;
            }
            seed += 1;
            assert!(seed < 100, "no attacker loss observed in 100 seeds");
        }
    }

    #[test]
    fn provinces_owned_by_tracks_conquest() {
        let mut c = province_campaign();
        assert_eq!(c.provinces_owned_by(0).len(), 1);

        attack_until_won(&mut c);

        assert_eq!(c.provinces_owned_by(0).len(), 2);
        assert!(c.provinces_owned_by(1).is_empty());
    }

    #[test]
    fn neighbors_resolve_to_real_provinces() {
        let c = province_campaign();
        let neighbors = c.neighbors_of(1);
        assert_eq!(neighbors.len(), 1);
        assert_eq!(neighbors[0].id, 2);
        assert!(c.province(1).unwrap().borders(2));
        assert!(!c.province(1).unwrap().borders(99));
    }

    #[test]
    fn hopping_onto_an_adjacent_enemy_city_besieges_it() {
        // The siege itself is a coin flip (see attack_until_won), so this
        // only pins down the parts that don't depend on who wins: the click
        // snapped to Bluehold rather than stopping short of it, and the
        // arrival triggered a siege rather than free movement.
        let mut c = province_campaign();
        let army_id = c.spawn_army(0, "A".into(), (0.0, 0.0));
        let mut rng = SmallRng::seed_from_u64(1);

        // A click anywhere past Bluehold still lands exactly on it - hops
        // snap to the target province's city rather than stopping short.
        let report = c.move_army(army_id, (250.0, 40.0), &mut rng).unwrap();

        assert_eq!(report.to, (100.0, 0.0), "landed on Bluehold, not the click");
        assert!(matches!(
            report.battle.unwrap().kind,
            BattleKind::Siege { city: 1, .. }
        ));
    }

    /// A 1-2-3 chain: province 2 sits between the other two and has no city
    /// of its own, so a hop into it has to fall back to its centroid. A
    /// second, cityless faction just keeps the game from ending on the spot
    /// (alive_count <= 1 is a win condition) - these tests never touch it.
    fn chain_provinces_campaign() -> Campaign {
        let factions = vec![
            Faction {
                id: 0,
                name: "Red".into(),
                money: 0,
                alive: true,
            },
            Faction {
                id: 1,
                name: "Blue".into(),
                money: 0,
                alive: true,
            },
        ];
        let cities = vec![City {
            id: 0,
            name: "Redhold".into(),
            income: 10,
            tier: 1,
            position: (0.0, 0.0),
            owner: 0,
            province: Some(1),
        }];
        Campaign::new(factions, cities, 10).with_provinces(vec![
            province(1, Some(0), vec![2]),
            province(2, None, vec![1, 3]),
            province(3, None, vec![2]),
        ])
    }

    #[test]
    fn move_army_rejects_a_non_adjacent_province_in_one_hop() {
        let mut c = chain_provinces_campaign();
        let army_id = c.spawn_army(0, "A".into(), (0.0, 0.0));
        let mut rng = SmallRng::seed_from_u64(1);
        // Province 3 borders province 2, not province 1 - not reachable in a
        // single order even though the army has moves to spare.
        assert!(c.move_army(army_id, (300.0, 0.0), &mut rng).is_err());
    }

    #[test]
    fn move_army_chains_hops_across_the_same_turn() {
        let mut c = chain_provinces_campaign();
        let army_id = c.spawn_army(0, "A".into(), (0.0, 0.0));
        let mut rng = SmallRng::seed_from_u64(1);

        let hop1 = c.move_army(army_id, (200.0, 0.0), &mut rng).unwrap();
        assert_eq!(
            hop1.to,
            (200.0, 0.0),
            "province 2 has no city, so it lands on its centroid"
        );
        assert_eq!(c.army(army_id).unwrap().moves_left, DEFAULT_MOVES - 1);

        let hop2 = c.move_army(army_id, (300.0, 0.0), &mut rng).unwrap();
        assert_eq!(hop2.to, (300.0, 0.0));
        assert_eq!(c.army(army_id).unwrap().moves_left, 0);

        // Budget spent - a third order this turn is refused.
        assert!(c.move_army(army_id, (300.0, 0.0), &mut rng).is_err());
    }

    #[test]
    fn reachable_provinces_grows_with_moves_left() {
        let mut c = chain_provinces_campaign();
        let army_id = c.spawn_army(0, "A".into(), (0.0, 0.0));

        let reachable = c.reachable_provinces(army_id);
        assert_eq!(
            reachable.len(),
            2,
            "2 moves reaches province 2, and through it, province 3"
        );
        assert!(reachable.contains(&2));
        assert!(reachable.contains(&3));

        let mut rng = SmallRng::seed_from_u64(1);
        c.move_army(army_id, (200.0, 0.0), &mut rng).unwrap();
        let reachable = c.reachable_provinces(army_id);
        assert_eq!(
            reachable.len(),
            2,
            "standing in province 2 with 1 move left reaches provinces 1 and 3"
        );
        assert!(reachable.contains(&1));
        assert!(reachable.contains(&3));

        c.move_army(army_id, (300.0, 0.0), &mut rng).unwrap();
        assert!(
            c.reachable_provinces(army_id).is_empty(),
            "no moves left, nothing is reachable"
        );
    }

    #[test]
    fn tags_carry_whatever_a_layer_reduced_to() {
        // Rust never enumerates tag names - that is what lets a new map layer
        // land without touching this crate.
        let mut tags = HashMap::new();
        tags.insert("terrain".to_string(), TagValue::Str("mountains".into()));
        tags.insert(
            "resources".to_string(),
            TagValue::List(vec!["iron".into(), "wine".into()]),
        );
        tags.insert("supply".to_string(), TagValue::Num(2.5));

        let mut p = province(1, Some(0), vec![]);
        p.tags = tags;
        let c = Campaign::new(
            vec![Faction {
                id: 0,
                name: "Red".into(),
                money: 0,
                alive: true,
            }],
            vec![City {
                id: 0,
                name: "Redhold".into(),
                income: 10,
                tier: 1,
                position: (0.0, 0.0),
                owner: 0,
                province: Some(1),
            }],
            10,
        )
        .with_provinces(vec![p]);

        assert_eq!(
            c.province_tag(1, "terrain").and_then(|t| t.as_str()),
            Some("mountains")
        );
        assert_eq!(
            c.province_tag(1, "resources").and_then(|t| t.as_list()),
            Some(["iron".to_string(), "wine".to_string()].as_slice())
        );
        assert_eq!(
            c.province_tag(1, "supply").and_then(|t| t.as_num()),
            Some(2.5)
        );
        assert!(c.province_tag(1, "roads").is_none());
    }

    #[test]
    fn province_nearest_to_picks_by_centroid() {
        let c = province_campaign();
        assert_eq!(c.province_nearest_to((5.0, 0.0)).unwrap().id, 1);
        assert_eq!(c.province_nearest_to((180.0, 0.0)).unwrap().id, 2);
    }

    #[test]
    fn a_campaign_without_a_map_package_has_no_provinces() {
        let c = sample_campaign();
        assert!(c.provinces.is_empty());
        assert!(c.province(1).is_none());
        assert!(c.provinces_owned_by(0).is_empty());
    }

    // -------------------------------------------------------------------
    // Unit composition / points-based battles / recruitment
    // -------------------------------------------------------------------

    fn army_with(archers: u32, melee: u32, cavalry: u32) -> Army {
        Army {
            id: 0,
            name: "Test".into(),
            owner: 0,
            position: (0.0, 0.0),
            movement: DEFAULT_MOVE_POINTS,
            max_movement: DEFAULT_MOVE_POINTS,
            moves_left: DEFAULT_MOVES,
            max_moves: DEFAULT_MOVES,
            garrisoned: None,
            alive: true,
            moved_this_turn: false,
            archers,
            melee,
            cavalry,
        }
    }

    #[test]
    fn points_are_weighted_by_unit_type() {
        assert_eq!(army_with(0, 0, 0).points(), 0);
        assert_eq!(army_with(3, 0, 0).points(), 3); // 3 archers * 1
        assert_eq!(army_with(0, 3, 0).points(), 6); // 3 melee * 2
        assert_eq!(army_with(0, 0, 3).points(), 9); // 3 cavalry * 3
        assert_eq!(army_with(2, 1, 1).points(), 7); // 2 archers + 1 melee*2 + 1 cav*3
    }

    #[test]
    fn higher_points_army_wins_field_battle_deterministically() {
        // Any seed works: with unequal points the coin flip is never reached.
        for seed in 0..10u64 {
            // Both armies start out of their cities and one step apart, as in
            // `armies_that_meet_fight_a_field_battle_and_one_dies`.
            let mut trial = sample_campaign_with_positions((-2000.0, 0.0), (2000.0, 0.0));
            let attacker_id = trial.spawn_army(0, "Red Host".into(), (0.0, 0.0));
            let defender_id = trial.spawn_army(1, "Blue Host".into(), (100.0, 0.0));
            // Give the attacker overwhelming points so the outcome never
            // rolls rng - if it did, this test would be flaky across seeds.
            trial.army_mut(attacker_id).unwrap().cavalry = 10; // 30 points
            trial.army_mut(defender_id).unwrap().archers = 1; // 1 point

            let mut rng = SmallRng::seed_from_u64(seed);
            let report = trial
                .move_army(attacker_id, (100.0, 0.0), &mut rng)
                .unwrap();
            let battle = report.battle.expect("meeting an enemy army must fight");
            assert!(
                battle.attacker_won,
                "attacker has far more points and must win regardless of rng seed {seed}"
            );
            assert!(trial.army(defender_id).is_none(), "defender destroyed");
            assert!(trial.army(attacker_id).is_some(), "attacker survives");
        }
    }

    #[test]
    fn lower_points_army_loses_field_battle_deterministically() {
        let mut c = sample_campaign_with_positions((-2000.0, 0.0), (2000.0, 0.0));
        let attacker_id = c.spawn_army(0, "Red Host".into(), (0.0, 0.0));
        let defender_id = c.spawn_army(1, "Blue Host".into(), (100.0, 0.0));
        c.army_mut(attacker_id).unwrap().archers = 1; // 1 point
        c.army_mut(defender_id).unwrap().cavalry = 10; // 30 points

        let mut rng = SmallRng::seed_from_u64(42);
        let report = c.move_army(attacker_id, (100.0, 0.0), &mut rng).unwrap();
        let battle = report.battle.expect("meeting an enemy army must fight");
        assert!(!battle.attacker_won, "attacker is far weaker and must lose");
        assert!(c.army(attacker_id).is_none(), "attacker destroyed");
        assert!(c.army(defender_id).is_some(), "defender survives");
    }

    #[test]
    fn recruit_at_city_adds_units_to_the_garrison() {
        let mut c = army_campaign();
        let mut rng = SmallRng::seed_from_u64(9);
        let before = c.army(0).unwrap().points();
        c.recruit_at_city(0, &mut rng).unwrap();
        let after = c.army(0).unwrap().points();
        assert!(
            after > before,
            "recruiting must add at least one unit's worth of points"
        );
        let total_units = {
            let a = c.army(0).unwrap();
            a.archers + a.melee + a.cavalry
        };
        assert!((1..=3).contains(&total_units), "adds between 1 and 3 units");
    }

    #[test]
    fn recruit_at_city_fails_without_a_garrisoned_army() {
        let mut c = sample_campaign();
        let mut rng = SmallRng::seed_from_u64(9);
        // sample_campaign has cities but no armies at all.
        assert!(c.recruit_at_city(0, &mut rng).is_err());
    }

    #[test]
    fn recruit_at_city_fails_when_garrison_left() {
        let mut c = army_campaign();
        let mut rng = SmallRng::seed_from_u64(1);
        // March the garrison out of its city.
        c.move_army(0, (200.0, 0.0), &mut rng).unwrap();
        assert!(c.recruit_at_city(0, &mut rng).is_err());
    }
}
