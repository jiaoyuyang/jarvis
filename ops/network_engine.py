#!/usr/bin/env python3
"""Privileged, bounded network operations. Never accepts shell or YAML input."""
from __future__ import annotations

import base64
import concurrent.futures
import copy
import fcntl
import hashlib
import ipaddress
import json
import os
import pwd
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

import yaml

VERSION = "1.4.1"
CONFIG = Path("/etc/mihomo/config.yaml")
WIREGUARD = Path("/etc/wireguard/wg0.conf")
STATE = Path("/var/lib/jarvis-ops-network")
HELPER = "/usr/local/sbin/jarvis-network-ops"
INSTALLER = "/opt/jarvis-ops-network/install.py"
ENGINE = "/opt/jarvis-ops-network/network_engine.py"
SYSTEMD_RUN = "/usr/bin/systemd-run"
WORKER_PATHS = (str(STATE), "/etc/mihomo", "/etc/wireguard")
UPGRADE_PATHS = (str(STATE), "/var/lib/jarvis-ops-network-install", "/opt/jarvis-ops-network",
                 "/opt/jarvis-ops-mcp", "/usr/local/sbin", "/etc/sudoers.d")
PROVIDERS = {"primary": "梯子猫订阅", "backup": "一元机场"}
SELECTORS = {"primary": "梯子猫", "backup": "一元日本01专线"}
FALLBACKS = ("OpenAI专用", "主备自动切换")
TESTS = (("https://www.gstatic.com/generate_204", 204),
         ("https://api.openai.com/v1/models", 401))
ID = re.compile(r"[0-9a-f]{32}")
PRIMARY_DNS_RESOLVERS = ("https://custom.tizimao.net/inst", "https://custom-us.ladder-cat.com/inst")
CLIENT_REPAIR_PATCH_SHA256 = 'ca7b3cebc56d5815ccc49f08e9dce9d3caf4e76a8259402fa2c2cf207864ac6a'
PRIMARY_NODE_DOMAINS = {"cn.bing.com", "tw1.apple.com", "tw2.apple.com"}


class OperationError(Exception):
    """Only fixed public error codes may cross the MCP boundary."""


class HostCommandError(OperationError):
    def __init__(self, program, returncode, stderr=""):
        super().__init__("host_command_failed")
        self.details = {"error": "host_command_failed", "program": Path(program).name,
                        "exit_code": returncode}
        # Classify only known messages; never return raw command output/arguments.
        if "operation not permitted" in stderr.lower() or "permission denied" in stderr.lower():
            self.details["reason"] = "permission_denied"
        elif 'read-only file system' in stderr.lower():
            self.details['reason'] = 'read_only_filesystem'
        elif 'unsupported_adapter_source_no_changes' in stderr or 'unsupported_client_source_no_changes' in stderr:
            self.details['reason'] = 'patch_source_mismatch'
        elif "no such device" in stderr.lower() or "does not exist" in stderr.lower():
            self.details["reason"] = "interface_not_found"


def require(ok, code):
    if not ok:
        raise OperationError(code)


def bounded_int(value, lower, upper):
    require(type(value) is int and lower <= value <= upper, "invalid_integer")
    return value


def digest(data):
    return hashlib.sha256(data).hexdigest()


def atomic(path, data, mode=0o600, uid=None, gid=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + path.name, dir=path.parent)
    try:
        os.fchmod(fd, mode)
        if uid is not None:
            os.fchown(fd, uid, gid)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
        directory = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def command(argv, timeout=15):
    try:
        result = subprocess.run(argv, capture_output=True, timeout=timeout, text=True)
    except FileNotFoundError:
        raise HostCommandError(argv[0], 127) from None
    except subprocess.TimeoutExpired:
        raise HostCommandError(argv[0], 124) from None
    if result.returncode:
        raise HostCommandError(argv[0], result.returncode, result.stderr)
    return result.stdout.strip()


def worker_command(unit, upgrade=False):
    """A service (never a scope) is spawned by PID 1, outside the tunnel sandbox."""
    paths = UPGRADE_PATHS if upgrade else WORKER_PATHS
    caps = "CAP_CHOWN CAP_DAC_OVERRIDE CAP_FOWNER CAP_NET_ADMIN"
    properties = ("Type=exec", "User=root", "UMask=0077", "NoNewPrivileges=yes",
                  "ProtectSystem=strict", "ProtectHome=read-only", "PrivateTmp=yes",
                  "RestrictSUIDSGID=yes", "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK",
                  "CapabilityBoundingSet=" + caps, "AmbientCapabilities=" + caps,
                  "ReadWritePaths=" + " ".join(paths),
                  "RuntimeMaxSec=" + ("160s" if upgrade else "115s"))
    argv = [SYSTEMD_RUN, "--quiet", "--collect", "--unit=" + unit]
    argv += ["--property=" + value for value in properties]
    if not upgrade:
        argv += ["--pipe", "--wait"]
    return argv


def broker(args, raw):
    require(os.geteuid() == 0, "root_helper_required")
    require(1 <= len(args) <= 2 and len(raw) <= 65536, "invalid_command")
    action = args[0]
    allowed = {"snapshot", "probe", "prepare", "apply", "confirm", "rollback", "status", "logs",
               "model-smoke", "runtime-diagnostics", "harness-smoke", "extension-status", "upgrade", "rollback-auto", "client-repair", "client-repair-rollback"}
    require(action in allowed, "unsupported_operation")
    data = json.loads(raw or b"{}")
    require(isinstance(data, dict), "invalid_request")
    if action in ("rollback-auto", "client-repair-rollback"):
        require(len(args) == 2 and ID.fullmatch(args[1]) and not data, "invalid_command")
    else:
        require(len(args) == 1, "invalid_command")
    identity = uuid.uuid4().hex
    if action == "upgrade":
        require(set(data) == {"commit_sha", "package_sha256"}, "invalid_upgrade_request")
        require(isinstance(data["commit_sha"], str) and re.fullmatch(r"[0-9a-f]{40}", data["commit_sha"])
                and isinstance(data["package_sha256"], str) and re.fullmatch(r"[0-9a-f]{64}", data["package_sha256"]),
                "invalid_upgrade_digest")
        STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
        require(not STATE.is_symlink() and STATE.stat().st_uid == 0, "unsafe_state_directory")
        os.chmod(STATE, 0o700)
        record = {"id": identity, "kind": "upgrade", "phase": "queued", "created_at": time.time(), **data}
        path = STATE / ("upgrade-" + identity + ".json")
        atomic(path, json.dumps(record).encode())
        argv = worker_command("jarvis-ops-upgrade-" + identity, upgrade=True)
        argv += ["/usr/bin/python3", "-I", INSTALLER, "--commit", data["commit_sha"],
                 "--package-sha256", data["package_sha256"], "--upgrade", "--request-id", identity]
        try:
            command(argv, timeout=15)
        except Exception:
            record["phase"] = "launch_failed"
            atomic(path, json.dumps(record).encode())
            raise
        return {"upgrade_id": identity, "phase": "queued", "independent_service": True,
                "check": "ops_extension_status; queued is not installed or committed"}
    argv = worker_command("jarvis-ops-task-" + identity)
    argv += ["/usr/bin/python3", "-I", ENGINE, "--worker", *args]
    result = subprocess.run(argv, input=raw.decode(), capture_output=True, text=True, timeout=120)
    try:
        response = json.loads(result.stdout)
    except ValueError:
        raise OperationError("independent_worker_unavailable") from None
    require(isinstance(response, dict) and isinstance(response.get("ok"), bool), "invalid_worker_result")
    # Preserve the worker's fixed error, not systemd's raw stderr or argv.
    return response


def yaml_node(node, path):
    for key in path:
        if isinstance(key, int):
            node = node.value[key]
        else:
            matches = [v for k, v in node.value if k.value == key]
            require(len(matches) == 1, "ambiguous_yaml_key")
            node = matches[0]
    return node


def yaml_patch(text, changes):
    """Preserve comments/credentials byte-for-byte outside declared values."""
    root = yaml.compose(text)
    expected = copy.deepcopy(yaml.safe_load(text))
    spans = []
    for path, value in changes:
        node = yaml_node(root, path)
        replacement = json.dumps(value, ensure_ascii=False)
        # Block collection end marks consume the newline/indent before the next
        # mapping key. Reintroduce that separator when replacing with flow JSON.
        if (isinstance(node, (yaml.MappingNode, yaml.SequenceNode)) and not node.flow_style
                and node.end_mark.line > node.start_mark.line
                and node.end_mark.index < len(text) and text[node.end_mark.index] not in '\r\n'):
            replacement += '\n' + ' ' * node.end_mark.column
        spans.append((node.start_mark.index, node.end_mark.index,
                      replacement))
        target = expected
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
    for start, end, value in sorted(spans, reverse=True):
        text = text[:start] + value + text[end:]
    require(yaml.safe_load(text) == expected, "yaml_patch_scope_mismatch")
    return text


def yaml_mapping_update(text, path, values):
    """Update declared fields; insert missing fields without replacing the block."""
    before = yaml.safe_load(text)
    target = before
    for key in path:
        target = target[key]
    require(isinstance(target, dict), "unsupported_yaml_mapping")
    expected = copy.deepcopy(before)
    wanted = expected
    for key in path:
        wanted = wanted[key]
    wanted.update(values)
    existing = [(path + (key,), value) for key, value in values.items() if key in target]
    if existing:
        text = yaml_patch(text, existing)
    missing = {key: value for key, value in values.items() if key not in target}
    if missing:
        node = yaml_node(yaml.compose(text), path)
        if node.flow_style or not node.value:
            text = yaml_patch(text, [(path, wanted)])
        else:
            indent = " " * node.start_mark.column
            addition = "".join(json.dumps(key) + ": " + json.dumps(value, ensure_ascii=False) + "\n" + indent
                               for key, value in missing.items())
            i = node.start_mark.index
            text = text[:i] + addition + text[i:]
    require(yaml.safe_load(text) == expected, "yaml_patch_scope_mismatch")
    return text


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise OperationError("unexpected_redirect")


def dns_candidate(text, updates):
    before = yaml.safe_load(text)
    expected = copy.deepcopy(before)
    new_dns = copy.deepcopy(before['dns'])
    new_dns.update(updates)
    expected['dns'] = new_dns  # Break aliases so other top-level values cannot change.
    try:
        candidate = yaml_mapping_update(text, ('dns',), updates)
        if yaml.safe_load(candidate) == expected:
            return candidate, False
    except yaml.YAMLError:
        pass
    except OperationError as error:
        if str(error) != 'yaml_patch_scope_mismatch':
            raise
    candidate = yaml.safe_dump(expected, allow_unicode=True, sort_keys=False)
    require(yaml.safe_load(candidate) == expected, 'normalized_yaml_scope_mismatch')
    return candidate, True


def scoped_candidate(text, changes):
    """Fall back to normalization only after checking exact declared semantics."""
    expected = copy.deepcopy(yaml.safe_load(text))
    for path, value in changes:
        target = expected
        for key in path[:-1]:
            target[key] = copy.deepcopy(target[key])
            target = target[key]
        target[path[-1]] = copy.deepcopy(value)
    try:
        candidate = yaml_patch(text, changes)
        if yaml.safe_load(candidate) == expected:
            return candidate, False
    except yaml.YAMLError:
        pass
    except OperationError as error:
        if str(error) != 'yaml_patch_scope_mismatch':
            raise
    candidate = yaml.safe_dump(expected, allow_unicode=True, sort_keys=False)
    require(yaml.safe_load(candidate) == expected, 'normalized_yaml_scope_mismatch')
    return candidate, True


CODEX_DIAGNOSTIC_SCRIPT = r'''
import ast
import asyncio
import hashlib
import importlib.util
import json
import tomllib
from pathlib import Path

root = Path('/app/working/workspaces/default')
out = {'scope': 'Fixed runtime metadata and failed records only; no normal chat, credentials or process arguments.'}
config = json.loads((root / 'agent.json').read_text())
settings = config.get('backend_settings') or {}
out['settings'] = {k: settings.get(k) for k in ('model', 'reasoning_effort', 'sandbox', 'approval_policy', 'turn_timeout_seconds', 'final_only')}
out['sources'] = {}
for name in ('adapter', 'app_server', 'projection'):
    spec = importlib.util.find_spec('qwenpaw.harnesses.codex.' + name)
    raw = Path(spec.origin).read_bytes()
    text = raw.decode()
    tree = ast.parse(text)
    methods = {node.name: ast.get_source_segment(text, node) or '' for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    entry = {'sha256': hashlib.sha256(raw).hexdigest()}
    if name == 'app_server':
        start = methods.get('start', '')
        entry.update(reader_health_checked_on_start='_reader_task' in start.split('resolution =')[0],
                     explicit_stream_limit='limit=' in start or 'limit =' in start,
                     python_default_stream_limit=asyncio.streams._DEFAULT_LIMIT,
                     bounded_initialization='wait_for' in start,
                     stderr_details_discarded='Codex app-server emitted stderr output' in methods.get('_read_stderr', ''))
    if name == 'adapter':
        entry['timeout_patch_v2'] = 'JARVIS_CODEX_TURN_TIMEOUT_PATCH_V2' in text
        entry['prepare_calls'] = [n.args[0].value for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == 'request' and n.args and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str) and n.args[0].value in ('skills/extraRoots/set', 'thread/start', 'thread/resume')]
    out['sources'][name] = entry
native = Path('/root/.codex/config.toml')
if native.is_file() and native.stat().st_size <= 1048576:
    try:
        data = tomllib.loads(native.read_text())
        out['native_config'] = {k: data.get(k) for k in ('model', 'model_provider', 'approval_policy', 'sandbox_mode')}
        out['native_config']['mcp_servers'] = [{'name': name, 'enabled': value.get('enabled', True),
            'transport': 'stdio' if value.get('command') else 'http'} for name, value in (data.get('mcp_servers') or {}).items() if isinstance(value, dict)]
    except (ValueError, OSError):
        out['native_config'] = {'error': 'unavailable'}
out['processes'] = []
for folder in list(Path('/proc').iterdir())[:2048]:
    if not folder.name.isdigit():
        continue
    try:
        args = (folder / 'cmdline').read_bytes().split(b'\0')
        is_codex = any(Path(a.decode(errors='replace')).name == 'codex' for a in args[:2])
        is_qwen = any(b'qwenpaw' in a for a in args[:3])
        if not (is_codex or is_qwen):
            continue
        fields = dict(line.split(':', 1) for line in (folder / 'status').read_text().splitlines() if ':' in line)
        out['processes'].append({'pid': int(folder.name), 'kind': 'app-server' if b'app-server' in args else 'codex-exec' if is_codex else 'qwenpaw',
            'ppid': fields.get('PPid', '').strip(), 'uid': fields.get('Uid', '').strip(),
            'state': fields.get('State', '').strip(), 'rss': fields.get('VmRSS', '').strip()})
    except (OSError, ValueError):
        continue
out['thread_maps'] = []
for path in list(root.rglob('codex_sessions.json'))[:4]:
    if path.is_file() and path.stat().st_size <= 1048576:
        value = json.loads(path.read_text())
        out['thread_maps'].append({'entries': len(value) if isinstance(value, dict) else None, 'bytes': path.stat().st_size})
out['failed_records'] = []
session_root = root / 'sessions/dingtalk'
paths = sorted(session_root.glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True)[:8]
for path in paths:
    if path.stat().st_size > 4194304:
        continue
    data = json.loads(path.read_text())
    context = (((data.get('agent') or {}).get('state') or {}).get('context') or [])
    for message in context[-100:]:
        if not isinstance(message, dict) or (message.get('metadata') or {}).get('status') != 'failed':
            continue
        content = message.get('content') or []
        blocks = [{'text': content}] if isinstance(content, str) else content
        texts = [str(b.get('text') or '') for b in blocks if isinstance(b, dict) and b.get('type', 'text') == 'text']
        out['failed_records'].append({'session_hash': hashlib.sha256(path.name.encode()).hexdigest()[:16],
            'timestamp': message.get('timestamp'), 'error': '\n'.join(texts)[-2000:]})
out['failed_records'] = out['failed_records'][-8:]
print(json.dumps(out, ensure_ascii=False))
'''

CODEX_HARNESS_SCRIPT = r'''
import asyncio
import json
import tempfile
import time
from pathlib import Path
from qwenpaw.harnesses.codex import adapter as module
from qwenpaw.harnesses.codex.app_server import CodexAppServerClient

records = []
clients = []
origin = time.monotonic()

class TracedClient(CodexAppServerClient):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        clients.append(self)
    async def request(self, method, params=None):
        entry = {'method': method, 'started': round(time.monotonic() - origin, 2)}
        records.append(entry)
        try:
            value = await super().request(method, params)
            entry['status'] = 'ok'
            return value
        except BaseException as error:
            entry['status'] = type(error).__name__
            raise
        finally:
            entry['seconds'] = round(time.monotonic() - origin - entry['started'], 2)

module.CodexAppServerClient = TracedClient

async def main():
    results = []
    with tempfile.TemporaryDirectory(prefix='jarvis-ops-harness-') as state:
        adapter = module.CodexAdapter(Path(state))
        settings = {'model': 'gpt-6.1-sol', 'reasoning_effort': 'high', 'sandbox': 'read-only',
                    'approval_policy': 'never', 'final_only': True, 'turn_timeout_seconds': 35}
        try:
            for stage in ('new_thread', 'resume_after_restart'):
                start = time.monotonic()
                answer = []
                complete = False
                failure = None
                try:
                    async def consume():
                        nonlocal complete
                        async for event in adapter.run_turn(session_id='jarvis-ops-isolated-smoke',
                                prompt='只回复：模型连通测试通过。不要调用工具、读取文件或执行命令。',
                                cwd=Path('/app/working/workspaces/default'), settings=settings):
                            kind = str(getattr(event.kind, 'value', event.kind))
                            if 'text_delta' in kind.lower():
                                answer.append(str(getattr(event, 'text', '') or ''))
                            if kind.lower() == 'completed':
                                complete = True
                            if kind.lower() in ('error', 'cancelled'):
                                raise RuntimeError(str(getattr(event, 'text', '') or kind))
                    await asyncio.wait_for(consume(), timeout=38)
                except Exception as error:
                    failure = type(error).__name__ + ': ' + str(error)[:1000]
                actual = ''.join(answer).strip()
                ok = complete and actual.rstrip('。.!！') == '模型连通测试通过'
                results.append({'stage': stage, 'ok': ok, 'completed': complete,
                    'seconds': round(time.monotonic() - start, 2), 'answer': actual if ok else '', 'error': failure})
                try:
                    await asyncio.wait_for(adapter.stop(), timeout=6)
                except Exception as error:
                    results[-1]['cleanup_error'] = type(error).__name__
                    break
                if not ok:
                    break
        finally:
            for client in clients:
                process = client._process
                try:
                    await asyncio.wait_for(client.stop(), timeout=2)
                except Exception:
                    if process is not None and process.returncode is None:
                        process.kill()
    print(json.dumps({'ok': len(results) == 2 and all(r['ok'] for r in results),
        'tests': results, 'rpc': records[-30:], 'scope': 'Independent temporary adapter and thread; production process/session untouched; no projected tools; read-only sandbox and approvals never.'}, ensure_ascii=False))

asyncio.run(main())
'''


class Controller:
    def __init__(self, config):
        require(config.get("external-controller") in (
            "127.0.0.1:9090", "localhost:9090", "0.0.0.0:9090"),
            "unsupported_controller")
        self.secret = str(config.get("secret") or "")
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), NoRedirect())

    def call(self, path, method="GET", data=None, timeout=10):
        headers = {"Content-Type": "application/json"}
        if self.secret:
            headers["Authorization"] = "Bearer " + self.secret
        request = urllib.request.Request("http://127.0.0.1:9090" + path,
            data=None if data is None else json.dumps(data).encode(),
            headers=headers, method=method)
        with self.opener.open(request, timeout=timeout) as response:
            body = response.read(2_097_153)
        require(len(body) <= 2_097_152, "controller_response_too_large")
        return json.loads(body) if body else {}


class Engine:
    def __init__(self, config=CONFIG, wg=WIREGUARD, state=STATE,
                 controller=None, runner=command, clock=time.time):
        self.config_path, self.wg_path, self.state = map(Path, (config, wg, state))
        self.runner, self.clock = runner, clock
        self.config = yaml.safe_load(self.config_path.read_text())
        self.controller = controller or Controller(self.config)
        self.state.mkdir(mode=0o700, parents=True, exist_ok=True)
        require(not self.state.is_symlink(), "unsafe_state_directory")
        if os.geteuid() == 0:
            require(self.state.stat().st_uid == 0, "unsafe_state_owner")
        os.chmod(self.state, 0o700)

    def api(self, path, **kwargs):
        return self.controller.call(path, **kwargs)

    def group(self, name):
        return self.api("/proxies/" + urllib.parse.quote(name, safe=""))

    def save(self, item):
        atomic(self.state / (item["id"] + ".json"),
               json.dumps(item, ensure_ascii=False).encode())

    def load(self, identity, kind=None):
        require(isinstance(identity, str) and ID.fullmatch(identity), "invalid_id")
        path = self.state / (identity + ".json")
        require(path.is_file() and not path.is_symlink(), "unknown_id")
        item = json.loads(path.read_text())
        require(item.get("id") == identity and (kind is None or item.get("kind") == kind),
                "invalid_record")
        return item

    def active_change(self):
        active = []
        for path in self.state.glob("*.json"):
            item = json.loads(path.read_text())
            if item.get("phase") == "pending":
                active.append(item["id"])
        return active

    def selected(self):
        result = {}
        for role, name in SELECTORS.items():
            group = self.group(name)
            require(group.get("type") == "Selector", "unsupported_selector")
            result[role] = group.get("now")
        return result

    def set_selected(self, selected):
        for role in ("backup", "primary"):
            name, node = SELECTORS[role], selected[role]
            require(node in self.group(name).get("all", []), "node_not_in_selector")
            self.api("/proxies/" + urllib.parse.quote(name, safe=""),
                     method="PUT", data={"name": node})
        require(self.selected() == selected, "selection_not_applied")

    def provider_inventory(self, role):
        """Read only the configured cache beneath the fixed Mihomo directory."""
        config = self.config.get("proxy-providers", {}).get(PROVIDERS[role], {})
        url = str(config.get("url") or "")
        result = {"provider": PROVIDERS[role], "source_host": urllib.parse.urlsplit(url).hostname,
                  "source_sha256": digest(url.encode()) if url else None, "nodes": []}
        name = config.get("path")
        if not isinstance(name, str) or not name:
            result["cache_error"] = "cache_path_not_configured"
            return result
        root = self.config_path.parent.resolve()
        path = Path(name) if Path(name).is_absolute() else root / name
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            result["cache_error"] = "cache_path_outside_mihomo"
            return result
        try:
            with path.open("rb") as stream:
                raw = stream.read(1_048_577)
            require(len(raw) <= 1_048_576, "provider_cache_too_large")
            data = yaml.safe_load(raw) or {}
            require(isinstance(data, dict) and isinstance(data.get("proxies", []), list), "invalid_provider_cache")
            for node in data.get("proxies", []):
                if not isinstance(node, dict):
                    continue
                host = str(node.get("server") or "")
                sni = str(node.get("sni") or node.get("servername") or host)
                # Never expose credentials, UUIDs, URL paths, or arbitrary YAML fields.
                if not re.fullmatch(r"[A-Za-z0-9.:-]{1,253}", host) or not re.fullmatch(r"[A-Za-z0-9.:-]{1,253}", sni):
                    continue
                port = node.get("port")
                if type(port) is not int or not 1 <= port <= 65535:
                    continue
                protocol = str(node.get("type") or "")
                if not re.fullmatch(r"[a-z0-9-]{1,32}", protocol):
                    continue
                result["nodes"].append({"name": redact(node.get("name", "")), "type": protocol,
                    "server": host, "port": port, "sni": sni,
                    "skip_cert_verify": node.get("skip-cert-verify", False)})
        except (OSError, ValueError, yaml.YAMLError, OperationError):
            result["cache_error"] = "provider_cache_unavailable"
        return result

    def host_read_diagnostics(self):
        result = {"effective_uid": os.geteuid()}
        try:
            status = dict(line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines() if ":" in line)
            caps = int(status["CapEff"].strip(), 16)
            result["cap_net_admin"] = bool(caps & (1 << 12))
            result["no_new_privileges"] = status.get("NoNewPrivs", "0").strip() == "1"
            result["network_namespace"] = os.readlink("/proc/self/ns/net")
            result["cgroup"] = Path("/proc/self/cgroup").read_text().splitlines()[-1].split(":", 2)[-1]
        except (OSError, KeyError, ValueError):
            result["capabilities_error"] = "unavailable"
        return result

    def management_identity(self):
        # Fixed service/account only. Never read argv, environment, or credentials.
        raw = self.runner(['systemctl', 'show', 'jarvis-ops-tunnel.service',
                           '--property=User', '--property=MainPID'])
        fields = dict(line.split('=', 1) for line in raw.splitlines() if '=' in line)
        require(fields.get('User') == 'jarvisops', 'unsupported_management_account')
        pid = fields.get('MainPID', '')
        require(pid.isdecimal() and int(pid) > 0, 'management_process_unavailable')
        uid = pwd.getpwnam('jarvisops').pw_uid
        require(uid > 0, 'unsupported_management_uid')
        status = Path('/proc/' + pid + '/status').read_text()
        match = re.search(r'(?m)^Uid:\s+(\d+)\s+(\d+)', status)
        require(match and int(match[1]) == uid and int(match[2]) == uid, 'management_uid_mismatch')
        return {'user': 'jarvisops', 'uid': uid}

    def snapshot(self):
        groups = {}
        for name in (*SELECTORS.values(), *FALLBACKS):
            group = self.group(name)
            groups[name] = {key: group.get(key) for key in ("type", "now", "all", "alive")}
        dns = self.config.get("dns", {})
        def public_urls(values):
            result = []
            if isinstance(values, str):
                values = [values]
            for value in values or []:
                value = str(value)
                if "://" in value:
                    parsed = urllib.parse.urlsplit(value)
                    value = parsed.scheme + "://" + (parsed.hostname or "") + parsed.path
                result.append(value)
            return result
        wg, wg_error = "", None
        try:
            wg = self.runner(["wg", "show", "wg0", "allowed-ips"])
        except HostCommandError as error:
            wg_error = error.details
        routes = {}
        for address in ("202.89.233.100", "202.89.233.101", "59.125.68.205"):
            try:
                raw = json.loads(self.runner(["ip", "-4", "-j", "route", "get", address]))
                routes[address] = {key: raw[0].get(key) for key in ("dev", "gateway", "prefsrc")}
            except (OperationError, ValueError, IndexError, TypeError):
                routes[address] = {"error": "route_read_unavailable"}
        runtime_nodes = {}
        for role, provider in PROVIDERS.items():
            meta = self.api('/providers/proxies/' + urllib.parse.quote(provider, safe=''))
            runtime_nodes[role] = sorted(p['name'] for p in meta.get('proxies', []) if p.get('name')
                and p.get('type') not in ('Selector', 'Fallback', 'URLTest', 'Direct', 'Reject', 'LoadBalance')
                and not re.match(r'^(剩余流量|套餐到期|到期时间)[：:]', p['name']))
        try:
            management = self.management_identity()
            rule = 'UID,' + str(management['uid']) + ',' + SELECTORS['backup']
            management['rule_first'] = (self.config.get('rules') or [None])[0] == rule
            management['process_mode'] = self.config.get('find-process-mode', 'strict')
        except (OperationError, OSError, KeyError):
            management = {'error': 'management_identity_unavailable'}
        return {"version": VERSION, "observed_at": self.clock(),
            "hostname": socket.gethostname(), "groups": groups, "management_route": management,
            "dns": {"prefer_h3": dns.get("prefer-h3"),
                    "respect_rules": dns.get("respect-rules"),
                    "fallback": public_urls(dns.get("fallback")),
                    "proxy_server_nameserver": public_urls(dns.get("proxy-server-nameserver"))},
            "primary_node_dns_policy": {name: public_urls(dns.get('proxy-server-nameserver-policy', {}).get(name))
                                        for name in sorted(PRIMARY_NODE_DOMAINS)},
            "provider_runtime_nodes": runtime_nodes,
            "selected_cache": self.config.get("profile", {}).get("store-selected"),
            "wireguard_allowed_ips": [line.split()[1:] for line in wg.splitlines()],
            "wireguard_read_error": wg_error, "host_read_context": self.host_read_diagnostics(),
            "entry_routes": routes,
            "provider_inventory": {role: self.provider_inventory(role) for role in PROVIDERS},
            "pending_changes": self.active_change()}

    def probe(self, provider="primary", offset=0, limit=4, timeout_ms=4000):
        require(provider in PROVIDERS, "invalid_provider")
        bounded_int(offset, 0, 10000)
        bounded_int(limit, 1, 6)
        bounded_int(timeout_ms, 1000, 8000)
        metadata = self.api("/providers/proxies/" + urllib.parse.quote(PROVIDERS[provider], safe=""))
        nodes = sorted({p["name"] for p in metadata.get("proxies", [])
                        if p.get("name") and p.get("type") not in (
                            "Selector", "Fallback", "URLTest", "LoadBalance", "Direct", "Reject")
                        and not re.match(r"^(剩余流量|套餐到期|到期时间)[：:]", p["name"])})
        cfg_hash = digest(self.config_path.read_bytes())
        def fresh(node):
            tests = []
            for url, status in TESTS:
                query = urllib.parse.urlencode({"url": url, "timeout": timeout_ms, "expected": status})
                try:
                    result = self.api("/proxies/" + urllib.parse.quote(node, safe="") + "/delay?" + query,
                                      timeout=timeout_ms / 1000 + 2)
                    ok = type(result.get("delay")) is int and result["delay"] > 0
                    tests.append({"url": url, "expected_http": status, "ok": ok,
                                  "delay_ms": result.get("delay") if ok else None})
                except Exception as error:
                    failure = {"url": url, "expected_http": status, "ok": False,
                               "error": type(error).__name__}
                    if isinstance(error, urllib.error.HTTPError):
                        failure["controller_http_status"] = error.code
                    tests.append(failure)
            receipt = {"id": uuid.uuid4().hex, "kind": "probe", "provider": provider,
                       "node": node, "observed_at": self.clock(), "config_hash": cfg_hash,
                       "tests": tests, "ok": all(t["ok"] for t in tests)}
            self.save(receipt)
            return receipt
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(fresh, nodes[offset:offset + limit]))
        return {"provider": provider, "total": len(nodes), "offset": offset,
                "next_offset": offset + limit if offset + limit < len(nodes) else None,
                "results": results, "scope": "fresh transport probes; not model generation"}

    def fresh_receipt(self, identity, role):
        item = self.load(identity, "probe")
        require(item["provider"] == role and item.get("ok") is True, "probe_not_successful")
        require(0 <= self.clock() - item["observed_at"] <= 180, "probe_expired")
        require(item["config_hash"] == digest(self.config_path.read_bytes()), "config_changed_since_probe")
        require(item["node"] in self.group(SELECTORS[role]).get("all", []), "node_not_in_selector")
        return item

    def primary_dns_template(self, url):
        parsed = urllib.parse.urlsplit(url)
        require(parsed.scheme == 'https' and parsed.hostname == 'web.laddercat.net'
                and parsed.port in (None,443) and not (parsed.username or parsed.password or parsed.query or parsed.fragment)
                and re.fullmatch(r'/subscribe/[0-9a-fA-F]{32,64}', parsed.path), 'unsupported_primary_subscription')
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({'https':'http://127.0.0.1:7890'}), NoRedirect())
        request = urllib.request.Request(url, headers={'User-Agent':'Clash.Meta'})
        with opener.open(request, timeout=8) as response:
            raw = response.read(1_048_577)
        require(len(raw) <= 1_048_576, 'subscription_too_large')
        try:
            data = yaml.safe_load(raw)
        except yaml.YAMLError:
            raise OperationError('invalid_subscription_yaml') from None
        require(isinstance(data,dict), 'invalid_subscription')
        resolvers = (data.get('dns') or {}).get('proxy-server-nameserver')
        require(isinstance(resolvers,list) and len(resolvers)==2 and set(resolvers)==set(PRIMARY_DNS_RESOLVERS),
                'provider_dns_template_changed')
        domains = {p.get('server') for p in data.get('proxies',[]) if isinstance(p,dict)}
        require(PRIMARY_NODE_DOMAINS <= domains, 'provider_node_template_changed')
        return list(PRIMARY_DNS_RESOLVERS)

    def prepare(self, profile, primary_receipt="", backup_receipt=""):
        require(profile in ("switch_primary", "switch_backup", "stabilize_dns", "persist_wireguard"), "invalid_profile")
        old_text = self.config_path.read_text()
        require(yaml.safe_load(old_text) == self.config, 'configuration_drift')
        new_text, old_wg, new_wg = old_text, None, None
        selections = self.selected()
        target = dict(selections)
        summary = {"profile": profile}
        if profile in ("switch_primary", "switch_backup"):
            require(self.config.get("profile", {}).get("store-selected") is True,
                    "selected_cache_not_enabled")
            if profile == 'switch_primary':
                target = {role: self.fresh_receipt(identity, role)["node"] for role, identity in (
                    ("primary", primary_receipt), ("backup", backup_receipt))}
            else:
                require(not primary_receipt, 'unexpected_primary_receipt')
                target['backup'] = self.fresh_receipt(backup_receipt, 'backup')['node']
            preferred = 'primary' if profile == 'switch_primary' else 'backup'
            order = [SELECTORS[preferred], SELECTORS['backup' if preferred == 'primary' else 'primary']]
            groups = self.config.get("proxy-groups", [])
            edits = []
            for name in FALLBACKS:
                matches = [i for i, group in enumerate(groups) if group.get("name") == name]
                require(len(matches) == 1, "ambiguous_fallback_group")
                i = matches[0]
                require(groups[i].get("type") == "fallback", "unsupported_fallback_group")
                members = groups[i].get("proxies", [])
                members = order + [x for x in members if x not in SELECTORS.values()]
                edits.append((("proxy-groups", i, "proxies"), members))
            new_text, normalized = scoped_candidate(old_text, edits)
            summary["yaml_layout_normalized"] = normalized
            summary["fallback_priority"] = order
            summary["semantic_scope"] = "Only the declared fallback member lists change; all other values match exactly."
            summary["target_selectors"] = target
        elif profile == "stabilize_dns":
            dns = self.config.get("dns", {})
            require(dns.get("proxy-server-nameserver"), "missing_independent_dns_bootstrap")
            require(dns.get("respect-rules") is True, "dns_rules_not_enabled")
            updates = {'prefer-h3': False, 'fallback': ["https://dns.quad9.net/dns-query#主备自动切换",
                                                       "https://dns.google/dns-query#主备自动切换"]}
            primary = self.config.get('proxy-providers', {}).get(PROVIDERS['primary'], {})
            url = str(primary.get('url') or '')
            if urllib.parse.urlsplit(url).hostname == 'web.laddercat.net':
                require(self.group(SELECTORS['backup']).get('alive') is True, 'backup_required_for_dns_bootstrap')
                resolvers = self.primary_dns_template(url)
                policy = copy.deepcopy(dns.get('proxy-server-nameserver-policy') or {})
                require(isinstance(policy,dict), 'unsupported_dns_policy')
                for name in PRIMARY_NODE_DOMAINS:
                    policy[name] = [r + '#' + SELECTORS['backup'] for r in resolvers]
                updates['proxy-server-nameserver-policy'] = policy
                # Keep the repaired provider on one explicit leaf. Its previous
                # URL-test selector can include unrelated providers and may move
                # the management tunnel before the client can confirm this plan.
                require(self.config.get('profile', {}).get('store-selected') is True,
                        'selected_cache_not_enabled')
                members = self.group(SELECTORS['primary']).get('all', [])
                candidates = [name for name in ('JP 日本｜中转 01', 'JP 日本｜中转 02') if name in members]
                require(candidates, 'supported_primary_leaf_missing')
                target['primary'] = candidates[0]
                summary['target_selectors'] = target.copy()
                summary['selector_scope'] = 'Pin the primary candidate; fallback remains automatic and transport verification is required.'
                identity = self.management_identity()
                rules = self.config.get('rules', [])
                require(isinstance(rules, list) and all(isinstance(r, str) for r in rules),
                        'unsupported_routing_rules')
                management_rule = 'UID,' + str(identity['uid']) + ',' + SELECTORS['backup']
                summary['management_route'] = {**identity, 'via': SELECTORS['backup'],
                    'rule': management_rule, 'scope': 'Dedicated Ops account only; Jarvis business rules unchanged.'}
                summary['primary_node_dns'] = {'domains': sorted(PRIMARY_NODE_DOMAINS),
                    'resolvers': resolvers, 'via': SELECTORS['backup'], 'source_sha256': digest(url.encode()),
                    'scope': 'node domains only; global bootstrap, credentials, SNI, routes and backup unchanged'}
            new_text, normalized = dns_candidate(old_text, updates)
            if summary.get('management_route'):
                expected = yaml.safe_load(new_text)
                expected['rules'] = [management_rule] + [r for r in rules if r != management_rule]
                expected['find-process-mode'] = 'strict'
                new_text = yaml.safe_dump(expected, allow_unicode=True, sort_keys=False)
                require(yaml.safe_load(new_text) == expected, 'management_route_scope_mismatch')
                normalized = True
            summary['yaml_layout_normalized'] = normalized
            summary['semantic_scope'] = ('Declared DNS fields plus the dedicated Ops UID rule and process matching; '
                'all other configuration values must match exactly.' if summary.get('management_route') else
                'Only the declared DNS fields change; all other values must match exactly.')
            summary["dns_fallback"] = ["dns.quad9.net via proxy", "dns.google via proxy"]
        else:
            old_wg = self.wg_path.read_text()
            require(len(re.findall(r"(?m)^\s*\[Peer\]\s*$", old_wg)) == 1, "only_single_peer_supported")
            require(not re.search(r"(?im)^\s*SaveConfig\s*=\s*true\s*$", old_wg), "wg_saveconfig_enabled")
            require(not re.search(r"(?im)^\s*Table\s*=\s*(?!auto\s*$)\S+", old_wg), "wg_custom_table")
            runtime = self.runner(["wg", "show", "wg0", "allowed-ips"]).splitlines()
            require(len(runtime) == 1, "only_single_peer_supported")
            cidrs = runtime[0].split()[1:]
            require(0 < len(cidrs) <= 16, "unexpected_wg_scope")
            for cidr in cidrs:
                network = ipaddress.ip_network(cidr, strict=True)
                require(network.num_addresses == 1 and network.network_address.is_global, "wg_host_routes_only")
            matches = list(re.finditer(r"(?m)^([ \t]*AllowedIPs[ \t]*=[ \t]*)([^\r\n]+)", old_wg))
            require(len(matches) == 1, "ambiguous_allowed_ips")
            old_cidrs = {x.strip() for x in matches[0].group(2).split(",")}
            require(old_cidrs <= set(cidrs), "would_remove_existing_allowed_ip")
            m = matches[0]
            new_wg = old_wg[:m.start(2)] + ", ".join(cidrs) + old_wg[m.end(2):]
            summary["allowed_ips"] = cidrs
        record = {"id": uuid.uuid4().hex, "kind": "change", "phase": "planned",
                  "created_at": self.clock(), "profile": profile, "summary": summary,
                  "old_config": old_text, "new_config": new_text,
                  "old_wg": old_wg, "new_wg": new_wg,
                  "old_selectors": selections, "new_selectors": target,
                  "receipts": {"primary": primary_receipt, "backup": backup_receipt}}
        self.save(record)
        return self.public(record)

    def public(self, item):
        return {key: item.get(key) for key in (
            "id", "kind", "phase", "profile", "created_at", "applied_at", "deadline", "summary", "verification") if key in item}

    def global_lock(self):
        stream = (self.state / "global.lock").open("a+")
        fcntl.flock(stream, fcntl.LOCK_EX)
        return stream

    def write_existing(self, path, text):
        require(path.is_file() and not path.is_symlink(), "unsafe_configuration_path")
        meta = path.stat()
        atomic(path, text.encode(), meta.st_mode & 0o777, meta.st_uid, meta.st_gid)

    def reload(self):
        self.api("/configs?force=true", method="PUT", data={"path": str(self.config_path)}, timeout=20)

    def flush_dns_cache(self):
        try:
            self.api('/cache/dns/flush', method='POST')
            return True
        except Exception:
            return False

    def apply(self, change_id):
        with self.global_lock():
            item = self.load(change_id, "change")
            require(item["phase"] == "planned", "change_not_planned")
            require(0 <= self.clock() - item["created_at"] <= 180, "plan_expired")
            require(not self.active_change(), "another_change_pending")
            require(self.config_path.read_text() == item["old_config"], "configuration_drift")
            require(self.selected() == item["old_selectors"], "selector_drift")
            if item['summary'].get('management_route'):
                actual = self.management_identity()
                require(all(actual[k] == item['summary']['management_route'][k] for k in ('user', 'uid')),
                        'management_identity_drift')
            if item["profile"] in ("switch_primary", "switch_backup"):
                for role, identity in item["receipts"].items():
                    if identity:
                        self.fresh_receipt(identity, role)
            if item["old_wg"] is not None:
                require(self.wg_path.read_text() == item["old_wg"], "wireguard_drift")
            candidate = self.state / (change_id + ".candidate.yaml")
            atomic(candidate, item["new_config"].encode())
            self.runner(["/usr/local/bin/mihomo", "-t", "-d", str(self.config_path.parent), "-f", str(candidate)], timeout=25)
            timer = "jarvis-network-guard-" + change_id
            item.update(phase="pending", applied_at=self.clock(), deadline=self.clock() + 120, timer=timer)
            self.save(item)
            try:
                self.runner(["systemd-run", "--quiet", "--unit=" + timer, "--on-active=120s",
                    "--timer-property=AccuracySec=1s", "--property=Type=oneshot",
                    "--property=RuntimeMaxSec=60s", HELPER, "rollback-auto", change_id])
                require(self.runner(["systemctl", "is-active", timer + ".timer"]) == "active", "guard_not_active")
                self.write_existing(self.config_path, item["new_config"])
                if item["new_wg"] is not None:
                    self.write_existing(self.wg_path, item["new_wg"])
                if item["new_config"] != item["old_config"]:
                    self.reload()
                    if item['summary'].get('primary_node_dns'):
                        require(self.flush_dns_cache(), 'dns_cache_flush_failed')
                self.set_selected(item["new_selectors"])
            except Exception:
                self._restore(item)
                raise
            return self.public(item)

    def _restore(self, item):
        # The independent timer retries if any restoration step fails.
        require(self.config_path.read_text() in (item["old_config"], item["new_config"]),
                "restore_configuration_drift")
        if item["old_wg"] is not None:
            require(self.wg_path.read_text() in (item["old_wg"], item["new_wg"]),
                    "restore_wireguard_drift")
        self.write_existing(self.config_path, item["old_config"])
        if item["old_wg"] is not None:
            self.write_existing(self.wg_path, item["old_wg"])
        if item["new_config"] != item["old_config"]:
            self.reload()
            if item['summary'].get('primary_node_dns'):
                self.flush_dns_cache()
        self.set_selected(item["old_selectors"])
        item["phase"] = "rolled_back"
        self.save(item)
        self.runner(["systemctl", "stop", item["timer"] + ".timer"])

    def rollback(self, change_id, automatic=False):
        with self.global_lock():
            item = self.load(change_id, "change")
            if item["phase"] != "pending":
                return self.public(item)
            self._restore(item)
            return self.public(item)

    def proxy_check(self):
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"https": "http://127.0.0.1:7890"}), NoRedirect())
        try:
            with opener.open(TESTS[1][0], timeout=10) as result:
                code = result.status
        except urllib.error.HTTPError as error:
            code = error.code
        require(code == 401, "openai_transport_check_failed")
        return {"openai_http": code, "scope": "transport only; not a model call"}

    def confirm(self, change_id):
        # Network I/O happens outside the lock so the guard can always acquire it.
        item = self.load(change_id, "change")
        require(item["phase"] == "pending", "change_not_pending")
        if item['summary'].get('management_route'):
            actual = self.management_identity()
            require(all(actual[k] == item['summary']['management_route'][k] for k in ('user', 'uid')),
                    'management_identity_drift')
        if item["profile"] == "persist_wireguard":
            runtime = self.runner(["wg", "show", "wg0", "allowed-ips"]).splitlines()
            require(len(runtime) == 1 and set(runtime[0].split()[1:]) == set(item["summary"]["allowed_ips"]),
                    "wireguard_runtime_drift")
            verification = {"scope": "disk mirrors existing WG runtime; no traffic change"}
        else:
            verification = self.proxy_check()
            for name in ("chatgpt.com", "api.openai.com"):
                result = self.api("/dns/query?" + urllib.parse.urlencode({"name": name, "type": "A"}))
                require(result.get("Status") == 0 and result.get("Answer"), "dns_check_failed")
            if item["profile"] in ('switch_primary', 'switch_backup'):
                preferred = 'primary' if item['profile'] == 'switch_primary' else 'backup'
                for name in FALLBACKS:
                    require(self.group(name).get('now') == SELECTORS[preferred], preferred + '_not_selected_yet')
        with self.global_lock():
            item = self.load(change_id, "change")
            require(item["phase"] == "pending" and self.clock() < item["deadline"], "guard_expired")
            require(self.config_path.read_text() == item["new_config"], "configuration_drift")
            if item["new_wg"] is not None:
                require(self.wg_path.read_text() == item["new_wg"], "wireguard_drift")
            require(self.selected() == item["new_selectors"], "selector_drift")
            item.update(phase="committed", verification=verification)
            self.save(item)
            self.runner(["systemctl", "stop", item["timer"] + ".timer"])
            return self.public(item)

    def status(self, change_id):
        return self.public(self.load(change_id, "change"))

    def extension_status(self):
        folder = Path("/var/lib/jarvis-ops-network-install")
        records = list(folder.glob("*/state.json"))
        latest = max(records, key=lambda p: p.stat().st_mtime) if records else None
        install = json.loads(latest.read_text()) if latest else {}
        upgrades = list(self.state.glob("upgrade-*.json"))
        upgrade = json.loads(max(upgrades, key=lambda p: p.stat().st_mtime).read_text()) if upgrades else None
        return {"version": VERSION, "arbitrary_shell": False, "rollback_seconds": 120,
                "execution": "independent_systemd_service",
                "upgrade_request": upgrade,
                "client_repair": self.latest_client_repair(),
                "installation": {key: install.get(key) for key in ("id", "phase", "commit", "package_sha256", "request_id")}}

    def latest_client_repair(self):
        records = []
        for path in self.state.glob('*.json'):
            value = json.loads(path.read_text())
            if value.get('kind') == 'client-repair':
                records.append(value)
        return self.public(max(records, key=lambda r: r['created_at'])) if records else None

    def compose_state(self):
        argv = ['docker', 'compose', '--project-directory', '/home/ubuntu/jarvis',
                '-f', '/home/ubuntu/jarvis/docker-compose.yml']
        data = json.loads(self.runner(argv + ['config', '--format', 'json']))
        return argv, data, digest(json.dumps(data, sort_keys=True).encode())

    def container_state(self):
        value = json.loads(self.runner(['docker', 'inspect', 'jarvis']))
        require(len(value) == 1, 'unexpected_container_inspection')
        return value[0]

    def image_probe(self, image=None):
        script = "import hashlib,json,subprocess;from pathlib import Path;" \
            "a=Path('/app/venv/lib/python3.11/site-packages/qwenpaw/harnesses/codex/app_server.py').read_bytes();" \
            "b=Path('/app/venv/lib/python3.11/site-packages/qwenpaw/harnesses/codex/adapter.py').read_bytes();" \
            "print(json.dumps({'client_sha256':hashlib.sha256(a).hexdigest(),'adapter_sha256':hashlib.sha256(b).hexdigest()," \
            "'client_marker':b'JARVIS_CODEX_CLIENT_HEALTH_V1' in a,'adapter_marker':b'JARVIS_CODEX_PROCESS_EPOCH_V1' in b," \
            "'cli':subprocess.check_output(['/app/venv/lib/python3.11/site-packages/codex_cli_bin/bin/codex','--version'],text=True).strip()}))"
        argv = ['docker', 'exec', 'jarvis', '/app/venv/bin/python', '-c', script] if image is None else [
            'docker', 'run', '--rm', '--network', 'none', '--read-only', '--cap-drop', 'ALL',
            '--security-opt', 'no-new-privileges:true', '--tmpfs', '/tmp:rw,nosuid',
            '--entrypoint', '/app/venv/bin/python', image, '-c', script]
        return json.loads(self.runner(argv, timeout=20))

    def wait_jarvis_health(self, image, seconds=45):
        deadline = self.clock() + seconds
        while self.clock() < deadline:
            value = self.container_state()
            require(value.get('Image') == image, 'container_image_drift')
            if (value.get('State', {}).get('Health') or {}).get('Status') == 'healthy':
                return
            time.sleep(1)
        raise OperationError('jarvis_health_timeout')

    def client_repair(self, commit_sha, package_sha256, operation='apply', change_id=''):
        require(isinstance(commit_sha, str) and re.fullmatch(r'[0-9a-f]{40}', commit_sha)
                and isinstance(package_sha256, str) and re.fullmatch(r'[0-9a-f]{64}', package_sha256), 'invalid_repair_digest')
        require(operation in ('apply', 'confirm', 'rollback'), 'invalid_repair_operation')
        installed = self.extension_status()['installation']
        require(installed.get('phase') == 'committed' and installed.get('commit') == commit_sha
                and installed.get('package_sha256') == package_sha256, 'repair_requires_installed_reviewed_release')
        if operation == 'rollback':
            return self.client_repair_restore(change_id)
        if operation == 'confirm':
            item = self.load(change_id, 'client-repair')
            require(item.get('commit') == commit_sha, 'repair_release_mismatch')
            if item['phase'] == 'committed':
                return self.public(item)
            require(item['phase'] == 'pending' and self.clock() < item['deadline'], 'repair_not_pending')
            require(self.compose_state()[2] == item['compose_hash'], 'compose_configuration_drift')
            require(self.container_state().get('Image') == item['candidate_image'], 'container_image_drift')
            require(self.image_probe() == item['candidate_probe'], 'client_source_drift')
            check = self.harness_smoke()
            require(check.get('ok') is True, 'installed_harness_smoke_failed')
            with self.global_lock():
                item = self.load(change_id, 'client-repair')
                require(item['phase'] == 'pending' and self.clock() < item['deadline'], 'repair_guard_expired')
                self.wait_jarvis_health(item['candidate_image'], seconds=3)
                require(self.compose_state()[2] == item['compose_hash'] and self.image_probe() == item['candidate_probe'], 'repair_verification_drift')
                item.update(phase='committed', verification={'isolated_harness': check, 'dingtalk_acceptance': 'pending'})
                self.save(item)
                self.runner(['systemctl', 'stop', item['timer'] + '.timer'])
            return self.public(item)
        require(not change_id, 'unexpected_change_id')
        with self.global_lock():
            require(not self.active_change(), 'another_change_pending')
            compose, spec, config_hash = self.compose_state()
            current = self.container_state()
            service = spec.get('services', {}).get('jarvis') or {}
            image = current.get('Config', {}).get('Image', '')
            require(re.fullmatch(r'jarvis:[A-Za-z0-9_.-]{1,180}', image) and service.get('image') == image,
                    'unsupported_image_reference')
            old_image = current.get('Image', '')
            require(re.fullmatch(r'sha256:[0-9a-f]{64}', old_image), 'unsupported_image_id')
            actual_env = dict(value.split('=', 1) for value in current.get('Config', {}).get('Env', []) if '=' in value)
            require(all(actual_env.get(k) == str(v) for k, v in (service.get('environment') or {}).items()),
                    'compose_environment_drift')
            require(service.get('network_mode') == current.get('HostConfig', {}).get('NetworkMode') == 'host', 'compose_network_drift')
            mounts = {v['Destination']: v for v in current.get('Mounts', [])}
            for volume in service.get('volumes', []):
                actual = mounts.get(volume.get('target'), {})
                require(volume.get('type') == actual.get('Type') == 'bind' and volume.get('source') == actual.get('Source')
                        and bool(volume.get('read_only', False)) == (not actual.get('RW', True)), 'compose_mount_drift')
            image_metadata = json.loads(self.runner(['docker', 'image', 'inspect', image]))[0]
            require(image_metadata['Id'] == old_image, 'image_alias_drift')
            expected_env = dict(v.split('=', 1) for v in image_metadata.get('Config', {}).get('Env', []) if '=' in v)
            expected_env.update({k: str(v) for k, v in (service.get('environment') or {}).items()})
            require(actual_env == expected_env, 'unmanaged_container_environment')
            require(set(mounts) == {v.get('target') for v in service.get('volumes', [])}, 'unmanaged_container_mounts')
            require(sorted(current.get('HostConfig', {}).get('CapDrop') or []) == sorted(service.get('cap_drop') or [])
                    and sorted(current.get('HostConfig', {}).get('SecurityOpt') or []) == sorted(service.get('security_opt') or []),
                    'compose_security_drift')
            probe = self.image_probe()
            require(probe.get('client_sha256') == 'e341ed34916909a99ad8ea54e5bbf5396327ce8c11c498491d8265b6b4a32333'
                    and probe.get('adapter_sha256') == 'a92640b5180613fd831d95190bc66e7e89cbe87d2e40de200a99370485867845',
                    'unsupported_live_client_source')
            url = 'https://raw.githubusercontent.com/jiaoyuyang/jarvis/' + commit_sha + '/patches/patch_qwenpaw_codex_client_health.py'
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({'https': 'http://127.0.0.1:7890'}), NoRedirect())
            with opener.open(url, timeout=10) as response:
                patch_bytes = response.read(65537)
            require(len(patch_bytes) <= 65536 and digest(patch_bytes) == CLIENT_REPAIR_PATCH_SHA256, 'repair_patch_digest_mismatch')
            identity = uuid.uuid4().hex
            build = self.state / ('client-build-' + identity)
            build.mkdir(mode=0o700)
            docker_config = self.state / ('client-docker-config-' + identity)
            docker_config.mkdir(mode=0o700)
            atomic(docker_config / 'config.json', json.dumps({'cliPluginsExtraDirs': ['/root/.docker/cli-plugins']}).encode())
            base_tag = 'jarvis:client-base-' + old_image[7:19]
            candidate_tag = 'jarvis:client-repair-' + CLIENT_REPAIR_PATCH_SHA256[:12]
            atomic(build / 'patch.py', patch_bytes, 0o644)
            dockerfile = ('FROM ' + base_tag + '\nCOPY patch.py /opt/jarvis/patches/patch_qwenpaw_codex_client_health.py\n'
                          'RUN /app/venv/bin/python /opt/jarvis/patches/patch_qwenpaw_codex_client_health.py\n')
            atomic(build / 'Dockerfile', dockerfile.encode(), 0o644)
            timer = 'jarvis-client-guard-' + identity
            item = {'id': identity, 'kind': 'client-repair', 'profile': 'codex-client', 'phase': 'pending',
                    'created_at': self.clock(), 'deadline': self.clock() + 180, 'timer': timer,
                    'old_image': old_image, 'image_alias': image, 'compose_hash': config_hash, 'commit': commit_sha,
                    'summary': {'scope': 'Derived running image, fixed client/adapter patch only; host checkout/env/proxy untouched',
                                'cli': probe.get('cli'), 'patch_sha256': CLIENT_REPAIR_PATCH_SHA256}}
            self.save(item)
            try:
                self.runner(['systemd-run', '--quiet', '--unit=' + timer, '--on-active=180s', '--timer-property=AccuracySec=1s',
                             '--property=Type=oneshot', '--property=Restart=on-failure', '--property=RestartSec=10s',
                             HELPER, 'client-repair-rollback', identity])
                require(self.runner(['systemctl', 'is-active', timer + '.timer']) == 'active', 'guard_not_active')
                self.runner(['docker', 'image', 'tag', old_image, base_tag])
                item['summary']['stage'] = 'building'
                self.save(item)
                self.runner(['docker', '--config', str(docker_config), 'build', '--network', 'none', '--pull=false',
                             '-t', candidate_tag, str(build)], timeout=45)
                item['summary']['stage'] = 'candidate_verification'
                self.save(item)
                candidate_image = json.loads(self.runner(['docker', 'image', 'inspect', candidate_tag]))[0]['Id']
                candidate_probe = self.image_probe(candidate_tag)
                require(candidate_probe.get('client_marker') and candidate_probe.get('adapter_marker')
                        and candidate_probe.get('cli') == probe.get('cli'), 'candidate_image_verification_failed')
                require(self.clock() < item['deadline'] and self.compose_state()[2] == config_hash, 'repair_pre_cutover_drift')
                item.update(candidate_image=candidate_image, candidate_probe=candidate_probe)
                self.save(item)
                item['summary']['stage'] = 'cutover'
                self.save(item)
                self.runner(['docker', 'image', 'tag', candidate_image, image])
                self.runner(compose + ['up', '-d', '--no-build', '--no-deps', '--force-recreate', 'jarvis'], timeout=25)
                self.wait_jarvis_health(candidate_image)
            except Exception as error:
                item['summary']['failed_stage'] = item['summary'].get('stage', 'guard_setup')
                item['summary']['failure'] = error.details if isinstance(error, HostCommandError) else {'error': str(error) if isinstance(error, OperationError) else type(error).__name__}
                self.save(item)
                self._client_repair_restore(item)
                raise
            return self.public(item)

    def _client_repair_restore(self, item):
        require(self.compose_state()[2] == item['compose_hash'], 'restore_compose_drift')
        current = self.container_state()
        require(current.get('Image') in (item['old_image'], item.get('candidate_image')), 'restore_container_drift')
        self.runner(['docker', 'image', 'tag', item['old_image'], item['image_alias']])
        if current.get('Image') != item['old_image']:
            compose = self.compose_state()[0]
            self.runner(compose + ['up', '-d', '--no-build', '--no-deps', '--force-recreate', 'jarvis'], timeout=25)
            self.wait_jarvis_health(item['old_image'])
        item['phase'] = 'rolled_back'
        self.save(item)
        self.runner(['systemctl', 'stop', item['timer'] + '.timer'])

    def client_repair_restore(self, change_id):
        with self.global_lock():
            item = self.load(change_id, 'client-repair')
            if item['phase'] == 'pending':
                self._client_repair_restore(item)
            return self.public(item)

    def application_logs(self, lines=80):
        bounded_int(lines, 1, 160)
        script = "from pathlib import Path;from collections import deque;import json;" \
                 "print(json.dumps({p:list(deque(Path(p).open(errors='replace'),maxlen=" + str(lines) + ")) if Path(p).is_file() else [] for p in ['/var/log/app.err.log','/var/log/app.out.log']}))"
        raw = self.runner(["docker", "exec", "jarvis", "python", "-c", script], timeout=15)
        data = json.loads(raw)
        # Return operational errors only, not ordinary chat content.
        selected = {name: [redact(line) for line in content if re.search(
            r"error|exception|traceback|timeout|failed|card.*final|turn.*completed|codex.*rpc", line, re.I)]
            for name, content in data.items()}
        return {"logs": selected, "scope": "recent matching application log lines"}

    @staticmethod
    def sanitized_result(value):
        if isinstance(value, str):
            return redact(value)
        if isinstance(value, list):
            return [Engine.sanitized_result(item) for item in value]
        if isinstance(value, dict):
            return {key: Engine.sanitized_result(item) for key, item in value.items()}
        return value

    def runtime_diagnostics(self):
        raw = self.runner(['docker', 'exec', 'jarvis', 'python', '-c', CODEX_DIAGNOSTIC_SCRIPT], timeout=25)
        require(len(raw) <= 131072, 'diagnostic_response_too_large')
        return self.sanitized_result(json.loads(raw))

    def harness_smoke(self):
        raw = self.runner(['docker', 'exec', 'jarvis', 'timeout', '-k', '5', '90',
                           'python', '-c', CODEX_HARNESS_SCRIPT], timeout=100)
        require(len(raw) <= 131072, 'diagnostic_response_too_large')
        return self.sanitized_result(json.loads(raw))

    def model_smoke(self):
        exe = "/app/venv/lib/python3.11/site-packages/codex_cli_bin/bin/codex"
        work = self.runner(["docker", "exec", "jarvis", "mktemp", "-d", "/tmp/jarvis-ops-smoke.XXXXXX"])
        require(re.fullmatch(r"/tmp/jarvis-ops-smoke\.[A-Za-z0-9]+", work), "unexpected_smoke_path")
        argv = ["docker", "exec", "jarvis", "timeout", "-k", "5", "90", exe, "exec",
                "--ignore-user-config", "--ephemeral", "--skip-git-repo-check", "--sandbox", "read-only",
                "-C", work, "-m", "gpt-6.1-sol", "-c", 'model_reasoning_effort="high"', "--json",
                "只回复：模型连通测试通过。不要调用工具、读取文件或执行命令。"]
        started = self.clock()
        result = subprocess.run(argv, capture_output=True, text=True, timeout=100)
        answers, completed, failures = [], False, []
        for line in result.stdout.splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("type") == "turn.completed":
                completed = True
            if event.get("type") in ("error", "turn.failed"):
                failures.append(redact(json.dumps(event, ensure_ascii=False)))
            item = event.get("item") or {}
            if event.get("type") == "item.completed" and item.get("type") == "agent_message":
                answers.append(str(item.get("text", "")))
        answer = "\n".join(answers).strip()
        ok = result.returncode == 0 and completed and answer.rstrip("。.!！") == "模型连通测试通过"
        return {"ok": ok, "model": "gpt-6.1-sol", "reasoning_effort": "high",
                "exit_code": result.returncode, "seconds": round(self.clock() - started, 1),
                "answer": answer if ok else "", "errors": failures[-3:],
                "stderr": redact(result.stderr[-2000:]) if not ok else ""}


def redact(text):
    text = re.sub(r"(?:https?|wss?)://[^\s<>\"']+", "[URL]", str(text))
    text = re.sub(r"(?i)\bBearer\s+\S+", "Bearer [REDACTED]", text)
    text = re.sub(r"\bsk-[A-Za-z0-9_-]+", "[KEY]", text)
    text = re.sub(r"\beyJ[\w-]+\.[\w-]+\.[\w-]+", "[JWT]", text)
    text = re.sub(r'''(?i)((?:\w*token|client_secret|api_key|password|cookie|authorization|ticket)["']?\s*[:=]\s*)(?:"[^"]*"|'[^']*'|[^\s,;]+)''', r"\1[REDACTED]", text)
    return text


def worker_main():
    require(os.geteuid() == 0, "root_helper_required")
    require(len(sys.argv) in (2, 3), "invalid_command")
    engine = Engine()
    action = sys.argv[1]
    if action in ("rollback-auto", "client-repair-rollback"):
        require(len(sys.argv) == 3, "invalid_command")
        return engine.rollback(sys.argv[2], automatic=True) if action == "rollback-auto" else engine.client_repair_restore(sys.argv[2])
    require(len(sys.argv) == 2, "invalid_command")
    raw = sys.stdin.buffer.read(65537)
    require(len(raw) <= 65536, "request_too_large")
    data = json.loads(raw or b"{}")
    require(isinstance(data, dict), "invalid_request")
    methods = {"snapshot": engine.snapshot, "probe": engine.probe, "prepare": engine.prepare,
               "apply": engine.apply, "confirm": engine.confirm, "rollback": engine.rollback,
               "status": engine.status, "logs": engine.application_logs, "model-smoke": engine.model_smoke,
               "runtime-diagnostics": engine.runtime_diagnostics, "harness-smoke": engine.harness_smoke,
               "client-repair": engine.client_repair,
               "extension-status": engine.extension_status}
    require(action in methods, "unsupported_operation")
    return methods[action](**data)


if __name__ == "__main__":
    try:
        if len(sys.argv) > 1 and sys.argv[1] == "--worker":
            del sys.argv[1]
            response = {"ok": True, "result": worker_main()}
        else:
            args = sys.argv[2:] if len(sys.argv) > 1 and sys.argv[1] == "--broker" else sys.argv[1:]
            result = broker(args, sys.stdin.buffer.read(65537))
            response = result if "ok" in result else {"ok": True, "result": result}
        print(json.dumps(response, ensure_ascii=False))
        if not response.get("ok"):
            sys.exit(1)
    except Exception as error:
        code = str(error) if isinstance(error, OperationError) else type(error).__name__
        print(json.dumps({"ok": False, "error": code}))
        sys.exit(1)
