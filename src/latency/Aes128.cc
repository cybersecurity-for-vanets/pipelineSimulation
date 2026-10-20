#include "Aes128.h"

#include <cstring>

namespace pipelineSimulation {
namespace crypto {

namespace {

uint8_t xtime(uint8_t value)
{
    return static_cast<uint8_t>((value << 1) ^ ((value & 0x80) ? 0x1b : 0x00));
}

uint8_t gfMultiply(uint8_t a, uint8_t b)
{
    uint8_t product = 0;
    while (b) {
        if (b & 1) product ^= a;
        a = xtime(a);
        b >>= 1;
    }
    return product;
}

uint8_t rotl8(uint8_t value, int shift)
{
    return static_cast<uint8_t>((value << shift) | (value >> (8 - shift)));
}

// S-box built from its definition (GF(2^8) inverse + affine map) to avoid a hand-typed table.
struct SBox {
    uint8_t table[256];

    SBox()
    {
        for (int value = 0; value < 256; ++value) {
            uint8_t inverse = 0;
            for (int candidate = 1; value && candidate < 256; ++candidate) {
                if (gfMultiply(static_cast<uint8_t>(value), static_cast<uint8_t>(candidate)) == 1) {
                    inverse = static_cast<uint8_t>(candidate);
                    break;
                }
            }
            table[value] = static_cast<uint8_t>(
                inverse ^ rotl8(inverse, 1) ^ rotl8(inverse, 2) ^ rotl8(inverse, 3) ^ rotl8(inverse, 4) ^ 0x63);
        }
    }
};

const SBox& sbox()
{
    static const SBox instance;
    return instance;
}

} // namespace

Aes128::Aes128(const uint8_t key[16])
{
    const uint8_t* s = sbox().table;
    std::memcpy(roundKeys, key, 16);
    uint8_t rcon = 0x01;
    for (int offset = 16; offset < 176; offset += 4) {
        uint8_t word[4];
        std::memcpy(word, roundKeys + offset - 4, 4);
        if (offset % 16 == 0) {
            const uint8_t first = word[0];
            word[0] = static_cast<uint8_t>(s[word[1]] ^ rcon);
            word[1] = s[word[2]];
            word[2] = s[word[3]];
            word[3] = s[first];
            rcon = xtime(rcon);
        }
        for (int i = 0; i < 4; ++i) roundKeys[offset + i] = roundKeys[offset - 16 + i] ^ word[i];
    }
}

void Aes128::encryptBlock(const uint8_t in[BLOCK_BYTES], uint8_t out[BLOCK_BYTES]) const
{
    const uint8_t* s = sbox().table;
    uint8_t state[16];
    for (int i = 0; i < 16; ++i) state[i] = in[i] ^ roundKeys[i];

    for (int round = 1; round <= 10; ++round) {
        uint8_t shifted[16];
        // SubBytes + ShiftRows (state is column-major: index = 4 * column + row).
        for (int column = 0; column < 4; ++column) {
            for (int row = 0; row < 4; ++row) {
                shifted[4 * column + row] = s[state[4 * ((column + row) % 4) + row]];
            }
        }
        if (round < 10) {
            for (int column = 0; column < 4; ++column) {
                uint8_t* c = shifted + 4 * column;
                const uint8_t all = c[0] ^ c[1] ^ c[2] ^ c[3];
                const uint8_t first = c[0];
                c[0] ^= all ^ xtime(c[0] ^ c[1]);
                c[1] ^= all ^ xtime(c[1] ^ c[2]);
                c[2] ^= all ^ xtime(c[2] ^ c[3]);
                c[3] ^= all ^ xtime(c[3] ^ first);
            }
        }
        for (int i = 0; i < 16; ++i) state[i] = shifted[i] ^ roundKeys[16 * round + i];
    }
    std::memcpy(out, state, 16);
}

} // namespace crypto
} // namespace pipelineSimulation
