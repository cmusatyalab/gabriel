package edu.cmu.cs.gabriel.client.observer;

public class SourceRttFps {
    private final String sourceName;
    private final double rtt;
    private final double fps;

    SourceRttFps(String sourceName, double rtt, double fps) {
        this.sourceName = sourceName;
        this.rtt = rtt;
        this.fps = fps;
    }

    public String getSourceName() {
        return this.sourceName;
    }

    public double getRtt() {
        return this.rtt;
    }

    public double getFps() {
        return this.fps;
    }
}
