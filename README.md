# SpoofDPI Toggle

A Firefox toolbar button for macOS that turns [SpoofDPI](https://github.com/xvzc/SpoofDPI) on and off.

SpoofDPI is a local proxy that fragments the TLS handshake so that ISP deep packet inspection (DPI)
cannot see which site you open. This extension starts it only when you need it and sends only the
sites you choose through it.

- **ON**: the extension starts SpoofDPI on `127.0.0.1:18080` through native messaging and routes only
  the domains from your list through it (default `linkedin.com`, `licdn.com`, subdomains included).
  Everything else goes direct.
- **OFF**: SpoofDPI is stopped and Firefox goes direct for everything.
- If SpoofDPI fails to start, the button returns to OFF and shows `!` with the error in its tooltip.

The macOS system proxy is never touched: SpoofDPI always runs with `--clean`, so it ignores any
`spoofdpi.toml` config, and the host refuses to start it while a leftover system-proxy state file exists.

## Requirements

- macOS (Apple Silicon or Intel), Firefox 140 or newer
- [Homebrew](https://brew.sh) and SpoofDPI: `brew install spoofdpi` (tested with 1.5.3)
- `/usr/bin/python3` from the Xcode Command Line Tools (already present if you use Homebrew; stdlib only)

## Install

1. Install SpoofDPI:

   ```sh
   brew install spoofdpi
   ```

2. Clone this repository and register the native host:

   ```sh
   git clone https://github.com/oleonov/spoofdpi-toggle.git
   cd spoofdpi-toggle
   ./install.sh
   ```

3. Download the signed `spoofdpi_toggle-*.xpi` from the
   [Releases](https://github.com/oleonov/spoofdpi-toggle/releases) page and open it in Firefox
   (drag it onto a window, or about:addons → gear → **Install Add-on From File…**).

4. Click the new toolbar button. If LinkedIn still does not open, see
   [Tuning for your ISP](#tuning-for-your-isp).

### What `install.sh` does

It writes two files and changes nothing else:

- `~/Library/Application Support/spoofdpi-toggle/host.sh`: a launcher that runs
  `host/spoofdpi_toggle_host.py` from this checkout with the resolved spoofdpi path
- `~/Library/Application Support/Mozilla/NativeMessagingHosts/spoofdpi_toggle.json`: the Firefox
  host manifest, allowed only for the extension id `spoofdpi-toggle@oleonov`

Firefox started from the Dock does not see your shell `PATH`, so `install.sh` finds spoofdpi once
(`PATH` first, then `/opt/homebrew/bin/spoofdpi`) and writes its absolute path into the launcher.
To use a spoofdpi from somewhere else, pass it explicitly:

```sh
SPOOFDPI_BIN=/path/to/spoofdpi ./install.sh
```

The launcher points into this checkout, so keep the repository where it is (or run `./install.sh` again
after moving it). Run it again as well if you install SpoofDPI after it. Running it twice is safe.

## Tuning for your ISP

By default SpoofDPI is started with settings that work against the author's ISP:

```
--dns-mode https --dns-https-url https://cloudflare-dns.com/dns-query --https-split-mode chunk --https-chunk-size 1
```

Different ISPs block differently. If the button turns ON but the sites still do not open, try other
settings. First find ones that work by hand (stop the extension first, it uses the same port):

```sh
spoofdpi --clean --listen-addr 127.0.0.1:18080 --https-split-mode sni
curl -sS -o /dev/null -w '%{http_code}\n' -x http://127.0.0.1:18080 https://www.linkedin.com
```

Then register them; `SPOOFDPI_ARGS` replaces the built-in tuning flags as a whole:

```sh
SPOOFDPI_ARGS="--https-split-mode sni --dns-mode https" ./install.sh
```

The new settings apply the next time you switch the button ON. Running `./install.sh`
without `SPOOFDPI_ARGS` returns to the built-in settings. See `spoofdpi --help` for all flags.
`--clean`, `--no-tui`, `--listen-addr`, `--app-mode`, `--config` and `--auto-configure-network` are
controlled by the extension and rejected in `SPOOFDPI_ARGS`.

## Usage

- Click the toolbar button to switch SpoofDPI on (badge `ON`) or off. Clicks are ignored while it is
  starting or stopping.
- The button is always OFF after a Firefox restart.
- Edit the domain list in the add-on's preferences (about:addons → SpoofDPI Toggle → Preferences):
  one domain per line, `#` starts a comment, subdomains are included. Invalid lines are reported
  and not saved. The last error is shown there too.

## Troubleshooting

- **Error details**: SpoofDPI's output is in `~/Library/Logs/spoofdpi-toggle.log` (overwritten on each start).
- **"SpoofDPI system-proxy state file /tmp/spoofdpi….state exists from another run"**: an earlier SpoofDPI
  run with `auto-configure-network` left a file that would make SpoofDPI rewrite the macOS proxy settings.
  Run `spoofdpi` once from a terminal (it restores the settings and removes the file), or delete the file.
- **"port 18080 is busy"**: something else listens on `127.0.0.1:18080`, often a SpoofDPI started by hand.
  Find it with `lsof -nP -iTCP:18080 -sTCP:LISTEN` and stop it.
- **"spoofdpi not found at …"**: install SpoofDPI, then run `./install.sh` again.
- **"SPOOFDPI_ARGS …"**: the flags given to `install.sh` are invalid; run it again with corrected ones.
- **"native host did not answer"** or **"native host disconnected"**: check that `./install.sh` ran and that both files
  above exist.

## Building and signing it yourself

Release Firefox only installs signed add-ons. For a quick test without signing, open
`about:debugging#/runtime/this-firefox`, click **Load Temporary Add-on…** and choose
`extension/manifest.json` (Firefox removes it on restart).

To sign your own build you need an addons.mozilla.org account. Add-on ids are unique on AMO, so first
replace `spoofdpi-toggle@oleonov` with your own id in both `extension/manifest.json` and `install.sh`,
and run `./install.sh` again. Create API keys at addons.mozilla.org → Developer Hub → Manage API Keys, then:

```sh
npm install
read -rs "WEB_EXT_API_KEY?JWT issuer: " && echo
read -rs "WEB_EXT_API_SECRET?JWT secret: " && echo
WEB_EXT_API_KEY="$WEB_EXT_API_KEY" WEB_EXT_API_SECRET="$WEB_EXT_API_SECRET" npm run sign
unset WEB_EXT_API_KEY WEB_EXT_API_SECRET
```

The keys are read without echo, so they never land in shell history. This signs the add-on as
unlisted (it is not published on AMO) and puts the `.xpi` into `dist/`.

Other scripts: `npm test` (extension logic tests), `npm run lint`, `npm run build` (unsigned zip in `dist/`).

## Development

```sh
/usr/bin/python3 -I -m unittest discover -s tests -p 'test_host.py' -v   # native host, fake spoofdpi
npm test                                                               # extension logic
/usr/bin/python3 -I tests/e2e_native.py   # live: real spoofdpi on 18080, curl to linkedin.com
```

## Uninstall

Remove the add-on in about:addons, then:

```sh
./uninstall.sh
```

It removes the two files written by `install.sh` and nothing else.

## License

[MIT](LICENSE)
