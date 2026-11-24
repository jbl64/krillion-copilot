// ==UserScript==
// @name         Krillion copilot
// @namespace    local.krillion.copilot
// @version      0.2
// @description  Shows AI-suggested answers in a side panel. You still type every answer yourself.
// @match        https://krillion.io/*
// @match        https://www.krillion.io/*
// @grant        GM_xmlhttpRequest
// @connect      localhost
// @connect      127.0.0.1
// ==/UserScript==

(function () {
  'use strict';

  const SERVER = 'http://localhost:8765';

  // ---- Selector adapter: if the site changes its markup, fix it here only. ----
  const SEL = {
    prompt: '.prompt',
    input: 'input.ds-input',
  };
  // Tier points are fixed by the game, so the tier is derived from the "+N PTS" number, not from page text.
  const POINTS_TIER = { 10: 'PLANKTON', 15: 'TOO CLEVER', 30: 'SCHOOLER', 60: 'RARE', 85: 'DEEP CUT', 100: 'ONE IN A KRILLION' };
  const NO_PROMPT_WARN_MS = 6000;

  // This script only READS the page. It never types, clicks or submits anything on the page.
  const getPrompt = () => (document.querySelector(SEL.prompt)?.textContent || '').trim();
  const getTyped = () => document.querySelector(SEL.input)?.value || '';

  // ---- Panel (our own UI; the collapse toggle only affects this panel) ----
  const panel = document.createElement('div');
  panel.style.cssText = [
    'position:fixed', 'top:12px', 'right:12px', 'width:300px', 'max-height:80vh', 'overflow:auto',
    'z-index:2147483647', 'background:rgba(10,20,35,0.94)', 'color:#e8f1ff', 'font:13px/1.4 system-ui,sans-serif',
    'border:1px solid #3b6ea8', 'border-radius:8px', 'padding:10px', 'box-shadow:0 4px 16px rgba(0,0,0,.4)',
  ].join(';');
  const header = document.createElement('div');
  header.style.cssText = 'font-weight:bold;cursor:pointer;user-select:none';
  const body = document.createElement('div');
  body.style.cssText = 'margin-top:6px';
  panel.appendChild(header);
  panel.appendChild(body);
  document.documentElement.appendChild(panel);

  let collapsed = false;
  function paintHeader() { header.textContent = 'Krillion copilot ' + (collapsed ? '▸' : '▾'); }
  header.addEventListener('click', () => { collapsed = !collapsed; body.style.display = collapsed ? 'none' : ''; paintHeader(); });
  paintHeader();

  function render(html) { body.innerHTML = html; }
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  render('Waiting for a prompt…');

  function post(path, data) {
    return new Promise((resolve, reject) => {
      GM_xmlhttpRequest({
        method: 'POST', url: SERVER + path, data: JSON.stringify(data),
        headers: { 'Content-Type': 'application/json' }, timeout: 30000,
        onload: (r) => {
          if (r.status && r.status >= 400) return reject(new Error('server error ' + r.status));
          try { resolve(JSON.parse(r.responseText)); } catch (e) { reject(e); }
        },
        onerror: () => reject(new Error('server unreachable (is uvicorn running on port 8765?)')),
        ontimeout: () => reject(new Error('timeout')),
      });
    });
  }

  // ---- Rendering ----
  function candidateHtml(c) {
    const bits = [];
    if (c.value) bits.push(esc(c.value));
    if (c.p_valid != null && c.p_valid < 0.9) bits.push('~' + Math.round(c.p_valid * 100) + '% sure');
    if (c.expected_pts != null) bits.push('~' + Math.round(c.expected_pts) + ' pts');
    if (c.flag === 'common') bits.push('common');
    return '<li style="margin-bottom:5px"><b style="font-size:15px">' + esc(c.answer) + '</b>' +
      (bits.length ? ' <span style="opacity:.6">· ' + bits.join(' · ') + '</span>' : '') +
      (c.why ? '<br><span style="opacity:.65;font-size:12px">' + esc(c.why) + '</span>' : '') + '</li>';
  }

  function resultHtml(res) {
    const cands = res.candidates || [];
    const dropped = res.dropped || [];
    const head = '<div style="opacity:.6;font-size:12px">' + esc(res.category) + ' · ' + (res.total_ms / 1000).toFixed(1) + ' s</div>';
    const list = cands.length
      ? '<ol style="padding-left:20px;margin:6px 0">' + cands.map(candidateHtml).join('') + '</ol>'
      : '<div style="margin:6px 0;color:#ffd48a">Nothing passed the checks for this prompt.</div>';
    const drop = dropped.length
      ? '<details style="opacity:.6;font-size:12px"><summary>' + dropped.length + ' dropped</summary>' +
        dropped.slice(0, 12).map((c) => esc(c.answer) + (c.valid_reason ? ' (' + esc(c.valid_reason) + ')' : '')).join('<br>') +
        (dropped.length > 12 ? '<br>…' : '') + '</details>'
      : '';
    return head + list + drop;
  }

  // ---- Round tracking ----
  let currentPrompt = '';
  let roundId = null;
  let lastTyped = '';
  let logged = false;
  let staleSig = '';          // reveal text that was already on screen when the prompt changed: ignore it
  let noPromptSince = Date.now();

  // A reveal "signature": the "NOTHING LANDED" or "+N PTS" text currently on the page ('' if none).
  function revealSig() {
    const text = document.body.innerText || '';
    if (/NOTHING LANDED/i.test(text)) return 'NOTHING LANDED';
    const m = text.match(/\+\s*(\d+)\s*PTS/i);
    return m ? m[0].replace(/\s+/g, '') : '';
  }

  async function onNewPrompt(prompt) {
    currentPrompt = prompt;
    roundId = null;
    logged = false;
    lastTyped = '';
    staleSig = revealSig();
    render('Thinking about: <i>' + esc(prompt) + '</i>');
    try {
      const res = await post('/suggest', { prompt, mode: location.pathname });
      if (prompt !== currentPrompt) return; // a newer prompt arrived while waiting
      roundId = res.round_id;
      render(resultHtml(res));
    } catch (e) {
      if (prompt === currentPrompt) render('<span style="color:#ff9b9b">Error: ' + esc(e.message) + '</span>');
    }
  }

  // Log the reveal (tier + points, or timeout) once per round.
  function checkReveal(sig) {
    if (roundId == null || logged || !sig || sig === staleSig) return;
    const timedOut = sig === 'NOTHING LANDED';
    const points = timedOut ? 0 : parseInt(sig.replace(/\D/g, ''), 10);
    logged = true;
    post('/log', {
      round_id: roundId, typed_answer: lastTyped, tier: timedOut ? '' : (POINTS_TIER[points] || ''),
      points, timed_out: timedOut,
    }).catch(() => {});
  }

  function tick() {
    const typed = getTyped();
    if (typed) lastTyped = typed;
    const p = getPrompt();
    if (p) {
      noPromptSince = null;
      if (p !== currentPrompt) onNewPrompt(p);
    } else {
      if (noPromptSince == null) noPromptSince = Date.now();
      if (!currentPrompt && Date.now() - noPromptSince > NO_PROMPT_WARN_MS) {
        render('<span style="color:#ffd48a">Can\'t find the prompt on this page (selector <code>' + esc(SEL.prompt) +
               '</code>). The site markup may have changed: update <code>SEL</code> in the userscript.</span>');
      }
    }
    const sig = revealSig();
    if (staleSig && !sig) staleSig = '';   // old reveal went away: the next one is new even if it looks the same
    checkReveal(sig);
  }

  setInterval(tick, 400);
})();
