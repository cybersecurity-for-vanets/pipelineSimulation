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
        string cryptoCipher = default("none"); // FF1 block cipher for the sensitive CAM fields: "none", "aes", "lea" or "speck"
        // CAM size model (conf/cam_model.json); GeoNetworking + BTP headers go in headerLength.
        int camBaseBytes = default(156);
        int camLowFrequencyExtraBytes = default(158);
        int camCertificateExtraBytes = default(98);
        double camLowFrequencyInterval @unit(s) = default(0.5s);
        double camCertificateInterval @unit(s) = default(1s);
        // Deadline for one CAM crypto operation (queue wait + service); ETSI minimum CAM interval.
        double camProcessingDeadline @unit(s) = default(0.1s);
        int cryptoOverheadBits @unit(bit) = default(0bit);
        double cryptoTxDelay @unit(s) = default(0s);
        double cryptoRxDelay @unit(s) = default(0s);
        // Per-message vectors (latency, processing, CPU queue wait, IPG, AoI, CAM bits) for percentiles; large, off by default.
        // Warm-up: scalars and per-message vectors restart at this time (0s = no warm-up).
        double warmupTime @unit(s) = default(0s);
        bool recordMessageVectors = default(false);
        int messageVectorStride = default(1); // keep every Nth sample of each vector
}