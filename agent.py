"""
CUCFS Admin Agent — Organizer-only access for event management.

Provides sales reports, attendee statistics, order lookup, and inventory management.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from pydantic import BaseModel, Field
from uagents import Agent, Context, Model, Protocol
from uagents_core.contrib.protocols.chat import (
    ChatAcknowledgement,
    ChatMessage,
    TextContent,
    chat_protocol_spec,
)

# Ensure project root on sys.path
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Load environment variables
load_dotenv(ROOT / ".env")

from tools import openai_client

# Configuration
AGENT_NAME = os.getenv("AGENT_NAME", "cucfs-admin-agent")
AGENT_SEED = os.getenv("AGENT_SEED", "cucfs_admin_seed")
AGENT_PORT = int(os.getenv("AGENT_PORT", "8041"))
AGENTVERSE_URL = os.getenv("AGENTVERSE_URL", "https://agentverse.ai")

# Access control
ADMIN_PASSPHRASE = os.getenv("ADMIN_PASSPHRASE", "").strip()
UBS_AGENT_ADDRESS = os.getenv("UBS_AGENT_ADDRESS", "").strip()
AUTHENTICATED_KEY = "authenticated_addresses"

if not ADMIN_PASSPHRASE:
    print("⚠️  WARNING: ADMIN_PASSPHRASE not set in .env. No one will be able to authenticate.")

agent = Agent(
    name=AGENT_NAME,
    seed=AGENT_SEED,
    port=AGENT_PORT,
    mailbox=True,
    agentverse=AGENTVERSE_URL,
    handle_messages_concurrently=True,
)

chat_proto = Protocol(spec=chat_protocol_spec)

# Signup notification message model
class SignupNotification(Model):
    """Message sent from UBS agent when someone registers."""
    order_id: str
    email: str
    name: str
    timestamp: str
    event_name: str = "UBS × Product Space × Fetch.ai AI Agent Challenge"

# Session storage keys
SESSIONS_KEY = "admin_sessions"
SIGNUPS_KEY = "event_signups"


def _get_session_key(ctx: Context, sender: str) -> str:
    """Generate a unique session key combining sender address and chat session ID."""
    chat_session = ctx.session if hasattr(ctx, 'session') else None
    if chat_session:
        return f"{sender}_{chat_session}"
    return sender  # Fallback to just sender if no session available


def _get_session(ctx: Context, sender: str) -> dict:
    """Get or create session state for a sender+session combination."""
    session_key = _get_session_key(ctx, sender)
    
    try:
        sessions = ctx.storage.get(SESSIONS_KEY) or {}
    except Exception:
        sessions = {}
    
    if session_key not in sessions:
        sessions[session_key] = {
            "history": [],
            "last_seen": datetime.now(timezone.utc).isoformat(),
        }
        ctx.storage.set(SESSIONS_KEY, sessions)
    
    return sessions[session_key]


def _save_session(ctx: Context, sender: str, session: dict):
    """Save session state."""
    session_key = _get_session_key(ctx, sender)
    
    try:
        sessions = ctx.storage.get(SESSIONS_KEY) or {}
        session["last_seen"] = datetime.now(timezone.utc).isoformat()
        sessions[session_key] = session
        ctx.storage.set(SESSIONS_KEY, sessions)
    except Exception as e:
        ctx.logger.error(f"Failed to save session: {e}")


def _is_authenticated(ctx: Context, sender: str) -> bool:
    """Check if sender is authenticated (UBS agent or passphrase-verified)."""
    if sender == UBS_AGENT_ADDRESS:
        return True
    try:
        authenticated = ctx.storage.get(AUTHENTICATED_KEY) or {}
        return sender in authenticated
    except Exception:
        return False


def _authenticate_address(ctx: Context, sender: str):
    """Add sender to authenticated addresses."""
    try:
        authenticated = ctx.storage.get(AUTHENTICATED_KEY) or {}
        authenticated[sender] = {
            "authenticated_at": datetime.now(timezone.utc).isoformat(),
        }
        ctx.storage.set(AUTHENTICATED_KEY, authenticated)
        ctx.logger.info(f"✅ Authenticated new address: {sender}")
    except Exception as e:
        ctx.logger.error(f"Failed to save authenticated address: {e}")


def _store_signup(ctx: Context, signup_data: dict):
    """Store a new signup in persistent storage."""
    try:
        signups = ctx.storage.get(SIGNUPS_KEY) or []
        signups.append(signup_data)
        ctx.storage.set(SIGNUPS_KEY, signups)
        ctx.logger.info(f"✅ Stored signup: {signup_data['email']} (Order: {signup_data['order_id']})")
        ctx.logger.info(f"📊 Total signups: {len(signups)}")
    except Exception as e:
        ctx.logger.error(f"Failed to store signup: {e}")


def _get_signups(ctx: Context) -> list:
    """Retrieve all signups from storage."""
    try:
        return ctx.storage.get(SIGNUPS_KEY) or []
    except Exception:
        return []


def _extract_text(msg: ChatMessage) -> str:
    """Extract text from chat message."""
    parts = []
    for item in msg.content or []:
        if isinstance(item, TextContent) and item.text:
            parts.append(item.text)
    return "\n".join(parts).strip()


@agent.on_event("startup")
async def on_startup(ctx: Context):
    ctx.logger.info(f"🔐 {AGENT_NAME} started. Address: {agent.wallet.address()}")
    ctx.logger.info(f"� Passphrase auth: {'configured' if ADMIN_PASSPHRASE else 'NOT SET'}")
    ctx.logger.info(f"🤖 UBS agent (always allowed): {UBS_AGENT_ADDRESS[:20]}..." if UBS_AGENT_ADDRESS else "⚠️ UBS_AGENT_ADDRESS not set")
    
    # Show authenticated addresses
    try:
        authenticated = ctx.storage.get(AUTHENTICATED_KEY) or {}
        if authenticated:
            ctx.logger.info(f"� Previously authenticated organizers: {len(authenticated)}")
            for addr in list(authenticated.keys())[:3]:
                ctx.logger.info(f"   ✓ {addr[:20]}...")
            if len(authenticated) > 3:
                ctx.logger.info(f"   ... and {len(authenticated) - 3} more")
        else:
            ctx.logger.info(f"📋 No organizers authenticated yet")
    except Exception as e:
        ctx.logger.warning(f"Could not load authenticated addresses: {e}")
    
    # Show signup statistics
    try:
        signups = _get_signups(ctx)
        if signups:
            ctx.logger.info(f"📊 Total event signups: {len(signups)}")
            recent = signups[-1]
            ctx.logger.info(f"   Most recent: {recent['email']} at {recent['timestamp']}")
        else:
            ctx.logger.info(f"📊 No signups recorded yet")
    except Exception as e:
        ctx.logger.warning(f"Could not load signup data: {e}")


@chat_proto.on_message(ChatMessage)
async def handle_chat(ctx: Context, sender: str, msg: ChatMessage):
    # Send acknowledgment
    await ctx.send(sender, ChatAcknowledgement(acknowledged_msg_id=msg.msg_id))
    
    # Extract user message
    text = _extract_text(msg)
    
    ctx.logger.info(f"� Incoming from: {sender[:20]}...")
    ctx.logger.info(f"   Text: {text[:100] if text else '(empty)'}")
    
    if not text:
        ctx.logger.warning(f"   ⚠️ Empty text content, skipping")
        return
    
    # Check if this is a signup notification from UBS agent (always allowed)
    if sender == UBS_AGENT_ADDRESS:
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict) and parsed.get("type") == "signup_notification":
                ctx.logger.info(f"🎉 Signup notification received from UBS agent!")
                signup_data = {
                    "order_id": parsed.get("order_id", ""),
                    "email": parsed.get("email", ""),
                    "name": parsed.get("name", ""),
                    "timestamp": parsed.get("timestamp", ""),
                    "event_name": parsed.get("event_name", ""),
                    "received_at": datetime.now(timezone.utc).isoformat(),
                }
                _store_signup(ctx, signup_data)
                ctx.logger.info(f"✅ Signup stored: {signup_data['name']} ({signup_data['email']})")
                return
        except (json.JSONDecodeError, TypeError):
            pass
    
    # Strip @mention prefix (Agentverse prepends @agent-name to messages)
    clean_text = text.strip()
    if clean_text.startswith("@"):
        clean_text = clean_text.split(" ", 1)[-1].strip() if " " in clean_text else clean_text
    
    # Check if sender is authenticated
    if not _is_authenticated(ctx, sender):
        # Check if message is the passphrase
        if clean_text.lower() == ADMIN_PASSPHRASE.lower():
            _authenticate_address(ctx, sender)
            await ctx.send(
                sender,
                ChatMessage(content=[
                    TextContent(text="✅ **Authenticated!**\n\nYou now have organizer access. How can I help you today?\n\nYou can ask me about:\n- Sign-up counts and attendee details\n- Sales reports and revenue\n- Order lookups\n- Ticket inventory\n- Agent-specific sign-ups")
                ])
            )
            return
        else:
            ctx.logger.info(f"🔒 Unauthenticated sender: {sender[:20]}...")
            await ctx.send(
                sender,
                ChatMessage(content=[
                    TextContent(text="🔒 **Authentication Required**\n\nThis agent is for event organizers only.\n\nPlease enter the admin passphrase to gain access.")
                ])
            )
            return
    
    ctx.logger.info(f"📥 [admin] ✅ Authenticated message from: {sender[:20]}...")
    ctx.logger.info(f"   Query: {clean_text}")
    
    # Get session
    session = _get_session(ctx, sender)
    history = session.get("history", [])
    
    # Run admin agent
    try:
        model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        reply, updated_history = openai_client.run_admin_turn(clean_text, history, model=model, ctx=ctx)
        
        # Save updated history
        session["history"] = updated_history[-20:]  # Keep last 20 messages
        _save_session(ctx, sender, session)
        
        ctx.logger.info(f"📤 [admin] reply={reply[:80]}")
        
        # Send response
        await ctx.send(sender, ChatMessage(content=[TextContent(text=reply)]))
        
    except Exception as e:
        ctx.logger.error(f"Error in admin agent: {e}")
        await ctx.send(
            sender,
            ChatMessage(content=[
                TextContent(text=f"❌ Error: {str(e)}")
            ])
        )


@chat_proto.on_message(ChatAcknowledgement)
async def handle_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
    ctx.logger.debug(f"Ack from {sender[:12]}... for {msg.acknowledged_msg_id}")


agent.include(chat_proto, publish_manifest=True)

if __name__ == "__main__":
    agent.run()

