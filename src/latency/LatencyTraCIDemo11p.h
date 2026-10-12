#pragma once

#include "veins/modules/application/traci/TraCIDemo11p.h"

namespace pipelineSimulation {

class LatencyTraCIDemo11p : public veins::TraCIDemo11p {
protected:
    double latencyTotalMs = 0;
    long latencySampleCount = 0;

    void onBSM(veins::DemoSafetyMessage* bsm) override;
    void onWSM(veins::BaseFrame1609_4* wsm) override;
    void finish() override;

    void recordLatency(const omnetpp::cMessage* message);
};

}