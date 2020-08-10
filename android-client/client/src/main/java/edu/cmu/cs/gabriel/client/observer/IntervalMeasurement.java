package edu.cmu.cs.gabriel.client.observer;

public class IntervalMeasurement {
    private final String sourceName;
    private final double intervalRtt;
    private final double overallRtt;
    private final double intervalFps;
    private final double overallFps;

    IntervalMeasurement(String sourceName, double intervalRtt, double overallRtt,
                        double intervalFps, double overallFps) {
        this.sourceName = sourceName;
        this.intervalRtt = intervalRtt;
        this.overallRtt = overallRtt;
        this.intervalFps = intervalFps;
        this.overallFps = overallFps;
    }

    public String getSourceName() {
        return this.sourceName;
    }

    public double getIntervalRtt() {
        return this.intervalRtt;
    }

    public double getOverallRtt() {
        return this.overallRtt;
    }

    public double getIntervalFps() {
        return this.intervalFps;
    }

    public double getOverallFps() {
        return this.overallFps;
    }
}
