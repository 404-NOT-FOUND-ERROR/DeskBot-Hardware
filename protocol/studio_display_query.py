"""Read-only display prerequisites. Transport and serial ownership stay external."""
from copy import deepcopy

from protocol import studio_runtime_query as runtime

SCHEMA = "deskbot.studio_display/v1"
PLAYERS = ("unknown", "unavailable", "initialized", "stopping")
REASONS = ("player_unknown", "player_unavailable", "player_stopping", "application_unknown",
           "application_transitioning", "application_busy", "page_unknown", "page_transitioning",
           "not_home", "sleep_unknown")


def reason(value):
    player = value["player"]["state"]
    if player != "initialized":
        return {"unknown": "player_unknown", "unavailable": "player_unavailable",
                "stopping": "player_stopping"}[player]
    for name in ("application", "page"):
        phase = value[name]["phase"]
        if phase == "unknown":
            return f"{name}_unknown"
        if phase == "transitioning":
            return f"{name}_transitioning"
        if name == "application" and value[name]["value"] != "idle":
            return "application_busy"
        if name == "page" and value[name]["value"] != "home":
            return "not_home"
    return "sleep_unknown"


def validate_reply(value):
    if (not isinstance(value, dict) or value.get("schema") != SCHEMA
            or value.get("message_type") != "display"
            or "player" not in value or "preview" not in value):
        raise ValueError("invalid_display_reply")
    base = {key: field for key, field in value.items() if key not in ("player", "preview")}
    base.update(schema=runtime.SCHEMA, message_type="state")
    runtime.validate_reply(base)
    player, preview = value["player"], value["preview"]
    if (not isinstance(player, dict) or set(player) != {"revision", "state"}
            or type(player["revision"]) is not int or not 0 <= player["revision"] <= 0xffffffff
            or player["state"] not in PLAYERS
            or (player["revision"] == 0 and player["state"] != "unknown")
            or not isinstance(preview, dict) or set(preview) != {"available", "reason"}
            or preview["available"] is not False or preview["reason"] != reason(value)):
        raise ValueError("invalid_display_prerequisites")
    return deepcopy(value)


class DisplayQueryClient(runtime.RuntimeQueryClient):
    """Uses the existing session, freshness, replay and reconnect checks."""
    QUERY_OPERATION = "display"
    REPLY_SCHEMA = SCHEMA
    validate_state_reply = staticmethod(validate_reply)
