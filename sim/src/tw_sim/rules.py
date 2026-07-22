"""Command validation and application: the single entry point through which any
faction (human or AI) mutates the game."""

from __future__ import annotations

from enum import Enum

from . import combat, command as cmds, data, diplomacy, event as ev
from .command import Command
from .config import balance
from .event import Event
from .ids import ArmyId, FactionId, ProvinceId
from .state import Army, Building, Construction, Force, GameState, Siege


class Rule(Enum):
    """Why a command was refused. The value is the message a frontend shows."""

    GAME_OVER = "the campaign is over"
    NOT_YOUR_ARMY = "that army is not yours"
    NOT_YOUR_PROVINCE = "you do not own that province"
    NO_SUCH_ARMY = "no such army"
    NO_SUCH_PROPOSAL = "no such proposal"
    NOT_ADJACENT = "that province is not adjacent"
    NO_MOVEMENT_LEFT = "no movement points left this turn"
    NO_ACCESS = "no military access (you are at peace with the owner)"
    NOT_AT_WAR = "you are not at war with them"
    ALREADY_AT_WAR = "you are already at war with them"
    NOTHING_TO_BESIEGE = "nothing to besiege here"
    SIEGE_CONTESTED = "another faction is already besieging this city"
    NOT_ENOUGH_GOLD = "not enough gold"
    NOT_ENOUGH_REGIMENTS = "not enough regiments"
    CONSTRUCTION_BUSY = "a construction project is already underway"
    MAX_LEVEL = "already at maximum level"
    ARMIES_NOT_TOGETHER = "armies must share a province (and an owner)"
    INVALID_PROPOSAL = "that proposal makes no sense right now"
    CANNOT_TARGET_SELF = "you cannot target yourself"


class RuleError(Exception):
    """A command the rules refused. Carries the `Rule` variant so callers can
    branch on it, and stringifies to the player-facing message."""

    def __init__(self, rule: Rule) -> None:
        super().__init__(rule.value)
        self.rule = rule


def apply(state: GameState, cmd: Command) -> list[Event]:
    """Validate and apply one command. Raises `RuleError` and leaves the state
    untouched if the command is illegal."""
    if state.winner is not None:
        raise RuleError(Rule.GAME_OVER)
    events: list[Event] = []
    me = state.current
    match cmd:
        case cmds.Move():
            _move_army(state, me, cmd.army, cmd.to, events)
        case cmds.Assault():
            _assault(state, me, cmd.army, events)
        case cmds.Besiege():
            _besiege(state, me, cmd.army, events)
        case cmds.Recruit():
            _own_province(state, me, cmd.province)
            cost = cmd.unit.cost
            if state.factions[me].treasury < cost:
                raise RuleError(Rule.NOT_ENOUGH_GOLD)
            state.factions[me].treasury -= cost
            state.provinces[cmd.province].city.recruit_queue.add(cmd.unit, 1)
            events.append(ev.RecruitQueued(province=cmd.province, unit=cmd.unit))
        case cmds.Build():
            _own_province(state, me, cmd.province)
            city = state.provinces[cmd.province].city
            if city.construction is not None:
                raise RuleError(Rule.CONSTRUCTION_BUSY)
            b = balance()
            level, cap = {
                Building.FARM: (city.farm, b.max_building_level),
                Building.MARKET: (city.market, b.max_building_level),
                Building.BARRACKS: (city.barracks, b.max_building_level),
                Building.WALLS: (city.walls, b.max_walls),
            }[cmd.building]
            if level >= cap:
                raise RuleError(Rule.MAX_LEVEL)
            next_level = level + 1
            cost = b.build_cost_per_level * next_level
            if state.factions[me].treasury < cost:
                raise RuleError(Rule.NOT_ENOUGH_GOLD)
            state.factions[me].treasury -= cost
            city.construction = Construction(building=cmd.building, turns_left=next_level)
            events.append(
                ev.ConstructionStarted(province=cmd.province, building=cmd.building)
            )
        case cmds.RaiseArmy():
            _own_province(state, me, cmd.province)
            garrison = state.provinces[cmd.province].city.garrison
            if cmd.regiments == 0 or cmd.regiments > garrison.total():
                raise RuleError(Rule.NOT_ENOUGH_REGIMENTS)
            force = garrison.split_off(cmd.regiments)
            id = ArmyId(state.next_army)
            state.next_army += 1
            state.armies[id] = Army(
                owner=me,
                location=cmd.province,
                force=force,
                mp=1,
                general=data.general_for(me, id),
            )
            events.append(
                ev.ArmyRaised(army=id, province=cmd.province, regiments=cmd.regiments)
            )
        case cmds.Merge():
            a = _army(state, cmd.from_)
            b_army = _army(state, cmd.into)
            if a.owner != me or b_army.owner != me:
                raise RuleError(Rule.NOT_YOUR_ARMY)
            if a.location != b_army.location or cmd.from_ == cmd.into:
                raise RuleError(Rule.ARMIES_NOT_TOGETHER)
            del state.armies[cmd.from_]
            b_army.force.merge(a.force)
            b_army.mp = min(b_army.mp, a.mp)
            events.append(ev.ArmiesMerged(from_=cmd.from_, into=cmd.into))
        case cmds.Garrison():
            a = _army(state, cmd.army)
            if a.owner != me:
                raise RuleError(Rule.NOT_YOUR_ARMY)
            if state.provinces[a.location].owner != me:
                raise RuleError(Rule.NOT_YOUR_PROVINCE)
            state.provinces[a.location].city.garrison.merge(a.force)
            del state.armies[cmd.army]
            lift_abandoned_siege(state, me, a.location, events)
            events.append(ev.Garrisoned(army=cmd.army, province=a.location))
        case cmds.DeclareWar():
            if cmd.on == me:
                raise RuleError(Rule.CANNOT_TARGET_SELF)
            if not state.factions[cmd.on].alive or state.factions[cmd.on].is_rebel:
                raise RuleError(Rule.INVALID_PROPOSAL)
            if state.at_war(me, cmd.on):
                raise RuleError(Rule.ALREADY_AT_WAR)
            diplomacy.declare_war(state, me, cmd.on, events)
        case cmds.Propose():
            if not diplomacy.proposal_valid(state, me, cmd.to, cmd.treaty):
                raise RuleError(Rule.INVALID_PROPOSAL)
            diplomacy.propose(state, me, cmd.to, cmd.treaty, events)
        case cmds.Respond():
            pos = next(
                (
                    i
                    for i, p in enumerate(state.proposals)
                    if p.id == cmd.proposal and p.to == me
                ),
                None,
            )
            if pos is None:
                raise RuleError(Rule.NO_SUCH_PROPOSAL)
            p = state.proposals.pop(pos)
            if cmd.accept and diplomacy.proposal_valid(state, p.from_, p.to, p.kind):
                diplomacy.enact(state, p.from_, p.to, p.kind)
                events.append(
                    ev.ProposalAccepted(from_=p.from_, to=p.to, treaty=p.kind)
                )
            else:
                state.shift_opinion(p.from_, p.to, -5)
                events.append(
                    ev.ProposalRejected(from_=p.from_, to=p.to, treaty=p.kind)
                )
        case _:
            raise TypeError(f"not a Command: {cmd!r}")
    return events


def _army(state: GameState, id: ArmyId) -> Army:
    try:
        return state.armies[id]
    except KeyError:
        raise RuleError(Rule.NO_SUCH_ARMY) from None


def _own_province(state: GameState, me: FactionId, p: ProvinceId) -> None:
    if state.provinces[p].owner != me:
        raise RuleError(Rule.NOT_YOUR_PROVINCE)


def _move_army(
    state: GameState,
    me: FactionId,
    army: ArmyId,
    to: ProvinceId,
    events: list[Event],
) -> None:
    a = _army(state, army)
    if a.owner != me:
        raise RuleError(Rule.NOT_YOUR_ARMY)
    if a.mp == 0:
        raise RuleError(Rule.NO_MOVEMENT_LEFT)
    if to not in state.provinces[a.location].adjacent:
        raise RuleError(Rule.NOT_ADJACENT)
    owner = state.provinces[to].owner
    if owner != me and not state.at_war(me, owner) and not state.allied(me, owner):
        raise RuleError(Rule.NO_ACCESS)

    from_ = a.location
    defenders = [
        id
        for id, d in state.armies.items()
        if d.location == to and state.at_war(me, d.owner)
    ]

    if not defenders:
        a.location = to
        a.mp -= 1
        events.append(ev.Moved(army=army, from_=from_, to=to))
        lift_abandoned_siege(state, me, from_, events)
        return

    # Field battle against everything hostile camped in the target province.
    defender_owner = state.armies[defenders[0]].owner
    def_regs = sum(state.armies[id].force.total() for id in defenders)
    def_cap = sum(state.armies[id].force.capability() for id in defenders)
    att_regs = a.force.total()
    result = combat.resolve(
        state.rng, a.force.capability(), def_cap, att_regs, def_regs, 0
    )
    if result.attacker_won:
        for id in defenders:
            del state.armies[id]
        _take_losses(a.force, result.winner_losses)
        a.location = to
        a.mp -= 1
    else:
        del state.armies[army]
        _shave_defenders(state, defenders, result.winner_losses)
    events.append(
        ev.FieldBattle(
            attacker=me,
            defender=defender_owner,
            province=to,
            attacker_regiments=att_regs,
            defender_regiments=def_regs,
            attacker_won=result.attacker_won,
            winner_losses=result.winner_losses,
        )
    )
    if result.attacker_won:
        lift_abandoned_siege(state, me, from_, events)
        # Beaten defenders can no longer maintain a siege here either.
        lift_abandoned_siege(state, defender_owner, to, events)


def _take_losses(force: Force, losses: int) -> None:
    """Take `losses` units out of a victorious force, cheapest troops first, but
    never wipe it out — a winner always keeps at least one unit."""
    keep = min(force.total(), 1)
    force.remove_units(min(losses, max(0, force.total() - keep)))


def _shave_defenders(state: GameState, defenders: list[ArmyId], losses: int) -> None:
    """Spread `losses` across the surviving defender armies, front to back,
    leaving each with at least one unit."""
    for id in defenders:
        if losses == 0:
            break
        d = state.armies.get(id)
        if d is not None:
            cut = min(losses, d.force.total() - 1)
            d.force.remove_units(cut)
            losses -= cut


def _assault(state: GameState, me: FactionId, army: ArmyId, events: list[Event]) -> None:
    a = _army(state, army)
    if a.owner != me:
        raise RuleError(Rule.NOT_YOUR_ARMY)
    if a.mp == 0:
        raise RuleError(Rule.NO_MOVEMENT_LEFT)
    p = a.location
    owner = state.provinces[p].owner
    if owner == me or not state.at_war(me, owner):
        raise RuleError(Rule.NOT_AT_WAR)

    a.mp = 0
    garrison = state.provinces[p].city.garrison
    if garrison.is_empty():
        capture(state, p, me, events)
        return

    # A long siege undermines the walls' defensive value.
    siege = state.provinces[p].siege
    siege_turns = siege.turns if siege is not None and siege.by == me else 0
    effective_walls = max(0, state.provinces[p].city.walls - siege_turns)
    result = combat.resolve(
        state.rng,
        a.force.capability(),
        garrison.capability(),
        a.force.total(),
        garrison.total(),
        effective_walls * balance().wall_defense_pct,
    )
    events.append(
        ev.Assaulted(
            attacker=me,
            province=p,
            attacker_regiments=a.force.total(),
            garrison=garrison.total(),
            attacker_won=result.attacker_won,
            winner_losses=result.winner_losses,
        )
    )
    if result.attacker_won:
        _take_losses(a.force, result.winner_losses)
        capture(state, p, me, events)
    else:
        del state.armies[army]
        _take_losses(state.provinces[p].city.garrison, result.winner_losses)
        lift_abandoned_siege(state, me, p, events)


def _besiege(state: GameState, me: FactionId, army: ArmyId, events: list[Event]) -> None:
    a = _army(state, army)
    if a.owner != me:
        raise RuleError(Rule.NOT_YOUR_ARMY)
    p = a.location
    owner = state.provinces[p].owner
    if owner == me or not state.at_war(me, owner):
        raise RuleError(Rule.NOT_AT_WAR)
    if state.provinces[p].city.garrison.is_empty():
        raise RuleError(Rule.NOTHING_TO_BESIEGE)
    siege = state.provinces[p].siege
    if siege is not None and siege.by == me:
        # Already besieging; just hold position.
        a.mp = 0
    elif siege is not None:
        raise RuleError(Rule.SIEGE_CONTESTED)
    else:
        a.mp = 0
        state.provinces[p].siege = Siege(by=me, turns=0)
        events.append(ev.SiegeStarted(faction=me, province=p))


def lift_abandoned_siege(
    state: GameState, f: FactionId, p: ProvinceId, events: list[Event]
) -> None:
    """If `f` has no armies left in `p`, any siege they were running there ends."""
    s = state.provinces[p].siege
    if s is not None and s.by == f:
        still_there = any(
            a.owner == f and a.location == p for a in state.armies.values()
        )
        if not still_there:
            state.provinces[p].siege = None
            events.append(ev.SiegeLifted(province=p))


def capture(state: GameState, p: ProvinceId, by: FactionId, events: list[Event]) -> None:
    """Transfer a province to `by`, with all the fallout: sacked defenses,
    possible faction destruction, possible campaign victory."""
    old = state.provinces[p].owner
    prov = state.provinces[p]
    prov.owner = by
    prov.city.garrison = Force()
    prov.city.walls = max(0, prov.city.walls - 1)
    prov.city.recruit_queue = Force()
    prov.city.construction = None
    prov.siege = None
    events.append(ev.CityFell(province=p, from_=old, to=by))
    state.shift_opinion(old, by, -30)
    if not state.factions[old].is_rebel and not state.faction_provinces(old):
        destroy_faction(state, old, events)
    check_winner(state, events)


def destroy_faction(state: GameState, f: FactionId, events: list[Event]) -> None:
    state.factions[f].alive = False
    state.armies = {id: a for id, a in state.armies.items() if a.owner != f}
    state.proposals = [p for p in state.proposals if p.from_ != f and p.to != f]
    for prov in state.provinces:
        if prov.siege is not None and prov.siege.by == f:
            prov.siege = None
    events.append(ev.FactionDestroyed(faction=f))


def check_winner(state: GameState, events: list[Event]) -> None:
    if state.winner is not None:
        return
    living = state.living_factions()
    if len(living) == 1:
        winner: FactionId | None = living[0]
    else:
        winner = next(
            (
                f
                for f in living
                if len(state.faction_provinces(f)) >= balance().win_provinces
            ),
            None,
        )
    if winner is not None:
        state.winner = winner
        events.append(ev.GameWon(faction=winner))
