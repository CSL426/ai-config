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
    "reset",
    "completion",
    "update",
    "help",
)
TOOLS = ("claude", "codex", "agy", "all")
TOOL_COMMANDS = ("init", "apply", "project", "status", "pull", "push", "sync")
SETUP_OPTIONS = ("--data-dir", "--repo-url", "--remote-name", "--replace-remote")
SHELLS = ("bash", "powershell")


def bash_completion() -> str:
    commands = " ".join(COMMANDS)
    tools = " ".join(TOOLS)
    tool_commands = "|".join(TOOL_COMMANDS)
    setup_options = " ".join(SETUP_OPTIONS)
    shells = " ".join(SHELLS)
    return f"""_ai_config_completion() {{
    local current command
    current="${{COMP_WORDS[COMP_CWORD]}}"
    if (( COMP_CWORD == 1 )); then
        COMPREPLY=( $(compgen -W '{commands}' -- "$current") )
        return
    fi
    command="${{COMP_WORDS[1]}}"
    case "$command" in
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
        completion)
            if (( COMP_CWORD == 2 )); then
                COMPREPLY=( $(compgen -W '{shells}' -- "$current") )
            fi
            ;;
    esac
}}
complete -o default -F _ai_config_completion ai-config ai-config.exe acg
"""


def powershell_completion() -> str:
    commands = ", ".join(f"'{value}'" for value in COMMANDS)
    tools = ", ".join(f"'{value}'" for value in TOOLS)
    tool_commands = ", ".join(f"'{value}'" for value in TOOL_COMMANDS)
    setup_options = ", ".join(f"'{value}'" for value in SETUP_OPTIONS)
    shells = ", ".join(f"'{value}'" for value in SHELLS)
    return f"""Register-ArgumentCompleter -CommandName @('ai-config', 'ai-config.exe', 'acg') -ScriptBlock {{
    param($wordToComplete, $commandAst, $cursorPosition)
    $commands = @({commands})
    $tools = @({tools})
    $toolCommands = @({tool_commands})
    $setupOptions = @({setup_options})
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
        if ($toolCommands -contains $command) {{
            $candidates = $tools
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
        elseif ($command -eq 'completion') {{
            $candidates = $shells
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
