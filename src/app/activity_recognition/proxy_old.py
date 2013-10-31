import socket
import sys
import struct
import time
from multiprocessing import Process, Manager

queue = Manager().Queue()

def clientToControl(HOST, PORT):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    try:
        sock.connect((HOST, PORT))
        print "Connected to control VM at %s:%d" % (HOST, PORT)
        print sock.getpeername()

        received = sock.recv(4)
        while received:
            header_size = struct.unpack("!I", received)[0]
            #print "header size: %d" % header_size
            received = sock.recv(4)
            data_size = struct.unpack("!I", received)[0]
            #print "data size: %d" % data_size

            header = ""
            received_size = 0
            while received_size < header_size:
                header_tmp = sock.recv(header_size - received_size)
                header += header_tmp
                received_size += len(header_tmp)
            #print received_size

            data = ""
            received_size = 0
            while received_size < data_size:
                data_tmp = sock.recv(data_size - received_size)
                data += data_tmp
                received_size += len(data_tmp)
            #print received_size
            #print "Got one frame"

            queue.put((header, data))
            
            received = sock.recv(4)

    except Exception as e:
        print "Caught exception"
        sys.stderr.write(str(e))
        sys.exit(1)
    finally:
        sock.close()

    print "client_to_control exits unexpectedly"

def clientToApp(HOST, PORT):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    try:
        sock.connect((HOST, PORT))
        print "Connected to application at %s:%d" % (HOST, PORT)

        while True:
            if not queue.empty():
                (header, data) = queue.get()
                #print "Got something"
                data_size = struct.pack("!I", len(data))
                sock.sendall(data_size)
                sock.sendall(data)
            else:
                time.sleep(0.001)
        sock.sendall(data + "\n")
    
    except Exception as e:
        sys.stderr.write(str(e))
        sys.exit(1)
    finally:
        sock.close()

    print "client_to_control exits unexpectedly"

def main():
    # TODO find IP and port of control VM
    controlHOST, controlPORT = "128.2.210.197", 10101
    # TODO find port of app
    appHOST, appPORT = "localhost", 8080

    #queue = Manager().Queue(maxsize = 10)
    p1 = Process(target = clientToControl, args = (controlHOST, controlPORT, ))
    p1.start()
    #p2 = Process(target = clientToApp, args = (appHOST, appPORT, ))
    #p2.start()
    p1.join()
    #p2.join()
    
if __name__ == "__main__":
    main()
