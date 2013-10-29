import socket
import sys
from multiprocessing import Process, Manager

def clientToControl(queue, HOST, PORT):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    try:
        sock.connect((HOST, PORT))

        while True:
            received = sock.recv(4)
            header_size = struct.unpack("!I", received)[0]
            received = sock.recv(4)
            data_size = struct.unpack("!I", received)[0]
            header = sock.recv(header_size)
            data = sock.recv(data_size)

            queue.put((header, data))

    except Exception as e:
        sys.stderr.write(str(e))
        sys.exit(1)
    finally:
        sock.close()

    print "client_to_control exits unexpectedly"

def clientToApp(queue, HOST, PORT):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    try:
        sock.connect((HOST, PORT))
        while True:
            if not queue.empty():
                (header, data) = queue.get()
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
    controlHOST, controlPORT = "localhost", 9999
    # TODO find port of app
    appHOST, appPORT = "localhost", 9999

    queue = Manager().Queue(maxsize = 10)
    p = Process(target = clientToControl, args = (queue, controlHOST, controlPORT, ))
    p.start()
    p = Process(target = clientToApp, args = (queue, appHOST, appPORT, ))
    p.start()
    
if __name__ == "__main__":
    main()
