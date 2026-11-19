# Smart Room Operations Agent

The local dashboard includes a bounded operations agent designed for a reliable
classroom demo.

## Architecture

The optional language model is a **planner**, not the source of sensor facts. It
chooses one explicit tool; local Python validates the arguments, executes the
tool, and formats the response from real TinyDB/hardware/network results.

Available tools are: current status, occupancy summary, today-vs-yesterday
comparison, high-occupancy event search, sensor health, AWS/TLS health, one
manual camera capture + NanoDet inference, bounded live threshold tuning, and
CSV export. There is **no arbitrary shell tool**.

## Enable the real AI planner

The code uses OpenAI's current Responses API. The dashboard also has a
deterministic fallback for the prepared demo prompts if Wi-Fi/API access fails.

```bash
cd ~/iot-project
git pull
.venv/bin/python -m pip install -r requirements-pi.txt
cp .env.agent.example .env.agent
nano .env.agent
```

Set:

```dotenv
OPENAI_API_KEY=your_project_api_key
OPENAI_MODEL=gpt-6-luna
```

Then restart the dashboard:

```bash
.venv/bin/python -m src.dashboard
```

The dashboard badge should say `AI planner · <model>`. If it says
`Local demo fallback`, the class-demo prompts still work, but the LLM planner
is not active. `.env.agent` is ignored by Git.

## 3-minute class demo

Start with several real sensor events already in TinyDB, open
`http://localhost:5000` (or `http://raspberrypi.local:5000`) and ask:

1. **How many people were detected in the last hour?**
2. **Show events with more than 1 person.**
3. **Was the room busier today than yesterday?**
4. **Run a sensor health check.**
5. **Why is AWS offline?** — this is useful even when AWS is healthy because the
   tool performs a real certificate/TLS reachability check.
6. **Change the person confidence threshold to 0.6.** — the bounded 0.20–0.90
   runtime setting applies to the next NanoDet inference without restarting.
7. **Export today's occupancy report.** — a CSV download button appears.
8. Optional finale: **Capture one image and run inference now.**

When the long-running monitor is active, the dashboard sends this request over a
small local filesystem command queue and the monitor reuses its already-open
Picamera2 instance. There is no need to stop the monitor. If the monitor is not
running, the dashboard safely falls back to opening the camera directly.

## One-command preflight

With the dashboard running, verify the class-demo routing without changing any
settings:

```bash
.venv/bin/python scripts/agent_preflight.py
```

It checks the agent status endpoint plus the current-status and last-hour
occupancy tools, then prints `PASS`.

## Reliability / safety

- TinyDB remains the local source of truth.
- Agent answers are formatted from tool outputs, not model-generated sensor facts.
- OpenAI failure automatically falls back to the deterministic classroom planner.
- Threshold changes are bounded and atomically stored in ignored
  `data/runtime-settings.json`.
- Manual captures remain local and do not create fake PIR events.
- The dashboard never competes with the monitor for Picamera2; a local command
  queue lets the monitor perform manual captures with the camera it already owns.
- AWS diagnosis uses the configured X.509 device certificate for a TLS handshake.
- CSV export is generated from TinyDB; users cannot provide a filesystem path.
- The agent never receives a generic shell, Git, or arbitrary-code tool.

## HTTP API

- `GET /api/agent/status`
- `POST /api/agent/chat`
- `GET /api/agent/report.csv?window=today`

Example:

```bash
curl -s -X POST http://127.0.0.1:5000/api/agent/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"How many people were detected in the last hour?"}'
```
