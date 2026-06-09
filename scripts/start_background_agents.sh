#!/bin/bash

# Script to start all background agents (research agent and agent gateway)
# This script starts the services in background and saves their PIDs to a file

set -e

# Color codes for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

print_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check if ANTHROPIC_API_KEY is set
if [ -z "$ANTHROPIC_API_KEY" ]; then
    print_error "ANTHROPIC_API_KEY environment variable is not set"
    print_error "Please set it with: export ANTHROPIC_API_KEY=sk-ant-..."
    exit 1
fi

# Get the script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_DIR="$( cd "$SCRIPT_DIR/.." && pwd )"
PID_FILE="$PROJECT_DIR/scripts/.agent_pids"

cd "$PROJECT_DIR"

print_info "Starting background agents..."

# Start the research agent in background
print_info "Starting research agent on port 9999..."
cd agents
uvicorn research_agent:app --port 9999 --reload > /tmp/research_agent.log 2>&1 &
RESEARCH_AGENT_PID=$!
cd "$PROJECT_DIR"

print_info "Research agent started with PID: $RESEARCH_AGENT_PID"
print_info "Research agent logs: /tmp/research_agent.log"

# Start the agent gateway in background
print_info "Starting agent gateway with minimal config..."
agentgateway -f config/config_minimal.yaml > /tmp/agent_gateway.log 2>&1 &
GATEWAY_PID=$!

print_info "Agent gateway started with PID: $GATEWAY_PID"
print_info "Agent gateway logs: /tmp/agent_gateway.log"

# Save PIDs to file
echo "$RESEARCH_AGENT_PID" > "$PID_FILE"
echo "$GATEWAY_PID" >> "$PID_FILE"

print_info "PIDs saved to: $PID_FILE"

# Wait for services to start up
print_info "Waiting for services to start up..."
sleep 5

# Check if research agent is running
if ! kill -0 $RESEARCH_AGENT_PID 2>/dev/null; then
    print_error "Research agent failed to start. Check /tmp/research_agent.log"
    exit 1
fi

# Check if agent gateway is running
if ! kill -0 $GATEWAY_PID 2>/dev/null; then
    print_error "Agent gateway failed to start. Check /tmp/agent_gateway.log"
    exit 1
fi

print_info "All background services started successfully"
print_info "Research agent: http://localhost:9999"
print_info "Agent Gateway: http://localhost:3000"
print_info "Admin UI: http://localhost:15000/ui/"
print_info ""
print_info "To stop the services, run: ./scripts/stop_background_agents.sh"
