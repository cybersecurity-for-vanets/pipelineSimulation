# Cryptographic Techniques and Latency Analysis in V2V Communications

---

## 1. Introduction

Cooperative Intelligent Transport Systems (C-ITS) establish Vehicle-to-Vehicle (V2V) communications as the foundational infrastructure for distributed autonomous driving and advanced active safety. Continuous telemetry exchange enables critical applications such as Cooperative Adaptive Cruise Control (CACC), intersection collision avoidance, and close-headway platooning.

However, vehicular ad-hoc networks operate in open 5.9 GHz DSRC/C-V2X spectrum and remain vulnerable to hostile attacks including packet spoofing, location tampering, session hijacking, and replay attacks. Incorporating cryptographic primitives introduces strict engineering trade-offs: unlike conventional IT networks where latency buffers of hundreds of milliseconds are acceptable, active vehicular safety imposes strict deterministic time budgets (typically $\le 100\text{ ms}$ for Cooperative Awareness Messages and $< 10\text{ ms}$ for high-density platooning control loops).

---

## 2. Standards & Trust Architectures

Vehicular security is governed globally by two harmonized frameworks:
- **IEEE 1609.2** (WAVE/DSRC suite, referenced in SAE J2735 and SAE J3161).
- **ETSI TS 103 097** (European C-ITS framework).
- **3GPP Sidelink PC5** security specifications: **3GPP TS 33.185** (LTE-V2X) and **3GPP TS 33.536** (5G NR V2X).

### Authentication vs. Confidentiality
In broadcast safety messages (Basic Safety Messages - BSM, and Cooperative Awareness Messages - CAM), kinematic telemetry (GNSS coordinates, speed vector, heading, acceleration) is public by design. Surrounding vehicles must decode telemetry instantaneously without pre-established security associations or private decryption handshakes. Therefore, broadcast security focuses strictly on **source authentication**, **payload integrity**, and **non-repudiation**.

Privacy and driver unlinkability are achieved via Vehicular Public Key Infrastructures (V2X PKI / SCMS) using short-lived pseudonym certificates rotated frequently over time and distance.

---

## 3. Cryptographic Primitives in V2V

| Primitive Category | Algorithm / Profile | Parameter Size | Overhead / Signature | Primary V2V Role |
| :--- | :--- | :--- | :--- | :--- |
| **Asymmetric Signature** | ECDSA (NIST P-256 / Brainpool P-256r1) | 256 bits | 64 bytes $(r, s)$ | Authentication & integrity of BSM / CAM broadcasts |
| **Alternative Signature** | Ed25519 (EdDSA on Curve25519) | 256 bits | 64 bytes | Side-channel resistant signature and verification |
| **Implicit Certificates** | ECQV (Elliptic Curve Qu-Vanstone) | 256 bits | ~32 bytes reconstruction data | Compact pseudonym certificate issuance |
| **Symmetric Session Encryption**| AES-CCM / AES-GCM (AEAD) | 128 / 256 bits | 16 bytes authentication tag | Confidentiality in unicast/groupcast platooning |
| **Key Agreement** | ECDH (P-256 or Curve25519) | 256 bits | 0 bytes (derived locally) | Ephemeral session key negotiation |
| **Format-Preserving Encryption**| FF1 (NIST SP 800-38G) | 128 bits | **0 bytes** (length-preserving) | Selective CAM field privacy without fragmentation |

---

## 4. End-to-End Latency & Computational Asymmetry

The total latency budget between physical event detection at the transmitter and mechanical actuator response at the receiver is decomposed as:

$$T_{\text{total}} = T_{\text{tx\_prep}} + T_{\text{tx\_crypto}} + T_{\text{tx\_queue}} + T_{\text{channel}} + T_{\text{rx\_crypto}} + T_{\text{rx\_control}}$$

Where:
- $T_{\text{tx\_crypto}}$: SHA-256 hashing, optional FF1 encryption, and ECDSA signature generation.
- $T_{\text{channel}}$: Physical radio transmission, CSMA/CA channel contention, and propagation.
- $T_{\text{rx\_crypto}}$: Certificate reconstruction (ECQV), optional FF1 decryption, and ECDSA signature verification.
- $T_{\text{rx\_control}}$: ADAS sensor fusion and actuation command.

### The Computational Asymmetry Bottleneck
Elliptic curve cryptography exhibits severe computational asymmetry:
1. **Signing** requires a single scalar point multiplication:
   $$R = k \cdot G$$
   This is computationally lightweight for the sender ($\approx 0.2 - 0.5\text{ ms}$ on automotive HSMs).
2. **Verification** requires two scalar point multiplications and an elliptic curve point addition:
   $$Q = u_1 \cdot G + u_2 \cdot P_{\text{pub}}$$
   This requires substantially more computing power ($\approx 0.5 - 2.5\text{ ms}$).

Under high vehicular density (e.g., 100–200 vehicles in radio range transmitting at 10 Hz), a receiving vehicle must process **1,000 to 2,000 incoming CAMs every second**. Without dedicated hardware acceleration or format-preserving efficiency, software verification queues experience starvation, resulting in message drops and stale situational awareness.

---

## 5. Mitigation Strategies & Format-Preserving Encryption (FF1)

To mitigate cryptographic bottlenecks without exceeding physical channel capacity:
1. **Hardware Security Modules (HSM):** EVITA Full automotive grade cryptographic coprocessors.
2. **Verify-on-Demand (VoD):** Early kinematic spatial filtering discarding irrelevant vehicles prior to cryptographic verification.
3. **Amortized Certificate Broadcast:** Transmitting full certificates once per second and 8-byte hashed digests in interim 100 ms packets.
4. **NIST SP 800-38G FF1 Encryption:** When selective telemetry fields require privacy (e.g., preventing tracking of specific vehicle attributes), traditional block ciphers (AES-CBC, AES-GCM) expand packet length, forcing physical layer fragmentation. **FF1 Format-Preserving Encryption** encrypts fields into ciphertexts of identical length and radix (0 bytes packet overhead), preserving MTU margins and mitigating radio congestion.

---

## 6. References

1. IEEE 1609.2-2016: *IEEE Standard for Wireless Access in Vehicular Environments - Security Services for Applications and Management Messages*.
2. ETSI TS 103 097 V1.4.1: *Intelligent Transport Systems (ITS); Security; Security header and certificate formats*.
3. NIST Special Publication 800-38G Revision 1: *Recommendation for Block Cipher Modes of Operation: Methods for Format-Preserving Encryption*.
4. 3GPP TS 33.536: *Security aspects of 3GPP support for advanced V2X services (Release 18)*.
5. EVITA Project: *E-safety Vehicle Intrusion proTected Applications - Secure On-board Architecture*.
6. ETSI TR 103 949 V1.1.1: *Quantum-Safe Cryptography for Intelligent Transport Systems*.
