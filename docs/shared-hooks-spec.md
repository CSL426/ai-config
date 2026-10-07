# Shared hooks

Status: Draft

## Problem

A skill written once reaches Claude Code, Codex and Antigravity: `claude/skills/`
projects to all three. A hook does not. One written in Claude Code's
`settings.json` syncs to every machine's Claude Code and stops there, so a
"whenever I push, do X" rule silently skips the other two tools.

## What the three tools accept (checked 2026-10-07)

| | Claude Code | Codex 0.160 | Antigravity 1.3 |
|---|---|---|---|
| file | `~/.claude/settings.json` `hooks` | `$CODEX_HOME/hooks.json`, every home | `~/.gemini/config/hooks.json`, one named entry per owner |
| shape | `{Event: [{matcher, hooks: [{type, command, timeout}]}]}` | same | `{name: {enabled, Event: [...]}}`; tool events grouped with a matcher, others a flat list |
| shell tool | `Bash` | `Bash` | `run_command`, command in `toolCall.args.CommandLine` |
| tool output in PostToolUse | `tool_response` | `tool_response` (one string) | none |
| inject context | `hookSpecificOutput.additionalContext` | same | not from PostToolUse; `PreInvocation` returning `{"injectSteps":[{"ephemeralMessage": …}]}` (verified live) |
| gate | `/hooks` or a new session loads it | each hook trusted in `/hooks`, again after any change | none for the user file |

## Design

A shared hook is written once, in Claude Code's format, and acg projects it.

- Definition: `claude/shared-hooks/<name>.json` in the data repository:
  `{"event", "matcher", "command", "timeout", "to"}`; `to` is `both`,
  `codex` or `agy` as for skills. Claude Code always gets it.
- `hooks share [<n> --name <name> [--to …]]` lists Claude Code's own
  (non-acg) hooks, or moves hook `<n>` into a definition.
  `hooks unshare <name>` puts it back into Claude Code's settings.
- `apply` projects every definition, as acg-owned entries so the
  database never sees the projected copies and a removed definition is
  removed everywhere:
  - Claude Code: a hook with `statusMessage` `acg：共用 <name>`.
  - Codex: the same entry in each home's `hooks.json`; other entries there
    are left alone.
  - Antigravity: entry `acg-<name>`. Its tool events go through
    `acg __agy-hook <name>`, which rewrites the payload into Claude Code's
    shape (`tool_name: Bash`, `tool_input.command`, `cwd`, `session_id`),
    runs the command, and keeps any `additionalContext` for the
    conversation; one shared `acg-inject` entry on `PreInvocation` hands it
    to the model before its next step.
- Events a tool lacks are skipped and `hooks list` says so (Codex has no
  `Stop`; Antigravity takes PreToolUse, PostToolUse and SessionStart here).
- Codex trust is never written. `hooks list` shows whether each Codex home
  has a trust record for the projected hook, and says to review it in
  `/hooks`.
- A hook's script cannot count on the tool's output: Antigravity sends
  none. It should fall back on `cwd`.

## Out of scope

Prompt and agent hook types; project-level hooks files; Antigravity's
`PostInvocation` and `Stop`.
