import logging
import zmq
from gabriel_protocol import gabriel_pb2
from gabriel_server import network_engine


TEN_SECONDS = 10000
REQUEST_RETRIES = 3


logger = logging.getLogger(__name__)


def run(engine, computation_type, server_address, engine_name,
        all_responses_required=False, timeout=TEN_SECONDS,
        request_retries=REQUEST_RETRIES):
    context = zmq.Context()

    while request_retries > 0:
        socket = context.socket(zmq.REQ)
        socket.connect(server_address)
        from_standalone_engine = gabriel_pb2.FromStandaloneEngine()
        from_standalone_engine.welcome.computation_type = computation_type
        from_standalone_engine.welcome.all_responses_required = (
            all_responses_required)
        from_standalone_engine.welcome.engine_name = engine_name
        socket.send(from_standalone_engine.SerializeToString())
        logger.info('Sent welcome message to server')

        while True:
            if socket.poll(timeout) == 0:
                logger.warning('No response from server')
                socket.setsockopt(zmq.LINGER, 0)
                socket.close()
                request_retries -= 1
                break

            message_from_server = socket.recv()
            if message_from_server == network_engine.HEARTBEAT:
                socket.send(network_engine.HEARTBEAT)
                continue

            input_frame = gabriel_pb2.InputFrame()
            input_frame.ParseFromString(message_from_server)
            result_wrapper = engine.handle(input_frame)

            from_standalone_engine = gabriel_pb2.FromStandaloneEngine()
            from_standalone_engine.result_wrapper.CopyFrom(result_wrapper)
            socket.send(from_standalone_engine.SerializeToString())

    logger.warning('Ran out of retires. Abandoning server connection.')
