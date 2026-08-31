"""ai-config — Cross-AI tool configuration manager (CLI dispatch).

The command implementations live in the commands/ subpackage: apply (apply /
init), status, maintenance (list / reset / package / project), sync (pull +
shared data-repo Git helpers), push, share (cross-CLI skill sharing), gui,
setup, update, deploy.
"""

import os
import sys

from .commands.apply import _init_tools, apply_tools
from .commands.maintenance import do_list, do_package, do_project, do_reset
from .commands.push import do_push
from .commands.status import show_status
from .commands.sync import do_sync
from .completion import SHELLS, render_completion
from .console import (
    BOLD,
    CYAN,
    NC,
    log_error,
    log_info,
    log_success,
)
from .paths import ALL_TOOLS, CONFIG_ERROR, ENTRYPOINT, SCRIPT_DIR


def usage() -> None:
    print(f"{BOLD}{ENTRYPOINT}{NC} — Cross-AI tool configuration manager")
    print()
    print(f"{BOLD}Usage:{NC}")
    print(f"  {ENTRYPOINT} <command> [tool]")
    print()
    print(f"{BOLD}Commands:{NC}")
    print("  setup           Configure data repository and verify push access")
    print("  init [tool]     Gather configs from tool homes into the data repository")
    print("  apply [tool]    Deploy data repository configs to tool home directories")
    print("  project [tool]  Project ~/.claude/ directly to other tool home dirs")
    print("  status [tool]   Show diff between the data repository and live configs")
    print("  pull [tool]     Safely fast-forward repo changes, then show status")
    print("  push [tool]     Gather, review, commit, and push local configuration")
    print("                  --allow-secrets skip the credential-content check")
    print("  sync [tool]     Alias for pull")
    print("  list            List managed tools")
    print("  package [skill] Zip a shared skill for Claude Desktop upload")
    print("  reset           Delete all managed config files")
    print("  deploy [dir]    Copy managed Claude config into a project's .claude/")
    print("                  --profile <name> reuse a saved selection")
    print("                  --save-as <name> remember this selection")
    print("  share <skill>   Copy a Claude skill (or plugin skill) into claude/shared/")
    print("                  --to <both|codex|agy> pick the target tools (default both)")
    print("  config          Show provider (git/gdrive), repo, and login state")
    print("  gui             Launch the graphical interface (needs ai-config[gui])")
    print("  skill           Print the acg usage guide (written for AI agents)")
    print("  completion      Print Bash or PowerShell completion script")
    print("  update [version] Install the latest release, or a specific version")
    print("  version         Show the installed version")
    print("  help            Show this help")
    print()
    print(f"{BOLD}Tools:{NC}")
    print("  claude          Claude Code (~/.claude/)")
    print("  codex           Codex CLI (~/.codex/)")
    print("  agy             Antigravity CLI (~/.gemini/antigravity-cli/)")
    print("  all             All supported tools (default)")


def resolve_tool(tool: str) -> str:
    aliases = {"antigravity": "agy", "antigravity-cli": "agy", "antigravity_cli": "agy"}
    tool = aliases.get(tool, tool)
    if tool not in ("claude", "codex", "agy", "all"):
        log_error(f"Unknown tool: {tool}")
        sys.exit(1)
    return tool


def main(argv: "list[str] | None" = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not args:
        if (
            "PYTEST_CURRENT_TEST" not in os.environ
            and not (SCRIPT_DIR / "claude").is_dir()
            and sys.stdin.isatty()
        ):
            from .commands.setup import run_setup

            return run_setup([])
        usage()
        return 0

    cmd = args[0]
    if cmd in ("help", "--help", "-h"):
        if len(args) > 1:
            log_error(f"Unexpected arguments: {' '.join(args[1:])}")
            return 1
        usage()
        return 0
    if cmd == "setup":
        from .commands.setup import run_setup

        return run_setup(args[1:])
    if cmd == "update":
        if len(args) > 2:
            log_error(f"Usage: {ENTRYPOINT} update [version]")
            return 1
        from .commands.update import run_update

        return run_update(args[1] if len(args) == 2 else None)
    if cmd == "__update-check":
        # 隱藏命令:被動更新檢查的背景行程進入點
        from .commands.update import run_update_check_refresh

        return run_update_check_refresh()
    if cmd in ("version", "--version", "-V"):
        if len(args) != 1:
            log_error(f"Usage: {ENTRYPOINT} version")
            return 1
        from .version import current_version

        installed_version = current_version()
        if installed_version is None:
            log_error("Could not determine the installed ai-config version")
            return 1
        print(f"ai-config (acg) {installed_version}")
        return 0
    if cmd == "completion":
        if len(args) != 2 or args[1] not in SHELLS:
            log_error(f"Usage: {ENTRYPOINT} completion <bash|powershell>")
            return 1
        print(render_completion(args[1]), end="")
        return 0
    if cmd == "skill":
        if len(args) != 1:
            log_error(f"Usage: {ENTRYPOINT} skill")
            return 1
        from .guide import render_guide

        print(render_guide(), end="")
        return 0

    # config 是唯讀總覽,未設定時也要能跑
    if cmd == "config":
        if len(args) != 1:
            log_error(f"Usage: {ENTRYPOINT} config")
            return 1
        from .commands.info import run_config_info

        return run_config_info()

    # gui 放在設定檢查之前:未設定時 GUI 內建首次設定表單
    if cmd == "gui":
        if len(args) != 1:
            log_error(f"Usage: {ENTRYPOINT} gui")
            return 1
        from .commands.gui import run_gui

        return run_gui()

    if CONFIG_ERROR:
        log_error(CONFIG_ERROR)
        log_info(f"Run {ENTRYPOINT} setup to replace the invalid configuration")
        return 1
    if "PYTEST_CURRENT_TEST" not in os.environ and not (SCRIPT_DIR / "claude").is_dir():
        log_error(
            f"Repository configuration directory not found at {SCRIPT_DIR}.\n"
            f"Run {ENTRYPOINT} setup to configure and verify your data repository."
        )
        return 1

    if cmd == "share":
        share_usage = f"Usage: {ENTRYPOINT} share <skill> [--to both|codex|agy]"
        rest, name, target = args[1:], None, "both"
        while rest:
            token = rest.pop(0)
            if token == "--to":
                if not rest:
                    log_error(f"--to requires a target\n{share_usage}")
                    return 1
                target = rest.pop(0)
            elif token.startswith("--"):
                log_error(f"Unknown option: {token}\n{share_usage}")
                return 1
            elif name is None:
                name = token
            else:
                log_error(share_usage)
                return 1
        if name is None:
            log_error(share_usage)
            return 1
        from .commands.share import run_share

        return run_share(name, target)

    if cmd == "deploy":
        from .commands.deploy import run_deploy

        deploy_usage = (
            f"Usage: {ENTRYPOINT} deploy [project-dir] "
            f"[--profile <name>] [--save-as <name>]"
        )
        rest, profile, save_as = args[1:], None, None
        positional: list[str] = []
        while rest:
            token = rest.pop(0)
            if token in ("--profile", "--save-as"):
                if not rest:
                    log_error(f"{token} requires a name\n{deploy_usage}")
                    return 1
                if token == "--profile":
                    profile = rest.pop(0)
                else:
                    save_as = rest.pop(0)
            elif token.startswith("--"):
                log_error(f"Unknown option: {token}\n{deploy_usage}")
                return 1
            else:
                positional.append(token)
        if len(positional) > 1:
            log_error(deploy_usage)
            return 1
        if profile is not None and save_as is not None:
            log_error("--profile and --save-as cannot be combined")
            return 1

        return run_deploy(
            positional[0] if positional else None,
            profile=profile,
            save_as=save_as,
        )

    if cmd == "package":
        if len(args) > 2:
            log_error(f"Unexpected arguments: {' '.join(args[2:])}")
            return 1
        skill_name = args[1] if len(args) > 1 else None
        return 0 if do_package(skill_name) else 1

    if cmd in ("list", "reset") and len(args) > 1:
        log_error(f"Unexpected arguments: {' '.join(args[1:])}")
        return 1

    allow_secrets = False
    positional: list[str] = []
    for token in args[1:]:
        if cmd == "push" and token == "--allow-secrets":
            allow_secrets = True
        else:
            positional.append(token)
    tool = positional[0] if positional else "all"
    if len(positional) > 1:
        log_error(f"Unexpected arguments: {' '.join(positional[1:])}")
        return 1
    tool = resolve_tool(tool)

    if cmd == "init":
        if not _init_tools(tool):
            return 1
        print()
        log_success(f"Init complete. Review with: {CYAN}{ENTRYPOINT} status{NC}")
    elif cmd == "apply":
        selected = [t for t in ALL_TOOLS if tool in ("all", t)]
        if not apply_tools(selected):
            return 1
        print()
        log_success(f"Apply complete. Verify with: {CYAN}{ENTRYPOINT} status{NC}")
    elif cmd == "project":
        if not do_project(tool):
            return 1
    elif cmd in ("pull", "sync"):
        code = do_sync(tool)
        if code != 0:
            return code
    elif cmd == "push":
        code = do_push(tool, allow_secrets=allow_secrets)
        if code != 0:
            return code
    elif cmd == "status":
        show_status(tool)
    elif cmd == "list":
        do_list()
    elif cmd == "reset":
        if not do_reset():
            return 1
    else:
        log_error(f"Unknown command: {cmd}")
        print()
        usage()
        return 1

    from .commands.update import maybe_notify_update

    maybe_notify_update()
    return 0


if __name__ == "__main__":
    sys.exit(main())
