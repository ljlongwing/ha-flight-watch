"""Constants for Flight Watch."""

DOMAIN = "flight_watch"
EVENT = "flight_watch_event"
SIGNAL_UPDATE = "flight_watch_update"

CONF_NOTIFY = "notify_entities"
CONF_EVENTS = "notify_events"
CONF_ADB_KEY = "aerodatabox_key"

# Events a flight goes through, in order; also the notification choices.
EV_TRACKING = "tracking"
EV_LEFT_GATE = "left_gate"
EV_TOOK_OFF = "took_off"
EV_LANDED = "landed"
EV_ARRIVED_GATE = "arrived_gate"
EV_REMOVED = "removed"
EV_PROBLEM = "problem"  # not found on that date / already landed
LEG_EVENTS = [EV_LEFT_GATE, EV_TOOK_OFF, EV_LANDED, EV_ARRIVED_GATE]
ALL_EVENTS = [EV_TRACKING, *LEG_EVENTS, EV_REMOVED, EV_PROBLEM]

STATE_UPCOMING = "upcoming"
STATE_TRACKING = "tracking"
STATE_DONE = "done"

FR24_WINDOW_DAYS = 8  # how far ahead FR24's flight list reaches
