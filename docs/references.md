# Academic References & Benchmark Methodology

---

## 1. Primary References

1. **Khaleghian, S., et al. (2023).** *Calibrating Real-World City Traffic Simulation Model Using Vehicle Speed Data*. In IEEE International Conference on Smart Computing (SMARTCOMP 2023).
   - **Key Principle:** Microscopic traffic models (Krauss in SUMO) are calibrated using empirical speed surveys as the objective function. Calibration is performed on a single peak period, and validation is carried out out-of-sample on distinct temporal windows.
   - **Validation Threshold:** A relative RMSE of $\le 25\%$ between simulated space-mean speed and empirical probe measurements represents state-of-the-art accuracy for real-world urban networks without synthetic sensor overfitting.

2. **Companhia de Engenharia de Tráfego de São Paulo (CET-SP).**
   - *Relatório de Mobilidade no Sistema Viário Principal (MSVP) — Volumes e Velocidades*, Annual Editions: 2016, 2017, 2018, and 2019.
   - Municipal traffic surveys providing classified hourly counts and space-mean speeds for the arterial road network of São Paulo, Brazil. Available at: [https://www.cetsp.com.br](https://www.cetsp.com.br).

3. **Transportation Research Board (TRB).**
   - *Highway Capacity Manual (HCM)*, 6th and 7th Editions. National Academies of Sciences, Engineering, and Medicine, Washington, D.C.
   - Defines operational Level of Service (LOS A through F) criteria for basic freeway segments (passenger car density), urban street segments (speed ratio), and signalized intersections (control delay).

4. **National Institute of Standards and Technology (NIST).**
   - *NIST Special Publication 800-38G Revision 1: Recommendation for Block Cipher Modes of Operation: Methods for Format-Preserving Encryption*.
   - Specifies the FF1 and FF3-1 format-preserving encryption modes based on Feistel structures.

5. **European Telecommunications Standards Institute (ETSI).**
   - *ETSI EN 302 637-2: Intelligent Transport Systems (ITS); Vehicular Communications; Basic Set of Applications; Part 2: Specification of Cooperative Awareness Basic Service*.
   - *ETSI TS 103 097: Intelligent Transport Systems (ITS); Security; Security header and certificate formats*.

6. **Institute of Electrical and Electronics Engineers (IEEE).**
   - *IEEE Std 1609.2-2016: IEEE Standard for Wireless Access in Vehicular Environments - Security Services for Applications and Management Messages*.
   - *IEEE Std 802.11p-2010: Wireless LAN Medium Access Control (MAC) and Physical Layer (PHY) Specifications: Wireless Access in Vehicular Environments*.

7. **Brummer, A., Sommer, C., et al. (2016).**
   - *Effects of Antenna Characteristics and Placements on a Vehicle-to-Vehicle Channel Scenario*.
   - Empirical measurements of directional gain patterns for roof-mounted monopoles in vehicular ad-hoc networks (`SampledAntenna1D`).

8. **Eckhoff, D., & Sommer, C. (2012).**
   - *A Statistical Process for Realistic Simulation of DSRC/WAVE Multipath Fading*.
   - Dual-distance Nakagami-m fast fading model for IEEE 802.11p at 5.9 GHz ($m=1.5$ for $d < 80\text{ m}$, $m=0.75$ for $d \ge 80\text{ m}$).

---

## 2. Block Cipher Specifications

- **AES-128:** FIPS PUB 197 (Advanced Encryption Standard).
- **LEA-128:** KS X 3246 (Lightweight Encryption Algorithm, developed by KISA, South Korea).
- **SPECK-128/128:** NSA Lightweight Cryptography Consortium. ARX (Add-Rotate-Xor) block cipher.
