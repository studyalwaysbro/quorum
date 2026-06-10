"""``python -m quorum.web`` — launch the live deliberation UI."""

from __future__ import annotations

import argparse


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Quorum web UI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true", help="auto-reload on code changes (dev)")
    args = parser.parse_args()

    try:
        import uvicorn
    except ModuleNotFoundError:
        raise SystemExit(
            "The web UI needs extra deps. Install them with:\n"
            "    pip install 'quorum-council[web]'\n"
            "or:  pip install fastapi uvicorn"
        )

    print(f"\n  Quorum UI → http://{args.host}:{args.port}\n")
    uvicorn.run("quorum.web.server:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
