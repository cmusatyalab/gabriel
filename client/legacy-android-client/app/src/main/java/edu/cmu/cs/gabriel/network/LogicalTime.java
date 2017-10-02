package edu.cmu.cs.gabriel.network;

import java.util.concurrent.atomic.AtomicInteger;

public class LogicalTime {
    public AtomicInteger imageTime; // in # of frames
    public double audioTime; // in seconds

    public LogicalTime() {
        this.imageTime = new AtomicInteger(0);
        this.audioTime = 0;
    }
}
