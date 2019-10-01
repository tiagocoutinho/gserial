import gevent
import gserial.posix

def loop():
  for i in range(1000):
    print(i)
    gevent.sleep(0.4)

t1 = gevent.spawn(loop)
s1 = gserial.posix.Serial('/tmp/roadrunner')

s1.write(b'*IDN?\n')
print(s1.readline())

s1.write(b'*IDN?\n')
print(s1.readline())
