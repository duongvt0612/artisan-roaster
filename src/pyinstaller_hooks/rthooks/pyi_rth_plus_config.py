import json
import logging
import os
import sys
from pathlib import Path


_log = logging.getLogger(__name__)


ENV_FILENAME = 'artisan-plus-env.json'
ENV_KEYS = (
    'ARTISAN_PLUS_API_BASE_URL',
    'ARTISAN_PLUS_WEB_BASE_URL',
)


def _load_packaged_plus_env() -> None:
    if not getattr(sys, 'frozen', False):
        return

    meipass = getattr(sys, '_MEIPASS', None)
    if not meipass:
        return

    env_file = Path(meipass) / ENV_FILENAME
    if not env_file.is_file():
        return

    try:
        payload = json.loads(env_file.read_text(encoding='utf-8'))
    except Exception as e:
        _log.warning('Failed to load packaged plus env from %s: %s', env_file.name, e)
        return

    if not isinstance(payload, dict):
        return

    for key in ENV_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value:
            os.environ.setdefault(key, value)


_load_packaged_plus_env()
