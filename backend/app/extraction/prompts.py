"""Prompts for the canonical extraction pipeline.

Kept apart from the provider so the rules the model is held to are readable on
their own, and so a change to wording is reviewable as a change to wording.

The governing instruction, repeated in every prompt, is that an unstated thing
must come back as unstated. A schedule that says "we could not determine the
timing of Week 4" is blocked by the validator and fixed by a human in minutes. A
schedule that quietly guesses Day 28 is approved, and a patient is seen on the
wrong day.
"""

from __future__ import annotations

SYSTEM = """\
You extract clinical trial schedule structure from protocol documents.

You are one stage of a pipeline. A deterministic layer assembles your output and
a human reviews it before any patient is scheduled. Your job is to report what
the protocol SAYS, with the evidence for it - not to produce a complete or
tidy-looking schedule.

Rules that override everything else:

1. NEVER invent a value the protocol does not state. If a visit's timing, window,
   applicability, or mode is not stated, omit it or mark it unresolved. Omission
   is a correct answer that a reviewer can fix. A plausible guess is a wrong
   answer that nobody catches.
2. NEVER resolve an ambiguity by choosing. If the protocol says two conflicting
   things, or a phrase could mean two things, report it as unresolved with both
   readings in `interpretation`.
3. Every claim MUST cite evidence_ids drawn from the evidence list you are given.
   A claim with no evidence is discarded downstream, so an uncited claim is
   wasted work.
4. Never produce patient dates. You produce RULES ("30 days after last dose"),
   never calculated calendar dates.
5. Use the protocol's own vocabulary for labels, and its own codes where it has
   them. Do not rename a visit to something tidier.

Reply with JSON only. No prose before or after."""


DOCUMENT_STRUCTURE = """\
Identify the parts of this protocol that describe the schedule.

Return JSON:
{
  "sections": [{"title": str, "page": int, "kind": "SOA_TABLE"|"NARRATIVE"|"FOOTNOTES"|"OTHER"}],
  "tables": [{"title": str, "page": int, "description": str}]
}

Include every schedule-of-assessments table, every footnote block belonging to
one, and any narrative section that states timing, windows, or conditions. Do
not include sections that only describe eligibility or statistics."""


EVIDENCE = """\
List the specific passages that state schedule structure. These become the
citations every later claim must reference, so prefer many small precise
passages over a few large ones.

Return JSON:
{
  "evidence": [{
    "ref": str,                      # a short unique id you choose, e.g. "e1"
    "evidence_type": "TABLE_CELL"|"TABLE_ROW"|"TABLE_COLUMN"|"FOOTNOTE"|"SECTION",
    "page_number": int,
    "section_title": str|null,
    "table_title": str|null,
    "row_identifier": str|null,
    "column_identifier": str|null,
    "source_text": str               # the passage, quoted verbatim
  }]
}

source_text must be quoted from the document, not paraphrased. A reviewer uses
it to check your reading against the protocol."""


#: One instruction per pipeline node. The node names come from the graph, so a
#: category with no entry here simply produces no claims - which is honest, not
#: a failure.
CATEGORY_PROMPTS: dict[str, str] = {
    "protocol_metadata": """\
Extract the schedule's identity.

Return JSON:
{"claims": [{
  "claim_id": str, "evidence_ids": [str], "confidence": float,
  "candidate": {"name": str, "description": str|null, "schedule_type": str}
}]}

Return at most one claim. `name` is the schedule's own title, e.g. "Schedule of
Assessments" or "Part A Schedule".""",

    "epochs": """\
Extract study periods (Screening, Treatment, Follow-up) if the protocol names them.

Return JSON:
{"claims": [{
  "claim_id": str, "evidence_ids": [str], "confidence": float,
  "candidate": {"code": str, "protocol_label": str, "display_name": str,
                "description": str|null, "sequence_number": int|null}
}]}

Return nothing if the protocol does not divide the schedule into named periods.""",

    "arms_cohorts_populations": """\
Extract the patient groups the schedule distinguishes: arms, cohorts, parts,
substudies, populations, sequences, countries.

Return JSON:
{"claims": [{
  "claim_id": str, "evidence_ids": [str], "confidence": float,
  "candidate": {
    "dimension_type": "ARM"|"COHORT"|"POPULATION"|"PART"|"SUBSTUDY"|"SEQUENCE"|"COUNTRY",
    "code": str, "protocol_label": str, "display_name": str,
    "description": str|null,
    "parent_dimension_type": str|null, "parent_code": str|null
  }
}]}

If the protocol nests groups ("Part A, Cohort 1"), set parent_dimension_type and
parent_code together. Set both or neither - a half-stated hierarchy behaves as
if there were none.""",

    "anchors": """\
Extract the reference dates the schedule counts from: randomisation, first dose,
last dose, surgery, disease progression, and any other stated reference point.

Return JSON:
{"claims": [{
  "claim_id": str, "evidence_ids": [str], "confidence": float,
  "candidate": {
    "code": str, "display_name": str, "anchor_type": str,
    "source_event_code": str|null,      # if this date comes from a visit occurring
    "source_condition_code": str|null   # if it comes from a clinical condition
  }
}]}

An anchor is a date a SITE SUPPLIES or that a recorded event produces. Do not
invent anchors the schedule does not count from.""",

    "events": """\
Extract every visit, contact, or assessment occasion in the schedule.

Return JSON:
{"claims": [{
  "claim_id": str, "evidence_ids": [str], "confidence": float,
  "candidate": {
    "code": str,                  # stable identifier, e.g. "C1D1", "WEEK_4"
    "protocol_label": str,        # exactly as the protocol column header reads
    "display_name": str,
    "event_type": str,            # SITE_VISIT, TELEPHONE_CONTACT, ASSESSMENT,
                                  # IMAGING, HOME_VISIT, INPATIENT_ADMISSION,
                                  # INPATIENT_DISCHARGE, UNSCHEDULED, OTHER
    "sequence_number": int|null
  }
}]}

One claim per DISTINCT occasion. A visit that repeats is ONE event with a
recurrence rule, not one event per occurrence - the recurrence stage handles
that. Include protocol-defined unscheduled visits.""",

    "event_types": """\
For each visit already identified, state how the patient attends it.

Return JSON:
{"claims": [{
  "claim_id": str, "evidence_ids": [str], "confidence": float,
  "candidate": {
    "event_code": str,
    "visit_mode": str|null,             # the single stated mode, or null
    "allowed_visit_modes": [str],       # every mode permitted, if more than one
    "activation": "SCHEDULED"|"ON_DEMAND"
  }
}]}

Modes: CLINIC, TELEPHONE, VIDEO, HOME, HOME_NURSE, IMAGING_CENTRE, LABORATORY,
PHARMACY, REMOTE.

If the protocol does not state a mode, set visit_mode to null and leave
allowed_visit_modes empty. Do NOT default to CLINIC - telling a patient to
travel when the protocol did not say so is the specific harm this avoids.

If the protocol permits more than one ("clinic or telephone"), list both in
allowed_visit_modes and leave visit_mode null: the choice is per patient.

activation is ON_DEMAND only for a visit that occurs when clinically indicated
rather than on a schedule ("Unscheduled Visit - may occur at any time").""",

    "timing": """\
For each visit, state WHEN it occurs, as a rule.

Return JSON:
{"claims": [{
  "claim_id": str, "evidence_ids": [str], "confidence": float,
  "candidate": {"event_code": str, "timing": <timing expression>}
}]}

A timing expression is one of:

  {"type": "PROTOCOL_DAY", "reference": {"kind":"ANCHOR","code": str}, "day": int}
  {"type": "OFFSET", "reference": {"kind":"ANCHOR","code": str},
   "offset": {"value": int, "unit": "DAY"|"WEEK"|"MONTH"|"YEAR"|"HOUR"|"MINUTE"}}
  {"type": "OFFSET", "reference": {"kind":"EVENT","event_code": str}, "offset": {...}}
  {"type": "NOMINAL_WINDOW", "nominal": <OFFSET or PROTOCOL_DAY>,
   "window": {"before": {"value": int, "unit": str}, "after": {"value": int, "unit": str}}}
  {"type": "CYCLE_DAY", "cycle_length": {"value": int, "unit": str}, "day": int,
   "reference": {"kind":"ANCHOR","code": str}}
  {"type": "WITHIN", "reference": {...}, "duration": {"value": int, "unit": str}}
  {"type": "NO_LATER_THAN", "reference": {...}, "limit": {"value": int, "unit": str}}
  {"type": "TRIGGERED", "trigger": {...}, "timing_after_trigger": {...}}
  {"type": "UNRESOLVED", "reason": str}

Use UNRESOLVED whenever the protocol's timing is absent, ambiguous, or expressed
in a way none of the above captures. Say precisely what is unclear in `reason`.
An UNRESOLVED timing blocks approval until a human decides, which is the correct
outcome - a guessed day is not.

Day numbering: protocols usually have no Day 0. If the protocol says "Day 1 =
first dose", a PROTOCOL_DAY of 1 IS the anchor date.""",

    "conditions": """\
Extract clinical conditions that gate whether a visit happens at all.

Return JSON:
{"claims": [{
  "claim_id": str, "evidence_ids": [str], "confidence": float,
  "candidate": {"event_code": str, "condition": <condition expression>}
}]}

A condition expression is one of:
  {"type":"COMPARISON","operator":"EQUALS"|"NOT_EQUALS"|"LT"|"LTE"|"GT"|"GTE",
   "left":{"kind":"FIELD","field": str}, "right":{"kind":"LITERAL","value": any}}
  {"type":"AND"|"OR","operands":[<condition>, ...]}
  {"type":"NOT","operand": <condition>}

Only for visits that are conditional on the patient's clinical state. A visit
that simply applies to one arm is NOT a condition - that is applicability.""",

    "dependencies": """\
Extract visits whose timing depends on another visit rather than on an anchor.

Return JSON:
{"claims": [{
  "claim_id": str, "evidence_ids": [str], "confidence": float,
  "candidate": {"event_code": str, "source_event_code": str,
                "dependency_type": "TEMPORAL"|"TRIGGER"|"PRECONDITION"|"SEQUENCE"|"ANCHOR"}
}]}""",

    "dependency_modes": """\
For each dependent visit, state whether it counts from the PLANNED date of the
previous visit or from the date the previous visit ACTUALLY happened.

Return JSON:
{"claims": [{
  "claim_id": str, "evidence_ids": [str], "confidence": float,
  "candidate": {"event_code": str,
                "dependency_mode": "NOMINAL"|"ACTUAL_PREVIOUS_EVENT"|"MANUAL"|"UNCLEAR"}
}]}

NOMINAL: the schedule grid does not move when a visit runs late.
ACTUAL_PREVIOUS_EVENT: later visits shift by however late the previous one was.
MANUAL: the protocol says a person decides.
UNCLEAR: the protocol does not say. Use this freely - it is the honest answer
and it stops the system from silently picking one behaviour over the other, which
would move real visit dates for real patients.""",

    "recurrence": """\
Extract visits that repeat.

Return JSON:
{"claims": [{
  "claim_id": str, "evidence_ids": [str], "confidence": float,
  "candidate": {"event_code": str, "recurrence": {
    "interval": {"value": int, "unit": "DAY"|"WEEK"|"MONTH"|"YEAR"},
    "start_reference": {"kind":"ANCHOR","code": str},
    "termination": {"type":"COUNT"|"DATE"|"EVENT"|"CONDITION"|"HORIZON",
                    "count": int|null, "date": str|null, "event_code": str|null,
                    "condition": <condition>|null},
    "include_start": bool
  }}
}]}

Use termination COUNT only when the protocol states a maximum number. If the
protocol gives no end ("continue every 21 days until progression" with no cap),
use EVENT or CONDITION if it names one, otherwise HORIZON. Never invent a
maximum: a protocol with no stated limit must keep no limit.""",

    "repeat_blocks": """\
Extract groups of visits that repeat together as a cycle, and what happens when
such a block is paused and resumed.

Return JSON:
{"claims": [{
  "claim_id": str, "evidence_ids": [str], "confidence": float,
  "candidate": {"code": str, "protocol_label": str, "display_name": str,
                "event_codes": [str],
                "dependency_mode": "NOMINAL"|"ACTUAL_PREVIOUS_EVENT"|"MANUAL"|"UNCLEAR",
                "resume_mode": "NOMINAL"|"ACTUAL_RESUME"|"MANUAL"|"UNCLEAR"}
}]}

resume_mode NOMINAL: after a hold, the cycle grid is unchanged.
resume_mode ACTUAL_RESUME: the cadence restarts from the day dosing resumed.
Use UNCLEAR when the protocol does not say. Do not guess - the two produce
different visit dates.""",

    "activities": """\
Extract the assessments performed at each visit - every marked cell in the
schedule of assessments.

Return JSON:
{"claims": [{
  "claim_id": str, "evidence_ids": [str], "confidence": float,
  "candidate": {"event_code": str, "code": str, "protocol_label": str,
                "display_name": str, "activity_type": str,
                "requiredness": "REQUIRED"|"OPTIONAL"|"CONDITIONAL"|"UNCLEAR",
                "sequence_number": int|null}
}]}

activity_type: ASSESSMENT, SAMPLE, TREATMENT, PROCEDURE, IMAGING, QUESTIONNAIRE,
ADMINISTRATIVE, OTHER.

If a cell carries a footnote marker, still report the activity here; the
qualifiers stage records what the marker means.""",

    "activity_timing": """\
Extract assessments with a stated time WITHIN the visit day - PK samples, ECGs
relative to dosing.

Return JSON:
{"claims": [{
  "claim_id": str, "evidence_ids": [str], "confidence": float,
  "candidate": {"event_code": str, "activity_code": str, "timing": <timing expression>}
}]}

Reference another activity with {"kind":"ACTIVITY","activity_code": str} - for
example "2 hours post-dose" is an OFFSET from the DOSE activity, not from
midnight. Include the window if one is given ("+/- 10 minutes").

Only for assessments the protocol actually times. Most have no intra-day time.""",

    "qualifiers": """\
Extract footnotes and table markers, and say what each one MEANS.

Return JSON:
{"claims": [{
  "claim_id": str, "evidence_ids": [str], "confidence": float,
  "candidate": {
    "marker": str|null,          # "a", "1", "*" - as printed
    "text": str,                 # the footnote, quoted
    "scope": "CELL"|"ROW"|"COLUMN"|"VISIT"|"ACTIVITY"|"BLOCK"|"ARM"|"COHORT"|"SUBSTUDY"|"PERIOD"|"GLOBAL",
    "category": "TIMING"|"WINDOW"|"CONDITION"|"APPLICABILITY"|"OPTIONALITY"|"EXCEPTION"|"REPEAT"|"STOP"|"SUBSTITUTION"|"SEQUENCE"|"DETAIL"|"UNRESOLVED",
    "target_codes": [str],       # the event or activity codes it applies to
    "resolved": bool
  }
}]}

Every marker you can see in the table must appear here, even if you cannot tell
what it means - in that case use category UNRESOLVED and resolved false. A
deterministic check compares your output against the markers found in the page
text and blocks approval for any you missed, so omitting one does not make it go
away.

target_codes must not be empty unless scope is GLOBAL.""",

    "conditional_actions": """\
Extract rules of the form "if X happens, then change the schedule".

Return JSON:
{"claims": [{
  "claim_id": str, "evidence_ids": [str], "confidence": float,
  "candidate": {
    "code": str, "protocol_label": str, "display_name": str,
    "condition": <condition expression>,
    "actions": [{
      "action_type": "ADD_EVENT"|"REPEAT_EVENT"|"STOP_BLOCK"|"PAUSE_BLOCK"|"RESUME_BLOCK"|"EXTEND_CONFINEMENT"|"CANCEL_EVENT"|"MANUAL_REVIEW",
      "target_code": str,
      "parameters": {"interval": {"value": int, "unit": str}}   # for REPEAT_EVENT
    }]
  }
}]}

Examples: "if ANC < 1000, repeat CBC weekly until recovery" is REPEAT_EVENT with
an interval. "On disease progression, discontinue treatment and begin survival
follow-up" is STOP_BLOCK plus ADD_EVENT.

These describe what the protocol says to do. They are never applied to a patient
without a human confirming.""",

    "conditional_resolution": """\
For each conditional rule, state what makes it STOP applying.

Return JSON:
{"claims": [{
  "claim_id": str, "evidence_ids": [str], "confidence": float,
  "candidate": {"code": str, "resolution_condition": <condition expression>}
}]}

"Repeat weekly until ANC recovers to >= 1000" resolves on ANC >= 1000. Without a
resolution the repeat has no stopping point, so report it if the protocol gives
one and omit it if not.""",

    "confinement": """\
Extract inpatient stays: periods where the patient is admitted and remains in
the unit across several days.

Return JSON:
{"claims": [{
  "claim_id": str, "evidence_ids": [str], "confidence": float,
  "candidate": {
    "code": str, "protocol_label": str, "display_name": str,
    "admission_event_code": str, "discharge_event_code": str,
    "dose_event_codes": [str],
    "days": [{"day_label": str, "relative_day": int, "activity_codes": [str]}]
  }
}]}

A confinement is ONE stay, not one visit per day. relative_day uses the
protocol's own day numbering, so a stay running Day -1 to Day 3 has
relative_day -1, 1, 2, 3 with no Day 0.

Only where the protocol says the patient remains resident. A run of daily
outpatient visits is not a confinement.""",
}
