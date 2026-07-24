"""Behaviors the Rust test suite never covered.

Every one of these is somewhere a silent port bug could hide: a reversed
iteration order, a `Copy` that quietly became an alias, a symmetric write that
only landed on one side of the matrix. They are asserted here because the
reference implementation is gone.
"""

from __future__ import annotations

import pytest

from tw_sim import ArmyId, Force, ProposalKind, ProvinceId, UnitType
from tw_sim import apply, end_turn, new_game
from tw_sim import command as cmds, diplomacy, event as ev, rules
from tw_sim.config import balance
from tw_sim.data import ENGLAND, SCOTLAND, WALES
from tw_sim.rules import Rule, RuleError
from tw_sim.state import Building, DiploStatus, Siege

# -------- Force ordering --------


def test_losses_fall_on_the_cheapest_troops_first():
    """`remove_units` goes melee -> archer -> cav, so cavalry survive longest."""
    f = Force(melee=2, archer=2, cav=2)
    assert f.remove_units(3) == 3
    assert (f.melee, f.archer, f.cav) == (0, 1, 2)
    # Saturates at the head count rather than going negative.
    assert f.remove_units(99) == 3
    assert f.total() == 0


def test_raised_armies_muster_the_strongest_troops_first():
    """`split_off` takes cav -> archer -> melee, leaving the levy behind."""
    garrison = Force(melee=5, archer=2, cav=1)
    field = garrison.split_off(4)
    assert (field.melee, field.archer, field.cav) == (1, 2, 1)
    assert (garrison.melee, garrison.archer, garrison.cav) == (4, 0, 0)


def test_a_units_capability_is_its_gold_cost():
    b = balance()
    assert UnitType.MELEE.capability == b.melee_cost
    assert UnitType.CAV.capability == b.cav_cost
    assert Force(melee=1, archer=1, cav=1).capability() == (
        b.melee_cost + b.archer_cost + b.cav_cost
    )


def test_force_copy_is_not_an_alias():
    """The Rust `Force` was `Copy`; the Python one is a reference type, so
    anywhere the port kept a snapshot it must have gone through `.copy()`."""
    a = Force(melee=3)
    b = a.copy()
    b.melee = 9
    assert a.melee == 3


# -------- relations are symmetric --------


def test_relations_are_symmetric_in_both_directions():
    state = new_game(42)
    state.set_status(ENGLAND, SCOTLAND, DiploStatus.ALLIANCE)
    assert state.rel(SCOTLAND, ENGLAND).status == DiploStatus.ALLIANCE
    assert state.allied(SCOTLAND, ENGLAND) and state.allied(ENGLAND, SCOTLAND)
    state.shift_opinion(SCOTLAND, ENGLAND, 25)
    assert state.rel(ENGLAND, SCOTLAND).opinion == state.rel(SCOTLAND, ENGLAND).opinion


def test_opinion_clamps_to_plus_minus_100():
    state = new_game(42)
    state.shift_opinion(ENGLAND, SCOTLAND, 9_000)
    assert state.rel(ENGLAND, SCOTLAND).opinion == 100
    state.shift_opinion(ENGLAND, SCOTLAND, -9_000)
    assert state.rel(ENGLAND, SCOTLAND).opinion == -100


# -------- commands the Rust tests never exercised --------


def test_merge_folds_one_army_into_another_and_takes_the_lower_mp():
    state = new_game(42)
    a, b = ArmyId(0), ArmyId(77)
    state.armies[b] = state.armies[a].copy()
    state.armies[b].force = Force(melee=1, cav=2)
    state.armies[b].mp = 0
    apply(state, cmds.Merge(from_=b, into=a))
    assert b not in state.armies
    assert state.armies[a].force.total() == 8 + 3
    assert state.armies[a].force.cav == 2
    assert state.armies[a].mp == 0  # the slower army sets the pace


def test_merge_rejects_armies_in_different_provinces():
    state = new_game(42)
    a, b = ArmyId(0), ArmyId(77)
    state.armies[b] = state.armies[a].copy()
    state.armies[b].location = ProvinceId(1)
    with pytest.raises(RuleError) as e:
        apply(state, cmds.Merge(from_=b, into=a))
    assert e.value.rule is Rule.ARMIES_NOT_TOGETHER


def test_garrison_disbands_an_army_into_its_city():
    state = new_game(42)
    army = ArmyId(0)
    before = state.provinces[0].city.garrison.total()
    apply(state, cmds.Garrison(army=army))
    assert army not in state.armies
    assert state.provinces[0].city.garrison.total() == before + 8


def test_garrison_requires_your_own_province():
    state = new_game(42)
    army = ArmyId(0)
    state.armies[army].location = ProvinceId(4)  # rebel Chester
    with pytest.raises(RuleError) as e:
        apply(state, cmds.Garrison(army=army))
    assert e.value.rule is Rule.NOT_YOUR_PROVINCE


def test_raise_army_pulls_regiments_out_of_the_garrison():
    state = new_game(42)
    events = apply(state, cmds.RaiseArmy(province=ProvinceId(0), regiments=3))
    raised = next(e for e in events if isinstance(e, ev.ArmyRaised))
    assert state.armies[raised.army].force.total() == 3
    assert state.provinces[0].city.garrison.total() == 5 - 3
    assert state.armies[raised.army].mp == 1  # mustering costs the turn's march


@pytest.mark.parametrize("n", [0, 99])
def test_raise_army_rejects_impossible_sizes(n):
    state = new_game(42)
    with pytest.raises(RuleError) as e:
        apply(state, cmds.RaiseArmy(province=ProvinceId(0), regiments=n))
    assert e.value.rule is Rule.NOT_ENOUGH_REGIMENTS


def test_build_charges_gold_and_completes_after_n_turns():
    state = new_game(42)
    b = balance()
    treasury = state.factions[ENGLAND].treasury
    # London starts with farm 1, so the next level is 2 and costs 2x.
    apply(state, cmds.Build(province=ProvinceId(0), building=Building.FARM))
    assert state.factions[ENGLAND].treasury == treasury - 2 * b.build_cost_per_level
    assert state.provinces[0].city.construction.turns_left == 2

    finished = False
    for _ in range(4):
        events = end_turn(state)
        while state.current != ENGLAND:
            events += end_turn(state)
        finished |= any(isinstance(e, ev.ConstructionFinished) for e in events)
        if finished:
            break
    assert finished
    assert state.provinces[0].city.farm == 2
    assert state.provinces[0].city.construction is None


def test_build_refuses_a_second_project_and_a_maxed_building():
    state = new_game(42)
    apply(state, cmds.Build(province=ProvinceId(0), building=Building.FARM))
    with pytest.raises(RuleError) as e:
        apply(state, cmds.Build(province=ProvinceId(0), building=Building.MARKET))
    assert e.value.rule is Rule.CONSTRUCTION_BUSY

    state.provinces[1].city.walls = balance().max_walls
    with pytest.raises(RuleError) as e:
        apply(state, cmds.Build(province=ProvinceId(1), building=Building.WALLS))
    assert e.value.rule is Rule.MAX_LEVEL


def test_respond_accepts_and_rejects_a_queued_proposal():
    state = new_game(42)
    # England is the player, so a proposal to it queues rather than resolving.
    diplomacy.declare_war(state, SCOTLAND, ENGLAND, [])
    events: list = []
    diplomacy.propose(state, SCOTLAND, ENGLAND, ProposalKind.PEACE, events)
    sent = next(e for e in events if isinstance(e, ev.ProposalSent))
    assert state.at_war(ENGLAND, SCOTLAND)

    out = apply(state, cmds.Respond(proposal=sent.id, accept=True))
    assert any(isinstance(e, ev.ProposalAccepted) for e in out)
    assert not state.at_war(ENGLAND, SCOTLAND)
    assert not state.proposals

    with pytest.raises(RuleError) as e:
        apply(state, cmds.Respond(proposal=sent.id, accept=True))
    assert e.value.rule is Rule.NO_SUCH_PROPOSAL


def test_rejecting_a_proposal_sours_the_relationship():
    state = new_game(42)
    diplomacy.declare_war(state, SCOTLAND, ENGLAND, [])
    events: list = []
    diplomacy.propose(state, SCOTLAND, ENGLAND, ProposalKind.PEACE, events)
    sent = next(e for e in events if isinstance(e, ev.ProposalSent))
    before = state.rel(ENGLAND, SCOTLAND).opinion
    apply(state, cmds.Respond(proposal=sent.id, accept=False))
    assert state.rel(ENGLAND, SCOTLAND).opinion == before - 5
    assert state.at_war(ENGLAND, SCOTLAND)


# -------- bankruptcy --------


def test_bankruptcy_makes_armies_desert():
    state = new_game(42)
    state.factions[ENGLAND].treasury = -10_000
    army = ArmyId(0)
    before = state.armies[army].force.total()
    # Roll the whole table back around to England, whose turn then begins broke.
    events = end_turn(state)
    while state.current != ENGLAND:
        events += end_turn(state)
    assert any(isinstance(e, ev.Desertion) and e.army == army for e in events)
    assert state.armies[army].force.total() == before - 1


def test_a_last_regiment_deserting_removes_the_army_entirely():
    state = new_game(42)
    state.factions[ENGLAND].treasury = -10_000
    army = ArmyId(0)
    state.armies[army].force = Force.of_melee(1)
    events = end_turn(state)
    while state.current != ENGLAND:
        events += end_turn(state)
    assert army not in state.armies
    assert any(
        isinstance(e, ev.Desertion) and e.army == army and e.remaining == 0
        for e in events
    )


# -------- victory --------


def test_holding_win_provinces_wins_the_campaign():
    state = new_game(42)
    # Hand England everything up to the threshold; the check fires on capture.
    target = balance().win_provinces
    for p in range(target - 1):
        state.provinces[p].owner = ENGLAND
    events: list = []
    rules.capture(state, ProvinceId(target - 1), ENGLAND, events)
    assert state.winner == ENGLAND
    assert any(isinstance(e, ev.GameWon) and e.faction == ENGLAND for e in events)
    # Nothing more can be commanded once the campaign is over.
    with pytest.raises(RuleError) as e:
        apply(state, cmds.Recruit(province=ProvinceId(0), unit=UnitType.MELEE))
    assert e.value.rule is Rule.GAME_OVER


def test_being_the_last_faction_standing_also_wins():
    state = new_game(42)
    for f in (SCOTLAND, WALES):
        rules.destroy_faction(state, f, [])
    events: list = []
    rules.destroy_faction(state, state.find_faction("Ireland"), events)
    rules.check_winner(state, events)
    assert state.winner == ENGLAND


# -------- siege bookkeeping --------


def test_marching_away_lifts_your_own_siege():
    state = new_game(42)
    army = ArmyId(0)
    state.armies[army].location = ProvinceId(4)  # rebel Chester
    apply(state, cmds.Besiege(army=army))
    assert state.provinces[4].siege is not None
    state.armies[army].mp = 1
    events = apply(state, cmds.Move(army=army, to=ProvinceId(1)))  # back to York
    assert state.provinces[4].siege is None
    assert any(isinstance(e, ev.SiegeLifted) for e in events)


def test_a_second_faction_cannot_contest_an_existing_siege():
    state = new_game(42)
    state.provinces[4].siege = Siege(by=SCOTLAND, turns=1)
    army = ArmyId(0)
    state.armies[army].location = ProvinceId(4)
    with pytest.raises(RuleError) as e:
        apply(state, cmds.Besiege(army=army))
    assert e.value.rule is Rule.SIEGE_CONTESTED
