# LinuxPop browser extension — privacy policy

**Effective date:** 2026-05-30
**Applies to:** LinuxPop Send-to-AI bridge (Firefox, Chrome and
Chromium-derived browsers).

## TL;DR

**This extension does not collect, store, transmit, sell, or share any
personal data. Nothing leaves your computer.** Linux users have heard
that promise from a lot of software that turned out to be lying; the
guarantees on this page are technical, not marketing, and you can
verify every one of them yourself by reading the ~150 lines of source
code in `extensions/shared/content.js`.

## What the extension does

When you click an AI button in the LinuxPop popup on your Linux desktop,
the LinuxPop daemon queues your prompt in a local-only HTTP service on
your machine (`127.0.0.1:8766`, never on a public interface), then
opens a chat-site URL with a one-time UUID in the URL fragment. This
extension, running in the chat-site tab:

1. Reads the UUID from `window.location.hash`.
2. Fetches the prompt from your local LinuxPop daemon over the loopback
   address.
3. Pastes the prompt into the chat composer.
4. Optionally presses Enter.

That is the entire extension. It runs only on the chat sites listed
under "Sites" below.

## What the extension does NOT do

- **Does not read chat history.** The script's only DOM access is
  finding the composer element and writing into it. It never reads
  prior messages, sidebar content, account names, or any other page
  data.
- **Does not phone home.** The browser sandbox and the extension's
  `host_permissions` list (auditable in `manifest.json`) restrict
  outbound network traffic to `http://127.0.0.1:876{6,7,8}` only.
  Any attempt to `fetch()` an external URL would be blocked by the
  browser.
- **Does not request "tabs", "scripting", "history", "cookies", or any
  other broad WebExtension permission.** The `permissions` and
  `optional_permissions` arrays in the manifest are both empty.
- **Does not store anything.** No `chrome.storage`, no `localStorage`,
  no `IndexedDB`, no cookies. The UUID lives in the URL hash for a
  few milliseconds and is then cleared via `history.replaceState`.
- **Does not include analytics, telemetry, error reporting,
  fingerprinting, or any third-party SDK.** No external scripts are
  loaded. No third-party fonts.
- **Does not run on any site other than the four listed below.**
  The `content_scripts.matches` array in the manifest is the
  enforcement boundary.

## Sites where the extension runs

| Site | Why |
|---|---|
| `https://claude.ai/*` | LinuxPop "Ask Claude" button |
| `https://chatgpt.com/*` | LinuxPop "Ask ChatGPT" button |
| `https://gemini.google.com/*` | LinuxPop "Ask Gemini" button |
| `https://www.perplexity.ai/*` and `https://perplexity.ai/*` | LinuxPop "Ask Perplexity" button |

The extension is dormant on every other page you visit. It is not
loaded, does not run, and cannot observe anything outside these
domains.

## Data the LinuxPop daemon stores on your machine

This privacy policy covers the browser extension. The LinuxPop daemon
itself runs on your computer and is governed by its own behaviour, but
for clarity: it stores your snippets, clipboard history (if enabled),
and settings in `~/.config/linuxpop/` and `~/.cache/linuxpop/`. None of
that is transmitted off your machine by either the daemon or this
extension. The chat sites themselves (claude.ai, chatgpt.com, etc.)
receive only what you would have typed yourself - this extension is
the typing.

## Permission audit (you can verify)

Open the extension's manifest in any browser at
`about:debugging` (Firefox) or `chrome://extensions` → Details (Chrome)
and confirm:

- `permissions: []`
- `optional_permissions: []`
- `host_permissions: ["http://127.0.0.1:8766/*", "http://127.0.0.1:8767/*", "http://127.0.0.1:8768/*"]`
- `content_scripts.matches`: only the four chat hosts above

If any of those don't match what you see, that's a bug — please open
an issue on GitHub.

## Changes to this policy

If we ever change what the extension does in a way that affects
privacy, we will:

1. Bump the major version (e.g. 0.x → 1.0).
2. Update this file with the effective date of the new version.
3. Note the change in the release commit message and AMO/CWS
   changelog.

We will not silently add data collection. If you can verify the
manifest stayed empty and the content script stayed small, the
privacy promise holds.

## Contact

Source code, issues, and discussion:
https://github.com/GaimsDevSoftware/linuxpop

If you find anything in the extension that contradicts this policy,
please open a GitHub issue.
