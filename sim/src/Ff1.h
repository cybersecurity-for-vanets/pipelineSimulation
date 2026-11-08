#pragma once

#include <cstdint>
#include <vector>

#include "BlockCipher.h"

namespace pipelineSimulation {
namespace crypto {

// FF1 format-preserving encryption (NIST SP 800-38G) over any 128-bit block cipher.
// Numerals are values in [0, radix); the ciphertext has the same length as the plaintext.
class Ff1 {
public:
    Ff1(const BlockCipher& cipher, uint32_t radix);

    std::vector<uint32_t> encrypt(const std::vector<uint8_t>& tweak, const std::vector<uint32_t>& numerals) const;
    std::vector<uint32_t> decrypt(const std::vector<uint8_t>& tweak, const std::vector<uint32_t>& numerals) const;

    // Convenience for radix 256: bytes are the numerals.
    std::vector<uint8_t> encryptBytes(const std::vector<uint8_t>& tweak, const std::vector<uint8_t>& plaintext) const;
    std::vector<uint8_t> decryptBytes(const std::vector<uint8_t>& tweak, const std::vector<uint8_t>& ciphertext) const;

private:
    const BlockCipher& cipher;
    uint32_t radix;

    std::vector<uint32_t> crypt(const std::vector<uint8_t>& tweak, const std::vector<uint32_t>& numerals, bool encrypting) const;
};

} // namespace crypto
} // namespace pipelineSimulation
