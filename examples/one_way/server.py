import logging
from gabriel_server.network_engine import server_runner


logging.basicConfig(format="%(levelname)s: %(message)s", level=logging.INFO)


server_runner.run(websocket_port=9099, zmq_address='tcp://*:5555', num_tokens=2,
                  input_queue_maxsize=60)
