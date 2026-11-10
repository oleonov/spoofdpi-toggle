"use strict";

const PROXY = { type: "http", host: "127.0.0.1", port: 18080 };
const START_TIMEOUT_MS = 10000;
const DEFAULT_DOMAINS = ["linkedin.com", "licdn.com"];
const HOSTNAME = /^(?!-)[a-z0-9-]{1,63}(?<!-)(\.(?!-)[a-z0-9-]{1,63}(?<!-))+$/;

// Returns the bare hostname for one options line, null for blank/comment, undefined if invalid.
function normalizeLine(line) {
  const text = line.trim().toLowerCase();
  if (text === "" || text.startsWith("#")) return null;
  const bare = text
    .replace(/^[a-z][a-z0-9+.-]*:\/\//, "")
    .replace(/[/?#].*$/, "")
    .replace(/:\d+$/, "")
    .replace(/^(\*\.|\.)/, "");
  try {
    const hostname = new URL("http://" + bare).hostname;
    return HOSTNAME.test(hostname) ? hostname : undefined;
  } catch (e) {
    return undefined;
  }
}

function normalizeDomains(text) {
  const valid = [];
  const invalid = [];
  for (const line of text.split(/\r?\n/)) {
    const domain = normalizeLine(line);
    if (domain === undefined) invalid.push(line.trim());
    else if (domain !== null && !valid.includes(domain)) valid.push(domain);
  }
  return { valid, invalid };
}

function hostMatches(hostname, domains) {
  const host = hostname.toLowerCase().replace(/\.$/, "");
  return domains.some((d) => host === d || host.endsWith("." + d));
}

function proxyDecision(hostname, state, domains) {
  return state === "ON" && hostMatches(hostname, domains) ? PROXY : { type: "direct" };
}

// effects: connect() -> port {postMessage, disconnect}; setTimer(fn, ms) -> handle;
// clearTimer(handle); render({state, error}). The owner forwards port events to
// onMessage/onDisconnect together with the port, which is how stale ports are told apart.
class Toggle {
  constructor(effects) {
    this.fx = effects;
    this.state = "OFF";
    this.error = null;
    this.port = null;
    this.timer = null;
  }

  onClick() {
    if (this.state === "OFF") this.start();
    else if (this.state === "ON") this.stop();
  }

  start() {
    this.error = null;
    this.state = "STARTING";
    try {
      this.port = this.fx.connect();
      this.port.postMessage({ cmd: "start" });
    } catch (e) {
      this.fail("cannot reach native host: " + e.message);
      return;
    }
    this.timer = this.fx.setTimer(() => this.onStartTimeout(), START_TIMEOUT_MS);
    this.fx.render(this);
  }

  stop() {
    const port = this.port;
    this.state = "STOPPING";
    port.postMessage({ cmd: "stop" });
    this.release();
    this.state = "OFF";
    this.fx.render(this);
  }

  onStartTimeout() {
    if (this.state === "STARTING") this.fail("native host did not answer");
  }

  onMessage(port, msg) {
    if (port !== this.port || !msg) return;
    if (msg.event === "exited") {
      if (this.state === "STARTING" || this.state === "ON") this.fail("spoofdpi exited (code " + msg.code + ")");
    } else if (this.state === "STARTING" && "ok" in msg) {
      if (!msg.ok) return this.fail(msg.error || "start failed");
      this.fx.clearTimer(this.timer);
      this.timer = null;
      this.state = "ON";
      this.fx.render(this);
    }
  }

  onDisconnect(port) {
    if (port !== this.port) return;
    if (this.state === "STARTING" || this.state === "ON") this.fail("native host disconnected");
  }

  fail(error) {
    this.release();
    this.state = "OFF";
    this.error = error;
    this.fx.render(this);
  }

  release() {
    if (this.timer !== null) this.fx.clearTimer(this.timer);
    this.timer = null;
    const port = this.port;
    this.port = null;
    if (port) port.disconnect();
  }
}

if (typeof module !== "undefined") {
  module.exports = { normalizeLine, normalizeDomains, hostMatches, proxyDecision, Toggle, PROXY, START_TIMEOUT_MS, DEFAULT_DOMAINS };
}
