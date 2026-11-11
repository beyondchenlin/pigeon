"""Load additive BK handlers, then run the original API server."""
import runpy
from pathlib import Path
import server

extension = Path('/user/phone-pilot/bk-server')
if extension.is_dir():
    server.__path__.append(str(extension))
from server.bk_telemetry import install
install()
from server.bk_ai import install as install_ai
install_ai()
from server.bk_maintenance import install as install_maintenance
install_maintenance()
runpy.run_module('server', run_name='__main__')
