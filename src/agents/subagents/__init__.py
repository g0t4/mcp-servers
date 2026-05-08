import argparse
import asyncio

from subagents.server import serve

def main():

    parser = argparse.ArgumentParser(description="langchain subagents server!")

    args = parser.parse_args()
    asyncio.run(serve())

if __name__ == "__main__":
    main()
