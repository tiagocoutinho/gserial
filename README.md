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

The ser2tcp server needs a configuration file written in YAML. It consists of a
list of serial entries. Each entry describes the serial port address (url),
the TCP port (listener) and optionally a TCP mode and other serial line options
(baudrate, parity, byte size, etc.)

Here is an example:

```yaml
- url: /dev/ttyS0
  baudrate: 9600
  listener: :2217   # listen on all network interfaces, TCP port 2217
  mode: rfc2217

- url: /dev/ttyS1
  baudrate: 115200
  mode: raw
  listener: :2218

- url: loop://
  listener: :2219
  baudrate: 115200
  mode: raw

- url: loop://
  listener: :2220
  baudrate: 9600
  mode: rfc2217
```