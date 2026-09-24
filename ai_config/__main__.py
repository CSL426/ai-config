"""ai-config — Cross-AI tool configuration manager (CLI dispatch).

The command implementations live in the commands/ subpackage: apply (apply /
init), status, maintenance (list / reset / package / project), sync (pull +
shared data-repo Git helpers), push, share (cross-CLI skill sharing), gui,
setup, update, deploy.
"""

import os
import sys

from .categories import validate_category
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
    set_force,
)
from .paths import ALL_TOOLS, CONFIG_ERROR, ENTRYPOINT, SCRIPT_DIR


def usage() -> None:
    print(f"{BOLD}{ENTRYPOINT}{NC} — Cross-AI tool configuration manager")
    print()
    print(f"{BOLD}Usage:{NC}")
    print(f"  {ENTRYPOINT} <command> [tool] [--force]")
    print()
    print(f"{BOLD}Global options:{NC}")
    print("  --force, -f     Answer every [y/N] confirmation with yes, so a")
    print("                  script or agent with no terminal runs unattended.")
    print("                  It fills in the answer; the prompt is still shown.")
    print("                  Confirmations live in: push, deploy, skill remove.")
    print("                  reset ignores it on purpose: deleting every config")
    print("                  file always has to be confirmed by a person.")
    print("                  A command that stops for want of a confirmation")
    print("                  exits non-zero; having nothing to do exits zero.")
    print()
    print(f"{BOLD}Commands:{NC}")
    print("  setup           Configure data repository and verify push access")
    print("                  --account <name> bind a gh account (private HTTPS repos)")
    print("                  --data-dir <path> where the data repository lives")
    print("                  --repo-url <url> clone from, or point the remote at, this")
    print("                  --remote-name <name> git remote to use (default origin)")
    print("                  --replace-remote overwrite a different existing remote")
    print("                  --provider git|gdrive sync transport (default git)")
    print("                  --gdrive-folder <path> Drive folder, nested like A/B")
    print("                  --gdrive-space visible|hidden where Drive files live")
    print("  init [tool]     Gather configs from tool homes into the data repository")
    print("  apply [tool]    Deploy data repository configs to tool home directories")
    print("                  --category settings|skills|all (default all)")
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
    print("  unshare <skill> Remove a skill from claude/shared/ (undoes share)")
    print("  ignore-skills [tool]")
    print("                  Stop reporting the tool's own bundled skills")
    print("                  --from <both|codex|agy> limit to one target")
    print("  config          Show provider (git/gdrive), repo, and login state")
    print("  login [account] Bind a GitHub account that can push to the data repo")
    print("                  --unbind drop the binding (gh's own account is never switched)")
    print("  versions        list installed versions; update <ver> switches back")
    print("  hooks <list|enable <name>|disable <name>>")
    print("                  machine-local Claude Code hooks; never synced")
    print("  keepalive <status|enable [HH:MM ...]|disable|send> [claude|codex|agy]")
    print("                  call a tool at chosen times so its five-hour usage")
    print("                  window starts where the day needs it; per machine,")
    print("                  and each tool keeps its own times (default: claude)")
    print("  msg list | setup | send <名稱或 id> <訊息> [--wait [秒]] [--from <名稱>]")
    print("                  talk to another live Claude/Codex session by name;")
    print("                  setup writes the Claude channel config and prints the")
    print("                  shell function that lets plain `claude` receive")
    print("  memory <status|enable|disable|adopt|release|path|push|autopush>")
    print("                  adopt [專案路徑|all|--scan [目錄]] sync one project's")
    print("                    or find every unsynced one below a directory")
    print("                  enable|disable codex|agy  remember capture on that host only")
    print("                  Shared notebook that every AI tool reads and writes")
    print("                  path --global|--project print one of the two roots")
    print("                  push --if-stale <小時> only when nothing pushed since")
    print("                  autopush status|enable [時]|disable  schedule a daily save")
    print("                  handoff list [專案路徑]|write <線> <內容>|claim [線]|done <線>")
    print("                    hand one work thread to the session that follows")
    print("                  handoff remind status|enable [百分比]|disable")
    print("                    Claude context reminder (default 70%; no automatic write)")
    print("                  push --allow-secrets skip the credential-content check")
    print("  desktop         Launch the desktop app (bundled on Windows)")
    print("                  --shortcut add desktop and app-menu shortcuts (Windows, Linux)")
    print("                  --wait stay in the foreground (shows errors)")
    print("  gui             Alias for desktop")
    print("  skill           Print the acg usage guide (written for AI agents)")
    print("  skill guide     Alias for the guide; works before setup")
    print("  skill add <dir> Install a local skill into the repo and Claude home")
    print("                  Refuses overwrite; apply --category skills for Codex/agy")
    print("  skill remove <name>")
    print("                  Confirm removal of all standalone sources and mirrors")
    print("                  Includes legacy stores; --force accepts confirmation")
    print("                  For shared copies only, use unshare then apply")
    print("  completion      Print Bash or PowerShell completion script")
    print("  update [version] Install the latest release, or a specific version,")
    print("                  and bring the Claude Code plugin along")
    print("  version         Show the installed version")
    print("  help            Show this help")
    print()
    print(f"{BOLD}Claude Code plugin:{NC}")
    print("  This repository is also a plugin marketplace. Installing it adds")
    print("  slash commands that carry an acg: prefix, so it is clear where")
    print("  they came from:")
    print("    claude plugin marketplace add CSL426/ai-config")
    print("    claude plugin install acg@acg")
    print("  /acg:status /acg:sync /acg:save /acg:share /acg:memory")
    print("  /acg:handoff /acg:handoffs /acg:pickup /acg:keepalive /acg:msg")
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
    if args and args[0] in {"__handoff-statusline", "__handoff-reminder"}:
        from .handoff_reminder import run_hook, run_statusline

        handler = run_statusline if args[0] == "__handoff-statusline" else run_hook
        return handler(args[1:])
    if args and args[0] == "__channel":
        # Claude Code 的 channel:stdout 是 MCP 協定通道,任何提示都不能印
        from .channel import run as run_channel

        return run_channel()
    if args and args[0] == "__commit-style":
        from .commit_style import run_hook

        return run_hook(args[1:])
    if args and args[0] == "__memory-project-entry":
        from .memory_hooks import run

        return run(args[1:])
    if args and args[0] == "__git-credential":
        # 隱藏命令:git 的 credential helper 進入點,由 acg login 寫進資料庫的
        # 本地 git 設定;向 gh 拿綁定帳號的 token
        from .ghauth import credential_helper_main

        return credential_helper_main(args[1:])
    if args and args[0] == "_apply-preview-worker":
        if len(args) != 2:
            return 1
        from .applyplan import worker_main

        return worker_main(args[1])
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

    # --force/-f 對所有指令一致,在各指令自己解析參數之前就吃掉
    if any(token in ("--force", "-f") for token in args):
        set_force(True)
        args = [token for token in args if token not in ("--force", "-f")]
        if not args:
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
    if cmd == "skill" and args[1:] in ([], ["guide"]):
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
    if cmd in ("gui", "desktop"):
        if args[1:] == ["--shortcut"]:
            from .desktop import create_desktop_shortcut

            return create_desktop_shortcut()
        wait = args[1:] == ["--wait"]
        if not wait and len(args) != 1:
            log_error(f"Usage: {ENTRYPOINT} {cmd} [--shortcut] [--wait]")
            return 1
        from .desktop import detach_and_run_gui, run_gui

        # 預設放進背景,讓終端機立刻拿回控制權;--wait 保留前景模式,
        # 錯誤訊息才看得到
        if not wait and detach_and_run_gui():
            return 0
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

    if cmd == "skill":
        from .commands.skill import run_skill

        return run_skill(args[1:])

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

    if cmd == "login":
        if len(args) > 2:
            log_error(f"Usage: {ENTRYPOINT} login [account|--unbind]")
            return 1
        from .commands.login import run_login

        return run_login(args[1] if len(args) == 2 else None)

    if cmd == "memory":
        from .commands.memory import run_memory

        return run_memory(args[1:])

    if cmd == "versions":
        from .commands.update import run_update_list

        return run_update_list()

    if cmd == "hooks":
        from .commands.hooks import run_hooks

        return run_hooks(args[1:])

    if cmd == "msg":
        from .commands.msg import run_msg

        return run_msg(args[1:])
    if cmd == "keepalive":
        from .commands.keepalive import run_keepalive

        return run_keepalive(args[1:])

    if cmd == "ignore-skills":
        if len(args) > 2:
            log_error(f"Usage: {ENTRYPOINT} ignore-skills [tool]")
            return 1
        from .commands.status import run_ignore_skills

        return run_ignore_skills(args[1] if len(args) == 2 else "all")

    if cmd == "unshare":
        unshare_usage = (
            f"Usage: {ENTRYPOINT} unshare <skill> [--from both|codex|agy]"
        )
        rest, name, target = args[1:], None, None
        while rest:
            token = rest.pop(0)
            if token == "--from":
                if not rest:
                    log_error(f"--from requires a target\n{unshare_usage}")
                    return 1
                target = rest.pop(0)
            elif token.startswith("--"):
                log_error(f"Unknown option: {token}\n{unshare_usage}")
                return 1
            elif name is None:
                name = token
            else:
                log_error(unshare_usage)
                return 1
        if name is None:
            log_error(unshare_usage)
            return 1
        from .commands.share import run_unshare

        return run_unshare(name, target)

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

    category = "all"
    if cmd == "apply":
        remaining = iter(args[1:])
        positional = []
        category_seen = False
        for token in remaining:
            if token == "--category":
                if category_seen:
                    log_error("Repeated --category")
                    return 1
                category_seen = True
                category = next(remaining, "")
                try:
                    validate_category(category)
                except ValueError as exc:
                    log_error(str(exc))
                    return 1
            elif token.startswith("-"):
                log_error(f"Unknown apply option: {token}")
                return 1
            else:
                positional.append(token)
        if len(positional) > 1:
            log_error("Only one apply tool is accepted")
            return 1
        args = [cmd, *positional]

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
    if cmd == "push" and tool == "memory":
        from .commands.push import MEMORY_SCOPE

        return do_push(MEMORY_SCOPE, allow_secrets=allow_secrets)
    tool = resolve_tool(tool)

    if cmd == "init":
        if not _init_tools(tool):
            return 1
        print()
        log_success(f"Init complete. Review with: {CYAN}{ENTRYPOINT} status{NC}")
    elif cmd == "apply":
        selected = [t for t in ALL_TOOLS if tool in ("all", t)]
        if not apply_tools(selected, category=category):
            return 1
        from .hooks import refresh_all

        refresh_all()
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

    from .autopush import opportunistic_push
    from .commands.update import maybe_notify_update

    maybe_notify_update()
    # 沒有排程的機器靠這裡兜底;裝了排程就完全不做事
    opportunistic_push()
    return 0


if __name__ == "__main__":
    sys.exit(main())
