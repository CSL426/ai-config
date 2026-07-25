# Authoring and installing skills

`ai-config` treats skills as ordinary directories in the data repository. Where
you put a skill decides which CLIs receive it.

## Where a skill goes

| Location in data repo | Reaches | Notes |
|---|---|---|
| `claude/shared/both/<skill>/` | Codex **and** Antigravity | Also keep a copy in `~/.claude/skills/<skill>/` if Claude Code should use it |
| `claude/shared/codex/<skill>/` | Codex only | |
| `claude/shared/agy/<skill>/` | Antigravity only | |
| `~/.claude/skills/<skill>/` | Claude Code only | **Not synced** — lives outside the data repo |

The `shared/` copy is authoritative. Deleting it there removes the mirrored copy
on the next `apply` (managed-skill reconciliation), so don't hand-delete the
projected copies.

`claude/commands/<name>.md` holds Claude Code slash commands. These are
Claude-only — Codex and Antigravity have no slash-command concept, so a command
cannot be shared across tools. A skill is the portable unit.

## Projection targets

`apply` and `project` write skills to each tool's canonical store:

- Codex → `~/.agents/skills/` (shared by Codex Desktop, CLI, and the IDE
  extension; the legacy `~/.codex/skills` is migrated once and marked with
  `.ai-config-codex-skills-migrated`)
- Antigravity → `~/.gemini/config/skills/` (the editor and CLI share this store)

Only `SKILL.md` plus the `examples/`, `references/`, `scripts/`, and `agents/`
subdirectories are copied. Anything else in a skill directory stays local.

## Installing a skill

```bash
# 1. author it (in ~/.claude/skills/<name>/ if Claude Code should use it too)
# 2. place the portable copy
cp -r ~/.claude/skills/<name> ~/ai-config/data/claude/shared/both/<name>

# 3. preview — read-only, safe anytime
ai-config status

# 4. deploy to the tool home dirs (auto-backs up first)
ai-config apply

# 5. commit + push for other machines
ai-config push          # init + review + commit + push, with confirmation
```

On another machine: `ai-config pull` then `ai-config apply`.

For Claude Desktop, which has no writable local skills directory, export
instead: `ai-config package <skill>` produces a zip to upload manually under
Settings → Customize → Skills. That is a one-way export.

## Frontmatter

Codex and Antigravity parse frontmatter more strictly than Claude Code, so the
CLI normalizes it during projection: `name`, `description`, and
`metadata.short-description` are filled in and quoted as needed. A skill with no
frontmatter at all gets one synthesized from its first `#` heading.

Keep `name` matching the directory name. Renaming a skill means renaming the
directory, the `name:` field, and any `/slash-command` file that invokes it —
missing one leaves a dangling reference.

Write `description` so it states *when* to use the skill, not just what it is;
that text is the only thing loaded until the skill triggers.

## Keeping skills cheap

Skill `name` + `description` are injected into every session, but the body is
read only when the skill triggers. So a long skill costs little until used —
provided the guidance actually lives in the body rather than the description.

For long skills, split reference material into `references/` and let `SKILL.md`
point at it, instead of inlining everything.
