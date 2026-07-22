// A msgpack subset, sized to exactly what the sidecar speaks.
//
// Unreal ships no msgpack, and the alternative — a third-party module in
// Source/ThirdParty — is a lot of build plumbing for a format whose decoder is
// a switch over one byte. Only the types `sim/src/tw_sim/server.py` can produce
// are handled: nil, bool, ints, doubles, strings, binary, arrays and maps.
// Anything else raises rather than being skipped, because a tag we did not
// expect means the two sides disagree about the protocol, which is a bug to
// surface loudly.
//
// Deliberately free of Unreal types, like Map/ProvinceLookup.h: this is the
// half of the bridge that can be compiled and tested on its own (see
// Tests/wire_test.cpp and `make cpp-test`), which is worth a great deal when
// the alternative is a full editor build per iteration.

#pragma once

#include <cstdint>
#include <map>
#include <stdexcept>
#include <string>
#include <vector>

namespace tw
{

/// Thrown on any malformed or unsupported input. Callers at the socket layer
/// turn this into a transport error; there is no partial-decode recovery.
struct FMsgPackError : std::runtime_error
{
    explicit FMsgPackError(const std::string& What) : std::runtime_error(What) {}
};

/// A decoded msgpack document.
///
/// Integers keep their signedness (`Int`) and doubles are separate, because the
/// snapshot mixes ids, counts and — through `proposals` — nothing else; there is
/// no float in the protocol today, but Python could start emitting one and
/// silently truncating it would be worse than carrying the field.
class FValue
{
public:
    enum class EType : uint8_t { Nil, Bool, Int, Double, Str, Bin, Array, Map };

    using FArray = std::vector<FValue>;
    using FMap = std::map<std::string, FValue>;

    FValue() = default;
    FValue(bool In) : Type(EType::Bool), Bool(In) {}
    FValue(int64_t In) : Type(EType::Int), Int(In) {}
    FValue(int In) : Type(EType::Int), Int(In) {}
    FValue(double In) : Type(EType::Double), Double(In) {}
    FValue(std::string In) : Type(EType::Str), Str(std::move(In)) {}
    FValue(const char* In) : Type(EType::Str), Str(In) {}
    FValue(FArray In) : Type(EType::Array), Array(std::move(In)) {}
    FValue(FMap In) : Type(EType::Map), Map(std::move(In)) {}

    /// Binary is decode-only — nothing the client sends is a byte blob — so it
    /// gets a named factory rather than a constructor that could be picked by
    /// accident.
    static FValue MakeBin(std::vector<uint8_t> In);

    EType GetType() const { return Type; }
    bool IsNil() const { return Type == EType::Nil; }

    /// Typed accessors. Each throws `FMsgPackError` on a type mismatch, so a
    /// decoder can read a message as a straight sequence of expectations
    /// without a conditional per field.
    bool AsBool() const;
    int64_t AsInt() const;
    double AsDouble() const;
    const std::string& AsStr() const;
    const std::vector<uint8_t>& AsBin() const;
    const FArray& AsArray() const;
    const FMap& AsMap() const;

    /// Map lookup by key. `At` throws when the key is absent; `Find` returns
    /// null, for the handful of genuinely optional fields.
    const FValue& At(const std::string& Key) const;
    const FValue* Find(const std::string& Key) const;

    /// `At(Key)` coerced, with the nil case folded in: `winner`, `construction`
    /// and `besieged_by` all arrive as either a value or nil.
    int64_t IntOr(const std::string& Key, int64_t Fallback) const;

private:
    EType Type = EType::Nil;
    bool Bool = false;
    int64_t Int = 0;
    double Double = 0.0;
    std::string Str;
    std::vector<uint8_t> BinData;
    FArray Array;
    FMap Map;
};

/// Encode one document. Only the tags a request needs are emitted (the client
/// sends nothing but maps of string to int/string/map), and integers always
/// take their shortest form so frames stay small.
std::vector<uint8_t> Pack(const FValue& Value);

/// Decode exactly one document from `Bytes`. Trailing bytes are an error: every
/// frame carries one message, and leftovers mean the length prefix lied.
FValue Unpack(const std::vector<uint8_t>& Bytes);

} // namespace tw
