// Translation between the msgpack documents on the socket and the USTRUCTs the
// game reads. The one place in the bridge that knows both vocabularies.
//
// Kept separate from the transport so it can be exercised without a socket, and
// separate from SimTypes.h so those stay plain data.

#pragma once

#include "CoreMinimal.h"

#include "MsgPack.h"
#include "SimTypes.h"

namespace tw
{

/// Build the msgpack request body for each op. The `op` names are the four in
/// `sim/src/tw_sim/server.py`; there is no fifth.
FValue MakeInitRequest(const FString& Campaign, int32 Seed);
FValue MakeCommandRequest(const FSimCommand& Cmd);
FValue MakeEndTurnRequest();
FValue MakeSnapshotRequest();

/// Decode a `{"ok": ...}` reply into a result. `events` and `snapshot` are both
/// optional in the general case — an `ok: false` reply carries neither.
///
/// `OutSnapshot` is only written when the reply actually contained one, so a
/// caller can pass its cached snapshot and have it left alone on failure.
FSimResult ParseResult(const FValue& Reply, FWorldSnapshot& OutSnapshot);

/// Exposed for tests; ParseResult calls it when a `snapshot` key is present.
FWorldSnapshot ParseSnapshot(const FValue& Snapshot);
FSimEvent ParseEvent(const FValue& Event);

} // namespace tw
