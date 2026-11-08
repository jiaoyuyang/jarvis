#!/usr/bin/env python3
"""One-time host bootstrap and subsequent pinned, protected Ops upgrades."""
from __future__ import annotations

import argparse
import ast
import base64
import fcntl
import hashlib
import json
import os
import pwd
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import uuid

REPOSITORY = "jiaoyuyang/jarvis"
FILES = ("network_engine.py", "mcp_extension.py", "install.py")
PACKAGE = Path("/opt/jarvis-ops-network")
STATE = Path("/var/lib/jarvis-ops-network-install")
SERVER = Path("/opt/jarvis-ops-mcp/server.py")
VENV_PYTHON = Path("/opt/jarvis-ops-mcp/.venv/bin/python")
SERVICE = "jarvis-ops-tunnel.service"
WRAPPER = Path("/usr/local/sbin/jarvis-network-ops")
SUDOERS = Path("/etc/sudoers.d/jarvis-network-ops")
BEGIN = "# BEGIN JARVIS NETWORK OPS EXTENSION"
END = "# END JARVIS NETWORK OPS EXTENSION"
TOOL_NAMES = {"ops_extension_status", "network_snapshot", "probe_proxy_nodes", "prepare_network_change",
              "apply_network_change", "confirm_network_change", "network_change_status",
              "rollback_network_change", "read_application_logs", "model_smoke_test", "upgrade_ops_extension"}


def require(ok, code):
    if not ok:
        raise RuntimeError(code)


def run(args, timeout=20):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    require(result.returncode == 0, "host_preflight_failed")
    return result.stdout.strip()


def atomic(path, content, mode=0o600, uid=0, gid=0):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".ops-install-", dir=path.parent)
    try:
        os.fchmod(fd, mode)
        os.fchown(fd, uid, gid)
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def package_hash(hashes):
    return hashlib.sha256("".join(name + "\0" + hashes[name] + "\n" for name in FILES).encode()).hexdigest()


def server_patch(source):
    require(source.count(BEGIN) == source.count(END) and source.count(BEGIN) <= 1,
            "ambiguous_existing_registration")
    source = re.sub(re.escape(BEGIN) + r".*?" + re.escape(END) + r"(?:\r?\n){0,2}", "", source, flags=re.S)
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for decorator in node.decorator_list:
                if isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute) and decorator.func.attr == "tool":
                    explicit = [k.value.value for k in decorator.keywords if k.arg == "name" and isinstance(k.value, ast.Constant)]
                    require((explicit[0] if explicit else node.name) not in TOOL_NAMES,
                            "tool_name_conflict_no_changes_made")
    variables = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            func = node.value.func
            if (isinstance(func, ast.Name) and func.id == "FastMCP") or (
                    isinstance(func, ast.Attribute) and func.attr == "FastMCP"):
                variables.extend(x.id for x in node.targets if isinstance(x, ast.Name))
    require(len(variables) == 1, "unsupported_mcp_construction_no_changes_made")
    guards = [node for node in tree.body if isinstance(node, ast.If) and
              ast.dump(node.test) in (
                  ast.dump(ast.parse('__name__ == "__main__"', mode="eval").body),
                  ast.dump(ast.parse('"__main__" == __name__', mode="eval").body))]
    require(len(guards) == 1, "unsupported_server_entrypoint_no_changes_made")
    for node in tree.body:
        if node is guards[0]:
            continue
        if isinstance(node, ast.Expr) and any(isinstance(x, ast.Call) and
            isinstance(x.func, ast.Attribute) and x.func.attr == "run" for x in ast.walk(node)):
            raise RuntimeError("unguarded_server_run_no_changes_made")
    lines = source.splitlines(keepends=True)
    block = (BEGIN + "\nimport sys as _jarvis_network_sys\n"
             "_jarvis_network_sys.path.insert(0, '/opt/jarvis-ops-network')\n"
             "from mcp_extension import register_tools as _jarvis_network_register\n"
             f"_jarvis_network_register({variables[0]})\n" + END + "\n\n")
    lines.insert(guards[0].lineno - 1, block)
    result = "".join(lines)
    compile(result, str(SERVER), "exec")
    return result, variables[0]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise RuntimeError("unexpected_github_redirect")


def service_actor(proc_root=Path("/proc")):
    """Use the actual running MCP child's UID, not a possibly different tunnel UID."""
    owners = set()
    for proc in proc_root.iterdir():
        if not proc.name.isdecimal():
            continue
        try:
            comm = (proc / "comm").read_text().strip()
            if not comm.startswith("python"):
                continue
            argv = (proc / "cmdline").read_bytes().split(b"\0")
            if str(SERVER).encode() in argv[1:]:
                owners.add(proc.stat().st_uid)
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
    require(len(owners) == 1, "cannot_identify_running_ops_actor_no_changes_made")
    return pwd.getpwuid(owners.pop()).pw_name


def fetch_release(commit, expected):
    require(re.fullmatch(r"[0-9a-f]{40}", commit) and re.fullmatch(r"[0-9a-f]{64}", expected),
            "invalid_release_identity")
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"https": "http://127.0.0.1:7890"}), NoRedirect())
    def get(path):
        req = urllib.request.Request("https://api.github.com/repos/" + REPOSITORY + path,
                                     headers={"Accept": "application/vnd.github+json", "User-Agent": "jarvis-ops-updater"})
        with opener.open(req, timeout=8) as response:
            raw = response.read(1_048_577)
        require(len(raw) <= 1_048_576, "github_response_too_large")
        return json.loads(raw)
    # Only the owner's merged default branch can supply privileged updates.
    require(get("/commits/main")["sha"] == commit, "release_is_not_current_main")
    manifest = get("/contents/ops/release.json?ref=" + commit)
    release = json.loads(base64.b64decode(manifest["content"], validate=False))
    hashes = release.get("sha256", {})
    require(set(hashes) == set(FILES) and package_hash(hashes) == expected == release.get("package_sha256"),
            "release_manifest_mismatch")
    payload = {}
    for name in FILES:
        obj = get("/contents/ops/" + name + "?ref=" + commit)
        require(obj.get("type") == "file" and obj.get("encoding") == "base64", "unsupported_release_file")
        data = base64.b64decode(obj["content"], validate=False)
        require(hashlib.sha256(data).hexdigest() == hashes[name], "release_file_hash_mismatch")
        compile(data, name, "exec")
        payload[name] = data
    return payload, release


def record(identity):
    require(re.fullmatch(r"[0-9a-f]{32}", identity), "invalid_install_id")
    folder = STATE / identity
    require(folder.is_dir() and not folder.is_symlink(), "unknown_install_id")
    return folder, json.loads((folder / "state.json").read_text())


def transaction_lock(identity):
    folder, _ = record(identity)
    stream = (folder / "transaction.lock").open("a+")
    import fcntl
    fcntl.flock(stream, fcntl.LOCK_EX)
    return stream


def restore(identity):
    with transaction_lock(identity):
        return _restore(identity)


def _restore(identity):
    folder, info = record(identity)
    if info["phase"] != "pending":
        return
    for item in info["files"]:
        path = Path(item["path"])
        if item["existed"]:
            atomic(path, (folder / item["backup"]).read_bytes(), item["mode"], item["uid"], item["gid"])
        elif path.exists():
            path.unlink()
    info["phase"] = "rolled_back"
    atomic(folder / "state.json", json.dumps(info).encode())
    run(["systemctl", "restart", SERVICE], timeout=30)


def verify(identity):
    with transaction_lock(identity):
        return _verify(identity)


def _verify(identity):
    folder, info = record(identity)
    if info["phase"] != "pending":
        return
    try:
        # Execute the bridge as the actual service account, not merely as root.
        argv = [str(WRAPPER), "snapshot"]
        if info["service_user"] != "root":
            argv = ["runuser", "-u", info["service_user"], "--", "sudo", "-n", *argv]
        result = subprocess.run(argv, input="{}", capture_output=True, text=True, timeout=35)
        require(result.returncode == 0 and json.loads(result.stdout).get("ok"), "privileged_bridge_check_failed")
        require(run(["systemctl", "is-active", SERVICE]) == "active", "ops_service_not_active")
        info["phase"] = "committed"
        atomic(folder / "state.json", json.dumps(info).encode())
        run(["systemctl", "stop", info["guard"] + ".timer"])
    except Exception:
        _restore(identity)
        raise


def install(commit, expected, upgrade=False):
    require(os.geteuid() == 0, "run_installer_as_root")
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    require(not STATE.is_symlink() and STATE.stat().st_uid == 0, "unsafe_install_state")
    os.chmod(STATE, 0o700)
    with (STATE / "install.lock").open("a+") as install_lock:
        fcntl.flock(install_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return _install_locked(commit, expected, upgrade)


def _install_locked(commit, expected, upgrade=False):
    for existing in STATE.glob("*/state.json"):
        require(json.loads(existing.read_text()).get("phase") != "pending", "another_install_pending")
    if PACKAGE.exists():
        require(not PACKAGE.is_symlink() and PACKAGE.stat().st_uid == 0,
                "unsafe_package_directory")
    require(SERVER.is_file() and not SERVER.is_symlink() and VENV_PYTHON.is_file(),
            "existing_ops_server_not_found_no_changes_made")
    source = SERVER.read_text()
    patched, mcp_name = server_patch(source)
    user = service_actor()
    require(re.fullmatch(r"[a-z_][a-z0-9_-]*\$?", user), "unsupported_service_user")
    run(["/usr/bin/python3", "-c", "import yaml"])
    payload, release = fetch_release(commit, expected)
    identity = uuid.uuid4().hex
    folder = STATE / identity
    folder.mkdir(mode=0o700)
    entries = []
    targets = [SERVER, WRAPPER, SUDOERS, PACKAGE / "release.json", *(PACKAGE / name for name in FILES)]
    for i, path in enumerate(targets):
        require(not path.is_symlink(), "unsafe_install_target")
        item = {"path": str(path), "existed": path.exists()}
        if path.exists():
            require(path.is_file(), "unsafe_install_target")
            st = path.stat()
            name = str(i) + ".backup"
            atomic(folder / name, path.read_bytes())
            item.update(backup=name, mode=st.st_mode & 0o777, uid=st.st_uid, gid=st.st_gid)
        entries.append(item)
    # Save this installer independently so rollback cannot be broken by its own upgrade.
    rollback_program = folder / "installer.py"
    atomic(rollback_program, Path(__file__).read_bytes(), 0o700)
    guard = "jarvis-ops-install-guard-" + identity
    info = {"id": identity, "phase": "pending", "files": entries, "guard": guard,
            "service_user": user, "commit": commit, "package_sha256": expected}
    atomic(folder / "state.json", json.dumps(info).encode())
    run(["systemd-run", "--quiet", "--unit=" + guard, "--on-active=180s",
         "--timer-property=AccuracySec=1s", "--property=RuntimeMaxSec=60s",
         "/usr/bin/python3", str(rollback_program), "--restore", identity])
    require(run(["systemctl", "is-active", guard + ".timer"]) == "active", "install_guard_not_active")
    try:
        PACKAGE.mkdir(mode=0o755, parents=True, exist_ok=True)
        os.chmod(PACKAGE, 0o755)
        for name, content in payload.items():
            atomic(PACKAGE / name, content, 0o644)
        atomic(PACKAGE / "release.json", json.dumps(release).encode(), 0o644)
        atomic(WRAPPER, b'#!/bin/sh\nexec /usr/bin/python3 -I /opt/jarvis-ops-network/network_engine.py "$@"\n', 0o755)
        if user != "root":
            sudo = f"{user} ALL=(root) NOPASSWD: {WRAPPER}\n"
            candidate = folder / "sudoers"
            atomic(candidate, sudo.encode(), 0o440)
            run(["visudo", "-cf", str(candidate)])
            atomic(SUDOERS, sudo.encode(), 0o440)
        elif SUDOERS.exists():
            SUDOERS.unlink()
        st = SERVER.stat()
        require(SERVER.read_text() == source, "server_changed_during_install")
        atomic(SERVER, patched.encode(), st.st_mode & 0o777, st.st_uid, st.st_gid)
        # Public SDK registry inspection verifies original and new tools can coexist.
        test = ("import importlib.util,asyncio,sys,inspect;"
                f"sys.path.insert(0,{str(SERVER.parent)!r});"
                f"s=importlib.util.spec_from_file_location('ops_check',{str(SERVER)!r});"
                "m=importlib.util.module_from_spec(s);sys.modules[s.name]=m;s.loader.exec_module(m);"
                f"app=getattr(m,{mcp_name!r});"
                "value=app.get_tools() if hasattr(app,'get_tools') else app.list_tools();"
                "tools=asyncio.run(value) if inspect.isawaitable(value) else value;"
                "names=set(tools) if isinstance(tools,dict) else {x.name for x in tools};"
                "assert {'network_snapshot','prepare_network_change','model_smoke_test','upgrade_ops_extension'}<=names")
        test_argv = [str(VENV_PYTHON), "-c", test]
        if user != "root":
            test_argv = ["runuser", "-u", user, "--", *test_argv]
        run(test_argv, timeout=25)
        # Return the tool result before the transport is restarted.
        reload_unit = "jarvis-ops-reload-" + identity
        run(["systemd-run", "--quiet", "--unit=" + reload_unit, "--on-active=3s",
             "/usr/bin/systemctl", "restart", SERVICE])
        run(["systemd-run", "--quiet", "--unit=jarvis-ops-verify-" + identity, "--on-active=8s",
             "--property=RuntimeMaxSec=70s", "/usr/bin/python3", str(rollback_program), "--verify", identity])
    except Exception:
        restore(identity)
        raise
    return {"installation_id": identity, "phase": "pending_local_verification",
            "guard_seconds": 180, "version": release["version"], "commit": commit,
            "client_tool_discovery": "refresh connection and verify new tool schemas",
            "jarvis_restart": False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--commit")
    parser.add_argument("--package-sha256")
    parser.add_argument("--upgrade", action="store_true")
    parser.add_argument("--restore")
    parser.add_argument("--verify")
    args = parser.parse_args()
    require(os.geteuid() == 0, "run_installer_as_root")
    if args.restore:
        restore(args.restore)
        return {"restored": True}
    if args.verify:
        verify(args.verify)
        folder, info = record(args.verify)
        return {"phase": info["phase"]}
    return install(args.commit or "", args.package_sha256 or "", args.upgrade)


if __name__ == "__main__":
    try:
        print(json.dumps(main(), ensure_ascii=False))
    except Exception as error:
        print(json.dumps({"ok": False, "error": str(error) if type(error) is RuntimeError else type(error).__name__}))
        sys.exit(1)
