"""End-of-turn resolution and turn rotation."""

from __future__ import annotations

from . import economy, event as ev, rules
from .config import balance
from .event import Event
from .ids import FactionId, ProvinceId
from .state import Building, GameState, Siege, UNIT_TYPES


def end_turn(state: GameState) -> list[Event]:
    """Resolve the current faction's end of turn (training, construction,
    sieges), advance to the next living faction, and begin its turn (income,
    movement)."""
    events: list[Event] = []
    if state.winner is not None:
        return events
    f = state.current

    # Training and construction tick in every city the faction owns.
    for i in range(len(state.provinces)):
        if state.provinces[i].owner != f:
            continue
        city = state.provinces[i].city
        # A settlement trains a population-scaled number of units per turn, with
        # a barracks speeding things along — so a big city can field several
        # regiments a turn while a village trickles them out.
        cap = max(city.population // balance().recruit_pop_per_unit, 1) + city.barracks
        trained = 0
        for t in UNIT_TYPES:
            if trained >= cap:
                break
            n = min(cap - trained, city.recruit_queue.count(t))
            city.recruit_queue.remove_units_of(t, n)
            city.garrison.add(t, n)
            trained += n
        if trained > 0:
            events.append(ev.RegimentsTrained(province=ProvinceId(i), count=trained))

        c = city.construction
        if c is not None:
            c.turns_left -= 1
            if c.turns_left == 0:
                match c.building:
                    case Building.FARM:
                        city.farm += 1
                    case Building.MARKET:
                        city.market += 1
                    case Building.BARRACKS:
                        city.barracks += 1
                    case Building.WALLS:
                        city.walls += 1
                city.construction = None
                events.append(
                    ev.ConstructionFinished(province=ProvinceId(i), building=c.building)
                )

    # Sieges this faction is running: tighten the noose or go home.
    for i in range(len(state.provinces)):
        p = ProvinceId(i)
        s = state.provinces[i].siege
        if s is None or s.by != f:
            continue
        rules.lift_abandoned_siege(state, f, p, events)
        s = state.provinces[i].siege
        if s is None:
            continue
        turns = s.turns + 1
        state.provinces[i].siege = Siege(by=f, turns=turns)
        if turns >= balance().siege_attrition_after:
            g = state.provinces[i].city.garrison
            g.remove_units(1)
            if g.is_empty():
                rules.capture(state, p, f, events)

    if state.winner is not None:
        return events

    # Advance to the next living, non-rebel faction.
    n = len(state.factions)
    next_ = f
    while True:
        next_ = (next_ + 1) % n
        cand = state.factions[next_]
        if cand.alive and not cand.is_rebel:
            break
    if next_ <= f:
        state.turn += 1
    state.current = FactionId(next_)
    events.extend(begin_turn(state))
    return events


def begin_turn(state: GameState) -> list[Event]:
    """Income, upkeep, desertion, and fresh movement for the faction whose turn
    is starting."""
    events: list[Event] = []
    f = state.current
    income = economy.faction_income(state, f)
    upkeep = economy.faction_upkeep(state, f)
    state.factions[f].treasury += income - upkeep
    events.append(
        ev.TurnBegan(faction=f, turn=state.turn, income=income, upkeep=upkeep)
    )

    # Broke factions bleed soldiers.
    if state.factions[f].treasury < 0:
        for id in state.faction_armies(f):
            a = state.armies[id]
            a.force.remove_units(1)
            remaining = a.force.total()
            location = a.location
            if remaining == 0:
                del state.armies[id]
                rules.lift_abandoned_siege(state, f, location, events)
            events.append(ev.Desertion(army=id, remaining=remaining))

    for a in state.armies.values():
        if a.owner == f:
            a.mp = balance().army_mp
    return events
