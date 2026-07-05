from google.protobuf import any_pb2 as _any_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class PayloadType(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    PAYLOAD_TYPE_UNSPECIFIED: _ClassVar[PayloadType]
    TEXT: _ClassVar[PayloadType]
    IMAGE: _ClassVar[PayloadType]
    AUDIO: _ClassVar[PayloadType]
    VIDEO: _ClassVar[PayloadType]
    CONTROL: _ClassVar[PayloadType]
    OTHER: _ClassVar[PayloadType]

class StatusCode(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    SUCCESS: _ClassVar[StatusCode]
    UNSPECIFIED_ERROR: _ClassVar[StatusCode]
    ENGINE_ERROR: _ClassVar[StatusCode]
    WRONG_INPUT_FORMAT: _ClassVar[StatusCode]
    NO_ENGINE_FOR_INPUT: _ClassVar[StatusCode]
    NO_TOKENS: _ClassVar[StatusCode]
    SERVER_DROPPED_FRAME: _ClassVar[StatusCode]
PAYLOAD_TYPE_UNSPECIFIED: PayloadType
TEXT: PayloadType
IMAGE: PayloadType
AUDIO: PayloadType
VIDEO: PayloadType
CONTROL: PayloadType
OTHER: PayloadType
SUCCESS: StatusCode
UNSPECIFIED_ERROR: StatusCode
ENGINE_ERROR: StatusCode
WRONG_INPUT_FORMAT: StatusCode
NO_ENGINE_FOR_INPUT: StatusCode
NO_TOKENS: StatusCode
SERVER_DROPPED_FRAME: StatusCode

class InputFrame(_message.Message):
    __slots__ = ("payload_type", "string_payload", "byte_payload", "any_payload")
    PAYLOAD_TYPE_FIELD_NUMBER: _ClassVar[int]
    STRING_PAYLOAD_FIELD_NUMBER: _ClassVar[int]
    BYTE_PAYLOAD_FIELD_NUMBER: _ClassVar[int]
    ANY_PAYLOAD_FIELD_NUMBER: _ClassVar[int]
    payload_type: PayloadType
    string_payload: str
    byte_payload: bytes
    any_payload: _any_pb2.Any
    def __init__(self, payload_type: _Optional[_Union[PayloadType, str]] = ..., string_payload: _Optional[str] = ..., byte_payload: _Optional[bytes] = ..., any_payload: _Optional[_Union[_any_pb2.Any, _Mapping]] = ...) -> None: ...

class FromClient(_message.Message):
    __slots__ = ("frame_id", "producer_id", "target_engine_ids", "input_frame", "client_info")
    FRAME_ID_FIELD_NUMBER: _ClassVar[int]
    PRODUCER_ID_FIELD_NUMBER: _ClassVar[int]
    TARGET_ENGINE_IDS_FIELD_NUMBER: _ClassVar[int]
    INPUT_FRAME_FIELD_NUMBER: _ClassVar[int]
    CLIENT_INFO_FIELD_NUMBER: _ClassVar[int]
    frame_id: int
    producer_id: str
    target_engine_ids: _containers.RepeatedScalarFieldContainer[str]
    input_frame: InputFrame
    client_info: _any_pb2.Any
    def __init__(self, frame_id: _Optional[int] = ..., producer_id: _Optional[str] = ..., target_engine_ids: _Optional[_Iterable[str]] = ..., input_frame: _Optional[_Union[InputFrame, _Mapping]] = ..., client_info: _Optional[_Union[_any_pb2.Any, _Mapping]] = ...) -> None: ...

class Status(_message.Message):
    __slots__ = ("code", "message")
    CODE_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    code: StatusCode
    message: str
    def __init__(self, code: _Optional[_Union[StatusCode, str]] = ..., message: _Optional[str] = ...) -> None: ...

class Result(_message.Message):
    __slots__ = ("status", "string_result", "bytes_result", "any_result", "target_engine_id", "frame_id")
    STATUS_FIELD_NUMBER: _ClassVar[int]
    STRING_RESULT_FIELD_NUMBER: _ClassVar[int]
    BYTES_RESULT_FIELD_NUMBER: _ClassVar[int]
    ANY_RESULT_FIELD_NUMBER: _ClassVar[int]
    TARGET_ENGINE_ID_FIELD_NUMBER: _ClassVar[int]
    FRAME_ID_FIELD_NUMBER: _ClassVar[int]
    status: Status
    string_result: str
    bytes_result: bytes
    any_result: _any_pb2.Any
    target_engine_id: str
    frame_id: int
    def __init__(self, status: _Optional[_Union[Status, _Mapping]] = ..., string_result: _Optional[str] = ..., bytes_result: _Optional[bytes] = ..., any_result: _Optional[_Union[_any_pb2.Any, _Mapping]] = ..., target_engine_id: _Optional[str] = ..., frame_id: _Optional[int] = ...) -> None: ...

class ToClient(_message.Message):
    __slots__ = ("welcome", "result_wrapper", "control")
    class Welcome(_message.Message):
        __slots__ = ("num_tokens_per_producer", "engine_ids")
        NUM_TOKENS_PER_PRODUCER_FIELD_NUMBER: _ClassVar[int]
        ENGINE_IDS_FIELD_NUMBER: _ClassVar[int]
        num_tokens_per_producer: int
        engine_ids: _containers.RepeatedScalarFieldContainer[str]
        def __init__(self, num_tokens_per_producer: _Optional[int] = ..., engine_ids: _Optional[_Iterable[str]] = ...) -> None: ...
    class Control(_message.Message):
        __slots__ = ("engine_ids",)
        ENGINE_IDS_FIELD_NUMBER: _ClassVar[int]
        engine_ids: _containers.RepeatedScalarFieldContainer[str]
        def __init__(self, engine_ids: _Optional[_Iterable[str]] = ...) -> None: ...
    class ResultWrapper(_message.Message):
        __slots__ = ("producer_id", "return_token", "result")
        PRODUCER_ID_FIELD_NUMBER: _ClassVar[int]
        RETURN_TOKEN_FIELD_NUMBER: _ClassVar[int]
        RESULT_FIELD_NUMBER: _ClassVar[int]
        producer_id: str
        return_token: bool
        result: Result
        def __init__(self, producer_id: _Optional[str] = ..., return_token: _Optional[bool] = ..., result: _Optional[_Union[Result, _Mapping]] = ...) -> None: ...
    WELCOME_FIELD_NUMBER: _ClassVar[int]
    RESULT_WRAPPER_FIELD_NUMBER: _ClassVar[int]
    CONTROL_FIELD_NUMBER: _ClassVar[int]
    welcome: ToClient.Welcome
    result_wrapper: ToClient.ResultWrapper
    control: ToClient.Control
    def __init__(self, welcome: _Optional[_Union[ToClient.Welcome, _Mapping]] = ..., result_wrapper: _Optional[_Union[ToClient.ResultWrapper, _Mapping]] = ..., control: _Optional[_Union[ToClient.Control, _Mapping]] = ...) -> None: ...

class FromStandaloneEngine(_message.Message):
    __slots__ = ("welcome", "result")
    class Welcome(_message.Message):
        __slots__ = ("engine_id", "all_responses_required")
        ENGINE_ID_FIELD_NUMBER: _ClassVar[int]
        ALL_RESPONSES_REQUIRED_FIELD_NUMBER: _ClassVar[int]
        engine_id: str
        all_responses_required: bool
        def __init__(self, engine_id: _Optional[str] = ..., all_responses_required: _Optional[bool] = ...) -> None: ...
    WELCOME_FIELD_NUMBER: _ClassVar[int]
    RESULT_FIELD_NUMBER: _ClassVar[int]
    welcome: FromStandaloneEngine.Welcome
    result: Result
    def __init__(self, welcome: _Optional[_Union[FromStandaloneEngine.Welcome, _Mapping]] = ..., result: _Optional[_Union[Result, _Mapping]] = ...) -> None: ...
