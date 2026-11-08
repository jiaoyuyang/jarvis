import copy
import asyncio
import base64
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import urllib.parse
import urllib.error


ROOT = Path(__file__).parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


engine = load("network_engine", ROOT / "ops/network_engine.py")
installer = load("ops_installer", ROOT / "ops/install.py")
extension = load("ops_extension", ROOT / "ops/mcp_extension.py")

CONFIG = '''# Preserve this comment and every secret outside edited values.
external-controller: 127.0.0.1:9090
secret: SECRET_SENTINEL
profile: {store-selected: true}
dns:
  prefer-h3: true
  respect-rules: true
  proxy-server-nameserver: [https://dns.alidns.com/dns-query]
  fallback: [https://cloudflare-dns.com/dns-query]
proxies:
  - {name: JP1, type: trojan, password: PASSWORD_SENTINEL}
proxy-groups:
  - {name: OpenAI专用, type: fallback, proxies: [梯子猫, 自动选择]}
  - {name: 主备自动切换, type: fallback, proxies: [梯子猫, 一元日本01专线]}
'''
WG = '''[Interface]
PrivateKey = PRIVATE_KEY_SENTINEL
Address = 172.16.0.2/32
[Peer]
PublicKey = PUBLIC
AllowedIPs = 202.89.233.100/32, 202.89.233.101/32
Endpoint = example.invalid:2408
'''
SERVER = '''from mcp.server.fastmcp import FastMCP
app = FastMCP("Existing Ops")
@app.tool()
def existing_tool():
    return "unchanged"
if __name__ == "__main__":
    app.run()
'''

# Production's observed imports, instance, annotated inputs, all eleven existing
# decorator tools and standard main guard. Bodies are inert; no production secrets.
EXISTING_TOOLS = {"system_overview", "jarvis_status", "proxy_status", "git_status", "recent_logs",
                  "restart_jarvis", "recover_codex_session", "set_proxy_node", "restart_mihomo",
                  "deploy_jarvis_main", "rollback_jarvis"}
MCP2_SERVER = '''from typing import Annotated, Literal
from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field
mcp = MCPServer("Existing production-style Ops")
READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False)
PRIVATE_WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False)
HIGH_IMPACT_WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=False)
SessionId = Annotated[str, Field(min_length=1, max_length=128)]
ProxyTarget = Literal["primary_jp01", "primary_jp02", "backup_jp01"]
CommitSha = Annotated[str, Field(min_length=40, max_length=40)]
'''
for _name in sorted(EXISTING_TOOLS):
    _hint = "READ_ONLY" if _name in {"system_overview", "jarvis_status", "proxy_status", "git_status", "recent_logs"} else (
        "HIGH_IMPACT_WRITE" if _name in {"deploy_jarvis_main", "rollback_jarvis"} else "PRIVATE_WRITE")
    _input = {"recover_codex_session": "session_id: SessionId", "set_proxy_node": "target: ProxyTarget",
              "rollback_jarvis": "commit_sha: CommitSha"}.get(_name, "")
    MCP2_SERVER += f'@mcp.tool(title="Existing {_name}", annotations={_hint})\ndef {_name}({_input}) -> str:\n    return "unchanged"\n'
MCP2_SERVER += 'if __name__ == "__main__":\n    mcp.run()\n'


class FakeController:
    def __init__(self):
        self.groups = {
            "梯子猫": {"type": "Selector", "now": "自动选择", "all": ["自动选择", "JP1", "JP2"]},
            "一元日本01专线": {"type": "Selector", "now": "B1", "all": ["B1", "B2"]},
            "OpenAI专用": {"type": "Fallback", "now": "一元日本01专线", "all": ["梯子猫", "一元日本01专线"]},
            "主备自动切换": {"type": "Fallback", "now": "一元日本01专线", "all": ["梯子猫", "一元日本01专线"]}}
        self.calls = []
        self.probe_failure, self.dns_failure = False, False

    def call(self, path, method="GET", data=None, timeout=10):
        self.calls.append((path, method, data))
        parsed = urllib.parse.urlsplit(path)
        name = urllib.parse.unquote(parsed.path)
        if name.startswith("/providers/proxies/"):
            nodes = ["JP1", "JP2"] if name.endswith("梯子猫订阅") else ["B1", "B2"]
            return {"proxies": [{"name": n, "type": "Trojan", "alive": False} for n in nodes]}
        if name.endswith("/delay"):
            node = name.split("/")[-2]
            expected = urllib.parse.parse_qs(parsed.query)["expected"][0]
            if node in ("JP2", "B2") or (self.probe_failure and expected == "401"):
                raise TimeoutError("SECRET_SENTINEL must not escape")
            return {"delay": 300}
        if name.startswith("/proxies/"):
            node = name[len("/proxies/"):]
            if method == "PUT":
                self.groups[node]["now"] = data["name"]
                if node == "梯子猫":
                    for group in engine.FALLBACKS:
                        self.groups[group]["now"] = "梯子猫" if data["name"] == "JP1" else "一元日本01专线"
                return {}
            return copy.deepcopy(self.groups[node])
        if name == "/dns/query":
            return {"Status": 2 if self.dns_failure else 0, "Answer": [] if self.dns_failure else [{"data": "1.2.3.4"}]}
        if name == "/configs":
            return {}
        if name == '/cache/dns/flush' and method == 'POST':
            return {}
        raise AssertionError("Unexpected controller operation: " + name)


class FakeRunner:
    def __init__(self):
        self.calls, self.timers = [], set()
        self.guard_failure = False
        self.allowed = "202.89.233.100/32 202.89.233.101/32 59.125.68.205/32"

    def __call__(self, argv, timeout=15):
        self.calls.append(list(argv))
        if argv[:4] == ["wg", "show", "wg0", "allowed-ips"]:
            return "PEER_PUBLIC_KEY\t" + self.allowed
        if argv[:5] == ["ip", "-4", "-j", "route", "get"]:
            return '[{"dev":"wg0","prefsrc":"172.16.0.2"}]'
        if argv[0] == "/usr/local/bin/mihomo":
            return "configuration valid"
        if argv[0] == "systemd-run":
            if self.guard_failure:
                raise engine.OperationError("guard_failure")
            self.timers.add(next(x.split("=", 1)[1] for x in argv if x.startswith("--unit=")) + ".timer")
            return ""
        if argv[:2] == ["systemctl", "is-active"]:
            return "active" if argv[2] in self.timers else "inactive"
        if argv[:2] == ["systemctl", "stop"]:
            self.timers.discard(argv[2])
            return ""
        raise AssertionError("Unexpected host command: " + repr(argv))


class NetworkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config, self.wg = self.root / "config.yaml", self.root / "wg0.conf"
        self.config.write_text(CONFIG)
        self.wg.write_text(WG)
        self.controller, self.runner = FakeController(), FakeRunner()
        self.now = 1000.0
        self.chown = mock.patch.object(os, "fchown")
        self.chown.start()
        self.addCleanup(self.chown.stop)
        self.e = engine.Engine(self.config, self.wg, self.root / "state",
                               self.controller, self.runner, lambda: self.now)

    def receipts(self):
        primary = self.e.probe("primary")["results"][0]["id"]
        backup = self.e.probe("backup")["results"][0]["id"]
        return primary, backup

    def switch(self):
        return self.e.prepare("switch_primary", *self.receipts())["id"]

    def test_snapshot_and_probe_never_expose_secrets(self):
        text = json.dumps([self.e.snapshot(), self.e.probe()], ensure_ascii=False)
        for secret in ("SECRET_SENTINEL", "PASSWORD_SENTINEL", "PRIVATE_KEY_SENTINEL", "PEER_PUBLIC_KEY"):
            self.assertNotIn(secret, text)

    def test_wg_permission_failure_keeps_other_snapshot_evidence(self):
        original = self.e.runner
        def runner(argv, **kwargs):
            if argv[0] == "wg":
                raise engine.HostCommandError("wg", 1, "Unable to access interface: Operation not permitted SECRET_SENTINEL")
            return original(argv, **kwargs)
        self.e.runner = runner
        snapshot = self.e.snapshot()
        self.assertEqual(snapshot['wireguard_read_error']['reason'], 'permission_denied')
        self.assertEqual(snapshot['entry_routes']['59.125.68.205']['dev'], 'wg0')
        self.assertIn('OpenAI专用', snapshot['groups'])
        self.assertNotIn('SECRET_SENTINEL', json.dumps(snapshot))

    def test_provider_inventory_excludes_credentials_and_outside_paths(self):
        cache = self.root / 'primary.yaml'
        cache.write_text('proxies:\n - {name: JP1, type: trojan, server: cn.bing.com, port: 50130, sni: static.example.com, password: PASSWORD_SENTINEL}\n')
        self.e.config['proxy-providers'] = {'梯子猫订阅': {'path': 'primary.yaml', 'url': 'https://example.com/SECRET_SENTINEL'}}
        result = self.e.provider_inventory('primary')
        self.assertEqual(result['nodes'][0]['port'], 50130)
        self.assertEqual(result['source_host'], 'example.com')
        self.assertNotIn('SENTINEL', json.dumps(result))
        self.e.config['proxy-providers']['梯子猫订阅']['path'] = '../outside.yaml'
        self.assertEqual(self.e.provider_inventory('primary')['cache_error'], 'cache_path_outside_mihomo')

    def test_controller_http_status_is_preserved_without_raw_response(self):
        original = self.controller.call
        def call(path, **kwargs):
            if '/delay?' in path:
                raise urllib.error.HTTPError('http://127.0.0.1/SECRET_SENTINEL', 504, 'SECRET_SENTINEL', {}, None)
            return original(path, **kwargs)
        self.controller.call = call
        result = self.e.probe(limit=1)
        self.assertEqual(result['results'][0]['tests'][0]['controller_http_status'], 504)
        self.assertNotIn('SECRET_SENTINEL', json.dumps(result))

    def test_quota_and_expiry_labels_are_not_probed_as_nodes(self):
        original = self.controller.call
        def call(path, **kwargs):
            if path.startswith('/providers/proxies/'):
                return {'proxies': [{'name': n, 'type': 'Trojan'} for n in ('JP1', '剩余流量：1 GB', '套餐到期：2099-01-01')]}
            return original(path, **kwargs)
        self.controller.call = call
        self.assertEqual(self.e.probe()['total'], 1)

    def test_fresh_probes_ignore_cached_dead_and_require_both_checks(self):
        result = self.e.probe()
        self.assertTrue(result["results"][0]["ok"])
        self.assertFalse(result["results"][1]["ok"])
        self.assertEqual(len([c for c in self.controller.calls if "/delay?" in c[0]]), 4)
        self.controller.probe_failure = True
        self.assertFalse(self.e.probe()["results"][0]["ok"])

    def test_provider_dns_plan_changes_only_node_policy_and_keeps_bootstrap(self):
        url='https://web.laddercat.net/subscribe/'+'a'*32
        text=CONFIG+'proxy-providers:\n  梯子猫订阅:\n    url: '+url+'\n'
        self.config.write_text(text)
        self.e.config=engine.yaml.safe_load(text)
        self.controller.groups[engine.SELECTORS['backup']]['alive']=True
        with mock.patch.object(self.e,'primary_dns_template',return_value=list(engine.PRIMARY_DNS_RESOLVERS)):
            plan=self.e.prepare('stabilize_dns')
        self.assertEqual(self.config.read_text(),text)
        self.assertNotIn('a'*32,json.dumps(plan))
        self.assertNotIn('PASSWORD_SENTINEL',json.dumps(plan))
        record=self.e.load(plan['id'])
        candidate=engine.yaml.safe_load(record['new_config'])
        self.assertEqual(candidate['dns']['proxy-server-nameserver'],self.e.config['dns']['proxy-server-nameserver'])
        self.assertEqual(candidate['proxy-providers'],self.e.config['proxy-providers'])
        self.assertEqual(candidate['proxies'],self.e.config['proxies'])
        for domain in engine.PRIMARY_NODE_DOMAINS:
            self.assertEqual(candidate['dns']['proxy-server-nameserver-policy'][domain],
                             [u+'#一元日本01专线' for u in engine.PRIMARY_DNS_RESOLVERS])
        self.e.apply(plan['id'])
        self.assertIn(('/cache/dns/flush','POST',None),self.controller.calls)
        self.e.rollback(plan['id'])
        self.assertEqual(self.config.read_text(),text)
        with mock.patch.object(self.e,'primary_dns_template',return_value=list(engine.PRIMARY_DNS_RESOLVERS)):
            failed=self.e.prepare('stabilize_dns')
        with mock.patch.object(self.e,'flush_dns_cache',return_value=False):
            with self.assertRaisesRegex(engine.OperationError,'dns_cache_flush_failed'):
                self.e.apply(failed['id'])
        self.assertEqual(self.e.status(failed['id'])['phase'],'rolled_back')
        self.assertEqual(self.config.read_text(),text)

    def test_mapping_insert_preserves_comments_and_other_values(self):
        text='dns:\n  # keep DNS comment\n  enable: true\nsecret: SECRET_SENTINEL\n'
        changed=engine.yaml_mapping_update(text,('dns',),{'new-policy':{'cn.bing.com':['https://example.com/inst#backup']}})
        self.assertIn('# keep DNS comment',changed)
        self.assertTrue(changed.endswith('secret: SECRET_SENTINEL\n'))
        self.assertTrue(engine.yaml.safe_load(changed)['dns']['enable'])
        inline='dns: {enable: true}\n'
        self.assertIn('new',engine.yaml.safe_load(engine.yaml_mapping_update(inline,('dns',),{'new':1}))['dns'])

    def test_block_collections_keep_the_separator_before_following_keys(self):
        text='dns:\n  prefer-h3: true\n  fallback:\n    - https://old.example/query\n  respect-rules: true\nsecret: SECRET_SENTINEL\n'
        values={'prefer-h3':False,'fallback':['https://new.example/query'],
                'proxy-server-nameserver-policy':{'cn.bing.com':['https://custom.tizimao.net/inst#backup']}}
        changed=engine.yaml_mapping_update(text,('dns',),values)
        data=engine.yaml.safe_load(changed)
        self.assertTrue(data['dns']['respect-rules'])
        self.assertEqual(data['dns']['fallback'],values['fallback'])
        self.assertTrue(changed.endswith('secret: SECRET_SENTINEL\n'))
        nested='dns:\n  policy:\n    host.example:\n      - 1.1.1.1\n  enable: true\n'
        changed=engine.yaml_patch(nested,[(('dns','policy'),{'new.example':['8.8.8.8']})])
        self.assertTrue(engine.yaml.safe_load(changed)['dns']['enable'])

    def test_subscription_template_is_pinned_and_rejects_untrusted_resolvers(self):
        with mock.patch.object(engine.urllib.request,'build_opener') as builder:
            for url in ('http://web.laddercat.net/subscribe/'+'a'*32, 'https://evil.example/subscribe/'+'a'*32,
                        'https://web.laddercat.net/subscribe/'+'a'*32+'?token=bad'):
                with self.assertRaises(engine.OperationError): self.e.primary_dns_template(url)
            builder.assert_not_called()
        body={'dns':{'proxy-server-nameserver':['https://evil.example/collect']},'proxies':[]}
        response=mock.MagicMock()
        response.__enter__.return_value.read.return_value=engine.yaml.safe_dump(body).encode()
        opener=mock.Mock();opener.open.return_value=response
        with mock.patch.object(engine.urllib.request,'build_opener',return_value=opener):
            with self.assertRaisesRegex(engine.OperationError,'provider_dns_template_changed'):
                self.e.primary_dns_template('https://web.laddercat.net/subscribe/'+'a'*32)

    def test_pagination_and_bounds(self):
        self.assertEqual(self.e.probe(limit=1)["next_offset"], 1)
        self.assertIsNone(self.e.probe(offset=1, limit=1)["next_offset"])
        for kwargs in ({"limit": 7}, {"timeout_ms": 999999}, {"offset": -1}, {"limit": True}, {"provider": "arbitrary"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(engine.OperationError):
                self.e.probe(**kwargs)

    def test_prepare_does_not_touch_runtime_and_is_reviewable(self):
        before = copy.deepcopy(self.controller.groups)
        plan = self.e.prepare("switch_primary", *self.receipts())
        self.assertEqual(self.config.read_text(), CONFIG)
        self.assertEqual(self.controller.groups, before)
        text = json.dumps(plan, ensure_ascii=False)
        self.assertNotIn("SECRET_SENTINEL", text)
        self.assertEqual(plan["summary"]["target_selectors"]["primary"], "JP1")

    def test_prepare_rejects_wrong_expired_failed_and_drifted_receipts(self):
        primary, backup = self.receipts()
        with self.assertRaises(engine.OperationError):
            self.e.prepare("switch_primary", backup, primary)
        self.now += 181
        with self.assertRaisesRegex(engine.OperationError, "probe_expired"):
            self.e.prepare("switch_primary", primary, backup)
        self.now -= 181
        failed = self.e.probe()["results"][1]["id"]
        with self.assertRaisesRegex(engine.OperationError, "probe_not_successful"):
            self.e.prepare("switch_primary", failed, backup)
        self.config.write_text(CONFIG + "# outside edit\n")
        with self.assertRaisesRegex(engine.OperationError, "config_changed_since_probe"):
            self.e.prepare("switch_primary", primary, backup)

    def test_ids_do_not_allow_paths(self):
        for identity in ("../secret", "/etc/shadow", "A" * 32, "0" * 33):
            with self.subTest(identity=identity), self.assertRaises(engine.OperationError):
                self.e.status(identity)

    def test_guard_is_active_before_configuration_changes(self):
        identity = self.switch()
        original = self.e.write_existing
        def guarded(path, value):
            self.assertTrue(self.runner.timers)
            return original(path, value)
        with mock.patch.object(self.e, "write_existing", side_effect=guarded):
            result = self.e.apply(identity)
        self.assertEqual(result["phase"], "pending")
        cfg = engine.yaml.safe_load(self.config.read_text())
        for name in engine.FALLBACKS:
            members = next(g for g in cfg["proxy-groups"] if g["name"] == name)["proxies"]
            self.assertEqual(members[0], "梯子猫")
            self.assertEqual(members[-1], "一元日本01专线")

    def test_failed_guard_does_not_leave_configuration_changes(self):
        identity = self.switch()
        self.runner.guard_failure = True
        with self.assertRaises(engine.OperationError):
            self.e.apply(identity)
        self.assertEqual(self.config.read_text(), CONFIG)
        self.assertEqual(self.e.status(identity)["phase"], "rolled_back")

    def test_expired_receipts_are_rechecked_on_apply(self):
        identity = self.switch()
        self.now += 181
        # Make plan itself fresh to isolate the receipt check.
        item = self.e.load(identity)
        item["created_at"] = self.now
        self.e.save(item)
        with self.assertRaisesRegex(engine.OperationError, "probe_expired"):
            self.e.apply(identity)
        self.assertFalse(self.runner.timers)

    def test_configuration_drift_is_rejected_before_applying(self):
        identity = self.switch()
        self.config.write_text(CONFIG + "# outside edit\n")
        with self.assertRaisesRegex(engine.OperationError, "configuration_drift"):
            self.e.apply(identity)
        self.assertFalse(self.runner.timers)

    def test_failed_confirmation_keeps_guard(self):
        identity = self.switch()
        self.e.apply(identity)
        with mock.patch.object(self.e, "proxy_check", side_effect=engine.OperationError("failed")):
            with self.assertRaises(engine.OperationError):
                self.e.confirm(identity)
        self.assertEqual(self.e.status(identity)["phase"], "pending")
        self.assertTrue(self.runner.timers)

    def test_confirmation_requires_actual_primary_selection(self):
        identity = self.switch()
        self.e.apply(identity)
        self.controller.groups["OpenAI专用"]["now"] = "一元日本01专线"
        with mock.patch.object(self.e, "proxy_check", return_value={"openai_http": 401}):
            with self.assertRaisesRegex(engine.OperationError, "primary_not_selected_yet"):
                self.e.confirm(identity)
        self.assertTrue(self.runner.timers)

    def test_confirm_stops_guard_and_committed_rollback_is_noop(self):
        identity = self.switch()
        self.e.apply(identity)
        with mock.patch.object(self.e, "proxy_check", return_value={"openai_http": 401}):
            self.assertEqual(self.e.confirm(identity)["phase"], "committed")
        self.assertFalse(self.runner.timers)
        current = self.config.read_text()
        self.e.rollback(identity, automatic=True)
        self.assertEqual(self.config.read_text(), current)

    def test_guard_deadline_cannot_be_extended_by_confirmation(self):
        identity = self.switch()
        self.e.apply(identity)
        self.now += 121
        with mock.patch.object(self.e, "proxy_check", return_value={}):
            with self.assertRaisesRegex(engine.OperationError, "guard_expired"):
                self.e.confirm(identity)
        self.assertTrue(self.runner.timers)

    def test_rollback_restores_configuration_and_both_selectors(self):
        identity = self.switch()
        self.e.apply(identity)
        self.e.rollback(identity, automatic=True)
        self.assertEqual(self.config.read_text(), CONFIG)
        self.assertEqual(self.e.selected(), {"primary": "自动选择", "backup": "B1"})
        self.assertEqual(self.e.rollback(identity)["phase"], "rolled_back")
        self.assertFalse(self.runner.timers)

    def test_rollback_does_not_overwrite_an_external_edit(self):
        identity = self.switch()
        self.e.apply(identity)
        current = self.config.read_text() + "# another operator\n"
        self.config.write_text(current)
        with self.assertRaisesRegex(engine.OperationError, "restore_configuration_drift"):
            self.e.rollback(identity)
        self.assertEqual(self.config.read_text(), current)

    def test_only_one_pending_change(self):
        first, second = self.switch(), self.switch()
        self.e.apply(first)
        with self.assertRaisesRegex(engine.OperationError, "another_change_pending|selector_drift"):
            self.e.apply(second)

    def test_dns_plan_preserves_credentials_and_bootstrap(self):
        identity = self.e.prepare("stabilize_dns")["id"]
        item = self.e.load(identity)
        self.assertIn("# Preserve this comment", item["new_config"])
        self.assertIn("secret: SECRET_SENTINEL", item["new_config"])
        before, after = engine.yaml.safe_load(CONFIG), engine.yaml.safe_load(item["new_config"])
        self.assertEqual(before["dns"]["proxy-server-nameserver"], after["dns"]["proxy-server-nameserver"])
        self.assertEqual(before["proxies"], after["proxies"])
        self.e.apply(identity)
        self.controller.dns_failure = True
        with mock.patch.object(self.e, "proxy_check", return_value={}):
            with self.assertRaisesRegex(engine.OperationError, "dns_check_failed"):
                self.e.confirm(identity)

    def test_wg_persistence_is_a_local_consistency_test(self):
        identity = self.e.prepare("persist_wireguard")["id"]
        self.e.apply(identity)
        self.assertEqual(self.config.read_text(), CONFIG)
        self.assertIn("59.125.68.205/32", self.wg.read_text())
        self.assertIn("PrivateKey = PRIVATE_KEY_SENTINEL", self.wg.read_text())
        with mock.patch.object(self.e, "proxy_check", side_effect=AssertionError("unnecessary external check")):
            self.assertEqual(self.e.confirm(identity)["phase"], "committed")

    def test_wg_default_routes_removal_and_saveconfig_are_rejected(self):
        for value in ("0.0.0.0/0", "127.0.0.1/32", "202.89.233.100/32"):
            self.runner.allowed = value
            with self.subTest(value=value), self.assertRaises(engine.OperationError):
                self.e.prepare("persist_wireguard")
        self.runner.allowed = "202.89.233.100/32 202.89.233.101/32"
        self.wg.write_text(WG.replace("Address =", "SaveConfig = true\nAddress ="))
        with self.assertRaises(engine.OperationError):
            self.e.prepare("persist_wireguard")

    def test_wg_runtime_drift_prevents_confirmation(self):
        identity = self.e.prepare("persist_wireguard")["id"]
        self.e.apply(identity)
        self.runner.allowed += " 1.1.1.1/32"
        with self.assertRaisesRegex(engine.OperationError, "wireguard_runtime_drift"):
            self.e.confirm(identity)


class InstallerTests(unittest.TestCase):
    def test_production_mcpserver_structure_preserves_all_existing_tools(self):
        first, variable = installer.server_patch(MCP2_SERVER)
        second, _ = installer.server_patch(first)
        self.assertEqual(first, second)
        self.assertEqual(variable, "mcp")
        self.assertEqual(installer.registered_names(first, variable), EXISTING_TOOLS)
        self.assertIn('annotations=HIGH_IMPACT_WRITE', first)
        self.assertIn('session_id: SessionId', first)
        self.assertTrue(first.endswith('    mcp.run()\n'))

    def test_sdk_import_aliases_and_annotated_instance(self):
        variants = [
            MCP2_SERVER.replace('import MCPServer', 'import MCPServer as Ops').replace('mcp = MCPServer(', 'mcp: Ops = Ops('),
            MCP2_SERVER.replace('from mcp.server import MCPServer', 'import mcp.server as sdk').replace('mcp = MCPServer(', 'mcp = sdk.MCPServer('),
        ]
        for source in variants:
            with self.subTest(source=source[:100]):
                self.assertEqual(installer.server_patch(source)[1], 'mcp')

    def test_unknown_constructors_multiple_instances_and_wrong_run_are_rejected(self):
        variants = [
            MCP2_SERVER.replace('from mcp.server import MCPServer', 'from unknown import MCPServer'),
            MCP2_SERVER + '\nsecond = MCPServer("second")\n',
            MCP2_SERVER.replace('mcp.run()', 'different.run()'),
            MCP2_SERVER.replace('mcp = MCPServer(', 'mcp = factory('),
        ]
        for source in variants:
            with self.subTest(source=source[:100]), self.assertRaises(RuntimeError):
                installer.server_patch(source)

    def test_public_registry_check_runs_patched_production_fixture(self):
        try:
            from mcp.server import MCPServer
        except ImportError:
            from mcp.server.fastmcp import FastMCP as MCPServer
            source = SERVER
            required = installer.TOOL_NAMES | {'existing_tool'}
        else:
            source = MCP2_SERVER
            required = installer.TOOL_NAMES | EXISTING_TOOLS
        patched, variable = installer.server_patch(source)
        patched = patched.replace("'/opt/jarvis-ops-network'", repr(str(ROOT / 'ops')))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'server.py'
            path.write_text(patched)
            result = subprocess.run([sys.executable, '-c', installer.registry_check_program(path, variable, required)],
                                    capture_output=True, text=True, timeout=25)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('registry_verified=' + str(len(required)), result.stdout)

    def test_existing_tools_preserved_and_registration_is_idempotent(self):
        first, variable = installer.server_patch(SERVER)
        second, _ = installer.server_patch(first)
        self.assertEqual(first, second)
        self.assertEqual(variable, "app")
        self.assertIn("def existing_tool", first)
        self.assertLess(first.index("_jarvis_network_register(app)"), first.index('if __name__'))

    def test_unknown_entrypoints_fail_closed(self):
        for text in ("print('unknown')", SERVER.replace('if __name__ == "__main__":\n    ', ''),
                     SERVER + "\nsecond = FastMCP('second')\n", SERVER + installer.BEGIN):
            with self.subTest(text=text[:40]), self.assertRaises(RuntimeError):
                installer.server_patch(text)

    def test_fixed_source_and_hash_identity(self):
        hashes = {name: "a" * 64 for name in installer.FILES}
        self.assertEqual(len(installer.package_hash(hashes)), 64)
        for commit, package in (("main", "0" * 64), ("f" * 40, "../file")):
            with self.assertRaises(RuntimeError):
                installer.fetch_release(commit, package)
        with self.assertRaises(RuntimeError):
            installer.NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.invalid")

    def test_conflicting_existing_tool_fails_closed(self):
        source = SERVER.replace("def existing_tool", "def network_snapshot")
        with self.assertRaisesRegex(RuntimeError, "tool_name_conflict"):
            installer.server_patch(source)

    def test_actor_is_the_actual_mcp_process(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            process = root / "12"
            process.mkdir()
            (process / "comm").write_text("python3\n")
            (process / "cmdline").write_bytes(b"python3\0" + str(installer.SERVER).encode() + b"\0")
            self.assertEqual(installer.service_actor(root), installer.pwd.getpwuid(os.getuid()).pw_name)
            (process / "cmdline").write_bytes(b"python3\0unrelated.py\0")
            with self.assertRaisesRegex(RuntimeError, "cannot_identify_running_ops_actor"):
                installer.service_actor(root)

    def test_release_hashes_and_current_main_are_verified(self):
        files = {name: b"# safe fixture\n" for name in installer.FILES}
        hashes = {name: installer.hashlib.sha256(data).hexdigest() for name, data in files.items()}
        expected = installer.package_hash(hashes)
        release = {"version": "test", "sha256": hashes, "package_sha256": expected}
        class Response:
            def __init__(self, obj): self.raw = json.dumps(obj).encode()
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, _): return self.raw
        def open_request(request, timeout):
            self.assertTrue(request.full_url.startswith("https://api.github.com/repos/jiaoyuyang/jarvis/"))
            if request.full_url.endswith("/commits/main"):
                return Response({"sha": "f" * 40})
            name = request.full_url.split("/ops/", 1)[1].split("?", 1)[0]
            content = json.dumps(release).encode() if name == "release.json" else files[name]
            return Response({"type": "file", "encoding": "base64", "content": base64.b64encode(content).decode()})
        opener = mock.Mock()
        opener.open.side_effect = open_request
        with mock.patch.object(installer.urllib.request, "build_opener", return_value=opener):
            payload, _ = installer.fetch_release("f" * 40, expected)
            self.assertEqual(payload, files)
            with self.assertRaisesRegex(RuntimeError, "release_is_not_current_main"):
                installer.fetch_release("e" * 40, expected)
            files["network_engine.py"] = b"# tampered\n"
            with self.assertRaisesRegex(RuntimeError, "release_file_hash_mismatch"):
                installer.fetch_release("f" * 40, expected)


class InstallerLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for constant, relative in (("STATE", "state"), ("PACKAGE", "package"), ("SERVER", "server.py"),
                                   ("VENV_PYTHON", "venv-python"), ("WRAPPER", "wrapper"), ("SUDOERS", "sudoers")):
            patch = mock.patch.object(installer, constant, self.root / relative)
            patch.start()
            self.addCleanup(patch.stop)
        installer.SERVER.write_text(SERVER)
        installer.VENV_PYTHON.write_text("fixture")
        self.calls = []
        self.actors = []
        self.timers = set()
        def run(argv, timeout=20, actor=None):
            self.calls.append(argv)
            self.actors.append((argv, actor))
            if argv[0] == "systemd-run":
                self.timers.add(next(s.split("=", 1)[1] for s in argv if s.startswith("--unit=")) + ".timer")
            if argv[:2] == ["systemctl", "is-active"]: return "active"
            return ""
        self.run = mock.patch.object(installer, "run", side_effect=run)
        self.run.start(); self.addCleanup(self.run.stop)
        real_require = installer.require
        def fixture_require(ok, code):
            # Only bypass root ownership prerequisites in this unprivileged fixture.
            if code not in ("run_installer_as_root", "unsafe_install_state", "unsafe_package_directory"):
                real_require(ok, code)
        patch = mock.patch.object(installer, "require", side_effect=fixture_require)
        patch.start(); self.addCleanup(patch.stop)
        patch = mock.patch.object(installer.os, "fchown")
        patch.start(); self.addCleanup(patch.stop)
        patch = mock.patch.object(installer, "service_actor", return_value="jarvis-ops")
        patch.start(); self.addCleanup(patch.stop)
        payload = {name: (ROOT / "ops" / name).read_bytes() for name in installer.FILES}
        patch = mock.patch.object(installer, "fetch_release", return_value=(payload, {"version": "test"}))
        patch.start(); self.addCleanup(patch.stop)

    def install(self):
        return installer.install("f" * 40, "a" * 64)

    def test_install_preserves_server_tools_and_verifies_as_service_account(self):
        result = self.install()
        self.assertEqual(result["phase"], "pending_local_verification")
        self.assertIn("def existing_tool", installer.SERVER.read_text())
        self.assertIn("_jarvis_network_register(app)", installer.SERVER.read_text())
        checks = [c for c in self.calls if "-c" in c and "ops_check" in c[-1]]
        self.assertEqual(next(actor for argv, actor in self.actors if argv == checks[0]), 'jarvis-ops')
        self.assertIn("python3 -I", installer.WRAPPER.read_text())
        self.assertIn('--broker "$@"', installer.WRAPPER.read_text())

    def test_install_guard_is_armed_before_source_is_replaced(self):
        original = installer.atomic
        def guarded(path, content, *args, **kwargs):
            if path == installer.SERVER:
                self.assertTrue(any("install-guard" in name for name in self.timers))
            return original(path, content, *args, **kwargs)
        with mock.patch.object(installer, "atomic", side_effect=guarded):
            self.install()

    def test_failed_verification_restores_original_source_and_removes_new_files(self):
        result = self.install()
        failed = mock.Mock(returncode=1, stdout="{}")
        with mock.patch.object(installer.subprocess, "run", return_value=failed):
            with self.assertRaisesRegex(RuntimeError, "privileged_bridge_check_failed"):
                installer.verify(result["installation_id"])
        self.assertEqual(installer.SERVER.read_text(), SERVER)
        self.assertFalse(installer.WRAPPER.exists())
        _, record = installer.record(result["installation_id"])
        self.assertEqual(record["phase"], "rolled_back")

    def test_verified_install_commits_and_guard_becomes_noop(self):
        result = self.install()
        ok = mock.Mock(returncode=0, stdout=json.dumps({'ok': True, 'result': {'host_read_context': {
            'cap_net_admin': True, 'no_new_privileges': True, 'cgroup': '/system.slice/jarvis-ops-task-fixture.service'}}}))
        with mock.patch.object(installer.subprocess, "run", return_value=ok) as run:
            installer.verify(result["installation_id"])
        argv = run.call_args.args[0]
        self.assertIn('--property=User=jarvis-ops', argv)
        self.assertIn('--property=NoNewPrivileges=no', argv)
        self.assertEqual(argv[-4:], ["sudo", "-n", str(installer.WRAPPER), "snapshot"])
        _, record = installer.record(result["installation_id"])
        self.assertEqual(record["phase"], "committed")
        current = installer.SERVER.read_text()
        installer.restore(result["installation_id"])
        self.assertEqual(installer.SERVER.read_text(), current)

    def test_second_pending_install_cannot_replace_first(self):
        self.install()
        with self.assertRaisesRegex(RuntimeError, "another_install_pending"):
            self.install()

    def test_same_release_resume_restores_baseline_before_fresh_install(self):
        first = self.install()
        payload = {name: (ROOT / 'ops' / name).read_bytes() for name in installer.FILES}
        # The real installer requires root-owned staged files; this unit fixture
        # bypasses only that ownership bit and still checks byte identity/drift.
        real_require = installer.require
        def fixture_require(ok, code):
            if code == 'staged_release_drift_no_resume':
                for name, content in payload.items():
                    self.assertEqual((installer.PACKAGE / name).read_bytes(), content)
                return
            real_require(ok, code)
        with mock.patch.object(installer, 'require', side_effect=fixture_require):
            second = installer.install('f' * 40, 'a' * 64, upgrade=True)
        self.assertNotEqual(first['installation_id'], second['installation_id'])
        self.assertEqual(installer.record(first['installation_id'])[1]['phase'], 'rolled_back')
        saved = installer.STATE / second['installation_id'] / 'installer.py'
        self.assertEqual(saved.read_bytes(), installer.RUNNING_SOURCE)

    def test_different_release_cannot_resume_a_pending_install(self):
        self.install()
        with self.assertRaisesRegex(RuntimeError, 'pending_install_does_not_match'):
            installer.install('e' * 40, 'a' * 64, upgrade=True)


class BrokerTests(unittest.TestCase):
    def test_normal_worker_is_separate_and_has_bounded_permissions(self):
        argv = engine.worker_command('jarvis-ops-task-fixture')
        self.assertNotIn('--scope', argv)
        self.assertIn('--pipe', argv)
        self.assertIn('--wait', argv)
        self.assertIn('--property=ProtectSystem=strict', argv)
        self.assertIn('--property=NoNewPrivileges=yes', argv)
        self.assertIn('--property=AmbientCapabilities=CAP_CHOWN CAP_DAC_OVERRIDE CAP_FOWNER CAP_NET_ADMIN', argv)
        self.assertIn('--property=RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK', argv)
        paths = next(x for x in argv if x.startswith('--property=ReadWritePaths='))
        self.assertNotIn('/usr', paths)
        self.assertNotIn('/home', paths)

    def test_worker_error_remains_structured_without_raw_systemd_stderr(self):
        response = mock.Mock(returncode=1, stdout='{"ok":false,"error":"fixed_error"}', stderr='SECRET_SENTINEL')
        with mock.patch.object(engine.os, 'geteuid', return_value=0), mock.patch.object(engine.subprocess, 'run', return_value=response) as run:
            result = engine.broker(['snapshot'], b'{}')
        self.assertEqual(result, {'ok': False, 'error': 'fixed_error'})
        self.assertEqual(run.call_args.args[0][-2:], ['--worker', 'snapshot'])

    def test_unknown_commands_paths_and_bad_digests_never_launch(self):
        with mock.patch.object(engine.os, 'geteuid', return_value=0), mock.patch.object(engine.subprocess, 'run') as run:
            for args, raw in ((['bash'], b'{}'), (['snapshot', '/etc/passwd'], b'{}'),
                              (['--worker', 'snapshot'], b'{}'), (['upgrade'], b'{"commit_sha":"main","package_sha256":"bad"}')):
                with self.subTest(args=args), self.assertRaises(engine.OperationError):
                    engine.broker(args, raw)
        run.assert_not_called()

    def test_upgrade_returns_queue_receipt_and_does_not_wait_for_its_own_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            real_require = engine.require
            def fixture_require(ok, code):
                if code != 'unsafe_state_directory': real_require(ok, code)
            with mock.patch.object(engine, 'STATE', Path(directory)), mock.patch.object(engine, 'require', side_effect=fixture_require), \
                    mock.patch.object(engine.os, 'geteuid', return_value=0), mock.patch.object(engine, 'command', return_value='') as command:
                result = engine.broker(['upgrade'], json.dumps({'commit_sha': 'f'*40, 'package_sha256': 'a'*64}).encode())
            self.assertEqual(result['phase'], 'queued')
            self.assertTrue(result['independent_service'])
            argv = command.call_args.args[0]
            self.assertNotIn('--pipe', argv)
            self.assertNotIn('--wait', argv)
            self.assertIn('--request-id', argv)
            self.assertTrue((Path(directory)/('upgrade-'+result['upgrade_id']+'.json')).is_file())


class ExtensionTests(unittest.TestCase):
    def test_registration_and_schema(self):
        class FakeMCP:
            def __init__(self): self.tools = {}
            def tool(self, **kwargs):
                def register(fn): self.tools[fn.__name__] = fn; return fn
                return register
        app = FakeMCP()
        extension.register_tools(app)
        self.assertEqual(len(app.tools), 11)
        self.assertIn("upgrade_ops_extension", app.tools)
        with mock.patch.object(extension, "invoke", return_value={"ok": True}) as invoke:
            app.tools["probe_proxy_nodes"](provider="primary", limit=2)
        self.assertEqual(invoke.call_args.kwargs["limit"], 2)

    def test_helper_has_no_shell_or_inherited_custom_command(self):
        fake = mock.Mock(returncode=0, stdout='{"ok":true,"result":{}}')
        with mock.patch.object(extension.os, "geteuid", return_value=1000), \
                mock.patch.object(extension.subprocess, "run", return_value=fake) as run:
            self.assertTrue(extension.invoke("snapshot")["ok"])
        self.assertEqual(run.call_args.args[0], ["sudo", "-n", extension.HELPER, "snapshot"])
        self.assertNotIn("shell", run.call_args.kwargs)

    def test_redaction(self):
        text = 'Bearer abc123 https://example.invalid/sub/secret token="VALUE" password=PASS sk-abcdefgh eyJabc.def.ghi'
        result = engine.redact(text)
        for value in ("abc123", "example.invalid", "VALUE", "PASS ", "sk-abcdef", "eyJabc"):
            self.assertNotIn(value, result)

    def test_real_sdk_preserves_existing_tools_and_builds_typed_schemas(self):
        try:
            from mcp.server import MCPServer
        except ImportError:
            from mcp.server.fastmcp import FastMCP as MCPServer
        app = MCPServer("Ops test")
        @app.tool()
        def original_tool() -> dict:
            return {"ok": True}
        extension.register_tools(app)
        tools = {t.name: t for t in asyncio.run(app.list_tools())}
        self.assertIn("original_tool", tools)
        self.assertEqual(len(tools), 12)
        self.assertEqual(set(tools), installer.TOOL_NAMES | {"original_tool"})
        tool = tools["probe_proxy_nodes"]
        schema = tool.input_schema if hasattr(tool, 'input_schema') else tool.inputSchema
        self.assertEqual(schema["properties"]["provider"]["enum"], ["primary", "backup"])
        for name in installer.TOOL_NAMES:
            self.assertIsNotNone(tools[name].annotations)
        def hint(name, snake, camel):
            values = tools[name].annotations.model_dump()
            return values[snake] if snake in values else values[camel]
        self.assertTrue(hint('network_snapshot', 'read_only_hint', 'readOnlyHint'))
        self.assertFalse(hint('network_snapshot', 'open_world_hint', 'openWorldHint'))
        self.assertTrue(hint('apply_network_change', 'destructive_hint', 'destructiveHint'))
        self.assertFalse(hint('apply_network_change', 'read_only_hint', 'readOnlyHint'))
        self.assertTrue(hint('upgrade_ops_extension', 'destructive_hint', 'destructiveHint'))
        with mock.patch.object(extension, 'invoke', return_value={'ok': True, 'result': {'version': 'fixture'}}):
            result = asyncio.run(app.call_tool('ops_extension_status', {}))
        self.assertIn('fixture', str(result))


if __name__ == "__main__":
    unittest.main()
