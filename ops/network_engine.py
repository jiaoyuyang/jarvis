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

VERSION = "1.1.2"
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
               "model-smoke", "extension-status", "upgrade", "rollback-auto"}
    require(action in allowed, "unsupported_operation")
    data = json.loads(raw or b"{}")
    require(isinstance(data, dict), "invalid_request")
    if action == "rollback-auto":
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
        return {"version": VERSION, "observed_at": self.clock(),
            "hostname": socket.gethostname(), "groups": groups,
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
        require(profile in ("switch_primary", "stabilize_dns", "persist_wireguard"), "invalid_profile")
        old_text = self.config_path.read_text()
        new_text, old_wg, new_wg = old_text, None, None
        selections = self.selected()
        target = dict(selections)
        summary = {"profile": profile}
        if profile == "switch_primary":
            require(self.config.get("profile", {}).get("store-selected") is True,
                    "selected_cache_not_enabled")
            target = {role: self.fresh_receipt(identity, role)["node"] for role, identity in (
                ("primary", primary_receipt), ("backup", backup_receipt))}
            groups = self.config.get("proxy-groups", [])
            edits = []
            for name in FALLBACKS:
                matches = [i for i, group in enumerate(groups) if group.get("name") == name]
                require(len(matches) == 1, "ambiguous_fallback_group")
                i = matches[0]
                require(groups[i].get("type") == "fallback", "unsupported_fallback_group")
                members = groups[i].get("proxies", [])
                members = [SELECTORS["primary"]] + [x for x in members if x not in SELECTORS.values()] + [SELECTORS["backup"]]
                edits.append((("proxy-groups", i, "proxies"), members))
            new_text = yaml_patch(old_text, edits)
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
                summary['primary_node_dns'] = {'domains': sorted(PRIMARY_NODE_DOMAINS),
                    'resolvers': resolvers, 'via': SELECTORS['backup'], 'source_sha256': digest(url.encode()),
                    'scope': 'node domains only; global bootstrap, credentials, SNI, routes and backup unchanged'}
            new_text = yaml_mapping_update(old_text, ('dns',), updates)
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
            if item["profile"] == "switch_primary":
                for role, identity in item["receipts"].items():
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
            if item["profile"] == "switch_primary":
                for name in FALLBACKS:
                    require(self.group(name).get("now") == SELECTORS["primary"], "primary_not_selected_yet")
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
                "installation": {key: install.get(key) for key in ("id", "phase", "commit", "package_sha256", "request_id")}}

    def application_logs(self, lines=80):
        bounded_int(lines, 1, 160)
        script = "from pathlib import Path;from collections import deque;import json;" \
                 "print(json.dumps({p:list(deque(Path(p).open(errors='replace'),maxlen=" + str(lines) + ")) if Path(p).is_file() else [] for p in ['/var/log/app.err.log','/var/log/app.out.log']}))"
        raw = self.runner(["docker", "exec", "jarvis", "python", "-c", script], timeout=15)
        data = json.loads(raw)
        # Return operational errors only, not ordinary chat content.
        selected = {name: [redact(line) for line in content if re.search(
            r"error|exception|traceback|timeout|failed|card.*final|turn.*completed", line, re.I)]
            for name, content in data.items()}
        return {"logs": selected, "scope": "recent matching application log lines"}

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
    if action == "rollback-auto":
        require(len(sys.argv) == 3, "invalid_command")
        return engine.rollback(sys.argv[2], automatic=True)
    require(len(sys.argv) == 2, "invalid_command")
    raw = sys.stdin.buffer.read(65537)
    require(len(raw) <= 65536, "request_too_large")
    data = json.loads(raw or b"{}")
    require(isinstance(data, dict), "invalid_request")
    methods = {"snapshot": engine.snapshot, "probe": engine.probe, "prepare": engine.prepare,
               "apply": engine.apply, "confirm": engine.confirm, "rollback": engine.rollback,
               "status": engine.status, "logs": engine.application_logs, "model-smoke": engine.model_smoke,
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
