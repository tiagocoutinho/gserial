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


# "for byte in data" fails for python3 as it returns ints instead of bytes
def iter_bytes(b):
    """Iterate over bytes, returning bytes instead of ints (python3)"""
    if isinstance(b, memoryview):
        b = b.tobytes()
    i = 0
    while True:
        a = b[i:i + 1]
        i += 1
        if a:
            yield a
        else:
            break


# all Python versions prior 3.x convert ``str([17])`` to '[17]' instead of '\x11'
# so a simple ``bytes(sequence)`` doesn't work for all versions
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
        # handle list of integers and bytes (one or more items) for Python 2 and 3
        return bytes(bytearray(seq))
