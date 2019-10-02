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


# TODO: serial.serialutil.Serial.send_break() uses time.sleep()
#       so it needs to be hacked!
