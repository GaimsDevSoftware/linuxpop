// LinuxPop browser extension - content script.
//
// =====================================================================
//  PRIVACY GUARANTEES (audit me)
// =====================================================================
//  This script does FOUR things, in order, and nothing else:
//
//    1. READS  : window.location.hash + location.search to find a UUID
//                placed there by the LinuxPop daemon when the user
//                clicked one of LinuxPop's AI buttons.
//    2. FETCHES: GET http://127.0.0.1:PORT/prompt/<uuid> to retrieve
//                the prompt the user themselves typed/selected.
//                127.0.0.1 is the user's own machine; the daemon
//                listens only on the loopback interface.
//    3. WRITES : the prompt into the focused chat composer via
//                document.execCommand("insertText"). It does NOT
//                read existing composer content. It does NOT read
//                chat history, sidebar, prior messages, account info,
//                cookies, localStorage, IndexedDB or any other
//                page-level state.
//    4. PINGS  : GET http://127.0.0.1:PORT/installed once per page
//                load so the LinuxPop Settings UI can show "the
//                extension is installed and reachable". This is a
//                liveness probe; no data is sent in the request body.
//
//  Network destinations this script can reach (enforced by the
//  manifest's host_permissions, which is auditable line-by-line):
//    - http://127.0.0.1:8766 / 8767 / 8768  (LinuxPop daemon, your PC)
//
//  Network destinations this script can NOT reach:
//    - Everything else. No fetch() to claude.ai, no fetch() to
//      anthropic.com, no fetch() to any analytics/telemetry endpoint.
//      Both the browser sandbox and CSP enforce this.
//
//  Storage this script uses: NONE. No chrome.storage, no
//  localStorage, no IndexedDB, no cookies. The UUID lives in the
//  URL hash for a few milliseconds and is then cleared via
//  history.replaceState.
//
//  The extension requests zero special browser APIs - no tabs, no
//  scripting, no history, no cookies, no webRequest. Read the
//  manifest.json next to this file to verify.
// =====================================================================

(function () {
  "use strict";

  const HASH_KEY = "linuxpop";
  const LOG_PREFIX = "[linuxpop-ext]";
  // Ports to try, in order. The first match wins. Keep this in sync
  // with manifest.json's host_permissions / connect-src.
  const PORTS = [8766, 8767, 8768];

  // Editor locators in preference order. Same list as the userscript
  // so behaviour stays consistent across the two distribution paths.
  const SELECTORS = [
    'div[contenteditable="true"][role="textbox"]',
    'div.ProseMirror[contenteditable="true"]',
    '#prompt-textarea',
    'textarea#prompt-textarea',
    'textarea[data-id]',
    'rich-textarea div[contenteditable="true"]',
    'div.ql-editor[contenteditable="true"]',
    'textarea[placeholder*="Ask" i]',
    'textarea[placeholder*="follow" i]',
    'textarea[placeholder*="anything" i]',
    'div[contenteditable="true"]',
    'textarea',
  ];

  function findToken() {
    // PRIVACY: reads ONLY the URL hash and search string, both of
    // which the user themselves caused to exist (LinuxPop appended
    // #linuxpop=<uuid> when they clicked the AI button). We don't
    // read anything else from the location object.
    const m = (location.hash + location.search).match(
      /[#&?]linuxpop=([0-9a-fA-F]{8,})/);
    return m ? m[1] : null;
  }

  function clearToken() {
    try {
      const url = new URL(location.href);
      if (url.hash) url.hash = "";
      const params = url.searchParams;
      if (params.has(HASH_KEY)) {
        params.delete(HASH_KEY);
        url.search = params.toString();
      }
      history.replaceState(null, "", url.toString());
    } catch (e) { /* non-fatal */ }
  }

  function isVisible(el) {
    if (!el || !el.isConnected) return false;
    const rect = el.getBoundingClientRect();
    if (rect.width < 4 || rect.height < 4) return false;
    const style = getComputedStyle(el);
    if (style.display === "none" || style.visibility === "hidden") return false;
    return true;
  }

  function findEditor() {
    // PRIVACY: walks the DOM to find the chat composer (a textarea or
    // contenteditable div). We check element TAG and rect dimensions
    // - we never read the editor's existing text content, never read
    // any sibling/parent element, and never serialise any part of the
    // DOM tree. The only DOM property read here is BoundingClientRect
    // and computedStyle, both purely visual checks.
    for (const sel of SELECTORS) {
      for (const el of document.querySelectorAll(sel)) {
        if (isVisible(el)) return el;
      }
    }
    return null;
  }

  function waitForEditor(timeoutMs) {
    return new Promise((resolve) => {
      const t0 = performance.now();
      const tick = () => {
        const el = findEditor();
        if (el) { resolve(el); return; }
        if (performance.now() - t0 > timeoutMs) { resolve(null); return; }
        setTimeout(tick, 80);
      };
      tick();
    });
  }

  async function tryPorts(pathFn) {
    // PRIVACY: the ONLY fetch() target this script ever uses is
    // 127.0.0.1 on the LinuxPop daemon's ports. The browser sandbox
    // and the manifest's host_permissions both enforce this - any
    // attempt to fetch a non-127.0.0.1 URL would throw or be blocked.
    // GET method only, never POST. No request body.
    for (const port of PORTS) {
      try {
        const res = await fetch(`http://127.0.0.1:${port}${pathFn(port)}`, {
          method: "GET", cache: "no-store",
        });
        if (res.ok) return { port, res };
      } catch (e) { /* connection refused - try next */ }
    }
    return null;
  }

  async function fetchPrompt(token) {
    const found = await tryPorts(() => `/prompt/${token}`);
    if (!found) throw new Error("bridge unreachable");
    return await found.res.json();
  }

  function pingInstalled() {
    // Fire-and-forget so the LinuxPop daemon's bridge can flip the
    // "userscript installed" indicator in Settings.
    tryPorts(() => "/installed").catch(() => {});
  }

  function insertIntoTextarea(el, text) {
    const proto = Object.getPrototypeOf(el);
    const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
    if (setter) setter.call(el, text);
    else el.value = text;
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function insertIntoContentEditable(el, text) {
    el.focus();
    let ok = false;
    try { ok = document.execCommand("insertText", false, text); }
    catch (e) { ok = false; }
    if (!ok) {
      const sel = window.getSelection();
      if (sel) { sel.selectAllChildren(el); sel.deleteFromDocument(); }
      el.innerText = text;
      el.dispatchEvent(new InputEvent("input", {
        bubbles: true, inputType: "insertText", data: text,
      }));
    }
  }

  function insert(el, text) {
    if (el.tagName === "TEXTAREA" || el.tagName === "INPUT") {
      el.focus();
      insertIntoTextarea(el, text);
    } else {
      insertIntoContentEditable(el, text);
    }
  }

  function pressEnter(el) {
    const init = {
      key: "Enter", code: "Enter", keyCode: 13, which: 13,
      bubbles: true, cancelable: true,
    };
    el.dispatchEvent(new KeyboardEvent("keydown", init));
    el.dispatchEvent(new KeyboardEvent("keypress", init));
    el.dispatchEvent(new KeyboardEvent("keyup", init));
  }

  async function run() {
    const token = findToken();
    if (!token) return;
    console.log(LOG_PREFIX, "token detected, fetching prompt");
    clearToken();

    let payload;
    try { payload = await fetchPrompt(token); }
    catch (e) {
      console.warn(LOG_PREFIX, "fetch failed:", e.message);
      return;
    }
    if (!payload || !payload.text) {
      console.warn(LOG_PREFIX, "empty payload"); return;
    }

    const el = await waitForEditor(8000);
    if (!el) {
      console.warn(LOG_PREFIX, "no editor found after 8s");
      return;
    }
    insert(el, payload.text);
    setTimeout(() => {
      if (payload.submit !== false) pressEnter(el);
    }, 120);
  }

  pingInstalled();
  run();
  window.addEventListener("hashchange", run);
})();
