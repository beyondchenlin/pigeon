import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


@unittest.skipUnless(os.name=='posix' and shutil.which('bash'), 'Use existing Linux bash')
class TermuxDiscoveryTests(unittest.TestCase):
    def test_fresh_system_port_only_and_no_interactive_prompt(self):
        script=Path(__file__).resolve().parents[1]/'ops/bk_termux_tunnel.sh'
        subprocess.run(['bash','-n',str(script)],check=True,timeout=3)
        with tempfile.TemporaryDirectory(prefix='bk-termux-discovery-') as folder:
            path=Path(folder)/'port'
            for data,expected in [('1794367000 42645\n','42645\n'),
                                  ('1794366000 42645\n',''),
                                  ('1794367000 -1\n',''),
                                  ('1794367000 999999\n',''),
                                  ('bad 42645\n',''),
                                  ('1794368000 42645\n','')]:
                path.write_text(data)
                r=subprocess.run(['bash','-c','source "$1"; discovered_port "$2" 1794367010',
                                  'test',str(script),str(path)],capture_output=True,text=True,timeout=3)
                self.assertEqual(r.stdout,expected,data)
                self.assertEqual(r.returncode,0 if expected else 1,data)


if __name__=='__main__':unittest.main()
