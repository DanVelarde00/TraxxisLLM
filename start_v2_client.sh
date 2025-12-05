#!/bin/bash
# Voice Assistant V2 - Client Startup Script

echo "=========================================="
echo "Voice Assistant V2 - Starting Client"
echo "=========================================="
echo ""

# Check if server is running
server_status=$(curl -s http://localhost:8001/status 2>/dev/null)
if [ $? -ne 0 ]; then
    echo "⚠ WARNING: V2 server is not running!"
    echo "Please start the server first:"
    echo "  ./start_v2_server.sh"
    echo ""
    read -p "Continue anyway? (y/n) " -n 1 -r
    echo ""
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
else
    echo "✓ Server is running"
fi

# Start client
echo ""
echo "Starting V2 client..."
echo ""
echo "Controls:"
echo "  Hold 'V' = Push-to-talk"
echo "  Press 'M' = Toggle mute"
echo "  Press 'Q' = Quit"
echo ""

python3 voice_assistant_v2.py
