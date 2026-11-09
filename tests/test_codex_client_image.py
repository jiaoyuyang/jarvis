"""Actual Docker derived-image test; enabled on a disposable CI runner."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import uuid

ROOT = Path(__file__).parents[1]


@unittest.skipUnless(os.environ.get('OPS_CLIENT_IMAGE_TEST') == '1', 'disposable Docker runner required')
class ClientImageTest(unittest.TestCase):
    def test_derived_patch_preserves_base_and_inherited_configuration(self):
        tag = 'jarvis-client-ci-' + uuid.uuid4().hex[:12]
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for source, target in [('tests/fixtures/codex_app_server_pinned.py', 'client.py'),
                                   ('tests/fixtures/codex_adapter_pinned.py', 'adapter.py'),
                                   ('patches/patch_qwenpaw_codex_client_health.py', 'patch.py')]:
                shutil.copy2(ROOT / source, folder / target)
            (folder / 'Dockerfile').write_text('FROM python:3.11-slim\nENV BASE_MARKER=preserved\nCOPY client.py adapter.py /fixture/\nCMD ["python", "--version"]\n')
            subprocess.run(['docker', 'build', '-t', tag + ':base', str(folder)], check=True, capture_output=True, timeout=150)
            (folder / 'Dockerfile').write_text('FROM ' + tag + ':base\nCOPY patch.py /patch.py\nRUN python /patch.py --client /fixture/client.py --adapter /fixture/adapter.py\n')
            subprocess.run(['docker', 'build', '--network', 'none', '-t', tag + ':fixed', str(folder)], check=True, capture_output=True, timeout=60)
            script = "from pathlib import Path;import os;print('JARVIS_CODEX_CLIENT_HEALTH_V1' in Path('/fixture/client.py').read_text());print(os.environ['BASE_MARKER'])"
            for name, expected in [('base', 'False\npreserved'), ('fixed', 'True\npreserved')]:
                result = subprocess.check_output(['docker', 'run', '--rm', '--network', 'none', tag + ':' + name, 'python', '-c', script], text=True)
                self.assertEqual(result.strip(), expected)
            old_cmd = subprocess.check_output(['docker', 'image', 'inspect', '--format', '{{json .Config.Cmd}}', tag + ':base'])
            new_cmd = subprocess.check_output(['docker', 'image', 'inspect', '--format', '{{json .Config.Cmd}}', tag + ':fixed'])
            self.assertEqual(old_cmd, new_cmd)


if __name__ == '__main__':
    unittest.main()
