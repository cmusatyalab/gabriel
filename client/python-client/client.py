#! /usr/bin/env python

import socket
import struct
import threading
import Queue
import StringIO
import cv2
import json
from time import sleep
import pdb
import sys
import select
import numpy as np
from config import Config
import base64
import protocol
from socketLib import ClientCommand, ClientReply, SocketClientThread

class GabrielSocketCommand(ClientCommand):
    STREAM=len(ClientCommand.ACTIONS)
    ACTIONS=ClientCommand.ACTIONS + [STREAM]
    LISTEN=len(ACTIONS)
    ACTIONS.append(LISTEN)
    
    def __init__(self, type, data=None):
        super(self.__class__.__name__, self).__init__()
        
        
class VideoStreamingThread(SocketClientThread):
    def __init__(self, cmd_q=None, reply_q=None):
        super(self.__class__, self).__init__(cmd_q, reply_q)
        self.handlers[GabrielSocketCommand.STREAM] = self._handle_STREAM
        self.is_streaming=False

    def run(self):
        while self.alive.isSet():
            try:
                cmd = self.cmd_q.get(True, 0.1)
                self.handlers[cmd.type](cmd)
            except Queue.Empty as e:
                continue

    # tokenm: token manager
    def _handle_STREAM(self, cmd):
        tokenm = cmd.data
        self.is_streaming=True
        video_capture = cv2.VideoCapture(0)
        id=0
        while self.alive.isSet() and self.is_streaming:
            # will be put into sleep if token is not available
            tokenm.getToken()
            ret, frame = video_capture.read()
            ret, jpeg_frame=cv2.imencode('.jpg', frame)
            header={protocol.Protocol_client.JSON_KEY_FRAME_ID : str(id)}
            header_json=json.dumps(header)
            self._handle_SEND(ClientCommand(ClientCommand.SEND, header_json))
            self._handle_SEND(ClientCommand(ClientCommand.SEND, jpeg_frame.tostring()))
            id+=1
        video_capture.release()        

class ResultReceivingThread(SocketClientThread):
    def __init__(self, cmd_q=None, reply_q=None):
        super(self.__class__, self).__init__(cmd_q, reply_q)
        self.handlers[GabrielSocketCommand.LISTEN] =  self._handle_LISTEN
        self.is_listening=False
        
    def run(self):
        while self.alive.isSet():
            try:
                cmd = self.cmd_q.get(True, 0.1)
                self.handlers[cmd.type](cmd)
            except Queue.Empty as e:
                continue

    def _handle_LISTEN(self, cmd):
        tokenm = cmd.data
        self.is_listening=True
        while self.alive.isSet() and self.is_listening:
            if self.socket:
                input=[self.socket]
                inputready,outputready,exceptready = select.select(input,[],[]) 
                for s in inputready: 
                    if s == self.socket: 
                        # handle the server socket
                        data = self._recv_gabriel_data()
                        self.reply_q.put(self._success_reply(data))
                        tokenm.putToken()
        
    def _recv_gabriel_data(self):
        data_size = struct.unpack("!I", self._recv_n_bytes(4))[0]
        data = self._recv_n_bytes(data_size)
        return data
        
# token manager implementing gabriel's token mechanism
class tokenManager(object):
    def __init__(self, token_num):
        super(self.__class__, self).__init__()        
        self.token_num=token_num
        # token val is [0..token_num)
        self.token_val=token_num -1
        self.lock = threading.Lock()
        self.has_token_cv = threading.Condition(self.lock)

    def _inc(self):
        self.token_val= (self.token_val + 1) if (self.token_val<self.token_num) else (self.token_val)

    def _dec(self):
        self.token_val= (self.token_val - 1) if (self.token_val>=0) else (self.token_val)

    def empty(self):
        return (self.token_val<0)

    def getToken(self):
        with self.has_token_cv:
            while self.token_val < 0:
                self.has_token_cv.wait()
            self._dec()

    def putToken(self):
        with self.has_token_cv:
            self._inc()                    
            if self.token_val >= 0:
                self.has_token_cv.notifyAll()

def run(sig_frame_available=None):
    tokenm = tokenManager(Config.TOKEN)
    stream_cmd_q = Queue.Queue()
    result_cmd_q = Queue.Queue()    
    result_reply_q = Queue.Queue()
    video_streaming_thread=VideoStreamingThread(cmd_q=stream_cmd_q)
    stream_cmd_q.put(ClientCommand(ClientCommand.CONNECT, (Config.GABRIEL_IP, Config.VIDEO_STREAM_PORT)) )
    stream_cmd_q.put(ClientCommand(GabrielSocketCommand.STREAM, tokenm))    
    result_receiving_thread = ResultReceivingThread(cmd_q=result_cmd_q, reply_q=result_reply_q)    
    result_cmd_q.put(ClientCommand(ClientCommand.CONNECT, (Config.GABRIEL_IP, Config.RESULT_RECEIVING_PORT)) )
    result_cmd_q.put(ClientCommand(GabrielSocketCommand.LISTEN, tokenm))
    result_receiving_thread.start()
    sleep(0.1)
    video_streaming_thread.start()

    try:
        while True:
            resp=result_reply_q.get()
            # connect and send also send reply to reply queue without any data attached
            if resp.type == ClientReply.SUCCESS and type(resp.data) is str:
                resp=json.loads(resp.data)['result']
                img=json.loads(resp)['val']
                if sig_frame_available == None:
                    print 'resp:{}'.format(img[:100])
                else:
                    # display received image on the pyqt ui
                    data=base64.b64decode(img)
                    np_data=np.fromstring(data, dtype=np.uint8)
                    frame=cv2.imdecode(np_data,cv2.IMREAD_COLOR)
                    rgb_frame = cv2.cvtColor(frame,cv2.COLOR_BGR2RGB)                    
                    sig_frame_available.emit(rgb_frame)
    except KeyboardInterrupt:
        video_streaming_thread.join()
        result_receiving_thread.join()
        with tokenm.has_token_cv:
            tokenm.has_token_cv.notifyAll()

if __name__ == '__main__':
    run()
