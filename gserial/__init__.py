import os
import importlib

if os.name == 'posix':
    from .posix import Serial, PosixPollSerial, VTIMESerial # noqa
else:
    raise ImportError("Sorry: no implementation for your platform ('{}') " \
                      "available".format(os.name))

from .base import SerialBase

__version__ = '0.2.2'

del os

protocol_handler_packages = [
    'gserial',
]

def serial_for_url(url, *args, **kwargs):
    """\
    Get an instance of the Serial class, depending on port/url. The port is not
    opened when the keyword parameter 'do_not_open' is true, by default it
    is. All other parameters are directly passed to the __init__ method when
    the port is instantiated.
    The list of package names that is searched for protocol handlers is kept in
    ``protocol_handler_packages``.
    e.g. we want to support a URL ``foobar://``. A module
    ``my_handlers.protocol_foobar`` is provided by the user. Then
    ``protocol_handler_packages.append("my_handlers")`` would extend the search
    path so that ``serial_for_url("foobar://"))`` would work.
    """
    # check and remove extra parameter to not confuse the Serial class
    do_open = not kwargs.pop('do_not_open', False)
    # the default is to use the native implementation
    klass = Serial
    try:
        url_lowercase = url.lower()
    except AttributeError:
        # it's not a string, use default
        pass
    else:
        # if it is an URL, try to import the handler module from the list of possible packages
        if '://' in url_lowercase:
            protocol = url_lowercase.split('://', 1)[0]
            module_name = '.{}'.format(protocol)
            for package_name in protocol_handler_packages:
                try:
                    importlib.import_module(package_name)
                    handler_module = importlib.import_module(module_name, package_name)
                except ImportError:
                    continue
                else:
                    if hasattr(handler_module, 'serial_class_for_url'):
                        url, klass = handler_module.serial_class_for_url(url)
                    else:
                        klass = handler_module.Serial
                    break
            else:
                raise ValueError('invalid URL, protocol {!r} not known'.format(protocol))
    # instantiate and open when desired
    instance = klass(None, *args, **kwargs)
    instance.port = url
    if do_open:
        instance.open()
    return instance
