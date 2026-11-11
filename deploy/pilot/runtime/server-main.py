#!/usr/bin/python3
import tornado.options

from tornado.options import define, options
from .service import Service
# BK_TELEMETRY_EXTENSION
import server
server.__path__.append('/user/phone-pilot/bk-server')
from server.bk_telemetry import install
install()
from server.bk_ai import install as install_ai
install_ai()
from server.bk_maintenance import install as install_maintenance
install_maintenance()


define("port", default=8800, type=int)
tornado.options.parse_command_line()

Service(options.port).run()
