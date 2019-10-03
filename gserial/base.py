import gevent
import serial


class SerialBase(serial.SerialBase):

    def send_break(self, duration=0.25):
        """\
        Send break condition. Timed, returns to idle state after given
        duration.
        """
        if not self.is_open:
            raise portNotOpenError
        self.break_condition = True
        gevent.sleep(duration)
        self.break_condition = False

