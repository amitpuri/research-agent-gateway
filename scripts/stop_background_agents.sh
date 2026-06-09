#!/bin/bash

# Script to stop background agents started by start_background_agents.sh

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

# Get the script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_DIR="$( cd "$SCRIPT_DIR/.." && pwd )"
PID_FILE="$PROJECT_DIR/scripts/.agent_pids"

if [ ! -f "$PID_FILE" ]; then
    print_warn "PID file not found: $PID_FILE"
    print_warn "No background agents to stop, or they were started manually"
    exit 0
fi

print_info "Stopping background agents..."

# Read PIDs from file
PIDS=()
while IFS= read -r line; do
    PIDS+=("$line")
done < "$PID_FILE"

# Kill each process
for PID in "${PIDS[@]}"; do
    if kill -0 "$PID" 2>/dev/null; then
        print_info "Stopping process with PID: $PID"
        kill "$PID"
    else
        print_warn "Process with PID $PID is not running"
    fi
done

# Wait for processes to terminate
sleep 2

# Remove PID file
rm -f "$PID_FILE"
print_info "PID file removed"

print_info "All background agents stopped"
