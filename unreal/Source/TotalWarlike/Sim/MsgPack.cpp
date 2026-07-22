#include "MsgPack.h"

#include <cstring>

namespace tw
{
namespace
{

/// msgpack is big-endian throughout.
template <typename T>
T ReadBE(const uint8_t* Data)
{
    T Out = 0;
    for (std::size_t i = 0; i < sizeof(T); ++i)
    {
        Out = static_cast<T>((Out << 8) | Data[i]);
    }
    return Out;
}

template <typename T>
void WriteBE(std::vector<uint8_t>& Out, T Value)
{
    for (std::size_t i = sizeof(T); i-- > 0;)
    {
        Out.push_back(static_cast<uint8_t>((Value >> (i * 8)) & 0xFF));
    }
}

/// A cursor over the frame. Every read is bounds-checked in one place, so the
/// decoder below can be a flat switch with no length arithmetic in it.
class FReader
{
public:
    FReader(const uint8_t* InData, std::size_t InSize) : Data(InData), Size(InSize) {}

    uint8_t Byte() { return *Take(1); }

    const uint8_t* Take(std::size_t Count)
    {
        if (Pos + Count > Size)
        {
            throw FMsgPackError("truncated msgpack frame");
        }
        const uint8_t* At = Data + Pos;
        Pos += Count;
        return At;
    }

    template <typename T>
    T Int()
    {
        return ReadBE<T>(Take(sizeof(T)));
    }

    std::size_t Remaining() const { return Size - Pos; }

private:
    const uint8_t* Data;
    std::size_t Size;
    std::size_t Pos = 0;
};

std::string TypeName(FValue::EType Type)
{
    switch (Type)
    {
    case FValue::EType::Nil: return "nil";
    case FValue::EType::Bool: return "bool";
    case FValue::EType::Int: return "int";
    case FValue::EType::Double: return "double";
    case FValue::EType::Str: return "str";
    case FValue::EType::Bin: return "bin";
    case FValue::EType::Array: return "array";
    case FValue::EType::Map: return "map";
    }
    return "?";
}

[[noreturn]] void WrongType(FValue::EType Got, const char* Wanted)
{
    throw FMsgPackError("expected " + std::string(Wanted) + ", got " + TypeName(Got));
}

FValue ReadValue(FReader& In);

FValue ReadStr(FReader& In, std::size_t Count)
{
    const uint8_t* Bytes = In.Take(Count);
    return FValue(std::string(reinterpret_cast<const char*>(Bytes), Count));
}

FValue ReadArray(FReader& In, std::size_t Count)
{
    FValue::FArray Out;
    Out.reserve(Count);
    for (std::size_t i = 0; i < Count; ++i)
    {
        Out.push_back(ReadValue(In));
    }
    return FValue(std::move(Out));
}

FValue ReadMap(FReader& In, std::size_t Count)
{
    FValue::FMap Out;
    for (std::size_t i = 0; i < Count; ++i)
    {
        FValue Key = ReadValue(In);
        // The protocol is all string keys. `strict_map_key=False` on the Python
        // side is about what it will *accept*, not what it sends.
        if (Key.GetType() != FValue::EType::Str)
        {
            throw FMsgPackError("map key is " + TypeName(Key.GetType()) + ", not str");
        }
        Out.emplace(Key.AsStr(), ReadValue(In));
    }
    return FValue(std::move(Out));
}

FValue ReadValue(FReader& In)
{
    const uint8_t Tag = In.Byte();

    if (Tag <= 0x7F) return FValue(static_cast<int64_t>(Tag));            // positive fixint
    if (Tag >= 0xE0) return FValue(static_cast<int64_t>(static_cast<int8_t>(Tag))); // negative fixint
    if ((Tag & 0xF0) == 0x80) return ReadMap(In, Tag & 0x0F);             // fixmap
    if ((Tag & 0xF0) == 0x90) return ReadArray(In, Tag & 0x0F);           // fixarray
    if ((Tag & 0xE0) == 0xA0) return ReadStr(In, Tag & 0x1F);             // fixstr

    switch (Tag)
    {
    case 0xC0: return FValue();
    case 0xC2: return FValue(false);
    case 0xC3: return FValue(true);

    case 0xC4: return FValue::MakeBin([&] { const std::size_t N = In.Int<uint8_t>();  const uint8_t* B = In.Take(N); return std::vector<uint8_t>(B, B + N); }());
    case 0xC5: return FValue::MakeBin([&] { const std::size_t N = In.Int<uint16_t>(); const uint8_t* B = In.Take(N); return std::vector<uint8_t>(B, B + N); }());
    case 0xC6: return FValue::MakeBin([&] { const std::size_t N = In.Int<uint32_t>(); const uint8_t* B = In.Take(N); return std::vector<uint8_t>(B, B + N); }());

    case 0xCA:
    {
        const uint32_t Bits = In.Int<uint32_t>();
        float F;
        std::memcpy(&F, &Bits, sizeof(F));
        return FValue(static_cast<double>(F));
    }
    case 0xCB:
    {
        const uint64_t Bits = In.Int<uint64_t>();
        double D;
        std::memcpy(&D, &Bits, sizeof(D));
        return FValue(D);
    }

    case 0xCC: return FValue(static_cast<int64_t>(In.Int<uint8_t>()));
    case 0xCD: return FValue(static_cast<int64_t>(In.Int<uint16_t>()));
    case 0xCE: return FValue(static_cast<int64_t>(In.Int<uint32_t>()));
    case 0xCF:
    {
        const uint64_t Raw = In.Int<uint64_t>();
        if (Raw > static_cast<uint64_t>(INT64_MAX))
        {
            throw FMsgPackError("uint64 too large for int64");
        }
        return FValue(static_cast<int64_t>(Raw));
    }
    case 0xD0: return FValue(static_cast<int64_t>(static_cast<int8_t>(In.Int<uint8_t>())));
    case 0xD1: return FValue(static_cast<int64_t>(static_cast<int16_t>(In.Int<uint16_t>())));
    case 0xD2: return FValue(static_cast<int64_t>(static_cast<int32_t>(In.Int<uint32_t>())));
    case 0xD3: return FValue(static_cast<int64_t>(In.Int<uint64_t>()));

    case 0xD9: return ReadStr(In, In.Int<uint8_t>());
    case 0xDA: return ReadStr(In, In.Int<uint16_t>());
    case 0xDB: return ReadStr(In, In.Int<uint32_t>());

    case 0xDC: return ReadArray(In, In.Int<uint16_t>());
    case 0xDD: return ReadArray(In, In.Int<uint32_t>());
    case 0xDE: return ReadMap(In, In.Int<uint16_t>());
    case 0xDF: return ReadMap(In, In.Int<uint32_t>());

    default:
        // ext types (0xC7-0xC9, 0xD4-0xD8) and the unused 0xC1. The sidecar
        // cannot produce these, so seeing one means we are out of sync with the
        // stream rather than reading a value we merely do not support.
        throw FMsgPackError("unsupported msgpack tag " + std::to_string(Tag));
    }
}

void PackInto(std::vector<uint8_t>& Out, const FValue& Value);

void PackInt(std::vector<uint8_t>& Out, int64_t Value)
{
    if (Value >= 0)
    {
        if (Value <= 0x7F)              { Out.push_back(static_cast<uint8_t>(Value)); }
        else if (Value <= UINT8_MAX)    { Out.push_back(0xCC); WriteBE<uint8_t>(Out, static_cast<uint8_t>(Value)); }
        else if (Value <= UINT16_MAX)   { Out.push_back(0xCD); WriteBE<uint16_t>(Out, static_cast<uint16_t>(Value)); }
        else if (Value <= UINT32_MAX)   { Out.push_back(0xCE); WriteBE<uint32_t>(Out, static_cast<uint32_t>(Value)); }
        else                            { Out.push_back(0xCF); WriteBE<uint64_t>(Out, static_cast<uint64_t>(Value)); }
    }
    else
    {
        if (Value >= -32)               { Out.push_back(static_cast<uint8_t>(Value)); }
        else if (Value >= INT8_MIN)     { Out.push_back(0xD0); WriteBE<uint8_t>(Out, static_cast<uint8_t>(Value)); }
        else if (Value >= INT16_MIN)    { Out.push_back(0xD1); WriteBE<uint16_t>(Out, static_cast<uint16_t>(Value)); }
        else if (Value >= INT32_MIN)    { Out.push_back(0xD2); WriteBE<uint32_t>(Out, static_cast<uint32_t>(Value)); }
        else                            { Out.push_back(0xD3); WriteBE<uint64_t>(Out, static_cast<uint64_t>(Value)); }
    }
}

void PackStr(std::vector<uint8_t>& Out, const std::string& Str)
{
    const std::size_t N = Str.size();
    if (N < 32)                 { Out.push_back(static_cast<uint8_t>(0xA0 | N)); }
    else if (N <= UINT8_MAX)    { Out.push_back(0xD9); WriteBE<uint8_t>(Out, static_cast<uint8_t>(N)); }
    else if (N <= UINT16_MAX)   { Out.push_back(0xDA); WriteBE<uint16_t>(Out, static_cast<uint16_t>(N)); }
    else                        { Out.push_back(0xDB); WriteBE<uint32_t>(Out, static_cast<uint32_t>(N)); }
    Out.insert(Out.end(), Str.begin(), Str.end());
}

void PackInto(std::vector<uint8_t>& Out, const FValue& Value)
{
    switch (Value.GetType())
    {
    case FValue::EType::Nil:
        Out.push_back(0xC0);
        break;
    case FValue::EType::Bool:
        Out.push_back(Value.AsBool() ? 0xC3 : 0xC2);
        break;
    case FValue::EType::Int:
        PackInt(Out, Value.AsInt());
        break;
    case FValue::EType::Double:
    {
        Out.push_back(0xCB);
        uint64_t Bits;
        const double D = Value.AsDouble();
        std::memcpy(&Bits, &D, sizeof(Bits));
        WriteBE<uint64_t>(Out, Bits);
        break;
    }
    case FValue::EType::Str:
        PackStr(Out, Value.AsStr());
        break;
    case FValue::EType::Bin:
    {
        const std::vector<uint8_t>& Bytes = Value.AsBin();
        Out.push_back(0xC6);
        WriteBE<uint32_t>(Out, static_cast<uint32_t>(Bytes.size()));
        Out.insert(Out.end(), Bytes.begin(), Bytes.end());
        break;
    }
    case FValue::EType::Array:
    {
        const FValue::FArray& Items = Value.AsArray();
        const std::size_t N = Items.size();
        if (N < 16)                 { Out.push_back(static_cast<uint8_t>(0x90 | N)); }
        else if (N <= UINT16_MAX)   { Out.push_back(0xDC); WriteBE<uint16_t>(Out, static_cast<uint16_t>(N)); }
        else                        { Out.push_back(0xDD); WriteBE<uint32_t>(Out, static_cast<uint32_t>(N)); }
        for (const FValue& Item : Items)
        {
            PackInto(Out, Item);
        }
        break;
    }
    case FValue::EType::Map:
    {
        const FValue::FMap& Entries = Value.AsMap();
        const std::size_t N = Entries.size();
        if (N < 16)                 { Out.push_back(static_cast<uint8_t>(0x80 | N)); }
        else if (N <= UINT16_MAX)   { Out.push_back(0xDE); WriteBE<uint16_t>(Out, static_cast<uint16_t>(N)); }
        else                        { Out.push_back(0xDF); WriteBE<uint32_t>(Out, static_cast<uint32_t>(N)); }
        for (const auto& [Key, Item] : Entries)
        {
            PackStr(Out, Key);
            PackInto(Out, Item);
        }
        break;
    }
    }
}

} // namespace

FValue FValue::MakeBin(std::vector<uint8_t> In)
{
    FValue Out;
    Out.Type = EType::Bin;
    Out.BinData = std::move(In);
    return Out;
}

bool FValue::AsBool() const
{
    if (Type != EType::Bool) WrongType(Type, "bool");
    return Bool;
}

int64_t FValue::AsInt() const
{
    if (Type != EType::Int) WrongType(Type, "int");
    return Int;
}

double FValue::AsDouble() const
{
    // An integer where a number is wanted is not a disagreement about the
    // protocol — msgpack encodes 3.0 as an int — so widening is right here.
    if (Type == EType::Int) return static_cast<double>(Int);
    if (Type != EType::Double) WrongType(Type, "double");
    return Double;
}

const std::string& FValue::AsStr() const
{
    if (Type != EType::Str) WrongType(Type, "str");
    return Str;
}

const std::vector<uint8_t>& FValue::AsBin() const
{
    if (Type != EType::Bin) WrongType(Type, "bin");
    return BinData;
}

const FValue::FArray& FValue::AsArray() const
{
    if (Type != EType::Array) WrongType(Type, "array");
    return Array;
}

const FValue::FMap& FValue::AsMap() const
{
    if (Type != EType::Map) WrongType(Type, "map");
    return Map;
}

const FValue* FValue::Find(const std::string& Key) const
{
    if (Type != EType::Map) WrongType(Type, "map");
    const auto It = Map.find(Key);
    return It == Map.end() ? nullptr : &It->second;
}

const FValue& FValue::At(const std::string& Key) const
{
    if (const FValue* Found = Find(Key))
    {
        return *Found;
    }
    throw FMsgPackError("missing key '" + Key + "'");
}

int64_t FValue::IntOr(const std::string& Key, int64_t Fallback) const
{
    const FValue* Found = Find(Key);
    return (Found == nullptr || Found->IsNil()) ? Fallback : Found->AsInt();
}

std::vector<uint8_t> Pack(const FValue& Value)
{
    std::vector<uint8_t> Out;
    PackInto(Out, Value);
    return Out;
}

FValue Unpack(const std::vector<uint8_t>& Bytes)
{
    FReader In(Bytes.data(), Bytes.size());
    FValue Out = ReadValue(In);
    if (In.Remaining() != 0)
    {
        throw FMsgPackError(std::to_string(In.Remaining()) + " trailing bytes after message");
    }
    return Out;
}

} // namespace tw
