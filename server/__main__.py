#!/usr/bin/python3
import tornado.options

from tornado.options import define, options
from .service import Service
from .bk_telemetry import install

install()

define("port", default=8800, type=int)
tornado.options.parse_command_line()

Service(options.port).run()
