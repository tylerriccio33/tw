// The event feed's sentences, ported from `Main.gd:_feed_line_for` and
// `_event_color`.
//
// These strings are the only part of the Godot HUD that milestone 1 keeps, and
// they are worth keeping verbatim: they are the readable half of the 22-variant
// event set, and rewriting them would mean rewriting the campaign's voice.
//
// Events with no line ("recruit_queued", "garrisoned", a partial desertion, ...)
// return an empty string, exactly as the GDScript did. That is not an oversight
// — the feed is a narrative, not a log.

#pragma once

#include "CoreMinimal.h"

#include "../Sim/SimTypes.h"

namespace tw
{

/// One line for the feed, or empty if this event is not worth narrating.
/// Needs the snapshot for faction and province names.
FString FeedLineFor(const FSimEvent& Event, const FWorldSnapshot& Snapshot);

/// Which faction's colour tints the line; white for events with no single owner.
/// `PaletteFor` is how the caller turns a FactionId into a colour — the palette
/// lives in the baked map data, which this file deliberately does not know about.
FLinearColor FeedColorFor(const FSimEvent& Event, TFunctionRef<FLinearColor(int32)> PaletteFor);

} // namespace tw
