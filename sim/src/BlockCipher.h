#pragma once

#include <cstdint>
#include <memory>
#include <string>

namespace pipelineSimulation {
namespace crypto {

constexpr int BLOCK_BYTES = 16;

// 128-bit block cipher used as the FF1 PRF. FF1 only needs the forward direction.
class BlockCipher {
public:
    virtual ~BlockCipher() = default;
    virtual void encryptBlock(const uint8_t in[BLOCK_BYTES], uint8_t out[BLOCK_BYTES]) const = 0;
};

// name: "aes", "lea" or "speck"; key: 16 bytes.
std::unique_ptr<BlockCipher> makeBlockCipher(const std::string& name, const uint8_t key[16]);

} // namespace crypto
} // namespace pipelineSimulation
