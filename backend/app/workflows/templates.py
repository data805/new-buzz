"""Reusable workflow templates. Internal-only templates are enabled by default;
anything that reaches outside systems starts disabled and unapproved."""
from __future__ import annotations

TEMPLATES: list[dict] = [
    {
        "template": "capture_enquiry",
        "name": "Capture enquiry from call",
        "description": "When the caller wants follow-up on a topic, create an enquiry record routed to the right team.",
        "trigger": "call.completed",
        "conditions": [{"field": "extracted.enquiry_topic", "op": "exists"}],
        "actions": [{"type": "create_enquiry", "params": {
            "name": "{{extracted.caller_name}}", "phone": "{{extracted.caller_phone||phone}}", "email": "{{extracted.caller_email}}",
            "organisation": "{{extracted.organisation}}", "topic": "{{extracted.enquiry_topic}}",
            "details": "{{extracted.enquiry_details}}\n\nSummary: {{extracted.summary}}", "team": "{{extracted.team}}"}}],
        "enabled": True,
    },
    {
        "template": "schedule_callback",
        "name": "Schedule requested callback",
        "description": "Create a callback task when the caller asks to be called back.",
        "trigger": "call.completed",
        "conditions": [{"field": "extracted.callback_requested", "op": "truthy"}],
        "actions": [{"type": "create_callback", "params": {
            "phone": "{{extracted.caller_phone||phone}}", "name": "{{extracted.caller_name}}", "requested_time": "{{extracted.callback_time}}",
            "reason": "{{extracted.summary}}"}}],
        "enabled": True,
    },
    {
        "template": "escalation_ticket",
        "name": "Escalation ticket",
        "description": "Open a high-priority ticket whenever a caller asks for a human or the agent could not help.",
        "trigger": "call.escalated",
        "conditions": [],
        "actions": [{"type": "create_ticket", "params": {
            "subject": "Caller requested a human", "details": "Reason: {{reason}}", "priority": "high", "phone": "{{phone}}"}},
                    {"type": "notify_staff", "params": {"message": "Escalation on call {{call_id}}: {{reason}}"}}],
        "enabled": True,
    },
    {
        "template": "needs_human_ticket",
        "name": "Unresolved call follow-up",
        "description": "Open a ticket when the post-call analysis says staff must act.",
        "trigger": "call.completed",
        "conditions": [{"field": "extracted.needs_human", "op": "truthy"}, {"field": "escalated", "op": "falsy"}],
        "actions": [{"type": "create_ticket", "params": {
            "subject": "Follow-up: {{extracted.intent}}", "details": "{{extracted.summary}}\nActions: {{extracted.action_items}}",
            "priority": "normal", "team": "{{extracted.team}}", "phone": "{{phone}}"}}],
        "enabled": True,
    },
    {
        "template": "record_feedback",
        "name": "Record caller feedback",
        "description": "Store a rating/comment when the caller gives feedback.",
        "trigger": "call.completed",
        "conditions": [{"field": "extracted.feedback_rating", "op": "exists"}],
        "actions": [{"type": "record_feedback", "params": {"rating": "{{extracted.feedback_rating}}", "comment": "{{extracted.feedback_comment}}"}}],
        "enabled": True,
    },
    {
        "template": "negative_feedback_alert",
        "name": "Negative feedback alert (email)",
        "description": "Email the team lead when a caller rates the service 2 or lower.",
        "trigger": "feedback.received",
        "conditions": [{"field": "rating", "op": "lt", "value": 3}],
        "actions": [{"type": "email", "params": {"to": "team-lead@example.com", "subject": "Low caller rating ({{rating}}/5)",
                                                 "body": "Call {{call_id}}: {{comment}}"}}],
        "enabled": False,
    },
    {
        "template": "callback_sms_confirmation",
        "name": "SMS callback confirmation",
        "description": "Text the caller to confirm their callback request.",
        "trigger": "callback.created",
        "conditions": [{"field": "phone", "op": "exists"}],
        "actions": [{"type": "sms", "params": {"to": "{{phone}}",
                                               "message": "Thank you for calling BongoHive. A team member will call you back ({{requested_time}})."}}],
        "enabled": False,
    },
    {
        "template": "post_call_n8n",
        "name": "Send call summary to n8n",
        "description": "Forward every completed call (summary, extraction, outcome) to an n8n webhook for CRM/Sheets/Slack automations.",
        "trigger": "call.completed",
        "conditions": [],
        "actions": [{"type": "n8n", "params": {"webhook_path": "webhook/voice-call-completed"}}],
        "enabled": False,
    },
    {
        "template": "missed_call_follow_up",
        "name": "Follow-up call after enquiry (consented contacts)",
        "description": "Place a follow-up call for a new enquiry. Requires outbound enabled, recorded consent and per-run approval.",
        "trigger": "enquiry.created",
        "conditions": [{"field": "phone", "op": "exists"}],
        "actions": [{"type": "outbound_call", "params": {"phone": "{{phone}}", "purpose": "Follow up on enquiry about {{topic}}"}}],
        "enabled": False,
        "require_run_approval": True,
    },
]
