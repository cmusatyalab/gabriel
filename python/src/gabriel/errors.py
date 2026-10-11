"""The errors Gabriel raises. Each says what went wrong."""


class GabrielError(Exception):
    """A failed call into Gabriel."""


class InvalidArgumentError(GabrielError, ValueError):
    """An argument was invalid, e.g. an unknown name or a malformed flow."""


class SizeExceededError(GabrielError, ValueError):
    """A payload is larger than its maximum size."""


class AgainError(GabrielError):
    """Nothing is available yet. The bindings turn it into None or False, so it seldom surfaces."""


class InternalError(GabrielError, RuntimeError):
    """Gabriel failed internally, e.g. in its shared memory transport."""


class WrongThreadError(GabrielError, RuntimeError):
    """A producer, engine or subscription was used from a thread other than the one that made it."""


class ClosedError(GabrielError, ValueError):
    """The handle was already closed."""
