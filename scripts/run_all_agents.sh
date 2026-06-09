#!/bin/bash

# Script to run all agents in background and then run the main coordinator agent
# This script starts the research agent and agent gateway in background,
# then runs the coordinator agent in the foreground.

set -e

# Generate timestamp for output file
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
OUTPUT_FILE="output_${TIMESTAMP}.txt"

# Redirect all script output to the timestamped file
exec > >(tee -a "$OUTPUT_FILE")
exec 2>&1

echo "Script output will be written to: $OUTPUT_FILE"
echo "========================================"
echo ""

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Function to print colored messages
print_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Function to cleanup background processes
cleanup() {
    print_info "Cleaning up background processes..."
    
    # Kill the research agent
    if [ -n "$RESEARCH_AGENT_PID" ]; then
        kill $RESEARCH_AGENT_PID 2>/dev/null || true
        print_info "Stopped research agent (PID: $RESEARCH_AGENT_PID)"
    fi
    
    # Kill the agent gateway
    if [ -n "$GATEWAY_PID" ]; then
        kill $GATEWAY_PID 2>/dev/null || true
        print_info "Stopped agent gateway (PID: $GATEWAY_PID)"
    fi
    
    # Wait for processes to terminate
    wait 2>/dev/null || true
    
    print_info "Cleanup complete"
}

# Set trap to call cleanup on script exit
trap cleanup EXIT INT TERM

# Check if ANTHROPIC_API_KEY is set
if [ -z "$ANTHROPIC_API_KEY" ]; then
    print_error "ANTHROPIC_API_KEY environment variable is not set"
    print_error "Please set it with: export ANTHROPIC_API_KEY=sk-ant-..."
    exit 1
fi

print_info "Starting all agents..."

# Get the script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_DIR="$( cd "$SCRIPT_DIR/.." && pwd )"

cd "$PROJECT_DIR"

# Start the research agent in background
print_info "Starting research agent on port 9999..."
cd agents
"uvicorn" research_agent:app --port 9999 --reload > /tmp/research_agent.log 2>&1 &
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
echo ""

# Run the coordinator agent in foreground
print_info "Starting coordinator agent..."
print_info "Press Ctrl+C to stop all services"
echo ""

"python" agents/coordinator_agent.py

# The coordinator agent will run until it completes
# Then cleanup will be called automatically via trap
