# Agent Management Scripts

This directory contains shell scripts to manage the research agent system.

## Scripts

### `run_all_agents.sh`
Main script that:
1. Starts the research agent in background (port 9999)
2. Starts the agent gateway in background (using minimal config)
3. Runs the coordinator agent in foreground

**Usage:**
```bash
./scripts/run_all_agents.sh
```

**Prerequisites:**
- Set `ANTHROPIC_API_KEY` environment variable
- Install dependencies: `pip install fastapi uvicorn anthropic httpx`
- Install Agent Gateway: `curl -sL https://agentgateway.dev/install | bash`

**Features:**
- Automatic cleanup on exit (Ctrl+C)
- Color-coded output
- Logs saved to `/tmp/research_agent.log` and `/tmp/agent_gateway.log`
- Error checking for service startup

### `start_background_agents.sh`
Starts only the background services (research agent + agent gateway) and saves their PIDs.

**Usage:**
```bash
./scripts/start_background_agents.sh
```

**Output:**
- Research agent: http://localhost:9999
- Agent Gateway: http://localhost:3000
- Admin UI: http://localhost:15000/ui/
- PIDs saved to `scripts/.agent_pids`

Use this when you want to run the coordinator agent separately or interact with the services manually.

### `stop_background_agents.sh`
Stops the background agents started by `start_background_agents.sh`.

**Usage:**
```bash
./scripts/stop_background_agents.sh
```

This script reads the PIDs from `scripts/.agent_pids` and stops the services.

## Manual Control

If you prefer manual control, you can start services individually:

**Terminal 1 - Research Agent:**
```bash
cd agents
uvicorn research_agent:app --port 9999 --reload
```

**Terminal 2 - Agent Gateway:**
```bash
agentgateway -f config/config_minimal.yaml
```

**Terminal 3 - Coordinator Agent:**
```bash
python agents/coordinator_agent.py
```

## Troubleshooting

### Services fail to start
Check the log files:
- Research agent: `/tmp/research_agent.log`
- Agent gateway: `/tmp/agent_gateway.log`

### Port already in use
If you see "address already in use" errors, stop the services:
```bash
./scripts/stop_background_agents.sh
# or manually kill processes using lsof or fuser
```

### ANTHROPIC_API_KEY not set
Set the environment variable:
```bash
export ANTHROPIC_API_KEY=sk-ant-...
```
