"""Small stdlib-only HTTP probe for runtimes that split quoted exec arguments."""

import argparse
import sys
from urllib.request import urlopen


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("url", nargs="?", default="http://127.0.0.1:8081/livez")
    args = parser.parse_args()
    try:
        with urlopen(args.url, timeout=2) as response:
            return int(response.status != 200)
    except Exception as exc:
        print(type(exc).__name__, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
