import os
import sys


def console_main() -> int:
    name = os.path.basename(sys.argv[0]) if sys.argv else "ai-config"
    os.environ.setdefault("AI_CONFIG_ENTRYPOINT", name)
    from ai_config.__main__ import main

    return main()
