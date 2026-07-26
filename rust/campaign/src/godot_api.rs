//! GDExtension bindings: a thin `Node` wrapper around the pure [`crate::model::Campaign`]
//! that GDScript drives via `#[func]` calls and listens to via `#[signal]`s.

use godot::classes::{INode, Node};
use godot::prelude::*;
use rand::thread_rng;

use crate::model::{Campaign, City, Faction};

struct CampaignExtension;

#[gdextension]
unsafe impl ExtensionLibrary for CampaignExtension {}

#[derive(GodotClass)]
#[class(base=Node)]
struct CampaignManager {
    base: Base<Node>,
    campaign: Option<Campaign>,
}

#[godot_api]
impl INode for CampaignManager {
    fn init(base: Base<Node>) -> Self {
        Self {
            base,
            campaign: None,
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
        self.campaign = Some(Campaign::new(factions, cities, 10));

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

        self.campaign = Some(Campaign::new(factions, cities, max_turns as u32));

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
            cities.push(&cd.to_variant());
        }

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
}
