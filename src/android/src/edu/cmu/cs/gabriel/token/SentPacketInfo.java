package edu.cmu.cs.gabriel.token;

public class SentPacketInfo {
	public long generatedTime;
	public int sentSize;

	public SentPacketInfo(long currentTimeMillis, int sentSize) {
		this.generatedTime = currentTimeMillis;
		this.sentSize = sentSize;
	}

}
