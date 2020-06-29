import argparse


DEFAULT_SOURCE_NAME = '0'
DEFAULT_NUM_SOURCES = 1


def parse_source_name():
    parser = argparse.ArgumentParser()
    parser.add_argument('source_name', nargs='?', default=DEFAULT_SOURCE_NAME)
    args = parser.parse_args()
    return args.source_name


def parse_num_sources():
    parser = argparse.ArgumentParser()
    parser.add_argument('num_sources', type=int, nargs='?',
                        default=DEFAULT_NUM_SOURCES)
    args = parser.parse_args()
    return args.num_sources
