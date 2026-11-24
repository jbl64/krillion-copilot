// Smoke test for the userscript with a fake DOM (no browser needed). Run: node userscript/smoke.test.mjs
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const src = fs.readFileSync(new URL('./krillion-copilot.user.js', import.meta.url), 'utf8');

class El {
  constructor() { this.style = {}; this.children = []; this.listeners = {}; this.innerHTML = ''; this.textContent = ''; }
  appendChild(c) { this.children.push(c); }
  addEventListener(t, f) { this.listeners[t] = f; }
  click() { throw new Error('userscript must not click page elements'); }
  focus() { throw new Error('userscript must not focus page elements'); }
  dispatchEvent() { throw new Error('userscript must not dispatch events'); }
}

function setup() {
  const s = { prompt: null, typed: '', bodyText: '', posts: [], now: 1000, tick: null, suggestResponse: null };
  const promptEl = { get textContent() { return s.prompt; } };
  const inputEl = { get value() { return s.typed; }, set value(v) { throw new Error('userscript must not type'); } };
  const elements = [];
  const document = {
    createElement: () => { const e = new El(); elements.push(e); return e; },
    documentElement: new El(),
    querySelector: (sel) => (sel === '.prompt' ? (s.prompt == null ? null : promptEl) : sel === 'input.ds-input' ? inputEl : null),
    body: { get innerText() { return s.bodyText; } },
  };
  const ctx = {
    document, location: { pathname: '/daily' }, console,
    Date: { now: () => s.now },
    setInterval: (f) => { s.tick = f; },
    GM_xmlhttpRequest: (req) => {
      const body = JSON.parse(req.data);
      s.posts.push({ path: req.url.replace('http://localhost:8765', ''), body });
      const reply = req.url.endsWith('/suggest') ? s.suggestResponse : { ok: true };
      setTimeout(() => req.onload({ status: 200, responseText: JSON.stringify(reply) }), 0);
    },
  };
  vm.runInNewContext(src, ctx);
  const [panel, header, body] = elements;
  return { s, panel, header, body };
}

const flush = () => new Promise((r) => setTimeout(r, 5));
const RESPONSE = {
  round_id: 7, category: 'open_category', total_ms: 5400,
  candidates: [
    { answer: 'Pasta alla Gricia', why: 'Roman pasta with guanciale', p_valid: 1.0, flag: null },
    { answer: 'Zhug', why: 'Yemeni red chili sauce', p_valid: 0.7, flag: 'common', value: '312,696 km2' },
  ],
  dropped: [{ answer: 'Fake dish', valid_reason: 'not a real dish' }],
};

// 1. prompt detected -> /suggest called -> panel rendered with compact details
{
  const t = setup();
  t.s.suggestResponse = RESPONSE;
  t.s.tick(); assert.match(t.body.innerHTML, /Waiting for a prompt/);
  t.s.prompt = 'Name a pasta dish';
  t.s.tick(); await flush();
  assert.equal(t.s.posts[0].path, '/suggest');
  assert.equal(t.s.posts[0].body.prompt, 'Name a pasta dish');
  assert.match(t.body.innerHTML, /Pasta alla Gricia/);
  assert.match(t.body.innerHTML, /~70% sure/);          // only shown below 90%
  assert.match(t.body.innerHTML, /common/);
  assert.match(t.body.innerHTML, /312,696 km2/);
  assert.doesNotMatch(t.body.innerHTML, /zipf/);
  assert.match(t.body.innerHTML, /1 dropped/);
  assert.match(t.body.innerHTML, /5\.4 s/);
  // collapse toggle only touches our own panel
  t.header.listeners.click(); assert.equal(t.body.style.display, 'none');
  t.header.listeners.click(); assert.equal(t.body.style.display, '');
}

// 2. reveal: tier comes from the points, not from page text (page also contains a tier legend)
{
  const t = setup();
  t.s.suggestResponse = RESPONSE;
  t.s.prompt = 'Name a pasta dish'; t.s.bodyText = 'Legend: PLANKTON 10 · SCHOOLER 30 · RARE 60';
  t.s.tick(); await flush();
  t.s.typed = 'Gricia'; t.s.tick();
  t.s.typed = '';  // input clears after submit
  t.s.bodyText = 'Legend: PLANKTON 10 · SCHOOLER 30 · RARE 60 ... DEEP CUT +85 PTS'; t.s.tick(); await flush();
  const log = t.s.posts.filter((p) => p.path === '/log');
  assert.equal(log.length, 1);
  assert.deepEqual(log[0].body, { round_id: 7, typed_answer: 'Gricia', tier: 'DEEP CUT', points: 85, timed_out: false });
  t.s.tick(); await flush();
  assert.equal(t.s.posts.filter((p) => p.path === '/log').length, 1);   // logged once per round
}

// 3. timeout reveal
{
  const t = setup();
  t.s.suggestResponse = RESPONSE;
  t.s.prompt = 'p1'; t.s.tick(); await flush();
  t.s.bodyText = 'NOTHING LANDED'; t.s.tick(); await flush();
  const log = t.s.posts.find((p) => p.path === '/log');
  assert.deepEqual(log.body, { round_id: 7, typed_answer: '', tier: '', points: 0, timed_out: true });
}

// 4. a reveal left over from the previous round is not logged against the new round
{
  const t = setup();
  t.s.suggestResponse = RESPONSE;
  t.s.prompt = 'p1'; t.s.tick(); await flush();
  t.s.bodyText = '+60 PTS'; t.s.tick(); await flush();
  assert.equal(t.s.posts.filter((p) => p.path === '/log').length, 1);
  t.s.prompt = 'p2'; t.s.tick(); await flush();              // new prompt, old "+60 PTS" still on screen
  t.s.tick(); await flush();
  assert.equal(t.s.posts.filter((p) => p.path === '/log').length, 1);
  t.s.bodyText = ''; t.s.tick();                              // old reveal disappears
  t.s.bodyText = '+60 PTS'; t.s.tick(); await flush();        // a new, identical-looking reveal is now real
  assert.equal(t.s.posts.filter((p) => p.path === '/log').length, 2);
}

// 5. selector failure is reported instead of waiting forever
{
  const t = setup();
  t.s.now = 1000; t.s.tick(); assert.match(t.body.innerHTML, /Waiting for a prompt/);
  t.s.now = 1000 + 7000; t.s.tick();
  assert.match(t.body.innerHTML, /Can't find the prompt/);
  assert.match(t.body.innerHTML, /\.prompt/);
}

// 6. empty result and server errors are readable
{
  const t = setup();
  t.s.suggestResponse = { round_id: 1, category: 'data_property', total_ms: 6000, candidates: [], dropped: [{ answer: 'x' }] };
  t.s.prompt = 'p'; t.s.tick(); await flush();
  assert.match(t.body.innerHTML, /Nothing passed the checks/);
}

console.log('userscript smoke tests passed');
