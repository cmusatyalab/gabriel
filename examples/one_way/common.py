import argparse


DEFAULT_SOURCE_NAME = '0'
DEFAULT_NUM_SOURCES = 1
DEFAULT_SERVER_HOST = 'localhost'
WEBSOCKET_PORT = 9099
ZMQ_PORT = 5555


def parse_source_name_server_host():
    parser = argparse.ArgumentParser()
    parser.add_argument('source_name', nargs='?', default=DEFAULT_SOURCE_NAME)
    parser.add_argument('server_host', nargs='?', default=DEFAULT_SERVER_HOST)
    return parser.parse_args()


def parse_num_sources_server_host():
    parser = argparse.ArgumentParser()
    parser.add_argument('num_sources', type=int, nargs='?',
                        default=DEFAULT_NUM_SOURCES)
    parser.add_argument('server_host', nargs='?', default=DEFAULT_SERVER_HOST)
    return parser.parse_args()
