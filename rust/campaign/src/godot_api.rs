//! GDExtension bindings: a thin `Node` wrapper around the pure [`crate::model::Campaign`]
//! that GDScript drives via `#[func]` calls and listens to via `#[signal]`s.

use godot::classes::{INode, Node};
use godot::prelude::*;
use rand::thread_rng;

use crate::model::{
    ArmyId, BattleKind, BattleReport, Campaign, City, Faction, MoveReport, DEFAULT_MOVE_POINTS,
};

struct CampaignExtension;

#[gdextension]
unsafe impl ExtensionLibrary for CampaignExtension {}

#[derive(GodotClass)]
#[class(base=Node)]
struct CampaignManager {
    base: Base<Node>,
    campaign: Option<Campaign>,
    /// Applied to the campaign at start; see `set_map_extent`.
    map_extent: f32,
}

#[godot_api]
impl INode for CampaignManager {
    fn init(base: Base<Node>) -> Self {
        Self {
            base,
            campaign: None,
            map_extent: 2048.0,
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

    /// Sets up a fixed 4-faction, 4-city scenario and starts the game. Call once before
    /// any other method.
    #[func]
    fn start_default_game(&mut self) {
        let factions = vec![
            Faction {
                id: 0,
                name: "Red".into(),
                money: 100,
                alive: true,
            },
            Faction {
                id: 1,
                name: "Blue".into(),
                money: 100,
                alive: true,
            },
            Faction {
                id: 2,
                name: "Green".into(),
                money: 100,
                alive: true,
            },
            Faction {
                id: 3,
                name: "Yellow".into(),
                money: 100,
                alive: true,
            },
        ];
        let cities = vec![
            City {
                id: 0,
                name: "Redhold".into(),
                income: 20,
                position: (-200.0, -200.0),
                owner: 0,
            },
            City {
                id: 1,
                name: "Bluehold".into(),
                income: 20,
                position: (200.0, -200.0),
                owner: 1,
            },
            City {
                id: 2,
                name: "Greenhold".into(),
                income: 20,
                position: (-200.0, 200.0),
                owner: 2,
            },
            City {
                id: 3,
                name: "Yellowhold".into(),
                income: 20,
                position: (200.0, 200.0),
                owner: 3,
            },
        ];
        let mut campaign = Campaign::new(factions, cities, 10).with_map_extent(self.map_extent);
        Self::spawn_starting_armies(&mut campaign);
        self.campaign = Some(campaign);

        let (faction_id, turn) = {
            let c = self.campaign.as_ref().unwrap();
            (c.current_faction_id() as i64, c.turn as i64)
        };
        self.signals().turn_started().emit(faction_id, turn);
    }

    /// Sets up the campaign from real world-derived city positions: `num_factions =
    /// city_positions.len().clamp(1, 4)` factions from the fixed Red/Blue/Green/Yellow
    /// roster, cities named "City N" with flat income 20, ownership assigned round-robin
    /// (`owner = index % num_factions`) so every faction starts with at least one city.
    /// City id == index into `city_positions`, so callers can map ids back to world
    /// positions directly. Call once before any other method.
    #[func]
    fn start_game_from_positions(&mut self, city_positions: PackedVector2Array, max_turns: i64) {
        const ROSTER: [&str; 4] = ["Red", "Blue", "Green", "Yellow"];

        let num_cities = city_positions.len();
        if num_cities == 0 {
            self.campaign = None;
            return;
        }
        let num_factions = num_cities.clamp(1, 4);

        let factions: Vec<Faction> = (0..num_factions)
            .map(|i| Faction {
                id: i as u32,
                name: ROSTER[i].into(),
                money: 100,
                alive: true,
            })
            .collect();

        let cities: Vec<City> = city_positions
            .as_slice()
            .iter()
            .enumerate()
            .map(|(i, v)| City {
                id: i as u32,
                name: format!("City {}", i + 1),
                income: 20,
                position: (v.x, v.y),
                owner: (i % num_factions) as u32,
            })
            .collect();

        let mut campaign =
            Campaign::new(factions, cities, max_turns as u32).with_map_extent(self.map_extent);
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
            factions.push(&fd.to_variant());
        }

        let mut cities = VarArray::new();
        for city in &c.cities {
            let mut cd = VarDictionary::new();
            cd.set("id", city.id as i64);
            cd.set("name", city.name.clone());
            cd.set("income", city.income as i64);
            cd.set("owner", city.owner as i64);
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
