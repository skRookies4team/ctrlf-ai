import time

LAST_STRATEGY = {}
STRATEGY_EVENTS = []

def record_strategy_event(domain: str, old: dict, new: dict):
    STRATEGY_EVENTS.append({
        "domain": domain,
        "old": old,
        "new": new,
        "ts": int(time.time()),
    })
