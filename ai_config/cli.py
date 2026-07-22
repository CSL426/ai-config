import os
import sys


_ENTRYPOINT_NAMES = {"ai-config", "acg"}


def console_main() -> int:
    # argv[0] is only trustworthy when launched via an installed script;
    # pytest and `python -m ai_config` would otherwise leak "__main__.py".
    name = os.path.basename(sys.argv[0]) if sys.argv and sys.argv[0] else ""
    name = name.removesuffix(".exe")
    if name not in _ENTRYPOINT_NAMES:
        name = "ai-config"
    os.environ.setdefault("AI_CONFIG_ENTRYPOINT", name)
    from ai_config import __main__ as command

    command.ENTRYPOINT = os.environ["AI_CONFIG_ENTRYPOINT"]
    return command.main()
