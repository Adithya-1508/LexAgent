"""CLI entrypoint for corpus operations."""

import sys

from .acquisition import main

if __name__ == "__main__":
    sys.exit(main())
