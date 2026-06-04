# AGENTS.md

## Project Overview

This is the Pali Scheduler Django application.

This is an older Django scheduling application that the user started a few years ago. The current goal is not to add major new features yet. The current goal is to understand the existing codebase, stabilize the foundation, improve readability, document the current structure, and prepare the project for future development.

## Current Phase

The project is in a foundation/setup phase.

Prioritize:
- Repository orientation
- Formatting and readability
- Documentation
- Dependency/setup clarity
- Identifying app structure
- Identifying obvious bugs or configuration problems
- Creating a safe development workflow

## Primary Goals

1. Preserve existing app behavior unless explicitly instructed otherwise.
2. Improve code readability and organization in small, reviewable steps.
3. Identify broken setup issues, missing dependencies, unclear architecture, outdated patterns, and risky areas.
4. Create clear documentation so a human developer or AI coding agent can understand the project quickly.
5. Keep all changes narrow, intentional, and easy to review.

## Working Rules

- Do not make broad rewrites.
- Do not rename files, move files, change routes, change models, or change database schema unless explicitly asked.
- Do not add new dependencies unless explicitly asked.
- Do not change business logic unless explicitly asked.
- Prefer small commits or small diffs that touch only a few files.
- Before editing code, explain what you plan to change.
- After editing code, summarize exactly what changed and why.
- Always list files changed.
- Always include testing performed.
- If testing cannot be completed because of environment limitations, clearly explain what failed and why.
- If something is unclear, make a reasonable assumption, state it, and keep the change small.

## Django-Specific Guidance

- Treat manage.py as the main Django entry point.
- Inspect settings.py, urls.py, models.py, views.py, templates, and static files before making structural changes.
- Do not change database models or migrations unless explicitly instructed.
- Do not alter URL behavior unless explicitly instructed.
- Do not remove legacy or experimental files until their purpose is understood.
- Prefer documentation and diagnostics before refactoring.

## Preferred Task Output Format

For each task, return:

1. Summary
2. Files changed
3. Testing performed
4. Issues found
5. Recommended next step

## User Context

The user is learning the project and using AI coding agents. Explain findings clearly enough that a non-expert developer can understand what happened.
