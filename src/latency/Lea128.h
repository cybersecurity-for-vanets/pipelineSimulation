#pragma once

#include "BlockCipher.h"

namespace pipelineSimulation {
namespace crypto {

// LEA-128 (KISA, ISO/IEC 29192-2): 128-bit key, 24 rounds.
class Lea128 : public BlockCipher {
public:
    explicit Lea128(const uint8_t key[16]);
    void encryptBlock(const uint8_t in[BLOCK_BYTES], uint8_t out[BLOCK_BYTES]) const override;

private:
    uint32_t roundKeys[24][6];
};

} // namespace crypto
} // namespace pipelineSimulation
