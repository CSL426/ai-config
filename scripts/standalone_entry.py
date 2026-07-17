"""PyInstaller entry point for the standalone executable."""

from ai_config.cli import console_main


if __name__ == "__main__":
    raise SystemExit(console_main())
