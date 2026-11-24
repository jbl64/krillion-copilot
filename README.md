# Krillion copilot

A Tampermonkey userscript reads the current Krillion prompt and shows AI-suggested rare answers in a side panel.
**You type every answer yourself.** The script only reads the page; it never types, clicks or submits an answer.

Pipeline (LangGraph): `route -> generate -> rule_check -> rarity -> validate`, logging every round to SQLite.
See CLAUDE.md for what each node does and the evaluation tooling.

## Setup
```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                   # then put your OpenAI key in .env
python -m pytest -q                                    # runs with a fake LLM, no key needed
uvicorn server.app:app --port 8765
```
Then in Chrome: install the Tampermonkey extension, create a new script, paste `userscript/krillion-copilot.user.js`,
save, and open krillion.io. If the script doesn't run, check that Tampermonkey is enabled for the site.
If the panel says it can't find the prompt, the site markup changed: fix `SEL` at the top of the userscript.
The panel shows each answer with a short reason; "~N% sure" appears only when the validator was unsure, and
"Dropped" (collapsed) lists answers the checks removed. Click the panel title to collapse it.
Offline test of the userscript logic (fake DOM, no browser): `node userscript/smoke.test.mjs`.

## Try the server without the browser
```bash
curl -s localhost:8765/suggest -H 'content-type: application/json' -d '{"prompt":"Name a pasta shape"}'
```
The response includes `total_ms` and per-node `calls` with latencies: this is the first latency measurement.

## Layout
- `server/llm.py` OpenAI wrapper (model from `KRILLION_MODEL`, default `gpt-4.1-mini`), fake mode for tests
- `server/graph.py` LangGraph nodes and prompts
- `server/db.py` SQLite logging (`krillion.db`, git-ignored)
- `server/app.py` `/suggest`, `/log`, `/health`
- `userscript/krillion-copilot.user.js` page reader and panel (selectors in one `SEL` block)

```mermaid
flowchart LR
  subgraph Browser
    US["userscript/<br/>krillion-copilot.user.js<br/>reads page, shows panel"]
  end
  subgraph server["server/ (FastAPI + LangGraph)"]
    APP["app.py<br/>/suggest /log /health"]
    GRAPH["graph.py<br/>nodes + prompts"]
    LLM["llm.py<br/>OpenAI wrapper, fake mode,<br/>JSON salvage"]
    RULES["rules.py<br/>letter-rule regex check"]
    RAR["rarity.py<br/>wordfreq flags"]
    DB["db.py<br/>SQLite logging"]
  end
  KDB[("krillion.db")]
  subgraph evaluation["evaluation/ (measure only)"]
    SH["sheets.py<br/>parse + fuzzy match"]
    RUN["run_eval.py<br/>score runs, rescore, tokens"]
    RES["results/*.json"]
  end
  SHEETS[("data/sheets/*.html<br/>saved day answers")]
  TESTS["tests/ with fake LLM<br/>+ userscript/smoke.test.mjs"]
  OAI(["OpenAI API"])
  US -->|"HTTP localhost:8765"| APP
  APP --> GRAPH
  GRAPH --> LLM --> OAI
  GRAPH --> RULES
  GRAPH --> RAR
  APP --> DB --> KDB
  SHEETS --> SH --> RUN
  RUN -->|"invokes"| GRAPH
  RUN --> RES
  TESTS -.-> APP
```


```mermaid
flowchart TD
  A["krillion.io shows the prompt"] --> B["Userscript polls every 400 ms<br/>reads the prompt text"]
  B -->|"POST /suggest"| C["route: LLM, ~1 s<br/>category + plan<br/>constraint prompts also get a regex rule and subject"]
  C --> D["generate: LLM, ~2-4 s<br/>8 niche-first answers, 20 for constraint<br/>fact prompts: bounds + numbers, code filters"]
  D --> E{"constraint prompt?"}
  E -->|yes| F["rule_check: code<br/>drop rule-breakers, keep 8"]
  E -->|no| G
  F --> G["rarity: code<br/>wordfreq flags, common answers last"]
  G --> H["validate: LLM, ~1-2 s<br/>p_valid per answer<br/>drop below 0.5, unsure ranked last"]
  H --> I[("round logged to SQLite")]
  H --> J["Panel shows the list<br/>you type the answer"]
  J --> K["Game reveals tier + points"]
  K -->|"POST /log"| L[("outcome saved")]
```


```mermaid
flowchart TD
  R["route returns a letter rule"] --> S{"regex compiles and passes<br/>its 4 sample answers?"}
  S -->|yes| OK["rule status ok: enforced in code"]
  S -->|no| RR["rule_repair: 1 extra LLM call<br/>shows the model the code's verdicts"]
  RR --> S2{"corrected rule passes<br/>the self-test?"}
  S2 -->|yes| OK
  S2 -->|no| SKIP["status stays failed:<br/>rule_check only truncates,<br/>validator still runs"]

  G["generate reply"] --> P{"valid JSON?"}
  P -->|yes| OKJ["use it"]
  P -->|"cut off at token cap"| SAL["parse_json salvage:<br/>keep the complete candidates"]
  SAL -->|"nothing usable"| EMPTY["empty list: panel says<br/>nothing passed the checks"]

  V["validate reply"] --> W{"usable?"}
  W -->|no| FO["fail open: keep all,<br/>p_valid unknown, no reordering"]
  W -->|yes| DROP["drop below 0.5,<br/>unsure behind confident"]

  subgraph Userscript
    U1["server unreachable, error, or 30 s timeout"] --> U2["panel shows the error,<br/>next prompt starts fresh"]
    U3["no .prompt element for 6 s"] --> U4["panel names the selector to fix"]
    U5["old reveal still on screen<br/>when the prompt changes"] --> U6["ignored until it changes"]
  end
```
