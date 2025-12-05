#!/bin/bash
# Voice Assistant V2 - Server Startup Script

echo "=========================================="
echo "Voice Assistant V2 - Starting Server"
echo "=========================================="
echo ""

# Check if .env file exists
if [ ! -f .env ]; then
    echo "⚠ WARNING: .env file not found!"
    echo "Please copy .env.example to .env and add your API keys:"
    echo "  cp .env.example .env"
    echo "  nano .env"
    echo ""
    read -p "Continue anyway? (y/n) " -n 1 -r
    echo ""
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# Load environment variables
if [ -f .env ]; then
    export $(cat .env | grep -v '^#' | xargs)
    echo "✓ Loaded .env file"
fi

# Check Python version
python_version=$(python3 --version 2>&1 | awk '{print $2}')
echo "✓ Python version: $python_version"

# Check if dependencies are installed
if ! python3 -c "import fastapi" 2>/dev/null; then
    echo "⚠ Dependencies not installed!"
    echo "Installing from requirements_v2.txt..."
    pip install -r requirements_v2.txt
fi

# Start server
echo ""
echo "Starting V2 server on port 8001..."
echo "Press Ctrl+C to stop"
echo ""

python3 server_v2.py
