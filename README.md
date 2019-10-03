# gevent-serial

A python 3 [gevent](https://gevent.org) friendly access to the serial port.

Deeply inspired by [pyserial](https://github.com/pyserial/pyserial). It tries to mimic
its interface but it doesn't aim to garantee full API compatibility.

For now only Linux (and possibly any BSD or posix complient system) is supported.

Support for local serial port, RFC2217, and RS485 (untested).

Includes an optional serial to TCP bridge (experimental).


## Installation

From within your favourite python environment:

```console
$ pip install gevent-serial
```

### Serial to TCP bridge

To be available, it requires you to install the optional server package
with:

```console
$ pip install gevent-serial[ser2tcp]
```