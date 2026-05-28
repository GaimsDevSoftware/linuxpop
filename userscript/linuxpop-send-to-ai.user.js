// ==UserScript==
// @name         LinuxPop Send-to-AI bridge
// @namespace    https://github.com/GaimsDevSoftware/linuxpop
// @version      0.1.0
// @description  Receives prompts from the LinuxPop daemon and inserts them into the focused AI chat editor.
// @author       LinuxPop
// @match        https://claude.ai/*
// @match        https://chatgpt.com/*
// @match        https://chat.openai.com/*
// @match        https://gemini.google.com/*
// @match        https://www.perplexity.ai/*
// @match        https://perplexity.ai/*
// @connect      127.0.0.1
// @grant        GM_xmlhttpRequest
// @run-at       document-idle
// ==/UserScript==

(function () {
  "use strict";

  const BRIDGE = "http://127.0.0.1:__LINUXPOP_BRIDGE_PORT__";
  const HASH_KEY = "linuxpop";
  const LOG_PREFIX = "[linuxpop]";

  // Selectors are listed in preference order. The first one that finds
  // a visible, editable element wins. If the page is still mounting the
  // editor when we arrive, the locator loop retries for a few seconds.
  const SELECTORS = [
    // Claude (claude.ai) - ProseMirror inside [role="textbox"]
    'div[contenteditable="true"][role="textbox"]',
    'div.ProseMirror[contenteditable="true"]',
    // ChatGPT (chatgpt.com) - newer UI is contenteditable, older is textarea
    '#prompt-textarea',
    'textarea#prompt-textarea',
    'textarea[data-id]',
    // Gemini (gemini.google.com)
    'rich-textarea div[contenteditable="true"]',
    'div.ql-editor[contenteditable="true"]',
    // Perplexity
    'textarea[placeholder*="Ask" i]',
    'textarea[placeholder*="follow" i]',
    'textarea[placeholder*="anything" i]',
    // Generic fallback
    'div[contenteditable="true"]',
    'textarea',
  ];

  function findToken() {
    // Accepts both #linuxpop=UUID and ?linuxpop=UUID (in case some
    // browsers strip fragments before we see them).
    const m = (location.hash + location.search).match(
      /[#&?]linuxpop=([0-9a-fA-F]{8,})/);
    return m ? m[1] : null;
  }

  function clearToken() {
    // Replace the hash so a refresh doesn't re-fire and so the URL stays
    // tidy. Leaves history alone via replaceState.
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
    for (const sel of SELECTORS) {
      const candidates = document.querySelectorAll(sel);
      for (const el of candidates) {
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

  function fetchPrompt(token) {
    return new Promise((resolve, reject) => {
      GM_xmlhttpRequest({
        method: "GET",
        url: `${BRIDGE}/prompt/${token}`,
        timeout: 5000,
        onload: (res) => {
          if (res.status !== 200) {
            reject(new Error(`bridge returned ${res.status}`));
            return;
          }
          try {
            resolve(JSON.parse(res.responseText));
          } catch (e) { reject(e); }
        },
        onerror: () => reject(new Error("bridge unreachable")),
        ontimeout: () => reject(new Error("bridge timed out")),
      });
    });
  }

  function insertIntoTextarea(el, text) {
    // Native setter so React's onChange listener actually fires.
    const proto = Object.getPrototypeOf(el);
    const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
    if (setter) setter.call(el, text);
    else el.value = text;
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function insertIntoContentEditable(el, text) {
    el.focus();
    // execCommand("insertText") is the only path ProseMirror /
    // Lexical / Slate accept - the alternatives (Range mutations,
    // dispatched InputEvents) are flagged isTrusted=false and
    // discarded silently. Yes, execCommand is "deprecated", but
    // every modern rich-text editor still listens for it because
    // contenteditable's spec leaves no replacement.
    let ok = false;
    try {
      ok = document.execCommand("insertText", false, text);
    } catch (e) { ok = false; }
    if (!ok) {
      // Fallback: stuff the text in as plain text via the Selection
      // API. This won't fire ProseMirror's transaction pipeline but
      // at least leaves the prompt visible so the user can submit it.
      const selection = window.getSelection();
      if (selection) {
        selection.selectAllChildren(el);
        selection.deleteFromDocument();
      }
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
    // We send Enter so the chat actually submits. Most apps key on the
    // `keydown` event; we fire keydown + keypress + keyup to cover
    // every framework. Shift is explicitly NOT held - we want submit,
    // not newline.
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
    try {
      payload = await fetchPrompt(token);
    } catch (e) {
      console.warn(LOG_PREFIX, "fetch failed:", e.message);
      return;
    }
    if (!payload || !payload.text) {
      console.warn(LOG_PREFIX, "empty payload");
      return;
    }

    const el = await waitForEditor(8000);
    if (!el) {
      console.warn(LOG_PREFIX, "no editor found after 8s - dropping prompt");
      return;
    }

    insert(el, payload.text);

    // Small settle so React commits state before we submit.
    setTimeout(() => {
      if (payload.submit !== false) {
        pressEnter(el);
        console.log(LOG_PREFIX, "inserted and submitted");
      } else {
        console.log(LOG_PREFIX, "inserted (no auto-submit)");
      }
    }, 120);
  }

  // Single-shot on load. Hash navigation within an SPA also re-fires
  // hashchange, so we listen for that too in case the same tab is
  // re-used for the next send.
  run();
  window.addEventListener("hashchange", run);
})();
