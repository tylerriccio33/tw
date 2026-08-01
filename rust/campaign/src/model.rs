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
    pub income: i32,
    pub position: (f32, f32),
    pub owner: FactionId,
    /// The province this city sits in, when the campaign was built from a map
    /// package. Taking the city takes the province with it.
    pub province: Option<ProvinceId>,
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
            garrisoned,
            alive: true,
            moved_this_turn: false,
        });
        id
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

    /// Moves `army_id` toward `target`, spending one move point per world unit
    /// travelled. Terrain is deliberately ignored - every direction costs the
    /// same and the ocean is walkable.
    ///
    /// Each army gets exactly one move order per turn (`moved_this_turn`,
    /// cleared by `refresh_movement` at the start of its owner's next turn) -
    /// a second order this turn is rejected even if move points remain. When
    /// the campaign has provinces attached, the order is also rejected unless
    /// the target lands in the army's current province or one of its direct
    /// neighbors (`Province::borders`) - no multi-hop moves in a single order.
    ///
    /// If `target` is further than the army's remaining move points, the army
    /// travels as far as it can *along that heading* and stops with an empty
    /// pool rather than refusing the order. Arriving next to an enemy army or
    /// on an enemy city resolves a battle immediately (see `resolve_battle`).
    pub fn move_army(
        &mut self,
        army_id: ArmyId,
        target: (f32, f32),
        rng: &mut impl Rng,
    ) -> Result<MoveReport, String> {
        if self.game_over {
            return Err("game is already over".into());
        }
        let (owner, from, movement, moved_this_turn) = {
            let army = self
                .army(army_id)
                .ok_or_else(|| "no such army".to_string())?;
            (
                army.owner,
                army.position,
                army.movement,
                army.moved_this_turn,
            )
        };
        if owner != self.current_faction_id() {
            return Err("it is not this faction's turn".into());
        }
        if moved_this_turn {
            return Err("army has already moved this turn".into());
        }
        if movement <= 0.0 {
            return Err("army has no move points left".into());
        }

        let target = self.clamp_to_map(target);
        let requested = distance(from, target);
        if requested <= f32::EPSILON {
            return Err("army is already there".into());
        }

        if !self.provinces.is_empty() {
            let from_province = self.province_nearest_to(from).map(|p| p.id);
            let to_province = self.province_nearest_to(target).map(|p| p.id);
            if let (Some(from_id), Some(to_id)) = (from_province, to_province) {
                let adjacent =
                    from_id == to_id || self.province(from_id).is_some_and(|p| p.borders(to_id));
                if !adjacent {
                    return Err(
                        "target province is not adjacent to the army's current province".into(),
                    );
                }
            }
        }

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
        let attacker_faction = self.army(attacker_army).expect("attacker alive").owner;
        let defender_faction = self.army(defender_army).expect("defender alive").owner;
        let attacker_won = rng.gen_bool(0.5);

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

        let attacker_won = rng.gen_bool(0.5);
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
    /// could actually reach this turn, or wanders off on a random heading,
    /// decided by a coin flip. Returns one report per army that moved.
    pub fn run_ai_turn(&mut self, rng: &mut impl Rng) -> Vec<MoveReport> {
        let faction = self.current_faction_id();
        let mut reports = Vec::new();
        let ids: Vec<ArmyId> = self.armies_owned_by(faction).iter().map(|a| a.id).collect();

        for id in ids {
            if self.game_over {
                break;
            }
            let Some(army) = self.army(id) else { continue };
            let (from, range) = (army.position, army.movement);
            if range <= 0.0 {
                continue;
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
        reports
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
                position: (0.0, 0.0),
                owner: 0,
                province: None,
            },
            City {
                id: 1,
                name: "Bluehold".into(),
                income: 10,
                position: (1.0, 0.0),
                owner: 1,
                province: None,
            },
        ];
        Campaign::new(factions, cities, 10)
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
                position: (0.0, 0.0),
                owner: 0,
                province: None,
            },
            City {
                id: 1,
                name: "B".into(),
                income: 5,
                position: (1.0, 0.0),
                owner: 0,
                province: None,
            },
            City {
                id: 2,
                name: "C".into(),
                income: 5,
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
                position: a,
                owner: 0,
                province: None,
            },
            City {
                id: 1,
                name: "Bluehold".into(),
                income: 10,
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
                position: (0.0, 0.0),
                owner: 0,
                province: Some(1),
            },
            City {
                id: 1,
                name: "Bluehold".into(),
                income: 10,
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
}
