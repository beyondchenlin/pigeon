import importlib.util,unittest
from pathlib import Path
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('phone',Path(__file__).resolve().parents[1]/'ops/bk_ai_phone.py')
phone=importlib.util.module_from_spec(spec);spec.loader.exec_module(phone)
class PackagePreparationTests(unittest.TestCase):
 def test_supported_packages(self):
  for p in ('com.beyondchenlin.bk101','com.beyondchenlin.bk101.mcp'):
   with self.subTest(package=p),patch.object(phone.subprocess,'run') as run,patch.object(phone,'api',return_value={'http':200,'body':{'device':{'secure':False,'ready':True,'helper_available':True}}}):
    self.assertTrue(phone.prepare_unsecured('/sdcard/Android/data/'+p+'/files/mcp-usb.json'))
    self.assertEqual(run.call_args_list[-1].args[0],['am','start','-n',p+'/com.wangzan101.mika.ui.MainActivity'])
 def test_unexpected_path_fails_before_actions(self):
  with patch.object(phone.subprocess,'run') as run:
   self.assertFalse(phone.prepare_unsecured('/sdcard/Android/data/other.app/files/mcp-usb.json'))
   run.assert_not_called()
