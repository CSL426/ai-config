import os


def console_main() -> int:
    os.environ.setdefault("AI_CONFIG_ENTRYPOINT", "ai-config")
    from ai_config.__main__ import main

    return main()
