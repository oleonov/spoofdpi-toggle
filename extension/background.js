"use strict";

let domains = DEFAULT_DOMAINS;

const toggle = new Toggle({
  connect() {
    const port = browser.runtime.connectNative("spoofdpi_toggle");
    port.onMessage.addListener((msg) => toggle.onMessage(port, msg));
    port.onDisconnect.addListener(() => toggle.onDisconnect(port));
    return port;
  },
  setTimer: (fn, ms) => setTimeout(fn, ms),
  clearTimer: (handle) => clearTimeout(handle),
  render({ state, error }) {
    const on = state === "ON";
    browser.browserAction.setIcon({ path: on ? "icons/on.svg" : "icons/off.svg" });
    browser.browserAction.setBadgeText({ text: on ? "ON" : error ? "!" : "" });
    const title = on ? "SpoofDPI: on" : error ? "SpoofDPI: off - " + error : "SpoofDPI: " + state.toLowerCase();
    browser.browserAction.setTitle({ title });
    browser.storage.local.set({ lastError: error || "" });
  },
});

browser.storage.local.get("domains").then((stored) => {
  if (Array.isArray(stored.domains)) domains = stored.domains;
});
browser.storage.onChanged.addListener((changes) => {
  if (changes.domains) domains = changes.domains.newValue || DEFAULT_DOMAINS;
});

browser.browserAction.onClicked.addListener(() => toggle.onClick());

browser.proxy.onRequest.addListener(
  (details) => proxyDecision(new URL(details.url).hostname, toggle.state, domains),
  { urls: ["<all_urls>"] }
);
browser.proxy.onError.addListener((error) => console.error("proxy error:", error.message));

browser.storage.local.set({ lastError: "" });
