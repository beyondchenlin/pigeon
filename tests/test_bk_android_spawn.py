import unittest
import os
from pathlib import Path
from unittest.mock import patch
from ops.bk_android_spawn import media_argv


class NativeCommandTests(unittest.TestCase):
    def test_shell_strings_are_not_accepted_as_native_argv(self):
        with self.assertRaises(ValueError):media_argv('screen; another command', '/runtime')

    def test_native_argv_resolves_only_trusted_bins(self):
        with patch('ops.bk_android_spawn.shutil.which', return_value='/runtime/bin/minicap') as which:
            result=media_argv(['minicap','-n','socket with spaces'], '/runtime')
        self.assertEqual(result, ['/runtime/bin/minicap', '-n', 'socket with spaces'])
        self.assertEqual(which.call_args.kwargs['path'], os.pathsep.join(map(str,[Path('/runtime/lib/python3.12/site-packages/lamda/tool'),Path('/runtime/bin'),Path('/system/bin')])))

    def test_existing_argv_preserves_literal_arguments(self):
        self.assertEqual(media_argv(['/system/bin/example', 'x;echo unwanted'], '/runtime'),
                         ['/system/bin/example', 'x;echo unwanted'])

    def test_missing_executable_and_nul_are_rejected(self):
        with patch('ops.bk_android_spawn.shutil.which', return_value=None):
            with self.assertRaisesRegex(ValueError, 'not found'):media_argv(['unknown'], '/runtime')
        with self.assertRaises(ValueError):media_argv(['/system/bin/example', '\0'], '/runtime')
