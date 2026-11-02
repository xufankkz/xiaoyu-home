"""Windows release entry point. All worker processes share one isolated instance."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import secrets
import signal
import sys
import time
from home_runtime import APP_NAME, VERSION
from home_runtime.instance import default_data_dir, initialize, environment
from home_runtime.files import read_json, atomic_json

def main():
    parser = argparse.ArgumentParser(description=APP_NAME)
    parser.add_argument('--data-dir', type=Path, default=default_data_dir())
    parser.add_argument('--worker', choices=('core', 'model', 'qzone', 'diary', 'memes', 'media', 'web'))
    parser.add_argument('--serve', action='store_true')
    parser.add_argument('--browser', action='store_true')
    parser.add_argument('--stop', action='store_true')
    args = parser.parse_args()
    data_dir = args.data_dir.resolve()
    if args.stop:
        data_dir.mkdir(parents=True, exist_ok=True)
        atomic_json(data_dir / 'stop.json', {'stop': True})
        return 0
    if args.worker:
        from home_runtime.worker import run
        return run(args.worker)
    # QLockFile distinguishes this instance from the existing production and preview.
    from PySide6.QtCore import QLockFile
    data_dir.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(data_dir / 'instance.lock'))
    lock.setStaleLockTime(30000)
    if not lock.tryLock(200):
        raise RuntimeError('这份小鱼之家已在运行，请打开已有窗口。')
    supervisor = None
    try:
        (data_dir / 'stop.json').unlink(missing_ok=True)
        workspace, runtime_path, runtime = initialize(data_dir)
        runtime['launch_token'] = secrets.token_urlsafe(32)
        atomic_json(runtime_path, runtime)
        env = environment(workspace, runtime_path, runtime)
        os.environ.update(env)
        for folder in (workspace / 'dafeiyu', workspace / 'xiaoyu-web'):
            sys.path.insert(0, str(folder))
        from home_runtime.supervisor import Supervisor
        supervisor = Supervisor(data_dir, workspace, runtime_path, runtime)
        supervisor.start_all()
        url = 'http://127.0.0.1:' + str(runtime['ports']['web']) + '/launch?ticket=' + runtime['launch_token']
        if args.serve:
            print(json.dumps({'edition': 'beta', 'version': VERSION, 'url': url, 'data_dir': str(data_dir)}, ensure_ascii=False), flush=True)
            stopped = False
            def stop(*_):
                nonlocal stopped
                stopped = True
            signal.signal(signal.SIGINT, stop)
            signal.signal(signal.SIGTERM, stop)
            while not stopped:
                if (data_dir / 'stop.json').exists():
                    break
                supervisor.tick()
                time.sleep(0.3)
            return 0
        from PySide6.QtWidgets import QApplication
        from PySide6.QtCore import QTimer
        from ui.web_window import SharedWebWindow
        app = QApplication(sys.argv[:1])
        app.setApplicationName('XiaoyuHomeBeta')
        app.setOrganizationName('XiaoyuHome')
        window = SharedWebWindow(data_dir=workspace / 'dafeiyu/data', url=url)
        window.setWindowTitle(APP_NAME + ' · ' + VERSION + ' 测试版')
        window.show()
        timer = QTimer(app)
        def tick():
            if (data_dir / 'stop.json').exists():
                app.quit()
            else:
                supervisor.tick()
        timer.timeout.connect(tick)
        timer.start(500)
        if args.browser:
            QTimer.singleShot(700, window.open_browser)
        return app.exec()
    finally:
        if supervisor:
            supervisor.stop()
        lock.unlock()

if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception:
        import traceback
        try:
            position = sys.argv.index("--data-dir")
            error_dir = Path(sys.argv[position + 1]).resolve()
        except (ValueError, IndexError):
            error_dir = default_data_dir()
        error_dir.mkdir(parents=True, exist_ok=True)
        (error_dir / 'startup-error.log').write_text(traceback.format_exc(), encoding='utf-8')
        raise
