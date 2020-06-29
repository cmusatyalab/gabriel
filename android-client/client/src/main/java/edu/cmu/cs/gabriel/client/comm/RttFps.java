package edu.cmu.cs.gabriel.client.comm;

public class RttFps {
    private final double rtt;
    private final double fps;

    public RttFps(double rtt, double fps) {
        this.rtt = rtt;
        this.fps = fps;
    }

    public double getRtt() {
        return this.rtt;
    }

    public double getFps() {
        return this.fps;
    }
}
