import logging

from .client import *


def escape(data):
    return data.replace(IAC, IAC_DOUBLED)


class PortManager(object):
    """\
    This class manages the state of Telnet and RFC 2217. It needs a serial
    instance and a connection to work with. Connection is expected to implement
    a write function, that writes the string to the network.
    """

    def __init__(self, serial_port, connection):
        self.serial = serial_port
        self.connection = connection
        parent_logger = getattr(serial_port, 'logger', logging.root)
        self.logger = parent_logger.getChild('PortManager')
        self._client_is_rfc2217 = False

        # filter state machine
        self.mode = M_NORMAL
        self.suboption = None
        self.telnet_command = None

        # states for modem/line control events
        self.modemstate_mask = 255
        self.last_modemstate = None
        self.linstate_mask = 0

        # all supported telnet options
        self._telnet_options = [
            TelnetOption(self, 'ECHO', ECHO, WILL, WONT, DO, DONT, REQUESTED),
            TelnetOption(self, 'we-SGA', SGA, WILL, WONT, DO, DONT, REQUESTED),
            TelnetOption(self, 'they-SGA', SGA, DO, DONT, WILL, WONT, INACTIVE),
            TelnetOption(self, 'we-BINARY', BINARY, WILL, WONT, DO, DONT, INACTIVE),
            TelnetOption(self, 'they-BINARY', BINARY, DO, DONT, WILL, WONT, REQUESTED),
            TelnetOption(self, 'we-RFC2217', COM_PORT_OPTION, WILL, WONT, DO, DONT, REQUESTED, self._client_ok),
            TelnetOption(self, 'they-RFC2217', COM_PORT_OPTION, DO, DONT, WILL, WONT, INACTIVE, self._client_ok),
        ]

        # negotiate Telnet/RFC2217 -> send initial requests
        self.logger.debug("requesting initial Telnet/RFC 2217 options")
        for option in self._telnet_options:
            if option.state is REQUESTED:
                self.telnet_send_option(option.send_yes, option.option)
        # issue 1st modem state notification

    def _client_ok(self):
        """\
        callback of telnet option. It gets called when option is activated.
        This one here is used to detect when the client agrees on RFC 2217. A
        flag is set so that other functions like check_modem_lines know if the
        client is OK.
        """
        # The callback is used for we and they so if one party agrees, we're
        # already happy. it seems not all servers do the negotiation correctly
        # and i guess there are incorrect clients too.. so be happy if client
        # answers one or the other positively.
        self._client_is_rfc2217 = True
        self.logger.info("client accepts RFC 2217")
        # this is to ensure that the client gets a notification, even if there
        # was no change
        self.check_modem_lines(force_notification=True)

    # - outgoing telnet commands and options

    def telnet_send_option(self, action, option):
        """Send DO, DONT, WILL, WONT."""
        self.connection.write(IAC + action + option)

    def rfc2217_send_subnegotiation(self, option, value=b''):
        """Subnegotiation of RFC 2217 parameters."""
        value = value.replace(IAC, IAC_DOUBLED)
        self.connection.write(IAC + SB + COM_PORT_OPTION + option + value + IAC + SE)

    # - check modem lines, needs to be called periodically from user to
    # establish polling

    def check_modem_lines(self, force_notification=False):
        """\
        read control lines from serial port and compare the last value sent to remote.
        send updates on changes.
        """
        modemstate = (
            (self.serial.cts and MODEMSTATE_MASK_CTS) |
            (self.serial.dsr and MODEMSTATE_MASK_DSR) |
            (self.serial.ri and MODEMSTATE_MASK_RI) |
            (self.serial.cd and MODEMSTATE_MASK_CD))
        # check what has changed
        deltas = modemstate ^ (self.last_modemstate or 0)  # when last is None -> 0
        if deltas & MODEMSTATE_MASK_CTS:
            modemstate |= MODEMSTATE_MASK_CTS_CHANGE
        if deltas & MODEMSTATE_MASK_DSR:
            modemstate |= MODEMSTATE_MASK_DSR_CHANGE
        if deltas & MODEMSTATE_MASK_RI:
            modemstate |= MODEMSTATE_MASK_RI_CHANGE
        if deltas & MODEMSTATE_MASK_CD:
            modemstate |= MODEMSTATE_MASK_CD_CHANGE
        # if new state is different and the mask allows this change, send
        # notification. suppress notifications when client is not rfc2217
        if modemstate != self.last_modemstate or force_notification:
            if (self._client_is_rfc2217 and (modemstate & self.modemstate_mask)) or force_notification:
                self.rfc2217_send_subnegotiation(
                    SERVER_NOTIFY_MODEMSTATE,
                    to_bytes([modemstate & self.modemstate_mask]))
                self.logger.info("NOTIFY_MODEMSTATE: {}".format(modemstate))
            # save last state, but forget about deltas.
            # otherwise it would also notify about changing deltas which is
            # probably not very useful
            self.last_modemstate = modemstate & 0xf0

    # - outgoing data escaping

    def escape(self, data):
        return escape(data)

    # - incoming data filter

    def filter(self, data):
        """\
        Handle a bunch of incoming bytes. This is a generator. It will yield
        all characters not of interest for Telnet/RFC 2217.

        The idea is that the reader thread pushes data from the socket through
        this filter:

        for byte in filter(socket.recv(1024)):
            # do things like CR/LF conversion/whatever
            # and write data to the serial port
            serial.write(byte)

        (socket error handling code left as exercise for the reader)
        """
        for byte in iter_bytes(data):
            if self.mode == M_NORMAL:
                # interpret as command or as data
                if byte == IAC:
                    self.mode = M_IAC_SEEN
                else:
                    # store data in sub option buffer or pass it to our
                    # consumer depending on state
                    if self.suboption is not None:
                        self.suboption += byte
                    else:
                        yield byte
            elif self.mode == M_IAC_SEEN:
                if byte == IAC:
                    # interpret as command doubled -> insert character
                    # itself
                    if self.suboption is not None:
                        self.suboption += byte
                    else:
                        yield byte
                    self.mode = M_NORMAL
                elif byte == SB:
                    # sub option start
                    self.suboption = bytearray()
                    self.mode = M_NORMAL
                elif byte == SE:
                    # sub option end -> process it now
                    self._telnet_process_subnegotiation(bytes(self.suboption))
                    self.suboption = None
                    self.mode = M_NORMAL
                elif byte in (DO, DONT, WILL, WONT):
                    # negotiation
                    self.telnet_command = byte
                    self.mode = M_NEGOTIATE
                else:
                    # other telnet commands
                    self._telnet_process_command(byte)
                    self.mode = M_NORMAL
            elif self.mode == M_NEGOTIATE:  # DO, DONT, WILL, WONT was received, option now following
                self._telnet_negotiate_option(self.telnet_command, byte)
                self.mode = M_NORMAL

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
            self.logger.debug('received COM_PORT_OPTION: {!r}'.format(suboption))
            if suboption[1:2] == SET_BAUDRATE:
                backup = self.serial.baudrate
                try:
                    (baudrate,) = struct.unpack(b"!I", suboption[2:6])
                    if baudrate != 0:
                        self.serial.baudrate = baudrate
                except ValueError as e:
                    self.logger.error("failed to set baud rate: {}".format(e))
                    self.serial.baudrate = backup
                else:
                    self.logger.info("{} baud rate: {}".format('set' if baudrate else 'get', self.serial.baudrate))
                self.rfc2217_send_subnegotiation(SERVER_SET_BAUDRATE, struct.pack(b"!I", self.serial.baudrate))
            elif suboption[1:2] == SET_DATASIZE:
                backup = self.serial.bytesize
                try:
                    (datasize,) = struct.unpack(b"!B", suboption[2:3])
                    if datasize != 0:
                        self.serial.bytesize = datasize
                except ValueError as e:
                    self.logger.error("failed to set data size: {}".format(e))
                    self.serial.bytesize = backup
                else:
                    self.logger.info("{} data size: {}".format('set' if datasize else 'get', self.serial.bytesize))
                self.rfc2217_send_subnegotiation(SERVER_SET_DATASIZE, struct.pack(b"!B", self.serial.bytesize))
            elif suboption[1:2] == SET_PARITY:
                backup = self.serial.parity
                try:
                    parity = struct.unpack(b"!B", suboption[2:3])[0]
                    if parity != 0:
                        self.serial.parity = RFC2217_REVERSE_PARITY_MAP[parity]
                except ValueError as e:
                    self.logger.error("failed to set parity: {}".format(e))
                    self.serial.parity = backup
                else:
                    self.logger.info("{} parity: {}".format('set' if parity else 'get', self.serial.parity))
                self.rfc2217_send_subnegotiation(
                    SERVER_SET_PARITY,
                    struct.pack(b"!B", RFC2217_PARITY_MAP[self.serial.parity]))
            elif suboption[1:2] == SET_STOPSIZE:
                backup = self.serial.stopbits
                try:
                    stopbits = struct.unpack(b"!B", suboption[2:3])[0]
                    if stopbits != 0:
                        self.serial.stopbits = RFC2217_REVERSE_STOPBIT_MAP[stopbits]
                except ValueError as e:
                    self.logger.error("failed to set stop bits: {}".format(e))
                    self.serial.stopbits = backup
                else:
                    self.logger.info("{} stop bits: {}".format('set' if stopbits else 'get', self.serial.stopbits))
                self.rfc2217_send_subnegotiation(
                    SERVER_SET_STOPSIZE,
                    struct.pack(b"!B", RFC2217_STOPBIT_MAP[self.serial.stopbits]))
            elif suboption[1:2] == SET_CONTROL:
                if suboption[2:3] == SET_CONTROL_REQ_FLOW_SETTING:
                    if self.serial.xonxoff:
                        self.rfc2217_send_subnegotiation(SERVER_SET_CONTROL, SET_CONTROL_USE_SW_FLOW_CONTROL)
                    elif self.serial.rtscts:
                        self.rfc2217_send_subnegotiation(SERVER_SET_CONTROL, SET_CONTROL_USE_HW_FLOW_CONTROL)
                    else:
                        self.rfc2217_send_subnegotiation(SERVER_SET_CONTROL, SET_CONTROL_USE_NO_FLOW_CONTROL)
                elif suboption[2:3] == SET_CONTROL_USE_NO_FLOW_CONTROL:
                    self.serial.xonxoff = False
                    self.serial.rtscts = False
                    self.logger.info("changed flow control to None")
                    self.rfc2217_send_subnegotiation(SERVER_SET_CONTROL, SET_CONTROL_USE_NO_FLOW_CONTROL)
                elif suboption[2:3] == SET_CONTROL_USE_SW_FLOW_CONTROL:
                    self.serial.xonxoff = True
                    self.logger.info("changed flow control to XON/XOFF")
                    self.rfc2217_send_subnegotiation(SERVER_SET_CONTROL, SET_CONTROL_USE_SW_FLOW_CONTROL)
                elif suboption[2:3] == SET_CONTROL_USE_HW_FLOW_CONTROL:
                    self.serial.rtscts = True
                    self.logger.info("changed flow control to RTS/CTS")
                    self.rfc2217_send_subnegotiation(SERVER_SET_CONTROL, SET_CONTROL_USE_HW_FLOW_CONTROL)
                elif suboption[2:3] == SET_CONTROL_REQ_BREAK_STATE:
                    self.logger.warning("requested break state - not implemented")
                    pass  # XXX needs cached value
                elif suboption[2:3] == SET_CONTROL_BREAK_ON:
                    self.serial.break_condition = True
                    self.logger.info("changed BREAK to active")
                    self.rfc2217_send_subnegotiation(SERVER_SET_CONTROL, SET_CONTROL_BREAK_ON)
                elif suboption[2:3] == SET_CONTROL_BREAK_OFF:
                    self.serial.break_condition = False
                    self.logger.info("changed BREAK to inactive")
                    self.rfc2217_send_subnegotiation(SERVER_SET_CONTROL, SET_CONTROL_BREAK_OFF)
                elif suboption[2:3] == SET_CONTROL_REQ_DTR:
                    self.logger.warning("requested DTR state - not implemented")
                    pass  # XXX needs cached value
                elif suboption[2:3] == SET_CONTROL_DTR_ON:
                    self.serial.dtr = True
                    self.logger.info("changed DTR to active")
                    self.rfc2217_send_subnegotiation(SERVER_SET_CONTROL, SET_CONTROL_DTR_ON)
                elif suboption[2:3] == SET_CONTROL_DTR_OFF:
                    self.serial.dtr = False
                    self.logger.info("changed DTR to inactive")
                    self.rfc2217_send_subnegotiation(SERVER_SET_CONTROL, SET_CONTROL_DTR_OFF)
                elif suboption[2:3] == SET_CONTROL_REQ_RTS:
                    self.logger.warning("requested RTS state - not implemented")
                    pass  # XXX needs cached value
                    #~ self.rfc2217_send_subnegotiation(SERVER_SET_CONTROL, SET_CONTROL_RTS_ON)
                elif suboption[2:3] == SET_CONTROL_RTS_ON:
                    self.serial.rts = True
                    self.logger.info("changed RTS to active")
                    self.rfc2217_send_subnegotiation(SERVER_SET_CONTROL, SET_CONTROL_RTS_ON)
                elif suboption[2:3] == SET_CONTROL_RTS_OFF:
                    self.serial.rts = False
                    self.logger.info("changed RTS to inactive")
                    self.rfc2217_send_subnegotiation(SERVER_SET_CONTROL, SET_CONTROL_RTS_OFF)
                #~ elif suboption[2:3] == SET_CONTROL_REQ_FLOW_SETTING_IN:
                #~ elif suboption[2:3] == SET_CONTROL_USE_NO_FLOW_CONTROL_IN:
                #~ elif suboption[2:3] == SET_CONTROL_USE_SW_FLOW_CONTOL_IN:
                #~ elif suboption[2:3] == SET_CONTROL_USE_HW_FLOW_CONTOL_IN:
                #~ elif suboption[2:3] == SET_CONTROL_USE_DCD_FLOW_CONTROL:
                #~ elif suboption[2:3] == SET_CONTROL_USE_DTR_FLOW_CONTROL:
                #~ elif suboption[2:3] == SET_CONTROL_USE_DSR_FLOW_CONTROL:
            elif suboption[1:2] == NOTIFY_LINESTATE:
                # client polls for current state
                self.rfc2217_send_subnegotiation(
                    SERVER_NOTIFY_LINESTATE,
                    to_bytes([0]))   # sorry, nothing like that implemented
            elif suboption[1:2] == NOTIFY_MODEMSTATE:
                self.logger.info("request for modem state")
                # client polls for current state
                self.check_modem_lines(force_notification=True)
            elif suboption[1:2] == FLOWCONTROL_SUSPEND:
                self.logger.info("suspend")
                self._remote_suspend_flow = True
            elif suboption[1:2] == FLOWCONTROL_RESUME:
                self.logger.info("resume")
                self._remote_suspend_flow = False
            elif suboption[1:2] == SET_LINESTATE_MASK:
                self.linstate_mask = ord(suboption[2:3])  # ensure it is a number
                self.logger.info("line state mask: 0x{:02x}".format(self.linstate_mask))
            elif suboption[1:2] == SET_MODEMSTATE_MASK:
                self.modemstate_mask = ord(suboption[2:3])  # ensure it is a number
                self.logger.info("modem state mask: 0x{:02x}".format(self.modemstate_mask))
            elif suboption[1:2] == PURGE_DATA:
                if suboption[2:3] == PURGE_RECEIVE_BUFFER:
                    self.serial.reset_input_buffer()
                    self.logger.info("purge in")
                    self.rfc2217_send_subnegotiation(SERVER_PURGE_DATA, PURGE_RECEIVE_BUFFER)
                elif suboption[2:3] == PURGE_TRANSMIT_BUFFER:
                    self.serial.reset_output_buffer()
                    self.logger.info("purge out")
                    self.rfc2217_send_subnegotiation(SERVER_PURGE_DATA, PURGE_TRANSMIT_BUFFER)
                elif suboption[2:3] == PURGE_BOTH_BUFFERS:
                    self.serial.reset_input_buffer()
                    self.serial.reset_output_buffer()
                    self.logger.info("purge both")
                    self.rfc2217_send_subnegotiation(SERVER_PURGE_DATA, PURGE_BOTH_BUFFERS)
                else:
                    self.logger.error("undefined PURGE_DATA: {!r}".format(list(suboption[2:])))
            else:
                self.logger.error("undefined COM_PORT_OPTION: {!r}".format(list(suboption[1:])))
        else:
            self.logger.warning("unknown subnegotiation: {!r}".format(suboption))
