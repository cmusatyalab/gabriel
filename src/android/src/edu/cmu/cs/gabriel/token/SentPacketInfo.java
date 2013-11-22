package edu.cmu.cs.gabriel.token;

public class SentPacketInfo {
	public long sentTime;
	public int sentSize;

	public SentPacketInfo(long currentTimeMillis, int sentSize) {
		this.sentTime = currentTimeMillis;
		this.sentSize = sentSize;
	}

}
