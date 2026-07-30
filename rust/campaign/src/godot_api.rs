//! GDExtension bindings: a thin `Node` wrapper around the pure [`crate::model::Campaign`]
//! that GDScript drives via `#[func]` calls and listens to via `#[signal]`s.

use godot::classes::{INode, Node};
use godot::prelude::*;
use rand::thread_rng;

use std::collections::HashMap;

use crate::model::{
    ArmyId, BattleKind, BattleReport, Campaign, City, CityId, Faction, FactionId, MoveReport,
    Province, ProvinceId, TagValue, DEFAULT_MOVE_POINTS,
};

struct CampaignExtension;

#[gdextension]
unsafe impl ExtensionLibrary for CampaignExtension {}

/// A faction as the map package describes it, before a campaign exists.
/// `key` is what the ownership layer refers to; `color` is what the UI paints
/// a province with once the simulation owns ownership.
#[derive(Debug, Clone)]
struct FactionDef {
    id: FactionId,
    key: String,
    name: String,
    color: String,
    money: i32,
}

/// A row of `provinces.table.json`, held between `load_provinces` and
/// `start_game_from_provinces`.
#[derive(Debug, Clone)]
struct ProvinceDef {
    id: ProvinceId,
    key: String,
    name: String,
    centroid: (f32, f32),
    /// Where its capital sits, from the map's `city_layer` (a `city_position`
    /// column). Falls back to the centroid for older exports that lack one.
    city_position: (f32, f32),
    neighbors: Vec<ProvinceId>,
    tags: HashMap<String, TagValue>,
    starting_owner: Option<String>,
}

fn dict_string(dict: &VarDictionary, key: &str) -> Option<String> {
    dict.get(key)
        .and_then(|v| v.try_to::<GString>().ok())
        .map(|s| s.to_string())
}

/// Godot's JSON parser has no integer type - every number in
/// provinces.table.json arrives as a float, so `"id": 1` comes back as `1.0`.
/// Accepting only i64 here silently drops every province in the table.
fn variant_to_i64(value: &Variant) -> Option<i64> {
    value
        .try_to::<i64>()
        .ok()
        .or_else(|| value.try_to::<f64>().ok().map(|n| n as i64))
}

fn dict_int(dict: &VarDictionary, key: &str) -> Option<i64> {
    dict.get(key).as_ref().and_then(variant_to_i64)
}

/// Reads one tag value without caring what layer produced it - a majority
/// reduce gives a string, an `any` reduce gives an array, and a numeric legend
/// field gives a number.
fn variant_to_tag(value: &Variant) -> Option<TagValue> {
    if let Ok(s) = value.try_to::<GString>() {
        return Some(TagValue::Str(s.to_string()));
    }
    if let Ok(array) = value.try_to::<VarArray>() {
        return Some(TagValue::List(
            array
                .iter_shared()
                .filter_map(|item| item.try_to::<GString>().ok())
                .map(|s| s.to_string())
                .collect(),
        ));
    }
    if let Ok(n) = value.try_to::<f64>() {
        return Some(TagValue::Num(n));
    }
    None
}

fn tag_to_variant(tag: &TagValue) -> Variant {
    match tag {
        TagValue::Str(s) => s.to_variant(),
        TagValue::Num(n) => n.to_variant(),
        TagValue::List(items) => {
            let mut array = VarArray::new();
            for item in items {
                array.push(&item.to_variant());
            }
            array.to_variant()
        }
    }
}

#[derive(GodotClass)]
#[class(base=Node)]
struct CampaignManager {
    base: Base<Node>,
    campaign: Option<Campaign>,
    /// Applied to the campaign at start; see `set_map_extent`.
    map_extent: f32,
    faction_defs: Vec<FactionDef>,
    province_defs: Vec<ProvinceDef>,
}

#[godot_api]
impl INode for CampaignManager {
    fn init(base: Base<Node>) -> Self {
        Self {
            base,
            campaign: None,
            map_extent: 2048.0,
            faction_defs: Vec::new(),
            province_defs: Vec::new(),
        }
    }
}

#[godot_api]
impl CampaignManager {
    /// Emitted right after a faction becomes the active one (including turn 1).
    #[signal]
    fn turn_started(faction_id: i64, turn: i64);

    /// Emitted after every resolved attack, win or lose.
    #[signal]
    fn battle_resolved(
        attacker_id: i64,
        defender_id: i64,
        city_id: i64,
        attacker_won: bool,
        defender_eliminated: bool,
    );

    /// Emitted once, when the campaign ends (max turns reached or one faction remains).
    #[signal]
    fn game_over(winner_id: i64);

    /// Emitted for every army move, player- or AI-ordered, *before* any battle
    /// the arrival triggered. `to` is where the army actually stopped, which is
    /// short of the order when it ran out of move points. GDScript animates the
    /// piece along `from` -> `to`.
    #[signal]
    fn army_moved(army_id: i64, from: Vector2, to: Vector2, spent: f32, movement_left: f32);

    /// Emitted after an army move resolves into a fight, with a report dict:
    /// `{kind, attacker_army, defender_army, city_id, attacker_faction,
    ///   defender_faction, attacker_won, loser_army, defender_eliminated}`.
    /// `kind` is "field" or "siege"; `city_id` is -1 for field battles, and
    /// `defender_army` is -1 when an undefended city was stormed. `loser_army`
    /// is the army that was destroyed (-1 if none was).
    #[signal]
    fn army_battle(report: VarDictionary);

    /// Move points every army starts each turn with, so the UI can draw a
    /// move-range ring without hardcoding the number.
    #[func]
    fn default_move_points(&self) -> f32 {
        DEFAULT_MOVE_POINTS
    }

    /// Half-width of the square armies are confined to. Set this from the real
    /// terrain extent *before* starting a game; it is ignored afterwards.
    #[func]
    fn set_map_extent(&mut self, map_extent: f32) {
        self.map_extent = map_extent;
    }

    /// Loads the faction roster from the map package's `factions.json`, as an
    /// array of `{key, name, color, money}` dicts. Colors are carried through
    /// to `get_state` so the UI never hardcodes a palette.
    ///
    /// Call before `start_game_from_provinces`.
    #[func]
    fn load_factions(&mut self, factions: VarArray) {
        self.faction_defs = factions
            .iter_shared()
            .filter_map(|entry| entry.try_to::<VarDictionary>().ok())
            .enumerate()
            .map(|(index, dict)| FactionDef {
                id: index as u32,
                key: dict_string(&dict, "key").unwrap_or_else(|| format!("faction_{index}")),
                name: dict_string(&dict, "name").unwrap_or_else(|| format!("Faction {index}")),
                color: dict_string(&dict, "color").unwrap_or_else(|| "#ffffff".into()),
                money: dict_int(&dict, "money").unwrap_or(100) as i32,
            })
            .collect();
    }

    /// Loads territory from the map package's `provinces.table.json`. GDScript
    /// hands the parsed rows straight through, so the only thing that knows the
    /// table's shape is this function.
    ///
    /// `tags` is read as an open map - a string, a number, or an array of
    /// strings, whatever the layer that produced it reduced to. Adding a layer
    /// to the map means new tags appear here with no change to this code.
    #[func]
    fn load_provinces(&mut self, provinces: VarArray) {
        self.province_defs = provinces
            .iter_shared()
            .filter_map(|entry| entry.try_to::<VarDictionary>().ok())
            .filter_map(|dict| {
                let id = dict_int(&dict, "id")? as ProvinceId;
                let centroid = dict
                    .get("centroid")
                    .and_then(|v| v.try_to::<VarArray>().ok())
                    .map(|a| {
                        let x = a.at(0).try_to::<f64>().unwrap_or(0.0) as f32;
                        let y = a.at(1).try_to::<f64>().unwrap_or(0.0) as f32;
                        (x, y)
                    })
                    .unwrap_or((0.0, 0.0));

                let city_position = dict
                    .get("city_position")
                    .and_then(|v| v.try_to::<VarArray>().ok())
                    .map(|a| {
                        let x = a.at(0).try_to::<f64>().unwrap_or(0.0) as f32;
                        let y = a.at(1).try_to::<f64>().unwrap_or(0.0) as f32;
                        (x, y)
                    })
                    .unwrap_or(centroid);

                let neighbors = dict
                    .get("neighbors")
                    .and_then(|v| v.try_to::<VarArray>().ok())
                    .map(|a| {
                        a.iter_shared()
                            .filter_map(|n| variant_to_i64(&n))
                            .map(|n| n as ProvinceId)
                            .collect()
                    })
                    .unwrap_or_default();

                let tags = dict
                    .get("tags")
                    .and_then(|v| v.try_to::<VarDictionary>().ok())
                    .map(|t| {
                        t.iter_shared()
                            .filter_map(|(key, value)| {
                                let name = key.try_to::<GString>().ok()?.to_string();
                                Some((name, variant_to_tag(&value)?))
                            })
                            .collect()
                    })
                    .unwrap_or_default();

                Some(ProvinceDef {
                    id,
                    key: dict_string(&dict, "key").unwrap_or_else(|| format!("province_{id}")),
                    name: dict_string(&dict, "name").unwrap_or_else(|| format!("Province {id}")),
                    centroid,
                    city_position,
                    neighbors,
                    tags,
                    starting_owner: dict_string(&dict, "starting_owner"),
                })
            })
            .collect();
    }

    /// Starts a campaign on the loaded province table: one city per province,
    /// sited at its area centroid, owned by whoever the map's ownership layer
    /// assigned it to.
    ///
    /// A province whose `starting_owner` names no loaded faction is left
    /// unowned and gets no city - an unclaimed province is a legitimate thing
    /// for a map to describe.
    #[func]
    fn start_game_from_provinces(&mut self, max_turns: i64) {
        if self.province_defs.is_empty() || self.faction_defs.is_empty() {
            self.campaign = None;
            return;
        }

        let factions: Vec<Faction> = self
            .faction_defs
            .iter()
            .map(|def| Faction {
                id: def.id,
                name: def.name.clone(),
                money: def.money,
                alive: true,
            })
            .collect();

        let owner_of = |key: &Option<String>| -> Option<FactionId> {
            let key = key.as_ref()?;
            self.faction_defs
                .iter()
                .find(|def| &def.key == key)
                .map(|def| def.id)
        };

        let mut provinces = Vec::with_capacity(self.province_defs.len());
        let mut cities = Vec::new();
        for def in &self.province_defs {
            let owner = owner_of(&def.starting_owner);
            if let Some(owner_id) = owner {
                cities.push(City {
                    id: cities.len() as CityId,
                    name: def.name.clone(),
                    income: 20,
                    position: def.city_position,
                    owner: owner_id,
                    province: Some(def.id),
                });
            }
            provinces.push(Province {
                id: def.id,
                key: def.key.clone(),
                name: def.name.clone(),
                centroid: def.centroid,
                neighbors: def.neighbors.clone(),
                tags: def.tags.clone(),
                owner,
            });
        }

        if cities.is_empty() {
            self.campaign = None;
            return;
        }

        let mut campaign = Campaign::new(factions, cities, max_turns as u32)
            .with_map_extent(self.map_extent)
            .with_provinces(provinces);
        Self::spawn_starting_armies(&mut campaign);
        self.campaign = Some(campaign);

        let (faction_id, turn) = {
            let c = self.campaign.as_ref().unwrap();
            (c.current_faction_id() as i64, c.turn as i64)
        };
        self.signals().turn_started().emit(faction_id, turn);
    }

    #[func]
    fn current_faction_id(&self) -> i64 {
        self.campaign
            .as_ref()
            .map_or(-1, |c| c.current_faction_id() as i64)
    }

    #[func]
    fn current_turn(&self) -> i64 {
        self.campaign.as_ref().map_or(0, |c| c.turn as i64)
    }

    #[func]
    fn max_turns(&self) -> i64 {
        self.campaign.as_ref().map_or(0, |c| c.max_turns as i64)
    }

    #[func]
    fn is_game_over(&self) -> bool {
        self.campaign.as_ref().is_some_and(|c| c.game_over)
    }

    #[func]
    fn winner_id(&self) -> i64 {
        self.campaign
            .as_ref()
            .and_then(|c| c.winner)
            .map_or(-1, |w| w as i64)
    }

    /// Snapshot for GDScript to render:
    /// `{turn, max_turns, current_faction, game_over, winner,
    ///   factions: [{id, name, money, alive, cities}, ...],
    ///   cities: [{id, name, income, owner, x, y}, ...]}`
    #[func]
    fn get_state(&self) -> VarDictionary {
        let mut state = VarDictionary::new();
        let Some(c) = self.campaign.as_ref() else {
            return state;
        };

        let mut factions = VarArray::new();
        for f in &c.factions {
            let mut fd = VarDictionary::new();
            fd.set("id", f.id as i64);
            fd.set("name", f.name.clone());
            fd.set("money", f.money as i64);
            fd.set("alive", f.alive);
            fd.set("cities", c.cities_owned_by(f.id).len() as i64);
            fd.set("provinces", c.provinces_owned_by(f.id).len() as i64);
            // The color the map package declared. This is the whole reason a
            // province can change hands and change color without any file on
            // disk changing.
            fd.set(
                "color",
                self.faction_defs
                    .iter()
                    .find(|def| def.id == f.id)
                    .map_or_else(|| "#ffffff".to_string(), |def| def.color.clone()),
            );
            factions.push(&fd.to_variant());
        }

        let mut provinces = VarArray::new();
        for province in &c.provinces {
            let mut pd = VarDictionary::new();
            pd.set("id", province.id as i64);
            pd.set("key", province.key.clone());
            pd.set("name", province.name.clone());
            pd.set("owner", province.owner.map_or(-1, |o| o as i64));
            pd.set("x", province.centroid.0 as f64);
            pd.set("y", province.centroid.1 as f64);
            let mut tags = VarDictionary::new();
            for (name, value) in &province.tags {
                tags.set(name.clone(), &tag_to_variant(value));
            }
            pd.set("tags", &tags.to_variant());
            provinces.push(&pd.to_variant());
        }

        let mut cities = VarArray::new();
        for city in &c.cities {
            let mut cd = VarDictionary::new();
            cd.set("id", city.id as i64);
            cd.set("name", city.name.clone());
            cd.set("income", city.income as i64);
            cd.set("owner", city.owner as i64);
            cd.set("province", city.province.map_or(-1, |p| p as i64));
            cd.set("x", city.position.0 as f64);
            cd.set("y", city.position.1 as f64);
            cd.set(
                "garrison",
                c.garrison_of(city.id).map_or(-1, |a| a.id as i64),
            );
            cities.push(&cd.to_variant());
        }

        let mut armies = VarArray::new();
        for army in c.armies.iter().filter(|a| a.alive) {
            let mut ad = VarDictionary::new();
            ad.set("id", army.id as i64);
            ad.set("name", army.name.clone());
            ad.set("owner", army.owner as i64);
            ad.set("x", army.position.0 as f64);
            ad.set("y", army.position.1 as f64);
            ad.set("movement", army.movement as f64);
            ad.set("max_movement", army.max_movement as f64);
            ad.set("garrisoned", army.garrisoned.map_or(-1, |g| g as i64));
            armies.push(&ad.to_variant());
        }

        state.set("armies", &armies.to_variant());
        state.set("map_extent", c.map_extent as f64);
        state.set("turn", c.turn as i64);
        state.set("max_turns", c.max_turns as i64);
        state.set("current_faction", c.current_faction_id() as i64);
        state.set("game_over", c.game_over);
        state.set("winner", c.winner.map_or(-1, |w| w as i64));
        state.set("factions", &factions.to_variant());
        state.set("provinces", &provinces.to_variant());
        state.set("cities", &cities.to_variant());
        state
    }

    /// The current faction attacks `target_city_id`. Emits `battle_resolved`, and
    /// `game_over` if the game ends as a result. Returns true if the attacker won.
    #[func]
    fn attack_city(&mut self, target_city_id: i64) -> bool {
        let Some(campaign) = self.campaign.as_mut() else {
            return false;
        };
        let attacker_id = campaign.current_faction_id();
        let Some(defender_id) = campaign
            .cities
            .iter()
            .find(|c| c.id == target_city_id as u32)
            .map(|c| c.owner)
        else {
            return false;
        };

        let mut rng = thread_rng();
        let Ok(outcome) = campaign.attack(attacker_id, target_city_id as u32, &mut rng) else {
            return false;
        };

        let game_over = campaign.game_over;
        let winner = campaign.winner;

        self.signals().battle_resolved().emit(
            attacker_id as i64,
            defender_id as i64,
            target_city_id,
            outcome.attacker_won,
            outcome.defender_eliminated,
        );
        if game_over {
            self.signals()
                .game_over()
                .emit(winner.map_or(-1, |w| w as i64));
        }

        outcome.attacker_won
    }

    /// Orders `army_id` toward `(x, y)` in world coordinates. Distance costs one
    /// move point per unit with no terrain modifier; an order past the army's
    /// remaining points moves it as far as it can along that heading instead of
    /// failing. Emits `army_moved`, then `army_battle` if the arrival started a
    /// fight, then `game_over` if that ended the campaign. Returns false if the
    /// order was rejected (wrong faction's turn, no points left, no such army).
    #[func]
    fn move_army(&mut self, army_id: i64, x: f32, y: f32) -> bool {
        let Some(campaign) = self.campaign.as_mut() else {
            return false;
        };
        let mut rng = thread_rng();
        let Ok(report) = campaign.move_army(army_id as ArmyId, (x, y), &mut rng) else {
            return false;
        };
        self.emit_move(&report);
        self.emit_game_over_if_ended();
        true
    }

    /// Garrisons `army_id` in the friendly city it is standing in. Returns the
    /// city id, or -1 if the army isn't in one (or it already has a garrison).
    #[func]
    fn garrison_army(&mut self, army_id: i64) -> i64 {
        let Some(campaign) = self.campaign.as_mut() else {
            return -1;
        };
        campaign
            .garrison_army(army_id as ArmyId)
            .map_or(-1, |city_id| city_id as i64)
    }

    /// Plays the current faction's armies with the built-in (random) AI,
    /// emitting `army_moved`/`army_battle` for each one so the UI can animate
    /// the whole turn. Returns how many armies moved.
    #[func]
    fn run_ai_turn(&mut self) -> i64 {
        let Some(campaign) = self.campaign.as_mut() else {
            return 0;
        };
        let mut rng = thread_rng();
        let reports = campaign.run_ai_turn(&mut rng);
        for report in &reports {
            self.emit_move(report);
        }
        self.emit_game_over_if_ended();
        reports.len() as i64
    }

    /// Ends the current faction's turn, crediting the next faction's city income.
    /// Emits `turn_started`, or `game_over` if the campaign has ended.
    #[func]
    fn end_turn(&mut self) {
        let Some(campaign) = self.campaign.as_mut() else {
            return;
        };
        campaign.end_turn();

        let game_over = campaign.game_over;
        let winner = campaign.winner;
        let faction_id = campaign.current_faction_id() as i64;
        let turn = campaign.turn as i64;

        if game_over {
            self.signals()
                .game_over()
                .emit(winner.map_or(-1, |w| w as i64));
        } else {
            self.signals().turn_started().emit(faction_id, turn);
        }
    }

    /// Gives every faction one army, parked in (and garrisoning) its first city.
    /// Recruitment doesn't exist yet, so this fixed starting force is the whole
    /// order of battle for a campaign.
    fn spawn_starting_armies(campaign: &mut Campaign) {
        let starts: Vec<(u32, String, (f32, f32))> = campaign
            .factions
            .iter()
            .filter_map(|f| {
                campaign
                    .cities_owned_by(f.id)
                    .first()
                    .map(|c| (f.id, format!("{} Army", f.name), c.position))
            })
            .collect();
        for (owner, name, position) in starts {
            campaign.spawn_army(owner, name, position);
        }
    }

    /// Fans one `MoveReport` out into the `army_moved` (+ optional
    /// `army_battle`) signals GDScript animates from.
    fn emit_move(&mut self, report: &MoveReport) {
        self.signals().army_moved().emit(
            report.army_id as i64,
            Vector2::new(report.from.0, report.from.1),
            Vector2::new(report.to.0, report.to.1),
            report.spent,
            report.movement_left,
        );
        if let Some(battle) = report.battle.as_ref() {
            self.emit_battle(battle);
        }
    }

    fn emit_battle(&mut self, battle: &BattleReport) {
        let (kind, city_id, defender_army) = match battle.kind {
            BattleKind::Field { defender_army } => ("field", -1, defender_army as i64),
            BattleKind::Siege {
                city,
                defender_army,
            } => ("siege", city as i64, defender_army.map_or(-1, |a| a as i64)),
        };
        // The loser is destroyed: the defender on an attacker win (for a siege
        // that's the garrison, which may not exist), the attacker otherwise.
        let loser_army = if battle.attacker_won {
            defender_army
        } else {
            battle.attacker_army as i64
        };

        let mut report = VarDictionary::new();
        report.set("kind", kind);
        report.set("attacker_army", battle.attacker_army as i64);
        report.set("defender_army", defender_army);
        report.set("city_id", city_id);
        report.set("attacker_faction", battle.attacker_faction as i64);
        report.set("defender_faction", battle.defender_faction as i64);
        report.set("attacker_won", battle.attacker_won);
        report.set("loser_army", loser_army);
        report.set("defender_eliminated", battle.defender_eliminated);
        self.signals().army_battle().emit(&report);
    }

    fn emit_game_over_if_ended(&mut self) {
        let Some(campaign) = self.campaign.as_ref() else {
            return;
        };
        if campaign.game_over {
            let winner = campaign.winner.map_or(-1, |w| w as i64);
            self.signals().game_over().emit(winner);
        }
    }
}
