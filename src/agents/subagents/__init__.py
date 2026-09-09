import argparse
import asyncio
import os

from subagents.server import serve

def main():

    parser = argparse.ArgumentParser(description="langchain subagents server!")
    parser.add_argument(
        "--workdir",
        default=os.environ.get("MCP_SUBAGENTS_WORKDIR"),
        help=(
            "Working directory for subagents. Falls back to the "
            "MCP_SUBAGENTS_WORKDIR environment variable."
        ),
    )
    args = parser.parse_args()
    asyncio.run(serve(workdir=args.workdir))

if __name__ == "__main__":
    main()
