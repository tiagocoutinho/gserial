
import socket
import struct
import logging
import urllib.parse

import gevent.queue

from serial import SerialBase, SerialException, to_bytes, \
    iterbytes, portNotOpenError, Timeout, rfc2217


class Serial(SerialBase):
    __doc__ = rfc2217.Serial.__doc__

    BAUDRATES = rfc2217.Serial.BAUDRATES

    def __init__(self, *args, **kwargs):
        self._socket = None
        self._linestate = 0
        self._modemstate = None
        self._modemstate_timeout = Timeout(-1)
        self._remote_suspend_flow = False
        self._write_lock = None
        self.logger = None
        self._ignore_set_control_answer = False
        self._poll_modem_state = False
        self._network_timeout = 3
        self._telnet_options = None
        self._rfc2217_port_settings = None
        self._rfc2217_options = None
        self._read_buffer = None
        super(Serial, self).__init__(*args, **kwargs)

    def open(self):
        """\
        Open port with current settings. This may throw a SerialException
        if the port cannot be opened.
        """
        self.logger = None
        self._ignore_set_control_answer = False
        self._poll_modem_state = False
        self._network_timeout = 3
        if self._port is None:
            raise SerialException("Port must be configured before it can be used.")
        if self.is_open:
            raise SerialException("Port is already open.")
        try:
            self._socket = socket.create_connection(self.from_url(self.portstr), timeout=5)  # XXX good value?
            self._socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except Exception as msg:
            self._socket = None
            raise SerialException("Could not open port {}: {}".format(self.portstr, msg))

        # use a thread save queue as buffer. it also simplifies implementing
        # the read timeout
        self._read_buffer = Queue.Queue()
        # to ensure that user writes does not interfere with internal
        # telnet/rfc2217 options establish a lock
        self._write_lock = threading.Lock()
        # name the following separately so that, below, a check can be easily done
        mandadory_options = [
            TelnetOption(self, 'we-BINARY', BINARY, WILL, WONT, DO, DONT, INACTIVE),
            TelnetOption(self, 'we-RFC2217', COM_PORT_OPTION, WILL, WONT, DO, DONT, REQUESTED),
        ]
        # all supported telnet options
        self._telnet_options = [
            TelnetOption(self, 'ECHO', ECHO, DO, DONT, WILL, WONT, REQUESTED),
            TelnetOption(self, 'we-SGA', SGA, WILL, WONT, DO, DONT, REQUESTED),
            TelnetOption(self, 'they-SGA', SGA, DO, DONT, WILL, WONT, REQUESTED),
            TelnetOption(self, 'they-BINARY', BINARY, DO, DONT, WILL, WONT, INACTIVE),
            TelnetOption(self, 'they-RFC2217', COM_PORT_OPTION, DO, DONT, WILL, WONT, REQUESTED),
        ] + mandadory_options
        # RFC 2217 specific states
        # COM port settings
        self._rfc2217_port_settings = {
            'baudrate': TelnetSubnegotiation(self, 'baudrate', SET_BAUDRATE, SERVER_SET_BAUDRATE),
            'datasize': TelnetSubnegotiation(self, 'datasize', SET_DATASIZE, SERVER_SET_DATASIZE),
            'parity':   TelnetSubnegotiation(self, 'parity',   SET_PARITY,   SERVER_SET_PARITY),
            'stopsize': TelnetSubnegotiation(self, 'stopsize', SET_STOPSIZE, SERVER_SET_STOPSIZE),
        }
        # There are more subnegotiation objects, combine all in one dictionary
        # for easy access
        self._rfc2217_options = {
            'purge':    TelnetSubnegotiation(self, 'purge',    PURGE_DATA,   SERVER_PURGE_DATA),
            'control':  TelnetSubnegotiation(self, 'control',  SET_CONTROL,  SERVER_SET_CONTROL),
        }
        self._rfc2217_options.update(self._rfc2217_port_settings)
        # cache for line and modem states that the server sends to us
        self._linestate = 0
        self._modemstate = None
        self._modemstate_timeout = Timeout(-1)
        # RFC 2217 flow control between server and client
        self._remote_suspend_flow = False

        self.is_open = True
        self._thread = threading.Thread(target=self._telnet_read_loop)
        self._thread.setDaemon(True)
        self._thread.setName('pySerial RFC 2217 reader thread for {}'.format(self._port))
        self._thread.start()

        try:    # must clean-up if open fails
            # negotiate Telnet/RFC 2217 -> send initial requests
            for option in self._telnet_options:
                if option.state is REQUESTED:
                    self.telnet_send_option(option.send_yes, option.option)
            # now wait until important options are negotiated
            timeout = Timeout(self._network_timeout)
            while not timeout.expired():
                time.sleep(0.05)    # prevent 100% CPU load
                if sum(o.active for o in mandadory_options) == sum(o.state != INACTIVE for o in mandadory_options):
                    break
            else:
                raise SerialException(
                    "Remote does not seem to support RFC2217 or BINARY mode {!r}".format(mandadory_options))
            if self.logger:
                self.logger.info("Negotiated options: {}".format(self._telnet_options))

            # fine, go on, set RFC 2271 specific things
            self._reconfigure_port()
            # all things set up get, now a clean start
            if not self._dsrdtr:
                self._update_dtr_state()
            if not self._rtscts:
                self._update_rts_state()
            self.reset_input_buffer()
            self.reset_output_buffer()
        except:
            self.close()
            raise

    def _reconfigure_port(self):
        """Set communication parameters on opened port."""
        if self._socket is None:
            raise SerialException("Can only operate on open ports")

        # if self._timeout != 0 and self._interCharTimeout is not None:
            # XXX

        if self._write_timeout is not None:
            raise NotImplementedError('write_timeout is currently not supported')
            # XXX

        # Setup the connection
        # to get good performance, all parameter changes are sent first...
        if not 0 < self._baudrate < 2 ** 32:
            raise ValueError("invalid baudrate: {!r}".format(self._baudrate))
        self._rfc2217_port_settings['baudrate'].set(struct.pack(b'!I', self._baudrate))
        self._rfc2217_port_settings['datasize'].set(struct.pack(b'!B', self._bytesize))
        self._rfc2217_port_settings['parity'].set(struct.pack(b'!B', RFC2217_PARITY_MAP[self._parity]))
        self._rfc2217_port_settings['stopsize'].set(struct.pack(b'!B', RFC2217_STOPBIT_MAP[self._stopbits]))

        # and now wait until parameters are active
        items = self._rfc2217_port_settings.values()
        if self.logger:
            self.logger.debug("Negotiating settings: {}".format(items))
        timeout = Timeout(self._network_timeout)
        while not timeout.expired():
            time.sleep(0.05)    # prevent 100% CPU load
            if sum(o.active for o in items) == len(items):
                break
        else:
            raise SerialException("Remote does not accept parameter change (RFC2217): {!r}".format(items))
        if self.logger:
            self.logger.info("Negotiated settings: {}".format(items))

        if self._rtscts and self._xonxoff:
            raise ValueError('xonxoff and rtscts together are not supported')
        elif self._rtscts:
            self.rfc2217_set_control(SET_CONTROL_USE_HW_FLOW_CONTROL)
        elif self._xonxoff:
            self.rfc2217_set_control(SET_CONTROL_USE_SW_FLOW_CONTROL)
        else:
            self.rfc2217_set_control(SET_CONTROL_USE_NO_FLOW_CONTROL)

    def close(self):
        """Close port"""
        self.is_open = False
        if self._socket:
            try:
                self._socket.shutdown(socket.SHUT_RDWR)
                self._socket.close()
            except:
                # ignore errors.
                pass
        if self._thread:
            self._thread.join(7)  # XXX more than socket timeout
            self._thread = None
            # in case of quick reconnects, give the server some time
            time.sleep(0.3)
        self._socket = None

    def from_url(self, url):
        """\
        extract host and port from an URL string, other settings are extracted
        an stored in instance
        """
        parts = urllib.parse.urlsplit(url)
        if parts.scheme != "rfc2217":
            raise SerialException(
                'expected a string in the form '
                '"rfc2217://<host>:<port>[?option[&option...]]": '
                'not starting with rfc2217:// ({!r})'.format(parts.scheme))
        try:
            # process options now, directly altering self
            for option, values in urllib.parse.parse_qs(parts.query, True).items():
                if option == 'logging':
                    logging.basicConfig()   # XXX is that good to call it here?
                    self.logger = logging.getLogger('pySerial.rfc2217')
                    self.logger.setLevel(LOGGER_LEVELS[values[0]])
                    self.logger.debug('enabled logging')
                elif option == 'ign_set_control':
                    self._ignore_set_control_answer = True
                elif option == 'poll_modem':
                    self._poll_modem_state = True
                elif option == 'timeout':
                    self._network_timeout = float(values[0])
                else:
                    raise ValueError('unknown option: {!r}'.format(option))
            if not 0 <= parts.port < 65536:
                raise ValueError("port not in range 0...65535")
        except ValueError as e:
            raise SerialException(
                'expected a string in the form '
                '"rfc2217://<host>:<port>[?option[&option...]]": {}'.format(e))
        return (parts.hostname, parts.port)

    #  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -  -

    @property
    def in_waiting(self):
        """Return the number of bytes currently in the input buffer."""
        if not self.is_open:
            raise portNotOpenError
        return self._read_buffer.qsize()

    def read(self, size=1):
        """\
        Read size bytes from the serial port. If a timeout is set it may
        return less characters as requested. With no timeout it will block
        until the requested number of bytes is read.
        """
        if not self.is_open:
            raise portNotOpenError
        data = bytearray()
        try:
            timeout = Timeout(self._timeout)
            while len(data) < size:
                if self._thread is None:
                    raise SerialException('connection failed (reader thread died)')
                buf = self._read_buffer.get(True, timeout.time_left())
                if buf is None:
                    return bytes(data)
                data += buf
                if timeout.expired():
                    break
        except Queue.Empty:  # -> timeout
            pass
        return bytes(data)

    def write(self, data):
        """\
        Output the given byte string over the serial port. Can block if the
        connection is blocked. May raise SerialException if the connection is
        closed.
        """
        if not self.is_open:
            raise portNotOpenError
        with self._write_lock:
            try:
                self._socket.sendall(to_bytes(data).replace(IAC, IAC_DOUBLED))
            except socket.error as e:
                raise SerialException("connection failed (socket error): {}".format(e))
        return len(data)

    def reset_input_buffer(self):
        """Clear input buffer, discarding all that is in the buffer."""
        if not self.is_open:
            raise portNotOpenError
        self.rfc2217_send_purge(PURGE_RECEIVE_BUFFER)
        # empty read buffer
        while self._read_buffer.qsize():
            self._read_buffer.get(False)

    def reset_output_buffer(self):
        """\
        Clear output buffer, aborting the current output and
        discarding all that is in the buffer.
        """
        if not self.is_open:
            raise portNotOpenError
        self.rfc2217_send_purge(PURGE_TRANSMIT_BUFFER)

    def _update_break_state(self):
        """\
        Set break: Controls TXD. When active, to transmitting is
        possible.
        """
        if not self.is_open:
            raise portNotOpenError
        if self.logger:
            self.logger.info('set BREAK to {}'.format('active' if self._break_state else 'inactive'))
        if self._break_state:
            self.rfc2217_set_control(SET_CONTROL_BREAK_ON)
        else:
            self.rfc2217_set_control(SET_CONTROL_BREAK_OFF)

    def _update_rts_state(self):
        """Set terminal status line: Request To Send."""
        if not self.is_open:
            raise portNotOpenError
        if self.logger:
            self.logger.info('set RTS to {}'.format('active' if self._rts_state else 'inactive'))
        if self._rts_state:
            self.rfc2217_set_control(SET_CONTROL_RTS_ON)
        else:
            self.rfc2217_set_control(SET_CONTROL_RTS_OFF)

    def _update_dtr_state(self):
        """Set terminal status line: Data Terminal Ready."""
        if not self.is_open:
            raise portNotOpenError
        if self.logger:
            self.logger.info('set DTR to {}'.format('active' if self._dtr_state else 'inactive'))
        if self._dtr_state:
            self.rfc2217_set_control(SET_CONTROL_DTR_ON)
        else:
            self.rfc2217_set_control(SET_CONTROL_DTR_OFF)

    @property
    def cts(self):
        """Read terminal status line: Clear To Send."""
        if not self.is_open:
            raise portNotOpenError
        return bool(self.get_modem_state() & MODEMSTATE_MASK_CTS)

    @property
    def dsr(self):
        """Read terminal status line: Data Set Ready."""
        if not self.is_open:
            raise portNotOpenError
        return bool(self.get_modem_state() & MODEMSTATE_MASK_DSR)

    @property
    def ri(self):
        """Read terminal status line: Ring Indicator."""
        if not self.is_open:
            raise portNotOpenError
        return bool(self.get_modem_state() & MODEMSTATE_MASK_RI)

    @property
    def cd(self):
        """Read terminal status line: Carrier Detect."""
        if not self.is_open:
            raise portNotOpenError
        return bool(self.get_modem_state() & MODEMSTATE_MASK_CD)

    # - - - platform specific - - -
    # None so far

    # - - - RFC2217 specific - - -

    def _telnet_read_loop(self):
        """Read loop for the socket."""
        mode = M_NORMAL
        suboption = None
        try:
            while self.is_open:
                try:
                    data = self._socket.recv(1024)
                except socket.timeout:
                    # just need to get out of recv form time to time to check if
                    # still alive
                    continue
                except socket.error as e:
                    # connection fails -> terminate loop
                    if self.logger:
                        self.logger.debug("socket error in reader thread: {}".format(e))
                    self._read_buffer.put(None)
                    break
                if not data:
                    self._read_buffer.put(None)
                    break  # lost connection
                for byte in iterbytes(data):
                    if mode == M_NORMAL:
                        # interpret as command or as data
                        if byte == IAC:
                            mode = M_IAC_SEEN
                        else:
                            # store data in read buffer or sub option buffer
                            # depending on state
                            if suboption is not None:
                                suboption += byte
                            else:
                                self._read_buffer.put(byte)
                    elif mode == M_IAC_SEEN:
                        if byte == IAC:
                            # interpret as command doubled -> insert character
                            # itself
                            if suboption is not None:
                                suboption += IAC
                            else:
                                self._read_buffer.put(IAC)
                            mode = M_NORMAL
                        elif byte == SB:
                            # sub option start
                            suboption = bytearray()
                            mode = M_NORMAL
                        elif byte == SE:
                            # sub option end -> process it now
                            self._telnet_process_subnegotiation(bytes(suboption))
                            suboption = None
                            mode = M_NORMAL
                        elif byte in (DO, DONT, WILL, WONT):
                            # negotiation
                            telnet_command = byte
                            mode = M_NEGOTIATE
                        else:
                            # other telnet commands
                            self._telnet_process_command(byte)
                            mode = M_NORMAL
                    elif mode == M_NEGOTIATE:  # DO, DONT, WILL, WONT was received, option now following
                        self._telnet_negotiate_option(telnet_command, byte)
                        mode = M_NORMAL
        finally:
            self._thread = None
            if self.logger:
                self.logger.debug("read thread terminated")

    # - incoming telnet commands and options

    def _telnet_process_command(self, command):
        """Process commands other than DO, DONT, WILL, WONT."""
        # Currently none. RFC2217 only uses negotiation and subnegotiation.
        if self.logger:
            self.logger.warning("ignoring Telnet command: {!r}".format(command))

    def _telnet_negotiate_option(self, command, option):
        """Process incoming DO, DONT, WILL, WONT."""
        # check our registered telnet options and forward command to them
        # they know themselves if they have to answer or not
        known = False
        for item in self._telnet_options:
            # can have more than one match! as some options are duplicated for
            # 'us' and 'them'
            if item.option == option:
                item.process_incoming(command)
                known = True
        if not known:
            # handle unknown options
            # only answer to positive requests and deny them
            if command == WILL or command == DO:
                self.telnet_send_option((DONT if command == WILL else WONT), option)
                if self.logger:
                    self.logger.warning("rejected Telnet option: {!r}".format(option))

    def _telnet_process_subnegotiation(self, suboption):
        """Process subnegotiation, the data between IAC SB and IAC SE."""
        if suboption[0:1] == COM_PORT_OPTION:
            if suboption[1:2] == SERVER_NOTIFY_LINESTATE and len(suboption) >= 3:
                self._linestate = ord(suboption[2:3])  # ensure it is a number
                if self.logger:
                    self.logger.info("NOTIFY_LINESTATE: {}".format(self._linestate))
            elif suboption[1:2] == SERVER_NOTIFY_MODEMSTATE and len(suboption) >= 3:
                self._modemstate = ord(suboption[2:3])  # ensure it is a number
                if self.logger:
                    self.logger.info("NOTIFY_MODEMSTATE: {}".format(self._modemstate))
                # update time when we think that a poll would make sense
                self._modemstate_timeout.restart(0.3)
            elif suboption[1:2] == FLOWCONTROL_SUSPEND:
                self._remote_suspend_flow = True
            elif suboption[1:2] == FLOWCONTROL_RESUME:
                self._remote_suspend_flow = False
            else:
                for item in self._rfc2217_options.values():
                    if item.ack_option == suboption[1:2]:
                        #~ print "processing COM_PORT_OPTION: %r" % list(suboption[1:])
                        item.check_answer(bytes(suboption[2:]))
                        break
                else:
                    if self.logger:
                        self.logger.warning("ignoring COM_PORT_OPTION: {!r}".format(suboption))
        else:
            if self.logger:
                self.logger.warning("ignoring subnegotiation: {!r}".format(suboption))

    # - outgoing telnet commands and options

    def _internal_raw_write(self, data):
        """internal socket write with no data escaping. used to send telnet stuff."""
        with self._write_lock:
            self._socket.sendall(data)

    def telnet_send_option(self, action, option):
        """Send DO, DONT, WILL, WONT."""
        self._internal_raw_write(IAC + action + option)

    def rfc2217_send_subnegotiation(self, option, value=b''):
        """Subnegotiation of RFC2217 parameters."""
        value = value.replace(IAC, IAC_DOUBLED)
        self._internal_raw_write(IAC + SB + COM_PORT_OPTION + option + value + IAC + SE)

    def rfc2217_send_purge(self, value):
        """\
        Send purge request to the remote.
        (PURGE_RECEIVE_BUFFER / PURGE_TRANSMIT_BUFFER / PURGE_BOTH_BUFFERS)
        """
        item = self._rfc2217_options['purge']
        item.set(value)  # transmit desired purge type
        item.wait(self._network_timeout)  # wait for acknowledge from the server

    def rfc2217_set_control(self, value):
        """transmit change of control line to remote"""
        item = self._rfc2217_options['control']
        item.set(value)  # transmit desired control type
        if self._ignore_set_control_answer:
            # answers are ignored when option is set. compatibility mode for
            # servers that answer, but not the expected one... (or no answer
            # at all) i.e. sredird
            time.sleep(0.1)  # this helps getting the unit tests passed
        else:
            item.wait(self._network_timeout)  # wait for acknowledge from the server

    def rfc2217_flow_server_ready(self):
        """\
        check if server is ready to receive data. block for some time when
        not.
        """
        #~ if self._remote_suspend_flow:
        #~     wait---

    def get_modem_state(self):
        """\
        get last modem state (cached value. If value is "old", request a new
        one. This cache helps that we don't issue to many requests when e.g. all
        status lines, one after the other is queried by the user (CTS, DSR
        etc.)
        """
        # active modem state polling enabled? is the value fresh enough?
        if self._poll_modem_state and self._modemstate_timeout.expired():
            if self.logger:
                self.logger.debug('polling modem state')
            # when it is older, request an update
            self.rfc2217_send_subnegotiation(NOTIFY_MODEMSTATE)
            timeout = Timeout(self._network_timeout)
            while not timeout.expired():
                time.sleep(0.05)    # prevent 100% CPU load
                # when expiration time is updated, it means that there is a new
                # value
                if not self._modemstate_timeout.expired():
                    break
            else:
                if self.logger:
                    self.logger.warning('poll for modem state failed')
            # even when there is a timeout, do not generate an error just
            # return the last known value. this way we can support buggy
            # servers that do not respond to polls, but send automatic
            # updates.
        if self._modemstate is not None:
            if self.logger:
                self.logger.debug('using cached modem state')
            return self._modemstate
        else:
            # never received a notification from the server
            raise SerialException("remote sends no NOTIFY_MODEMSTATE")

