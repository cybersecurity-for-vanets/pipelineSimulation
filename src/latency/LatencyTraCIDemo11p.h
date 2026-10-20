#pragma once

#include <memory>
#include <unordered_map>
#include <unordered_set>
#include <utility>

#include "BlockCipher.h"
#include "CamFieldCipher.h"
#include "veins/modules/mac/ieee80211p/Mac1609_4.h"
#include "veins/modules/application/traci/TraCIDemo11p.h"

namespace pipelineSimulation {

class LatencyTraCIDemo11p : public veins::TraCIDemo11p {
protected:
    static constexpr int PROCESSING_BINS = 200;        // 10 us bins from 0 to 2 ms, plus overflow
    static constexpr double PROCESSING_BIN_S = 10e-6;
    static constexpr int PDR_DISTANCE_BINS = 6;        // 50 m bins up to the 300 m range
    static constexpr double PDR_DISTANCE_BIN_M = 50.0;

    enum AdaptiveEventKind {
        SEND_ADAPTIVE_CAM = 100,
        UPDATE_CHANNEL_BUSY_RATIO = 101,
        SEND_CAM_AFTER_CRYPTO = 102,
        PROCESS_CAM_AFTER_CRYPTO = 103
    };

    double latencyTotalMs = 0;
    long latencySampleCount = 0;
    double channelBusyRatioTotal = 0;
    long channelBusyRatioSamples = 0;
    double cryptoTxDelayTotalMs = 0;
    long cryptoTxOperationCount = 0;
    double cryptoRxDelayTotalMs = 0;
    long cryptoRxOperationCount = 0;
    long pdrExpectedReceptions = 0;
    long pdrReceivedReceptions = 0;
    long ff1DecryptFailures = 0;
    long camSentCount = 0;
    long camBytesTotal = 0;
    long camLowFrequencyCount = 0;
    long camCertificateCount = 0;

    // Single-core OBU processing model: FIFO shared by tx and rx operations.
    omnetpp::simtime_t cpuBusyUntil;
    omnetpp::simtime_t camProcessingDeadline;
    double cpuBusyTimeS = 0;
    double cpuQueueWaitTotalS = 0;
    long cpuQueueSamples = 0;
    long cpuQueueLength = 0;
    long cpuMaxQueueLength = 0;
    long processingDeadlineMisses = 0;
    double processingLatencyTotalMs = 0;
    double networkLatencyTotalMs = 0;

    // KPI distributions as bin counts (summable across nodes) and sums for IPG / peak AoI.
    long processingBins[PROCESSING_BINS + 1] = {};
    long pdrExpectedByDistance[PDR_DISTANCE_BINS] = {};
    long pdrReceivedByDistance[PDR_DISTANCE_BINS] = {};
    double ipgTotalS = 0;
    long ipgSamples = 0;
    double peakAoITotalS = 0;
    // station id -> (arrival time, generation time) of its last received CAM.
    std::unordered_map<uint32_t, std::pair<omnetpp::simtime_t, omnetpp::simtime_t>> lastRxByStation;

    std::unique_ptr<crypto::BlockCipher> camBlockCipher;
    std::unique_ptr<crypto::CamFieldCipher> camFieldCipher;

    // CAM size model (conf/cam_model.json) and security envelope state.
    long camBaseBytes = 0;
    long camLowFrequencyExtraBytes = 0;
    long camCertificateExtraBytes = 0;
    omnetpp::simtime_t camLowFrequencyInterval;
    omnetpp::simtime_t camCertificateInterval;
    omnetpp::simtime_t lastLowFrequencyTime;
    omnetpp::simtime_t lastCertificateTime;
    bool hasSentLowFrequency = false;
    bool hasSentCertificate = false;
    bool newNeighbourSinceLastCam = false;
    uint32_t stationId = 0;
    std::unordered_set<uint32_t> knownStations;

    omnetpp::cMessage* adaptiveCamEvent = nullptr;
    omnetpp::cMessage* channelBusyRatioEvent = nullptr;
    omnetpp::cMessage* camCryptoEvent = nullptr;
    veins::DemoSafetyMessage* pendingCam = nullptr;
    omnetpp::simtime_t camMinInterval;
    omnetpp::simtime_t camMaxInterval;
    omnetpp::simtime_t cbrWindow;
    omnetpp::simtime_t dccMinInterval;
    omnetpp::simtime_t cryptoTxDelay;
    omnetpp::simtime_t cryptoRxDelay;
    omnetpp::simtime_t lastCamTime;
    omnetpp::simtime_t channelStateChangedAt;
    omnetpp::simtime_t busyTimeInWindow;
    double camPositionThreshold;
    double camHeadingThresholdRad;
    double camSpeedThreshold;
    double cbrLowThreshold;
    double cbrHighThreshold;
    double camCommunicationRange = 0;
    double lastCamHeadingRad = 0;
    double lastCamSpeed = 0;
    double pendingCamHeadingRad = 0;
    double pendingCamSpeed = 0;
    veins::Coord lastCamPosition;
    veins::Coord pendingCamPosition;
    int cryptoOverheadBits = 0;
    bool hasSentCam = false;
    bool channelIsBusy = false;

    ~LatencyTraCIDemo11p() override;
    void initialize(int stage) override;
    void handleSelfMsg(omnetpp::cMessage* msg) override;
    void handlePositionUpdate(omnetpp::cObject* obj) override;
    void receiveSignal(omnetpp::cComponent* source, omnetpp::simsignal_t signalID, bool busy, omnetpp::cObject* details) override;
    void onBSM(veins::DemoSafetyMessage* bsm) override;
    void onWSM(veins::BaseFrame1609_4* wsm) override;
    void finish() override;

    bool movementThresholdExceeded() const;
    void scheduleAdaptiveCam();
    void sendAdaptiveCam();
    void sendPendingCam();
    void processReceivedCam(veins::DemoSafetyMessage* bsm);
    void updateChannelBusyRatio();
    long countPotentialCamReceivers(long* byDistance) const;
    bool isVehicleCam(const veins::DemoSafetyMessage* bsm) const;
    double recordLatency(const omnetpp::cMessage* message);
    crypto::CamSensitiveFields currentCamFields() const;
    void applyCamSize(veins::DemoSafetyMessage* cam);
    void attachCamFields(veins::DemoSafetyMessage* cam) const;
    uint32_t checkReceivedCamFields(veins::DemoSafetyMessage* cam);
    omnetpp::simtime_t scheduleOnCpu(omnetpp::cMessage* msg, omnetpp::simtime_t service);
    static int distanceBin(double meters);
};

}