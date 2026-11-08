#include "Ff1.h"

#include <algorithm>
#include <cmath>
#include <cstring>
#include <stdexcept>

namespace pipelineSimulation {
namespace crypto {

namespace {

// log2(radix) when radix is a power of two, otherwise 0.
int radixBits(uint32_t radix)
{
    if (radix & (radix - 1)) return 0;
    int bits = 0;
    while ((1u << bits) < radix) ++bits;
    return bits;
}

// Writes NUM_radix(numerals) as a big-endian integer of exactly `length` bytes into `out`.
void numeralsToBytes(const uint32_t* numerals, size_t count, uint32_t radix, uint8_t* out, size_t length)
{
    std::memset(out, 0, length);
    const int bits = radixBits(radix);
    if (bits > 0) {
        // Power-of-two radix: each numeral is `bits` bits; pack them from the least significant end.
        uint64_t accumulator = 0;
        int accumulated = 0;
        size_t position = length;
        for (size_t i = count; i-- > 0;) {
            accumulator |= static_cast<uint64_t>(numerals[i]) << accumulated;
            accumulated += bits;
            while (accumulated >= 8) {
                out[--position] = static_cast<uint8_t>(accumulator);
                accumulator >>= 8;
                accumulated -= 8;
            }
        }
        if (accumulated > 0) out[--position] = static_cast<uint8_t>(accumulator);
        return;
    }
    for (size_t n = 0; n < count; ++n) {
        uint64_t carry = numerals[n];
        for (size_t i = length; i-- > 0;) {
            const uint64_t value = static_cast<uint64_t>(out[i]) * radix + carry;
            out[i] = static_cast<uint8_t>(value);
            carry = value >> 8;
        }
    }
}

// Writes the lowest `count` base-radix digits of the big-endian integer `bytes` (most significant first).
void bytesToNumerals(const uint8_t* bytes, size_t length, uint32_t radix, uint32_t* out, size_t count)
{
    std::fill(out, out + count, 0u);
    const int bits = radixBits(radix);
    if (bits > 0 && count * bits <= length * 8) {
        // Power-of-two radix: y mod radix^count is the lowest count * bits bits.
        const uint64_t mask = (1ULL << bits) - 1;
        uint64_t accumulator = 0;
        int accumulated = 0;
        size_t position = length;
        for (size_t i = count; i-- > 0;) {
            while (accumulated < bits) {
                accumulator |= static_cast<uint64_t>(bytes[--position]) << accumulated;
                accumulated += 8;
            }
            out[i] = static_cast<uint32_t>(accumulator & mask);
            accumulator >>= bits;
            accumulated -= bits;
        }
        return;
    }
    std::vector<uint8_t> value(bytes, bytes + length);
    for (size_t digit = count; digit-- > 0;) {
        uint64_t remainder = 0;
        for (uint8_t& byte : value) {
            const uint64_t current = (remainder << 8) | byte;
            byte = static_cast<uint8_t>(current / radix);
            remainder = current % radix;
        }
        out[digit] = static_cast<uint32_t>(remainder);
    }
}

void putBigEndian(uint8_t* out, uint64_t value, int bytes)
{
    for (int i = 0; i < bytes; ++i) out[i] = static_cast<uint8_t>(value >> (8 * (bytes - 1 - i)));
}

} // namespace

Ff1::Ff1(const BlockCipher& cipher, uint32_t radix) : cipher(cipher), radix(radix)
{
    if (radix < 2 || radix > (1u << 16)) throw std::invalid_argument("FF1 radix must be in [2, 2^16]");
}

std::vector<uint32_t> Ff1::crypt(const std::vector<uint8_t>& tweak, const std::vector<uint32_t>& numerals, bool encrypting) const
{
    const size_t n = numerals.size();
    if (n < 2 || std::pow(static_cast<double>(radix), static_cast<double>(n)) < 1e6) {
        throw std::invalid_argument("FF1 input too short for radix (radix^n must be >= 10^6)");
    }
    for (uint32_t numeral : numerals) {
        if (numeral >= radix) throw std::invalid_argument("FF1 numeral out of range for radix");
    }

    const size_t u = n / 2;
    const size_t v = n - u;
    const size_t b = static_cast<size_t>(std::ceil(std::ceil(v * std::log2(static_cast<double>(radix))) / 8.0));
    const size_t d = 4 * ((b + 3) / 4) + 4;

    // P = [1]^1 || [2]^1 || [1]^1 || [radix]^3 || [10]^1 || [u mod 256]^1 || [n]^4 || [t]^4.
    // P is the same in every round, so the CBC-MAC state after it is computed once.
    uint8_t prefixState[BLOCK_BYTES] = {1, 2, 1};
    putBigEndian(prefixState + 3, radix, 3);
    prefixState[6] = 10;
    prefixState[7] = static_cast<uint8_t>(u % 256);
    putBigEndian(prefixState + 8, n, 4);
    putBigEndian(prefixState + 12, tweak.size(), 4);
    cipher.encryptBlock(prefixState, prefixState);

    // Q = T || [0]^((-t-b-1) mod 16) || [i]^1 || [NUM_radix(B)]^b; only the last 1 + b bytes change per round.
    const size_t padding = (16 - (tweak.size() + b + 1) % 16) % 16;
    std::vector<uint8_t> q(tweak.size() + padding + 1 + b, 0);
    std::copy(tweak.begin(), tweak.end(), q.begin());
    uint8_t* roundByte = q.data() + tweak.size() + padding;
    uint8_t* valueBytes = roundByte + 1;

    std::vector<uint32_t> a(numerals.begin(), numerals.begin() + u);
    std::vector<uint32_t> bHalf(numerals.begin() + u, numerals.end());
    std::vector<uint8_t> s((d + BLOCK_BYTES - 1) / BLOCK_BYTES * BLOCK_BYTES);
    std::vector<uint32_t> y(v);

    for (int step = 0; step < 10; ++step) {
        const int round = encrypting ? step : 9 - step;
        const size_t m = (round % 2 == 0) ? u : v;
        // Encryption mixes B into A; decryption walks the Feistel network backwards.
        const auto& input = encrypting ? bHalf : a;
        auto& target = encrypting ? a : bHalf;

        *roundByte = static_cast<uint8_t>(round);
        numeralsToBytes(input.data(), input.size(), radix, valueBytes, b);

        // R = PRF(P || Q): CBC-MAC with a zero IV, continued from the precomputed P state.
        uint8_t r[BLOCK_BYTES];
        std::memcpy(r, prefixState, BLOCK_BYTES);
        for (size_t offset = 0; offset < q.size(); offset += BLOCK_BYTES) {
            for (int i = 0; i < BLOCK_BYTES; ++i) r[i] ^= q[offset + i];
            cipher.encryptBlock(r, r);
        }

        // S = first d bytes of R || CIPH(R xor [1]^16) || CIPH(R xor [2]^16) || ...
        std::memcpy(s.data(), r, BLOCK_BYTES);
        for (uint64_t j = 1; j * BLOCK_BYTES < d; ++j) {
            uint8_t* block = s.data() + j * BLOCK_BYTES;
            std::memcpy(block, r, BLOCK_BYTES);
            for (int i = 0; i < 8; ++i) block[BLOCK_BYTES - 1 - i] ^= static_cast<uint8_t>(j >> (8 * i));
            cipher.encryptBlock(block, block);
        }
        bytesToNumerals(s.data(), d, radix, y.data(), m);

        // C = (NUM_radix(target) +/- y) mod radix^m, digit by digit, in place.
        int64_t carry = 0;
        for (size_t i = m; i-- > 0;) {
            int64_t digit = static_cast<int64_t>(target[i]) + (encrypting ? 1 : -1) * static_cast<int64_t>(y[i]) + carry;
            carry = 0;
            if (digit >= static_cast<int64_t>(radix)) {
                digit -= radix;
                carry = 1;
            }
            else if (digit < 0) {
                digit += radix;
                carry = -1;
            }
            target[i] = static_cast<uint32_t>(digit);
        }
        // Encryption: A <- B, B <- C. Decryption: B <- A, A <- C. Both are a swap of the halves.
        std::swap(a, bHalf);
    }

    a.insert(a.end(), bHalf.begin(), bHalf.end());
    return a;
}

std::vector<uint32_t> Ff1::encrypt(const std::vector<uint8_t>& tweak, const std::vector<uint32_t>& numerals) const
{
    return crypt(tweak, numerals, true);
}

std::vector<uint32_t> Ff1::decrypt(const std::vector<uint8_t>& tweak, const std::vector<uint32_t>& numerals) const
{
    return crypt(tweak, numerals, false);
}

std::vector<uint8_t> Ff1::encryptBytes(const std::vector<uint8_t>& tweak, const std::vector<uint8_t>& plaintext) const
{
    if (radix != 256) throw std::logic_error("encryptBytes requires radix 256");
    const auto result = encrypt(tweak, std::vector<uint32_t>(plaintext.begin(), plaintext.end()));
    return std::vector<uint8_t>(result.begin(), result.end());
}

std::vector<uint8_t> Ff1::decryptBytes(const std::vector<uint8_t>& tweak, const std::vector<uint8_t>& ciphertext) const
{
    if (radix != 256) throw std::logic_error("decryptBytes requires radix 256");
    const auto result = decrypt(tweak, std::vector<uint32_t>(ciphertext.begin(), ciphertext.end()));
    return std::vector<uint8_t>(result.begin(), result.end());
}

} // namespace crypto
} // namespace pipelineSimulation
