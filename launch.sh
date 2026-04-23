#!/bin/bash

# Get the directory where the script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# Change to the script directory
cd "$SCRIPT_DIR"

# Check if uv is installed
if ! command -v uv &> /dev/null; then
    echo "Error: uv is not installed. Please install it first."
    exit 1
fi

# Check if Python virtual environment exists, if not create it
if [ ! -d ".venv" ]; then
    echo "Creating Python virtual environment..."
    uv venv
fi

# Activate the virtual environment
source .venv/bin/activate

# Install dependencies if needed
if [ ! -f "uv.lock" ]; then
    echo "Installing dependencies..."
    uv pip install -e .
fi

# Start the MCP server
echo "Starting MCP server (HTTP mode)..."
uv run python src/mcp-server/server_http.py
