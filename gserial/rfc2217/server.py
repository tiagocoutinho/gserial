import os
import logging

import gevent.server

import gserial
from .manager import PortManager


log = logging.getLogger('gserial.rfc2217.server')


def serial_to_tcp(manager):
    src = manager.serial.read()
    if src:
        # escape outgoing data when needed (Telnet IAC (0xff) character)
        dst = manager.escape(src)
        manager.connection.write(dst)
    return src


def tcp_to_serial(manager):
    src = manager.connection.recv(1024)
    if src:
        dst = b''.join(manager.filter(src))
        manager.serial.write(dst)
    return src


def poll_statusline(manager, period=1):
    while True:
        gevent.sleep(period)
        manager.check_modem_lines()


def serial_to_tcp_loop(manager):
    while True:
        serial_to_tcp(manager)


def tcp_to_serial_loop(manager):
    while True:
        if not tcp_to_serial(manager):
            break


def serial_for_config(config):
    opts = dict(config)
    opts.pop('listener')
    serial_url = opts.pop('url')
    if 'open' in opts:
        opts['do_not_open'] = not config.pop('open')
    if 'parity' in config:
        opts['parity'] = config['parity'][:1].upper()
    if 'timeout' in config:
        opts['timeout'] = config['timeout'] if config['timeout'] >= 0 else None
    print(opts)
    serial = gserial.serial_for_url(serial_url, **opts)
    print(serial.baudrate)
    return serial

class Bridge:

    class Connection:
        def __init__(self, sock):
            self.write = sock.sendall
            self.recv = sock.recv

    def __init__(self, config):
        self.config = config
        sl = config['url']
        listener = config['listener']
        self.log = log.getChild('Bridge({}<->{})'.format(sl, listener))

    def serve_forever(self):
        tcp_listener = self.config['listener']
        if isinstance(tcp_listener, list):
            tcp_listener = tuple(tcp_listener)
        self.server = gevent.server.StreamServer(tcp_listener, self.handle)
        self.log.info('Ready to accept requests')
        self.server.serve_forever()

    def stop(self):
        self.server.stop()

    def serial_to_tcp_loop(self, manager):
        self.log.info('serial to tcp task started')
        try:
            serial_to_tcp_loop(manager)
        except gevent.socket.error as msg:
            self.log.exception('error on serial to tcp:'.format(msg))
        finally:
            self.log.info('serial to tcp task terminated')

    def tcp_to_serial_loop(self, manager):
        self.log.info('tcp to serial task started')
        try:
            tcp_to_serial_loop(manager)
        except gevent.socket.error as msg:
            self.log.exception('error on tcp to serial:'.format(msg))
        finally:
            self.log.info('tcp to serial task terminated')

    def poll_statusline(self, manager):
        self.log.info('poll task started')
        try:
            poll_statusline(manager)
        finally:
            self.log.info('poll task terminated')

    def handle(self, sock, addr):
        self.log.info('connection from %r', addr)
        serial = serial_for_config(self.config)
        connection = self.Connection(sock)
        manager = PortManager(serial, connection)
        reader = gevent.spawn(self.serial_to_tcp_loop, manager)
        writer = gevent.spawn(self.tcp_to_serial_loop, manager)
        poller = gevent.spawn(self.poll_statusline, manager)
        gevent.joinall((reader, writer, poller), count=1)
        self.log.info('disconnection from %r', addr)
        reader.kill()
        writer.kill()
        poller.kill()
        serial.close()
        sock.close()


def load_config(filename):
    if not os.path.exists(filename):
        raise ValueError('configuration file does not exist')
    ext = os.path.splitext(filename)[-1]
    if ext.endswith('toml'):
        from toml import load
    elif ext.endswith('yml') or ext.endswith('.yaml'):
        import yaml
        def load(fobj):
            return yaml.load(fobj, Loader=yaml.Loader)
    elif ext.endswith('json'):
        from json import load
    elif ext.endswith('py'):
        # python only supports a single detector definition
        def load(fobj):
            r = {}
            exec(fobj.read(), None, r)
            return [r]
    else:
        raise NotImplementedError
    with open(filename)as fobj:
        return load(fobj)


def bridges(config):
    if isinstance(config, dict):
        config = [dict(item, name=key)
                  for key, item in config.items()]
    return [Bridge(item) for item in config]


def start(bridges):
    return [gevent.spawn(bridge.serve_forever) for bridge in bridges]


def stop(bridges):
    for bridge in bridges:
        bridge.stop()


def serve_forever(bridges):
    tasks = start(bridges)
    try:
        gevent.joinall(tasks)
    except KeyboardInterrupt:
        log.info('Ctrl-C pressed. Bailing out')
    stop(bridges)


def run(filename):
    logging.info('preparing to run...')
    config = load_config(filename)
    bs = bridges(config)
    serve_forever(bs)


def main(args=None):
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('-c', help='configuration file',
                        dest='config_file',
                        default='./gser2net.toml')
    parser.add_argument('--log-level', help='log level', type=str,
                        default='INFO',
                        choices=['DEBUG', 'INFO', 'WARN', 'ERROR'])

    options = parser.parse_args(args)

    log_fmt = '%(levelname)s %(asctime)-15s %(name)s: %(message)s'
    logging.basicConfig(level=options.log_level, format=log_fmt)
    run(options.config_file)


if __name__ == '__main__':
    main()
