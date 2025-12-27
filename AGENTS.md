# Agent Instructions

This project follows the **LabsNFoundries** development process with **Guardrails** to manage code quality expectations across different stages of development.

## Process Overview

### LabsNFoundries Stages

This project uses a staged development process that balances human understanding (learning) with sustainable code (building). Code exists at different maturity levels, and you should adjust your code quality investment accordingly.

**Learning Stages** (focus: human understanding):
1. **Research** - Exploring the problem space (throwaway code, zero quality investment)
2. **Study** - Learning solution components (throwaway code, zero quality investment)
3. **POC** - Proving a solution slice works (minimal investment, focus on component contracts)

**Building Stages** (focus: sustainable code):
4. **Prototype** - First realistic use (moderate quality, clean interfaces, basic tests)
5. **MVP** - Complete enough for real use/refinement (high quality, solid architecture, good test coverage)
6. **Production** - Long-term sustainable solution (very high quality, comprehensive tests, full documentation)

**Key principle:** Different components in this codebase are at different stages. Always check which stage applies before making assumptions about code quality expectations.

See `process_docs/labs_n_foundries_0.2.org` for detailed stage descriptions.

### Stories and Tasks

Work is organized into **Stories** (higher-level goals) and **Tasks** (implementation steps):

**Stories** are tracked as org-mode documents:
- Location: `process_docs/stories/story-NNN-short-title.org`
- Template: `process_docs/stories/story-template.org`
- Each Story has: Title, ID, Stage assignment, Description, Constraints, Tasks, Retrospective
- Stories define WHAT to accomplish and at WHAT quality level (via Stage)

**Tasks** are tracked using beads issues:
- **CRITICAL**: You MUST create beads issues for all Story tasks
- Use `bd create` commands (see Quick Reference below)
- Link to parent Story in task description/title
- Tasks define HOW to implement the Story
- Update task status as you work (`bd update`, `bd close`)

**Mandatory Workflow:**

1. **Human creates Story document** with Stage assignment
2. **You create beads issues** for each planned task:
   ```bash
   bd create --title="[Story-003] Extract EventRouter to module" --type=task --priority=2
   bd create --title="[Story-003] Create EventNetServer base class" --type=task --priority=2
   # ... one issue per task
   ```
3. **Human reviews and approves** the task breakdown
4. **You implement** each task:
   - Mark task as `in_progress`: `bd update <id> --status=in_progress`
   - Implement code with stage decorators
   - Mark task as complete: `bd close <id>`
   - Reference beads issue in commit message
5. **Human reviews** implementation results
6. **You write Retrospective** section in Story document
7. **Story marked complete**, beads issues linked in "Related Work > Beads Tasks" section

**IMPORTANT**: Do NOT track tasks only in the Story document - always create beads issues. This provides persistent tracking across sessions and enables dependency management.

See `process_docs/guardrails_0.1.org` for complete workflow details.

## Beads Quick Reference

This project uses **bd** (beads) for task tracking.

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --status in_progress  # Claim work
bd close <id>         # Complete work
bd sync               # Sync with git
```

## Code Quality Guardrails

These rules apply to ALL stages unless the Story explicitly requests different behavior.

### DON'T Do (Avoid LLM Over-Engineering)

1. **Don't add parameters** unless there's a clear current need
2. **Don't add error handling** except for:
   - Normal shutdown logic (socket closing, task cancellation)
   - Explicit user request
3. **Don't add retry loops** - most errors can't be resolved by retrying
4. **Don't refactor** to extract common features without explicit direction
5. **Don't add unrequested features** in the name of "completeness"
6. **Don't add defensive code** like extensive parameter validation (let it crash with clear stack traces)

### DO This (Clarity Aids)

1. **Do use type annotations** on all functions and methods
2. **Do use Enums and Dataclasses** to clarify code structure
3. **Do provide comments** that increase human comprehension
4. **Do use the @stage decorator** to mark new code (see below)

**Philosophy:** Don't add code the human has to study when there's no immediate need. Do add things that make studying easier.

### Stage Decorator Usage

Mark all new functions, methods, and classes with the `@stage` decorator:

```python
from palaver.stage_markers import Stage, stage

@stage(Stage.MVP, track_coverage=True)
class NewFeature:
    """Production-ready component."""
    pass

@stage(Stage.POC, track_coverage=False)
def experimental_function():
    """Quick proof of concept."""
    pass
```

**Rules:**
- Always use the Stage assigned in the current Story
- Set `track_coverage=True` for Prototype/MVP/Production stages
- Set `track_coverage=False` for Research/Study/POC stages
- The decorator is zero runtime overhead (just adds metadata attributes)

See `src/palaver/stage_markers.py` for full API and `Stage.expected_quality` descriptions.

## Landing the Plane (Session Completion)

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **PUSH TO REMOTE** - This is MANDATORY:
   ```bash
   git pull --rebase
   bd sync
   git push
   git status  # MUST show "up to date with origin"
   ```
5. **Clean up** - Clear stashes, prune remote branches
6. **Verify** - All changes committed AND pushed
7. **Hand off** - Provide context for next session

**CRITICAL RULES:**
- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds

Use 'bd' for task tracking
