package edu.cmu.cs.gabriel.token;

public class SentPacketInfo {
    public long generatedTime;
    public long compressedTime;
    public int sentSize;

    public SentPacketInfo(long generatedTime, long compressedTime, int sentSize) {
        this.generatedTime = generatedTime;
        this.compressedTime = compressedTime;
        this.sentSize = sentSize;
    }

}
