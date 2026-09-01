"""Bounded semantic prompt for Goal/domain resolution."""

GOAL_RESOLUTION_PROMPT = """Resolve only the user's business Goal and domain.
Return one JSON object with resolution, domain, goal, candidate_facts,
authorized_domains, constraints, question, reason_code. resolution is one of
NEW, CONTINUE, SWITCH, CANCEL, GENERAL_HELP, UNCERTAIN. domain is one of
repair, billing, announcement, inspection or null.

Use these exact JSON shapes: candidate_facts is a plain JSON object whose keys map
directly to business values; never return a generic {"type":"value"} object, a typed
wrapper, or a list of name/value records. authorized_domains and constraints are arrays
of strings. For a single executable Goal, authorized_domains normally contains its domain.
Use canonical fact names when present:
- repair: work_order_id, location, description, category, urgency, appointment_at,
  assigned_to_me
- billing: bill_id, period, fee_type, subject, description
- announcement: announcement_id, title, body, audience, topic, requirements,
  revision_instruction, target_date, scheduled_at, query
- inspection: target, task_id, event_id, location, point, description, status, assignee
For example, an affected facility and its reported symptom are represented as
{"location":"3栋电梯","description":"电梯一直报警"}.
Announcement audience is always a JSON object, not a string; preserve a natural audience
as {"description":"全体住户"}. A request for a water-outage notice should preserve
the semantic topic, for example {"topic":"停水通知"}, even before its schedule and
audience are supplied. Inspection task queries set target to "task"; security incident
reports set target to "event" when a target fact is useful.

This stage must never choose or mention a capability, tool, workflow step, risk enum,
event type, permission, confirmation, identity, scope, database, lease, or idempotency.
Missing business facts do not make a Goal uncertain. For example, a request to initiate
a repair is repair even when location and fault details are absent; the domain ReAct loop
will ask for them. Use UNCERTAIN only when the intended business domain itself genuinely
cannot be inferred.

When active_goal is present, prefer CONTINUE for follow-up questions, short answers,
corrections, or multiple newly supplied facts that relate to it. A generic category answer
such as "其他事件" continues an active security-event Goal; it is not a new list query.
Use SWITCH only when the user explicitly starts a different Goal/domain. If one utterance
cancels the old Goal and clearly asks for a new task, return SWITCH for the new Goal. Use
CANCEL only when no replacement task is requested.

candidate_facts contains only user-authored business facts useful to the selected domain.
Extract all facts supplied in the current utterance, but do not invent missing values.
Resolve relative periods and dates against the supplied business_date when a capability
will need a normalized period or date; preserve the user's wording in the Goal itself.
For public-area safety reports, preserve the user's location and description; downstream
business rules derive event type and minimum risk. authorized_domains contains only domains
explicitly requested in the current utterance. question is required only for UNCERTAIN.
constraints is empty unless the user explicitly asks to look up an existing record before
handling it; then include lookup_existing_first.
Return JSON only, without markdown or hidden reasoning.
"""
