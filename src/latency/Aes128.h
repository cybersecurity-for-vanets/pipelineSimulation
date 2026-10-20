#pragma once

#include "BlockCipher.h"

namespace pipelineSimulation {
namespace crypto {

// Portable AES-128 (FIPS-197), table-free except for the S-box; no hardware acceleration.
class Aes128 : public BlockCipher {
public:
    explicit Aes128(const uint8_t key[16]);
    void encryptBlock(const uint8_t in[BLOCK_BYTES], uint8_t out[BLOCK_BYTES]) const override;

private:
    uint8_t roundKeys[176];
};

} // namespace crypto
} // namespace pipelineSimulation
