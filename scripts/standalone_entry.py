"""PyInstaller entry point for the standalone executable."""

from ai_config.cli import standalone_main

if __name__ == "__main__":
    raise SystemExit(standalone_main())
