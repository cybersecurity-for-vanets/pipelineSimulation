// Crypto benchmark for the CAM security model. Run it on OBU-class hardware.
//  1. Validates AES/LEA/SPECK, the NIST FF1 samples and the CAM field cipher; exits non-zero on failure.
//  2. Times FF1 encryption/decryption of the sensitive CAM fields (with cycle-walking) per block cipher.
//  3. Times ECDSA P-256 / SHA-256 signing and verification (OpenSSL), as used for every CAM.
// Prints one JSON object on stdout.
//
// Usage: ff1_benchmark [iterations] [signed_bytes]

#include <algorithm>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <random>
#include <string>
#include <vector>

#include <openssl/evp.h>

#include "Aes128.h"
#include "CamFieldCipher.h"
#include "Ff1.h"
#include "Lea128.h"
#include "Speck128.h"

using namespace pipelineSimulation::crypto;
using Clock = std::chrono::steady_clock;

namespace {

std::vector<uint8_t> fromHex(const std::string& hex)
{
    std::vector<uint8_t> bytes;
    for (size_t i = 0; i + 1 < hex.size(); i += 2) {
        bytes.push_back(static_cast<uint8_t>(std::stoul(hex.substr(i, 2), nullptr, 16)));
    }
    return bytes;
}

std::vector<uint32_t> toNumerals(const std::string& text)
{
    std::vector<uint32_t> numerals;
    for (char symbol : text) {
        numerals.push_back(symbol <= '9' ? symbol - '0' : symbol - 'a' + 10);
    }
    return numerals;
}

bool checkBlock(const char* name, const BlockCipher& cipher, const std::string& plain, const std::string& expected)
{
    uint8_t out[BLOCK_BYTES];
    cipher.encryptBlock(fromHex(plain).data(), out);
    const bool ok = std::vector<uint8_t>(out, out + BLOCK_BYTES) == fromHex(expected);
    if (!ok) std::fprintf(stderr, "[FAIL] %s known-answer test\n", name);
    return ok;
}

bool checkFf1(const char* name, uint32_t radix, const std::string& tweak, const std::string& plain, const std::string& expected)
{
    const Aes128 aes(fromHex("2b7e151628aed2a6abf7158809cf4f3c").data());
    const Ff1 ff1(aes, radix);
    const auto cipherText = ff1.encrypt(fromHex(tweak), toNumerals(plain));
    const bool ok = cipherText == toNumerals(expected) && ff1.decrypt(fromHex(tweak), cipherText) == toNumerals(plain);
    if (!ok) std::fprintf(stderr, "[FAIL] %s\n", name);
    return ok;
}

CamSensitiveFields randomFields(std::mt19937_64& rng)
{
    CamSensitiveFields fields;
    fields.stationId = static_cast<uint32_t>(rng());
    fields.latitude = std::uniform_int_distribution<int32_t>(-900000000, 900000001)(rng);
    fields.longitude = std::uniform_int_distribution<int32_t>(-1800000000, 1800000001)(rng);
    fields.speed = std::uniform_int_distribution<int>(0, 16383)(rng);
    fields.heading = std::uniform_int_distribution<int>(0, 3601)(rng);
    return fields;
}

bool checkCamFieldCipher(const BlockCipher& cipher, const char* name)
{
    const CamFieldCipher camCipher(cipher);
    std::mt19937_64 rng(7);
    for (int i = 0; i < 2000; ++i) {
        const auto plain = randomFields(rng);
        const uint16_t tweak = static_cast<uint16_t>(rng());
        const auto encrypted = camCipher.encrypt(plain, tweak);
        if (!CamFieldCipher::isValid(encrypted) || camCipher.decrypt(encrypted, tweak) != plain) {
            std::fprintf(stderr, "[FAIL] CAM field cipher round trip / range check (%s)\n", name);
            return false;
        }
    }
    return true;
}

bool runKnownAnswerTests()
{
    bool ok = true;
    const Aes128 aes(fromHex("000102030405060708090a0b0c0d0e0f").data());
    ok &= checkBlock("AES-128 (FIPS-197 C.1)", aes, "00112233445566778899aabbccddeeff", "69c4e0d86a7b0430d8cdb78070b4c55a");
    // KISA vector; its ciphertext is commonly printed as 32-bit words (354ec89f 18c6c628 a7c73255 fd8b6404),
    // here as the little-endian byte stream LEA defines.
    const Lea128 lea(fromHex("0f1e2d3c4b5a69788796a5b4c3d2e1f0").data());
    ok &= checkBlock("LEA-128 (KISA)", lea, "101112131415161718191a1b1c1d1e1f", "9fc84e3528c6c6185532c7a704648bfd");

    // SPECK-128/128 reference vector is given as words: key (l0, k0), plaintext (x, y), ciphertext (x, y).
    uint8_t speckKey[16];
    const uint64_t k0 = 0x0706050403020100ULL, l0 = 0x0f0e0d0c0b0a0908ULL;
    for (int i = 0; i < 8; ++i) {
        speckKey[i] = static_cast<uint8_t>(k0 >> (8 * i));
        speckKey[8 + i] = static_cast<uint8_t>(l0 >> (8 * i));
    }
    const Speck128 speck(speckKey);
    uint64_t x = 0x6c61766975716520ULL, y = 0x7469206564616d20ULL;
    speck.encryptWords(x, y);
    if (x != 0xa65d985179783265ULL || y != 0x7860fedf5c570d18ULL) {
        std::fprintf(stderr, "[FAIL] SPECK-128/128 known-answer test\n");
        ok = false;
    }

    ok &= checkFf1("FF1-AES128 NIST sample 1", 10, "", "0123456789", "2433477484");
    ok &= checkFf1("FF1-AES128 NIST sample 2", 10, "39383736353433323130", "0123456789", "6124200773");
    ok &= checkFf1("FF1-AES128 NIST sample 3", 36, "3737373770717273373737",
        "0123456789abcdefghi", "a9tv40mll9kdu509eum");

    ok &= checkCamFieldCipher(aes, "aes") && checkCamFieldCipher(lea, "lea") && checkCamFieldCipher(speck, "speck");
    return ok;
}

struct Stats {
    double median, p95, mean, min;
};

Stats summarize(std::vector<double> samples)
{
    std::sort(samples.begin(), samples.end());
    double total = 0;
    for (double sample : samples) total += sample;
    return {samples[samples.size() / 2], samples[samples.size() * 95 / 100], total / samples.size(), samples.front()};
}

void printStats(const char* label, const Stats& stats)
{
    std::printf("\"%s\": {\"median_us\": %.4f, \"p95_us\": %.4f, \"mean_us\": %.4f, \"min_us\": %.4f}",
        label, stats.median, stats.p95, stats.mean, stats.min);
}

double elapsedUs(Clock::time_point start, Clock::time_point end)
{
    return std::chrono::duration<double, std::micro>(end - start).count();
}

// Per-CAM cost with cycle-walking is data dependent; report per-FF1-call times and the mean number
// of calls per CAM, so effective cost = median call time x mean calls (robust to OS noise).
bool benchmarkFieldCipher(const char* name, int iterations)
{
    const auto key = fromHex("000102030405060708090a0b0c0d0e0f");
    const auto cipher = makeBlockCipher(name, key.data());
    const CamFieldCipher camCipher(*cipher);
    std::mt19937_64 rng(11);

    std::vector<CamSensitiveFields> inputs;
    std::vector<uint16_t> tweaks;
    for (int i = 0; i < iterations; ++i) {
        inputs.push_back(randomFields(rng));
        tweaks.push_back(static_cast<uint16_t>(rng()));
    }
    for (int i = 0; i < std::max(iterations / 10, 1); ++i) camCipher.encrypt(inputs[i], tweaks[i]);

    std::vector<double> encryptCall, decryptCall;
    long encryptRounds = 0, decryptRounds = 0;
    for (int i = 0; i < iterations; ++i) {
        int rounds = 0;
        auto start = Clock::now();
        const auto encrypted = camCipher.encrypt(inputs[i], tweaks[i], &rounds);
        auto end = Clock::now();
        encryptCall.push_back(elapsedUs(start, end) / rounds);
        encryptRounds += rounds;

        start = Clock::now();
        const auto decrypted = camCipher.decrypt(encrypted, tweaks[i], &rounds);
        end = Clock::now();
        decryptCall.push_back(elapsedUs(start, end) / rounds);
        decryptRounds += rounds;
        if (decrypted != inputs[i]) {
            std::fprintf(stderr, "[FAIL] FF1-%s CAM field round trip\n", name);
            return false;
        }
    }

    std::printf("\"%s\": {", name);
    printStats("encrypt_call", summarize(encryptCall));
    std::printf(", \"encrypt_calls_per_cam\": %.4f, ", static_cast<double>(encryptRounds) / iterations);
    printStats("decrypt_call", summarize(decryptCall));
    std::printf(", \"decrypt_calls_per_cam\": %.4f}", static_cast<double>(decryptRounds) / iterations);
    return true;
}

bool benchmarkEcdsa(int iterations, size_t signedBytes)
{
    EVP_PKEY* key = EVP_PKEY_Q_keygen(nullptr, nullptr, "EC", "P-256");
    if (!key) {
        std::fprintf(stderr, "[FAIL] ECDSA P-256 key generation\n");
        return false;
    }
    std::vector<uint8_t> message(signedBytes);
    for (size_t i = 0; i < signedBytes; ++i) message[i] = static_cast<uint8_t>(i * 13 + 1);

    std::vector<double> signTimes, verifyTimes;
    bool ok = true;
    for (int i = 0; ok && i < iterations; ++i) {
        message[0] = static_cast<uint8_t>(i);
        std::vector<uint8_t> signature(128);
        size_t signatureLength = signature.size();

        auto start = Clock::now();
        EVP_MD_CTX* signContext = EVP_MD_CTX_new();
        ok = EVP_DigestSignInit(signContext, nullptr, EVP_sha256(), nullptr, key) == 1 &&
            EVP_DigestSign(signContext, signature.data(), &signatureLength, message.data(), message.size()) == 1;
        EVP_MD_CTX_free(signContext);
        auto end = Clock::now();
        signTimes.push_back(elapsedUs(start, end));

        start = Clock::now();
        EVP_MD_CTX* verifyContext = EVP_MD_CTX_new();
        ok = ok && EVP_DigestVerifyInit(verifyContext, nullptr, EVP_sha256(), nullptr, key) == 1 &&
            EVP_DigestVerify(verifyContext, signature.data(), signatureLength, message.data(), message.size()) == 1;
        EVP_MD_CTX_free(verifyContext);
        end = Clock::now();
        verifyTimes.push_back(elapsedUs(start, end));
    }
    EVP_PKEY_free(key);
    if (!ok) {
        std::fprintf(stderr, "[FAIL] ECDSA P-256 sign/verify\n");
        return false;
    }

    std::printf("\"ecdsa_p256\": {\"signed_bytes\": %zu, ", signedBytes);
    printStats("sign", summarize(signTimes));
    std::printf(", ");
    printStats("verify", summarize(verifyTimes));
    std::printf("}");
    return true;
}

} // namespace

int main(int argc, char** argv)
{
    const int iterations = argc > 1 ? std::atoi(argv[1]) : 10000;
    const size_t signedBytes = argc > 2 ? std::strtoul(argv[2], nullptr, 10) : 200;

    if (!runKnownAnswerTests()) return 1;

    std::printf("{\"iterations\": %d, \"field_cipher\": {", iterations);
    const char* names[] = {"aes", "lea", "speck"};
    for (int index = 0; index < 3; ++index) {
        if (index) std::printf(", ");
        if (!benchmarkFieldCipher(names[index], iterations)) return 1;
    }
    std::printf("}, ");
    if (!benchmarkEcdsa(iterations, signedBytes)) return 1;
    std::printf("}\n");
    return 0;
}
