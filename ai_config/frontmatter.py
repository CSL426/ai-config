"""Normalize skill frontmatter for strict Codex and Antigravity parsers."""

import re


def _yaml_squote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _first_sentence(text: str) -> str:
    return re.split(r"\.\s+", text, maxsplit=1)[0].rstrip(".").strip()


def sanitize_skill_frontmatter(content: str, default_name: str = "skill") -> str:
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")

    if len(lines) < 2 or lines[0] != "---":
        heading = re.search(r"(?m)^#\s+(.+)$", normalized)
        name = heading.group(1).strip() if heading else default_name
        scalar = _yaml_squote(name)
        return (
            f"---\nname: {scalar}\ndescription: >-\n  {name}\nmetadata:\n"
            f"  short-description: {scalar}\n---\n{normalized}"
        )

    try:
        closing_fence = lines.index("---", 1)
    except ValueError as exc:
        raise ValueError(f"Invalid YAML frontmatter for skill: {default_name}") from exc

    frontmatter = lines[1:closing_fence]
    name = default_name
    description = ""
    has_name = False
    has_description = False
    has_metadata = False
    has_short_description = False
    metadata_index = -1

    index = 0
    while index < len(frontmatter):
        line = frontmatter[index]
        name_match = re.match(r"^name:\s*(.+)$", line)
        if name_match:
            has_name = True
            name = name_match.group(1).strip()

        description_match = re.match(r"^description:\s*(.*)$", line)
        if description_match:
            has_description = True
            description = description_match.group(1).strip()
            fully_quoted = re.match(r"^([\"']).*\1$", description) is not None
            if description and description[0] not in ">|" and not fully_quoted:
                frontmatter[index] = "description: >-"
                frontmatter.insert(index + 1, f"  {description}")
                index += 1

        if re.match(r"^metadata:\s*$", line):
            has_metadata = True
            metadata_index = index
        if re.match(r"^\s+short-description:\s*(.+)$", line):
            has_short_description = True
        index += 1

    if not has_name:
        frontmatter.append(f"name: {_yaml_squote(name)}")
    if not has_description:
        description = name
        frontmatter.extend(("description: >-", f"  {description}"))

    description_for_short = description
    if not description_for_short or description_for_short[0] in ">|":
        description_for_short = name
    elif match := re.fullmatch(r'"([^"\\]*)"', description_for_short):
        description_for_short = match.group(1)
    elif description_for_short.startswith(('"', "'")):
        description_for_short = name

    if not has_short_description:
        short_line = (
            "  short-description: "
            + _yaml_squote(_first_sentence(description_for_short))
        )
        if not has_metadata:
            frontmatter.extend(("metadata:", short_line))
        else:
            metadata_end = len(frontmatter)
            for index in range(metadata_index + 1, len(frontmatter)):
                if frontmatter[index] and not frontmatter[index][0].isspace():
                    metadata_end = index
                    break
            frontmatter.insert(metadata_end, short_line)

    result = ["---", *frontmatter, "---", *lines[closing_fence + 1 :]]
    return "\n".join(result)
