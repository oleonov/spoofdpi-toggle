"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { normalizeLine, normalizeDomains, hostMatches, proxyDecision, Toggle, PROXY, START_TIMEOUT_MS } = require("../extension/lib.js");

test("normalizeLine_variousForms_returnsBareHostname", () => {
  assert.equal(normalizeLine("LinkedIn.com"), "linkedin.com");
  assert.equal(normalizeLine("https://www.linkedin.com/in/x?y=1#z"), "www.linkedin.com");
  assert.equal(normalizeLine("linkedin.com:8443"), "linkedin.com");
  assert.equal(normalizeLine("*.licdn.com"), "licdn.com");
  assert.equal(normalizeLine(".licdn.com"), "licdn.com");
  assert.equal(normalizeLine("  münchen.de  "), "xn--mnchen-3ya.de");
});

test("normalizeLine_blankAndComment_returnsNull", () => {
  assert.equal(normalizeLine("   "), null);
  assert.equal(normalizeLine("# linkedin.com"), null);
});

test("normalizeLine_invalid_returnsUndefined", () => {
  for (const bad of ["foo bar", "localhost", "-a.com", "a..com", "*.", "a.com:xx", "http://", "exa_mple.com"]) {
    assert.equal(normalizeLine(bad), undefined, bad);
  }
});

test("normalizeDomains_mixed_savesValidAndReportsInvalid", () => {
  const r = normalizeDomains("linkedin.com\r\n# c\n\nfoo bar\nLINKEDIN.com\nlicdn.com");
  assert.deepEqual(r.valid, ["linkedin.com", "licdn.com"]);
  assert.deepEqual(r.invalid, ["foo bar"]);
});

test("hostMatches_subdomainsAndLookalikes", () => {
  const d = ["linkedin.com"];
  assert.ok(hostMatches("linkedin.com", d));
  assert.ok(hostMatches("www.linkedin.com", d));
  assert.ok(hostMatches("WWW.LinkedIn.com.", d));
  assert.ok(!hostMatches("notlinkedin.com", d));
  assert.ok(!hostMatches("linkedin.com.evil.org", d));
  assert.ok(!hostMatches("com", d));
});

test("proxyDecision_onAndOff", () => {
  const d = ["linkedin.com"];
  assert.deepEqual(proxyDecision("www.linkedin.com", "ON", d), PROXY);
  assert.deepEqual(proxyDecision("example.org", "ON", d), { type: "direct" });
  for (const s of ["OFF", "STARTING", "STOPPING"]) {
    assert.deepEqual(proxyDecision("www.linkedin.com", s, d), { type: "direct" });
  }
});

function setup() {
  const ports = [];
  const timers = new Map();
  let nextTimer = 1;
  const renders = [];
  let connectError = null;
  const toggle = new Toggle({
    connect() {
      if (connectError) throw connectError;
      const port = { sent: [], disconnected: false, postMessage(m) { this.sent.push(m); }, disconnect() { this.disconnected = true; } };
      ports.push(port);
      return port;
    },
    setTimer(fn, ms) { const id = nextTimer++; timers.set(id, { fn, ms }); return id; },
    clearTimer(id) { timers.delete(id); },
    render(v) { renders.push({ state: v.state, error: v.error }); },
  });
  return { toggle, ports, timers, renders, failConnect(e) { connectError = e; } };
}

function turnOn(t) {
  t.toggle.onClick();
  t.toggle.onMessage(t.ports[t.ports.length - 1], { ok: true });
}

test("click_off_startsAndSendsStartWithTimeout", () => {
  const t = setup();
  t.toggle.onClick();
  assert.equal(t.toggle.state, "STARTING");
  assert.deepEqual(t.ports[0].sent, [{ cmd: "start" }]);
  assert.equal([...t.timers.values()][0].ms, START_TIMEOUT_MS);
});

test("reply_okWhileStarting_goesOnAndClearsTimer", () => {
  const t = setup();
  turnOn(t);
  assert.equal(t.toggle.state, "ON");
  assert.equal(t.timers.size, 0);
  assert.equal(t.toggle.error, null);
});

test("reply_errorWhileStarting_goesOffWithErrorAndDisconnects", () => {
  const t = setup();
  t.toggle.onClick();
  t.toggle.onMessage(t.ports[0], { ok: false, error: "port 18080 is busy" });
  assert.equal(t.toggle.state, "OFF");
  assert.equal(t.toggle.error, "port 18080 is busy");
  assert.ok(t.ports[0].disconnected);
  assert.equal(t.timers.size, 0);
});

test("startTimeout_noReply_goesOffWithError", () => {
  const t = setup();
  t.toggle.onClick();
  [...t.timers.values()][0].fn();
  assert.equal(t.toggle.state, "OFF");
  assert.equal(t.toggle.error, "native host did not answer");
  assert.ok(t.ports[0].disconnected);
});

test("click_whileStarting_ignored", () => {
  const t = setup();
  t.toggle.onClick();
  t.toggle.onClick();
  assert.equal(t.ports.length, 1);
  assert.equal(t.toggle.state, "STARTING");
});

test("click_whileOn_sendsStopDisconnectsAndGoesOff", () => {
  const t = setup();
  turnOn(t);
  t.toggle.onClick();
  assert.deepEqual(t.ports[0].sent, [{ cmd: "start" }, { cmd: "stop" }]);
  assert.ok(t.ports[0].disconnected);
  assert.equal(t.toggle.state, "OFF");
  assert.equal(t.toggle.error, null);
});

test("click_afterError_clearsErrorAndRestarts", () => {
  const t = setup();
  t.toggle.onClick();
  t.toggle.onMessage(t.ports[0], { ok: false, error: "x" });
  t.toggle.onClick();
  assert.equal(t.toggle.state, "STARTING");
  assert.equal(t.toggle.error, null);
  assert.equal(t.ports.length, 2);
});

test("exited_whileStarting_goesOffWithError", () => {
  const t = setup();
  t.toggle.onClick();
  t.toggle.onMessage(t.ports[0], { event: "exited", code: 1 });
  assert.equal(t.toggle.state, "OFF");
  assert.match(t.toggle.error, /exited \(code 1\)/);
  assert.equal(t.timers.size, 0);
});

test("exited_whileOn_goesOffWithError", () => {
  const t = setup();
  turnOn(t);
  t.toggle.onMessage(t.ports[0], { event: "exited", code: 9 });
  assert.equal(t.toggle.state, "OFF");
  assert.match(t.toggle.error, /code 9/);
  assert.ok(t.ports[0].disconnected);
});

test("disconnect_whileStartingOrOn_goesOffWithError", () => {
  const a = setup();
  a.toggle.onClick();
  a.toggle.onDisconnect(a.ports[0]);
  assert.equal(a.toggle.state, "OFF");
  assert.ok(a.toggle.error);
  const b = setup();
  turnOn(b);
  b.toggle.onDisconnect(b.ports[0]);
  assert.equal(b.toggle.state, "OFF");
  assert.ok(b.toggle.error);
});

test("reply_okAfterTimeoutOff_ignored", () => {
  const t = setup();
  t.toggle.onClick();
  [...t.timers.values()][0].fn();
  t.toggle.onMessage(t.ports[0], { ok: true });
  assert.equal(t.toggle.state, "OFF");
});

test("reply_okFromStalePort_ignoredWhileNewAttemptStarting", () => {
  const t = setup();
  t.toggle.onClick();
  t.toggle.onMessage(t.ports[0], { ok: false, error: "x" });
  t.toggle.onClick();
  t.toggle.onMessage(t.ports[0], { ok: true });
  assert.equal(t.toggle.state, "STARTING");
  t.toggle.onDisconnect(t.ports[0]);
  t.toggle.onMessage(t.ports[0], { event: "exited", code: 1 });
  assert.equal(t.toggle.state, "STARTING");
  t.toggle.onMessage(t.ports[1], { ok: true });
  assert.equal(t.toggle.state, "ON");
});

test("reply_okWhileOn_ignored", () => {
  const t = setup();
  turnOn(t);
  t.toggle.onMessage(t.ports[0], { ok: false, error: "late" });
  assert.equal(t.toggle.state, "ON");
});

test("click_connectThrows_goesOffWithError", () => {
  const t = setup();
  t.failConnect(new Error("no host"));
  t.toggle.onClick();
  assert.equal(t.toggle.state, "OFF");
  assert.match(t.toggle.error, /no host/);
  assert.equal(t.timers.size, 0);
});
