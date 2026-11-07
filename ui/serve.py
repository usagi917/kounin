"""起動の入口。127.0.0.1 だけで待ち受け、それ以外を拒否する（INV20）。"""

import argparse
import sys

import uvicorn

from ui.server import create_app


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8020)
    args = parser.parse_args()
    if args.host != "127.0.0.1":
        sys.exit("kounin は 127.0.0.1 だけで待ち受けます")
    print(f"kounin を開きました： http://127.0.0.1:{args.port}/")
    uvicorn.run(create_app(), host=args.host, port=args.port, log_level="warning", access_log=False,
                server_header=False)


if __name__ == "__main__":
    main()
