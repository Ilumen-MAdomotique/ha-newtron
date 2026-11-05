/* Newtron Smart — embedded Home Assistant configuration panel.
 *
 * Talks to the integration through two websocket commands:
 *   newtron/devices  -> snapshot of all configured devices
 *   newtron/command  -> publish cmnd/<topic>/<Command> and await stat response
 *
 * Every command used here comes from the "Smart Newtron — REST API & MQTT
 * Command Reference" (June 2026). MQTT commands need no API key.
 */

const REFRESH_MS = 10000;

const INPUT_MODES_BIG = [ // SMART 8R / 16R
  { v: 0, l: "Toggle" }, { v: 1, l: "Follower" },
  { v: 2, l: "Reverse follower" }, { v: 4, l: "Push" },
];
const INPUT_MODES_SMALL = [ // SMART 1R / 2R
  { v: 0, l: "Toggle" }, { v: 2, l: "Follower" },
  { v: 1, l: "Reverse follower" }, { v: 3, l: "Push" },
];
const OUTPUT_MODES = [
  { v: 0, l: "None" }, { v: 1, l: "Auto off" }, { v: 2, l: "Auto on" },
];
// What is wired on an input, shown that way in Home Assistant ("" = plain contact)
const SENSOR_TYPES = [
  ["", "Contact"], ["door", "Porte"], ["window", "Fenêtre"], ["garage_door", "Porte de garage"],
  ["motion", "Mouvement"], ["occupancy", "Présence"], ["smoke", "Fumée"], ["moisture", "Fuite d'eau"],
  ["gas", "Gaz"], ["vibration", "Vibration"], ["tamper", "Sabotage"], ["power", "Courant"],
];
// Firmware the Maintenance tab accepts to install (anything else would wipe the Newtron firmware)
const NEWTRON_FIRMWARE = /^https?:\/\/([a-z0-9-]+\.)*newtron\.tech\/.+\.bin$/i;
// Probes the Configuration tab offers for a GPIO (firmware component codes)
const GPIO_PROBES = [
  [0, "Rien de branché"], [1312, "Sonde de température DS18B20"],
  [1216, "Température + humidité DHT22 / AM2301"], [1184, "Température + humidité DHT11"],
  [1248, "Température + humidité SI7021"],
];
const SHUTTER_MODES = [
  { v: "0", l: "Double toggle" }, { v: "1", l: "Double push" }, { v: "2", l: "Simple push" },
];
const ALARM_MODES = [
  { v: 0, l: "Disabled" }, { v: 1, l: "NC — normally closed" }, { v: 2, l: "NO — normally open" },
];
const SIREN_TYPES = [
  { v: 0, l: "None" }, { v: 1, l: "Relay" }, { v: 2, l: "Persistent variable" },
];
const TIMER_MODES = [
  { v: 0, l: "Specific time" }, { v: 1, l: "Sunrise offset" },
  { v: 2, l: "Sunset offset" }, { v: 3, l: "Prayer times (FDAMI)" },
];
const DAY_NAMES = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}
function kv(label, value) {
  return `<div class="kv"><span>${esc(label)}</span><b>${esc(value ?? "—")}</b></div>`;
}
/* Column header with an optional (i) hover tooltip. */
function th(label, tip) {
  return `<th>${esc(label)}${tip ? `<span class="info" title="${esc(tip)}">i</span>` : ""}</th>`;
}

class NewtronPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._devices = [];
    this._selected = null;
    this._tab = "overview";
    this._timer = null;
    this._editing = false;
    this._initialized = false;
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._initialized) {
      this._initialized = true;
      this._init();
    }
  }

  disconnectedCallback() {
    if (this._timer) clearInterval(this._timer);
  }

  async _init() {
    this._renderShell();
    await this._loadDevices();
    this._timer = setInterval(() => this._tick(), REFRESH_MS);
  }

  async _tick() {
    if (this._editing || this._busy()) return;
    await this._loadDevices(true);
  }

  // The refresh redraws the tab, which closes an open list and drops the
  // cursor from a field: wait while a field has focus or a card form is open.
  _busy() {
    const active = this.shadowRoot.activeElement;
    if (active && active.matches("input, select, textarea")) return true;
    return this._tab === "config" && !!this._cfgDraft;
  }

  // -- websocket -------------------------------------------------------
  _ws(msg) { return this._hass.connection.sendMessagePromise(msg); }

  async _loadDevices(quiet = false) {
    try {
      const res = await this._ws({ type: "newtron/devices" });
      this._devices = res.devices || [];
      if (!this.dev && this._devices.length) {
        this._selected = this._devices[0].entry_id;
      }
      if (!this.dev) this._tab = "config";
      this._renderNav();
      if (!quiet || this._tab === "overview" || this._tab === "config") this._renderTab();
    } catch (e) {
      if (!quiet) this._toast(`Could not load devices: ${e.message || e}`, true);
    }
  }

  get dev() {
    return this._devices.find((d) => d.entry_id === this._selected) || null;
  }

  async cmd(command, payload = "", wait = true, timeout = 8) {
    const res = await this._ws({
      type: "newtron/command",
      entry_id: this._selected,
      command, payload, wait, timeout,
    });
    if (wait && res.timed_out) throw new Error(`No response to ${command}`);
    return res.response;
  }

  // -- shell / nav -----------------------------------------------------
  _renderShell() {
    this.shadowRoot.innerHTML = `
      <style>${STYLES}</style>
      <div class="layout">
        <header>
          <div class="brand"><span class="bolt">&#9889;</span> Newtron Smart</div>
          <select id="devsel"></select>
          <span id="conn" class="conn"></span>
        </header>
        <nav id="tabs"></nav>
        <main id="content"></main>
        <div id="toast" class="toast"></div>
      </div>`;
    this.shadowRoot.getElementById("devsel").addEventListener("change", (e) => {
      this._selected = e.target.value;
      this._tab = "overview";
      this._renderNav();
      this._renderTab();
    });
    this.shadowRoot.getElementById("tabs").addEventListener("click", (e) => {
      const tab = e.target.closest("[data-tab]");
      if (!tab) return;
      this._tab = tab.dataset.tab;
      this._editing = false;
      this._renderNav();
      this._renderTab();
    });
    this.shadowRoot.getElementById("content").addEventListener("input", () => {
      this._editing = true;
    });
  }

  _tabsFor(dev) {
    if (dev.family === "ir") {
      return [
        ["overview", "Overview"], ["ir", "IR remotes"], ["timers", "Timers"],
        ["webhooks", "Webhooks"], ["network", "Network"], ["maintenance", "Maintenance"], ["console", "Console"],
      ];
    }
    if (dev.family === "dali") {
      // Lights, groups and scenes live on the DALI bus: no relay, input or shutter
      return [
        ["overview", "Overview"], ["timers", "Timers"], ["webhooks", "Webhooks"],
        ["network", "Network"], ["maintenance", "Maintenance"], ["console", "Console"], ["config", "Configuration"],
      ];
    }
    const t = [["overview", "Overview"], ["inputs", "Inputs"]];
    if (dev.family === "dimmer") t.push(["dimmer", "Outputs (AO)"]);
    else t.push(["outputs", "Outputs & variables"]);
    if (dev.family === "relay") t.push(["shutters", "Shutters"], ["alarms", "Alarms"]);
    t.push(["timers", "Timers"], ["webhooks", "Webhooks"]);
    if (dev.capabilities && dev.capabilities.max_vthermostats) t.push(["thermostats", "Thermostats"]);
    if (dev.family === "pro") t.push(["datalogger", "Data logger"]);
    t.push(["network", "Network"], ["maintenance", "Maintenance"], ["console", "Console"], ["config", "Configuration"]);
    return t;
  }

  _renderNav() {
    const sel = this.shadowRoot.getElementById("devsel");
    sel.innerHTML = this._devices.map((d) =>
      `<option value="${d.entry_id}" ${d.entry_id === this._selected ? "selected" : ""}>
         ${esc(d.serial_number)} — ${esc(d.board_name || d.family)}</option>`).join("");
    const dev = this.dev;
    const conn = this.shadowRoot.getElementById("conn");
    if (dev) {
      conn.textContent = dev.available ? "online" : "offline";
      conn.className = `conn ${dev.available ? "ok" : "bad"}`;
    } else {
      conn.textContent = "";
    }
    sel.hidden = !this._devices.length;
    const tabs = this.shadowRoot.getElementById("tabs");
    tabs.innerHTML = (dev ? this._tabsFor(dev) : [["config", "Configuration"]])
      .map(([id, label]) =>
        `<button data-tab="${id}" class="${id === this._tab ? "active" : ""}">${label}</button>`).join("");
  }

  async _renderTab() {
    const c = this.shadowRoot.getElementById("content");
    if (this._tab === "config") {
      try { await this._tabConfig(c); } catch (e) { c.innerHTML = `<div class="empty">⚠ ${esc(e.message || e)}</div>`; }
      this._editing = false;
      return;
    }
    const dev = this.dev;
    if (!dev) return;
    c.innerHTML = `<div class="loading">Loading…</div>`;
    try {
      switch (this._tab) {
        case "overview": this._tabOverview(c, dev); break;
        case "inputs": await this._tabInputs(c, dev); break;
        case "outputs": await this._tabOutputs(c, dev); break;
        case "dimmer": await this._tabDimmer(c, dev); break;
        case "shutters": await this._tabShutters(c, dev); break;
        case "ir": await this._tabIR(c, dev); break;
        case "alarms": await this._tabAlarms(c, dev); break;
        case "timers": await this._tabTimers(c, dev); break;
        case "webhooks": await this._tabWebhooks(c, dev); break;
        case "thermostats": await this._tabThermostats(c, dev); break;
        case "datalogger": await this._tabDataLogger(c, dev); break;
        case "network": await this._tabNetwork(c, dev); break;
        case "maintenance": await this._tabMaintenance(c, dev); break;
        case "console": this._tabConsole(c); break;
      }
    } catch (e) {
      c.innerHTML = `<div class="empty">⚠ ${esc(e.message || e)}</div>`;
    }
    this._editing = false;
  }

  // -- OVERVIEW --------------------------------------------------------
  _tabOverview(c, dev) {
    const ds = dev.device_status || {};
    const info = ds.device_info || {};
    const net = ds.network || {};
    const mqtt = ds.mqtt || {};
    const dd = ds.dyn_dns || {};
    const sa = dev.status_all || {};
    const caps = dev.capabilities || {};

    const inputCells = Object.entries(sa.inputs || {}).map(([k, v]) =>
      `<div class="io ${v === "ON" ? "on" : ""}"><span>INPUT</span>${k.replace("input", "")}</div>`).join("");
    const outputCells = [
      ...Object.entries(sa.relays || {}).map(([k, v]) => {
        const n = k.replace("relay", "");
        return `<div class="io rel out-ctl ${v === "ON" ? "on" : ""}" data-relay="${n}"
          title="Click to toggle output ${n}"><span>OUTPUT</span>${n}</div>`;
      }),
      ...Object.entries(sa.ao || {}).map(([k, v]) =>
        `<div class="io ao ${v > 0 ? "on" : ""}"><span>AO</span>${k.replace("ao", "")}<b>${v}%</b></div>`),
      ...Object.entries(sa.roller_shutters || {}).map(([k, v]) =>
        `<div class="io sh"><span>SH</span>${k.replace("roller_shutter", "")}<b>${v.Position}%</b></div>`),
      ...Object.entries(sa.lights || {}).map(([k, v]) => {
        const n = k.replace("light", "");
        const ok = v.state === "OK";
        return `<div class="io ao ${ok ? "dali-ctl" : ""} ${v.level > 0 ? "on" : ""}" data-dali="${n}"
          title="${esc(v.name || `DALI ${n}`)} — ${esc(v.state || "")}${ok ? " (click to toggle)" : ""}">
          <span>DALI</span>${n}<b>${ok ? `${v.level ?? "—"}%` : esc(v.state || "?")}</b></div>`;
      }),
    ].join("");

    const sensorRows = Object.entries(sa.sensors || {})
      .filter(([, v]) => typeof v === "object")
      .map(([k, v]) => `<tr><td>${esc(k)}</td><td>${esc(v.id ?? "")}</td>
        <td>${v.temperature ?? "—"} °${esc(sa.sensors.temp_unit || "C")}</td></tr>`).join("");

    const dali = dev.family === "dali";
    c.innerHTML = `
      <div class="card">
        ${dali ? "" : `<h3>Inputs</h3>
        <div class="strip">${inputCells || "<i>No live inputs yet — press Refresh.</i>"}</div>`}
        <h3 ${dali ? "" : `style="margin-top:16px"`}>${dali ? "Lumières DALI" : "Outputs"} <span class="hint-inline">— ${dali
          ? "cliquez une lumière pour l'allumer / l'éteindre" : "click an output to toggle it"}</span></h3>
        <div class="strip">${outputCells || "<i>No live outputs yet — press Refresh.</i>"}</div>
      </div>
      <div class="grid">
        <div class="card"><h3>Identity</h3>
          ${kv("Serial number", info.serial_number)}
          ${kv("Board", info.board_name)}
          ${kv("Firmware", info.firmware_version)}
          ${kv("MAC", info.mac_address)}
          ${kv("Uptime", info.uptime)}
          ${kv("Boot count", info.boot_count)}
          ${kv("Last reset", info.reset_reason)}
        </div>
        <div class="card"><h3>Network</h3>
          ${kv("Connection", net.connection)}
          ${kv("Addressing", net.addressing_mode)}
          ${kv("IP", net.ip)}
          ${kv("Gateway", net.gateway)}
          ${kv("DNS", [net.dns1, net.dns2].filter(Boolean).join(", "))}
        </div>
        <div class="card"><h3>MQTT</h3>
          ${kv("Broker", mqtt.host ? `${mqtt.host}:${mqtt.port}` : "")}
          ${kv("Client", mqtt.client)}
          ${kv("Topic", mqtt.topic)}
          ${kv("Connected", mqtt.connected ? "yes" : "no")}
          ${kv("DynDNS", dd.activated ? `active — ${dd.url || ""}` : "inactive")}
        </div>
        <div class="card"><h3>Capabilities</h3>
          <div class="chips">${Object.entries(caps).map(([k, v]) =>
            `<span class="chip">${esc(k)}: ${esc(v)}</span>`).join("")}</div>
        </div>
        ${sensorRows ? `<div class="card"><h3>Temperature sensors</h3>
          <table><tr><th>Sensor</th><th>ROM</th><th>Temperature</th></tr>${sensorRows}</table></div>` : ""}
      </div>
      <div class="actions">
        <button id="ov-refresh">Refresh</button>
        <button id="ov-identify" class="secondary" title="Blinks relay 1 / AO1 four times">Identify device</button>
      </div>`;

    c.querySelector("#ov-refresh").onclick = async () => {
      await this.cmd("DeviceStatus").catch(() => {});
      await this.cmd("StatusAll").catch(() => {});
      await this._loadDevices();
      this._toast("Refreshed");
    };
    c.querySelector("#ov-identify").onclick = async () => {
      await this.cmd("VisualDiscover", "", false);
      this._toast("Identification blink started");
    };
    c.querySelectorAll(".dali-ctl").forEach((el) => {
      el.onclick = async () => {
        await this.cmd(`DaliPower${el.dataset.dali}`, "2", false);
        await this.cmd(`DaliState${el.dataset.dali}`).catch(() => {});
        await this.cmd("StatusAll").catch(() => {});
        await this._loadDevices();
      };
    });
    c.querySelectorAll(".out-ctl").forEach((el) => {
      el.onclick = async () => {
        const n = el.dataset.relay;
        try {
          const res = await this.cmd(`Power${n}`, "TOGGLE");
          const state = res ? (res[`POWER${n}`] || res.POWER || "?") : "?";
          this._toast(`Output ${n} → ${state}`);
          await this._loadDevices();
        } catch (e) {
          this._toast(e.message || e, true);
        }
      };
    });
  }

  // -- INPUTS ----------------------------------------------------------
  _inputModes(dev) {
    const board = (dev.board_name || "").toUpperCase();
    return /\b(1R|2R)\b/.test(board) ? INPUT_MODES_SMALL : INPUT_MODES_BIG;
  }

  async _tabInputs(c, dev) {
    const res = await this.cmd("InputConfig0");
    const list = (res && res.InputConfig) || [];
    const modes = this._inputModes(dev);
    c.innerHTML = `
      <div class="card">
        <h3>Input modes</h3>
        <p class="hint">Each physical input drives the output of the same index according to its mode.
        Only changed rows are written (<code>SetInputConfig</code> is partial).</p>
        <table>
          <tr>${th("Input", "Physical input number on the board")}
              ${th("Mode", "How the input drives the output of the same index: Toggle flips it on each press, Follower copies the input state, Reverse follower copies the opposite state, Push keeps the output on only while pressed")}</tr>
          ${list.map((row) => `
            <tr><td>Input ${row.input}</td><td>
              <select data-input="${row.input}" data-orig="${row.mode}">
                ${modes.map((m) => `<option value="${m.v}" ${m.v === row.mode ? "selected" : ""}>${m.l}</option>`).join("")}
              </select></td></tr>`).join("")}
        </table>
        <div class="actions"><button id="in-save">Save changes</button></div>
      </div>`;

    c.querySelector("#in-save").onclick = async () => {
      const changed = [...c.querySelectorAll("select[data-input]")]
        .filter((s) => s.value !== s.dataset.orig)
        .map((s) => ({ input: Number(s.dataset.input), mode: Number(s.value) }));
      if (!changed.length) return this._toast("Nothing changed");
      const res2 = await this.cmd("SetInputConfig", JSON.stringify(changed));
      this._toast(`Saved (${(res2 && res2.SetInputConfig) ?? changed.length} input(s))`);
      this._renderTab();
    };
  }

  // -- OUTPUTS & VARIABLES --------------------------------------------
  async _tabOutputs(c, dev) {
    const [outs, vars, pos] = await Promise.all([
      this.cmd("OutputConfig0").catch(() => null),
      this.cmd("VarConfig0").catch(() => null),
      this.cmd("PowerOnState").catch(() => null),
    ]);
    const outList = (outs && outs.OutputConfig) || [];
    const varList = (vars && vars.VarConfig) || [];
    const posVal = pos ? Object.values(pos)[0] : null;

    const modeSel = (cur, cls) =>
      `<select class="${cls}">${OUTPUT_MODES.map((m) =>
        `<option value="${m.v}" ${m.v === cur ? "selected" : ""}>${m.l}</option>`).join("")}</select>`;

    c.innerHTML = `
      <div class="card">
        <h3>Power-on behaviour</h3>
        <label class="switchline">
          <input type="checkbox" id="pos-check" ${posVal === 3 || posVal === "3" ? "checked" : ""}>
          Restore last relay state after a power outage (<code>PowerOnState 3 / 0</code>)
        </label>
      </div>
      <div class="card">
        <h3>Outputs</h3>
        <p class="hint">Auto off / auto on use the delay (seconds). Shutter relays are managed by the
        shutter driver and don't appear here.</p>
        <table>
          <tr>${th("Output", "Physical relay output number on the board")}
              ${th("Name", "Friendly name shown in Home Assistant (max 32 characters)")}
              ${th("Mode", "None: stays as commanded. Auto off: switches off after the delay. Auto on: switches back on after the delay.")}
              ${th("Delay (s)", "Delay in seconds used by the auto off / auto on modes")}</tr>
          ${outList.map((o) => `
            <tr data-output="${o.output}" data-orig='${esc(JSON.stringify(o))}'>
              <td>Output ${o.output}</td>
              <td><input class="o-name" value="${esc(o.name || "")}" maxlength="32"></td>
              <td>${modeSel(o.mode, "o-mode")}</td>
              <td><input class="o-delay" type="number" min="0" step="0.5" value="${o.delay ?? 0}"></td>
            </tr>`).join("")}
        </table>
        <div class="actions">
          <button id="out-save">Save changes</button>
          <button id="out-clear" class="danger">Clear all outputs</button>
        </div>
      </div>
      <div class="card">
        <h3>Volatile variables</h3>
        <table>
          <tr>${th("Var", "Volatile variable index (resets to 0 on reboot)")}
              ${th("Mode", "None: keeps its value. Auto off: resets to 0 after the delay. Auto on: returns to 1 after the delay.")}
              ${th("Delay (s)", "Delay in seconds used by the auto off / auto on modes")}</tr>
          ${varList.map((v) => `
            <tr data-var="${v.var ?? v.output}" data-orig='${esc(JSON.stringify(v))}'>
              <td>Var ${v.var ?? v.output}</td>
              <td>${modeSel(v.mode, "v-mode")}</td>
              <td><input class="v-delay" type="number" min="0" step="0.5" value="${v.delay ?? 0}"></td>
            </tr>`).join("")}
        </table>
        <div class="actions">
          <button id="var-save">Save changes</button>
          <button id="var-clear" class="danger">Clear all variables</button>
        </div>
      </div>`;

    c.querySelector("#out-save").onclick = async () => {
      const changed = [];
      c.querySelectorAll("tr[data-output]").forEach((tr) => {
        const orig = JSON.parse(tr.dataset.orig);
        const row = {
          output: Number(tr.dataset.output),
          name: tr.querySelector(".o-name").value,
          mode: Number(tr.querySelector(".o-mode").value),
          delay: Number(tr.querySelector(".o-delay").value),
        };
        if (row.name !== (orig.name || "") || row.mode !== orig.mode || row.delay !== (orig.delay ?? 0)) {
          changed.push(row);
        }
      });
      if (!changed.length) return this._toast("Nothing changed");
      await this.cmd("SetOutputConfig", JSON.stringify(changed));
      this._toast(`Saved ${changed.length} output(s)`);
      this._renderTab();
    };
    c.querySelector("#out-clear").onclick = () => this._confirm(
      "Reset ALL output names, modes and delays?",
      async () => { await this.cmd("ClearOutputConfig"); this._toast("Outputs cleared"); this._renderTab(); });

    c.querySelector("#var-save").onclick = async () => {
      const changed = [];
      c.querySelectorAll("tr[data-var]").forEach((tr) => {
        const orig = JSON.parse(tr.dataset.orig);
        const row = {
          var: Number(tr.dataset.var),
          mode: Number(tr.querySelector(".v-mode").value),
          delay: Number(tr.querySelector(".v-delay").value),
        };
        if (row.mode !== orig.mode || row.delay !== (orig.delay ?? 0)) changed.push(row);
      });
      if (!changed.length) return this._toast("Nothing changed");
      await this.cmd("SetVarConfig", JSON.stringify(changed));
      this._toast(`Saved ${changed.length} variable(s)`);
      this._renderTab();
    };
    c.querySelector("#var-clear").onclick = () => this._confirm(
      "Reset ALL volatile variable modes and delays?",
      async () => { await this.cmd("ClearVarConfig"); this._toast("Variables cleared"); this._renderTab(); });

    c.querySelector("#pos-check").onchange = async (e) => {
      await this.cmd("PowerOnState", e.target.checked ? "3" : "0");
      this._toast(`PowerOnState ${e.target.checked ? 3 : 0}`);
    };
  }

  // -- DIMMER ----------------------------------------------------------
  async _tabDimmer(c) {
    const [levels, modes] = await Promise.all([this.cmd("AO0"), this.cmd("AOMode0")]);
    const rows = [];
    for (let i = 1; i <= 8; i++) {
      const lvl = levels ? levels[`AO${i}`] : undefined;
      if (lvl === undefined) continue;
      rows.push({ i, lvl, mode: modes ? modes[`AOMode${i}`] : 0 });
    }
    c.innerHTML = `
      <div class="card">
        <h3>Analog outputs (0-10 V)</h3>
        <p class="hint">Bistable channels snap commanded values to 0 % or 100 % at the 50 % threshold.
        <code>ON</code> restores the memorised level; <code>OFF</code> memorises it.</p>
        <table>
          <tr><th>Output</th><th>Level</th><th></th><th>Input mode</th></tr>
          ${rows.map((r) => `
            <tr>
              <td>AO${r.i}</td>
              <td class="slider-cell">
                <input type="range" min="0" max="100" value="${r.lvl}" data-ao="${r.i}">
                <span id="ao-val-${r.i}">${r.lvl}%</span>
              </td>
              <td class="nowrap">
                <button class="mini" data-aoon="${r.i}">ON</button>
                <button class="mini secondary" data-aooff="${r.i}">OFF</button>
              </td>
              <td><select data-aomode="${r.i}">
                <option value="0" ${r.mode === 0 ? "selected" : ""}>Push button</option>
                <option value="1" ${r.mode === 1 ? "selected" : ""}>Bistable switch</option>
              </select></td>
            </tr>`).join("")}
        </table>
        <div class="actions"><button id="ao-reset" class="danger">Reset all outputs (AOReset)</button></div>
      </div>`;

    c.querySelectorAll("input[data-ao]").forEach((sl) => {
      sl.oninput = () => { c.querySelector(`#ao-val-${sl.dataset.ao}`).textContent = `${sl.value}%`; };
      sl.onchange = async () => {
        const requested = sl.value;
        const res = await this.cmd(`AO${sl.dataset.ao}`, requested);
        const applied = res ? res[`AO${sl.dataset.ao}`] : requested;
        sl.value = applied;
        c.querySelector(`#ao-val-${sl.dataset.ao}`).textContent = `${applied}%`;
        if (String(applied) !== String(requested)) this._toast(`Snapped to ${applied}% (bistable)`);
      };
    });
    c.querySelectorAll("button[data-aoon]").forEach((b) => {
      b.onclick = async () => {
        const res = await this.cmd(`AOOn${b.dataset.aoon}`);
        this._toast(`AO${b.dataset.aoon} → ${res ? res[`AOOn${b.dataset.aoon}`] : "?"}%`);
        this._renderTab();
      };
    });
    c.querySelectorAll("button[data-aooff]").forEach((b) => {
      b.onclick = async () => { await this.cmd(`AOOff${b.dataset.aooff}`); this._renderTab(); };
    });
    c.querySelectorAll("select[data-aomode]").forEach((s) => {
      s.onchange = async () => {
        await this.cmd(`AOMode${s.dataset.aomode}`, s.value);
        this._toast(`AO${s.dataset.aomode} mode saved`);
        this._renderTab();
      };
    });
    c.querySelector("#ao-reset").onclick = () => this._confirm(
      "Set all outputs to 0 % and clear all memorised levels?",
      async () => { await this.cmd("AOReset"); this._toast("All outputs reset"); this._renderTab(); });
  }

  // -- SHUTTERS --------------------------------------------------------
  async _tabShutters(c) {
    const res = await this.cmd("RollerConfig0");
    const cfg = (res && res.RollerConfig) || {};
    const shutters = cfg.roller_shutters || [];

    const row = (s = {}) => `
      <tr class="sh-row">
        <td><input class="sh-name" value="${esc(s.name || "")}" placeholder="Name"></td>
        <td><input class="sh-relay" type="number" min="1" value="${s.relay1 ?? ""}" style="width:60px">
            <span class="hint-inline">→ closes on ${s.relay1 ? s.relay1 + 1 : "relay1+1"}</span></td>
        <td><input class="sh-inv" type="checkbox" ${s.inverted ? "checked" : ""}></td>
        <td><input class="sh-open" type="number" min="1" step="0.5" value="${s.open_duration ?? 10}" style="width:70px"></td>
        <td><input class="sh-close" type="number" min="1" step="0.5" value="${s.close_duration ?? 10}" style="width:70px"></td>
        <td><select class="sh-mode">${SHUTTER_MODES.map((m) =>
          `<option value="${m.v}" ${String(s.mode ?? "0") === m.v ? "selected" : ""}>${m.l}</option>`).join("")}</select></td>
        <td><button class="mini danger sh-del">✕</button></td>
      </tr>`;

    const sa = (this.dev && this.dev.status_all) || {};
    const live = Object.entries(sa.roller_shutters || {}).map(([k, v]) => ({
      idx: Number(k.replace("roller_shutter", "")),
      name: (shutters[Number(k.replace("roller_shutter", "")) - 1] || {}).name || "",
      pos: v.Position ?? 0,
      dir: v.Direction ?? 0,
    }));
    const dirTxt = (d) => (d === 1 ? "▲ opening" : d === -1 ? "▼ closing" : "stopped");

    c.innerHTML = `
      ${live.length ? `
      <div class="card">
        <h3>Control</h3>
        <table>
          <tr>${th("Shutter", "Shutter number, in configuration order")}
              ${th("Position", "Current position reported by the device: 0 % = closed, 100 % = open")}
              ${th("State", "Movement currently in progress")}
              ${th("Control", "Open / stop / close the shutter")}
              ${th("Go to", "Send the shutter to an exact position (0-100 %)")}</tr>
          ${live.map((s) => `
            <tr>
              <td>${s.idx}${s.name ? ` — ${esc(s.name)}` : ""}</td>
              <td><b>${s.pos}%</b></td>
              <td>${dirTxt(s.dir)}</td>
              <td class="nowrap">
                <button class="mini" data-shopen="${s.idx}">▲ Open</button>
                <button class="mini secondary" data-shstop="${s.idx}">■ Stop</button>
                <button class="mini" data-shclose="${s.idx}">▼ Close</button>
              </td>
              <td class="nowrap">
                <input type="number" min="0" max="100" value="${s.pos}" style="width:64px" id="sh-pos-${s.idx}"> %
                <button class="mini secondary" data-shgoto="${s.idx}">Go</button>
              </td>
            </tr>`).join("")}
        </table>
        <p class="hint">Position refreshes automatically a moment after each command.</p>
      </div>` : ""}
      <div class="card">
        <h3>Roller shutters</h3>
        <p class="hint">Each shutter uses <b>2 consecutive relays</b>: choose the first relay and the next one
        (relay + 1) is automatically reserved as the second relay of that shutter. Saving writes the <b>complete</b>
        configuration (<code>SetRollerConfig</code>); reload the integration afterwards to refresh cover entities.</p>
        <table id="sh-table">
          <tr>${th("Name", "Friendly name of the shutter")}
              ${th("Relay 1 (open)", "First relay of the pair — drives the opening direction. The next relay (relay + 1) is automatically used for closing.")}
              ${th("Invert", "Swap the open/close directions if the motor is wired the other way round")}
              ${th("Opening duration (s)", "Time in seconds for a full travel from closed to open")}
              ${th("Closing duration (s)", "Time in seconds for a full travel from open to closed")}
              ${th("Control mode", "How the wall inputs drive the shutter: double toggle, double push or simple push")}
              <th></th></tr>
          ${shutters.map(row).join("")}
        </table>
        <div class="actions">
          <button id="sh-add" class="secondary">Add shutter</button>
          <button id="sh-save">Save configuration</button>
          <button id="sh-clear" class="danger">Clear all shutters</button>
        </div>
      </div>`;

    const refreshSoon = () => setTimeout(async () => {
      await this.cmd("StatusAll").catch(() => {});
      await this._loadDevices();
      if (this._tab === "shutters") this._renderTab();
    }, 1200);
    c.querySelectorAll("[data-shopen]").forEach((b) => {
      b.onclick = async () => { await this.cmd(`ShutterOpen${b.dataset.shopen}`, "", false); refreshSoon(); };
    });
    c.querySelectorAll("[data-shstop]").forEach((b) => {
      b.onclick = async () => { await this.cmd(`ShutterStop${b.dataset.shstop}`, "", false); refreshSoon(); };
    });
    c.querySelectorAll("[data-shclose]").forEach((b) => {
      b.onclick = async () => { await this.cmd(`ShutterClose${b.dataset.shclose}`, "", false); refreshSoon(); };
    });
    c.querySelectorAll("[data-shgoto]").forEach((b) => {
      b.onclick = async () => {
        const n = b.dataset.shgoto;
        const pct = c.querySelector(`#sh-pos-${n}`).value;
        await this.cmd(`ShutterPosition${n}`, String(pct), false);
        refreshSoon();
      };
    });

    const bindDelete = () => c.querySelectorAll(".sh-del").forEach((b) => {
      b.onclick = () => b.closest("tr").remove();
    });
    bindDelete();

    c.querySelector("#sh-add").onclick = () => {
      c.querySelector("#sh-table").insertAdjacentHTML("beforeend", row());
      bindDelete();
    };
    c.querySelector("#sh-save").onclick = async () => {
      const payload = { roller_shutters: [] };
      for (const tr of c.querySelectorAll(".sh-row")) {
        const relay1 = Number(tr.querySelector(".sh-relay").value);
        if (!relay1) return this._toast("Every shutter needs relay 1", true);
        payload.roller_shutters.push({
          name: tr.querySelector(".sh-name").value || "Shutter",
          relay1,
          inverted: tr.querySelector(".sh-inv").checked,
          open_duration: Number(tr.querySelector(".sh-open").value),
          close_duration: Number(tr.querySelector(".sh-close").value),
          mode: tr.querySelector(".sh-mode").value,
        });
      }
      const res2 = await this.cmd("SetRollerConfig", JSON.stringify(payload));
      this._toast(`Saved (${(res2 && res2.Count) ?? payload.roller_shutters.length} shutter(s))`);
      this._renderTab();
    };
    c.querySelector("#sh-clear").onclick = () => this._confirm(
      "Delete ALL roller shutter configuration?",
      async () => { await this.cmd("ClearRollerConfig"); this._toast("Shutters cleared"); this._renderTab(); });
  }

  // -- IR REMOTES (SMART IR, doc §29) ----------------------------------
  async _tabIR(c) {
    const res = await this.cmd("IRList");
    const list = (res && res.IRList) || [];

    const AC_MODES = [
      { base: 2, l: "Auto" }, { base: 14, l: "Heat" }, { base: 26, l: "Cool" },
    ];
    const acCard = (r) => `
      <div class="card ${r.enabled ? "" : "ir-disabled"}">
        <div class="timer-head"><b>${esc(r.name)}</b>
          <span class="chip">Air conditioner${r.enabled ? "" : " — disabled"}</span></div>
        <div class="actions">
          <button class="mini" data-irplay="${esc(r.name)}" data-irkey="1">⏻ Power ON / OFF</button>
        </div>
        <div class="form-grid" style="margin-top:10px">
          <label>Mode<select class="ir-mode">${AC_MODES.map((m) =>
            `<option value="${m.base}">${m.l}</option>`).join("")}</select></label>
          <label>Temperature<select class="ir-temp">${Array.from({ length: 12 }, (_, i) =>
            `<option value="${i}" ${i === 5 ? "selected" : ""}>${19 + i} °C</option>`).join("")}</select></label>
        </div>
        <div class="actions"><button class="mini secondary ir-send" data-remote="${esc(r.name)}">Send mode + temperature</button></div>
      </div>`;

    const univCard = (r) => `
      <div class="card ${r.enabled ? "" : "ir-disabled"}">
        <div class="timer-head"><b>${esc(r.name)}</b>
          <span class="chip">Universal${r.enabled ? "" : " — disabled"}</span></div>
        <p class="hint">Keys are numbered in the order they were learned (1 … N).</p>
        <div class="actions">
          <label>Key #<input class="ir-key" type="number" min="1" value="1" style="width:70px"></label>
          <button class="mini ir-send-univ" data-remote="${esc(r.name)}">Send key</button>
        </div>
      </div>`;

    c.innerHTML = `
      <div class="card">
        <h3>IR remotes</h3>
        <p class="hint">Remotes learned on this board (<code>IRList</code>). Sending a key replays the stored
        code with <code>IRPlay &lt;remote&gt; &lt;keyId&gt;</code>. AC key map: 1 = power,
        2-13 = Auto 19-30 °C, 14-25 = Heat, 26-37 = Cool. A disabled remote refuses all keys.</p>
      </div>
      ${list.length
        ? `<div class="timer-grid">${list.map((r) => (r.type === "ac" ? acCard(r) : univCard(r))).join("")}</div>`
        : `<div class="empty">No IR remote stored on this board yet.<br>
           Learn remotes from the device's own web interface first.</div>`}`;

    const play = async (remote, keyId) => {
      try {
        const resp = await this.cmd("IRPlay", `${remote} ${keyId}`);
        const err = resp && typeof resp.IRPlay === "string" && resp.IRPlay.startsWith("Error");
        this._toast(err ? resp.IRPlay : `Sent key ${keyId} on "${remote}"`, !!err);
      } catch (e) {
        this._toast(e.message || e, true);
      }
    };
    c.querySelectorAll("[data-irplay]").forEach((b) => {
      b.onclick = () => play(b.dataset.irplay, b.dataset.irkey);
    });
    c.querySelectorAll(".ir-send").forEach((b) => {
      b.onclick = () => {
        const card = b.closest(".card");
        const base = Number(card.querySelector(".ir-mode").value);
        const offset = Number(card.querySelector(".ir-temp").value);
        play(b.dataset.remote, base + offset);
      };
    });
    c.querySelectorAll(".ir-send-univ").forEach((b) => {
      b.onclick = () => {
        const key = b.closest(".card").querySelector(".ir-key").value;
        play(b.dataset.remote, key);
      };
    });
  }

  // -- ALARMS ----------------------------------------------------------
  async _tabAlarms(c) {
    const res = await this.cmd("AlarmConfig0");
    const list = (res && res.AlarmConfig) || [];
    c.innerHTML = `
      <div class="card">
        <h3>Alarm system</h3>
        <p class="hint">Each input can trigger a siren. Arming = index of the volatile variable that arms it.
        Only changed rows are written.</p>
        <table>
          <tr>${th("Input", "Physical input monitored by this alarm entry")}
              ${th("Mode", "Sensor wiring: NC (normally closed) triggers when the circuit opens, NO (normally open) triggers when it closes. Disabled ignores the input.")}
              ${th("Arming (Var)", "Index of the volatile variable that arms this alarm: the alarm only fires while that Var is 1. 0 = always armed.")}
              ${th("Siren type", "What the alarm activates when it fires: a relay output or a persistent variable (Mem)")}
              ${th("Siren idx", "Index of the relay or persistent variable used as the siren")}
              ${th("Duration (s)", "How long the siren stays active after the alarm fires")}
              ${th("Enabled", "Master switch for this alarm entry")}</tr>
          ${list.map((a) => `
            <tr data-alarm="${a.input}" data-orig='${esc(JSON.stringify(a))}'>
              <td>Input ${a.input}</td>
              <td><select class="al-mode">${ALARM_MODES.map((m) =>
                `<option value="${m.v}" ${m.v === a.mode ? "selected" : ""}>${m.l}</option>`).join("")}</select></td>
              <td><input class="al-arm" type="number" min="0" max="8" value="${a.arming ?? 0}" style="width:60px"></td>
              <td><select class="al-stype">${SIREN_TYPES.map((m) =>
                `<option value="${m.v}" ${m.v === a.siren_type ? "selected" : ""}>${m.l}</option>`).join("")}</select></td>
              <td><input class="al-sidx" type="number" min="0" value="${a.siren_idx ?? 0}" style="width:60px"></td>
              <td><input class="al-tempo" type="number" min="0" step="0.5" value="${a.tempo ?? 0}" style="width:70px"></td>
              <td><input class="al-act" type="checkbox" ${a.activated ? "checked" : ""}></td>
            </tr>`).join("")}
        </table>
        <div class="actions">
          <button id="al-save">Save changes</button>
          <button id="al-clear" class="danger">Clear all alarms</button>
        </div>
      </div>`;

    c.querySelector("#al-save").onclick = async () => {
      const changed = [];
      c.querySelectorAll("tr[data-alarm]").forEach((tr) => {
        const orig = JSON.parse(tr.dataset.orig);
        const row = {
          input: Number(tr.dataset.alarm),
          mode: Number(tr.querySelector(".al-mode").value),
          arming: Number(tr.querySelector(".al-arm").value),
          siren_type: Number(tr.querySelector(".al-stype").value),
          siren_idx: Number(tr.querySelector(".al-sidx").value),
          tempo: Number(tr.querySelector(".al-tempo").value),
          activated: tr.querySelector(".al-act").checked ? 1 : 0,
        };
        const norm = {
          input: orig.input, mode: orig.mode, arming: orig.arming ?? 0,
          siren_type: orig.siren_type, siren_idx: orig.siren_idx ?? 0,
          tempo: orig.tempo ?? 0, activated: orig.activated ? 1 : 0,
        };
        if (JSON.stringify(row) !== JSON.stringify(norm)) changed.push(row);
      });
      if (!changed.length) return this._toast("Nothing changed");
      const res2 = await this.cmd("SetAlarmConfig", JSON.stringify(changed));
      this._toast(`Saved — ${(res2 && res2.alarm_count) ?? "?"} active alarm(s)`);
      this._renderTab();
    };
    c.querySelector("#al-clear").onclick = () => this._confirm(
      "Reset ALL alarm entries and delete the configuration file?",
      async () => { await this.cmd("ClearAlarmConfig"); this._toast("Alarms cleared"); this._renderTab(); });
  }

  // -- TIMERS ----------------------------------------------------------
  _timerActions(dev, output) {
    if (dev.family === "dimmer" && output >= 1 && output <= 8) {
      return [
        { v: 0, l: "Off" }, { v: 1, l: "On" }, { v: 2, l: "25 %" },
        { v: 3, l: "50 %" }, { v: 4, l: "75 %" }, { v: 5, l: "100 %" },
      ];
    }
    return [{ v: 0, l: "Off" }, { v: 1, l: "On" }, { v: 2, l: "Toggle" }, { v: 3, l: "Rule" }];
  }

  async _tabTimers(c, dev) {
    const maxTimers = Number(dev.capabilities.max_timers || 16);
    const globalRes = await this.cmd("Timers").catch(() => null);
    const globalOn = globalRes && String(globalRes.Timers).toUpperCase() === "ON";
    const reads = [];
    for (let i = 1; i <= maxTimers; i++) reads.push(this.cmd(`Timer${i}`).catch(() => null));
    const results = await Promise.all(reads);
    const timers = results.map((r, i) => (r && r[`Timer${i + 1}`]) || {});

    const outputHint = dev.family === "dimmer"
      ? "1-8 = AO1…AO8, 9-16 = Var1…Var8"
      : `1-${dev.capabilities.outputs || "N"} = relay`;

    const timerCard = (t, i) => {
      const n = i + 1;
      const days = (t.Days || "0000000").split("");
      const mode = Number(t.Mode ?? 0);
      const output = Number(t.Output ?? 1);
      return `
      <div class="card timer" data-timer="${n}">
        <div class="timer-head"><b>Timer ${n}</b>
          <label class="switchline"><input class="t-arm" type="checkbox" ${t.Arm ? "checked" : ""}> Armed</label></div>
        <div class="form-grid">
          <label>Mode<select class="t-mode">${TIMER_MODES.map((m) =>
            `<option value="${m.v}" ${m.v === mode ? "selected" : ""}>${m.l}</option>`).join("")}</select></label>
          <label>Time<input class="t-time" value="${esc(t.Time || "00:00")}"
            title="HH:MM, ±offset minutes for sun modes, or FDAMI bits for prayer mode"></label>
          <label>Window (min)<input class="t-window" type="number" min="0" max="15" value="${t.Window ?? 0}"></label>
          <label>Output <span class="hint-inline">(${outputHint})</span>
            <input class="t-output" type="number" min="1" max="16" value="${output}"></label>
          <label>Action<select class="t-action">${this._timerActions(dev, output).map((a) =>
            `<option value="${a.v}" ${a.v === Number(t.Action ?? 0) ? "selected" : ""}>${a.l}</option>`).join("")}</select></label>
          <label>Repeat<select class="t-repeat">
            <option value="1" ${t.Repeat ? "selected" : ""}>Weekly</option>
            <option value="0" ${!t.Repeat ? "selected" : ""}>Once</option></select></label>
        </div>
        <div class="days">${DAY_NAMES.map((d, di) =>
          `<label class="day"><input type="checkbox" class="t-day" data-day="${di}"
            ${days[di] === "1" ? "checked" : ""}>${d}</label>`).join("")}</div>
        <div class="actions"><button class="mini t-save">Save timer ${n}</button></div>
      </div>`;
    };

    c.innerHTML = `
      <div class="card">
        <label class="switchline">
          <input type="checkbox" id="t-global" ${globalOn ? "checked" : ""}>
          Timers feature enabled (a timer fires only if this AND its own Arm are on)
        </label>
      </div>
      <div class="timer-grid">${timers.map(timerCard).join("")}</div>
      <div class="card">
        <h3>Location (sunrise / sunset &amp; prayer times)</h3>
        <div class="form-grid">
          <label>City<input id="loc-city" placeholder="Casablanca"></label>
          <label>Country<input id="loc-country" placeholder="Morocco"></label>
          <label>Latitude<input id="loc-lat" type="number" step="0.0001" placeholder="33.5731"></label>
          <label>Longitude<input id="loc-lon" type="number" step="0.0001" placeholder="-7.5898"></label>
        </div>
        <div class="actions">
          <button id="loc-save" class="secondary">Save location</button>
          <button id="loc-prayers" class="secondary">Show prayer times</button>
        </div>
        <pre id="loc-out" class="json" hidden></pre>
      </div>
      <div class="actions"><button id="t-clear" class="danger">Clear all timers</button></div>`;

    c.querySelector("#t-global").onchange = async (e) => {
      await this.cmd("Timers", e.target.checked ? "1" : "0");
      this._toast(`Timers ${e.target.checked ? "enabled" : "disabled"}`);
    };

    c.querySelectorAll(".timer").forEach((card) => {
      const outInput = card.querySelector(".t-output");
      outInput.onchange = () => {
        const actions = this._timerActions(dev, Number(outInput.value));
        const sel = card.querySelector(".t-action");
        const cur = sel.value;
        sel.innerHTML = actions.map((a) => `<option value="${a.v}">${a.l}</option>`).join("");
        if ([...sel.options].some((o) => o.value === cur)) sel.value = cur;
      };
      card.querySelector(".t-save").onclick = async () => {
        const n = card.dataset.timer;
        const days = [...card.querySelectorAll(".t-day")].map((d) => (d.checked ? "1" : "0")).join("");
        const payload = {
          Arm: card.querySelector(".t-arm").checked ? 1 : 0,
          Mode: Number(card.querySelector(".t-mode").value),
          Time: card.querySelector(".t-time").value,
          Window: Number(card.querySelector(".t-window").value),
          Days: days,
          Repeat: Number(card.querySelector(".t-repeat").value),
          Output: Number(card.querySelector(".t-output").value),
          Action: Number(card.querySelector(".t-action").value),
        };
        await this.cmd(`Timer${n}`, JSON.stringify(payload));
        this._toast(`Timer ${n} saved`);
      };
    });

    c.querySelector("#loc-save").onclick = async () => {
      const v = (id) => c.querySelector(`#${id}`).value.trim();
      if (v("loc-city")) await this.cmd("PrayerCity", v("loc-city"));
      if (v("loc-country")) await this.cmd("PrayerCountry", v("loc-country"));
      if (v("loc-lat")) await this.cmd("Latitude", v("loc-lat"));
      if (v("loc-lon")) await this.cmd("Longitude", v("loc-lon"));
      this._toast("Location saved");
    };
    c.querySelector("#loc-prayers").onclick = async () => {
      const res = await this.cmd("PrayerTime");
      const out = c.querySelector("#loc-out");
      out.hidden = false;
      out.textContent = JSON.stringify(res, null, 2);
    };
    c.querySelector("#t-clear").onclick = () => this._confirm(
      "Reset ALL timers and disable the global timers feature?",
      async () => { await this.cmd("ClearTimers"); this._toast("Timers cleared"); this._renderTab(); });
  }

  // -- WEBHOOKS --------------------------------------------------------
  _webhookIoTypes(dev) {
    if (dev.family === "dimmer") {
      return [
        { v: 1, l: "Digital input (DI)" }, { v: 2, l: "Analog output (AO)" },
        { v: 3, l: "Volatile variable (Var)" },
      ];
    }
    const types = [
      { v: 1, l: "Physical input" }, { v: 2, l: "Relay output" },
      { v: 3, l: "Volatile variable (Var)" },
    ];
    if (dev.family === "relay") types.push({ v: 4, l: "Persistent variable (Mem)" });
    return types;
  }

  _webhookStates(dev, ioType) {
    if (dev.family === "dimmer" && Number(ioType) === 2) {
      return [
        { v: 1, l: "Settles at 100 %" }, { v: 2, l: "Settles at 0 %" },
        { v: 3, l: "Variation (new level)" },
      ];
    }
    return [
      { v: 1, l: "On (rising edge)" }, { v: 2, l: "Off (falling edge)" },
      { v: 3, l: "Any change" },
    ];
  }

  async _tabWebhooks(c, dev) {
    const res = await this.cmd("WebhookConfig0");
    const list = (res && res.WebhookConfig) || [];
    const ioTypes = this._webhookIoTypes(dev);

    const row = (w = { io_type: 1, io_index: 1, state: 1, activated: 1, url: "" }) => `
      <tr class="wh-row">
        <td><select class="wh-type">${ioTypes.map((t) =>
          `<option value="${t.v}" ${t.v === w.io_type ? "selected" : ""}>${t.l}</option>`).join("")}</select></td>
        <td><input class="wh-idx" type="number" min="1" max="8" value="${w.io_index ?? 1}" style="width:60px"></td>
        <td><select class="wh-state">${this._webhookStates(dev, w.io_type).map((s) =>
          `<option value="${s.v}" ${s.v === w.state ? "selected" : ""}>${s.l}</option>`).join("")}</select></td>
        <td><input class="wh-url" value="${esc((w.url || "").trim())}" placeholder="http://…/cm?cmnd=Power1 Toggle"></td>
        <td><input class="wh-act" type="checkbox" ${w.activated ? "checked" : ""}></td>
        <td><button class="mini danger wh-del">✕</button></td>
      </tr>`;

    c.innerHTML = `
      <div class="card">
        <h3>Webhooks</h3>
        <p class="hint">An HTTP GET fires when the monitored I/O changes state. On AO channels the webhook fires
        once per press after the level settles (~1 s). Saving writes the complete table.</p>
        <table id="wh-table">
          <tr>${th("I/O type", "Which kind of I/O is monitored: physical input, relay output, volatile variable (Var) or persistent variable (Mem)")}
              ${th("Index", "Index of the monitored input / output / variable")}
              ${th("Trigger", "Edge that fires the webhook: rising (On), falling (Off) or any change")}
              ${th("URL", "HTTP GET sent when the trigger fires — e.g. a Tasmota command or any REST endpoint")}
              ${th("Enabled", "Enable or pause this webhook without deleting it")}
              <th></th></tr>
          ${list.map(row).join("")}
        </table>
        <div class="actions">
          <button id="wh-add" class="secondary">Add webhook</button>
          <button id="wh-save">Save webhooks</button>
          <button id="wh-clear" class="danger">Clear all webhooks</button>
        </div>
      </div>`;

    const bind = () => {
      c.querySelectorAll(".wh-del").forEach((b) => { b.onclick = () => b.closest("tr").remove(); });
      c.querySelectorAll(".wh-type").forEach((sel) => {
        sel.onchange = () => {
          const states = this._webhookStates(dev, sel.value);
          const ssel = sel.closest("tr").querySelector(".wh-state");
          const cur = ssel.value;
          ssel.innerHTML = states.map((s) => `<option value="${s.v}">${s.l}</option>`).join("");
          if ([...ssel.options].some((o) => o.value === cur)) ssel.value = cur;
        };
      });
    };
    bind();

    c.querySelector("#wh-add").onclick = () => {
      c.querySelector("#wh-table").insertAdjacentHTML("beforeend", row());
      bind();
    };
    const origRows = list.map((w) => ({
      io_type: Number(w.io_type), io_index: Number(w.io_index ?? 1),
      state: Number(w.state), activated: w.activated ? 1 : 0,
      url: (w.url || "").trim(),
    }));
    c.querySelector("#wh-save").onclick = async () => {
      const payload = [...c.querySelectorAll(".wh-row")].map((tr) => ({
        io_type: Number(tr.querySelector(".wh-type").value),
        io_index: Number(tr.querySelector(".wh-idx").value),
        state: Number(tr.querySelector(".wh-state").value),
        activated: tr.querySelector(".wh-act").checked ? 1 : 0,
        url: tr.querySelector(".wh-url").value.trim(),
      })).filter((w) => w.url);

      if (payload.length === origRows.length) {
        // Same row count: write only the entries that changed (WebhookConfig<N>)
        const changed = payload
          .map((row, i) => ({ row, n: i + 1 }))
          .filter(({ row, n }) => JSON.stringify(row) !== JSON.stringify(origRows[n - 1]));
        if (!changed.length) return this._toast("Nothing changed");
        for (const { row, n } of changed) {
          await this.cmd(`WebhookConfig${n}`, JSON.stringify(row));
        }
        this._toast(`Saved ${changed.length} webhook(s)`);
      } else {
        // Rows added or removed: rewrite the whole table
        const res2 = await this.cmd("SetWebhookConfig", JSON.stringify(payload));
        this._toast(`Saved — ${(res2 && res2.webhook_count) ?? payload.length} webhook(s)`);
      }
      this._renderTab();
    };
    c.querySelector("#wh-clear").onclick = () => this._confirm(
      "Delete ALL webhooks?",
      async () => { await this.cmd("ClearWebhookConfig"); this._toast("Webhooks cleared"); this._renderTab(); });
  }

  // -- THERMOSTATS -----------------------------------------------------
  async _tabThermostats(c, dev) {
    const [enableRes, vtRes] = await Promise.all([
      this.cmd("VThermostatEnable").catch(() => null),
      this.cmd("VThermostat").catch(() => null),
    ]);
    const enabled = enableRes && String(enableRes.VThermostatEnable).toUpperCase().startsWith("ON");
    const slots = {};
    if (vtRes) {
      Object.entries(vtRes).forEach(([k, v]) => {
        const m = k.toLowerCase().match(/^vthermostat(\d+)$/);
        if (m && typeof v === "object") slots[m[1]] = v;
      });
    }
    const maxVt = Number(dev.capabilities.max_vthermostats || 8);

    const slotCard = (n) => {
      const t = slots[n] || {};
      return `
      <div class="card vt" data-vt="${n}" data-orig='${esc(JSON.stringify(t))}'>
        <div class="timer-head"><b>Thermostat ${n}</b>
          <label class="switchline"><input class="vt-act" type="checkbox" ${t.active ? "checked" : ""}> Active</label></div>
        <div class="form-grid">
          <label>Name<input class="vt-name" maxlength="32" value="${esc(t.name || "")}"></label>
          <label>Setpoint (19-27 °C)<input class="vt-set" type="number" min="19" max="27" step="0.5" value="${t.set ?? 21}"></label>
          <label>Hysteresis (°C)<input class="vt-hyst" type="number" min="0" step="0.1" value="${t.hysteresis ?? t.hyst ?? 0.5}"></label>
          <label>Sensor index (0 = none)<input class="vt-sensor" type="number" min="0" max="8" value="${t.sensor ?? 0}"></label>
          <label>Relay (0 = none)<input class="vt-relay" type="number" min="0" value="${t.relay ?? 0}"></label>
          <label class="switchline" style="align-self:end"><input class="vt-inv" type="checkbox" ${t.inverse ? "checked" : ""}> Inverse relay</label>
        </div>
        ${t.ambient != null ? `<p class="hint">Ambient: <b>${t.ambient} °C</b> — relay ${esc(t.relay_state || "?")}</p>` : ""}
        <div class="actions"><button class="mini vt-save">Save thermostat ${n}</button></div>
      </div>`;
    };

    c.innerHTML = `
      <div class="card">
        <label class="switchline">
          <input type="checkbox" id="vt-global" ${enabled ? "checked" : ""}>
          Virtual thermostats enabled (global switch — <b>changing this restarts the device</b>)
        </label>
        <p class="hint">A thermostat regulates only if the global switch AND its own Active switch are on.
        ON when T &lt; setpoint − hyst, OFF when T &gt; setpoint + hyst.</p>
      </div>
      <div class="timer-grid">
        ${Array.from({ length: maxVt }, (_, i) => slotCard(String(i + 1))).join("")}
      </div>
      <div class="actions"><button id="vt-clear" class="danger">Clear all thermostats</button></div>`;

    c.querySelector("#vt-global").onchange = (e) => this._confirm(
      `${e.target.checked ? "Enable" : "Disable"} virtual thermostats? The device will restart.`,
      async () => { await this.cmd("VThermostatEnable", e.target.checked ? "1" : "0"); this._toast("Applied — device restarting"); },
      () => { e.target.checked = !e.target.checked; });

    c.querySelectorAll(".vt").forEach((card) => {
      card.querySelector(".vt-save").onclick = async () => {
        const n = card.dataset.vt;
        const orig = JSON.parse(card.dataset.orig);
        const cur = {
          name: card.querySelector(".vt-name").value,
          set: Number(card.querySelector(".vt-set").value),
          hyst: Number(card.querySelector(".vt-hyst").value),
          sensor: Number(card.querySelector(".vt-sensor").value),
          relay: Number(card.querySelector(".vt-relay").value),
          inverse: card.querySelector(".vt-inv").checked ? 1 : 0,
          active: card.querySelector(".vt-act").checked ? 1 : 0,
        };
        const payload = {};
        if (cur.name !== (orig.name || "")) payload.name = cur.name;
        if (cur.set !== orig.set) payload.set = cur.set;
        if (cur.hyst !== (orig.hysteresis ?? orig.hyst)) payload.hyst = cur.hyst;
        if (cur.sensor !== (orig.sensor ?? 0)) payload.sensor = cur.sensor;
        if (cur.relay !== (orig.relay ?? 0)) payload.relay = cur.relay;
        if (cur.inverse !== (orig.inverse ? 1 : 0)) payload.inverse = cur.inverse;
        if (cur.active !== (orig.active ? 1 : 0)) payload.active = cur.active;
        if (!Object.keys(payload).length) return this._toast("Nothing changed");
        const res = await this.cmd(`VThermostat${n}`, JSON.stringify(payload));
        const returned = res && (res[`vthermostat${n}`] || res[`VThermostat${n}`]);
        if (returned && returned.relay === 0 && cur.relay !== 0) {
          this._toast("Relay rejected — already used by another feature", true);
        } else {
          this._toast(`Thermostat ${n} saved`);
        }
        this._renderTab();
      };
    });
    c.querySelector("#vt-clear").onclick = () => this._confirm(
      "Erase all 8 thermostat configurations? (Global switch unchanged.)",
      async () => { await this.cmd("ClearVThermostat"); this._toast("Thermostats cleared"); this._renderTab(); });
  }

  // -- DATA LOGGER (pro) ----------------------------------------------
  async _tabDataLogger(c, dev) {
    const [stateRes, sensorsRes] = await Promise.all([
      this.cmd("DataLogger").catch(() => null),
      this.cmd("DataLoggerSensors").catch(() => null),
    ]);
    const on = stateRes && String(stateRes.DataLogger).toUpperCase() === "ON";
    const sel = (sensorsRes && sensorsRes.DataLoggerSensors) || {};
    const detected = Object.entries((dev.status_all || {}).sensors || {})
      .filter(([, v]) => typeof v === "object");

    const slotSel = (id, cur) => `
      <select id="${id}">
        <option value="0" ${cur === 0 ? "selected" : ""}>None</option>
        ${detected.map(([k], i) =>
          `<option value="${i + 1}" ${cur === i + 1 ? "selected" : ""}>${i + 1} — ${esc(k)}</option>`).join("")}
      </select>`;

    c.innerHTML = `
      <div class="card">
        <h3>Temperature data logger</h3>
        <p class="hint">Samples up to 2 DS18x20 sensors every 5 minutes to CSV on LittleFS (~30 days FIFO).
        Select sensors first — with both slots None, enabling records nothing.</p>
        <div class="form-grid">
          <label>Slot 1${slotSel("dl-s1", sel.s1 ?? 0)}</label>
          <label>Slot 2${slotSel("dl-s2", sel.s2 ?? 0)}</label>
        </div>
        <div class="actions"><button id="dl-save">Save sensor slots</button></div>
        <label class="switchline"><input type="checkbox" id="dl-enable" ${on ? "checked" : ""}> Recording enabled</label>
        <p class="hint">View the chart on the device page <code>/ld</code>; download CSV via <code>/ld?download=1</code>.</p>
      </div>`;

    c.querySelector("#dl-save").onclick = async () => {
      const s1 = c.querySelector("#dl-s1").value;
      const s2 = c.querySelector("#dl-s2").value;
      const res = await this.cmd("DataLoggerSensors", `${s1},${s2}`);
      const body = res && res.DataLoggerSensors;
      if (body && body.error) this._toast(`Rejected: ${body.error}`, true);
      else this._toast("Sensor slots saved");
    };
    c.querySelector("#dl-enable").onchange = async (e) => {
      await this.cmd("DataLogger", e.target.checked ? "1" : "0");
      this._toast(`Recording ${e.target.checked ? "enabled" : "disabled"}`);
    };
  }

  // -- NETWORK ---------------------------------------------------------
  async _tabNetwork(c, dev) {
    const net = (dev.device_status || {}).network || {};
    const localModeRes = await this.cmd("NvLocalMode").catch(() => null);
    const localMode = localModeRes ? localModeRes.NvLocalMode : null;

    c.innerHTML = `
      <div class="grid">
        <div class="card"><h3>Current connection</h3>
          ${kv("Type", net.connection)} ${kv("IP", net.ip)} ${kv("Mode", net.addressing_mode)}
          <div class="actions">
            <button id="nw-wifi" class="secondary">Read WiFi settings</button>
            <button id="nw-eth" class="secondary">Read Ethernet settings</button>
          </div>
          <p class="hint">Each read only succeeds on the interface currently in use.</p>
          <div id="nw-out" hidden></div>
        </div>
        <div class="card"><h3>Broker mode (NvLocalMode)</h3>
          ${kv("Current mode", localMode === 0 ? "0 — Cloud (TLS, provisioned)"
            : localMode === 1 ? "1 — Local broker (this Home Assistant)" : "unknown")}
          <p class="hint">Switching back to cloud disconnects the device from this broker —
          Home Assistant loses it until it is switched to local again.</p>
          <div class="actions"><button id="nw-cloud" class="danger">Return device to cloud broker</button></div>
        </div>
      </div>
      <div class="card"><h3>Write WiFi settings</h3>
        <p class="hint">⚠ The device restarts after a write (10-15 s). IP 0.0.0.0 = DHCP.
        A wrong SSID/password can take the device offline.</p>
        <div class="form-grid">
          <label>SSID 1<input id="wf-ssid1"></label>
          <label>Password 1<input id="wf-pwd1" type="password"></label>
          <label>SSID 2 (fallback)<input id="wf-ssid2"></label>
          <label>Password 2<input id="wf-pwd2" type="password"></label>
          <label>Hostname<input id="wf-host"></label>
          <label>IP (0.0.0.0 = DHCP)<input id="wf-ip" value="0.0.0.0"></label>
          <label>Gateway<input id="wf-gw"></label>
          <label>Subnet<input id="wf-sn" value="255.255.255.0"></label>
          <label>DNS 1<input id="wf-dns1"></label>
          <label>DNS 2<input id="wf-dns2"></label>
        </div>
        <div class="actions"><button id="wf-save" class="danger">Write WiFi config (restarts device)</button></div>
      </div>`;

    const NICE_LABELS = {
      ssid1: "SSID 1", pwd1: "Password 1", ssid2: "SSID 2 (fallback)", pwd2: "Password 2",
      ssid: "SSID", hostname: "Hostname", ip: "IP address", gateway: "Gateway",
      subnet: "Subnet mask", dns1: "DNS 1", dns2: "DNS 2", mac: "MAC address",
      rssi: "RSSI (dBm)", signal: "Signal (%)", connection: "Connection",
      addressing_mode: "Addressing mode", mdns: "mDNS name", channel: "Channel",
    };
    const niceLabel = (k) =>
      NICE_LABELS[k.toLowerCase()] || k.replace(/_/g, " ").replace(/^./, (ch) => ch.toUpperCase());
    const showSettings = (title, data) => {
      const out = c.querySelector("#nw-out");
      out.hidden = false;
      let body = data;
      // Unwrap {"WifiSettings": {...}} style replies
      if (body && typeof body === "object") {
        const keys = Object.keys(body);
        if (keys.length === 1 && body[keys[0]] && typeof body[keys[0]] === "object") body = body[keys[0]];
      }
      if (!body || typeof body !== "object") {
        out.innerHTML = kv(title, String(body ?? "no data"));
        return;
      }
      out.innerHTML = `<h4 style="margin:14px 0 6px">${esc(title)}</h4>` +
        Object.entries(body).map(([k, v]) => {
          let val = v;
          if (/pwd|password/i.test(k) && val) val = "••••••••";
          else if (val !== null && typeof val === "object") val = JSON.stringify(val);
          return kv(niceLabel(k), val === "" ? "—" : val);
        }).join("");
    };
    c.querySelector("#nw-wifi").onclick = async () =>
      showSettings("WiFi settings", await this.cmd("WifiSettings"));
    c.querySelector("#nw-eth").onclick = async () =>
      showSettings("Ethernet settings", await this.cmd("EthSettings"));

    c.querySelector("#nw-cloud").onclick = () => this._confirm(
      "Send NvLocalMode 0? The device returns to the cloud broker and disappears from this Home Assistant.",
      async () => { await this.cmd("NvLocalMode 0", "", false); this._toast("Sent — rebooting to cloud mode"); });

    c.querySelector("#wf-save").onclick = () => {
      const get = (id) => c.querySelector(`#${id}`).value.trim();
      const payload = {};
      if (get("wf-ssid1")) payload.ssid1 = get("wf-ssid1");
      if (get("wf-pwd1")) payload.pwd1 = get("wf-pwd1");
      if (get("wf-ssid2")) payload.ssid2 = get("wf-ssid2");
      if (get("wf-pwd2")) payload.pwd2 = get("wf-pwd2");
      if (get("wf-host")) payload.hostname = get("wf-host");
      payload.ip = get("wf-ip") || "0.0.0.0";
      if (payload.ip !== "0.0.0.0") {
        if (get("wf-gw")) payload.gateway = get("wf-gw");
        if (get("wf-sn")) payload.subnet = get("wf-sn");
        if (get("wf-dns1")) payload.dns1 = get("wf-dns1");
        if (get("wf-dns2")) payload.dns2 = get("wf-dns2");
      }
      if (!payload.ssid1) return this._toast("SSID 1 is required", true);
      this._confirm(
        `Write WiFi config and restart the device?\n${JSON.stringify(payload, null, 2)}`,
        async () => { await this.cmd("WifiSettings", JSON.stringify(payload), false); this._toast("Sent — restarting (10-15 s)"); });
    };
  }

  // -- MAINTENANCE -----------------------------------------------------
  // Firmware update (OtaUrlDiscover + UpgradeDiscover), location for the sun
  // and prayer timers (GeoCity / GeoCountry), 8R / 16R hardware revision
  // (SmartHwRev).
  async _tabMaintenance(c, dev) {
    const info = (dev.device_status || {}).device_info || {};
    const [ota, city, country] = await Promise.all([
      this.cmd("OtaUrl", "", true, 4).catch(() => null),
      this.cmd("GeoCity", "", true, 4).catch(() => null),
      this.cmd("GeoCountry", "", true, 4).catch(() => null),
    ]);
    const url = (ota && (ota.OtaUrl || ota.OtaUrlDiscover)) || "";
    const rev = info.hardware_revision;
    const old16r = /^2\.2\./.test(info.firmware_version || "");
    c.innerHTML = `
      <div class="card"><h3>Firmware</h3>
        ${kv("Version installée", info.firmware_version)}
        <div class="form-grid" style="margin-top:12px">
          <label>Adresse du firmware<input id="mt-url" value="${esc(url)}"
            placeholder="https://newtron.tech/firmware/smart8r_last_fr.bin"></label>
        </div>
        ${url && !NEWTRON_FIRMWARE.test(url) ? `<p class="hint">⚠ L'adresse enregistrée dans la carte n'est pas
          un firmware Newtron (${esc(url)}) : la mettre à jour avec effacerait le firmware Newtron.
          Indiquez l'adresse du firmware Newtron de cette carte.</p>` : ""}
        <p class="hint" style="margin-top:10px">La carte télécharge le firmware à cette adresse, redémarre
          et revient en 1 à 2 minutes. Ne coupez pas son alimentation pendant ce temps.</p>
        <div class="actions"><button id="mt-upgrade">Mettre à jour le firmware</button></div>
      </div>
      <div class="card"><h3>Emplacement</h3>
        <p class="hint">La carte n'a pas de GPS : sa ville et son pays donnent sa position, utilisée par les
          minuteries au lever / coucher du soleil et aux heures de prière.</p>
        ${city === null && country === null ? `<p class="hint">⚠ Cette carte ne répond pas à GeoCity :
          son firmware est trop ancien, mettez-le à jour d'abord.</p>` : ""}
        <div class="form-grid">
          <label>Ville<input id="mt-city" value="${esc((city && city.GeoCity) || "")}" placeholder="Casablanca"></label>
          <label>Pays<input id="mt-country" value="${esc((country && country.GeoCountry) || "")}" placeholder="Maroc"></label>
        </div>
        <div class="actions"><button id="mt-geo">Enregistrer l'emplacement</button></div>
      </div>
      ${rev === undefined || rev === null ? "" : `<div class="card"><h3>Révision matérielle</h3>
        <p class="hint">Les SMART 8R et 16R existent en 2 versions (v1 : ancien stock, v2 : production
          actuelle) que la carte ne sait pas reconnaître seule. Sur une 16R, une mauvaise révision inverse
          les relais 1 à 12. Changer la révision redémarre la carte.</p>
        ${kv("Révision déclarée", rev ? `v${rev}` : "non déclarée")}
        <div class="form-grid" style="margin-top:12px">
          <label>Nouvelle révision<select id="mt-rev">
            <option value="1" ${rev === 1 ? "selected" : ""}>v1 — ancien stock</option>
            <option value="2" ${rev === 2 ? "selected" : ""}>v2 — production actuelle</option>
          </select></label>
        </div>
        <div class="actions"><button id="mt-rev-save">Déclarer la révision</button></div>
      </div>`}`;

    c.querySelector("#mt-upgrade").onclick = () => {
      const newUrl = c.querySelector("#mt-url").value.trim();
      if (!newUrl) return this._toast("Indiquez l'adresse du firmware", true);
      if (!NEWTRON_FIRMWARE.test(newUrl)) {
        return this._toast("Seul un firmware Newtron (https://newtron.tech/firmware/…) peut être installé d'ici", true);
      }
      this._confirm(`Mettre à jour le firmware de ${dev.serial_number} ?\n${newUrl}\n\nLa carte redémarre.`, async () => {
        if (newUrl !== url) await this.cmd("OtaUrlDiscover", newUrl);
        await this.cmd("UpgradeDiscover", "1", false);
        this._toast("Mise à jour lancée : la carte revient dans 1 à 2 minutes");
      });
    };
    c.querySelector("#mt-geo").onclick = async () => {
      const v = (id) => c.querySelector(id).value.trim();
      if (!v("#mt-city") || !v("#mt-country")) return this._toast("Indiquez la ville et le pays", true);
      try {
        await this.cmd("GeoCity", v("#mt-city"));
        await this.cmd("GeoCountry", v("#mt-country"));
        this._toast("Emplacement enregistré : la carte calcule sa position");
      } catch (e) {
        this._toast(e.message || e, true);
      }
    };
    if (c.querySelector("#mt-rev-save")) c.querySelector("#mt-rev-save").onclick = () => {
      const n = c.querySelector("#mt-rev").value;
      if (Number(n) === rev) return this._toast("Cette révision est déjà déclarée");
      this._confirm(`Déclarer ${dev.serial_number} en v${n} ? La carte redémarre.`, async () => {
        await this.cmd(`${old16r ? "Smart16RRev" : "SmartHWRev"} ${n}`, "", false);
        this._toast(`Révision v${n} envoyée : la carte redémarre`);
      });
    };
  }

  // -- CONFIGURATION (inventory) ---------------------------------------
  // Cards planned for this installation. Adding a card: pick its model, the
  // form then lists what that model has (R1..Rn, AO1..AOn, shutters) with a
  // name and a Home Assistant area for each. A listed card is added on its
  // own when it comes online; names and shutters are written to the card.
  async _tabConfig(c) {
    if (!this._models) this._models = await this._ws({ type: "newtron/models" });
    const models = this._models;
    const inv = await this._ws({ type: "newtron/inventory" });
    const areas = Object.values(this._hass.areas || {})
      .sort((a, b) => a.name.localeCompare(b.name));
    const STATUS = {
      configured: ["ok", "Ajoutée à HA"],
      online: ["warn", "En ligne — ajout en cours"],
      waiting: ["", "En attente de la carte"],
    };
    const badge = (s) => `<span class="badge ${STATUS[s][0]}">${STATUS[s][1]}</span>`;
    const modelLabel = (id, fallback) => (models[id] || {}).label || fallback || "—";
    // The card takes its address on its own (DHCP) and announces it on the broker.
    const ipCell = (card) => esc(card.live.ip || "—");
    const relayCount = (card) => {
      const outs = Object.values(card.outputs || {});
      const sh = Object.keys(card.shutters || {}).length;
      const shown = (group) => Object.values(card[group] || {}).filter((x) => x.used).length;
      const ent = shown("inputs"), cap = shown("sensors"), vars = shown("variables");
      if (!outs.length && !sh) return "—";
      return [`${outs.filter((o) => o.used).length} sorties`, ent && `${ent} entrée${ent > 1 ? "s" : ""}`,
        sh && `${sh} volet${sh > 1 ? "s" : ""}`,
        cap && `${cap} capteur${cap > 1 ? "s" : ""}`, vars && `${vars} variable${vars > 1 ? "s" : ""}`]
        .filter(Boolean).join(" · ");
    };
    const areaSelect = (cls, cur) =>
      `<select class="${cls}"><option value="">— automatique —</option>
        ${areas.map((a) => `<option value="${esc(a.area_id)}" ${a.area_id === cur ? "selected" : ""}>${esc(a.name)}</option>`).join("")}
      </select>`;
    const draft = this._cfgDraft;
    const lay = draft && draft.layout;
    const model = draft && models[draft.model_id];

    // Relays driving a shutter, recomputed from the shutters being edited.
    if (lay) {
      const taken = {};
      lay.shutters.forEach((s, i) => {
        if (s.relay1) { taken[s.relay1] = i + 1; taken[Number(s.relay1) + 1] = i + 1; }
      });
      lay.outputs.forEach((o) => { o.shutter = taken[o.index] || null; });
    }

    const outputsTable = () => {
      const prefix = lay.kind === "dimmer" ? "AO" : "R";
      return `
        <table>
          <tr>${th("Utilisé", "Décoché : la sortie est masquée dans Home Assistant")}
            ${th("Sortie")}
            ${th("Nom", lay.kind === "dimmer" ? "Écrit dans la carte (24 caractères max)"
              : "Écrit dans la carte (32 caractères max)")}
            ${th("Pièce", "Pièce Home Assistant. « Automatique » : la pièce citée dans le nom")}</tr>
          ${lay.outputs.map((o, i) => o.shutter ? `
            <tr class="muted"><td></td><td class="nowrap"><b>${prefix}${o.index}</b></td>
              <td colspan="2"><span class="badge">→ volet V${o.shutter}</span></td></tr>` : `
            <tr data-row="o${i}">
              <td><input type="checkbox" class="r-used" ${o.used ? "checked" : ""}></td>
              <td class="nowrap"><b>${prefix}${o.index}</b></td>
              <td><input class="r-name o-name" maxlength="${lay.kind === "dimmer" ? 24 : 32}" value="${esc(o.name || "")}"
                placeholder="${esc(o.card_name || `${prefix === "R" ? "Relais" : "Sortie AO"} ${o.index}`)}"></td>
              <td>${areaSelect("r-area", o.area_id)}</td></tr>`).join("")}
        </table>`;
    };
    const relayOptions = (cur) => {
      const opts = [];
      for (let r = 1; r < lay.outputs.length; r += 1) {
        opts.push(`<option value="${r}" ${Number(cur) === r ? "selected" : ""}>R${r} ↑ / R${r + 1} ↓</option>`);
      }
      return opts.join("");
    };
    const shuttersTable = () => `
      <p class="hint">${lay.shutters.length} / ${lay.max_shutters} volets max — chaque volet utilise
        2 relais qui se suivent : montée puis descente.</p>
      ${lay.shutters.length ? `<table>
        <tr>${th("Utilisé")}${th("Volet")}${th("Nom")}${th("Relais", "Relais de montée, le suivant sert à la descente")}
          ${th("Ouverture (s)", "Durée d'une ouverture complète")}${th("Fermeture (s)", "Durée d'une fermeture complète")}
          ${th("Pièce")}<th></th></tr>
        ${lay.shutters.map((s, i) => `<tr data-row="s${i}">
          <td><input type="checkbox" class="r-used" ${s.used ? "checked" : ""}></td>
          <td class="nowrap"><b>V${i + 1}</b></td>
          <td><input class="r-name o-name" maxlength="32" value="${esc(s.name || "")}"
            placeholder="${esc(s.card_name || `Volet ${i + 1}`)}"></td>
          <td><select class="s-relay"><option value="">—</option>${relayOptions(s.relay1)}</select></td>
          <td><input class="s-open" type="number" min="1" step="0.5" value="${s.open_duration ?? 30}" style="width:72px"></td>
          <td><input class="s-close" type="number" min="1" step="0.5" value="${s.close_duration ?? 30}" style="width:72px"></td>
          <td>${areaSelect("r-area", s.area_id)}</td>
          <td><button class="mini danger s-del" title="Supprimer ce volet">✕</button></td></tr>`).join("")}
      </table>` : `<p class="hint">Aucun volet.</p>`}
      ${lay.shutters.length < lay.max_shutters
        ? `<div class="actions"><button id="cfg-add-sh" class="secondary mini">+ Ajouter un volet</button></div>` : ""}`;

    const typeSelect = (cur) => `<select class="t-type">${SENSOR_TYPES.map(([v, l]) =>
      `<option value="${v}" ${(cur || "") === v ? "selected" : ""}>${l}</option>`).join("")}</select>`;
    const inputsTable = () => `
      <p class="hint">Entrées de la carte (boutons, interrupteurs, contacts). Cochez celles à afficher
        dans Home Assistant, puis choisissez ce qui est branché : l'entrée s'affiche comme tel.</p>
      <table>
        <tr>${th("Afficher", "Coché : l'entrée apparaît dans Home Assistant")}${th("Entrée")}
          ${th("Nom", "Nom dans Home Assistant")}${th("Type", "Ce qui est branché sur l'entrée")}${th("Pièce")}</tr>
        ${lay.inputs.map((r, i) => `<tr data-row="i${i}">
          <td><input type="checkbox" class="r-used" ${r.used ? "checked" : ""}></td>
          <td class="nowrap"><b>E${r.index}</b></td>
          <td><input class="r-name o-name" maxlength="32" value="${esc(r.name || "")}" placeholder="Entrée ${r.index}"></td>
          <td>${typeSelect(r.device_class)}</td>
          <td>${areaSelect("r-area", r.area_id)}</td></tr>`).join("")}
      </table>`;
    const gpioOf = (g) => g.code ?? g.card_code ?? 0;
    const gpioSelect = (g) => {
      const cur = gpioOf(g);
      const known = GPIO_PROBES.some(([v]) => v === cur);
      return `<select class="g-probe">${known ? "" : `<option value="${cur}" selected>Autre réglage de la carte (laissé tel quel)</option>`}
        ${GPIO_PROBES.map(([v, l]) => `<option value="${v}" ${v === cur ? "selected" : ""}>${l}</option>`).join("")}</select>`;
    };
    const sensorsTable = () => `
      <p class="hint">Capteurs branchés sur les <b>GPIO</b> de la carte. Choisissez la sonde branchée sur
        chaque GPIO : à l'enregistrement, la carte redémarre (15 s environ) pour la prendre en compte,
        puis ses mesures apparaissent dessous.</p>
      ${lay.gpios.length ? `<table>
        <tr>${th("GPIO", "Borne de la carte")}${th("Capteur branché")}</tr>
        ${lay.gpios.map((g, i) => `<tr data-gpio="${i}">
          <td class="nowrap"><b>GPIO ${g.pin}</b></td><td>${gpioSelect(g)}</td></tr>`).join("")}
      </table>` : `<p class="hint">${lay.source === "card"
        ? "La carte n'a pas répondu : rouvrez la carte dans un instant."
        : "La liste des GPIO s'affiche quand la carte est en ligne."}</p>`}
      <h4 class="sub">Mesures</h4>
      ${lay.sensors.length ? `<table>
        <tr>${th("Afficher", "Coché : la mesure apparaît dans Home Assistant")}${th("Sonde")}
          ${th("Nom", "Nom dans Home Assistant")}${th("Pièce")}</tr>
        ${lay.sensors.map((r, i) => `<tr data-row="t${i}">
          <td><input type="checkbox" class="r-used" ${r.used ? "checked" : ""}></td>
          <td class="nowrap"><b>${esc(r.label)}</b> <span class="hint-inline">${esc(r.measure || "")}</span></td>
          <td><input class="r-name o-name" maxlength="32" value="${esc(r.name || "")}" placeholder="${esc(r.card_name || r.label)}"></td>
          <td>${areaSelect("r-area", r.area_id)}</td></tr>`).join("")}
      </table>` : `<p class="hint">Aucune mesure reçue de la carte pour l'instant.</p>`}`;
    const variablesTable = () => `
      <p class="hint">Variables de la carte, utilisées par ses scénarios. <b>Var</b> : volatile, remise à 0
        au redémarrage. <b>Mem</b> : persistante. Cochez celles à afficher dans Home Assistant.</p>
      <table>
        <tr>${th("Afficher", "Coché : la variable apparaît dans Home Assistant")}${th("Variable")}
          ${th("Nom", "Nom dans Home Assistant")}${th("Pièce")}</tr>
        ${lay.variables.map((r, i) => `<tr data-row="v${i}">
          <td><input type="checkbox" class="r-used" ${r.used ? "checked" : ""}></td>
          <td class="nowrap"><b>${r.persistent ? "Mem" : "Var"} ${r.index}</b></td>
          <td><input class="r-name o-name" maxlength="32" value="${esc(r.name || "")}"
            placeholder="${r.persistent ? "Mem" : "Var"} ${r.index}"></td>
          <td>${areaSelect("r-area", r.area_id)}</td></tr>`).join("")}
      </table>`;
    // One tab per kind of item: relays, shutters, sensors, variables
    const sections = !lay ? [] : [
      ["sorties", lay.kind === "dimmer" ? "Sorties 0-10 V" : "Relais",
        lay.outputs.filter((o) => !o.shutter).length, outputsTable],
      ...(lay.inputs.length ? [["entrees", "Entrées", lay.inputs.length, inputsTable]] : []),
      ...(lay.kind === "relay" && lay.max_shutters ? [["volets", "Volets roulants", lay.shutters.length, shuttersTable]] : []),
      ...(lay.kind === "relay"
        ? [["capteurs", "Capteurs", lay.gpios.filter((g) => gpioOf(g)).length, sensorsTable]] : []),
      ...(lay.variables.length ? [["variables", "Variables", lay.variables.length, variablesTable]] : []),
    ];
    const section = sections.find((x) => x[0] === draft._section) || sections[0];

    const formFor = () => {
      if (!lay) return "";
      const head = lay.source === "card"
        ? `Configuration lue dans la carte${lay.online ? "" : " (hors ligne : dernière configuration connue)"}.
           En enregistrant, les noms et volets modifiés sont <b>écrits dans la carte</b>.`
        : draft.status === "configured"
          ? `La carte est <b>hors ligne</b> : cette configuration lui sera <b>envoyée dès son retour</b>
             sur le broker.`
          : `La carte n'est pas encore dans Home Assistant : cette configuration lui sera
             <b>envoyée dès son arrivée</b> sur le broker.`;
      const unconfirmed = model && model.confirmed === false
        ? `<p class="hint">⚠ Nombre de sorties du ${esc(model.label)} non publié sur la fiche technique :
           vérifiez-le. Une fois la carte en ligne, sa vraie configuration remplace ces lignes.</p>` : "";
      if (lay.kind === "ir" || lay.kind === "dali") {
        return `<p class="hint">${lay.kind === "ir"
          ? "Contrôleur infrarouge : un seul appareil dans Home Assistant. Les télécommandes s'apprennent dans l'onglet <b>IR remotes</b> une fois la carte ajoutée."
          : "Passerelle DALI : un seul appareil dans Home Assistant, placé dans la pièce choisie ci-dessus."}</p>`;
      }
      return `<p class="hint" style="margin-top:14px">${head}</p>${unconfirmed}
        <div class="soustabs" role="tablist">${sections.map(([id, label, n]) => `<button type="button"
          role="tab" aria-selected="${id === section[0]}" data-section="${id}"
          class="${id === section[0] ? "active" : ""}">${esc(label)}<span class="compte">${n}</span></button>`).join("")}</div>
        <div class="tableau">${section[3]()}</div>`;
    };

    c.innerHTML = `
      <div class="card"><h3>Cartes de l'installation</h3>
        <p class="hint">Préparez ici les cartes avant d'aller chez le client. Dès qu'une carte de
        cette liste se connecte au broker, elle est ajoutée à Home Assistant toute seule : les noms
        et les volets sont écrits dans la carte et chaque sortie va dans sa pièce.</p>
        ${inv.cards.length ? `<table>
          <tr><th>N° de série</th><th>Nom de la carte</th><th>Modèle</th><th>IP</th>
            <th>Configuration</th><th>Statut</th><th></th></tr>
          ${inv.cards.map((card) => `<tr>
            <td class="nowrap"><b>${esc(card.serial)}</b></td>
            <td>${esc(card.name || "—")}</td>
            <td>${esc(modelLabel(card.model_id || card.live.model_id, card.live.model || card.model))}</td>
            <td class="nowrap">${ipCell(card)}</td>
            <td>${relayCount(card)}</td>
            <td>${badge(card.status)}${card.pushed === false && card.status === "configured"
              ? ` <span class="badge warn" title="La carte n'a pas encore reçu la configuration">à envoyer</span>` : ""}</td>
            <td class="nowrap">
              <button class="mini secondary" data-edit="${esc(card.serial)}">Modifier</button>
              <button class="mini danger" data-del="${esc(card.serial)}">Retirer</button>
            </td></tr>`).join("")}
        </table>` : `<p class="hint">Aucune carte pour l'instant.</p>`}
        <div class="actions"><button id="cfg-new">+ Ajouter une carte</button></div>
      </div>

      ${draft ? `<div class="card"><h3>${draft._existing ? `Carte ${esc(draft.serial)}` : "Nouvelle carte"}</h3>
        <div class="form-grid">
          <label>La carte *
            <select id="cfg-model" ${draft._locked_model ? "disabled" : ""}>
              <option value="">— choisir le modèle —</option>
              ${Object.entries(models).map(([id, m]) =>
                `<option value="${id}" ${id === draft.model_id ? "selected" : ""}>${esc(m.label)}</option>`).join("")}
            </select></label>
          ${model ? `
          <label>N° de série *<input id="cfg-serial" placeholder="SMART16R-1504"
            value="${esc(draft.serial || "")}" ${draft._existing ? "disabled" : ""}></label>
          <label>Nom de la carte<input id="cfg-name" placeholder="Tableau RDC" value="${esc(draft.name || "")}"></label>
          ${model.kind === "ir" || model.kind === "dali" ? `<label>Pièce
            <select id="cfg-area"><option value="">— aucune —</option>
              ${areas.map((a) => `<option value="${esc(a.area_id)}" ${a.area_id === draft.area_id ? "selected" : ""}>${esc(a.name)}</option>`).join("")}
            </select></label>` : ""}
          <label>Notes<input id="cfg-notes" value="${esc(draft.notes || "")}"></label>
          <label>Ville<input id="cfg-city" value="${esc(draft.city || "")}"
            placeholder="${esc((lay && lay.card_city) || "Casablanca")}"></label>
          <label>Pays<input id="cfg-country" value="${esc(draft.country || "")}"
            placeholder="${esc((lay && lay.card_country) || "Maroc")}"></label>
          ${/^(8R|16R)/.test(draft.model_id || "") ? `<label>Révision matérielle
            <select id="cfg-rev"><option value="">${lay && lay.hardware_revision
              ? `— garder v${lay.hardware_revision} —` : "— ne pas changer —"}</option>
              <option value="1" ${draft.hw_rev === 1 ? "selected" : ""}>v1 — ancien stock</option>
              <option value="2" ${draft.hw_rev === 2 ? "selected" : ""}>v2 — production actuelle</option>
            </select></label>` : ""}` : ""}
        </div>
        ${model ? `<p class="hint-inline" style="margin-top:8px">${esc(model.label)} :
          ${model.outputs ? `${model.outputs} ${model.kind === "dimmer" ? "sorties 0-10 V" : "relais"}` : "pas de sortie"}${
          model.inputs ? ` · ${model.inputs} entrées` : ""}${model.max_shutters ? ` · ${model.max_shutters} volets max` : ""} · ${esc(model.network)}</p>` : ""}
        ${formFor()}
        <div class="actions">
          ${model ? `<button id="cfg-save">Enregistrer${lay && lay.source === "card" && lay.online ? " et envoyer à la carte" : ""}</button>` : ""}
          <button id="cfg-cancel" class="secondary">Annuler</button>
        </div>
      </div>` : ""}

      ${inv.unlisted.length ? `<div class="card"><h3>Cartes vues sur le broker, hors liste</h3>
        <p class="hint">Cartes connectées à ce broker ou déjà dans Home Assistant, mais absentes de la
        liste ci-dessus. Ajoutez-les pour configurer leurs sorties.</p>
        <table>
          <tr><th>N° de série</th><th>Modèle</th><th>IP</th><th>Statut</th><th></th></tr>
          ${inv.unlisted.map((u) => `<tr>
            <td class="nowrap"><b>${esc(u.serial)}</b></td>
            <td>${esc(modelLabel(u.live.model_id, u.live.model))}</td>
            <td>${esc(u.live.ip || "—")}</td>
            <td>${u.status === "configured" ? badge("configured")
              : `<span class="badge warn">Proposée dans « Découverts »</span>`}</td>
            <td><button class="mini" data-adopt="${esc(u.serial)}" data-model="${esc(u.live.model_id || "")}">Ajouter à la liste</button></td>
          </tr>`).join("")}
        </table></div>` : ""}`;

    const loadLayout = async (d) => {
      d.layout = d.model_id
        ? await this._ws({ type: "newtron/inventory/layout", serial: d.serial || "", model_id: d.model_id })
        : null;
      // The live card knows its own model better than the form.
      if (d.layout && d.layout.source === "card" && d.layout.model_id) {
        d.model_id = d.layout.model_id;
        d._locked_model = true;
      }
    };
    const openDraft = async (d) => {
      await loadLayout(d);
      this._cfgDraft = d;
      this._renderTab();
    };
    c.querySelector("#cfg-new").onclick = () => openDraft({});
    c.querySelectorAll("[data-edit]").forEach((b) => b.onclick = () => {
      const card = inv.cards.find((x) => x.serial === b.dataset.edit);
      openDraft({ ...card, model_id: card.model_id || card.live.model_id, _existing: true });
    });
    c.querySelectorAll("[data-adopt]").forEach((b) => b.onclick = () =>
      openDraft({ serial: b.dataset.adopt, model_id: b.dataset.model || null }));
    c.querySelectorAll("[data-del]").forEach((b) => b.onclick = () => this._confirm(
      `Retirer ${b.dataset.del} de la liste ? (l'appareil reste dans Home Assistant)`,
      async () => {
        await this._ws({ type: "newtron/inventory/delete", serial: b.dataset.del });
        this._toast("Carte retirée de la liste");
        this._renderTab();
      }));

    if (!draft) return;
    const q = (id) => c.querySelector(`#${id}`);
    const val = (id) => (q(id) ? q(id).value.trim() : "");
    const keepHeader = () => Object.assign(draft, {
      serial: draft._existing ? draft.serial : val("cfg-serial") || draft.serial,
      name: val("cfg-name"), notes: val("cfg-notes"),
      area_id: val("cfg-area") || null,
      city: val("cfg-city"), country: val("cfg-country"),
      hw_rev: val("cfg-rev") ? Number(val("cfg-rev")) : null,
    });
    // Keep what was typed across the periodic refresh.
    c.querySelectorAll(".form-grid input, .form-grid select:not(#cfg-model)").forEach((el) =>
      el.addEventListener("input", keepHeader));
    q("cfg-model").onchange = async (e) => {
      keepHeader();
      draft.model_id = e.target.value || null;
      await loadLayout(draft);
      this._renderTab();
    };
    // A serial typed for a card already online: read its real configuration.
    if (q("cfg-serial") && !draft._existing) q("cfg-serial").onchange = async () => {
      keepHeader();
      await loadLayout(draft);
      this._renderTab();
    };
    // Phone: the tab row scrolls sideways, keep the open tab in view
    const ouvert = c.querySelector(".soustabs .active");
    if (ouvert) ouvert.parentElement.scrollLeft = ouvert.offsetLeft - 16;
    c.querySelectorAll("[data-section]").forEach((b) => b.onclick = () => {
      keepHeader();
      draft._section = b.dataset.section;
      this._renderTab();
    });
    const ROWS = { o: "outputs", s: "shutters", i: "inputs", t: "sensors", v: "variables" };
    const rowOf = (tr) => lay[ROWS[tr.dataset.row[0]]][Number(tr.dataset.row.slice(1))];
    c.querySelectorAll("tr[data-gpio]").forEach((tr) => {
      tr.querySelector(".g-probe").onchange = (e) => { lay.gpios[Number(tr.dataset.gpio)].code = Number(e.target.value); };
    });
    c.querySelectorAll("tr[data-row]").forEach((tr) => {
      const row = rowOf(tr);
      tr.querySelector(".r-used").onchange = (e) => { row.used = e.target.checked; };
      tr.querySelector(".r-name").oninput = (e) => { row.name = e.target.value; };
      tr.querySelector(".r-area").onchange = (e) => { row.area_id = e.target.value || null; };
      const type = tr.querySelector(".t-type");
      if (type) type.onchange = (e) => { row.device_class = e.target.value || null; };
      if (tr.dataset.row[0] !== "s") return;
      tr.querySelector(".s-relay").onchange = (e) => {
        const r = Number(e.target.value) || null;
        const clash = r && lay.shutters.some((s) => s !== row && s.relay1
          && [s.relay1, s.relay1 + 1].some((x) => x === r || x === r + 1));
        if (clash) { this._toast(`R${r}/R${r + 1} sont déjà pris par un autre volet`, true); e.target.value = row.relay1 || ""; return; }
        row.relay1 = r;
        this._renderTab();
      };
      tr.querySelector(".s-open").oninput = (e) => { row.open_duration = Number(e.target.value); };
      tr.querySelector(".s-close").oninput = (e) => { row.close_duration = Number(e.target.value); };
      tr.querySelector(".s-del").onclick = () => {
        lay.shutters.splice(lay.shutters.indexOf(row), 1);
        this._renderTab();
      };
    });
    if (q("cfg-add-sh")) q("cfg-add-sh").onclick = () => {
      keepHeader();
      lay.shutters.push({ used: true, name: "", relay1: null, open_duration: 30, close_duration: 30, area_id: null });
      this._renderTab();
    };
    q("cfg-cancel").onclick = () => { this._cfgDraft = null; this._renderTab(); };
    if (!q("cfg-save")) return;
    q("cfg-save").onclick = async () => {
      keepHeader();
      const serial = draft.serial;
      if (!serial) return this._toast("Le n° de série est obligatoire", true);
      if (lay && lay.shutters.some((s) => !s.relay1)) return this._toast("Choisissez les relais de chaque volet", true);
      const slot = (r) => ({ used: r.used, name: (r.name || "").trim() || null, area_id: r.area_id || null });
      const outputs = Object.fromEntries(((lay && lay.outputs) || [])
        .filter((o) => !o.shutter).map((o) => [String(o.index), slot(o)]));
      const shutters = Object.fromEntries(((lay && lay.shutters) || []).map((s, i) => [String(i + 1), {
        ...slot(s), relay1: Number(s.relay1), open_duration: Number(s.open_duration) || 30,
        close_duration: Number(s.close_duration) || 30,
      }]));
      const keyed = (group) => Object.fromEntries(((lay && lay[group]) || [])
        .map((r) => [String(r.key), group === "inputs" ? { ...slot(r), device_class: r.device_class || null } : slot(r)]));
      try {
        const saved = await this._ws({
          type: "newtron/inventory/save", serial,
          model_id: draft.model_id, model: (models[draft.model_id] || {}).label || null,
          name: draft.name, notes: draft.notes, area_id: draft.area_id || null,
          city: draft.city || null, country: draft.country || null, hw_rev: draft.hw_rev || null,
          outputs, shutters,
          inputs: keyed("inputs"), variables: keyed("variables"), sensors: keyed("sensors"),
          // Only GPIO set here are written; "as is on the card" stays untouched
          gpios: Object.fromEntries(((lay && lay.gpios) || []).filter((g) => g.code != null)
            .map((g) => [String(g.pin), g.code])),
        });
        this._cfgDraft = null;
        this._toast(saved.pushed ? `Carte ${serial} : configuration envoyée à la carte`
          : `Carte ${serial} enregistrée — envoi à la carte dès son arrivée`);
        await this._loadDevices(true);
        this._renderTab();
      } catch (e) {
        this._toast(e.message || e, true);
      }
    };
  }

  // -- CONSOLE ---------------------------------------------------------
  _tabConsole(c) {
    c.innerHTML = `
      <div class="card">
        <h3>Raw command console</h3>
        <p class="hint">Publishes <code>cmnd/&lt;topic&gt;/&lt;command&gt;</code> and shows the device's
        <code>stat</code> response. Any command from the API reference works.</p>
        <div class="form-grid">
          <label>Command<input id="con-cmd" placeholder="StatusAll" value="StatusAll"></label>
          <label>Payload (optional)<input id="con-payload" placeholder="e.g. TOGGLE or a JSON array"></label>
        </div>
        <div class="actions">
          <button id="con-send">Send</button>
          <label class="switchline"><input type="checkbox" id="con-wait" checked> Wait for response</label>
        </div>
        <pre id="con-out" class="json" hidden></pre>
      </div>`;
    c.querySelector("#con-send").onclick = async () => {
      const command = c.querySelector("#con-cmd").value.trim();
      const payload = c.querySelector("#con-payload").value;
      const wait = c.querySelector("#con-wait").checked;
      if (!command) return this._toast("Enter a command", true);
      const out = c.querySelector("#con-out");
      out.hidden = false;
      out.textContent = "…";
      try {
        const res = await this.cmd(command, payload, wait);
        out.textContent = wait ? JSON.stringify(res, null, 2) : "(sent, no wait)";
      } catch (e) {
        out.textContent = `⚠ ${e.message || e}`;
      }
    };
  }

  // -- helpers ---------------------------------------------------------
  _toast(msg, error = false) {
    const t = this.shadowRoot.getElementById("toast");
    t.textContent = msg;
    t.className = `toast show ${error ? "error" : ""}`;
    clearTimeout(this._toastTimer);
    this._toastTimer = setTimeout(() => { t.className = "toast"; }, 3200);
  }

  _confirm(message, onYes, onNo) {
    const ok = window.confirm(message);
    if (ok) Promise.resolve(onYes()).catch((e) => this._toast(e.message || e, true));
    else if (onNo) onNo();
  }
}

const STYLES = `
  :host { display: block; --nt-accent: #00b8a9; --nt-accent2: #ffb000;
    --nt-bg: var(--primary-background-color, #f3f4f6);
    --nt-card: var(--card-background-color, #fff);
    --nt-text: var(--primary-text-color, #1c1c1e);
    --nt-muted: var(--secondary-text-color, #6b7280);
    --nt-border: var(--divider-color, #e3e5e8);
    --nt-danger: #e5484d;
    font-family: var(--paper-font-body1_-_font-family, "Roboto", system-ui, sans-serif);
    color: var(--nt-text); }
  .layout { min-height: 100vh; background: var(--nt-bg); }
  header { display: flex; align-items: center; gap: 16px; padding: 14px 22px;
    background: linear-gradient(95deg, #0c2733, #0f3b3a);
    color: #fff; position: sticky; top: 0; z-index: 5; }
  .brand { font-weight: 700; font-size: 18px; letter-spacing: .3px; display: flex; align-items: center; gap: 8px; }
  .bolt { color: var(--nt-accent2); font-size: 20px; }
  header select { margin-left: auto; background: rgba(255,255,255,.12); color: #fff;
    border: 1px solid rgba(255,255,255,.25); border-radius: 8px; padding: 7px 10px; font-size: 14px; max-width: 360px; }
  header select option { color: #111; }
  .conn { font-size: 12px; padding: 3px 10px; border-radius: 999px; text-transform: uppercase; letter-spacing: .5px; }
  .conn.ok { background: rgba(0,184,169,.25); color: #6ff0e2; }
  .conn.bad { background: rgba(229,72,77,.25); color: #ffb3b5; }
  nav { display: flex; gap: 4px; padding: 8px 16px; background: var(--nt-card);
    border-bottom: 1px solid var(--nt-border); overflow-x: auto; position: sticky; top: 56px; z-index: 4; }
  nav button { border: 0; background: transparent; color: var(--nt-muted); padding: 9px 14px;
    border-radius: 8px; font-size: 14px; cursor: pointer; white-space: nowrap; font-weight: 500; }
  nav button:hover { background: rgba(0,0,0,.05); color: var(--nt-text); }
  nav button.active { background: var(--nt-accent); color: #04201d; font-weight: 700; }
  main { padding: 20px; max-width: 1080px; margin: 0 auto; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 16px; }
  .card { background: var(--nt-card); border: 1px solid var(--nt-border); border-radius: 14px;
    padding: 18px; margin-bottom: 16px; box-shadow: 0 1px 2px rgba(0,0,0,.04); }
  .card h3 { margin: 0 0 12px; font-size: 15px; letter-spacing: .2px; }
  .kv { display: flex; justify-content: space-between; gap: 12px; padding: 5px 0;
    border-bottom: 1px dashed var(--nt-border); font-size: 14px; }
  .kv:last-child { border-bottom: 0; }
  .kv span { color: var(--nt-muted); }
  .kv b { text-align: right; word-break: break-all; }
  table { width: 100%; border-collapse: collapse; font-size: 14px; }
  th { text-align: left; color: var(--nt-muted); font-weight: 600; padding: 6px 8px; border-bottom: 2px solid var(--nt-border); }
  td { padding: 6px 8px; border-bottom: 1px solid var(--nt-border); vertical-align: middle; }
  input, select { font: inherit; padding: 7px 9px; border: 1px solid var(--nt-border);
    border-radius: 8px; background: var(--nt-bg); color: var(--nt-text); box-sizing: border-box; }
  input:focus, select:focus { outline: 2px solid var(--nt-accent); outline-offset: -1px; }
  input[type=checkbox] { width: 18px; height: 18px; accent-color: var(--nt-accent); }
  input[type=range] { width: 160px; accent-color: var(--nt-accent); padding: 0; }
  .o-name, .wh-url { width: 100%; }
  button { font: inherit; font-weight: 600; padding: 9px 16px; border: 0; border-radius: 9px;
    background: var(--nt-accent); color: #04201d; cursor: pointer; }
  button:hover { filter: brightness(1.05); }
  button.secondary { background: rgba(0,0,0,.06); color: var(--nt-text); }
  button.danger { background: var(--nt-danger); color: #fff; }
  button.mini { padding: 5px 11px; font-size: 13px; border-radius: 7px; }
  .actions { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; margin-top: 14px; }
  .hint { color: var(--nt-muted); font-size: 13px; line-height: 1.5; margin: 0 0 12px; }
  .hint-inline { color: var(--nt-muted); font-size: 12px; }
  code { background: rgba(0,0,0,.06); padding: 1px 5px; border-radius: 5px; font-size: 12px; }
  .chips { display: flex; flex-wrap: wrap; gap: 6px; }
  .chip { background: rgba(0,184,169,.12); color: #0c7d72; border-radius: 999px; padding: 4px 10px; font-size: 12px; }
  .switchline { display: flex; align-items: center; gap: 9px; font-size: 14px; cursor: pointer; }
  .strip { display: flex; flex-wrap: wrap; gap: 8px; }
  .io { display: flex; flex-direction: column; align-items: center; justify-content: center;
    min-width: 52px; padding: 8px 6px; border-radius: 10px; background: var(--nt-bg);
    border: 1px solid var(--nt-border); font-weight: 700; font-size: 14px; }
  .io span { font-size: 10px; color: var(--nt-muted); font-weight: 600; }
  .io b { font-size: 11px; font-weight: 600; color: var(--nt-muted); }
  .io.on { background: rgba(0,184,169,.18); border-color: var(--nt-accent); color: #0c7d72; }
  .io.rel.on { background: rgba(255,176,0,.2); border-color: var(--nt-accent2); color: #9a6b00; }
  .io.ao.on { background: rgba(255,176,0,.18); border-color: var(--nt-accent2); }
  .io.out-ctl { cursor: pointer; user-select: none; }
  .io.out-ctl:hover { box-shadow: 0 0 0 2px var(--nt-accent2); }
  .io.out-ctl:active { transform: scale(.95); }
  .info { display: inline-flex; align-items: center; justify-content: center;
    width: 14px; height: 14px; margin-left: 5px; border-radius: 50%;
    background: var(--nt-muted); color: #fff; font-size: 10px; font-weight: 700;
    font-style: italic; cursor: help; vertical-align: middle; line-height: 1; }
  .info:hover { background: var(--nt-accent); }
  .ir-disabled { opacity: .55; }
  .ir-disabled button { pointer-events: none; filter: grayscale(1); }
  .form-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; }
  .form-grid label { display: flex; flex-direction: column; gap: 5px; font-size: 13px; color: var(--nt-muted); }
  .form-grid label input, .form-grid label select { color: var(--nt-text); }
  .timer-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(330px, 1fr)); gap: 16px; }
  .timer-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
  .days { display: flex; gap: 6px; margin-top: 12px; }
  .day { display: flex; flex-direction: column-reverse; align-items: center; gap: 3px; font-size: 11px; color: var(--nt-muted); }
  .slider-cell { display: flex; align-items: center; gap: 10px; }
  .slider-cell span { min-width: 42px; font-weight: 700; }
  .nowrap { white-space: nowrap; }
  h4.sub { margin: 18px 0 8px; font-size: 14px; }
  .soustabs { display: flex; gap: 4px; margin: 16px 0 14px; border-bottom: 1px solid var(--nt-border);
    overflow-x: auto; scrollbar-width: none; }
  .soustabs::-webkit-scrollbar { display: none; }
  .soustabs button { background: transparent; color: var(--nt-muted); border-radius: 9px 9px 0 0;
    padding: 9px 14px; border-bottom: 2px solid transparent; margin-bottom: -1px; display: inline-flex;
    align-items: center; gap: 8px; white-space: nowrap; }
  .soustabs button:hover { color: var(--nt-text); background: rgba(0,0,0,.04); filter: none; }
  .soustabs button.active { color: var(--nt-text); border-bottom-color: var(--nt-accent); }
  .soustabs .compte { font-size: 11px; font-weight: 700; padding: 1px 7px; border-radius: 999px;
    background: rgba(0,0,0,.07); color: var(--nt-muted); }
  .soustabs button.active .compte { background: var(--nt-accent); color: #04201d; }
  input:disabled, select:disabled { opacity: .45; cursor: not-allowed; }
  /* Phone: the table scrolls sideways instead of crushing the name column */
  .tableau { overflow-x: auto; -webkit-overflow-scrolling: touch; }
  .tableau table { min-width: 640px; }
  .tableau .o-name { min-width: 150px; }
  .tableau .hint { position: sticky; left: 0; max-width: calc(100vw - 64px); }
  tr.muted td { color: var(--nt-muted); }
  .badge { display: inline-block; padding: 3px 9px; border-radius: 999px; font-size: 12px;
    font-weight: 600; background: rgba(0,0,0,.06); color: var(--nt-muted); white-space: nowrap; }
  .badge.ok { background: rgba(0,184,169,.18); color: #0c7d72; }
  .badge.warn { background: rgba(255,176,0,.2); color: #9a6b00; }
  .badge.bad { background: rgba(229,72,77,.15); color: var(--nt-danger); }
  .json { background: #0c1b22; color: #b6f0e7; padding: 14px; border-radius: 10px; font-size: 12.5px;
    overflow: auto; max-height: 360px; white-space: pre-wrap; word-break: break-word; }
  .empty { text-align: center; color: var(--nt-muted); padding: 60px 20px; font-size: 15px; line-height: 1.6; }
  .loading { color: var(--nt-muted); padding: 30px; }
  .toast { position: fixed; left: 50%; bottom: 26px; transform: translate(-50%, 30px);
    background: #0f3b3a; color: #fff; padding: 12px 22px; border-radius: 10px; font-size: 14px;
    opacity: 0; pointer-events: none; transition: all .25s; box-shadow: 0 8px 28px rgba(0,0,0,.25); z-index: 20; }
  .toast.show { opacity: 1; transform: translate(-50%, 0); }
  .toast.error { background: var(--nt-danger); }
  @media (max-width: 600px) { main { padding: 12px; } header { padding: 12px 14px; } }
`;

customElements.define("newtron-panel", NewtronPanel);
