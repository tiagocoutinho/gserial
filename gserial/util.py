import time


def iter_bytes(b):
    """Iterate over bytes, returning bytes instead of ints (python3)"""
    if isinstance(b, memoryview):
        b = b.tobytes()
    for i in range(len(b)):
        yield b[i:i + 1]


def to_bytes(seq):
    """convert a sequence to a bytes type"""
    if isinstance(seq, bytes):
        return seq
    elif isinstance(seq, bytearray):
        return bytes(seq)
    elif isinstance(seq, memoryview):
        return seq.tobytes()
    elif isinstance(seq, str):
        raise TypeError('str is not supported, please encode to bytes: {!r}'.format(seq))
    else:
        return bytes(seq)


class Strip(object):
    """
    Encapsulate object with a short str/repr/format.
    Useful to have in log messages since it only computes the representation
    if the log message is recorded. Example::

        >>> import logging
        >>> from gserial.util import Strip
        >>> logging.basicConfig(level=logging.DEBUG)
        >>> msg_from_socket = 'Here it is my testament: ' + 50*'bla '
        >>> logging.debug('Received: %s', Strip(msg_from_socket))
        DEBUG:root:Received: Here it is my testament: bla bla bla bla bla [...]
    """

    __slots__ = "obj", "max_len"

    def __init__(self, obj, max_len=80):
        self.obj = obj
        self.max_len = max_len

    def __strip(self, s):
        max_len = self.max_len
        if len(s) > max_len:
            suffix = " [...]"
            s = s[: max_len - len(suffix)] + suffix
        return s

    def __str__(self):
        return self.__strip(str(self.obj))

    def __repr__(self):
        return self.__strip(repr(self.obj))

    def __format__(self, format_spec):
        return self.__strip(format(self.obj, format_spec))


class Timeout(object):
    """
    Abstraction for timeout operations. Using time.monotonic()

    The class can also be initialized with 0 or None, in order to support
    non-blocking and fully blocking I/O operations. The attributes
    is_non_blocking and is_infinite are set accordingly.
    """

    def __init__(self, duration):
        """Initialize a timeout with given duration"""
        self.is_infinite = (duration is None)
        self.is_non_blocking = (duration == 0)
        self.duration = duration
        if duration is not None:
            self.target_time = time.monotonic() + duration
        else:
            self.target_time = None

    def expired(self):
        """Return a boolean, telling if the timeout has expired"""
        return self.target_time is not None and self.time_left() <= 0

    def time_left(self):
        """Return how many seconds are left until the timeout expires"""
        if self.is_non_blocking:
            return 0
        elif self.is_infinite:
            return None
        else:
            delta = self.target_time - time.monotonic()
            if delta > self.duration:
                # clock jumped, recalculate
                self.target_time = time.monotonic() + self.duration
                return self.duration
            else:
                return max(0, delta)

    def restart(self, duration):
        """
        Restart a timeout, only supported if a timeout was already set up
        before.
        """
        self.duration = duration
        self.target_time = time.monotonic() + duration
