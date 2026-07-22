"""Treaties, wars, and how the AI answers an offer."""

from __future__ import annotations

from . import event as ev
from .event import Event
from .ids import FactionId
from .state import DiploStatus, GameState, Proposal, ProposalKind


def declare_war(
    state: GameState, by: FactionId, on: FactionId, events: list[Event]
) -> None:
    """Declare war: breaks any treaty, tanks opinion, and drags the target's
    allies in (one level deep — allies of allies stay out)."""
    state.set_status(by, on, DiploStatus.WAR)
    state.shift_opinion(by, on, -60)
    events.append(ev.WarDeclared(by=by, on=on))
    for ally in state.living_factions():
        if ally != by and ally != on and state.allied(ally, on) and not state.at_war(ally, by):
            # An ally who is also allied to the aggressor sits it out.
            if state.allied(ally, by):
                continue
            state.set_status(ally, by, DiploStatus.WAR)
            state.shift_opinion(ally, by, -40)
            events.append(ev.AllyJoinedWar(ally=ally, against=by))
    drop_dead_proposals(state)


def enact(state: GameState, from_: FactionId, to: FactionId, kind: ProposalKind) -> None:
    """Put a signed treaty into effect."""
    match kind:
        case ProposalKind.PEACE:
            state.set_status(from_, to, DiploStatus.PEACE)
            state.shift_opinion(from_, to, 20)
        case ProposalKind.TRADE:
            state.set_status(from_, to, DiploStatus.TRADE)
            state.shift_opinion(from_, to, 20)
        case ProposalKind.ALLIANCE:
            state.set_status(from_, to, DiploStatus.ALLIANCE)
            state.shift_opinion(from_, to, 40)


def proposal_valid(
    state: GameState, from_: FactionId, to: FactionId, kind: ProposalKind
) -> bool:
    """Would the treaty even make sense given current relations?"""
    if from_ == to or not state.factions[to].alive or state.factions[to].is_rebel:
        return False
    status = state.rel(from_, to).status
    match kind:
        case ProposalKind.PEACE:
            return status == DiploStatus.WAR
        case ProposalKind.TRADE:
            return status == DiploStatus.PEACE
        case ProposalKind.ALLIANCE:
            return status in (DiploStatus.PEACE, DiploStatus.TRADE)


def evaluate(
    state: GameState, from_: FactionId, to: FactionId, kind: ProposalKind
) -> bool:
    """AI decision on an incoming proposal."""
    opinion = state.rel(from_, to).opinion
    my_str = max(state.strength(to), 1)
    their_str = max(state.strength(from_), 1)
    match kind:
        # Take peace when losing; otherwise sometimes, if not too bitter.
        case ProposalKind.PEACE:
            return my_str < their_str or (opinion > -40 and state.rng.random() < 0.3)
        case ProposalKind.TRADE:
            return opinion >= -20
        case ProposalKind.ALLIANCE:
            return opinion >= 20 and my_str * 2 >= their_str and their_str * 2 >= my_str


def propose(
    state: GameState,
    from_: FactionId,
    to: FactionId,
    kind: ProposalKind,
    events: list[Event],
) -> None:
    """Route a proposal: AI recipients answer immediately, humans get it queued."""
    if state.factions[to].is_player:
        id = state.next_proposal
        state.next_proposal += 1
        state.proposals.append(Proposal(id=id, from_=from_, to=to, kind=kind))
        events.append(ev.ProposalSent(id=id, from_=from_, to=to, treaty=kind))
    elif evaluate(state, from_, to, kind):
        enact(state, from_, to, kind)
        events.append(ev.ProposalAccepted(from_=from_, to=to, treaty=kind))
    else:
        state.shift_opinion(from_, to, -5)
        events.append(ev.ProposalRejected(from_=from_, to=to, treaty=kind))


def propose_random(
    state: GameState,
    from_: FactionId,
    to: FactionId,
    kind: ProposalKind,
    events: list[Event],
) -> bool:
    """Like `propose`, but the recipient's answer is a pure coin-flip rather
    than a considered `evaluate`. The diplomacy screen uses this: acceptance
    there is deliberately left to chance. Returns whether the treaty was signed.
    """
    accepted = state.rng.random() < 0.5
    if accepted:
        enact(state, from_, to, kind)
        events.append(ev.ProposalAccepted(from_=from_, to=to, treaty=kind))
    else:
        state.shift_opinion(from_, to, -5)
        events.append(ev.ProposalRejected(from_=from_, to=to, treaty=kind))
    return accepted


def drop_dead_proposals(state: GameState) -> None:
    """Remove queued proposals that stopped making sense (e.g. war broke out)."""
    state.proposals = [
        p for p in state.proposals if proposal_valid(state, p.from_, p.to, p.kind)
    ]
