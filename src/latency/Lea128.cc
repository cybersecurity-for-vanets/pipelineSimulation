#include "Lea128.h"

namespace pipelineSimulation {
namespace crypto {

namespace {

constexpr uint32_t DELTA[4] = {0xc3efe9db, 0x44626b02, 0x79e27c8a, 0x78df30ec};

uint32_t rol(uint32_t value, int shift)
{
    shift &= 31;
    return shift ? (value << shift) | (value >> (32 - shift)) : value;
}

uint32_t ror(uint32_t value, int shift)
{
    return rol(value, 32 - (shift & 31));
}

uint32_t loadLe32(const uint8_t* bytes)
{
    return static_cast<uint32_t>(bytes[0]) | static_cast<uint32_t>(bytes[1]) << 8 |
        static_cast<uint32_t>(bytes[2]) << 16 | static_cast<uint32_t>(bytes[3]) << 24;
}

void storeLe32(uint32_t value, uint8_t* bytes)
{
    for (int i = 0; i < 4; ++i) bytes[i] = static_cast<uint8_t>(value >> (8 * i));
}

} // namespace

Lea128::Lea128(const uint8_t key[16])
{
    uint32_t t[4];
    for (int i = 0; i < 4; ++i) t[i] = loadLe32(key + 4 * i);

    for (int round = 0; round < 24; ++round) {
        const uint32_t delta = DELTA[round % 4];
        t[0] = rol(t[0] + rol(delta, round), 1);
        t[1] = rol(t[1] + rol(delta, round + 1), 3);
        t[2] = rol(t[2] + rol(delta, round + 2), 6);
        t[3] = rol(t[3] + rol(delta, round + 3), 11);
        const uint32_t keys[6] = {t[0], t[1], t[2], t[1], t[3], t[1]};
        for (int i = 0; i < 6; ++i) roundKeys[round][i] = keys[i];
    }
}

void Lea128::encryptBlock(const uint8_t in[BLOCK_BYTES], uint8_t out[BLOCK_BYTES]) const
{
    uint32_t x[4];
    for (int i = 0; i < 4; ++i) x[i] = loadLe32(in + 4 * i);

    for (const auto& rk : roundKeys) {
        const uint32_t next0 = rol((x[0] ^ rk[0]) + (x[1] ^ rk[1]), 9);
        const uint32_t next1 = ror((x[1] ^ rk[2]) + (x[2] ^ rk[3]), 5);
        const uint32_t next2 = ror((x[2] ^ rk[4]) + (x[3] ^ rk[5]), 3);
        x[3] = x[0];
        x[0] = next0;
        x[1] = next1;
        x[2] = next2;
    }
    for (int i = 0; i < 4; ++i) storeLe32(x[i], out + 4 * i);
}

} // namespace crypto
} // namespace pipelineSimulation
