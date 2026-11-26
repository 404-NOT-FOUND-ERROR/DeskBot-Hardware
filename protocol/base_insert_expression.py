"""Map the verified base-reconnect event into the mounted expression player."""


def patch(raw):
    old = b'emote_display->InsertAnimDialog("insert", 3000);'
    new = b'emote_display->ReplayEmotion("insert");'
    if raw.count(old) != 1:
        raise ValueError('base_insert_expression_anchor_changed')
    # Each reconnect is a fresh event, including after the previous insert
    # auto-finished. Replay avoids same-semantic state-refresh suppression.
    # Reconnection selects the mounted role's authored entry/body/exit clip;
    # heartbeat state and all other attachment events keep their old path.
    return raw.replace(old, new, 1)
