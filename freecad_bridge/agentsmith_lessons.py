"""Parsing, validating and appending entries in harness/lessons.md.

`lessons.md` is the most valuable file in the harness — every entry is a rule paid
for by a real failed or rejected task — and it has been maintained entirely by
hand. Manual processes like that stall: the two findings from the first full eval
sweep (a press-fit bore modelled at nominal, construction bodies left visible) sat
unrecorded because writing them up was a separate chore nobody was holding.

This module is the mechanical half: it knows the file's shape, so a candidate
entry can be generated, checked and appended without a human having to remember
the format. It deliberately does NOT write anything by itself — the panel shows
the proposal and a person accepts it. A rule the agent must treat as
non-negotiable should not appear in the file without someone agreeing to it.

Imports no FreeCAD, no Qt.
"""

import re

HEADING = re.compile(r"^##\s+L(\d+)\s+—\s+(.+?)\s*$", re.M)
RULE_MARKER = "**Rule:**"

# Entries are short on purpose: a rule nobody reads is not in effect. Anything
# longer than this is a playbook section wearing a lesson's clothes.
MAX_ENTRY_CHARS = 900


def parse_entries(text):
    """Every entry as {number, title, body}, in file order."""
    entries = []
    matches = list(HEADING.finditer(text or ""))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        entries.append({
            "number": int(match.group(1)),
            "title": match.group(2).strip(),
            "body": text[match.end():end].strip(),
        })
    return entries


def next_number(text):
    """The number a new entry should take: one past the highest used.

    Highest, not count: entries are never renumbered (rules are cited by number
    from the playbooks), so a gap left by a promoted entry must not be reused.
    """
    entries = parse_entries(text)
    return max((entry["number"] for entry in entries), default=0) + 1


def validate_entry(entry_text, existing_text=""):
    """Problems with a candidate entry, as a list of human-readable strings.

    Empty list means it is well-formed. This checks shape, not wisdom — whether
    the rule is *right* is exactly what the human confirmation step is for.
    """
    problems = []
    text = (entry_text or "").strip()
    if not text:
        return ["the entry is empty"]

    match = HEADING.search(text)
    if not match:
        problems.append("no heading in the form '## L<n> — <title> (<date>, <subject>)'")
    else:
        number = int(match.group(1))
        used = {entry["number"] for entry in parse_entries(existing_text)}
        if number in used:
            problems.append("L%d already exists — the next free number is L%d"
                            % (number, next_number(existing_text)))
        if match.start() != 0:
            problems.append("the heading must be the first line")
        if not re.search(r"\(\d{4}-\d{2}-\d{2}", match.group(2)):
            problems.append("the heading is missing an ISO date, e.g. (2026-07-25, wall hook)")

    if RULE_MARKER not in text:
        problems.append("no '%s' — an entry without a rule is just an anecdote" % RULE_MARKER)
    else:
        rule = text.split(RULE_MARKER, 1)[1].strip()
        if len(rule) < 20:
            problems.append("the rule is too short to act on")

    if len(text) > MAX_ENTRY_CHARS:
        problems.append("longer than %d characters — a rule nobody reads is not in effect; "
                        "shorten it or promote it into a playbook" % MAX_ENTRY_CHARS)

    body = HEADING.sub("", text, count=1).strip()
    if body.split(RULE_MARKER)[0].strip() == "":
        problems.append("no incident description before the rule — the entry must say "
                        "what actually happened")
    return problems


def append_entry(text, entry_text):
    """The file contents with the entry appended, separated by one blank line."""
    body = (text or "").rstrip("\n")
    entry = (entry_text or "").strip()
    if not entry:
        return text
    return "%s\n\n%s\n" % (body, entry)


def build_prompt(number, task_prompt, outcome_summary, reviewer_verdict, today):
    """The instruction handed to the small agent that drafts the entry.

    Deliberately narrow: one entry, the exact format, and an explicit permission
    to decline. A pipeline that must produce a rule every time will invent one,
    and an invented rule in a file the modelling agent treats as binding is worse
    than an empty file.
    """
    return (
        "You are recording ONE lesson in the AgentSmith modeling harness after a task "
        "that failed or was flagged by the reviewer.\n\n"
        "Write a single markdown entry in EXACTLY this shape, and nothing else — no "
        "preamble, no code fences:\n\n"
        "## L%d — <short imperative title> (%s, <part or task>)\n"
        "<one to three sentences on what actually happened and why it mattered.> "
        "%s <the binding rule, stated so a modeling agent can follow it without "
        "knowing this incident.>\n\n"
        "Rules for the rule:\n"
        "- It must be checkable. 'Be careful with tolerances' is not a rule; 'a press-fit "
        "bore is modelled undersize — state the interference in the Spreadsheet' is.\n"
        "- It must generalise beyond this one part, or it does not belong here.\n"
        "- Keep the whole entry under %d characters.\n"
        "- If the failure was caused by the harness/tooling rather than by modelling "
        "judgement, or if there is no generalisable rule in it, reply with exactly "
        "NO LESSON and one sentence saying why. An invented rule is worse than none, "
        "because the modeling agent treats this file as binding.\n\n"
        "THE TASK THAT WAS REQUESTED:\n%s\n\n"
        "HOW IT ENDED:\n%s\n\n"
        "REVIEWER VERDICT (may be empty):\n%s\n"
        % (number, today, RULE_MARKER, MAX_ENTRY_CHARS,
           task_prompt or "(not recorded)",
           outcome_summary or "(not recorded)",
           reviewer_verdict or "(none)")
    )


def extract_entry(agent_output):
    """Pull the entry out of whatever the drafting agent replied.

    Returns (entry_text, declined). Models wrap things in prose or code fences even
    when told not to, and re-running costs a whole call — so salvage the entry if
    it is in there, and treat an explicit refusal as a valid answer, not an error.
    """
    text = (agent_output or "").strip()
    if not text:
        return "", False
    if "NO LESSON" in text.upper():
        return text, True

    fenced = re.search(r"```(?:markdown|md)?\s*(.*?)```", text, re.S)
    if fenced:
        text = fenced.group(1).strip()

    match = HEADING.search(text)
    if not match:
        return text, False
    return text[match.start():].strip(), False
