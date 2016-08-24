using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using System.Threading.Tasks;

namespace gabriel_client
{
    class TokenController
    {
        private int _currentToken = 0;
        private Object _tokenLock = new Object();
        private ConcurrentDictionary<long, SentPacketInfo> _sentPackets = new ConcurrentDictionary<long, SentPacketInfo>();
        private long _prevRecvFrameID = 0;
        private MyLogger _myLogger;

        public TokenController(int tokenSize, MyLogger myLogger)
        {
            this._currentToken = tokenSize;
            this._myLogger = myLogger;
        }

        public int GetCurrentToken()
        {
            return _currentToken;
        }

        public void IncreaseTokens(int count)
        {
            lock (_tokenLock)
            {
                _currentToken += count;
            }
        }

        public void DecreaseToken()
        {
            lock (_tokenLock)
            {
                _currentToken--;
            }
        }

        public void LogSentPacket(long frameID, long dataTime, long compressedTime)
        {
            this._sentPackets.TryAdd(frameID, new SentPacketInfo(dataTime, compressedTime));
        }

        public async Task ProcessReceivedPacket(ReceivedPacketInfo receivedPacket)
        {
            long recvFrameID = receivedPacket.frameID;
            string recvEngineID = receivedPacket.engineID;
            SentPacketInfo sentPacket = null;
            bool isSuccess;

            // increase appropriate amount of tokens
            int increaseCount = 0;
            for (long frameID = _prevRecvFrameID + 1; frameID < recvFrameID; frameID++)
            {
                sentPacket = null;
                if (Const.IS_EXPERIMENT)
                {
                    // Do not remove since we need to measure latency even for the late response
                    isSuccess = _sentPackets.TryGetValue(frameID, out sentPacket);
                }
                else
                {
                    isSuccess = _sentPackets.TryRemove(frameID, out sentPacket);
                }
                if (isSuccess == true)
                {
                    increaseCount++;
                }
            }
            IncreaseTokens(increaseCount);

            // deal with the current response
            isSuccess = _sentPackets.TryGetValue(recvFrameID, out sentPacket);
            if (isSuccess == true)
            {
                // do not increase token if have already received duplicated ack
                if (recvFrameID > _prevRecvFrameID)
                {
                    IncreaseTokens(1);
                }

                if (Const.IS_EXPERIMENT)
                {
                    string log = recvFrameID + "\t" + recvEngineID + "\t" +
                            sentPacket.generatedTime + "\t" + sentPacket.compressedTime + "\t" +
                            receivedPacket.msgRecvTime + "\t" + receivedPacket.guidanceDoneTime + "\t" +
                            receivedPacket.status;
                    await _myLogger.WriteString(log + "\n");
                }
            }
            if (recvFrameID > _prevRecvFrameID)
            {
                _prevRecvFrameID = recvFrameID;
            }
        }
    }

    class SentPacketInfo
    {
        public long generatedTime;
        public long compressedTime;

        public SentPacketInfo(long generatedTime, long compressedTime)
        {
            this.generatedTime = generatedTime;
            this.compressedTime = compressedTime;
        }
    }

    class ReceivedPacketInfo
    {
        public long frameID;
        public string engineID;
        public string status;
        public long msgRecvTime;
        public long guidanceDoneTime;

        public ReceivedPacketInfo(long frameID, String engineID, String status)
        {
            this.frameID = frameID;
            this.engineID = engineID;
            this.status = status;
            this.msgRecvTime = -1;
            this.guidanceDoneTime = -1;
        }

        public void setMsgRecvTime(long time)
        {
            msgRecvTime = time;
        }

        public void setGuidanceDoneTime(long time)
        {
            guidanceDoneTime = time;
        }
    }
}
