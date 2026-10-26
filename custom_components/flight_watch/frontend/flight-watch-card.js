// Flight Watch card: tracked flights (with stop), live map, upcoming trips (with remove), and an add form.
// Plain custom element, no build step. Data comes from the Flight Watch sensor's attributes.

const DEFAULT_ENTITY = "sensor.flight_watch_tracked_flights";

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

// IANA zone for an airport; whole-hour offsets fall back to Etc/GMT±h (note the inverted sign).
function zoneOf(ap, home) {
  if (ap && ap.tz) return ap.tz;
  if (ap && ap.offset != null && ap.offset % 3600 === 0) {
    const h = -ap.offset / 3600;
    return h === 0 ? "UTC" : `Etc/GMT${h > 0 ? "+" : ""}${h}`;
  }
  return home;
}

function clock(ts, zone, withDay) {
  const o = { timeZone: zone, hour: "numeric", minute: "2-digit", timeZoneName: "short" };
  if (withDay) o.weekday = "short";
  return new Intl.DateTimeFormat("en-US", o).format(new Date(ts * 1000));
}

function offsetOf(ts, zone) {
  return new Intl.DateTimeFormat("en-US", { timeZone: zone, timeZoneName: "longOffset" }).format(new Date(ts * 1000)).split(" ").pop();
}

// Airport-local time, plus home time in () when the zone differs, plus a countdown if in the future.
function when(ts, ap, home, withDay = true, count = true) {
  if (!ts) return "–";
  const z = zoneOf(ap, home);
  let s = clock(ts, z, withDay);
  if (offsetOf(ts, z) !== offsetOf(ts, home)) s += ` (${clock(ts, home, false)})`;
  const m = Math.round((ts - Date.now() / 1000) / 60);
  if (count && m > 0) {
    if (m < 60) s += ` · in ${m} min`;
    else {
      const h = Math.round(m / 30) / 2;
      s += ` · in ${h} hour${h !== 1 ? "s" : ""}`;
    }
  }
  return s;
}

const place = (ap) => (ap && ap.city && ap.iata ? `${ap.city} (${ap.iata})` : (ap && (ap.city || ap.iata)) || "?");

function route(legs) {
  if (!legs.length || !legs[0].origin || !legs[0].origin.iata) return "";
  const stops = [legs[0].origin];
  for (const l of legs) {
    if (stops.some((s) => s.iata === l.destination.iata)) break;
    stops.push(l.destination);
  }
  return stops.map(place).join(" → ");
}

function legStatus(l) {
  if (l.real_arr) return "🛬 Landed";
  if (l.live && l.real_dep) return `✈️ In air${l.alt ? ` · ${l.alt.toLocaleString()} ft` : ""}${l.speed ? ` · ${l.speed} kt` : ""}`;
  if (l.live) return "🚶 Left the gate";
  return "🕓 Scheduled";
}

function delay(l) {
  const arr = l.real_arr || l.est_arr;
  if (!arr || !l.sched_arr) return "";
  const d = Math.round((arr - l.sched_arr) / 60);
  return d > 10 ? `⚠️ ${d} min late` : d < -5 ? `✅ ${-d} min early` : "✅ On time";
}

function localToday() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

class FlightWatchCard extends HTMLElement {
  setConfig(config) {
    this._config = { entity: DEFAULT_ENTITY, map: true, title: "Flights", ...config };
    this._built = false;
  }

  static getStubConfig() {
    return { entity: DEFAULT_ENTITY };
  }

  getCardSize() {
    return 8;
  }

  getGridOptions() {
    return { columns: "full" };
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._built) this._build();
    const st = hass.states[this._config.entity];
    const key = st ? st.last_updated : "missing";
    const minute = Math.floor(Date.now() / 60000); // countdowns move even when data doesn't
    if (key !== this._key || minute !== this._minute) {
      this._key = key;
      this._minute = minute;
      this._render(st);
    }
    if (this._map) this._map.hass = hass;
  }

  _build() {
    this._built = true;
    this.innerHTML = `
      <ha-card>
        <style>
          .fw { padding: 16px; display: flex; flex-direction: column; gap: 12px; }
          .fw h2 { margin: 0; font-size: 1.25em; font-weight: 500; }
          .tracked, .upcoming { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 380px), 1fr)); gap: 8px; }
          .tracked > .empty, .upcoming > .empty { grid-column: 1 / -1; }
          .fw h3 { margin: 8px 0 0; font-size: 1em; font-weight: 500; color: var(--secondary-text-color); text-transform: uppercase; letter-spacing: .04em; }
          .flight { border: 1px solid var(--divider-color); border-radius: 12px; padding: 10px 12px; }
          .row { display: flex; align-items: flex-start; gap: 8px; }
          .row .main { flex: 1; min-width: 0; }
          .title { font-weight: 500; }
          .sub { color: var(--secondary-text-color); font-size: .9em; white-space: pre-line; }
          table { width: 100%; border-collapse: collapse; font-size: .9em; margin-top: 6px; }
          td, th { text-align: left; padding: 2px 6px 2px 0; vertical-align: top; }
          th { color: var(--secondary-text-color); font-weight: 400; }
          .x { background: none; border: none; color: var(--error-color, #db4437); cursor: pointer; font-size: 1.2em; padding: 0 4px; line-height: 1; }
          .empty { color: var(--secondary-text-color); }
          form { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
          form input { font: inherit; padding: 8px; border-radius: 8px; border: 1px solid var(--divider-color); background: var(--card-background-color); color: var(--primary-text-color); min-width: 0; }
          form .wide { grid-column: 1 / -1; }
          form button { grid-column: 1 / -1; font: inherit; padding: 8px; border: none; border-radius: 8px; background: var(--primary-color); color: var(--text-primary-color, #fff); cursor: pointer; }
          .err { color: var(--error-color, #db4437); font-size: .9em; grid-column: 1 / -1; }
          @media (min-width: 900px) {
            form { grid-template-columns: 3fr 1fr 1fr auto; }
            form .wide, form button { grid-column: auto; }
            form button { padding: 8px 24px; }
          }
        </style>
        <div class="fw">
          <h2></h2>
          <div class="tracked"></div>
          <div class="map"></div>
          <h3>Upcoming</h3>
          <div class="upcoming"></div>
          <h3>Add a flight</h3>
          <form>
            <input class="wide" name="flight" placeholder="Flight, e.g. AS300 or Southwest 1570/1875" required>
            <input name="date" type="date" required>
            <input name="who" placeholder="Who (optional)">
            <button type="submit">Add</button>
            <div class="err"></div>
          </form>
        </div>
      </ha-card>`;
    this.querySelector("h2").textContent = this._config.title;
    const form = this.querySelector("form");
    form.date.value = localToday();
    form.addEventListener("submit", (ev) => this._add(ev, form));
    this.addEventListener("click", (ev) => {
      const b = ev.target.closest("button.x");
      if (b && confirm(b.dataset.ask)) this._hass.callService("flight_watch", "remove_flight", { id: b.dataset.ids.split(",") });
    });
  }

  async _add(ev, form) {
    ev.preventDefault();
    const err = form.querySelector(".err");
    err.textContent = "";
    try {
      await this._hass.callService("flight_watch", "add_flight", { flight: form.flight.value, date: form.date.value, who: form.who.value });
      form.flight.value = "";
      form.who.value = "";
      // Date is kept, so the legs of one trip can be added in a row.
    } catch (e) {
      err.textContent = e.message || String(e);
    }
  }

  _render(st) {
    const tracked = this.querySelector(".tracked");
    const upcoming = this.querySelector(".upcoming");
    if (!st) {
      tracked.innerHTML = `<div class="empty">${esc(this._config.entity)} not found. Is Flight Watch set up?</div>`;
      upcoming.innerHTML = "";
      return;
    }
    const home = this._hass.config.time_zone;
    const a = st.attributes;
    const now = [...(a.tracking || []), ...(a.done || [])];

    tracked.innerHTML = now.length
      ? now.map((e) => e.legs.map((l) => this._legHtml(e, l, home)).join("")).join("")
      : `<div class="empty">No flights tracked today.</div>`;

    // Trips = same who + same day (no who = on its own).
    const trips = [];
    for (const e of a.upcoming || []) {
      const t = e.who && trips.find((x) => x.who === e.who && x.date === e.date);
      if (t) t.entries.push(e);
      else trips.push({ who: e.who, date: e.date, entries: [e] });
    }
    upcoming.innerHTML = trips.length ? trips.map((t) => this._tripHtml(t, home)).join("") : `<div class="empty">Nothing scheduled.</div>`;

    this._renderMap(now.some((e) => e.legs.some((l) => l.live && l.lat != null)));
  }

  _legHtml(e, l, home) {
    const who = e.who ? `${esc(e.who)}'s flight · ` : "";
    const al = l.airline ? `${esc(l.airline)} · ` : "";
    const depAct = l.real_dep || l.est_dep;
    const arrAct = l.real_arr || l.est_arr;
    const term = l.destination.terminal ? ` · Terminal ${esc(l.destination.terminal)}` : "";
    return `
      <div class="flight"><div class="row"><div class="main">
        <div class="title">${who}${esc(l.number)} · ${esc(place(l.origin))} → ${esc(place(l.destination))}</div>
        <div class="sub">${legStatus(l)} · ${al}${esc(delay(l))}${term}</div>
        <table>
          <tr><th></th><th>Scheduled</th><th>Actual / estimated</th></tr>
          <tr><td>Depart</td><td>${esc(when(l.sched_dep, l.origin, home, true, false))}</td><td>${esc(depAct ? when(depAct, l.origin, home) : "–")}</td></tr>
          <tr><td>Arrive</td><td>${esc(when(l.sched_arr, l.destination, home, true, false))}</td><td>${esc(arrAct ? when(arrAct, l.destination, home) : "–")}</td></tr>
        </table>
      </div>
      <button class="x" title="Stop tracking" data-ids="${esc(e.id)}" data-ask="Stop tracking ${esc(l.number)}? Its alerts stop too.">✖</button>
      </div></div>`;
  }

  _tripHtml(t, home) {
    const legs = t.entries.flatMap((e) => e.legs).sort((x, y) => x.sched_dep - y.sched_dep);
    const nums = t.entries.map((e) => e.number).join(" + ");
    const al = legs.length && legs[0].airline ? `${legs[0].airline} ` : "";
    const d = new Date(`${t.date}T00:00:00`);
    const days = Math.round((d - new Date(`${localToday()}T00:00:00`)) / 86400000);
    const rel = days === 0 ? "today" : days === 1 ? "tomorrow" : `in ${days} days`;
    const r = route(legs);
    const dep = legs.length ? `, departs ${when(legs[0].sched_dep, legs[0].origin, home, false, false)}` : "";
    const wait = !legs.length && days <= 0 ? "\nNot found on FR24 yet · checking hourly" : "";
    const src = t.entries.some((e) => e.source === "adb") ? " · schedule from AeroDataBox" : "";
    const ids = t.entries.map((e) => e.id).join(",");
    return `
      <div class="flight"><div class="row"><div class="main">
        <div class="title">${t.who ? `${esc(t.who)} · ` : ""}${esc(al)}${esc(nums)}</div>
        <div class="sub">${esc(d.toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" }))} · ${rel}${r ? `\n${esc(r)}${esc(dep)}${esc(src)}` : ""}${wait}</div>
      </div>
      <button class="x" title="Remove" data-ids="${esc(ids)}" data-ask="Remove this trip (${esc(nums)})?">✖</button>
      </div></div>`;
  }

  async _renderMap(show) {
    const box = this.querySelector(".map");
    if (!this._config.map || !show) {
      box.style.display = "none";
      return;
    }
    box.style.display = "";
    if (this._map || this._mapLoading) return;
    this._mapLoading = true;
    const helpers = await window.loadCardHelpers();
    // The map card defaults to square, which is a whole screen tall at panel width.
    const aspect_ratio = box.clientWidth > 700 ? "2.5:1" : "1:1";
    this._map = helpers.createCardElement({ type: "map", geo_location_sources: ["flight_watch"], hours_to_show: 6, auto_fit: true, aspect_ratio });
    this._map.hass = this._hass;
    box.appendChild(this._map);
  }
}

// HA 2026.10+ swaps window.customElements for a scoped registry during boot; this extra module can run
// before that, so wait for the app (loadCardHelpers) or the card lands in the discarded registry.
(function register(tries = 0) {
  if (!window.loadCardHelpers && tries < 300) return setTimeout(register, 100, tries + 1);
  if (!customElements.get("flight-watch-card")) customElements.define("flight-watch-card", FlightWatchCard);
  window.customCards = window.customCards || [];
  window.customCards.push({ type: "flight-watch-card", name: "Flight Watch", description: "Tracked and upcoming flights, live map, add/remove." });
})();
