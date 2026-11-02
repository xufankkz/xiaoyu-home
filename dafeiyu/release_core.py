from core import db, settings
from core.emotion import vector
from core.cognition import scheduler
from core import api
import json
import release_paths
import time
import signal

def main():
    settings.ensure_dirs()
    db.init_db()
    vector.load()
    api.start(port=settings.app()['bridge']['core_emotion_api_port'])
    scheduler.set_app_mode(False)
    with open(release_paths.path('release/settings.json'), encoding='utf-8') as stream:
        automatic = json.load(stream).get('automation_enabled') is True
    if automatic:
        scheduler.start()
    stopped = False
    def stop(*_):
        nonlocal stopped
        stopped = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        while not stopped:
            time.sleep(0.3)
    finally:
        scheduler.stop()
        api.stop()
        db.close()

if __name__ == '__main__':
    main()
