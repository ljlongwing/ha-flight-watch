# Flight Watch

A Home Assistant integration for keeping an eye on the flights of people you care about. Add a flight number
and a date. On that day it is tracked and you get alerts when it leaves the gate, takes off, lands and reaches
the gate. Alerts can go to Telegram, the mobile app, email or any other notify entity.

- **The right flight on the right day.** Flight numbers repeat daily, and some fly different routes on different
  days. Flight Watch picks the flight that departs on your date (local to the departure airport).
- **Times that make sense.** Airport-local times, plus your own time in brackets when the zone differs:
  `took off at 8:48 AM EDT (6:48 AM MDT), ETA 11:01 AM PDT (12:01 PM MDT) · in 3.5 hours`.
- **Names.** Add "who" and alerts read "Tony's flight (Southwest WN1875, Denver (DEN) → Orlando (MCO)) landed at …".
- **Trips.** Add `Southwest 1570/1875` to add both flights of a connection at once.
- **A dashboard card** with tracked flights (and a button to stop tracking one), a live map, upcoming trips, and an
  add form.

## Install

1. HACS → ⋮ → Custom repositories → add this repository as an *Integration*, then install **Flight Watch**.
   (Or copy `custom_components/flight_watch` into your `config/custom_components`.)
2. Restart Home Assistant.
3. Settings → Devices & services → Add integration → **Flight Watch**. Pick where alerts go and which ones.
4. Add the card to a dashboard: `type: custom:flight-watch-card`.

## The card

```yaml
type: custom:flight-watch-card
title: Flights          # optional
map: true               # optional, live map of airborne flights
entity: sensor.flight_watch_tracked_flights  # optional
colors:                 # optional, map marker/trail color by who (or flight number)
  Heather: pink         # a theme color name (red, blue, deep-orange, ...) or any CSS color like "#e91e63"
  Anthony: blue
```

## Settings

| Setting | |
|---|---|
| Send alerts to | Any notify entities. Empty = Home Assistant notifications. |
| Alerts to send | Now tracking, left the gate, took off, landed, at the gate, stopped tracking, problems. |
| AeroDataBox key | Optional. FR24 only lists flights about 8 days ahead; with a free [AeroDataBox](https://rapidapi.com/aedbx-aedbx/api/aerodatabox) key, routes for flights further out are shown too (checked weekly, a few calls a month). |

## Actions and events

- `flight_watch.add_flight`: `flight` (`AS300`, `Alaska 300`, `Southwest 1570/1875`), `date`, optional `who`, and optional `from`/`to` airport codes for a flight that makes several stops (several flights: `from` is the first, `to` the last). Without them, tracking starts where the person's earlier flight that day lands and stops before the route doubles back. The card asks when a flight has several legs.
- `flight_watch.lookup_flight`: `flight`, `date`; returns every leg flown that day (response only).
- `flight_watch.remove_flight`: `id` (from the sensor's attributes).
- Event `flight_watch_event` on every change: `type` (`tracking`, `left_gate`, `took_off`, `landed`,
  `arrived_gate`, `removed`, `problem`), `flight`, `date`, `who`, `message`, `leg`. Use it for your own automations.
- `sensor.flight_watch_tracked_flights`: number of flights being tracked; attributes `tracking`, `upcoming`, `done`.
- `geo_location` entities (source `flight_watch`) for each airborne flight.

## How it works and limits

Flight data comes from Flightradar24's public website endpoints. They are unofficial: FR24 can change or limit
them at any time. Flight Watch keeps its requests low: every 15 minutes on the day of a flight, every minute only
from 3 hours before departure until it is at the gate, and every 6 hours for upcoming flights.

"Left the gate" is when FR24 first sees the aircraft move; "at the gate" is when FR24 stops tracking it after
landing, or 15 minutes after landing if it never says.

## License

MIT. Built with help from Claude (Anthropic) and tested on my own setup.
