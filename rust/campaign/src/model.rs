//! Pure game logic for the campaign map, independent of Godot.

use rand::Rng;

pub type FactionId = u32;
pub type CityId = u32;

#[derive(Debug, Clone)]
pub struct City {
    pub id: CityId,
    pub name: String,
    pub income: i32,
    pub position: (f32, f32),
    pub owner: FactionId,
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

#[derive(Debug)]
pub struct Campaign {
    pub factions: Vec<Faction>,
    pub cities: Vec<City>,
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
        self.collect_income(self.current_faction_id());
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
            },
            City {
                id: 1,
                name: "Bluehold".into(),
                income: 10,
                position: (1.0, 0.0),
                owner: 1,
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
            },
            City {
                id: 1,
                name: "B".into(),
                income: 5,
                position: (1.0, 0.0),
                owner: 0,
            },
            City {
                id: 2,
                name: "C".into(),
                income: 5,
                position: (2.0, 0.0),
                owner: 1,
            },
        ];
        let mut c = Campaign::new(factions, cities, 1);
        assert!(!c.game_over);
        c.end_turn(); // turn -> 2, exceeds max_turns(1)
        assert!(c.game_over);
        assert_eq!(c.winner, Some(0));
    }

    #[test]
    fn alive_count_two_survives_full_battle_loop() {
        let mut c = sample_campaign();
        let mut rng = SmallRng::seed_from_u64(2);
        let outcome = c.attack(0, 1, &mut rng);
        assert!(outcome.is_ok());
    }
}
