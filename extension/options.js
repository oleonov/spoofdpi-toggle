"use strict";

const area = document.getElementById("domains");
const status = document.getElementById("status");
const lastError = document.getElementById("lastError");

function showLastError(message) {
  lastError.textContent = message ? "Last error: " + message : "";
}

browser.storage.local.get(["domains", "lastError"]).then((stored) => {
  area.value = (stored.domains || DEFAULT_DOMAINS).join("\n");
  showLastError(stored.lastError);
});
browser.storage.onChanged.addListener((changes) => {
  if (changes.lastError) showLastError(changes.lastError.newValue);
});

document.getElementById("save").addEventListener("click", async () => {
  const { valid, invalid } = normalizeDomains(area.value);
  await browser.storage.local.set({ domains: valid });
  area.value = valid.join("\n");
  status.textContent = invalid.length
    ? "Saved " + valid.length + ". Invalid lines skipped: " + invalid.join(", ")
    : "Saved " + valid.length + " domains.";
});
