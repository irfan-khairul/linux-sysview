"""Command-line entry point: python -m sysview"""

import argparse
import sys

from . import __version__
from .sampler import Sampler
from .server import make_server


def parse_args(argv):
    parser = argparse.ArgumentParser(
        prog="sysview",
        description="Web-based Linux system resource and process viewer.",
    )
    parser.add_argument("--host", default="0.0.0.0",
                        help="address to bind (default: 0.0.0.0; use 127.0.0.1 for localhost only)")
    parser.add_argument("--port", type=int, default=8080,
                        help="port to listen on (default: 8080)")
    parser.add_argument("--interval", type=float, default=2.0,
                        help="default UI refresh interval in seconds (default: 2)")
    parser.add_argument("--version", action="version", version="sysview %s" % __version__)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)

    sampler = Sampler(interval=1.0)
    sampler.start()

    try:
        try:
            httpd = make_server(args.host, args.port, sampler)
        except OSError as exc:
            print("Cannot bind %s:%d — %s" % (args.host, args.port, exc), file=sys.stderr)
            return 1

        print("sysview %s serving on http://%s:%d" % (__version__, args.host, args.port))
        print("Press Ctrl+C to stop.")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down.")
        finally:
            httpd.shutdown()
            httpd.server_close()
        return 0
    finally:
        sampler.stop()


if __name__ == "__main__":
    sys.exit(main())
