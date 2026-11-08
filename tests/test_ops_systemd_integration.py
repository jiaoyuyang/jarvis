"""Disposable-VM integration: real UID, mount, AF and cgroup isolation.

Run only with OPS_SYSTEMD_INTEGRATION=1 as root on a systemd VM. Never run on
the production server: fixtures create their own /run, /etc and sudoers paths.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import unittest
import uuid


@unittest.skipUnless(os.environ.get('OPS_SYSTEMD_INTEGRATION') == '1', 'disposable systemd VM only')
class SystemdIsolationTests(unittest.TestCase):
    def setUp(self):
        self.assertEqual(os.geteuid(), 0)
        self.assertEqual(Path('/proc/1/comm').read_text().strip(), 'systemd')
        self.identity = uuid.uuid4().hex
        self.root = Path('/run/jarvis-ops-it-' + self.identity)
        self.root.mkdir(mode=0o755)
        self.etc = Path('/etc/jarvis-ops-it-' + self.identity)
        self.etc.mkdir(mode=0o777)
        self.etc.chmod(0o777)
        self.state = self.root / 'state'
        self.state.mkdir(mode=0o700)
        self.sudoers = Path('/etc/sudoers.d/jarvis-ops-it-' + self.identity)
        self.addCleanup(self.cleanup)
        self.engine = self.root / 'engine.py'
        self.engine.write_bytes((Path(__file__).parents[1] / 'ops/network_engine.py').read_bytes())
        self.worker = self.root / 'worker.py'
        self.worker.write_text('''import json,os,socket
from pathlib import Path
s=socket.socket(socket.AF_NETLINK,socket.SOCK_RAW,socket.NETLINK_ROUTE);s.close()
target=Path(''' + repr(str(self.etc)) + ''')
(target/'worker-ok').write_text('ok')
try:
    Path('/etc/jarvis-ops-it-forbidden-'''+self.identity+'''').write_text('bad')
    outside_readonly=False
except OSError:
    outside_readonly=True
status=Path('/proc/self/status').read_text()
print(json.dumps({'ok':True,'result':{'netlink':True,'outside_readonly':outside_readonly,
    'uid':os.geteuid(),'nnp':next(x for x in status.splitlines() if x.startswith('NoNewPrivs:')).endswith('1')}}))
''')
        self.installer = self.root / 'installer.py'
        self.installer.write_text('''import time,subprocess,os
from pathlib import Path
time.sleep(3)
r=subprocess.run(['/usr/bin/python3','-c','import os;print(os.geteuid())'],user=65534,group=65534,extra_groups=[],capture_output=True,text=True)
assert r.returncode==0, r.stderr
assert r.stdout.strip()=='65534'
p=Path(''' + repr(str(self.etc / 'async-survived')) + ''')
p.write_text('independent')
os.chown(p,65534,65534)
''')
        helper_py = self.root / 'helper.py'
        helper_py.write_text('''import importlib.util,json,sys
from pathlib import Path
s=importlib.util.spec_from_file_location('engine', '''+repr(str(self.engine))+''')
m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
m.ENGINE='''+repr(str(self.worker))+'''
m.INSTALLER='''+repr(str(self.installer))+'''
m.STATE=Path('''+repr(str(self.state))+''')
m.WORKER_PATHS=m.UPGRADE_PATHS=('''+repr(str(self.state))+','+repr(str(self.etc))+''')
result=m.broker(sys.argv[1:],sys.stdin.buffer.read(65537))
print(json.dumps(result if 'ok' in result else {'ok':True,'result':result}))
''')
        self.helper = self.root / 'helper'
        self.helper.write_text('#!/bin/sh\nexec '+sys.executable+' -I '+str(helper_py)+' "$@"\n')
        self.helper.chmod(0o755)
        self.sudoers.write_text('nobody ALL=(root) NOPASSWD: '+str(self.helper)+'\n')
        self.sudoers.chmod(0o440)
        subprocess.run(['visudo','-cf',str(self.sudoers)],check=True,capture_output=True)
        self.caller = self.root / 'caller.py'
        self.caller.write_text('''import errno,json,os,socket,subprocess,sys
from pathlib import Path
try:
    socket.socket(socket.AF_NETLINK,socket.SOCK_RAW,socket.NETLINK_ROUTE).close()
    direct_netlink_denied=False
except OSError:
    direct_netlink_denied=True
try:
    Path('''+repr(str(self.etc/'caller-forbidden'))+''').write_text('bad')
    direct_readonly=False
except OSError as error:
    direct_readonly=error.errno==errno.EROFS
action='upgrade' if sys.argv[1]=='upgrade' else 'snapshot'
data={'commit_sha':'f'*40,'package_sha256':'a'*64} if action=='upgrade' else {}
r=subprocess.run(['/usr/bin/sudo','-n','''+repr(str(self.helper))+''',action],
    input=json.dumps(data),capture_output=True,text=True,timeout=25)
print(json.dumps({'uid':os.geteuid(),'direct_netlink_denied':direct_netlink_denied,
    'direct_readonly':direct_readonly,'code':r.returncode,'response':r.stdout,'stderr':r.stderr}))
''')

    def cleanup(self):
        if self.state.exists():
            for record in self.state.glob('upgrade-*.json'):
                identity = json.loads(record.read_text())['id']
                subprocess.run(['systemctl','stop','jarvis-ops-upgrade-'+identity+'.service'],capture_output=True)
        self.sudoers.unlink(missing_ok=True)
        shutil.rmtree(self.root, ignore_errors=True)
        shutil.rmtree(self.etc, ignore_errors=True)

    def call(self, action):
        argv = ['/usr/bin/systemd-run','--quiet','--pipe','--wait','--collect',
            '--unit=jarvis-ops-it-caller-'+self.identity,
            '--property=User=nobody','--property=ProtectSystem=full',
            '--property=NoNewPrivileges=no',
            '--property=RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6',
            '/usr/bin/python3',str(self.caller),action]
        result = subprocess.run(argv,capture_output=True,text=True,timeout=35)
        self.assertEqual(result.returncode,0,result.stderr)
        data=json.loads(result.stdout)
        self.assertEqual(data['uid'],65534)
        self.assertTrue(data['direct_netlink_denied'])
        self.assertTrue(data['direct_readonly'])
        self.assertEqual(data['code'],0,data['stderr'])
        return json.loads(data['response'])

    def test_restricted_nonroot_caller_reaches_only_bounded_worker(self):
        result=self.call('snapshot')
        self.assertTrue(result['ok'])
        self.assertTrue(result['result']['netlink'])
        self.assertTrue(result['result']['outside_readonly'])
        self.assertTrue(result['result']['nnp'])
        self.assertEqual(result['result']['uid'],0)
        self.assertEqual((self.etc/'worker-ok').read_text(),'ok')

    def test_async_upgrade_survives_the_calling_unit_exit(self):
        result=self.call('upgrade')
        self.assertEqual(result['result']['phase'],'queued')
        self.assertTrue(result['result']['independent_service'])
        marker=self.etc/'async-survived'
        deadline=time.monotonic()+10
        while not marker.exists() and time.monotonic()<deadline:
            time.sleep(0.1)
        if not marker.exists():
            unit='jarvis-ops-upgrade-'+result['result']['upgrade_id']+'.service'
            log=subprocess.run(['journalctl','-u',unit,'-n','40','--no-pager'],capture_output=True,text=True)
            self.fail('independent upgrade did not complete:\n'+log.stdout+log.stderr)


if __name__ == '__main__':
    unittest.main()
