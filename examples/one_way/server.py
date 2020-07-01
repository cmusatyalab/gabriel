import common
import logging
from gabriel_server.network_engine import server_runner


ZMQ_ADDRESS_FORMAT = 'tcp://*:{}'


logging.basicConfig(format="%(levelname)s: %(message)s", level=logging.INFO)


def main():
    zmq_address = ZMQ_ADDRESS_FORMAT.format(common.ZMQ_PORT)
    server_runner.run(common.WEBSOCKET_PORT, zmq_address, num_tokens=2,
                      input_queue_maxsize=60)


if __name__ == '__main__':
    main()
