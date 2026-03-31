#!/bin/bash
DATA_DIR=/app/agent_data
mkdir -p "$DATA_DIR"

# On startup, restore persisted data files to working directory
if ls "$DATA_DIR"/*_data.json 1>/dev/null 2>&1; then
    cp "$DATA_DIR"/*_data.json /app/
    echo "Restored persisted data files from $DATA_DIR"
fi

# On shutdown, save data files back to persistent volume
cleanup() {
    cp /app/*_data.json "$DATA_DIR/" 2>/dev/null
    echo "Saved data files to $DATA_DIR"
}
trap cleanup EXIT

exec python agent.py
