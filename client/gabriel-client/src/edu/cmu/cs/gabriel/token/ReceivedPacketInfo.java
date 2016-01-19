package edu.cmu.cs.gabriel.token;

public class ReceivedPacketInfo {
    public long frameID;
    public String engineID;
    public String status;
    public long msgRecvTime;
    public long guidanceDoneTime;

    public ReceivedPacketInfo(long frameID, String engineID, String status) {
        this.frameID = frameID;
        this.engineID = engineID;
        this.status = status;
        this.msgRecvTime = -1;
        this.guidanceDoneTime = -1;
    }

    public void setMsgRecvTime(long time) {
        msgRecvTime = time;
    }

    public void setGuidanceDoneTime(long time) {
        guidanceDoneTime = time;
    }
}
