#include "BlockCipher.h"

#include <stdexcept>

#include "Aes128.h"
#include "Lea128.h"
#include "Speck128.h"

namespace pipelineSimulation {
namespace crypto {

std::unique_ptr<BlockCipher> makeBlockCipher(const std::string& name, const uint8_t key[16])
{
    if (name == "aes") return std::make_unique<Aes128>(key);
    if (name == "lea") return std::make_unique<Lea128>(key);
    if (name == "speck") return std::make_unique<Speck128>(key);
    throw std::invalid_argument("Unknown block cipher: " + name);
}

} // namespace crypto
} // namespace pipelineSimulation
