#pragma once

#include <cstdint>

#include "BlockCipher.h"
#include "Ff1.h"

namespace pipelineSimulation {
namespace crypto {

// Sensitive CAM data elements, in ETSI TS 102 894-2 (CDD) units and ranges.
struct CamSensitiveFields {
    uint32_t stationId = 0;  // StationID: 0..4294967295
    int32_t latitude = 0;    // Latitude: -900000000..900000001 (0.1 microdegree)
    int32_t longitude = 0;   // Longitude: -1800000000..1800000001 (0.1 microdegree)
    uint16_t speed = 0;      // SpeedValue: 0..16383 (0.01 m/s)
    uint16_t heading = 0;    // HeadingValue: 0..3601 (0.1 degree, 0 = north, clockwise)

    bool operator==(const CamSensitiveFields& other) const
    {
        return stationId == other.stationId && latitude == other.latitude && longitude == other.longitude &&
            speed == other.speed && heading == other.heading;
    }
    bool operator!=(const CamSensitiveFields& other) const { return !(*this == other); }
};

// Format-preserving encryption of the sensitive CAM fields: the five fields are packed into one
// mixed-radix integer (< ~2^120.3), encrypted with FF1 over 121 binary numerals, and cycle-walked
// until the result is again a valid field tuple. Ciphertext fields keep their ETSI ranges.
class CamFieldCipher {
public:
    static constexpr int DOMAIN_BITS = 121;

    explicit CamFieldCipher(const BlockCipher& cipher);

    // tweak: GenerationDeltaTime of the CAM, sent in clear. rounds (optional): FF1 calls used.
    CamSensitiveFields encrypt(const CamSensitiveFields& fields, uint16_t tweak, int* rounds = nullptr) const;
    CamSensitiveFields decrypt(const CamSensitiveFields& fields, uint16_t tweak, int* rounds = nullptr) const;

    static bool isValid(const CamSensitiveFields& fields);

private:
    Ff1 ff1;

    CamSensitiveFields walk(const CamSensitiveFields& fields, uint16_t tweak, bool encrypting, int* rounds) const;
};

} // namespace crypto
} // namespace pipelineSimulation
