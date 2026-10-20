#include "LatencyTraCIDemo11p.h"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <stdexcept>
#include <string>

using namespace omnetpp;

namespace {
constexpr int VEHICLE_CAM_PSID = 42;
// Verification-only message parameters: they do not add to the frame bit length.
constexpr const char* CAM_FIELDS_PAR = "camFields";          // sensitive fields as sent (FF1 ciphertext or clear)
constexpr const char* CAM_PLAIN_FIELDS_PAR = "camPlainFields"; // sender's plaintext, to check decryption
// Shared group key: every vehicle can decrypt every CAM.
constexpr uint8_t CAM_GROUP_KEY[16] = {
    0x2b, 0x7e, 0x15, 0x16, 0x28, 0xae, 0xd2, 0xa6, 0xab, 0xf7, 0x15, 0x88, 0x09, 0xcf, 0x4f, 0x3c};

std::string formatFields(const pipelineSimulation::crypto::CamSensitiveFields& fields)
{
    return std::to_string(fields.stationId) + "," + std::to_string(fields.latitude) + "," +
        std::to_string(fields.longitude) + "," + std::to_string(fields.speed) + "," + std::to_string(fields.heading);
}

pipelineSimulation::crypto::CamSensitiveFields parseFields(const char* text)
{
    pipelineSimulation::crypto::CamSensitiveFields fields;
    unsigned long stationId = 0;
    long latitude = 0, longitude = 0;
    unsigned speed = 0, heading = 0;
    if (std::sscanf(text, "%lu,%ld,%ld,%u,%u", &stationId, &latitude, &longitude, &speed, &heading) != 5) {
        throw cRuntimeError("Malformed CAM field parameter '%s'", text);
    }
    fields.stationId = static_cast<uint32_t>(stationId);
    fields.latitude = static_cast<int32_t>(latitude);
    fields.longitude = static_cast<int32_t>(longitude);
    fields.speed = static_cast<uint16_t>(speed);
    fields.heading = static_cast<uint16_t>(heading);
    return fields;
}

// GenerationDeltaTime (ETSI EN 302 637-2): generation time in ms modulo 65536, sent in clear; used as FF1 tweak.
uint16_t generationDeltaTime(const cMessage* cam)
{
    return static_cast<uint16_t>(cam->getCreationTime().inUnit(SIMTIME_MS) % 65536);
}
}

Define_Module(pipelineSimulation::LatencyTraCIDemo11p);

pipelineSimulation::LatencyTraCIDemo11p::~LatencyTraCIDemo11p()
{
    cancelAndDelete(adaptiveCamEvent);
    cancelAndDelete(channelBusyRatioEvent);
    cancelAndDelete(camCryptoEvent);
    cancelAndDelete(warmupEvent);
    delete pendingCam;
}

void pipelineSimulation::LatencyTraCIDemo11p::initialize(int stage)
{
    veins::TraCIDemo11p::initialize(stage);

    if (stage == 0) {
        camMinInterval = SimTime(par("camMinInterval").doubleValue());
        camMaxInterval = SimTime(par("camMaxInterval").doubleValue());
        cbrWindow = SimTime(par("cbrWindow").doubleValue());
        camPositionThreshold = par("camPositionThreshold").doubleValue();
        camHeadingThresholdRad = par("camHeadingThresholdDeg").doubleValue() * std::acos(-1.0) / 180.0;
        camSpeedThreshold = par("camSpeedThreshold").doubleValue();
        cbrLowThreshold = par("cbrLowThreshold").doubleValue();
        cbrHighThreshold = par("cbrHighThreshold").doubleValue();
        cryptoOverheadBits = par("cryptoOverheadBits").intValue();
        cryptoTxDelay = SimTime(par("cryptoTxDelay").doubleValue());
        cryptoRxDelay = SimTime(par("cryptoRxDelay").doubleValue());

        warmupTime = SimTime(par("warmupTime").doubleValue());
        if (warmupTime < SIMTIME_ZERO) {
            throw cRuntimeError("warmupTime must be non-negative");
        }
        recordMessageVectors = par("recordMessageVectors").boolValue();
        messageVectorStride = par("messageVectorStride").intValue();
        if (messageVectorStride < 1) {
            throw cRuntimeError("messageVectorStride must be at least 1");
        }
        latencyEndToEndVec.vector.setName("latencyEndToEndMs");
        processingLatencyVec.vector.setName("processingLatencyMs");
        networkLatencyVec.vector.setName("networkLatencyMs");
        cpuQueueWaitVec.vector.setName("cpuQueueWaitMs");
        ipgVec.vector.setName("ipgMs");
        aoiVec.vector.setName("aoiMs");
        camBitsVec.vector.setName("camBits");

        auto* connectionManager = getSimulation()->getSystemModule()->getSubmodule("connectionManager");
        if (!connectionManager) {
            throw cRuntimeError("Cannot find connectionManager to calculate CAM reception opportunities");
        }
        camCommunicationRange = connectionManager->par("maxInterfDist").doubleValue();

        if (camMinInterval <= SIMTIME_ZERO || camMaxInterval < camMinInterval || cbrWindow <= SIMTIME_ZERO) {
            throw cRuntimeError("Invalid adaptive CAM time interval configuration");
        }
        if (camPositionThreshold <= 0 || camHeadingThresholdRad <= 0 || camSpeedThreshold <= 0 ||
            cbrLowThreshold < 0 || cbrHighThreshold > 1 || cbrLowThreshold >= cbrHighThreshold) {
            throw cRuntimeError("Invalid adaptive CAM threshold configuration");
        }
        if (cryptoOverheadBits < 0 || cryptoTxDelay < SIMTIME_ZERO || cryptoRxDelay < SIMTIME_ZERO) {
            throw cRuntimeError("Crypto overhead and processing delays must be non-negative");
        }

        camBaseBytes = par("camBaseBytes").intValue();
        camLowFrequencyExtraBytes = par("camLowFrequencyExtraBytes").intValue();
        camCertificateExtraBytes = par("camCertificateExtraBytes").intValue();
        camLowFrequencyInterval = SimTime(par("camLowFrequencyInterval").doubleValue());
        camCertificateInterval = SimTime(par("camCertificateInterval").doubleValue());
        if (camBaseBytes <= 0 || camLowFrequencyExtraBytes < 0 || camCertificateExtraBytes < 0 ||
            camLowFrequencyInterval <= SIMTIME_ZERO || camCertificateInterval <= SIMTIME_ZERO) {
            throw cRuntimeError("Invalid CAM size model configuration");
        }
        // StationID: random pseudonym per vehicle (reproducible per seed).
        stationId = static_cast<uint32_t>(intuniform(0, 0xffff)) << 16 | static_cast<uint32_t>(intuniform(0, 0xffff));

        camProcessingDeadline = SimTime(par("camProcessingDeadline").doubleValue());
        if (camProcessingDeadline <= SIMTIME_ZERO) {
            throw cRuntimeError("camProcessingDeadline must be positive");
        }

        const std::string cipherName = par("cryptoCipher").stdstringValue();
        if (cipherName != "none") {
            try {
                camBlockCipher = crypto::makeBlockCipher(cipherName, CAM_GROUP_KEY);
            }
            catch (const std::invalid_argument& error) {
                throw cRuntimeError("Invalid cryptoCipher '%s': %s", cipherName.c_str(), error.what());
            }
            camFieldCipher = std::make_unique<crypto::CamFieldCipher>(*camBlockCipher);
        }

        dccMinInterval = camMinInterval;
        adaptiveCamEvent = new cMessage("adaptive CAM event", SEND_ADAPTIVE_CAM);
        channelBusyRatioEvent = new cMessage("channel busy ratio event", UPDATE_CHANNEL_BUSY_RATIO);
        camCryptoEvent = new cMessage("CAM crypto processing event", SEND_CAM_AFTER_CRYPTO);
        warmupEvent = new cMessage("warm-up end event", WARMUP_END);

        auto* macModule = veins::FindModule<veins::Mac1609_4*>::findSubModule(getParentModule());
        if (!macModule) {
            throw cRuntimeError("Cannot find Mac1609_4 to monitor channel busy state");
        }
        macModule->subscribe(veins::Mac1609_4::sigChannelBusy, this);
    }
    else if (stage == 1) {
        channelStateChangedAt = simTime();
        scheduleAt(simTime(), adaptiveCamEvent);
        scheduleAt(simTime() + cbrWindow, channelBusyRatioEvent);
        if (warmupTime > SIMTIME_ZERO) scheduleAt(warmupTime, warmupEvent);
    }
}

void pipelineSimulation::LatencyTraCIDemo11p::handleSelfMsg(cMessage* msg)
{
    if (msg == adaptiveCamEvent) {
        const bool heartbeatDue = !hasSentCam || simTime() >= lastCamTime + camMaxInterval;
        const bool movementDue = hasSentCam && movementThresholdExceeded() && simTime() >= lastCamTime + dccMinInterval;

        if (heartbeatDue || movementDue) {
            sendAdaptiveCam();
        }
        else {
            scheduleAdaptiveCam();
        }
        return;
    }

    if (msg == channelBusyRatioEvent) {
        updateChannelBusyRatio();
        scheduleAt(simTime() + cbrWindow, channelBusyRatioEvent);
        scheduleAdaptiveCam();
        return;
    }

    if (msg == camCryptoEvent) {
        sendPendingCam();
        return;
    }

    if (msg == warmupEvent) {
        resetStatistics();
        return;
    }

    if (msg->getKind() == PROCESS_CAM_AFTER_CRYPTO) {
        auto* bsm = check_and_cast<veins::DemoSafetyMessage*>(msg);
        --cpuQueueLength;
        processReceivedCam(bsm);
        delete bsm;
        return;
    }

    veins::TraCIDemo11p::handleSelfMsg(msg);
}

void pipelineSimulation::LatencyTraCIDemo11p::handlePositionUpdate(cObject* obj)
{
    // Skip TraCIDemo11p's demo "stopped vehicle" warning: it broadcasts non-ETSI WSMs and makes
    // receivers reroute in SUMO, which would distort the calibrated traffic.
    veins::DemoBaseApplLayer::handlePositionUpdate(obj);
    if (hasSentCam) scheduleAdaptiveCam();
}

void pipelineSimulation::LatencyTraCIDemo11p::receiveSignal(cComponent*, simsignal_t signalID, bool busy, cObject*)
{
    if (signalID != veins::Mac1609_4::sigChannelBusy || busy == channelIsBusy) return;

    if (channelIsBusy) busyTimeInWindow += simTime() - channelStateChangedAt;
    channelIsBusy = busy;
    channelStateChangedAt = simTime();
}

bool pipelineSimulation::LatencyTraCIDemo11p::movementThresholdExceeded() const
{
    if (!hasSentCam || !mobility || mobility->getHeading().isNan()) return false;

    const double headingDifference = std::abs(std::remainder(
        mobility->getHeading().getRad() - lastCamHeadingRad, 2.0 * std::acos(-1.0)));
    return curPosition.distance(lastCamPosition) > camPositionThreshold ||
        headingDifference > camHeadingThresholdRad ||
        std::abs(mobility->getSpeed() - lastCamSpeed) > camSpeedThreshold;
}

void pipelineSimulation::LatencyTraCIDemo11p::scheduleAdaptiveCam()
{
    if (!adaptiveCamEvent || !adaptiveCamEvent->isScheduled()) {
        simtime_t deadline = simTime();
        if (hasSentCam) {
            deadline = lastCamTime + camMaxInterval;
            if (movementThresholdExceeded()) {
                deadline = std::min(deadline, lastCamTime + dccMinInterval);
            }
            if (deadline < simTime()) deadline = simTime();
        }
        scheduleAt(deadline, adaptiveCamEvent);
        return;
    }

    simtime_t deadline = hasSentCam ? lastCamTime + camMaxInterval : simTime();
    if (hasSentCam && movementThresholdExceeded()) {
        deadline = std::min(deadline, lastCamTime + dccMinInterval);
    }
    if (deadline < simTime()) deadline = simTime();

    if (adaptiveCamEvent->getArrivalTime() != deadline) {
        cancelEvent(adaptiveCamEvent);
        scheduleAt(deadline, adaptiveCamEvent);
    }
}

void pipelineSimulation::LatencyTraCIDemo11p::sendAdaptiveCam()
{
    if (pendingCam) return;

    pendingCam = new veins::DemoSafetyMessage();
    populateWSM(pendingCam);
    pendingCam->setPsid(VEHICLE_CAM_PSID);
    applyCamSize(pendingCam);
    attachCamFields(pendingCam);
    pendingCamPosition = curPosition;
    pendingCamHeadingRad = mobility->getHeading().getRad();
    pendingCamSpeed = mobility->getSpeed();
    scheduleOnCpu(camCryptoEvent, cryptoTxDelay);
}

void pipelineSimulation::LatencyTraCIDemo11p::sendPendingCam()
{
    if (!pendingCam) throw cRuntimeError("CAM crypto event fired without a pending CAM");
    --cpuQueueLength;

    auto* cam = pendingCam;
    pendingCam = nullptr;
    pdrExpectedReceptions += countPotentialCamReceivers(pdrExpectedByDistance);
    // Sender-side processing (queue wait + service), used to split latency at the receiver.
    cam->addPar("txProcessingS").setDoubleValue((simTime() - cam->getCreationTime()).dbl());
    sendDown(cam);

    cryptoTxDelayTotalMs += cryptoTxDelay.dbl() * 1000.0;
    ++cryptoTxOperationCount;
    lastCamPosition = pendingCamPosition;
    lastCamHeadingRad = pendingCamHeadingRad;
    lastCamSpeed = pendingCamSpeed;
    lastCamTime = simTime();
    hasSentCam = true;
    scheduleAdaptiveCam();
}

void pipelineSimulation::LatencyTraCIDemo11p::processReceivedCam(veins::DemoSafetyMessage* bsm)
{
    // Received CAMs do not touch this vehicle's own CAM timing: ETSI generation depends only on its own state.
    const double latencyS = recordLatency(bsm);
    const uint32_t stationId = checkReceivedCamFields(bsm);

    const double txProcessingS = bsm->hasPar("txProcessingS") ? bsm->par("txProcessingS").doubleValue() : 0.0;
    const double rxProcessingS = bsm->hasPar("rxProcessingS") ? bsm->par("rxProcessingS").doubleValue() : 0.0;
    const double processingS = txProcessingS + rxProcessingS;
    processingLatencyTotalMs += processingS * 1000.0;
    networkLatencyTotalMs += (latencyS - processingS) * 1000.0;
    recordMessageSample(processingLatencyVec, processingS * 1000.0);
    recordMessageSample(networkLatencyVec, (latencyS - processingS) * 1000.0);
    ++processingBins[std::min(static_cast<int>(processingS / PROCESSING_BIN_S), PROCESSING_BINS)];

    ++pdrReceivedByDistance[distanceBin(curPosition.distance(bsm->getSenderPos()))];

    // IPG: arrival-to-arrival. Peak AoI at this arrival: age of the previous received CAM from the same station.
    const simtime_t now = simTime();
    const simtime_t generation = bsm->getCreationTime();
    auto previous = lastRxByStation.find(stationId);
    if (previous != lastRxByStation.end()) {
        const double ipgS = (now - previous->second.first).dbl();
        const double aoiS = (now - previous->second.second).dbl();
        ipgTotalS += ipgS;
        ++ipgSamples;
        peakAoITotalS += aoiS;
        recordMessageSample(ipgVec, ipgS * 1000.0);
        recordMessageSample(aoiVec, aoiS * 1000.0);
        previous->second = std::make_pair(now, generation);
    }
    else {
        lastRxByStation.emplace(stationId, std::make_pair(now, generation));
    }

    cryptoRxDelayTotalMs += cryptoRxDelay.dbl() * 1000.0;
    ++cryptoRxOperationCount;
    veins::TraCIDemo11p::onBSM(bsm);
}

void pipelineSimulation::LatencyTraCIDemo11p::updateChannelBusyRatio()
{
    simtime_t busyDuration = busyTimeInWindow;
    if (channelIsBusy) busyDuration += simTime() - channelStateChangedAt;

    const double cbr = std::clamp(busyDuration.dbl() / cbrWindow.dbl(), 0.0, 1.0);
    channelBusyRatioTotal += cbr;
    ++channelBusyRatioSamples;
    busyTimeInWindow = SIMTIME_ZERO;
    channelStateChangedAt = simTime();

    const double load = std::clamp(
        (cbr - cbrLowThreshold) / (cbrHighThreshold - cbrLowThreshold), 0.0, 1.0);
    dccMinInterval = camMinInterval + (camMaxInterval - camMinInterval) * load;
}

void pipelineSimulation::LatencyTraCIDemo11p::resetStatistics()
{
    // End of warm-up: keep protocol state (neighbours, CPU queue, busy window, cipher) and restart the counters.
    // ff1DecryptFailures is not reset on purpose: it must be zero over the whole run.
    latencyTotalMs = 0;
    latencySampleCount = 0;
    channelBusyRatioTotal = 0;
    channelBusyRatioSamples = 0;
    cryptoTxDelayTotalMs = 0;
    cryptoTxOperationCount = 0;
    cryptoRxDelayTotalMs = 0;
    cryptoRxOperationCount = 0;
    pdrExpectedReceptions = 0;
    pdrReceivedReceptions = 0;
    camSentCount = 0;
    camBytesTotal = 0;
    camLowFrequencyCount = 0;
    camCertificateCount = 0;
    cpuBusyTimeS = 0;
    cpuQueueWaitTotalS = 0;
    cpuQueueSamples = 0;
    cpuMaxQueueLength = cpuQueueLength;
    processingDeadlineMisses = 0;
    processingLatencyTotalMs = 0;
    networkLatencyTotalMs = 0;
    for (auto& bin : processingBins) bin = 0;
    for (auto& count : pdrExpectedByDistance) count = 0;
    for (auto& count : pdrReceivedByDistance) count = 0;
    ipgTotalS = 0;
    ipgSamples = 0;
    peakAoITotalS = 0;
}

void pipelineSimulation::LatencyTraCIDemo11p::onBSM(veins::DemoSafetyMessage* bsm)
{
    // Only vehicle CAMs are signed/ciphered and processed; RSU beacons follow the demo application unchanged.
    if (!isVehicleCam(bsm)) {
        veins::TraCIDemo11p::onBSM(bsm);
        return;
    }
    ++pdrReceivedReceptions;

    if (cryptoRxDelay == SIMTIME_ZERO) {
        processReceivedCam(bsm);
        return;
    }

    auto* pendingBsm = check_and_cast<veins::DemoSafetyMessage*>(bsm->dup());
    pendingBsm->setKind(PROCESS_CAM_AFTER_CRYPTO);
    const simtime_t rxProcessing = scheduleOnCpu(pendingBsm, cryptoRxDelay);
    pendingBsm->addPar("rxProcessingS").setDoubleValue(rxProcessing.dbl());
}

void pipelineSimulation::LatencyTraCIDemo11p::onWSM(veins::BaseFrame1609_4*)
{
    // Vehicles send only CAMs; ignore demo WSMs instead of rerouting like TraCIDemo11p does.
}

void pipelineSimulation::LatencyTraCIDemo11p::finish()
{
    veins::TraCIDemo11p::finish();
    if (channelBusyRatioSamples > 0) {
        recordScalar("meanChannelBusyRatio", channelBusyRatioTotal / channelBusyRatioSamples);
    }
    // Length of the measurement window (after warm-up), used to normalise per-second metrics.
    recordScalar("measurementTimeS", (simTime() - warmupTime).dbl());
    recordScalar("cryptoTxDelayTotalMs", cryptoTxDelayTotalMs);
    recordScalar("cryptoTxOperationCount", cryptoTxOperationCount);
    recordScalar("cryptoRxDelayTotalMs", cryptoRxDelayTotalMs);
    recordScalar("cryptoRxOperationCount", cryptoRxOperationCount);
    recordScalar("pdrExpectedReceptions", pdrExpectedReceptions);
    recordScalar("pdrReceivedReceptions", pdrReceivedReceptions);
    if (camFieldCipher) recordScalar("ff1DecryptFailures", ff1DecryptFailures);
    recordScalar("camSentCount", camSentCount);
    recordScalar("camBytesTotal", camBytesTotal);
    recordScalar("camLowFrequencyCount", camLowFrequencyCount);
    recordScalar("camCertificateCount", camCertificateCount);
    recordScalar("cpuBusyTimeS", cpuBusyTimeS);
    recordScalar("cpuQueueWaitTotalS", cpuQueueWaitTotalS);
    recordScalar("cpuQueueSamples", cpuQueueSamples);
    recordScalar("cpuMaxQueueLength", cpuMaxQueueLength);
    recordScalar("processingDeadlineMisses", processingDeadlineMisses);
    recordScalar("processingLatencyTotalMs", processingLatencyTotalMs);
    recordScalar("networkLatencyTotalMs", networkLatencyTotalMs);
    recordScalar("ipgTotalS", ipgTotalS);
    recordScalar("ipgSamples", ipgSamples);
    recordScalar("peakAoITotalS", peakAoITotalS);
    for (int bin = 0; bin <= PROCESSING_BINS; ++bin) {
        recordScalar(("procBin_" + std::to_string(bin)).c_str(), processingBins[bin]);
    }
    for (int bin = 0; bin < PDR_DISTANCE_BINS; ++bin) {
        const std::string suffix = "_d" + std::to_string(bin);
        recordScalar(("pdrExpected" + suffix).c_str(), pdrExpectedByDistance[bin]);
        recordScalar(("pdrReceived" + suffix).c_str(), pdrReceivedByDistance[bin]);
    }
    if (latencySampleCount > 0) {
        recordScalar("packetLatencyTotalMs", latencyTotalMs);
        recordScalar("packetLatencySamples", latencySampleCount);
    }
}

double pipelineSimulation::LatencyTraCIDemo11p::recordLatency(const cMessage* message)
{
    const double latencyS = (simTime() - message->getCreationTime()).dbl();
    const double latencyMs = latencyS * 1000.0;
    if (std::isfinite(latencyMs) && latencyMs >= 0) {
        latencyTotalMs += latencyMs;
        ++latencySampleCount;
        recordMessageSample(latencyEndToEndVec, latencyMs);
    }
    return latencyS;
}

void pipelineSimulation::LatencyTraCIDemo11p::recordMessageSample(MessageVector& sample, double value)
{
    if (!recordMessageVectors || simTime() < warmupTime) return;
    if (sample.seen++ % messageVectorStride == 0) sample.vector.record(value);
}

long pipelineSimulation::LatencyTraCIDemo11p::countPotentialCamReceivers(long* byDistance) const
{
    auto* systemModule = getSimulation()->getSystemModule();
    long receiverCount = 0;
    const int nodeCount = systemModule->getSubmoduleVectorSize("node");

    for (int index = 0; index < nodeCount; ++index) {
        auto* node = systemModule->getSubmodule("node", index);
        auto* application = node
            ? dynamic_cast<LatencyTraCIDemo11p*>(node->getSubmodule("appl"))
            : nullptr;
        if (!application || application == this || !application->mobility) continue;

        const double distance = curPosition.distance(application->curPosition);
        if (distance > camCommunicationRange) continue;
        ++receiverCount;
        if (byDistance) ++byDistance[distanceBin(distance)];
    }
    return receiverCount;
}

int pipelineSimulation::LatencyTraCIDemo11p::distanceBin(double meters)
{
    return std::min(static_cast<int>(meters / PDR_DISTANCE_BIN_M), PDR_DISTANCE_BINS - 1);
}

simtime_t pipelineSimulation::LatencyTraCIDemo11p::scheduleOnCpu(cMessage* msg, simtime_t service)
{
    // FIFO single core: the operation starts when the core is free and occupies it for `service`.
    const simtime_t start = std::max(simTime(), cpuBusyUntil);
    const simtime_t finish = start + service;
    cpuBusyUntil = finish;
    cpuBusyTimeS += service.dbl();
    cpuQueueWaitTotalS += (start - simTime()).dbl();
    recordMessageSample(cpuQueueWaitVec, (start - simTime()).dbl() * 1000.0);
    ++cpuQueueSamples;
    cpuMaxQueueLength = std::max(cpuMaxQueueLength, ++cpuQueueLength);
    if (finish - simTime() > camProcessingDeadline) ++processingDeadlineMisses;
    scheduleAt(finish, msg);
    return finish - simTime();
}

bool pipelineSimulation::LatencyTraCIDemo11p::isVehicleCam(const veins::DemoSafetyMessage* bsm) const
{
    return bsm->getPsid() == VEHICLE_CAM_PSID;
}

pipelineSimulation::crypto::CamSensitiveFields pipelineSimulation::LatencyTraCIDemo11p::currentCamFields() const
{
    crypto::CamSensitiveFields fields;
    fields.stationId = stationId;

    // Latitude/Longitude in 0.1 microdegree, from the SUMO network's geo-reference.
    const auto lonLat = traci->getLonLat(curPosition);
    fields.longitude = static_cast<int32_t>(std::lround(std::clamp(lonLat.first, -180.0, 180.0) * 1e7));
    fields.latitude = static_cast<int32_t>(std::lround(std::clamp(lonLat.second, -90.0, 90.0) * 1e7));

    // SpeedValue in 0.01 m/s (16383 = unavailable, so cap at 16382).
    fields.speed = static_cast<uint16_t>(std::min(std::lround(std::max(mobility->getSpeed(), 0.0) * 100.0), 16382L));

    // HeadingValue in 0.1 degree, 0 = north, clockwise. Veins Heading: 0 = east, counter-clockwise.
    const double compassDeg = std::fmod(90.0 - mobility->getHeading().getRad() * 180.0 / std::acos(-1.0) + 720.0, 360.0);
    fields.heading = static_cast<uint16_t>(std::lround(compassDeg * 10.0) % 3600);
    return fields;
}

void pipelineSimulation::LatencyTraCIDemo11p::applyCamSize(veins::DemoSafetyMessage* cam)
{
    // Structural size model (conf/cam_model.json): signed CAM with digest, plus the low-frequency
    // container every camLowFrequencyInterval and a full certificate every camCertificateInterval
    // or after a new neighbour was heard. headerLength carries the GeoNetworking + BTP headers.
    const bool includeLowFrequency = !hasSentLowFrequency || simTime() - lastLowFrequencyTime >= camLowFrequencyInterval;
    const bool includeCertificate = !hasSentCertificate || newNeighbourSinceLastCam ||
        simTime() - lastCertificateTime >= camCertificateInterval;

    long bodyBytes = camBaseBytes;
    if (includeLowFrequency) {
        bodyBytes += camLowFrequencyExtraBytes;
        lastLowFrequencyTime = simTime();
        hasSentLowFrequency = true;
        ++camLowFrequencyCount;
    }
    if (includeCertificate) {
        bodyBytes += camCertificateExtraBytes;
        lastCertificateTime = simTime();
        hasSentCertificate = true;
        newNeighbourSinceLastCam = false;
        ++camCertificateCount;
    }

    cam->setBitLength(headerLength + bodyBytes * 8 + cryptoOverheadBits);
    ++camSentCount;
    camBytesTotal += cam->getByteLength();
    recordMessageSample(camBitsVec, static_cast<double>(cam->getBitLength()));
}

void pipelineSimulation::LatencyTraCIDemo11p::attachCamFields(veins::DemoSafetyMessage* cam) const
{
    const auto plain = currentCamFields();
    const auto sent = camFieldCipher ? camFieldCipher->encrypt(plain, generationDeltaTime(cam)) : plain;
    cam->addPar(CAM_FIELDS_PAR).setStringValue(formatFields(sent).c_str());
    cam->addPar(CAM_PLAIN_FIELDS_PAR).setStringValue(formatFields(plain).c_str());
}

uint32_t pipelineSimulation::LatencyTraCIDemo11p::checkReceivedCamFields(veins::DemoSafetyMessage* cam)
{
    if (!cam->hasPar(CAM_FIELDS_PAR) || !cam->hasPar(CAM_PLAIN_FIELDS_PAR)) {
        if (camFieldCipher) ++ff1DecryptFailures;
        return 0;
    }
    const auto plain = parseFields(cam->par(CAM_PLAIN_FIELDS_PAR).stringValue());
    if (camFieldCipher) {
        const auto received = parseFields(cam->par(CAM_FIELDS_PAR).stringValue());
        if (camFieldCipher->decrypt(received, generationDeltaTime(cam)) != plain) ++ff1DecryptFailures;
    }
    // A CAM from an unknown station triggers a full certificate in our next CAM.
    if (knownStations.insert(plain.stationId).second) newNeighbourSinceLastCam = true;
    return plain.stationId;
}
