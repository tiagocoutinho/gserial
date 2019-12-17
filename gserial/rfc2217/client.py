import struct
import logging
import functools
import telnetlib
import urllib.parse

import gevent.lock
import gevent.event
import gevent.queue
import gevent.socket

from gserial import base
from gserial.util import Timeout, Strip, iter_bytes, to_bytes
from gserial.exception import SerialException, portNotOpenError


log = logging.getLogger('gserial.rfc2217')

# telnet protocol characters
SE = telnetlib.SE    # Subnegotiation End
NOP = telnetlib.NOP   # No Operation
DM = telnetlib.DM    # Data Mark
BRK = telnetlib.BRK   # Break
IP = telnetlib.IP    # Interrupt process
AO = telnetlib.AO    # Abort output
AYT = telnetlib.AYT   # Are You There
EC = telnetlib.EC    # Erase Character
EL = telnetlib.EL    # Erase Line
GA = telnetlib.GA    # Go Ahead
SB = telnetlib.SB    # Subnegotiation Begin
WILL = telnetlib.WILL
WONT = telnetlib.WONT
DO = telnetlib.DO
DONT = telnetlib.DONT
IAC = telnetlib.IAC # Interpret As Command
IAC_DOUBLED = 2*IAC

# selected telnet options
BINARY = telnetlib.BINARY    # 8-bit data path
ECHO = telnetlib.ECHO      # echo
SGA = telnetlib.SGA # suppress go ahead

# RFC2217
COM_PORT_OPTION = b'\x2c'

# Client to Access Server
SET_BAUDRATE = b'\x01'
SET_DATASIZE = b'\x02'
SET_PARITY = b'\x03'
SET_STOPSIZE = b'\x04'
SET_CONTROL = b'\x05'
NOTIFY_LINESTATE = b'\x06'
NOTIFY_MODEMSTATE = b'\x07'
FLOWCONTROL_SUSPEND = b'\x08'
FLOWCONTROL_RESUME = b'\x09'
SET_LINESTATE_MASK = b'\x0a'
SET_MODEMSTATE_MASK = b'\x0b'
PURGE_DATA = b'\x0c'

SERVER_SET_BAUDRATE = b'\x65'
SERVER_SET_DATASIZE = b'\x66'
SERVER_SET_PARITY = b'\x67'
SERVER_SET_STOPSIZE = b'\x68'
SERVER_SET_CONTROL = b'\x69'
SERVER_NOTIFY_LINESTATE = b'\x6a'
SERVER_NOTIFY_MODEMSTATE = b'\x6b'
SERVER_FLOWCONTROL_SUSPEND = b'\x6c'
SERVER_FLOWCONTROL_RESUME = b'\x6d'
SERVER_SET_LINESTATE_MASK = b'\x6e'
SERVER_SET_MODEMSTATE_MASK = b'\x6f'
SERVER_PURGE_DATA = b'\x70'

SET_CONTROL_REQ_FLOW_SETTING = b'\x00'        # Request Com Port Flow Control Setting (outbound/both)
SET_CONTROL_USE_NO_FLOW_CONTROL = b'\x01'     # Use No Flow Control (outbound/both)
SET_CONTROL_USE_SW_FLOW_CONTROL = b'\x02'     # Use XON/XOFF Flow Control (outbound/both)
SET_CONTROL_USE_HW_FLOW_CONTROL = b'\x03'     # Use HARDWARE Flow Control (outbound/both)
SET_CONTROL_REQ_BREAK_STATE = b'\x04'         # Request BREAK State
SET_CONTROL_BREAK_ON = b'\x05'                # Set BREAK State ON
SET_CONTROL_BREAK_OFF = b'\x06'               # Set BREAK State OFF
SET_CONTROL_REQ_DTR = b'\x07'                 # Request DTR Signal State
SET_CONTROL_DTR_ON = b'\x08'                  # Set DTR Signal State ON
SET_CONTROL_DTR_OFF = b'\x09'                 # Set DTR Signal State OFF
SET_CONTROL_REQ_RTS = b'\x0a'                 # Request RTS Signal State
SET_CONTROL_RTS_ON = b'\x0b'                  # Set RTS Signal State ON
SET_CONTROL_RTS_OFF = b'\x0c'                 # Set RTS Signal State OFF
SET_CONTROL_REQ_FLOW_SETTING_IN = b'\x0d'     # Request Com Port Flow Control Setting (inbound)
SET_CONTROL_USE_NO_FLOW_CONTROL_IN = b'\x0e'  # Use No Flow Control (inbound)
SET_CONTROL_USE_SW_FLOW_CONTOL_IN = b'\x0f'   # Use XON/XOFF Flow Control (inbound)
SET_CONTROL_USE_HW_FLOW_CONTOL_IN = b'\x10'   # Use HARDWARE Flow Control (inbound)
SET_CONTROL_USE_DCD_FLOW_CONTROL = b'\x11'    # Use DCD Flow Control (outbound/both)
SET_CONTROL_USE_DTR_FLOW_CONTROL = b'\x12'    # Use DTR Flow Control (inbound)
SET_CONTROL_USE_DSR_FLOW_CONTROL = b'\x13'    # Use DSR Flow Control (outbound/both)

LINESTATE_MASK_TIMEOUT = 128        # Time-out Error
LINESTATE_MASK_SHIFTREG_EMPTY = 64  # Transfer Shift Register Empty
LINESTATE_MASK_TRANSREG_EMPTY = 32  # Transfer Holding Register Empty
LINESTATE_MASK_BREAK_DETECT = 16    # Break-detect Error
LINESTATE_MASK_FRAMING_ERROR = 8    # Framing Error
LINESTATE_MASK_PARTIY_ERROR = 4     # Parity Error
LINESTATE_MASK_OVERRUN_ERROR = 2    # Overrun Error
LINESTATE_MASK_DATA_READY = 1       # Data Ready

MODEMSTATE_MASK_CD = 128            # Receive Line Signal Detect (also known as Carrier Detect)
MODEMSTATE_MASK_RI = 64             # Ring Indicator
MODEMSTATE_MASK_DSR = 32            # Data-Set-Ready Signal State
MODEMSTATE_MASK_CTS = 16            # Clear-To-Send Signal State
MODEMSTATE_MASK_CD_CHANGE = 8       # Delta Receive Line Signal Detect
MODEMSTATE_MASK_RI_CHANGE = 4       # Trailing-edge Ring Detector
MODEMSTATE_MASK_DSR_CHANGE = 2      # Delta Data-Set-Ready
MODEMSTATE_MASK_CTS_CHANGE = 1      # Delta Clear-To-Send

PURGE_RECEIVE_BUFFER = b'\x01'      # Purge access server receive data buffer
PURGE_TRANSMIT_BUFFER = b'\x02'     # Purge access server transmit data buffer
PURGE_BOTH_BUFFERS = b'\x03'        # Purge both the access server receive data
                                    # buffer and the access server transmit data buffer

SET_CONTROL_REQ_FLOW_SETTING = b'\x00'        # Request Com Port Flow Control Setting (outbound/both)
SET_CONTROL_USE_NO_FLOW_CONTROL = b'\x01'     # Use No Flow Control (outbound/both)
SET_CONTROL_USE_SW_FLOW_CONTROL = b'\x02'     # Use XON/XOFF Flow Control (outbound/both)
SET_CONTROL_USE_HW_FLOW_CONTROL = b'\x03'     # Use HARDWARE Flow Control (outbound/both)
SET_CONTROL_REQ_BREAK_STATE = b'\x04'         # Request BREAK State
SET_CONTROL_BREAK_ON = b'\x05'                # Set BREAK State ON
SET_CONTROL_BREAK_OFF = b'\x06'               # Set BREAK State OFF
SET_CONTROL_REQ_DTR = b'\x07'                 # Request DTR Signal State
SET_CONTROL_DTR_ON = b'\x08'                  # Set DTR Signal State ON
SET_CONTROL_DTR_OFF = b'\x09'                 # Set DTR Signal State OFF
SET_CONTROL_REQ_RTS = b'\x0a'                 # Request RTS Signal State
SET_CONTROL_RTS_ON = b'\x0b'                  # Set RTS Signal State ON
SET_CONTROL_RTS_OFF = b'\x0c'                 # Set RTS Signal State OFF
SET_CONTROL_REQ_FLOW_SETTING_IN = b'\x0d'     # Request Com Port Flow Control Setting (inbound)
SET_CONTROL_USE_NO_FLOW_CONTROL_IN = b'\x0e'  # Use No Flow Control (inbound)
SET_CONTROL_USE_SW_FLOW_CONTOL_IN = b'\x0f'   # Use XON/XOFF Flow Control (inbound)
SET_CONTROL_USE_HW_FLOW_CONTOL_IN = b'\x10'   # Use HARDWARE Flow Control (inbound)
SET_CONTROL_USE_DCD_FLOW_CONTROL = b'\x11'    # Use DCD Flow Control (outbound/both)
SET_CONTROL_USE_DTR_FLOW_CONTROL = b'\x12'    # Use DTR Flow Control (inbound)
SET_CONTROL_USE_DSR_FLOW_CONTROL = b'\x13'    # Use DSR Flow Control (outbound/both)

LINESTATE_MASK_TIMEOUT = 128        # Time-out Error
LINESTATE_MASK_SHIFTREG_EMPTY = 64  # Transfer Shift Register Empty
LINESTATE_MASK_TRANSREG_EMPTY = 32  # Transfer Holding Register Empty
LINESTATE_MASK_BREAK_DETECT = 16    # Break-detect Error
LINESTATE_MASK_FRAMING_ERROR = 8    # Framing Error
LINESTATE_MASK_PARTIY_ERROR = 4     # Parity Error
LINESTATE_MASK_OVERRUN_ERROR = 2    # Overrun Error
LINESTATE_MASK_DATA_READY = 1       # Data Ready

MODEMSTATE_MASK_CD = 128            # Receive Line Signal Detect (also known as Carrier Detect)
MODEMSTATE_MASK_RI = 64             # Ring Indicator
MODEMSTATE_MASK_DSR = 32            # Data-Set-Ready Signal State
MODEMSTATE_MASK_CTS = 16            # Clear-To-Send Signal State
MODEMSTATE_MASK_CD_CHANGE = 8       # Delta Receive Line Signal Detect
MODEMSTATE_MASK_RI_CHANGE = 4       # Trailing-edge Ring Detector
MODEMSTATE_MASK_DSR_CHANGE = 2      # Delta Data-Set-Ready
MODEMSTATE_MASK_CTS_CHANGE = 1      # Delta Clear-To-Send

PURGE_RECEIVE_BUFFER = b'\x01'      # Purge access server receive data buffer
PURGE_TRANSMIT_BUFFER = b'\x02'     # Purge access server transmit data buffer
PURGE_BOTH_BUFFERS = b'\x03'        # Purge both the access server receive data
                                    # buffer and the access server transmit data buffer


RFC2217_PARITY_MAP = {
    base.PARITY_NONE: 1,
    base.PARITY_ODD: 2,
    base.PARITY_EVEN: 3,
    base.PARITY_MARK: 4,
    base.PARITY_SPACE: 5,
}
RFC2217_REVERSE_PARITY_MAP = dict((v, k) for k, v in RFC2217_PARITY_MAP.items())

RFC2217_STOPBIT_MAP = {
    base.STOPBITS_ONE: 1,
    base.STOPBITS_ONE_POINT_FIVE: 3,
    base.STOPBITS_TWO: 2,
}
RFC2217_REVERSE_STOPBIT_MAP = dict((v, k) for k, v in RFC2217_STOPBIT_MAP.items())

# Telnet filter states
M_NORMAL = 0
M_IAC_SEEN = 1
M_NEGOTIATE = 2

# TelnetOption and TelnetSubnegotiation states
REQUESTED = 'REQUESTED'
ACTIVE = 'ACTIVE'
INACTIVE = 'INACTIVE'
REALLY_INACTIVE = 'REALLY_INACTIVE'


class TelnetOption(object):
    """Manage a single telnet option, keeps track of DO/DONT WILL/WONT."""

    def __init__(self, connection, name, option, send_yes, send_no, ack_yes,
                 ack_no, initial_state, activation_callback=None, deactivation_callback=None):
        """\
        Initialize option.
        :param connection: connection used to transmit answers
        :param name: a readable name for debug outputs
        :param send_yes: what to send when option is to be enabled.
        :param send_no: what to send when option is to be disabled.
        :param ack_yes: what to expect when remote agrees on option.
        :param ack_no: what to expect when remote disagrees on option.
        :param initial_state: options initialized with REQUESTED are tried to
            be enabled on startup. use INACTIVE for all others.
        """
        self.connection = connection
        self.name = name
        self.option = option
        self.send_yes = send_yes
        self.send_no = send_no
        self.ack_yes = ack_yes
        self.ack_no = ack_no
        self.state = initial_state
        self.active = False
        self.active_event = gevent.event.Event()
        self.activation_callback = activation_callback or (lambda : None)
        self.deactivation_callback = deactivation_callback or (lambda : None)

    def __repr__(self):
        """String for debug outputs"""
        return "{o.name}:{o.active}({o.state})".format(o=self)

    def process_incoming(self, command):
        """\
        A DO/DONT/WILL/WONT was received for this option, update state and
        answer when needed.
        """
        if command == self.ack_yes:
            if self.state is REQUESTED:
                self.activate()
            elif self.state is ACTIVE:
                pass
            elif self.state is INACTIVE:
                self.activate(send=True)
            elif self.state is REALLY_INACTIVE:
                self.connection.telnet_send_option(self.send_no, self.option)
            else:
                raise ValueError('option in illegal state {!r}'.format(self))
        elif command == self.ack_no:
            if self.state is REQUESTED:
                self.deactivate()
            elif self.state is ACTIVE:
                self.deactivate(send=True)
            elif self.state is INACTIVE:
                pass
            elif self.state is REALLY_INACTIVE:
                pass
            else:
                raise ValueError('option in illegal state {!r}'.format(self))

    def activate(self, send=False):
        self.state = ACTIVE
        if send:
            self.connection.telnet_send_option(self.send_yes, self.option)
        self.active = True
        self.active_event.set()
        self.activation_callback()

    def deactivate(self, send=False):
        self.state = INACTIVE
        if send:
            self.connection.telnet_send_option(self.send_no, self.option)
        self.active = False
        self.active_event.clear()
        self.deactivation_callback()


class TelnetSubnegotiation(object):
    """\
    A object to handle subnegotiation of options. In this case actually
    sub-sub options for RFC 2217. It is used to track com port options.
    """

    def __init__(self, connection, name, option, ack_option=None):
        if ack_option is None:
            ack_option = option
        self.connection = connection
        self.name = name
        self.option = option
        self.value = None
        self.ack_option = ack_option
        self.state = INACTIVE
        self.active_event = gevent.event.Event()

    def __repr__(self):
        """String for debug outputs."""
        return "{sn.name}:{sn.state}".format(sn=self)

    def set(self, value):
        """\
        Request a change of the value. a request is sent to the server. if
        the client needs to know if the change is performed he has to check the
        state of this object.
        """
        self.value = value
        self.state = REQUESTED
        self.active_event.clear()
        self.connection.rfc2217_send_subnegotiation(self.option, self.value)
        self.connection.logger.debug("SB Requesting {} -> {!r}".format(self.name, self.value))

    def is_ready(self):
        """\
        Check if answer from server has been received. when server rejects
        the change, raise a ValueError.
        """
        if self.state == REALLY_INACTIVE:
            raise ValueError("remote rejected value for option {!r}".format(self.name))
        return self.state == ACTIVE
    # add property to have a similar interface as TelnetOption
    active = property(is_ready)

    def wait(self, timeout=1):
        """\
        Wait until the subnegotiation has been acknowledged or timeout. It
        can also throw a value error when the answer from the server does not
        match the value sent.
        """
        with gevent.Timeout(timeout, SerialException("timeout while waiting for option {!r}".format(self.name))):
            self.active_event.wait()

    def check_answer(self, suboption):
        """\
        Check an incoming subnegotiation block. The parameter already has
        cut off the header like sub option number and com port option value.
        """
        if self.value == suboption[:len(self.value)]:
            self.state = ACTIVE
            self.active_event.set()
        else:
            # error propagation done in is_ready
            self.state = REALLY_INACTIVE
            self.active_event.clear()
        self.connection.logger.debug("SB Answer {} -> {!r} -> {}".format(self.name, suboption, self.state))


def ensure_open(f):
    @functools.wraps(f)
    def wrapper(self, *args, **kwargs):
        if not self.is_open:
            raise portNotOpenError
        return f(self, *args, **kwargs)
    return wrapper


class Serial(base.SerialBase):

    BAUDRATES = (50, 75, 110, 134, 150, 200, 300, 600, 1200, 1800, 2400, 4800,
                 9600, 19200, 38400, 57600, 115200)

    def __init__(self, *args, **kwargs):
        self._socket = None
        self._linestate = 0
        self._modemstate = None
        self._modemstate_timeout = Timeout(-1)
        self._remote_suspend_flow = False
        self._write_lock = None
        self.logger = log
        self._ignore_set_control_answer = False
        self._poll_modem_state = False
        self._network_timeout = 1
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
        self._ignore_set_control_answer = False
        self._poll_modem_state = False
        self._network_timeout = 1
        if self._port is None:
            raise SerialException("Port must be configured before it can be used.")
        if self.is_open:
            raise SerialException("Port is already open.")
        addr = self.from_url(self.portstr)
        self.logger = logging.getLogger('RFC2217({}:{})'.format(*addr))
        try:
            self._socket = gevent.socket.create_connection(addr, timeout=2)
            self._socket.setsockopt(gevent.socket.IPPROTO_TCP,
                                    gevent.socket.TCP_NODELAY, 1)
        except Exception as msg:
            self._socket = None
            raise SerialException("Could not open port {}: {}".format(self.portstr, msg))

        # use a thread save queue as buffer. it also simplifies implementing
        # the read timeout
        self._read_buffer = gevent.queue.Queue()
        # to ensure that user writes does not interfere with internal
        # telnet/rfc2217 options establish a lock
        self._write_lock = gevent.lock.RLock()
        # name the following separately so that, below, a check can be easily done
        all_mandatory = gevent.event.Event()
        def event_callback():
            if sum(o.active for o in mandadory_options) == sum(o.state != INACTIVE for o in mandadory_options):
                all_mandatory.set()
        mandadory_options = [
            TelnetOption(self, 'we-BINARY', BINARY, WILL, WONT, DO, DONT, INACTIVE, event_callback, event_callback),
            TelnetOption(self, 'we-RFC2217', COM_PORT_OPTION, WILL, WONT, DO, DONT, REQUESTED, event_callback, event_callback),
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
        self._thread = gevent.spawn(self._telnet_read_loop)

        try:    # must clean-up if open fails
            # negotiate Telnet/RFC 2217 -> send initial requests
            for option in self._telnet_options:
                if option.state is REQUESTED:
                    self.telnet_send_option(option.send_yes, option.option)

            # now wait until important options are negotiated
            timeout_error = SerialException(
                "Remote does not seem to support RFC2217 or BINARY mode {!r}".format(mandadory_options))
            with gevent.Timeout(self._network_timeout, timeout_error):
                all_mandatory.wait()
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
        self.logger.debug("Negotiating settings: {}".format(items))
        timeout_error = SerialException(
            "Remote does not accept parameter change (RFC2217): {!r}".format(items))
        with gevent.Timeout(self._network_timeout, timeout_error):
            gevent.wait([o.active_event for o in items])
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
            gevent.sleep(0.3)
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
    @ensure_open
    def in_waiting(self):
        """Return the number of bytes currently in the input buffer."""
        return self._read_buffer.qsize()

    @ensure_open
    def read(self, size=1):
        """\
        Read size bytes from the serial port. If a timeout is set it may
        return less characters as requested. With no timeout it will block
        until the requested number of bytes is read.
        """
        data = bytearray()
        timeout = gevent.Timeout(self._timeout)
        try:
            while len(data) < size:
                if self._thread is None or self._thread.ready():
                    raise SerialException('connection failed (reader thread died)')
                buf = self._read_buffer.get()
                if buf is None:
                    break
                data += buf
        except gevent.Timeout:
            pass
        except gevent.queue.Empty:
            pass
        return bytes(data)

    @ensure_open
    def write(self, data):
        """\
        Output the given byte string over the serial port. Can block if the
        connection is blocked. May raise SerialException if the connection is
        closed.
        """
        try:
            self._internal_raw_write(to_bytes(data).replace(IAC, IAC_DOUBLED))
        except gevent.socket.error as e:
            raise SerialException("connection failed (socket error): {}".format(e))
        return len(data)

    @ensure_open
    def reset_input_buffer(self):
        """Clear input buffer, discarding all that is in the buffer."""
        self.rfc2217_send_purge(PURGE_RECEIVE_BUFFER)
        # empty read buffer
        while self._read_buffer.qsize():
            self._read_buffer.get(False)

    @ensure_open
    def reset_output_buffer(self):
        """\
        Clear output buffer, aborting the current output and
        discarding all that is in the buffer.
        """
        self.rfc2217_send_purge(PURGE_TRANSMIT_BUFFER)

    @ensure_open
    def _update_break_state(self):
        """\
        Set break: Controls TXD. When active, to transmitting is
        possible.
        """
        self.logger.info('set BREAK to {}'.format('active' if self._break_state else 'inactive'))
        if self._break_state:
            self.rfc2217_set_control(SET_CONTROL_BREAK_ON)
        else:
            self.rfc2217_set_control(SET_CONTROL_BREAK_OFF)

    @ensure_open
    def _update_rts_state(self):
        """Set terminal status line: Request To Send."""
        self.logger.info('set RTS to {}'.format('active' if self._rts_state else 'inactive'))
        if self._rts_state:
            self.rfc2217_set_control(SET_CONTROL_RTS_ON)
        else:
            self.rfc2217_set_control(SET_CONTROL_RTS_OFF)

    @ensure_open
    def _update_dtr_state(self):
        """Set terminal status line: Data Terminal Ready."""
        self.logger.info('set DTR to {}'.format('active' if self._dtr_state else 'inactive'))
        if self._dtr_state:
            self.rfc2217_set_control(SET_CONTROL_DTR_ON)
        else:
            self.rfc2217_set_control(SET_CONTROL_DTR_OFF)

    @property
    @ensure_open
    def cts(self):
        """Read terminal status line: Clear To Send."""
        return bool(self.get_modem_state() & MODEMSTATE_MASK_CTS)

    @property
    @ensure_open
    def dsr(self):
        """Read terminal status line: Data Set Ready."""
        return bool(self.get_modem_state() & MODEMSTATE_MASK_DSR)

    @property
    @ensure_open
    def ri(self):
        """Read terminal status line: Ring Indicator."""
        return bool(self.get_modem_state() & MODEMSTATE_MASK_RI)

    @property
    @ensure_open
    def cd(self):
        """Read terminal status line: Carrier Detect."""
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
                except gevent.socket.timeout:
                    # just need to get out of recv form time to time to check if
                    # still alive
                    continue
                except gevent.socket.error as e:
                    # connection fails -> terminate loop
                    self.logger.debug("socket error in reader thread: {}".format(e))
                    self._read_buffer.put(None)
                    break
                self.logger.debug('RECV %r', Strip(data))
                if not data:
                    self._read_buffer.put(None)
                    break  # lost connection
                for byte in iter_bytes(data):
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
            self.logger.debug("read thread terminated")

    # - incoming telnet commands and options

    def _telnet_process_command(self, command):
        """Process commands other than DO, DONT, WILL, WONT."""
        # Currently none. RFC2217 only uses negotiation and subnegotiation.
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
                self.logger.warning("rejected Telnet option: {!r}".format(option))

    def _telnet_process_subnegotiation(self, suboption):
        """Process subnegotiation, the data between IAC SB and IAC SE."""
        if suboption[0:1] == COM_PORT_OPTION:
            option = suboption[1:2]
            if option == SERVER_NOTIFY_LINESTATE and len(suboption) >= 3:
                self._linestate = ord(suboption[2:3])  # ensure it is a number
                self.logger.info("NOTIFY_LINESTATE: {}".format(self._linestate))
            elif option == SERVER_NOTIFY_MODEMSTATE and len(suboption) >= 3:
                self._modemstate = ord(suboption[2:3])  # ensure it is a number
                self.logger.info("NOTIFY_MODEMSTATE: {}".format(self._modemstate))
                # update time when we think that a poll would make sense
                self._modemstate_timeout.restart(0.3)
            elif option == FLOWCONTROL_SUSPEND:
                self._remote_suspend_flow = True
            elif option == FLOWCONTROL_RESUME:
                self._remote_suspend_flow = False
            else:
                for item in self._rfc2217_options.values():
                    if item.ack_option == option:
                        #~ print "processing COM_PORT_OPTION: %r" % list(suboption[1:])
                        item.check_answer(bytes(suboption[2:]))
                        break
                else:
                    self.logger.warning("ignoring COM_PORT_OPTION: {!r}".format(suboption))
        else:
            self.logger.warning("ignoring subnegotiation: {!r}".format(suboption))

    # - outgoing telnet commands and options

    def _internal_raw_write(self, data):
        """internal socket write with no data escaping. used to send telnet stuff."""
        with self._write_lock:
            self.logger.debug('SEND %r', Strip(data))
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
            gevent.sleep(0.1)  # this helps getting the unit tests passed
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
            self.logger.debug('polling modem state')
            # when it is older, request an update
            self.rfc2217_send_subnegotiation(NOTIFY_MODEMSTATE)
            timeout = Timeout(self._network_timeout)
            while not timeout.expired():
                gevent.sleep(0.05)    # prevent 100% CPU load
                # when expiration time is updated, it means that there is a new
                # value
                if not self._modemstate_timeout.expired():
                    break
            else:
                self.logger.warning('poll for modem state failed')
            # even when there is a timeout, do not generate an error just
            # return the last known value. this way we can support buggy
            # servers that do not respond to polls, but send automatic
            # updates.
        if self._modemstate is not None:
            self.logger.debug('using cached modem state')
            return self._modemstate
        else:
            # never received a notification from the server
            raise SerialException("remote sends no NOTIFY_MODEMSTATE")


if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG,
        format='%(threadName)-12s %(levelname)s %(asctime)-15s %(name)s: %(message)s')
    s = Serial('rfc2217://localhost:2217')
    message = b'bla ble bli\n'
    s.write(message)
    assert s.readline() == message
    message = 2048*b'Hello! ' + b'\n'
    s.write(message)
    assert s.readline() == message
