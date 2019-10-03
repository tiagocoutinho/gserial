import serial as _serial

with open(_serial.__file__) as _fserial:
    exec(_fserial.read())

del _serial

from .base import SerialBase

VERSION = '0.1.0'

import os
if os.name == 'posix':
    from .posix import Serial, PosixPollSerial, VTIMESerial # noqa
else:
    raise ImportError("Sorry: no implementation for your platform ('{}') " \
                      "available".format(os.name))
del os

protocol_handler_packages = [
    'gserial.urlhandler',
]


