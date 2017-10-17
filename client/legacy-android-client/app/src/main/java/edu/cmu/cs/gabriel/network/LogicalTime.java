package edu.cmu.cs.gabriel.network;

import java.util.concurrent.atomic.AtomicInteger;

import edu.cmu.cs.gabriel.Const;

public class LogicalTime {
    public AtomicInteger imageTime; // in # of frames
    public double audioTime; // in seconds

    public LogicalTime() {
        this.imageTime = new AtomicInteger(0);
        this.audioTime = 0;
    }

    public void increaseAudioTime(double n) { // n is in seconds
        this.audioTime += n;
        this.imageTime.set((int) (this.audioTime * 15));
    }

    public void increaseImageTime(int n) { // n is in # of frames
        this.imageTime.getAndAdd(n);
        this.audioTime = this.imageTime.doubleValue() / 15;
    }
}
