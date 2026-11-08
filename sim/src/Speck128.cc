#include "Speck128.h"

namespace pipelineSimulation {
namespace crypto {

namespace {

uint64_t rol(uint64_t value, int shift)
{
    return (value << shift) | (value >> (64 - shift));
}

uint64_t ror(uint64_t value, int shift)
{
    return (value >> shift) | (value << (64 - shift));
}

uint64_t loadLe64(const uint8_t* bytes)
{
    uint64_t value = 0;
    for (int i = 7; i >= 0; --i) value = (value << 8) | bytes[i];
    return value;
}

void storeLe64(uint64_t value, uint8_t* bytes)
{
    for (int i = 0; i < 8; ++i) bytes[i] = static_cast<uint8_t>(value >> (8 * i));
}

void speckRound(uint64_t& x, uint64_t& y, uint64_t key)
{
    x = (ror(x, 8) + y) ^ key;
    y = rol(y, 3) ^ x;
}

} // namespace

Speck128::Speck128(const uint8_t key[16])
{
    uint64_t k = loadLe64(key);
    uint64_t l = loadLe64(key + 8);
    for (uint64_t round = 0; round < 32; ++round) {
        roundKeys[round] = k;
        speckRound(l, k, round);
    }
}

void Speck128::encryptWords(uint64_t& x, uint64_t& y) const
{
    for (uint64_t key : roundKeys) speckRound(x, y, key);
}

void Speck128::encryptBlock(const uint8_t in[BLOCK_BYTES], uint8_t out[BLOCK_BYTES]) const
{
    uint64_t y = loadLe64(in);
    uint64_t x = loadLe64(in + 8);
    encryptWords(x, y);
    storeLe64(y, out);
    storeLe64(x, out + 8);
}

} // namespace crypto
} // namespace pipelineSimulation
