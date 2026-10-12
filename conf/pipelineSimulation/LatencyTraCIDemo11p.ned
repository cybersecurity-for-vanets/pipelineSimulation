package pipelineSimulation;

import org.car2x.veins.modules.application.traci.TraCIDemo11p;

simple LatencyTraCIDemo11p extends TraCIDemo11p
{
    parameters:
        @class(pipelineSimulation::LatencyTraCIDemo11p);
        double camMinInterval @unit(s) = default(0.1s);
        double camMaxInterval @unit(s) = default(1s);
        double cbrWindow @unit(s) = default(1s);
        double camPositionThreshold @unit(m) = default(4m);
        double camHeadingThresholdDeg = default(4);
        double camSpeedThreshold @unit(mps) = default(0.5mps);
        double cbrLowThreshold = default(0.3);
        double cbrHighThreshold = default(0.6);
        int cryptoOverheadBits @unit(bit) = default(0bit);
        double cryptoTxDelay @unit(s) = default(0s);
        double cryptoRxDelay @unit(s) = default(0s);
}