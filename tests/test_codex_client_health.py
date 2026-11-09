import asyncio
import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location('client_health_patch', ROOT / 'patches/patch_qwenpaw_codex_client_health.py')
patch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patch)
CLIENT = (ROOT / 'tests/fixtures/codex_app_server_pinned.py').read_text()
ADAPTER = (ROOT / 'tests/fixtures/codex_adapter_pinned.py').read_text()


def load_client(patched):
    name = 'codex_health_fixture'
    package = types.ModuleType(name)
    package.__path__ = []
    discovery = types.ModuleType(name + '.discovery')
    discovery.CodexBinaryResolution = object
    discovery.resolve_codex_binary_info = lambda binary: types.SimpleNamespace(path='/fake/codex')
    module = types.ModuleType(name + '.app_server')
    module.__package__ = name
    with mock.patch.dict(sys.modules, {name: package, name + '.discovery': discovery}):
        exec(compile(patch.render_client(CLIENT) if patched else CLIENT, '<client-fixture>', 'exec'), module.__dict__)
    return module


class Process:
    def __init__(self, limit=65536, large=False, drop=None):
        self.returncode = None
        self.stdout = asyncio.StreamReader(limit=limit)
        self.stderr = asyncio.StreamReader(limit=limit)
        self.stdin = self
        self.sent = []
        self.large, self.drop = large, drop
    def write(self, data):
        request = json.loads(data)
        self.sent.append(request)
        if request.get('method') and 'id' in request and request['method'] != self.drop:
            result = {'text': 'x' * 70000} if self.large and request['method'] == 'thread/read' else {}
            self.stdout.feed_data(json.dumps({'id': request['id'], 'result': result}).encode() + b'\n')
    async def drain(self):
        await asyncio.sleep(0)
    def terminate(self):
        self.returncode = 0
        self.stdout.feed_eof()
        self.stderr.feed_eof()
    kill = terminate
    async def wait(self):
        return self.returncode


class ClientHealthTests(unittest.IsolatedAsyncioTestCase):
    async def test_original_reuses_live_process_after_64k_reader_failure(self):
        module = load_client(False)
        client = module.CodexAppServerClient()
        process = Process()
        client._process = process
        client._reader_task = asyncio.create_task(client._read_stdout())
        process.stdout.feed_data(json.dumps({'id': 999, 'result': 'x' * 70000}).encode() + b'\n')
        with self.assertRaises(ValueError):
            await client._reader_task
        await client.start()
        with self.assertRaises(asyncio.TimeoutError):
            await asyncio.wait_for(client.request('thread/start'), timeout=0.02)
        self.assertIsNone(process.returncode)

    async def test_patched_transport_reads_large_response_and_reuses_healthy_client(self):
        module = load_client(True)
        client = module.CodexAppServerClient()
        async def create(*args, **kwargs):
            return Process(limit=kwargs['limit'], large=True)
        with mock.patch.object(module.asyncio, 'create_subprocess_exec', side_effect=create) as spawn:
            await client.start()
            result = await asyncio.wait_for(client.request('thread/read'), timeout=1)
            self.assertEqual(len(result['text']), 70000)
            await client.start()
            self.assertEqual(spawn.call_count, 1)
            self.assertFalse(client._reader_task.done())
            await client.stop()

    async def test_dead_reader_restarts_process_and_advances_epoch(self):
        module = load_client(True)
        client = module.CodexAppServerClient()
        async def create(*args, **kwargs):
            return Process(limit=kwargs['limit'])
        with mock.patch.object(module.asyncio, 'create_subprocess_exec', side_effect=create) as spawn:
            await client.start()
            old, epoch = client._process, client._jarvis_generation
            client._reader_task.cancel()
            await asyncio.gather(client._reader_task, return_exceptions=True)
            await client.start()
            self.assertIsNot(client._process, old)
            self.assertEqual(old.returncode, 0)
            self.assertGreater(client._jarvis_generation, epoch)
            await asyncio.wait_for(client.request('account/read'), timeout=1)
            self.assertEqual(spawn.call_count, 2)
            await client.stop()

    async def test_waiting_approval_does_not_block_rpc_and_decision_is_unchanged(self):
        module = load_client(True)
        client = module.CodexAppServerClient()
        waiting, release = asyncio.Event(), asyncio.Event()
        async def handler(message):
            waiting.set()
            await release.wait()
            return {'decision': 'decline'}
        async def create(*args, **kwargs):
            return Process(limit=kwargs['limit'])
        client.set_server_request_handler(handler)
        with mock.patch.object(module.asyncio, 'create_subprocess_exec', side_effect=create):
            await client.start()
            process = client._process
            process.stdout.feed_data(json.dumps({'id': 1000, 'method': 'item/commandExecution/requestApproval', 'params': {}}).encode() + b'\n')
            await asyncio.wait_for(waiting.wait(), timeout=1)
            await asyncio.wait_for(client.request('account/read'), timeout=1)
            self.assertFalse(any(p.get('id') == 1000 for p in process.sent))
            release.set()
            await asyncio.gather(*tuple(client._jarvis_server_tasks))
            self.assertIn({'id': 1000, 'result': {'decision': 'decline'}}, process.sent)
            await client.stop()

    async def test_prepare_rpc_timeout_stops_client_and_names_the_stage(self):
        module = load_client(True)
        client = module.CodexAppServerClient()
        real_wait_for = asyncio.wait_for
        async def bounded(awaitable, timeout):
            return await real_wait_for(awaitable, 0.02 if timeout == 30 else timeout)
        async def create(*args, **kwargs):
            return Process(limit=kwargs['limit'], drop='thread/start')
        with mock.patch.object(module.asyncio, 'create_subprocess_exec', side_effect=create):
            await client.start()
            process = client._process
            with mock.patch.object(module.asyncio, 'wait_for', side_effect=bounded):
                with self.assertRaisesRegex(module.CodexAppServerError, 'thread/start timed out'):
                    await client.request('thread/start')
            self.assertIsNone(client._process)
            self.assertEqual(process.returncode, 0)
            self.assertFalse(client._pending)


class PatchBoundaryTests(unittest.TestCase):
    def test_sources_match_production_and_patch_is_strict_and_idempotent(self):
        self.assertEqual(hashlib.sha256(CLIENT.encode()).hexdigest(), patch.CLIENT_SHA256)
        self.assertEqual(hashlib.sha256(ADAPTER.encode()).hexdigest(), patch.ADAPTER_SHA256)
        for source, render in ((CLIENT, patch.render_client), (ADAPTER, patch.render_adapter)):
            result = render(source)
            self.assertEqual(render(result), result)
            compile(result, '<patched-fixture>', 'exec')
            with self.assertRaisesRegex(ValueError, 'unsupported_'):
                render(source + '# changed\n')
        with tempfile.TemporaryDirectory() as directory:
            client, adapter = Path(directory) / 'client.py', Path(directory) / 'adapter.py'
            client.write_text(CLIENT)
            adapter.write_text('unknown source')
            with self.assertRaises(ValueError):
                patch.patch(client, adapter)
            self.assertEqual(client.read_text(), CLIENT)
            self.assertEqual(adapter.read_text(), 'unknown source')

    def test_process_epoch_invalidates_loaded_thread_cache(self):
        text = patch.render_adapter(ADAPTER)
        node = next(n for n in ast.walk(ast.parse(text)) if isinstance(n, ast.AsyncFunctionDef) and n.name == '_thread_for_session')
        method = ''.join(text.splitlines(keepends=True)[node.lineno - 1:node.end_lineno])
        async def write_json(*args):
            pass
        namespace = {'asyncio': asyncio, 'CodexAppServerError': RuntimeError, 'write_json_atomic_async': write_json}
        exec('from __future__ import annotations\nclass Adapter:\n' + method, namespace)
        async def run():
            instance = namespace['Adapter']()
            client = types.SimpleNamespace(_jarvis_generation=2, request=mock.AsyncMock(return_value={}))
            instance._session_lock = asyncio.Lock()
            instance._threads = {'session': 'thread'}
            instance._loaded_threads = {(hash((id(client), 1)), 'thread')}
            instance._session_path = Path('/unused')
            result = await instance._thread_for_session(client, 'session', Path('/unused'), {})
            self.assertEqual(result, 'thread')
            client.request.assert_awaited_once_with('thread/resume', {'threadId': 'thread'})
            await instance._thread_for_session(client, 'session', Path('/unused'), {})
            self.assertEqual(client.request.await_count, 1)
        asyncio.run(run())


if __name__ == '__main__':
    unittest.main()
