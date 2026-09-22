"""Portable folder entrypoint: python -m ttaem_cva."""
import argparse
from pathlib import Path
from .acquire import prepare, video_number
from .llm_gateway import PendingAgentResponse

def main():
    parser = argparse.ArgumentParser(description="TTaem CVA: local VOD summary workflow")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("prepare", "generate"):
        item = sub.add_parser(name)
        item.add_argument("url")
    item = sub.add_parser("transcribe")
    item.add_argument("url")
    item.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    item = sub.add_parser("serve")
    item.add_argument("--port", type=int, default=8767)
    args = parser.parse_args()
    if args.command == "serve":
        from .server import serve
        serve(args.port)
        return 0
    root = Path(__file__).resolve().parents[1]
    run = root / "runs" / video_number(args.url)
    if args.command == "prepare":
        prepare(args.url, run)
    elif args.command == "transcribe":
        from .transcribe import transcribe
        transcribe(run, device=args.device)
    elif args.command == "generate":
        from .workflow import generate
        try:
            generate(run)
            from .acquire import save_json
            from .public_report import project, render
            import json
            data = project(json.loads((run / "result-private.json").read_text(encoding="utf-8")))
            save_json(run / "public-report.json", data)
            print("Summary ready. Start: python -m ttaem_cva serve")
            print("New generated draft: http://127.0.0.1:8767/preview/" + data["video_no"] + "/generated.html")
            if (run / "current.json").exists():
                print("An existing reviewed version is preserved. The review workspace still shows that saved version.")
            print("Review: http://127.0.0.1:8767/report-workspace?base=" + data["video_no"])
        except PendingAgentResponse as pending:
            print("AGENT_ACTION_REQUIRED:", pending.path)
            print("Read that request, write its response file, then resume this command. No separate model API is needed.")
            return 10
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
