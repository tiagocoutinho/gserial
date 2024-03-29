import os
import pty
import tty
import time

name = '/tmp/roadrunner'
if os.path.islink(name):
    os.unlink(name)
master, slave = pty.openpty()
tty.setraw(master)
port = os.ttyname(slave)
os.symlink(port, name)


def handle():
    while True:
        data = os.read(master, 1024)
        datau = data.upper().strip().decode()
        print('processing {!r}'.format(data))
        if datau == '*IDN?':
            msg = 'RoadRunner, ACME Inc., v1013, 234567\n'
        else:
            msg = 'ERROR: Unknown command\n'
        print('start reply with: \'', end='', flush=True)
        for c in msg:
            os.write(master, c.encode())
            if c in '\n\r\t':
                c = repr(c)[1:-1]
            print(c, end='', flush=True)
            time.sleep(0.02)
        print('\'... [DONE]')


print('Ready to accept requests on {}'.format(name))
try:
    handle()
except KeyboardInterrupt:
    print('Ctrl-C pressed. Bailing out')
finally:
    os.unlink(name)
