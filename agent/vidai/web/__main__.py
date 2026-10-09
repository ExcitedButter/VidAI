"""`python -m vidai.web` — start the local MasSurge UI."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from vidai.config import VidaiSettings
from vidai.web.server import serve


def main() -> int:
    parser = argparse.ArgumentParser(description="MasSurge local web UI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--data-dir", default=None)
    parser.add_argument("--mock", action="store_true", help="offline: canned LLM + synthetic clips")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = VidaiSettings.from_env()
    if args.data_dir:
        settings.data_dir = Path(args.data_dir).expanduser()
    if args.mock:
        settings.mock = True
    server = serve(settings, args.host, args.port)
    print(f"MasSurge UI: http://{args.host}:{server.server_port}   (Ctrl-C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
