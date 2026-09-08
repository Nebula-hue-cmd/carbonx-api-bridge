"""PyInstaller entry point (builds cleaner than carbonx_bridge/__main__.py)."""

from carbonx_bridge.server import main

if __name__ == "__main__":
    main()