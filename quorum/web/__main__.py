"""``python -m quorum.web`` — launch the live deliberation UI."""

from __future__ import annotations

import argparse


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Quorum web UI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true", help="auto-reload on code changes (dev)")
    parser.add_argument(
        "--insecure-public-bind", action="store_true",
        help="allow a non-loopback bind without authentication (unsafe; use only behind your own auth proxy)",
    )
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost", "::1"} and not args.insecure_public_bind:
        raise SystemExit(
            "Refusing a non-loopback bind: Quorum's local UI has no user authentication.\n"
            "Use the default localhost bind, or explicitly pass --insecure-public-bind "
            "only behind an authenticated, body-limited reverse proxy."
        )

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
