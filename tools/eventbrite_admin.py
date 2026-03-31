"""
Eventbrite API tools for organizer/admin level operations.

Requires EVENTBRITE_OAUTH_TOKEN with organizer permissions.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import httpx


def _eventbrite_token() -> Optional[str]:
    tok = os.getenv("EVENTBRITE_OAUTH_TOKEN", "").strip()
    return tok or None


def _event_id() -> Optional[str]:
    eid = os.getenv("EVENTBRITE_EVENT_ID", "").strip()
    return eid or None


def _get_sales_goals() -> Dict[str, Any]:
    """
    Get sales goals from environment variables.
    
    Expected format:
    SALES_GOAL_TICKETS=390       # Total tickets to sell
    SALES_GOAL_REVENUE=14000     # Target revenue in GBP
    """
    return {
        "target_tickets": int(os.getenv("SALES_GOAL_TICKETS", "0") or "0"),
        "target_revenue_gbp": float(os.getenv("SALES_GOAL_REVENUE", "0") or "0"),
    }


def get_sales_summary(event_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Get sales summary: total orders, revenue, tickets sold, breakdown by tier.
    
    Returns aggregate statistics suitable for admin reporting.
    """
    token = _eventbrite_token()
    if not token:
        return {"error": "MISSING_TOKEN"}
    
    eid = event_id or _event_id()
    if not eid:
        return {"error": "MISSING_EVENT_ID"}
    
    # Fetch orders
    orders_url = f"https://www.eventbriteapi.com/v3/events/{eid}/orders/"
    attendees_url = f"https://www.eventbriteapi.com/v3/events/{eid}/attendees/"
    headers = {"Authorization": f"Bearer {token}"}
    
    try:
        with httpx.Client(timeout=30.0) as client:
            # Get orders
            orders_resp = client.get(orders_url, headers=headers)
            if orders_resp.status_code >= 400:
                return {"error": "HTTP_ERROR", "status": orders_resp.status_code, "endpoint": "orders"}
            
            orders_data = orders_resp.json() or {}
            orders = orders_data.get("orders", [])
            
            # Get attendees
            attendees_resp = client.get(attendees_url, headers=headers)
            if attendees_resp.status_code >= 400:
                return {"error": "HTTP_ERROR", "status": attendees_resp.status_code, "endpoint": "attendees"}
            
            attendees_data = attendees_resp.json() or {}
            attendees = attendees_data.get("attendees", [])
    except Exception as e:
        return {"error": "EXCEPTION", "message": str(e)}
    
    # Calculate summary
    total_orders = len(orders)
    total_revenue_minor = sum(
        o.get("costs", {}).get("gross", {}).get("value", 0) for o in orders
    )
    total_revenue_major = total_revenue_minor / 100.0
    
    # Status breakdown
    status_counts = {}
    for o in orders:
        status = o.get("status", "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
    
    # Ticket tier breakdown
    tier_breakdown = {}
    checked_in_count = 0
    for att in attendees:
        tier = att.get("ticket_class_name", "Unknown")
        tier_breakdown[tier] = tier_breakdown.get(tier, 0) + 1
        if att.get("checked_in"):
            checked_in_count += 1
    
    total_attendees = len(attendees)
    
    # Get sales goals (if configured)
    goals = _get_sales_goals()
    
    result = {
        "event_id": eid,
        "total_orders": total_orders,
        "total_revenue_gbp": f"£{total_revenue_major:.2f}",
        "total_attendees": total_attendees,
        "checked_in": checked_in_count,
        "check_in_rate": f"{checked_in_count / total_attendees * 100:.1f}%" if total_attendees > 0 else "0%",
        "order_status_breakdown": status_counts,
        "ticket_tier_breakdown": tier_breakdown,
    }
    
    # Add goal tracking if goals are set
    if goals["target_tickets"] > 0 or goals["target_revenue_gbp"] > 0:
        result["sales_goals"] = {}
        
        if goals["target_tickets"] > 0:
            progress_pct = (total_attendees / goals["target_tickets"]) * 100
            result["sales_goals"]["tickets"] = {
                "target": goals["target_tickets"],
                "current": total_attendees,
                "remaining": goals["target_tickets"] - total_attendees,
                "progress": f"{progress_pct:.1f}%",
                "on_track": progress_pct >= 50,  # Simple heuristic
            }
        
        if goals["target_revenue_gbp"] > 0:
            progress_pct = (total_revenue_major / goals["target_revenue_gbp"]) * 100
            result["sales_goals"]["revenue"] = {
                "target": f"£{goals['target_revenue_gbp']:.2f}",
                "current": f"£{total_revenue_major:.2f}",
                "remaining": f"£{goals['target_revenue_gbp'] - total_revenue_major:.2f}",
                "progress": f"{progress_pct:.1f}%",
                "on_track": progress_pct >= 50,  # Simple heuristic
            }
    
    return result


def get_recent_orders(event_id: Optional[str] = None, limit: int = 10) -> Dict[str, Any]:
    """
    Get recent orders with details (for admin lookup).
    
    Returns: list of orders with ID, name, email, status, created time, amount.
    """
    token = _eventbrite_token()
    if not token:
        return {"error": "MISSING_TOKEN"}
    
    eid = event_id or _event_id()
    if not eid:
        return {"error": "MISSING_EVENT_ID"}
    
    url = f"https://www.eventbriteapi.com/v3/events/{eid}/orders/"
    headers = {"Authorization": f"Bearer {token}"}
    
    try:
        with httpx.Client(timeout=20.0) as client:
            resp = client.get(url, headers=headers, params={"page_size": limit})
            if resp.status_code >= 400:
                return {"error": "HTTP_ERROR", "status": resp.status_code}
            
            data = resp.json() or {}
            orders = data.get("orders", [])
    except Exception as e:
        return {"error": "EXCEPTION", "message": str(e)}
    
    recent = []
    for o in orders[:limit]:
        recent.append({
            "order_id": o.get("id"),
            "name": o.get("name"),
            "email": o.get("email"),
            "status": o.get("status"),
            "created": o.get("created"),
            "amount": o.get("costs", {}).get("gross", {}).get("display"),
        })
    
    return {
        "event_id": eid,
        "orders": recent,
        "count": len(recent),
    }


def get_daily_sales_breakdown(event_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Get sales breakdown by day: tickets sold per day with dates sorted.
    
    Returns: daily breakdown with date, tickets sold, revenue for each day.
    """
    from datetime import datetime
    from collections import defaultdict
    
    token = _eventbrite_token()
    if not token:
        return {"error": "MISSING_TOKEN"}
    
    eid = event_id or _event_id()
    if not eid:
        return {"error": "MISSING_EVENT_ID"}
    
    url = f"https://www.eventbriteapi.com/v3/events/{eid}/orders/"
    headers = {"Authorization": f"Bearer {token}"}
    
    # Fetch ALL orders with pagination
    all_orders = []
    page = 1
    has_more = True
    
    try:
        with httpx.Client(timeout=30.0) as client:
            while has_more:
                resp = client.get(url, headers=headers, params={"page": page, "page_size": 50})
                if resp.status_code >= 400:
                    return {"error": "HTTP_ERROR", "status": resp.status_code}
                
                data = resp.json() or {}
                orders = data.get("orders", [])
                pagination = data.get("pagination", {})
                
                all_orders.extend(orders)
                
                # Check if there are more pages
                has_more = pagination.get("has_more_items", False)
                page += 1
                
                # Safety limit to prevent infinite loops
                if page > 100:
                    break
    except Exception as e:
        return {"error": "EXCEPTION", "message": str(e)}
    
    # Group by date
    daily_stats = defaultdict(lambda: {"tickets": 0, "revenue": 0.0, "orders": 0})
    
    for order in all_orders:
        # Only count placed/paid orders
        status = order.get("status", "")
        if status not in ["placed", "paid"]:
            continue
        
        created_str = order.get("created", "")
        if not created_str:
            continue
        
        # Parse ISO datetime and extract date
        try:
            dt = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
            date_key = dt.strftime("%Y-%m-%d")  # YYYY-MM-DD format
        except:
            continue
        
        # Count tickets (attendees) in this order
        ticket_count = 0
        for item in order.get("ticket_classes", []):
            ticket_count += item.get("quantity", 0)
        
        # Get revenue
        revenue_str = order.get("costs", {}).get("gross", {}).get("major_value", "0")
        try:
            revenue = float(revenue_str)
        except:
            revenue = 0.0
        
        daily_stats[date_key]["tickets"] += ticket_count
        daily_stats[date_key]["revenue"] += revenue
        daily_stats[date_key]["orders"] += 1
    
    # Sort by date and format output
    sorted_days = sorted(daily_stats.items())
    daily_breakdown = []
    
    for date_str, stats in sorted_days:
        daily_breakdown.append({
            "date": date_str,
            "tickets_sold": stats["tickets"],
            "orders": stats["orders"],
            "revenue": f"£{stats['revenue']:.2f}",
        })
    
    return {
        "event_id": eid,
        "total_days": len(daily_breakdown),
        "daily_breakdown": daily_breakdown,
    }


def lookup_order(order_id: str) -> Dict[str, Any]:
    """
    Look up a specific order by ID (admin verification).
    
    Returns: order details including attendees, payment status, etc.
    """
    token = _eventbrite_token()
    if not token:
        return {"error": "MISSING_TOKEN"}
    
    url = f"https://www.eventbriteapi.com/v3/orders/{order_id}/"
    headers = {"Authorization": f"Bearer {token}"}
    
    try:
        with httpx.Client(timeout=20.0) as client:
            resp = client.get(url, headers=headers)
            if resp.status_code >= 400:
                return {"error": "HTTP_ERROR", "status": resp.status_code}
            
            data = resp.json() or {}
    except Exception as e:
        return {"error": "EXCEPTION", "message": str(e)}
    
    # Extract key fields
    return {
        "order_id": data.get("id"),
        "name": data.get("name"),
        "email": data.get("email"),
        "status": data.get("status"),
        "created": data.get("created"),
        "changed": data.get("changed"),
        "costs": {
            "base_price": data.get("costs", {}).get("base_price", {}).get("display"),
            "eventbrite_fee": data.get("costs", {}).get("eventbrite_fee", {}).get("display"),
            "gross": data.get("costs", {}).get("gross", {}).get("display"),
        },
        "event_id": data.get("event_id"),
    }


def get_attendee_details(event_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Get attendee statistics (aggregate only - no PII exposed).
    
    Returns: total attendees, breakdown by tier, check-in stats, refund counts.
    """
    token = _eventbrite_token()
    if not token:
        return {"error": "MISSING_TOKEN"}
    
    eid = event_id or _event_id()
    if not eid:
        return {"error": "MISSING_EVENT_ID"}
    
    url = f"https://www.eventbriteapi.com/v3/events/{eid}/attendees/"
    headers = {"Authorization": f"Bearer {token}"}
    
    try:
        with httpx.Client(timeout=20.0) as client:
            resp = client.get(url, headers=headers)
            if resp.status_code >= 400:
                return {"error": "HTTP_ERROR", "status": resp.status_code}
            
            data = resp.json() or {}
            attendees = data.get("attendees", [])
    except Exception as e:
        return {"error": "EXCEPTION", "message": str(e)}
    
    total = len(attendees)
    checked_in = sum(1 for a in attendees if a.get("checked_in"))
    cancelled = sum(1 for a in attendees if a.get("cancelled"))
    refunded = sum(1 for a in attendees if a.get("refunded"))
    
    tier_breakdown = {}
    for a in attendees:
        tier = a.get("ticket_class_name", "Unknown")
        tier_breakdown[tier] = tier_breakdown.get(tier, 0) + 1
    
    return {
        "event_id": eid,
        "total_attendees": total,
        "checked_in": checked_in,
        "check_in_rate": f"{checked_in / total * 100:.1f}%" if total > 0 else "0%",
        "cancelled": cancelled,
        "refunded": refunded,
        "tier_breakdown": tier_breakdown,
    }


def get_ticket_inventory(event_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Get current ticket inventory: total, sold, remaining per tier.
    """
    token = _eventbrite_token()
    if not token:
        return {"error": "MISSING_TOKEN"}
    
    eid = event_id or _event_id()
    if not eid:
        return {"error": "MISSING_EVENT_ID"}
    
    url = f"https://www.eventbriteapi.com/v3/events/{eid}/ticket_classes/"
    headers = {"Authorization": f"Bearer {token}"}
    
    try:
        with httpx.Client(timeout=20.0) as client:
            resp = client.get(url, headers=headers)
            if resp.status_code >= 400:
                return {"error": "HTTP_ERROR", "status": resp.status_code}
            
            data = resp.json() or {}
            ticket_classes = data.get("ticket_classes", [])
    except Exception as e:
        return {"error": "EXCEPTION", "message": str(e)}
    
    inventory = []
    for tc in ticket_classes:
        total = tc.get("quantity_total")
        sold = tc.get("quantity_sold", 0)
        remaining = (total - sold) if isinstance(total, int) else "Unlimited"
        
        inventory.append({
            "name": tc.get("name"),
            "price": tc.get("cost", {}).get("display", "Free"),
            "total": total if total else "Unlimited",
            "sold": sold,
            "remaining": remaining,
            "status": tc.get("on_sale_status"),
        })
    
    return {
        "event_id": eid,
        "ticket_types": inventory,
    }

