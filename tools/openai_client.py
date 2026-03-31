"""
OpenAI tool-calling loop for CUCFS Admin Agent.

Handles organizer queries: sales stats, attendee demographics, order lookup.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List

from openai import OpenAI

from . import eventbrite_admin

# Initialize OpenAI client
_openai_client: OpenAI | None = None


def get_openai_client() -> OpenAI:
    global _openai_client
    if _openai_client is None:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY not set")
        _openai_client = OpenAI(api_key=api_key)
    return _openai_client


SYSTEM_PROMPT = """You are the UCLA PS Ticketing Admin Agent — an AI assistant for event organizers.

**Your capabilities:**

1. **Eventbrite Data (ALL registrations):**
   - Sales reports: total orders, revenue, ticket sales, goal tracking
   - Attendee statistics: check-in rates, tier breakdown
   - Order lookup: verify specific orders by ID
   - Inventory management: tickets sold/remaining per tier
   - Daily sales breakdown: tickets sold per day sorted chronologically

2. **Agent Signup Tracking (registrations via the AI chatbot agent only):**
   - Count of agent-specific signups
   - List recent agent signups
   - Search agent signups by email/name
   - Today's agent signups

**CRITICAL - Choosing the right data source:**
- For ALL signup/registration questions, ALWAYS use Eventbrite tools (`get_sales_summary`, `get_recent_orders`, `get_attendee_details`) by default.
- ONLY use agent signup tracking tools (`get_agent_signup_count`, `list_agent_signups`, `search_agent_signup`, `get_agent_signups_today`) when the user EXPLICITLY mentions "agent", "chatbot", or "through our agent" in their question.
- Examples: "how many signups?" → Eventbrite. "how many signed up through agent?" → agent tracking tools.

**CRITICAL - Check-in Queries:**
When users ask about check-ins, you MUST call `get_attendee_details`. DO NOT use `get_sales_summary` for check-in questions.

**CRITICAL - Daily Sales Queries:**
When users ask about sales by day/date, you MUST call `get_daily_sales_breakdown`.

**Important:**
- Only aggregate stats are shared — never expose individual attendee PII unless asked for a specific order.
- Be concise and data-driven in your responses.
- When showing currency, use the format returned by the API (e.g., £45.00).
- If sales goals are configured, show progress and whether you're on track.
- If an error occurs, explain it clearly to the admin.

**Your tone:**
- Professional, efficient, helpful
- Assume the user is an event organizer with access to all event data
"""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_sales_summary",
            "description": "Get overall sales summary: total orders, total signups, revenue, ticket sales by tier, sales goal progress. Use this for ANY question about signups, registrations, sales, revenue, or tickets sold. This is the DEFAULT tool for signup/registration questions. DO NOT use this for check-in questions.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_recent_orders",
            "description": "Get list of people who signed up / registered, with their names, emails, order IDs, status, and timestamps. Use this when user asks 'who signed up', 'list signups', 'show me the registrants', or any question about WHO registered.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Number of recent orders to fetch (default: 10)",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lookup_order",
            "description": "Look up a specific order by its Eventbrite order ID",
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {
                        "type": "string",
                        "description": "The Eventbrite order ID (e.g., '14012362503')",
                    }
                },
                "required": ["order_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_attendee_details",
            "description": "Get attendee check-in statistics and counts. ALWAYS use this tool when user asks about check-ins (e.g., 'how many checked in', 'check-in count', 'how many people have checked in', 'number of check-ins', 'who checked in'). Returns: total attendees, checked-in count, not-checked-in count, cancelled, refunded, breakdown by ticket tier.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_ticket_inventory",
            "description": "Get current ticket inventory: total, sold, remaining for each ticket tier",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_daily_sales_breakdown",
            "description": "Get sales breakdown by day showing tickets sold per day sorted chronologically. ALWAYS use this tool when user asks to 'sort by day', 'tickets per day', 'daily sales', 'breakdown by date', 'how many tickets sold each day', or any query about daily/date-based sales patterns.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_agent_signup_count",
            "description": "Get count of signups made specifically through the AI chatbot agent. ONLY use this when user explicitly says 'agent signups', 'through agent', 'chatbot signups', or 'through our agent'. Do NOT use for general signup questions.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_agent_signups",
            "description": "List signups made specifically through the AI chatbot agent. ONLY use when user explicitly asks about agent/chatbot signups.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Number of signups to return (default: 10, use -1 for all)",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_agent_signup",
            "description": "Search agent-specific signups by email or name. ONLY use when user explicitly asks about agent/chatbot signups.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Email address or name to search for",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_agent_signups_today",
            "description": "Get today's signups made through the AI chatbot agent only. ONLY use when user explicitly asks about agent/chatbot signups.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
]


def _execute_tool(tool_name: str, tool_args: Dict[str, Any], ctx = None) -> Dict[str, Any]:
    """Execute a tool call and return the result."""
    if tool_name == "get_sales_summary":
        return eventbrite_admin.get_sales_summary()
    elif tool_name == "get_recent_orders":
        limit = tool_args.get("limit", 10)
        return eventbrite_admin.get_recent_orders(limit=limit)
    elif tool_name == "lookup_order":
        order_id = tool_args.get("order_id")
        if not order_id:
            return {"error": "Missing order_id parameter"}
        return eventbrite_admin.lookup_order(order_id)
    elif tool_name == "get_attendee_details":
        return eventbrite_admin.get_attendee_details()
    elif tool_name == "get_ticket_inventory":
        return eventbrite_admin.get_ticket_inventory()
    elif tool_name == "get_daily_sales_breakdown":
        return eventbrite_admin.get_daily_sales_breakdown()
    # Signup tracking tools
    elif tool_name == "get_agent_signup_count":
        from datetime import datetime, timezone
        if not ctx:
            return {"error": "Context required for signup tools"}
        signups = ctx.storage.get("event_signups") or []
        return {"total_signups": len(signups), "timestamp": datetime.now(timezone.utc).isoformat()}
    elif tool_name == "list_agent_signups":
        if not ctx:
            return {"error": "Context required for signup tools"}
        signups = ctx.storage.get("event_signups") or []
        limit = tool_args.get("limit", 10)
        if limit == -1:
            result_signups = signups
        else:
            result_signups = signups[-limit:] if len(signups) > limit else signups
        return {"signups": result_signups, "total": len(signups), "showing": len(result_signups)}
    elif tool_name == "search_agent_signup":
        if not ctx:
            return {"error": "Context required for signup tools"}
        query = tool_args.get("query", "").lower()
        signups = ctx.storage.get("event_signups") or []
        matches = [s for s in signups if query in s.get("email", "").lower() or query in s.get("name", "").lower()]
        return {"matches": matches, "count": len(matches), "query": query}
    elif tool_name == "get_agent_signups_today":
        from datetime import datetime, timezone, timedelta
        if not ctx:
            return {"error": "Context required for signup tools"}
        signups = ctx.storage.get("event_signups") or []
        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_signups = [s for s in signups if datetime.fromisoformat(s.get("timestamp", "1970-01-01T00:00:00+00:00")) >= today_start]
        return {"signups_today": today_signups, "count": len(today_signups), "date": now.date().isoformat()}
    else:
        return {"error": "UNKNOWN_TOOL", "tool": tool_name}


def run_admin_turn(
    user_message: str,
    history: List[Dict[str, str]],
    model: str = "gpt-4o-mini",
    ctx = None,
) -> tuple[str, List[Dict[str, str]]]:
    """
    Run one admin agent turn: process user query, call tools, return response.
    
    Args:
        user_message: The admin's query
        history: Conversation history (list of {role, content})
        model: OpenAI model to use
        ctx: Agent context (required for signup tools)
    
    Returns:
        (assistant_reply, updated_history)
    """
    client = get_openai_client()
    
    # Build messages
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_message})
    
    # Tool-calling loop (max 5 iterations)
    for iteration in range(5):
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=TOOLS,
            temperature=0.1,
        )
        
        choice = response.choices[0]
        assistant_message = choice.message
        
        # If no tool calls, we have the final response
        if not assistant_message.tool_calls:
            reply = assistant_message.content or "(no response)"
            
            # Update history
            updated_history = list(history)
            updated_history.append({"role": "user", "content": user_message})
            updated_history.append({"role": "assistant", "content": reply})
            
            return reply, updated_history
        
        # Execute tool calls
        messages.append({
            "role": "assistant",
            "content": assistant_message.content,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in assistant_message.tool_calls
            ],
        })
        
        for tool_call in assistant_message.tool_calls:
            tool_name = tool_call.function.name
            tool_args = json.loads(tool_call.function.arguments)
            
            result = _execute_tool(tool_name, tool_args, ctx=ctx)
            
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": json.dumps(result),
            })
    
    # If we hit max iterations, return an error
    updated_history = list(history)
    updated_history.append({"role": "user", "content": user_message})
    updated_history.append({
        "role": "assistant",
        "content": "Sorry, I reached the maximum number of tool calls. Please try rephrasing your question."
    })
    
    return "Sorry, I reached the maximum number of tool calls. Please try rephrasing your question.", updated_history

