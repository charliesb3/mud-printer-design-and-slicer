# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# Mud Printer Software — Claude Code Instructions

This file defines the operating rules Claude Code must follow when working in this repository.

The purpose of these rules is to maintain reliable project continuity across sessions, including sessions with little or no access to previous conversation history.

The project uses persistent Markdown memory files to preserve goals, architecture, decisions, current state, and next steps.

The codebase and Git history remain the authoritative record of the actual implementation.

---

# 1. MANDATORY OPERATING LOOP

For every substantive task in this repository, follow this loop.

## BEFORE WORK

Before proposing architecture, writing code, modifying code, debugging, or making substantive project decisions:

1. Read `PROJECT_OVERVIEW.md`.

2. Determine which subproject or subprojects are relevant to the requested work.

3. Read e `PROJECT_MEMORY.md` file for every relevant subproject.

4. Review documented:
   - goals
   - requirements
   - constraints
   - architecture
   - previous decisions
   - completed work
   - current state
   - open questions
   - next steps

5. Check whether the requested work conflicts with any documented decision, constraint, or architectural assumption.

Do not begin substantive work until this orientation has been completed.

Do not load unrelated subproject memories unless they are relevant to the task.

If work crosses subproject boundaries, read all relevant memories.

## DURING WORK

While working:

1. Treat documented goals, requirements, constraints, and prior decisions as established project context.

2. Do not silently contradict or replace an established decision.

3. If a different approach appears better than a documented decision, explicitly identify the conflict and discuss it with the user before changing direction.

4. Keep track of developments that materially change persistent project state.

These may include:

- completed work
- architectural decisions
- new requirements
- new constraints
- changed requirements
- important implementation decisions
- abandoned approaches
- reasons an approach failed
- newly discovered problems
- resolved problems
- changes in system relationships
- changes in priorities
- changes in next steps

Do not clutter persistent memory with:

- routine conversation
- transient debugging output
- unsuccessful experiments that have no future relevance
- trivial implementation details that can easily be recovered from the code
- speculative ideas that the user has not adopted

## AFTER WORK

Before declaring a substantive task complete:

1. Review what changed during the task.

2. Determine whether persistent project state changed.

3. If it did, update the relevant subproject `PROJECT_MEMORY.md`.

4. Determine whether anything changed at the overall-system level.

5. If it did, update `PROJECT_OVERVIEW.md`.

6. Check that documented Current State accurately describes what now exists.

7. Check that Completed Work reflects newly completed and verified work.

8. Check that Open Questions reflects questions that were resolved or newly discovered.

9. Check that Next Steps still represent the most useful next actions.

10. Verify that important decisions made during the task have been recorded.

11. Follow the Git Commit Procedure (Section 3) to stage, commit, and verify the completed unit of work.

12. Only after this check should the task be considered complete.

This operating loop is mandatory.

---

# 2. COMPLETION CHECK

Before declaring any substantive task complete, silently check:

- Did I read `PROJECT_OVERVIEW.md`?
- Did I identify the relevant subproject(s)?
- Did I read their `PROJECT_MEMORY.md` files?
- Did I respect existing requirements, constraints, and decisions?
- Did this work change persistent project state?
- If so, did I update the appropriate memory?
- Were any important decisions made that need to be recorded?
- Were any previous decisions changed?
- If so, was the reason recorded?
- Does Current State still describe reality?
- Are Next Steps still accurate?
- Did anything change that belongs in `PROJECT_OVERVIEW.md`?
- Did I follow the Git Commit Procedure?
- Did I stage only changes belonging to this task?
- Did I verify the commit succeeded?
- Did I report the commit hash?

Do not print this checklist after every task unless doing so is useful to the user.

The purpose of the checklist is verification, not additional conversational output.

---

# 3. GIT COMMIT PROCEDURE

Local Git commits are part of the normal completion process for substantive units of work.

A commit should represent a coherent, meaningful, verified unit of work — not every individual file edit or action.

Before declaring a substantive unit of work complete:

1. Complete the requested work.
2. Perform whatever verification or testing is appropriate for that work.
3. Update the relevant PROJECT_MEMORY.md file(s) if persistent project state changed.
4. Update PROJECT_OVERVIEW.md only if overall system-level state changed.
5. Run `git status`.
6. Review the relevant `git diff`.
7. Determine which changed files belong to the currt task.
8. Check for unrelated, unexpected, or user-created changes.
9. Stage only the files or portions of files that belong to the completed task.
10. Create a local Git commit with a concise, descriptive commit message explaining the meaningful unit of work that was completed.
11. Verify that the commit succeeded.
12. Report the resulting commit hash when reporting task completion.

## Commit Safety Rules

- Do not commit after every action or every file edit.
- Prefer one commit per coherent, working unit of development.
- Do not create meaningless micro-commits merely because a file changed.
- Do not use `git add .`, `git add -A`, or an equivalent indiscriminate staging command without first verifying that every included change belongs to the current task.
- Never silently include unrelated changes in a commit.
- Never discard, overwrite, revert, reset, or otherwise destroy unrelated or user-created changes in order to make the working tree clean.
- If unrelated changes are present, leave them untouched and commit only the changes belonging to the current task when they can be safely separated.
- If the task's changes cannot be safely separated from unrelated changes, stop and ask the user rather than guessing.
- Do not commit work that is known to be broken or incomplete as though it were completed.
- If a checkpoint commit for incomplete work would be useful, only create one when explicitly requested by the user, and clearly identify it as a checkpoint/WIP commit.
- Do not amend, squash, rebase, reset, force-push, or otherwise rewrite Git history unless explicitly requested by the user.
- Do not push commits to GitHub or any other remote repository automatically.
- A local commit and a remote push are separate actions.
- Only push when explicitly requested by the user.
- Do not create or switch branches automatically unless the task requires it or the user requests it.

## Relationship to the Memory System

For substantive project work, the preferred completion sequence is:

work
→ verify/test
→ up persistent memory if needed
→ review Git changes
→ commit the coherent unit of work
→ report completion

The codebase is authoritative for current implementation.
Git records implementation history.
PROJECT_OVERVIEW.md and PROJECT_MEMORY.md preserve project intent, decisions, state, and reasoning.

These systems should reinforce one another.

## What Counts as a Substantive Unit of Work

Use judgment.

Examples that generally SHOULD result in a commit:

- implementing a feature
- fixing a bug
- completing a meaningful refactor
- changing project architecture
- adding or changing persistent project documentation
- completing a configuration change
- completing a tested development milestone

Examples that generally SHOULD NOT independently trigger a commit:

- reading files
- investigating a problem without changing anything
- answering a question
- running a test
- inspecting logs
- temporary debugging experiments that are reverted
- an individual edit that is only one step within an unfinished large

When uncertain whether the current work constitutes a coherent completed unit, prefer finishing and verifying the logical unit before committing rather than producing unnecessary micro-commits.

---

# 4. MEMORY ARCHITECTURE

This repository represents the overall Mud Printer Software system.

Persistent project context is divided into:

`PROJECT_OVERVIEW.md`

and subproject-specific `PROJECT_MEMORY.md` files.

The memory hierarchy is:

Mud Printer Software
|
|-- PROJECT_OVERVIEW.md
|
|-- design-toolpath/
|   `-- PROJECT_MEMORY.md
|
|-- pi-interface/
|   `-- PROJECT_MEMORY.md
|
`-- arduino-grbl/
    `-- PROJECT_MEMORY.md

These divisions are organizational context boundaries.

They do NOT require the subprojects to become separate applications, repositories, or independently deployed programs.

The structure may be split, merged, renamed, or reorganized later if the actual architecture develops differently.

---

# 5. PROJECT_OVERVIEW.md

`PROJECT_OVERVIEW.md` is the canonical high-level memory for the entire Mud Printer Software system.

It should contain information such as:

- the overall project goal
- the fundamental philosophy of the system
- high-level system architecture
- relationships between subprojects
- project-wide requirements
- project-wide constraints
- hardware/software boundaries
- major system-wide decisions
- interfaces between major components
- major unresolved system-level questions

Do not use `PROJECT_OVERVIEW.md` as a running work log.

Do not duplicate detailed subproject history into it.

Only update it when something meaningful changes at the overall-system level.

---

# 6. SUBPROJECT 1 — DESIGN + TOOLPATH

Memory location:

`design-toolpath/PROJECT_MEMORY.md`

This subproject concerns the environment for designing printable forms and translating those designs into machine instructions.

It includes, but is not limited to:

- the stacked 2D vector approach currently being considered
- creation of printable geometry
- manipulatioof printable geometry
- layer-based design
- transformations between layers
- keyframes
- interpolation between layers or keyframes
- visualization
- print preview
- clay-specific toolpath generation
- printability constraints
- machine-aware design constraints
- path ordering
- print parameters
- conversion of resulting paths into G-code or other machine instructions

Design and toolpath are intentionally being treated as one subproject for now.

This is because the intended design representation may correspond closely to the physical paths followed by the printer, making the conventional separation between CAD and slicing less appropriate for this machine.

This boundary may be reconsidered later.

Do not assume that Design and Toolpath must remain combined permanently.

---

# 7. SUBPROJECT 2 — RASPBERRY PI / PRINTER INTERFACE

Memory location:

`pi-interface/PROJECT_MEMORY.md`

This subproject concerns the higher-level computer and user interface used to operate the printer.

The Raspberry Pi is intend to perform the higher-level role currently served by Universal G-code Sender, but with software and an interface designed specifically around this machine.

It includes, but is not limited to:

- communication with the Arduino/GRBL controller
- sending G-code or other machine instructions
- serial communication
- jogging
- homing
- starting print jobs
- pausing jobs
- resuming jobs
- stopping jobs
- displaying machine position
- displaying machine state
- alarms and errors
- job/file management
- printer-specific controls
- browser-based user interface
- local operation
- offline operation
- Raspberry Pi deployment
- application startup behavior
- automatic startup when the printer powers on
- local network operation
- Wi-Fi access-point operation when appropriate
- allowing a laptop, tablet, or other device to control the printer without external internet infrastructure

The Raspberry Pi provides higher-level printer operation.

It should not be assumed to perform timing-critical motion control that belongs on the Arduino/GRBL layer.

---

# 8. SUBPROJECT 3 — ARDUINO / GRBL MOTION CONTROL

Memory location:

`arduino-grbl/PROJECT_MEMORY.md`

This subproject concerns the real-time motion-control layer running on the Arduino.

It includes, but is not limited to:

- GRBL
- GRBL configuration
- any necessary GRBL customization
- interpretation of incoming machine instructions
- real-time motion control
- step/direction signals
- axis behavior
- motor-control configuration
- limit switches
- homing
- machine I/O
- alarms and safety-related machine states
- communication between GRBL and the Raspberry Pi
- hardware-specific motion-controller requirements

The Arduino/GRBL layer should remain conceptually distinct from the Raspberry Pi interface.

In the current architecture:

Raspberry Pi:
higher-level printer operation and user interface

Arduino + GRBL:
real-time machine motion

Do not move timing-critical motion-control responsibilities to the Raspberry Pi without explicitly discussing the architectural chan with the user.

---

# 9. MEMORY MAINTENANCE RULES

Persistent memory should optimize for useful future orientation, not exhaustive history.

When updating memory:

1. Preserve original goals unless the user explicitly changes them.

2. Preserve important architectural decisions.

3. Preserve important constraints.

4. Record the reasoning behind consequential decisions when that reasoning will be useful later.

5. When a previous decision is intentionally changed, do not simply erase the old decision.

Record:
- what changed
- what the previous approach was
- why it changed

6. Record abandoned approaches when remembering why they were abandoned will prevent future wasted work.

7. Move completed items out of Current Work or Next Steps when appropriate.

8. Keep Current State synchronized with what actually exists.

9. Keep Open Questions current.

10. Keep Next Steps current and prioritized.

11. Do not claim work has been completed unless there is evidence that it has been completed.

12. Do not invent project history.

13. If information is uncertain, mark it as uncertain rather than converting an assumption into a fact.

14. Do not rewrite an entire memory file unnecessarily when a small targeted update is sufficient.

15. Keep memory concise enough that a fresh Claude session can efficiently read it before beginning work.

---

# 10. SOURCE-OF-TRUTH HIERARCHY

Different sources serve different purposes.

Use the following hierarchy:

## Actual implementation

The codebase is authoritative for what the software currently does.

## Implementation history

Git history is authoritative for what code changed and when.

## Project intent and state

`PROJECT_OVERVIEW.md` and the relevant `PROJECT_MEMORY.md` files are authoritative for:

- project goals
- architectural intent
- requirements
- constraints
- decisions
- reasoning
- current conceptual state
- unresolved questions
- intended next steps

## Conversation

Conversation provides immediate working context but should not be relied upon as the sole long-term record of important project information.

If conversation produces a consequential decision or project-state change, persist that information in the appropriate memory file.

---

# 11. HANDLING CONFLICTS

If sources appear to conflict:

Do not silently choose one.

For example, if:

- memory says a feature is implemented
- but the code does not contain it

or:

- memory specifies one architecture
- but the code appears to implement another

identify the discrepancy.

Use Git history when useful to determine what happened.

If the correct interpretation is still uncertain, ask the user rather than rewriting history based on an assumption.

---

# 12. INSTRUCTION PRIORITY

The following rules are mandatory:

1. Read project memory before substantive work.
2. Read only the relevant subproject memories unless multiple subprojects are involved.
3. Do not silently overturn documented requirements, constraints, or decisions.
4. Update persistent memory after meaningful project-state changes.
5. Do not invent history or completed work.
6. Verify memory state before declaring substantive work complete.
7. Treat code and Git as authoritative evidence of actual implementation.

Other instructions in this document should be treated as guidance for applying these mandatory rules effectively.

If this file becomes larger over time, preserve the visibility and priority of these mandatory rules.

---

# 13. KEEP THE MEMORY SYSTEM MAINTAINABLE

Do not allow the memory system itself to grow without purpose.

If a memory file becomes excessively large, repetitive, stale, or difficult to use:

- identify the problem
- propose a cleanup or restructuring
- preserve important historical information
- do not restructure persistent memory in a way that loses consequential decisions or context

If a subproject becomes large enough that its memory is no longer effective, propose splitting it.

If two subprojects become so interconnected that maintaining separate memories causes unnecessary duplication or confusion, propose merging or reorganizing them.

Do not make major changes to the memory architecture without discussing them with the user first.
