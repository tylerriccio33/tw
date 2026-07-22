"""Heuristic AI: inspects the state, emits ordinary Commands through
`rules.apply` so it plays by exactly the same rules as the player."""

from __future__ import annotations

from collections import deque

from . import command as cmds, rules
from .config import balance
from .event import Event
from .ids import FactionId, ProvinceId
from .state import Building, DiploStatus, GameState, ProposalKind, UnitType


def take_turn(state: GameState) -> list[Event]:
    f = state.current
    events: list[Event] = []
    _diplomacy_phase(state, f, events)
    _economy_phase(state, f, events)
    _military_phase(state, f, events)
    return events


def _go(state: GameState, cmd, events: list[Event]) -> None:
    """Apply a command, ignoring rule errors (the AI's plans can go stale as
    earlier commands change the world; that's fine, it just skips them)."""
    try:
        events.extend(rules.apply(state, cmd))
    except rules.RuleError:
        pass


def _diplomacy_phase(state: GameState, f: FactionId, events: list[Event]) -> None:
    my_str = max(state.strength(f), 1)
    living = state.living_factions()

    # Sue for peace in wars that are going badly.
    for e in living:
        if state.at_war(f, e) and my_str * 5 < state.strength(e) * 3:
            _go(state, cmds.Propose(to=e, treaty=ProposalKind.PEACE), events)

    # Opportunistic aggression against much weaker neighbors, if not already
    # fighting a real war.
    fighting = any(state.at_war(f, e) for e in living)
    if not fighting and state.rng.random() < 0.12:
        candidates = [
            c
            for c in living
            if c != f
            and state.share_border(f, c)
            and not state.allied(f, c)
            and state.strength(c) * 3 < my_str * 2
        ]
        if candidates:
            _go(state, cmds.DeclareWar(on=min(candidates, key=state.strength)), events)

    # Friendly overtures to neighbors.
    for n in living:
        if n == f or not state.share_border(f, n):
            continue
        rel = state.rel(f, n)
        if rel.status == DiploStatus.PEACE and state.rng.random() < 0.2:
            _go(state, cmds.Propose(to=n, treaty=ProposalKind.TRADE), events)
        elif (
            rel.status == DiploStatus.TRADE
            and rel.opinion >= 30
            and state.rng.random() < 0.1
        ):
            _go(state, cmds.Propose(to=n, treaty=ProposalKind.ALLIANCE), events)


def _economy_phase(state: GameState, f: FactionId, events: list[Event]) -> None:
    owned = state.faction_provinces(f)

    # Recruit at the frontier (any owned province touching hostile land —
    # rebels count, since everyone is at war with them).
    frontier = [
        p
        for p in owned
        if any(
            state.at_war(f, state.provinces[q].owner)
            for q in state.provinces[p].adjacent
        )
    ]
    pool = frontier or owned
    if pool:
        p = pool[0]
        # Raise a combined-arms force: a cavalry wedge when flush, backed by
        # archers and a core of cheap melee, keeping a treasury buffer.
        for unit in (UnitType.CAV, UnitType.ARCHER, UnitType.MELEE):
            if state.factions[f].treasury > 150 + unit.cost:
                _go(state, cmds.Recruit(province=p, unit=unit), events)

    # Field an army from a fat garrison when there is fighting to do.
    fighting = (
        any(
            not state.factions[e].is_rebel and state.at_war(f, FactionId(e))
            for e in range(len(state.factions))
        )
        or bool(frontier)
    )
    if fighting:
        for p in owned:
            garrison = state.provinces[p].city.garrison.total()
            has_army = any(
                a.owner == f and a.location == p for a in state.armies.values()
            )
            if garrison >= 6 and not has_army:
                _go(
                    state,
                    cmds.RaiseArmy(province=p, regiments=garrison - 3),
                    events,
                )

    # Invest surplus gold in the least-developed building of the first idle city.
    if state.factions[f].treasury > 350:
        for p in owned:
            city = state.provinces[p].city
            if city.construction is not None:
                continue
            options = sorted(
                [
                    (city.farm, Building.FARM),
                    (city.market, Building.MARKET),
                    (city.barracks, Building.BARRACKS),
                ],
                key=lambda o: o[0],
            )
            choice = next((b for lvl, b in options if lvl < 3), None)
            if choice is not None:
                _go(state, cmds.Build(province=p, building=choice), events)
                break


def _military_phase(state: GameState, f: FactionId, events: list[Event]) -> None:
    for id in state.faction_armies(f):
        # An army acts until it runs out of movement, dies, or settles into a siege.
        for _ in range(8):
            live = state.armies.get(id)
            if live is None:
                break
            a = live.copy()
            here = a.location
            owner = state.provinces[here].owner

            if owner != f and state.at_war(f, owner):
                # Standing on hostile ground: take the city.
                if a.mp == 0:
                    break
                garrison = state.provinces[here].city.garrison
                if garrison.is_empty():
                    _go(state, cmds.Assault(army=id), events)
                    break
                siege = state.provinces[here].siege
                contested = siege is not None and siege.by != f
                siege_turns = siege.turns if siege is not None and siege.by == f else 0
                walls = max(0, state.provinces[here].city.walls - siege_turns)
                def_score = (
                    garrison.capability()
                    * (100 + walls * balance().wall_defense_pct)
                    // 100
                )
                att_score = a.force.capability()
                if att_score * 10 > def_score * 13:
                    _go(state, cmds.Assault(army=id), events)
                    break
                if not contested:
                    _go(state, cmds.Besiege(army=id), events)
                    break
                # Someone else is starving this city; look for another fight.
                next_ = _step_toward_enemy(state, f, here, a.force.capability())
                if next_ is None:
                    break
                _go(state, cmds.Move(army=id, to=next_), events)
                moved = state.armies.get(id)
                if moved is None or moved.location != next_:
                    break
                continue

            if a.mp == 0:
                break
            next_ = _step_toward_enemy(state, f, here, a.force.capability())
            if next_ is None:
                break
            _go(state, cmds.Move(army=id, to=next_), events)
            # If the move was rejected or we died in a field battle, stop.
            after = state.armies.get(id)
            if after == a or after is None:
                break


def _step_toward_enemy(
    state: GameState, f: FactionId, from_: ProvinceId, capability: int
) -> ProvinceId | None:
    """BFS toward the nearest hostile province we can plausibly take on, walking
    only through territory we may legally enter. Returns the first step."""

    def passable(p: ProvinceId) -> bool:
        o = state.provinces[p].owner
        return o == f or state.allied(f, o) or state.at_war(f, o)

    def is_target(p: ProvinceId) -> bool:
        o = state.provinces[p].owner
        if not state.at_war(f, o):
            return False
        # A city someone else is already starving out is not worth marching on.
        siege = state.provinces[p].siege
        if siege is not None and siege.by != f:
            return False
        # Don't walk into a field battle we would clearly lose.
        return state.enemy_capability_in(f, p) * 13 <= capability * 10

    prev: dict[ProvinceId, ProvinceId] = {}
    seen = {from_}
    queue = deque([from_])
    while queue:
        p = queue.popleft()
        if p != from_ and is_target(p):
            # Walk back to the first step out of `from_`.
            step = p
            while prev[step] != from_:
                step = prev[step]
            return step
        for q in state.provinces[p].adjacent:
            if q not in seen and passable(q):
                seen.add(q)
                prev[q] = p
                queue.append(q)
    return None
