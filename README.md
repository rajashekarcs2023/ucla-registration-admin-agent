# UCLA Admin Agent

## Overview

The UCLA Admin Agent is an AI-powered administrative assistant for event organizers. It works in tandem with the UCLA Ticketing Agent to provide real-time event management and analytics.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         Event Flow                               │
└─────────────────────────────────────────────────────────────────┘

1. User registers via UBS Ticketing Agent
   └─> Order created on Eventbrite
   └─> Ticketing Agent sends signup notification to Admin Agent

2. Admin Agent receives and stores signup data
   └─> Stored in persistent JSON storage (survives container restarts)

3. Organizers authenticate with Admin Agent
   └─> Enter passphrase (configured in .env)
   └─> Address saved to authenticated_addresses (persistent)

4. Organizers query Admin Agent
   └─> "How many signups?"
   └─> "Who signed up through the agent?"
   └─> Agent uses OpenAI + Eventbrite API + stored signup data


┌─────────────────────────────────────────────────────────────────┐
│                     Component Architecture                       │
└─────────────────────────────────────────────────────────────────┘

┌──────────────────────┐         ┌──────────────────────┐
│  UBS Ticketing Agent │────────▶│   UCLA Admin Agent   │
│                      │  Chat   │                      │
│  - User registration │ Message │  - Signup tracking   │
│  - Order lookup      │  (JSON) │  - Analytics         │
│  - Eventbrite API    │         │  - Organizer queries │
└──────────────────────┘         └──────────────────────┘
                                          │
                                          │ Queries
                                          ▼
                                 ┌─────────────────┐
                                 │  Eventbrite API │
                                 │                 │
                                 │  - Event info   │
                                 │  - Ticket types │
                                 │  - Order data   │
                                 └─────────────────┘
```

## How Updates Work

### Real-time Signup Notifications

When a user registers through the ticketing agent:

1. **Ticketing Agent** completes Eventbrite registration
2. **Ticketing Agent** sends a `ChatMessage` to Admin Agent with JSON payload:
   ```json
   {
     "type": "signup_notification",
     "order_id": "14518610763",
     "email": "user@example.com",
     "name": "John Doe",
     "timestamp": "2026-03-23T07:25:51Z",
     "event_name": "UBS × Product Space × Fetch.ai AI Agent Challenge"
   }
   ```
3. **Admin Agent** receives message, extracts data, stores in `event_signups` array
4. **Persistent storage** ensures data survives container restarts via Docker volume mount

### Authentication System

**Passphrase-based** (not whitelist):
- UBS Ticketing Agent always allowed (by agent address)
- Organizers authenticate once with passphrase (set in `.env`)
- Authenticated addresses stored permanently in `authenticated_addresses`
- No need to restart agent when new organizers join

### Data Queries

Organizers can ask:
- **"How many signups?"** → Queries Eventbrite API for total count
- **"Who signed up through the agent?"** → Queries local `event_signups` storage
- **"Show me sales data"** → Queries Eventbrite API for revenue/orders
- **"Lookup order 12345"** → Queries Eventbrite API for specific order

## Key Components

### 1. Agent Core (`agent.py`)
- Handles incoming messages via uagents framework
- Passphrase authentication
- Session management (per organizer + chat session)
- Signup notification reception and storage

### 2. OpenAI Integration (`tools/openai_client.py`)
- GPT-4o-mini with function calling
- System prompt defines agent capabilities
- Tools for Eventbrite queries and agent-specific signup tracking
- Distinguishes between Eventbrite signups vs agent-tracked signups

### 3. Eventbrite Tools (`tools/eventbrite_admin.py`)
- Event info (name, dates, capacity)
- Ticket types and availability
- Order lookup
- Sales data and revenue

### 4. Persistent Storage
- **Volume mount**: `./agent_data` → `/app/agent_data`
- **Entrypoint script**: Syncs data files on container start/stop
- **Data files**: `<agent_address>_data.json` contains:
  - `admin_sessions`: Conversation history per organizer
  - `authenticated_addresses`: Passphrase-verified organizers
  - `event_signups`: Agent-tracked registrations

## Deployment

### Docker Setup
- **Port**: 8048
- **Container**: `ucla-ps-admin-agent`
- **Network**: `agent-network` (bridge)
- **Health check**: Socket connection on port 8048

### Environment Variables
```bash
AGENT_NAME="UCLA_PS_AdminAgent"
AGENT_PORT="8048"
AGENT_SEED="UCLA-PS-TICKETING-AGENT"
ADMIN_PASSPHRASE="<your-passphrase>"
UBS_AGENT_ADDRESS="agent1qf6a22yerur7gxm2jfgg3nn484tar5vx36p3gvy9f0awnhwd7ajgsfax6tm"
OPENAI_API_KEY="sk-..."
EVENTBRITE_OAUTH_TOKEN="HGVWABGALVGRRUB64ZJG"
EVENTBRITE_EVENT_ID="1985241779604"
```

### Running Locally
```bash
# Install dependencies
pip install -r requirements.txt

# Run agent
python agent.py
```

### Running in Docker
```bash
# Build and start
docker-compose up --build -d

# View logs
docker-compose logs -f admin-agent

# Stop
docker-compose down
```

## Data Flow Diagram

```
User                 Ticketing Agent           Admin Agent              Eventbrite
  │                         │                        │                      │
  │  1. Register            │                        │                      │
  ├────────────────────────▶│                        │                      │
  │                         │  2. Create order       │                      │
  │                         ├───────────────────────────────────────────────▶│
  │                         │◀───────────────────────────────────────────────┤
  │                         │  3. Signup notification│                      │
  │                         ├───────────────────────▶│                      │
  │                         │                        │ (Store locally)      │
  │                         │                        │                      │
Organizer                   │                        │                      │
  │                         │                        │                      │
  │  4. Authenticate        │                        │                      │
  ├────────────────────────────────────────────────▶│                      │
  │  (passphrase)           │                        │                      │
  │                         │                        │ (Save address)       │
  │                         │                        │                      │
  │  5. Query               │                        │                      │
  ├────────────────────────────────────────────────▶│                      │
  │  "How many signups?"    │                        │                      │
  │                         │                        │  6. Fetch data       │
  │                         │                        ├─────────────────────▶│
  │                         │                        │◀─────────────────────┤
  │  7. Response            │                        │                      │
  │◀────────────────────────────────────────────────┤                      │
  │  "12 total signups"     │                        │                      │
```

## Security

- **No hardcoded credentials**: All secrets in `.env` (gitignored)
- **Passphrase auth**: Prevents unauthorized access
- **Address whitelisting**: UBS agent always allowed, organizers auth once
- **Read-only Eventbrite**: Uses OAuth token with minimal permissions
- **Container isolation**: Docker provides process and filesystem isolation

## Troubleshooting

### Port already in use
```bash
# Check what's using the port
lsof -i :8048

# Change port in .env, docker-compose.yml, and Dockerfile
```

### Data not persisting
```bash
# Verify volume mount exists
docker volume ls

# Check entrypoint.sh is executable
chmod +x entrypoint.sh
```

### Agent not receiving messages
```bash
# Check agent address matches in ticketing agent's .env
# Verify both agents are registered on Agentverse
# Check network connectivity between containers
```