from serial import serialposix as _serialposix

# load a copy of the original serial.serialposix module right here
with open(_serialposix.__file__) as _fposix:
    exec(_fposix.read())

del _serialposix
del _fposix

# serialposix uses select.select to both read and write from the file
# descriptor. Below We inject gevent version of select and efectively
# force Serial object to use gevent select library instead!

from gevent import select

# Hack send_break: it seems to be the only one to use time.sleep
from gserial.base import SerialBase
Serial.send_break = SerialBase.send_break
