"""Shell completion generation for the standalone CLI."""

COMMANDS = (
    "setup",
    "init",
    "apply",
    "project",
    "status",
    "pull",
    "push",
    "sync",
    "list",
    "package",
    "reset",
    "deploy",
    "share",
    "config",
    "memory",
    "gui",
    "desktop",
    "skill",
    "completion",
    "update",
    "version",
    "help",
    "--version",
    "--help",
    "-V",
    "-h",
)
TOOLS = (
    "claude",
    "codex",
    "agy",
    "all",
    "antigravity",
    "antigravity-cli",
    "antigravity_cli",
)
TOOL_COMMANDS = ("init", "apply", "project", "status", "pull", "push", "sync")
SETUP_OPTIONS = ("--data-dir", "--repo-url", "--remote-name", "--replace-remote")
DEPLOY_OPTIONS = ("--profile", "--save-as")
MEMORY_COMMANDS = ("status", "enable", "disable", "adopt", "release", "path", "push")
SHELLS = ("bash", "powershell")


def bash_completion() -> str:
    commands = " ".join(COMMANDS)
    tools = " ".join(TOOLS)
    tool_commands = "|".join(TOOL_COMMANDS)
    setup_options = " ".join(SETUP_OPTIONS)
    deploy_options = " ".join(DEPLOY_OPTIONS)
    memory_commands = " ".join(MEMORY_COMMANDS)
    shells = " ".join(SHELLS)
    return f"""_ai_config_completion() {{
    local current command previous
    COMPREPLY=()
    current="${{COMP_WORDS[COMP_CWORD]}}"
    if (( COMP_CWORD == 1 )); then
        COMPREPLY=( $(compgen -W '{commands}' -- "$current") )
        return
    fi
    command="${{COMP_WORDS[1]}}"
    previous="${{COMP_WORDS[COMP_CWORD-1]}}"
    case "$command" in
        apply)
            if [[ "$previous" == --category ]]; then
                COMPREPLY=( $(compgen -W 'settings skills all' -- "$current") )
            else
                COMPREPLY=( $(compgen -W '{tools} --category' -- "$current") )
            fi
            ;;
        {tool_commands})
            if (( COMP_CWORD == 2 )); then
                COMPREPLY=( $(compgen -W '{tools}' -- "$current") )
            fi
            ;;
        setup)
            if (( COMP_CWORD == 2 )) || [[ "$current" == -* ]]; then
                COMPREPLY=( $(compgen -W '{setup_options}' -- "$current") )
            fi
            ;;
        deploy)
            if [[ "$current" == -* ]]; then
                COMPREPLY=( $(compgen -W '{deploy_options}' -- "$current") )
            fi
            ;;
        memory)
            if (( COMP_CWORD == 2 )); then
                COMPREPLY=( $(compgen -W '{memory_commands}' -- "$current") )
            fi
            ;;
        completion)
            if (( COMP_CWORD == 2 )); then
                COMPREPLY=( $(compgen -W '{shells}' -- "$current") )
            fi
            ;;
    esac
}}
complete -o default -F _ai_config_completion ai-config acg
"""


def powershell_completion() -> str:
    commands = ", ".join(f"'{value}'" for value in COMMANDS)
    tools = ", ".join(f"'{value}'" for value in TOOLS)
    tool_commands = ", ".join(f"'{value}'" for value in TOOL_COMMANDS)
    setup_options = ", ".join(f"'{value}'" for value in SETUP_OPTIONS)
    deploy_options = ", ".join(f"'{value}'" for value in DEPLOY_OPTIONS)
    memory_commands = ", ".join(f"'{value}'" for value in MEMORY_COMMANDS)
    shells = ", ".join(f"'{value}'" for value in SHELLS)
    return f"""Register-ArgumentCompleter -CommandName @('ai-config', 'acg') -ScriptBlock {{
    param($wordToComplete, $commandAst, $cursorPosition)
    $commands = @({commands})
    $tools = @({tools})
    $toolCommands = @({tool_commands})
    $setupOptions = @({setup_options})
    $deployOptions = @({deploy_options})
    $memoryCommands = @({memory_commands})
    $shells = @({shells})
    $arguments = @(
        $commandAst.CommandElements |
            Select-Object -Skip 1 |
            ForEach-Object {{ $_.Extent.Text }}
    )
    if ($arguments.Count -eq 0 -or ($arguments.Count -eq 1 -and $wordToComplete)) {{
        $candidates = $commands
    }}
    else {{
        $command = $arguments[0]
        if ($command -eq 'apply') {{
            if ($arguments[-1] -eq $wordToComplete) {{
                $previousArgument = $arguments[-2]
            }}
            else {{
                $previousArgument = $arguments[-1]
            }}
            if ($previousArgument -eq '--category') {{
                $candidates = @('settings', 'skills', 'all')
            }}
            else {{
                $candidates = $tools + @('--category')
            }}
        }}
        elseif ($toolCommands -contains $command) {{
            if (
                $arguments.Count -eq 1 -or
                ($arguments.Count -eq 2 -and $arguments[-1] -eq $wordToComplete)
            ) {{
                $candidates = $tools
            }}
            else {{
                $candidates = @()
            }}
        }}
        elseif ($command -eq 'setup') {{
            if ($arguments[-1] -eq $wordToComplete) {{
                $previousArgument = $arguments[-2]
            }}
            else {{
                $previousArgument = $arguments[-1]
            }}
            if ($previousArgument -eq '--data-dir') {{
                [System.Management.Automation.CompletionCompleters]::CompleteFilename(
                    $wordToComplete
                )
                return
            }}
            $candidates = $setupOptions
        }}
        elseif ($command -eq 'deploy') {{
            $candidates = $deployOptions
        }}
        elseif ($command -eq 'memory') {{
            if (
                $arguments.Count -eq 1 -or
                ($arguments.Count -eq 2 -and $arguments[-1] -eq $wordToComplete)
            ) {{
                $candidates = $memoryCommands
            }}
            else {{
                $candidates = @()
            }}
        }}
        elseif ($command -eq 'completion') {{
            if (
                $arguments.Count -eq 1 -or
                ($arguments.Count -eq 2 -and $arguments[-1] -eq $wordToComplete)
            ) {{
                $candidates = $shells
            }}
            else {{
                $candidates = @()
            }}
        }}
        else {{
            $candidates = @()
        }}
    }}
    $candidates |
        Where-Object {{ $_ -like "$wordToComplete*" }} |
        Sort-Object -Unique
}}
"""


def render_completion(shell: str) -> str:
    if shell == "bash":
        return bash_completion()
    if shell == "powershell":
        return powershell_completion()
    raise ValueError(f"Unsupported completion shell: {shell}")
