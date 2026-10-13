#include "LatencyTraCIDemo11p.h"

#include <algorithm>
#include <cmath>

using namespace omnetpp;

namespace {
constexpr int VEHICLE_CAM_PSID = 42;
}

Define_Module(pipelineSimulation::LatencyTraCIDemo11p);

pipelineSimulation::LatencyTraCIDemo11p::~LatencyTraCIDemo11p()
{
    cancelAndDelete(adaptiveCamEvent);
    cancelAndDelete(channelBusyRatioEvent);
    cancelAndDelete(camCryptoEvent);
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

        dccMinInterval = camMinInterval;
        adaptiveCamEvent = new cMessage("adaptive CAM event", SEND_ADAPTIVE_CAM);
        channelBusyRatioEvent = new cMessage("channel busy ratio event", UPDATE_CHANNEL_BUSY_RATIO);
        camCryptoEvent = new cMessage("CAM crypto processing event", SEND_CAM_AFTER_CRYPTO);

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

    if (msg->getKind() == PROCESS_CAM_AFTER_CRYPTO) {
        auto* bsm = check_and_cast<veins::DemoSafetyMessage*>(msg);
        processReceivedCam(bsm);
        delete bsm;
        return;
    }

    veins::TraCIDemo11p::handleSelfMsg(msg);
}

void pipelineSimulation::LatencyTraCIDemo11p::handlePositionUpdate(cObject* obj)
{
    veins::TraCIDemo11p::handlePositionUpdate(obj);
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
    pendingCam->addBitLength(cryptoOverheadBits);
    pendingCamPosition = curPosition;
    pendingCamHeadingRad = mobility->getHeading().getRad();
    pendingCamSpeed = mobility->getSpeed();
    scheduleAt(simTime() + cryptoTxDelay, camCryptoEvent);
}

void pipelineSimulation::LatencyTraCIDemo11p::sendPendingCam()
{
    if (!pendingCam) throw cRuntimeError("CAM crypto event fired without a pending CAM");

    auto* cam = pendingCam;
    pendingCam = nullptr;
    pdrExpectedReceptions += countPotentialCamReceivers();
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
    recordLatency(bsm);
    cryptoRxDelayTotalMs += cryptoRxDelay.dbl() * 1000.0;
    ++cryptoRxOperationCount;
    veins::TraCIDemo11p::onBSM(bsm);

    lastCamPosition = curPosition;
    lastCamHeadingRad = mobility->getHeading().getRad();
    lastCamSpeed = mobility->getSpeed();
    lastCamTime = simTime();
    hasSentCam = true;
    scheduleAdaptiveCam();
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

void pipelineSimulation::LatencyTraCIDemo11p::onBSM(veins::DemoSafetyMessage* bsm)
{
    if (isVehicleCam(bsm)) ++pdrReceivedReceptions;

    if (cryptoRxDelay == SIMTIME_ZERO) {
        processReceivedCam(bsm);
        return;
    }

    auto* pendingBsm = check_and_cast<veins::DemoSafetyMessage*>(bsm->dup());
    pendingBsm->setKind(PROCESS_CAM_AFTER_CRYPTO);
    scheduleAt(simTime() + cryptoRxDelay, pendingBsm);
}

void pipelineSimulation::LatencyTraCIDemo11p::onWSM(veins::BaseFrame1609_4* wsm)
{
    recordLatency(wsm);
    veins::TraCIDemo11p::onWSM(wsm);
}

void pipelineSimulation::LatencyTraCIDemo11p::finish()
{
    veins::TraCIDemo11p::finish();
    if (channelBusyRatioSamples > 0) {
        recordScalar("meanChannelBusyRatio", channelBusyRatioTotal / channelBusyRatioSamples);
    }
    recordScalar("cryptoTxDelayTotalMs", cryptoTxDelayTotalMs);
    recordScalar("cryptoTxOperationCount", cryptoTxOperationCount);
    recordScalar("cryptoRxDelayTotalMs", cryptoRxDelayTotalMs);
    recordScalar("cryptoRxOperationCount", cryptoRxOperationCount);
    recordScalar("pdrExpectedReceptions", pdrExpectedReceptions);
    recordScalar("pdrReceivedReceptions", pdrReceivedReceptions);
    if (latencySampleCount > 0) {
        recordScalar("packetLatencyTotalMs", latencyTotalMs);
        recordScalar("packetLatencySamples", latencySampleCount);
    }
}

void pipelineSimulation::LatencyTraCIDemo11p::recordLatency(const cMessage* message)
{
    const double latencyMs = (simTime() - message->getCreationTime()).dbl() * 1000.0;
    if (std::isfinite(latencyMs) && latencyMs >= 0) {
        latencyTotalMs += latencyMs;
        ++latencySampleCount;
    }
}

long pipelineSimulation::LatencyTraCIDemo11p::countPotentialCamReceivers() const
{
    auto* systemModule = getSimulation()->getSystemModule();
    long receiverCount = 0;
    const int nodeCount = systemModule->getSubmoduleVectorSize("node");

    for (int index = 0; index < nodeCount; ++index) {
        auto* node = systemModule->getSubmodule("node", index);
        auto* application = node
            ? dynamic_cast<LatencyTraCIDemo11p*>(node->getSubmodule("appl"))
            : nullptr;
        if (application && application != this && application->mobility &&
            curPosition.distance(application->curPosition) <= camCommunicationRange) {
            ++receiverCount;
        }
    }
    return receiverCount;
}

bool pipelineSimulation::LatencyTraCIDemo11p::isVehicleCam(const veins::DemoSafetyMessage* bsm) const
{
    return bsm->getPsid() == VEHICLE_CAM_PSID;
}
