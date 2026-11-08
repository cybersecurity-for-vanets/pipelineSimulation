#include "CamFieldCipher.h"

#include <stdexcept>
#include <vector>

namespace pipelineSimulation {
namespace crypto {

namespace {

using u128 = unsigned __int128;

constexpr int64_t LATITUDE_OFFSET = 900000000;
constexpr int64_t LONGITUDE_OFFSET = 1800000000;
constexpr uint64_t STATION_RANGE = 4294967296ULL;
constexpr uint64_t LATITUDE_RANGE = 1800000002ULL;
constexpr uint64_t LONGITUDE_RANGE = 3600000002ULL;
constexpr uint64_t SPEED_RANGE = 16384;
constexpr uint64_t HEADING_RANGE = 3602;

constexpr u128 DOMAIN_SIZE =
    static_cast<u128>(STATION_RANGE) * LATITUDE_RANGE * LONGITUDE_RANGE * SPEED_RANGE * HEADING_RANGE;

u128 pack(const CamSensitiveFields& fields)
{
    u128 value = fields.stationId;
    value = value * LATITUDE_RANGE + static_cast<uint64_t>(fields.latitude + LATITUDE_OFFSET);
    value = value * LONGITUDE_RANGE + static_cast<uint64_t>(fields.longitude + LONGITUDE_OFFSET);
    value = value * SPEED_RANGE + fields.speed;
    value = value * HEADING_RANGE + fields.heading;
    return value;
}

CamSensitiveFields unpack(u128 value)
{
    CamSensitiveFields fields;
    fields.heading = static_cast<uint16_t>(value % HEADING_RANGE);
    value /= HEADING_RANGE;
    fields.speed = static_cast<uint16_t>(value % SPEED_RANGE);
    value /= SPEED_RANGE;
    fields.longitude = static_cast<int32_t>(static_cast<int64_t>(value % LONGITUDE_RANGE) - LONGITUDE_OFFSET);
    value /= LONGITUDE_RANGE;
    fields.latitude = static_cast<int32_t>(static_cast<int64_t>(value % LATITUDE_RANGE) - LATITUDE_OFFSET);
    value /= LATITUDE_RANGE;
    fields.stationId = static_cast<uint32_t>(value);
    return fields;
}

std::vector<uint32_t> toBits(u128 value)
{
    std::vector<uint32_t> bits(CamFieldCipher::DOMAIN_BITS);
    for (int i = CamFieldCipher::DOMAIN_BITS - 1; i >= 0; --i) {
        bits[i] = static_cast<uint32_t>(value & 1);
        value >>= 1;
    }
    return bits;
}

u128 fromBits(const std::vector<uint32_t>& bits)
{
    u128 value = 0;
    for (uint32_t bit : bits) value = (value << 1) | bit;
    return value;
}

} // namespace

static_assert(DOMAIN_SIZE <= (static_cast<u128>(1) << CamFieldCipher::DOMAIN_BITS), "CAM field domain exceeds FF1 domain");
static_assert(DOMAIN_SIZE > (static_cast<u128>(1) << (CamFieldCipher::DOMAIN_BITS - 1)), "FF1 domain wider than needed");

CamFieldCipher::CamFieldCipher(const BlockCipher& cipher) : ff1(cipher, 2)
{
}

bool CamFieldCipher::isValid(const CamSensitiveFields& fields)
{
    return fields.latitude >= -LATITUDE_OFFSET && fields.latitude <= 900000001 &&
        fields.longitude >= -LONGITUDE_OFFSET && fields.longitude <= 1800000001 &&
        fields.speed < SPEED_RANGE && fields.heading < HEADING_RANGE;
}

CamSensitiveFields CamFieldCipher::walk(const CamSensitiveFields& fields, uint16_t tweak, bool encrypting, int* rounds) const
{
    if (!isValid(fields)) throw std::invalid_argument("CAM field outside its ETSI range");

    const std::vector<uint8_t> tweakBytes = {static_cast<uint8_t>(tweak >> 8), static_cast<uint8_t>(tweak)};
    u128 value = pack(fields);
    int count = 0;
    // Cycle-walking: FF1 permutes [0, 2^121); repeat until the value is back in the field domain.
    do {
        const auto bits = toBits(value);
        value = fromBits(encrypting ? ff1.encrypt(tweakBytes, bits) : ff1.decrypt(tweakBytes, bits));
        ++count;
    } while (value >= DOMAIN_SIZE);

    if (rounds) *rounds = count;
    return unpack(value);
}

CamSensitiveFields CamFieldCipher::encrypt(const CamSensitiveFields& fields, uint16_t tweak, int* rounds) const
{
    return walk(fields, tweak, true, rounds);
}

CamSensitiveFields CamFieldCipher::decrypt(const CamSensitiveFields& fields, uint16_t tweak, int* rounds) const
{
    return walk(fields, tweak, false, rounds);
}

} // namespace crypto
} // namespace pipelineSimulation
