"""Port of `engine/tests/campaign.rs`.

Eleven of the thirteen Rust tests come across unchanged in intent. The two
determinism tests (`same_seed_same_campaign`, `different_seeds_diverge`) are
deliberately dropped: determinism is de-scoped for the Python simulation, which
uses `random.Random` rather than a reproducible ChaCha8 stream.
"""

from __future__ import annotations

import pytest

from tw_sim import ai, combat, command as cmds, diplomacy, economy, event as ev
from tw_sim import Army, ArmyId, Force, ProposalKind, ProvinceId, UnitType
from tw_sim import apply, end_turn, new_game
from tw_sim.config import balance
from tw_sim.data import ENGLAND, IRELAND, REBELS, SCOTLAND, WALES, general_for
from tw_sim.state import DiploStatus

# -------- combat --------


def test_ties_favor_the_defender():
    assert not combat.decide(1000, 1000)
    assert combat.decide(1001, 1000)


def test_overwhelming_attacker_always_wins():
    state = new_game(1)
    for _ in range(100):
        r = combat.resolve(state.rng, 100, 5, 100, 5, 40)
        assert r.attacker_won
        assert r.winner_losses == 3  # ceil(5/2)


def test_overwhelming_defender_always_wins():
    state = new_game(2)
    for _ in range(100):
        r = combat.resolve(state.rng, 5, 100, 5, 100, 0)
        assert not r.attacker_won


# -------- economy --------


def test_england_starting_income_matches_formula():
    state = new_game(42)
    b = balance()
    # England holds London 60, York 45, Norwich 40, Exeter 35; each with farm 1
    # (+10) and market 1 (+15); no trade. Taxation adds tax_per_thousand per
    # 1000 population: London (Capital 12000), York (City 6000), Norwich and
    # Exeter (Town 2000 each).
    tax = (12_000 + 6_000 + 2_000 + 2_000) * b.tax_per_thousand // 1000
    assert economy.faction_income(state, ENGLAND) == (
        60 + 45 + 40 + 35 + 4 * (b.farm_income + b.market_income) + tax
    )
    # Upkeep: 8-regiment army + garrisons of 5, 3, 3 and 3.
    assert economy.faction_upkeep(state, ENGLAND) == (
        8 * b.regiment_upkeep + (5 + 3 + 3 + 3) * b.garrison_upkeep
    )


def test_trade_agreement_pays_both_bordering_partners():
    state = new_game(42)
    # England (York) borders Scotland (Lothian) directly across the marches.
    before = economy.faction_income(state, SCOTLAND)
    diplomacy.enact(state, ENGLAND, SCOTLAND, ProposalKind.TRADE)
    assert economy.faction_income(state, SCOTLAND) == before + balance().trade_bonus


# -------- diplomacy --------


def test_war_drags_in_allies():
    state = new_game(42)
    # Scotland and Ireland become allies; England declares war on Scotland.
    state.set_status(SCOTLAND, IRELAND, DiploStatus.ALLIANCE)
    events = apply(state, cmds.DeclareWar(on=SCOTLAND))
    assert state.at_war(ENGLAND, SCOTLAND)
    assert state.at_war(ENGLAND, IRELAND)
    assert any(isinstance(e, ev.AllyJoinedWar) and e.ally == IRELAND for e in events)


def test_ai_accepts_trade_when_friendly_rejects_when_hated():
    state = new_game(42)
    assert diplomacy.evaluate(state, ENGLAND, SCOTLAND, ProposalKind.TRADE)
    state.shift_opinion(ENGLAND, SCOTLAND, -100)
    assert not diplomacy.evaluate(state, ENGLAND, SCOTLAND, ProposalKind.TRADE)


def test_ai_accepts_peace_when_weaker():
    state = new_game(42)
    diplomacy.declare_war(state, ENGLAND, SCOTLAND, [])
    # Make Scotland much weaker than England.
    for id in state.faction_armies(SCOTLAND):
        del state.armies[id]
    assert diplomacy.evaluate(state, ENGLAND, SCOTLAND, ProposalKind.PEACE)


# -------- conquest & elimination --------


def test_assault_captures_city_and_can_eliminate_a_faction():
    state = new_game(42)
    diplomacy.declare_war(state, ENGLAND, WALES, [])
    # A doomstack teleported to Cardiff (5), then Caernarfon (6) — Wales's two
    # provinces, so taking both eliminates the faction.
    doom = ArmyId(900)
    state.armies[doom] = Army(
        owner=ENGLAND,
        location=ProvinceId(5),
        force=Force.of_melee(99),
        mp=2,
        general=general_for(ENGLAND, doom),
    )
    apply(state, cmds.Assault(army=doom))
    assert state.provinces[5].owner == ENGLAND
    assert state.factions[WALES].alive

    state.armies[doom].location = ProvinceId(6)
    state.armies[doom].mp = 2
    events = apply(state, cmds.Assault(army=doom))
    assert state.provinces[6].owner == ENGLAND
    assert not state.factions[WALES].alive
    assert any(isinstance(e, ev.FactionDestroyed) and e.faction == WALES for e in events)
    # Wales's field army is gone with it.
    assert all(a.owner != WALES for a in state.armies.values())


def test_siege_starves_out_a_garrison():
    state = new_game(42)
    # England besieges Chester (rebel, garrison 4) with its starting army 0.
    army = ArmyId(0)
    state.armies[army].location = ProvinceId(4)
    apply(state, cmds.Besiege(army=army))
    fell = False
    for _ in range(16):
        # Loop a full round back to England each time; re-besieging is not
        # needed, the siege persists while the army stays put.
        def fell_here(events):
            return any(
                isinstance(e, ev.CityFell) and e.province == ProvinceId(4)
                for e in events
            )

        fell |= fell_here(end_turn(state))
        while state.current != ENGLAND:
            # Skip AI decision-making: just end their turns.
            fell |= fell_here(end_turn(state))
        if fell:
            break
    assert fell, "siege never starved out the garrison"
    assert state.provinces[4].owner == ENGLAND


# -------- the world keeps turning --------


def test_all_ai_campaign_runs_and_the_map_changes():
    state = new_game(123)
    state.factions[0].is_player = False
    rebel_provinces_at_start = len(state.faction_provinces(REBELS))
    for _ in range(400):
        if state.winner is not None:
            break
        ai.take_turn(state)
        end_turn(state)
    rebel_provinces_now = len(state.faction_provinces(REBELS))
    assert rebel_provinces_now < rebel_provinces_at_start or state.winner is not None, (
        "after 400 faction-turns nothing was ever conquered "
        f"(rebels still hold {rebel_provinces_now})"
    )
