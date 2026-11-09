#!/usr/bin/env python3
"""Repair the pinned Codex RPC transport without changing approval decisions."""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
from pathlib import Path
import textwrap

CLIENT_SHA256 = 'e341ed34916909a99ad8ea54e5bbf5396327ce8c11c498491d8265b6b4a32333'
ADAPTER_SHA256 = 'a92640b5180613fd831d95190bc66e7e89cbe87d2e40de200a99370485867845'
MARKER = '# JARVIS_CODEX_CLIENT_HEALTH_V1'
EPOCH_MARKER = '# JARVIS_CODEX_PROCESS_EPOCH_V1'

START = '''
async def start(self) -> None:
    """Require initialized live readers, not merely a live subprocess."""
    async with self._start_lock:
        healthy = (self._process is not None and self._process.returncode is None
                   and getattr(self, '_jarvis_ready', False)
                   and self._reader_task is not None and not self._reader_task.done()
                   and self._stderr_task is not None and not self._stderr_task.done())
        if healthy:
            return
        await self._stop_process()
        resolution = self.binary_resolution
        if resolution is None:
            raise CodexAppServerError('Codex CLI not found')
        self._process = await asyncio.create_subprocess_exec(
            str(resolution.path), 'app-server',
            *[item for override in self._config_overrides for item in ('-c', override)],
            '--listen', 'stdio://', stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env={**os.environ, **self._runtime_environment}, limit=16 * 1024 * 1024)
        self._jarvis_generation = getattr(self, '_jarvis_generation', 0) + 1
        self._jarvis_server_tasks = set()
        self._reader_task = asyncio.create_task(self._read_stdout())
        self._stderr_task = asyncio.create_task(self._read_stderr())
        try:
            await asyncio.wait_for(self.request('initialize', {'clientInfo': {
                'name': 'qwenpaw', 'title': 'QwenPaw', 'version': '1'}}), timeout=20)
            await self.notify('initialized', {})
            self._jarvis_ready = True
        except BaseException as error:
            await self._stop_process()
            if isinstance(error, asyncio.TimeoutError):
                raise CodexAppServerError('Codex initialize timed out after 20 seconds; client stopped') from error
            raise
'''

REQUEST = '''
async def request(self, method, params=None):
    if method != 'initialize':
        await self.start()
    elif self._process is None or self._process.returncode is not None:
        raise CodexAppServerError('Codex app-server is not running')
    self._request_id += 1
    request_id = self._request_id
    future = asyncio.get_running_loop().create_future()
    self._pending[request_id] = future
    logger.info('Codex RPC method=%s state=start', method)
    try:
        await self._send({'method': method, 'id': request_id, 'params': params or {}})
        if method in {'skills/extraRoots/set', 'thread/start', 'thread/resume', 'account/read'}:
            try:
                result = await asyncio.wait_for(future, timeout=30)
            except asyncio.TimeoutError as error:
                await self.stop()
                raise CodexAppServerError('Codex ' + method + ' timed out after 30 seconds; client stopped') from error
        else:
            result = await future
        logger.info('Codex RPC method=%s state=done', method)
        return result
    finally:
        self._pending.pop(request_id, None)
'''

STOP = '''
async def stop(self):
    async with self._start_lock:
        await self._stop_process()

async def _stop_process(self):
    process = self._process
    self._process = None
    self._jarvis_ready = False
    tasks = [self._reader_task, self._stderr_task, *getattr(self, '_jarvis_server_tasks', set())]
    for task in tasks:
        if task is not None and not task.done() and task is not asyncio.current_task():
            task.cancel()
    self._fail_pending(CodexAppServerError('Codex app-server stopped'))
    if process is not None and process.returncode is None:
        try:
            process.terminate()
            await asyncio.wait_for(process.wait(), timeout=5)
        except asyncio.TimeoutError:
            process.kill()
            await asyncio.wait_for(process.wait(), timeout=2)
        except ProcessLookupError:
            pass
    waiting = [task for task in tasks if task is not None and task is not asyncio.current_task()]
    if waiting:
        try:
            await asyncio.wait_for(asyncio.gather(*waiting, return_exceptions=True), timeout=2)
        except asyncio.TimeoutError:
            pass

def _jarvis_server_request_done(self, task):
    self._jarvis_server_tasks.discard(task)
    if not task.cancelled():
        error = task.exception()
        if error is not None:
            logger.warning('Codex approval response failed: %s', type(error).__name__)
'''

STDOUT = '''
async def _read_stdout(self):
    process = self._process
    if process is None or process.stdout is None:
        return
    try:
        while line := await process.stdout.readline():
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                logger.warning('Codex emitted invalid JSON')
                continue
            await self._dispatch(message)
    except asyncio.CancelledError:
        raise
    except Exception as error:
        logger.warning('Codex response reader failed: %s', type(error).__name__)
        self._fail_pending(CodexAppServerError('Codex response reader failed (' + type(error).__name__ + ')'))
    finally:
        self._fail_pending(CodexAppServerError('Codex app-server response stream closed'))
'''

STDERR = '''
async def _read_stderr(self):
    process = self._process
    if process is None or process.stderr is None:
        return
    try:
        while line := await process.stderr.readline():
            value = line.decode(errors='replace').lower()
            if any(word in value for word in ('error', 'warn', 'failed', 'timed out', 'panic')):
                category = ('tls' if 'tls' in value else 'model_catalog' if 'refresh available models' in value
                            else 'mcp' if 'mcp' in value else 'authentication' if '403' in value or '401' in value
                            else 'timeout' if 'timed out' in value else 'runtime')
                logger.warning('Codex stderr category=%s', category)
    except asyncio.CancelledError:
        raise
    except Exception as error:
        logger.warning('Codex stderr reader failed: %s', type(error).__name__)
'''


def replace_methods(source, replacements):
    tree = ast.parse(source)
    edits = []
    lines = source.splitlines(keepends=True)
    for name, replacement in replacements.items():
        nodes = [n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == name]
        if len(nodes) != 1:
            raise ValueError('unsupported_method_' + name)
        node = nodes[0]
        edits.append((node.lineno - 1, node.end_lineno,
                      textwrap.indent(textwrap.dedent(replacement).strip() + '\n', ' ' * node.col_offset)))
    for start, end, replacement in sorted(edits, reverse=True):
        lines[start:end] = [replacement]
    result = ''.join(lines)
    compile(result, '<patched-client>', 'exec')
    return result


def render_client(source):
    if MARKER in source:
        return source
    if hashlib.sha256(source.encode()).hexdigest() != CLIENT_SHA256:
        raise ValueError('unsupported_client_source_no_changes')
    result = replace_methods(source, {'start': START, 'request': REQUEST, 'stop': STOP,
        '_read_stdout': STDOUT, '_read_stderr': STDERR})
    old = '            await self._handle_server_request(message)\n            return\n'
    new = '''            task = asyncio.create_task(self._handle_server_request(message))
            self._jarvis_server_tasks.add(task)
            task.add_done_callback(self._jarvis_server_request_done)
            return
'''
    if result.count(old) != 1:
        raise ValueError('unsupported_dispatch_no_changes')
    result = result.replace(old, new)
    result += '\n' + MARKER + '\n'
    compile(result, '<patched-client>', 'exec')
    return result


def render_adapter(source):
    if EPOCH_MARKER in source:
        return source
    if hashlib.sha256(source.encode()).hexdigest() != ADAPTER_SHA256:
        raise ValueError('unsupported_adapter_source_no_changes')
    old = '(id(client), thread_id)'
    if source.count(old) != 2:
        raise ValueError('unsupported_thread_cache_no_changes')
    source = source.replace(old, '(hash((id(client), getattr(client, "_jarvis_generation", 0))), thread_id)')
    source += '\n' + EPOCH_MARKER + '\n'
    compile(source, '<patched-adapter>', 'exec')
    return source


def patch(client, adapter):
    values = [(client, render_client(client.read_text())), (adapter, render_adapter(adapter.read_text()))]
    for path, value in values:
        path.write_text(value)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--client', type=Path)
    parser.add_argument('--adapter', type=Path)
    args = parser.parse_args()
    client = args.client or Path(importlib.util.find_spec('qwenpaw.harnesses.codex.app_server').origin)
    adapter = args.adapter or Path(importlib.util.find_spec('qwenpaw.harnesses.codex.adapter').origin)
    patch(client, adapter)
    print('codex_client_health=installed')


if __name__ == '__main__':
    main()
