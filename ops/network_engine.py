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

VERSION = "1.0.1"
CONFIG = Path("/etc/mihomo/config.yaml")
WIREGUARD = Path("/etc/wireguard/wg0.conf")
STATE = Path("/var/lib/jarvis-ops-network")
HELPER = "/usr/local/sbin/jarvis-network-ops"
INSTALLER = "/opt/jarvis-ops-network/install.py"
PROVIDERS = {"primary": "梯子猫订阅", "backup": "一元机场"}
SELECTORS = {"primary": "梯子猫", "backup": "一元日本01专线"}
FALLBACKS = ("OpenAI专用", "主备自动切换")
TESTS = (("https://www.gstatic.com/generate_204", 204),
         ("https://api.openai.com/v1/models", 401))
ID = re.compile(r"[0-9a-f]{32}")


class OperationError(Exception):
    """Only fixed public error codes may cross the MCP boundary."""


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
    result = subprocess.run(argv, capture_output=True, timeout=timeout, text=True)
    require(result.returncode == 0, "host_command_failed")
    return result.stdout.strip()


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
        spans.append((node.start_mark.index, node.end_mark.index,
                      json.dumps(value, ensure_ascii=False)))
        target = expected
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
    for start, end, value in sorted(spans, reverse=True):
        text = text[:start] + value + text[end:]
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

    def snapshot(self):
        groups = {}
        for name in (*SELECTORS.values(), *FALLBACKS):
            group = self.group(name)
            groups[name] = {key: group.get(key) for key in ("type", "now", "all", "alive")}
        dns = self.config.get("dns", {})
        def public_urls(values):
            result = []
            for value in values or []:
                value = str(value)
                if "://" in value:
                    parsed = urllib.parse.urlsplit(value)
                    value = parsed.scheme + "://" + (parsed.hostname or "") + parsed.path
                result.append(value)
            return result
        wg = self.runner(["wg", "show", "wg0", "allowed-ips"])
        return {"version": VERSION, "observed_at": self.clock(),
            "hostname": socket.gethostname(), "groups": groups,
            "dns": {"prefer_h3": dns.get("prefer-h3"),
                    "respect_rules": dns.get("respect-rules"),
                    "fallback": public_urls(dns.get("fallback")),
                    "proxy_server_nameserver": public_urls(dns.get("proxy-server-nameserver"))},
            "selected_cache": self.config.get("profile", {}).get("store-selected"),
            "wireguard_allowed_ips": [line.split()[1:] for line in wg.splitlines()],
            "pending_changes": self.active_change()}

    def probe(self, provider="primary", offset=0, limit=4, timeout_ms=4000):
        require(provider in PROVIDERS, "invalid_provider")
        bounded_int(offset, 0, 10000)
        bounded_int(limit, 1, 6)
        bounded_int(timeout_ms, 1000, 8000)
        metadata = self.api("/providers/proxies/" + urllib.parse.quote(PROVIDERS[provider], safe=""))
        nodes = sorted({p["name"] for p in metadata.get("proxies", [])
                        if p.get("name") and p.get("type") not in (
                            "Selector", "Fallback", "URLTest", "LoadBalance", "Direct", "Reject")})
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
                    tests.append({"url": url, "expected_http": status, "ok": False,
                                  "error": type(error).__name__})
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
            new_text = yaml_patch(old_text, [(("dns", "prefer-h3"), False),
                (("dns", "fallback"), ["https://dns.quad9.net/dns-query#主备自动切换",
                                          "https://dns.google/dns-query#主备自动切换"])])
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
        return {"version": VERSION, "arbitrary_shell": False, "rollback_seconds": 120,
                "installation": {key: install.get(key) for key in ("id", "phase", "commit", "package_sha256")}}

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


def main():
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
    if action == "upgrade":
        require(set(data) == {"commit_sha", "package_sha256"}, "invalid_upgrade_request")
        require(re.fullmatch(r"[0-9a-f]{40}", data["commit_sha"]) and
                re.fullmatch(r"[0-9a-f]{64}", data["package_sha256"]), "invalid_upgrade_digest")
        # The installer independently verifies origin/main and every pinned file hash.
        return json.loads(command(["/usr/bin/python3", "-I", INSTALLER, "--commit", data["commit_sha"],
            "--package-sha256", data["package_sha256"], "--upgrade"], timeout=100))
    require(action in methods, "unsupported_operation")
    return methods[action](**data)


if __name__ == "__main__":
    try:
        print(json.dumps({"ok": True, "result": main()}, ensure_ascii=False))
    except Exception as error:
        code = str(error) if isinstance(error, OperationError) else type(error).__name__
        print(json.dumps({"ok": False, "error": code}))
        sys.exit(1)
