// Flight Watch card: tracked flights (with stop), live map, upcoming trips (with remove), and an add form.
// Plain custom element, no build step. Data comes from the Flight Watch sensor's attributes.

const DEFAULT_ENTITY = "sensor.flight_watch_tracked_flights";
// Same as the brand icon (assets/icon.svg); inlined because /api/brands needs auth.
const LOGO = `<svg class="logo" aria-hidden="true" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256"> <circle cx="128" cy="128" r="122" fill="#039BE5"/> <g fill="none" stroke="#fff" stroke-linecap="round" opacity=".45" stroke-width="9"> <path d="M40 170 A92 92 0 0 1 86 40"/> </g> <path fill="none" stroke="#fff" stroke-width="7" stroke-linecap="round" stroke-dasharray="2 16" opacity=".8" d="M50 214 Q66 182 92 166"/> <path fill="#fff" transform="translate(140 120) rotate(45) scale(1.25)" d="M0,-62 C6,-62 9,-55 9,-46 L9,-14 L62,16 L62,30 L9,14 L9,42 L26,54 L26,63 L0,57 L-26,63 L-26,54 L-9,42 L-9,14 L-62,30 L-62,16 L-9,-14 L-9,-46 C-9,-55 -6,-62 0,-62 Z"/> </svg>`;

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

// Legs up to the trip's destination: a flight number that continues back (AA590 DFW→MCO→DFW) is cut off.
function tripLegs(legs) {
  if (!legs.length || !legs[0].origin || !legs[0].origin.iata) return [];
  const seen = [legs[0].origin.iata];
  const out = [];
  for (const l of legs) {
    if (seen.includes(l.destination.iata)) break;
    seen.push(l.destination.iata);
    out.push(l);
  }
  return out;
}

function route(legs) {
  const used = tripLegs(legs);
  return used.length ? [legs[0].origin, ...used.map((l) => l.destination)].map(place).join(" → ") : "";
}

const hm = (sec) => {
  const m = Math.round(sec / 60);
  return m >= 60 ? `${Math.floor(m / 60)}h ${m % 60}m` : `${m}m`;
};

// Scheduled gate-to-gate time; timestamps are UTC so time zones don't matter.
function duration(legs) {
  const used = tripLegs(legs);
  if (!used.length || used.some((l) => !l.sched_dep || !l.sched_arr)) return "";
  const total = used[used.length - 1].sched_arr - used[0].sched_dep;
  if (used.length === 1) return ` · ${hm(total)} flight`;
  const layover = total - used.reduce((t, l) => t + l.sched_arr - l.sched_dep, 0);
  return ` · ${hm(total)} total (${hm(layover)} layover${used.length > 2 ? "s" : ""})`;
}

// FR24 gives feet and knots; the integration's options pick what to show.
let UNITS = { alt: "ft", speed: "kt" };
const TO_UNIT = { ft: 1, m: 0.3048, kt: 1, mph: 1.15078, kmh: 1.852 };
const inUnit = (v, u) => `${Math.round(v * (TO_UNIT[u] || 1)).toLocaleString()} ${u === "kmh" ? "km/h" : u}`;

function legStatus(l) {
  if (l.canceled && !l.real_dep) return "❌ Canceled";
  if (l.sent?.includes("arrived_gate")) return "🧳 At the gate";
  if (l.real_arr) return "🛬 Landed";
  if (l.live && l.real_dep) return `✈️ In air${l.alt ? ` · ${inUnit(l.alt, UNITS.alt)}` : ""}${l.speed ? ` · ${inUnit(l.speed, UNITS.speed)}` : ""}`;
  if (l.live) return "🚶 Left the gate";
  return "🕓 Scheduled";
}

function delay(l) {
  if (l.canceled && !l.real_dep) return "";
  const arr = l.real_arr || l.est_arr;
  const [act, sched] = arr && l.sched_arr ? [arr, l.sched_arr] : [l.est_dep, l.sched_dep];
  if (!act || !sched) return "";
  const d = Math.round((act - sched) / 60);
  return d > 10 ? `⚠️ ${d} min late` : d < -5 ? `✅ ${-d} min early` : "✅ On time";
}

// Trips = same who + same day (no who = on its own).
function trips(entries) {
  const out = [];
  for (const e of entries) {
    const t = e.who && out.find((x) => x.who === e.who && x.date === e.date);
    if (t) t.entries.push(e);
    else out.push({ who: e.who, date: e.date, entries: [e] });
  }
  return out;
}

const uniq = (xs) => xs.filter((x, i) => xs.findIndex((y) => y.iata === x.iata) === i);

function localToday() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

class FlightWatchCard extends HTMLElement {
  setConfig(config) {
    this._config = { entity: DEFAULT_ENTITY, map: true, title: "Flights", ...config };
    this._built = false;
    this._key = this._map = null; // rebuilt DOM is empty (map included), so render on the next hass
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
    // Re-rendering would close an open color picker.
    if ((key !== this._key || minute !== this._minute) && !this.querySelector("input.clr:focus")) {
      this._key = key;
      this._minute = minute;
      this._render(st);
    }
    if (this._map) {
      const cfg = this._mapConfig();
      if (JSON.stringify(cfg.entities) !== this._mapEntities) this._setMapConfig(cfg);
      this._map.hass = hass;
    }
  }

  // The map card only draws trails (hours_to_show) for listed entities, not geo_location_sources ones.
  _mapConfig() {
    const s = this._hass.states;
    const entities = Object.keys(s).filter((id) => id.startsWith("geo_location.") && s[id].attributes.source === "flight_watch").sort();
    // Window, not box width: the box is display:none (width 0) when the config is rebuilt between flights.
    const aspect_ratio = window.innerWidth > 900 ? "3:1" : "2:1";
    // Colors are picked per person on the card; others get the map's palette.
    const colors = s[this._config.entity]?.attributes.colors || {};
    const list = entities.map((entity) => {
      const color = colors[s[entity].attributes.friendly_name];
      return color ? { entity, color } : entity;
    });
    return { type: "map", entities: list, hours_to_show: 6, aspect_ratio };
  }

  _setMapConfig(cfg) {
    this._mapEntities = JSON.stringify(cfg.entities);
    this._map.setConfig(cfg);
  }

  _build() {
    this._built = true;
    this.innerHTML = `
      <ha-card>
        <style>
          .fw { padding: 16px; display: flex; flex-direction: column; gap: 12px; }
          .fw h2 { margin: 0; font-size: 1.25em; font-weight: 500; display: flex; align-items: center; gap: 10px; }
          .fw h2 .logo { width: 32px; height: 32px; flex: none; }
          .tracked, .upcoming { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 380px), 1fr)); gap: 8px; }
          .tracked > .empty, .upcoming > .empty { grid-column: 1 / -1; }
          .fw h3 { margin: 8px 0 0; font-size: 1em; font-weight: 500; color: var(--secondary-text-color); text-transform: uppercase; letter-spacing: .04em; }
          .flight { border: 1px solid var(--divider-color); border-radius: 12px; padding: 10px 12px; }
          .flight.done { opacity: 0.6; }
          .row { display: flex; align-items: flex-start; gap: 8px; }
          .row .main { flex: 1; min-width: 0; }
          .title { font-weight: 500; }
          .clr { width: 18px; height: 18px; padding: 0; border: none; border-radius: 50%; background: none; cursor: pointer; vertical-align: -3px; margin-right: 6px; }
          .clr::-webkit-color-swatch-wrapper { padding: 0; }
          .clr::-webkit-color-swatch { border: none; border-radius: 50%; }
          .clr::-moz-color-swatch { border: none; border-radius: 50%; }
          .sub { color: var(--secondary-text-color); font-size: .9em; white-space: pre-line; }
          table { width: 100%; border-collapse: collapse; font-size: .9em; margin-top: 6px; table-layout: fixed; }
          td:first-child, th:first-child { width: 72px; } /* same columns in every leg of a trip */
          td, th { text-align: left; padding: 2px 6px 2px 0; vertical-align: top; }
          th { color: var(--secondary-text-color); font-weight: 400; }
          .x { background: none; border: none; color: var(--error-color, #db4437); cursor: pointer; font-size: 1.2em; padding: 0 4px; line-height: 1; }
          .empty { color: var(--secondary-text-color); }
          form { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
          form input { font: inherit; padding: 8px; border-radius: 8px; border: 1px solid var(--divider-color); background: var(--card-background-color); color: var(--primary-text-color); min-width: 0; }
          form .wide { grid-column: 1 / -1; }
          form button { grid-column: 1 / -1; font: inherit; padding: 8px; border: none; border-radius: 8px; background: var(--primary-color); color: var(--text-primary-color, #fff); cursor: pointer; }
          .err { color: var(--error-color, #db4437); font-size: .9em; grid-column: 1 / -1; }
          .err.info { color: var(--secondary-text-color); }
          .leg { border-top: 1px solid var(--divider-color); margin-top: 10px; padding-top: 8px; }
          .pick { grid-column: 1 / -1; display: flex; flex-wrap: wrap; gap: 8px 16px; align-items: center; padding: 10px 12px; border-radius: 8px; background: var(--secondary-background-color); }
          .pick[hidden] { display: none; }
          .pick .q { flex-basis: 100%; }
          .pick select { font: inherit; padding: 6px; margin-left: 6px; border-radius: 8px; border: 1px solid var(--divider-color); background: var(--card-background-color); color: var(--primary-text-color); }
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
            <div class="pick" hidden>
              <div class="q"></div>
              <label>Gets on at <select name="board"></select></label>
              <label>Gets off at <select name="leave"></select></label>
            </div>
            <div class="err"></div>
          </form>
        </div>
      </ha-card>`;
    this.querySelector("h2").innerHTML = `${LOGO}<span>${esc(this._config.title)}</span>`;
    const form = this.querySelector("form");
    form.date.value = localToday();
    form.addEventListener("submit", (ev) => this._add(ev, form));
    // A different flight or day needs a fresh lookup.
    for (const f of [form.flight, form.date]) f.addEventListener("input", () => (form.querySelector(".pick").hidden = true));
    this.addEventListener("click", (ev) => {
      const b = ev.target.closest("button.x");
      if (b && confirm(b.dataset.ask)) this._hass.callService("flight_watch", "remove_flight", { id: b.dataset.ids.split(",") });
    });
    this.addEventListener("change", (ev) => {
      if (ev.target.matches("input.clr")) this._hass.callService("flight_watch", "set_color", { who: ev.target.dataset.who, color: ev.target.value });
    });
  }

  async _add(ev, form) {
    ev.preventDefault();
    const err = form.querySelector(".err");
    const pick = form.querySelector(".pick");
    const btn = form.querySelector("button");
    err.textContent = "";
    err.classList.remove("info");
    btn.disabled = true;
    try {
      const data = { flight: form.flight.value, date: form.date.value, who: form.who.value };
      if (pick.hidden) {
        err.textContent = "Looking up the flight…";
        err.classList.add("info");
        if (await this._askLegs(data, form)) return; // shown the picker; the next Add uses it
      } else {
        data.from = form.board.value;
        data.to = form.leave.value;
      }
      await this._hass.callService("flight_watch", "add_flight", data);
      form.flight.value = "";
      form.who.value = "";
      pick.hidden = true;
      // Date is kept, so the legs of one trip can be added in a row.
    } catch (e) {
      err.textContent = e.message || String(e);
    } finally {
      if (err.classList.contains("info")) err.textContent = "";
      btn.disabled = false;
    }
  }

  // A flight number with several legs that day: ask where they get on and off. True if asked.
  async _askLegs(data, form) {
    let flights;
    try {
      const r = await this._hass.callService("flight_watch", "lookup_flight", { flight: data.flight, date: data.date }, undefined, false, true);
      flights = r.response.flights;
    } catch (e) {
      return false; // can't look it up now: add it, legs get picked automatically later
    }
    const multi = flights.filter((f) => f.legs.length > 1);
    if (!multi.length) return false;
    const legs = flights.flatMap((f) => f.legs).sort((x, y) => x.sched_dep - y.sched_dep);
    const auto = tripLegs(legs);
    const opts = (aps, sel) => uniq(aps).map((a) => `<option value="${esc(a.iata)}"${a.iata === sel ? " selected" : ""}>${esc(place(a))}</option>`).join("");
    form.board.innerHTML = opts(legs.map((l) => l.origin), (auto[0] || legs[0]).origin.iata);
    form.leave.innerHTML = opts(legs.map((l) => l.destination), (auto[auto.length - 1] || legs[legs.length - 1]).destination.iata);
    const f = multi[0];
    const stops = [f.legs[0].origin, ...f.legs.map((l) => l.destination)].map((a) => a.iata).join(" → ");
    form.querySelector(".q").textContent = `${f.number} flies ${stops} that day. Where does ${data.who || "the traveller"} get on and off? Then press Add.`;
    form.querySelector(".pick").hidden = false;
    return true;
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
    this._colors = a.colors || {};
    UNITS = a.units || UNITS;
    // Departure order, so a trip keeps its spot when it lands.
    const now = [...(a.tracking || []), ...(a.done || []).map((e) => ({ ...e, done: true }))].sort((x, y) => (x.legs[0]?.sched_dep ?? 0) - (y.legs[0]?.sched_dep ?? 0));

    tracked.innerHTML = now.length
      ? trips(now).map((t) => this._activeHtml(t, home)).join("")
      : `<div class="empty">No flights tracked today.</div>`;

    const up = trips(a.upcoming || []);
    upcoming.innerHTML = up.length ? up.map((t) => this._tripHtml(t, home)).join("") : `<div class="empty">Nothing scheduled.</div>`;

    const flying = now.flatMap((e) => e.legs).filter((l) => l.live && l.lat != null);
    this._fitPoints = flying.flatMap((l) => [l.origin, l]).filter((p) => p && p.lat != null).map((p) => [p.lat, p.lon]);
    this._renderMap(flying.length > 0);
    this._fit();
  }

  // The map card's auto_fit zooms to the plane alone; frame the path flown so far (origin to plane) instead.
  _fit() {
    const map = this._map?.shadowRoot?.querySelector("ha-map");
    if (!map) {
      if (this._map && !this._fitRetry) this._fitRetry = setTimeout(() => ((this._fitRetry = null), this._fit()), 500);
      return;
    }
    // ha-map queues this until its engine loads, and pauses its own auto-fit afterwards.
    if (this._fitPoints?.length) map.fitBounds(this._fitPoints, { zoom: 9, pad: 0.1 });
  }

  // Map color for this person; grey until one is picked (the map then uses its own palette).
  _swatch(who) {
    return who ? `<input type="color" class="clr" title="Map color for ${esc(who)}" data-who="${esc(who)}" value="${esc(this._colors[who] || "#9e9e9e")}">` : "";
  }

  // One box per trip; a connection gets a section per leg.
  _activeHtml(t, home) {
    const legs = t.entries.flatMap((e) => e.legs).sort((x, y) => x.sched_dep - y.sched_dep);
    if (!legs.length) return "";
    const nums = t.entries.map((e) => e.number).join(" + ");
    const ids = t.entries.map((e) => e.id).join(",");
    const leg = (l) => `${esc(l.number)} · ${esc(place(l.origin))} → ${esc(place(l.destination))}`;
    const head = legs.length === 1
      ? `<div class="title">${this._swatch(t.who)}${t.who ? `${esc(t.who)}'s flight · ` : ""}${leg(legs[0])}</div>${this._legBody(legs[0], home)}`
      : `<div class="title">${this._swatch(t.who)}${t.who ? `${esc(t.who)}'s trip · ` : "Trip · "}${esc(nums)}</div>
         <div class="sub">${esc(route(legs))}${esc(duration(legs))}</div>
         ${legs.map((l) => `<div class="leg"><div class="title">${leg(l)}</div>${this._legBody(l, home)}</div>`).join("")}`;
    return `
      <div class="flight${t.entries.every((e) => e.done) ? " done" : ""}"><div class="row"><div class="main">${head}</div>
      <button class="x" title="Stop tracking" data-ids="${esc(ids)}" data-ask="Stop tracking ${esc(nums)}? Its alerts stop too.">✖</button>
      </div></div>`;
  }

  _legBody(l, home) {
    const al = l.airline ? `${esc(l.airline)} · ` : "";
    const depAct = l.real_dep || l.est_dep;
    const arrAct = l.real_arr || l.est_arr;
    const spot = (a) => [a.terminal && `terminal ${a.terminal}`, a.gate && `gate ${a.gate}`].filter(Boolean).join(", ");
    const where = [!l.real_dep && spot(l.origin) && `Departs ${spot(l.origin)}`, spot(l.destination) && `Arrives ${spot(l.destination)}`, l.destination.baggage && `Baggage claim ${l.destination.baggage}`].filter(Boolean).join(" · ");
    return `
        <div class="sub">${legStatus(l)} · ${al}${esc(delay(l))}</div>
        ${where ? `<div class="sub">${esc(where)}</div>` : ""}
        <table>
          <tr><th></th><th>Scheduled</th><th>Actual / estimated</th></tr>
          <tr><td>Depart</td><td>${esc(when(l.sched_dep, l.origin, home, true, false))}</td><td>${esc(depAct ? when(depAct, l.origin, home) : "–")}</td></tr>
          <tr><td>Arrive</td><td>${esc(when(l.sched_arr, l.destination, home, true, false))}</td><td>${esc(arrAct ? when(arrAct, l.destination, home) : "–")}</td></tr>
          <tr><td>Duration</td><td>${l.sched_dep && l.sched_arr ? hm(l.sched_arr - l.sched_dep) : "–"}</td><td>${depAct && arrAct ? hm(arrAct - depAct) : "–"}</td></tr>
        </table>`;
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
        <div class="title">${this._swatch(t.who)}${t.who ? `${esc(t.who)} · ` : ""}${esc(al)}${esc(nums)}</div>
        <div class="sub">${esc(d.toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" }))} · ${rel}${r ? `\n${esc(r)}${esc(dep)}${esc(duration(legs))}${esc(src)}` : ""}${wait}</div>
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
    const cfg = this._mapConfig();
    this._map = helpers.createCardElement(cfg);
    this._mapEntities = JSON.stringify(cfg.entities);
    this._map.hass = this._hass;
    box.appendChild(this._map);
    this._mapLoading = false;
    this._fit();
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
