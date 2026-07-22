// The seam the whole migration is built around.
//
// Gameplay code talks to this interface and never learns that a simulation turn
// is a socket round-trip to a Python process. The plan calls for an embedded
// CPython implementation later; when that lands it implements these four
// methods and nothing above this file changes.
//
// The granularity is the point: four calls, none of them per-object and none of
// them per-frame. If a new method here would be called from Tick, it is the
// wrong method.

#pragma once

#include "CoreMinimal.h"

#include "SimTypes.h"

class ISimTransport
{
public:
    virtual ~ISimTransport() = default;

    /// Start (or attach to) a simulation and load `Campaign` at `Seed`. Returns
    /// false only on a transport failure; the snapshot is available from
    /// `GetSnapshot` immediately afterwards.
    virtual bool Initialize(const FString& Campaign, int32 Seed) = 0;

    /// Apply one player command. A rejected command is `Out.bOk == false` with
    /// the rule name, and is *not* a transport failure — the return value is
    /// false only when the connection itself broke.
    virtual bool SendCommand(const FSimCommand& Cmd, FSimResult& Out) = 0;

    /// Resolve the player's turn and every AI faction's, in one call.
    virtual bool EndTurn(FSimResult& Out) = 0;

    /// The world as of the last reply. Never a round-trip — read it freely.
    virtual const FWorldSnapshot& GetSnapshot() const = 0;

    /// Why the last call returned false. Empty while healthy.
    virtual const FString& GetTransportError() const = 0;
};
