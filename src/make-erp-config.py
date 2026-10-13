#!/usr/bin/env python3
# ABOUT
# Writes erp-config.json: the LVG ERP server this build of Artisan talks to. Run by the build scripts right before PyInstaller; the
# PyInstaller specs refuse to build without the file, so no installer can be made that does not know its server.
#
#   ERP_API_BASE_URL=https://erp-api.example.vn/api ERP_ENVIRONMENT=production ./build-macos3.sh
#   python3 make-erp-config.py --url https://erp-api-stg.example.vn/api --env staging
#
# --url / --env / --socket-url default to the environment variables ERP_API_BASE_URL / ERP_ENVIRONMENT / ERP_SOCKET_URL.
# There is deliberately no default server and no default environment. The environment ("production", "staging", ...) keeps the logins,
# the files waiting to be sent and the remembered roaster of two builds on one PC apart; production is the only one without a tag in
# the title bar.

import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from erp import config # noqa: E402 # pylint: disable=wrong-import-position


def main(argv:list[str]|None = None) -> int:
    parser = argparse.ArgumentParser(description='Write erp-config.json for a build of Artisan.')
    parser.add_argument('--url', default=os.environ.get(config.ENV_API_URL), help='ERP API base URL (https, e.g. https://erp-api.example.vn/api)')
    parser.add_argument('--env', default=os.environ.get(config.ENV_ENVIRONMENT), help='environment name: production, staging, ...')
    parser.add_argument('--socket-url', default=os.environ.get(config.ENV_SOCKET_URL), help='Socket.IO origin if it is not the API host (optional)')
    parser.add_argument('--out', default=str(HERE / config.CONFIG_FILENAME), help='file to write')
    args = parser.parse_args(argv)

    try:
        if not args.url or not args.url.strip():
            raise config.ConfigError(f'No ERP server: give --url or set {config.ENV_API_URL}.')
        if not args.env or not args.env.strip():
            raise config.ConfigError(f'No environment: give --env or set {config.ENV_ENVIRONMENT} (production, staging, ...).')
        content:dict[str, str] = {
            'apiBaseUrl': config.validate_api_url(args.url),
            'environment': config.validate_environment(args.env),
        }
        if args.socket_url and args.socket_url.strip():
            content['socketUrl'] = config.validate_api_url(args.socket_url)
    except config.ConfigError as e:
        print(f'make-erp-config: {e.message}', file=sys.stderr)
        return 2

    Path(args.out).write_text(json.dumps(content, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(f'make-erp-config: {args.out}: {content["environment"]} -> {content["apiBaseUrl"]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
