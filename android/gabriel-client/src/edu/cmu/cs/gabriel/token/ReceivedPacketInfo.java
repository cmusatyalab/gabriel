package edu.cmu.cs.gabriel.token;

public class ReceivedPacketInfo {
	public long frameID;
	public String engineID;
	public String status;
	public long msg_recv_time;
	public long guidance_done_time;

	public ReceivedPacketInfo(long frameID, String engineID, String status) {
		this.frameID = frameID;
		this.engineID = engineID;
		this.status = status;
		this.msg_recv_time = -1;
		this.guidance_done_time = -1;
	}

	public void setMsgRecvTime(long time) {
		msg_recv_time = time;
    }
	
	public void setGuidanceDoneTime(long time) {
		guidance_done_time = time;
    }
}
