#pragma once

#include "veins/modules/mac/ieee80211p/Mac1609_4.h"
#include "veins/modules/application/traci/TraCIDemo11p.h"

namespace pipelineSimulation {

class LatencyTraCIDemo11p : public veins::TraCIDemo11p {
protected:
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
    void recordLatency(const omnetpp::cMessage* message);
};

}