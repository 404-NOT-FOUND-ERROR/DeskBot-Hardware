"""Select a single Kconfig branch while preserving all other source text."""
import re


_DIRECTIVE = re.compile(r"^\s*#\s*(if|ifdef|ifndef|elif|else|endif)\b(.*)$")


def select_config_branch(source, symbol, enabled):
    """Resolve CONFIG_<symbol> conditionals; leave unrelated directives intact."""
    target = f"CONFIG_{symbol}"
    lines = source.splitlines(keepends=True)
    output = []
    index = 0
    while index < len(lines):
        match = _DIRECTIVE.match(lines[index].rstrip("\r\n"))
        if not match or match.group(1) != "if" or match.group(2).strip() != target:
            output.append(lines[index])
            index += 1
            continue

        depth = 1
        alternate = None
        end = index + 1
        while end < len(lines):
            nested = _DIRECTIVE.match(lines[end].rstrip("\r\n"))
            if nested:
                directive, expression = nested.group(1), nested.group(2).strip()
                if directive in ("if", "ifdef", "ifndef"):
                    depth += 1
                elif directive == "endif":
                    depth -= 1
                    if depth == 0:
                        break
                elif depth == 1 and directive == "else":
                    if alternate is not None:
                        raise ValueError(f"duplicate #else for {target}")
                    alternate = end
                elif depth == 1 and directive == "elif":
                    raise ValueError(f"unexpected #elif for {target}")
            end += 1
        if end >= len(lines):
            raise ValueError(f"unterminated #if {target}")
        if enabled:
            selected = lines[index + 1 : alternate if alternate is not None else end]
        else:
            selected = lines[alternate + 1 : end] if alternate is not None else []
        output.extend(selected)
        index = end + 1
    return "".join(output)


def legacy_emote_owner(source):
    """Project optional role/layout additions back onto the original owner path.

    Keep the actual fallback player call, action bindings and display lifecycle;
    only remove the explicit replay entry wrapper added for network playback.
    """
    for symbol in ("DESKBOT_ROLE_PLAYER_ENABLE", "DESKBOT_ASSET_LAYOUT_ENABLE"):
        source = select_config_branch(source, symbol, False)
    start = source.index("void EmoteDisplay::SetEmotion(const char* const emotion)")
    body = source.index("void EmoteDisplay::ApplyEmotion(const char* const emotion, bool replay)", start)
    source = source[:start] + source[body:].replace(
        "void EmoteDisplay::ApplyEmotion(const char* const emotion, bool replay)",
        "void EmoteDisplay::SetEmotion(const char* const emotion)", 1)
    return source
