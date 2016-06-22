package edu.cmu.cs.gabriel.token;

public class SentPacketInfo {
    public long generatedTime;
    public long compressedTime;

    public SentPacketInfo(long generatedTime, long compressedTime) {
        this.generatedTime = generatedTime;
        this.compressedTime = compressedTime;
    }
}
