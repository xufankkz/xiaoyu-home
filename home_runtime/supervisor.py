"""Manage only child processes spawned by this release instance."""
from pathlib import Path
import os
import signal
import subprocess
import sys
import time
import urllib.request
from .instance import source_root, environment
from .files import atomic_json, read_json
from .process_job import ProcessJob
import re
import socket
from .integrations import Integrations, components
from .credentials import Credentials

class Supervisor:
    def __init__(self, data_dir, workspace, runtime_path, runtime):
        self.data_dir, self.workspace = Path(data_dir), Path(workspace)
        self.runtime_path, self.runtime = runtime_path, runtime
        self.env = environment(workspace, runtime_path, runtime)
        self.children, self.logs = {}, []
        self.job = ProcessJob()
        self.control_id = None
        self.automatic = None
        self.last_tick = 0
        self.errors = {}

    def command(self, name):
        if name == 'bridge':
            return [str(source_root() / 'runtime/node/node.exe'), str(self.workspace / 'qq-bridge/src/bridge.js')]
        if name == 'dsh':
            from .dsh import prepare
            return prepare(self.workspace, self.runtime, self.env)
        if getattr(sys, 'frozen', False):
            return [sys.executable, '--worker', name, '--data-dir', str(self.data_dir)]
        return [sys.executable, str(source_root() / 'launcher.py'), '--worker', name, '--data-dir', str(self.data_dir)]

    def start(self, name):
        if name in self.children and self.children[name].poll() is None:
            return
        log_dir = self.workspace / 'release/logs'
        log_dir.mkdir(parents=True, exist_ok=True)
        output = (log_dir / (name + '.log')).open('wb' if name == 'dsh' else 'ab', buffering=0)
        self.logs.append(output)
        self.children[name] = subprocess.Popen(self.command(name), env=self.env, cwd=self.workspace,
            stdout=output, stderr=output, stdin=subprocess.DEVNULL,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.job.add(self.children[name])

    def start_all(self):
        for name in ('model', 'core', 'qzone', 'diary', 'memes', 'media', 'web'):
            self.start(name)
        deadline = time.monotonic() + 35
        while time.monotonic() < deadline:
            failed = {name: proc.returncode for name, proc in self.children.items() if proc.poll() is not None}
            if failed:
                raise RuntimeError('发行实例服务启动失败：' + ', '.join(failed) + '。请查看当前实例的 release/logs。')
            try:
                request = urllib.request.Request('http://127.0.0.1:' + str(self.runtime['ports']['web']) + '/api/release/health',
                                                 headers={'X-Xiaoyu-Internal': self.runtime['internal_token']})
                with urllib.request.urlopen(request, timeout=1) as response:
                    if response.status == 200:
                        self.reconcile(force=True)
                        return
            except OSError:
                pass
            time.sleep(0.2)
        raise RuntimeError('发行实例启动等待超时，请查看当前实例日志。')

    def stop_one(self, name):
        process = self.children.pop(name, None)
        if process and process.poll() is None:
            import psutil
            try:
                descendants = psutil.Process(process.pid).children(recursive=True)
            except psutil.Error:
                descendants = []
            process.terminate()
            for child in descendants:
                try:
                    child.terminate()
                except psutil.Error:
                    pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()

    def reconcile(self, force=False):
        control = read_json(self.workspace / 'release/control.json')
        if not force and control.get('id') == self.control_id:
            return
        self.control_id = control.get('id')
        store = Integrations(self.workspace)
        store.apply_files()
        self.runtime = read_json(self.runtime_path)
        settings = read_json(self.workspace / 'release/settings.json')
        self.env = environment(self.workspace, self.runtime_path, self.runtime)
        secrets = Credentials(self.workspace / 'release/credentials.dpapi')
        self.env.update(XIAOYU_ONEBOT_TOKEN=secrets.get('onebot:token'),
            XIAOYU_ONEBOT_WS_TOKEN=secrets.get('onebot:ws_token') or secrets.get('onebot:token'),
            XIAOYU_DSH_TOKEN=secrets.get('dsh:token'), XIAOYU_GATEWAY_TOKEN=self.runtime['internal_token'],
            XIAOYU_INSTANCE_CHAT_KEY=self.runtime['internal_token'], DSH_TELEMETRY_DISABLED='1', DSH_TELEMETRY_MODE='DISABLED')
        self.stop_one('bridge')
        self.stop_one('dsh')
        self.errors = {}
        if control.get('action') == 'migration-reload' or (self.automatic is not None and self.automatic != settings.get('automation_enabled', False)):
            self.stop_one('core')
            self.start('core')
        self.automatic = settings.get('automation_enabled', False)
        integration = settings.get('integrations', {})
        dsh, onebot = integration.get('dsh', {}), integration.get('onebot', {})
        if dsh.get('enabled') and dsh.get('managed'):
            try:
                self.start('dsh')
            except (OSError, ValueError) as exc:
                self.errors['dsh'] = str(exc)
        if onebot.get('enabled'):
            if not dsh.get('enabled'):
                self.errors['bridge'] = '请同时启用 DSH 连接。'
            elif not onebot.get('ws_url') or not onebot.get('http_url'):
                self.errors['bridge'] = '请填写 OneBot HTTP 与正向 WebSocket 地址。'
            elif not onebot.get('allow_private') and not onebot.get('allow_groups'):
                self.errors['bridge'] = '请先明确配置联系人白名单。'
            elif not dsh.get('managed'):
                self.start('bridge')
        self.tick(force=True)

    def tick(self, force=False):
        if not force and time.monotonic() - self.last_tick < 1:
            return
        self.last_tick = time.monotonic()
        self.reconcile() if not force else None
        settings = read_json(self.workspace / 'release/settings.json')
        integrations = settings.get('integrations', {})
        dsh = self.children.get('dsh')
        if dsh and dsh.poll() is None and integrations.get('onebot', {}).get('enabled') and 'bridge' not in self.children and 'bridge' not in self.errors:
            log = self.workspace / 'release/logs/dsh.log'
            tokens = re.findall(r'[?&]token=([A-Za-z0-9_-]{16,})', log.read_text(encoding='utf-8', errors='replace'))
            if tokens:
                self.env['XIAOYU_DSH_TOKEN'] = tokens[-1]
                try:
                    with socket.create_connection(('127.0.0.1', self.runtime['ports']['dsh']), timeout=0.1):
                        pass
                    self.start('bridge')
                except OSError:
                    pass
        atomic_json(self.workspace / 'release/processes.json', {
            'services': {name: {'running': process.poll() is None, 'pid': process.pid, 'exit_code': process.poll()}
                         for name, process in self.children.items()}, 'errors': self.errors})

    def stop(self):
        for process in reversed(list(self.children.values())):
            if process.poll() is None:
                process.terminate()
        for process in self.children.values():
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        for stream in self.logs:
            stream.close()
        self.children.clear()
        self.job.close()
