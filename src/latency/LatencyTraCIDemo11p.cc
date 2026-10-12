#include "LatencyTraCIDemo11p.h"

#include <cmath>

using namespace omnetpp;

Define_Module(pipelineSimulation::LatencyTraCIDemo11p);

void pipelineSimulation::LatencyTraCIDemo11p::onBSM(veins::DemoSafetyMessage* bsm)
{
    recordLatency(bsm);
    veins::TraCIDemo11p::onBSM(bsm);
}

void pipelineSimulation::LatencyTraCIDemo11p::onWSM(veins::BaseFrame1609_4* wsm)
{
    recordLatency(wsm);
    veins::TraCIDemo11p::onWSM(wsm);
}

void pipelineSimulation::LatencyTraCIDemo11p::finish()
{
    veins::TraCIDemo11p::finish();
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