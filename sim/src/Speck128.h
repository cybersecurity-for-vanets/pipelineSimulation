#pragma once

#include "BlockCipher.h"

namespace pipelineSimulation {
namespace crypto {

// SPECK-128/128 (Beaulieu et al., 2013): 64-bit words, 32 rounds.
// Byte layout: block = y || x and key = k0 || l0, each word little-endian.
class Speck128 : public BlockCipher {
public:
    explicit Speck128(const uint8_t key[16]);
    void encryptBlock(const uint8_t in[BLOCK_BYTES], uint8_t out[BLOCK_BYTES]) const override;
    void encryptWords(uint64_t& x, uint64_t& y) const;

private:
    uint64_t roundKeys[32];
};

} // namespace crypto
} // namespace pipelineSimulation
