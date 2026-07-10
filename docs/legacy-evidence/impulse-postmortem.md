# Impulse Postmortem

Status: **LEGACY EXPLORATORY EVIDENCE**. Closed in May 2026 after forward paper
observation.

## Hypothesis

The experiment attempted to detect an early impulse and hold it until a
structural exit condition occurred. Historical replay looked promising enough
to justify a bounded paper observation, not a production claim.

## Outcome

Forward paper observation did not reproduce the replay result. Candidate
selection was too sensitive to the original market window, and the exit logic
gave back too much of the observed movement. The family was stopped rather
than promoted.

## What It Taught

- A favorable replay, including an out-of-sample split, does not replace
  forward paper evidence.
- Detecting a move and capturing its economic value are different problems.
- A small number of apparently strong instruments can create a misleading
  aggregate result.
- Exit geometry and risk containment need their own validation instead of
  inheriting credibility from entry detection.

## Current Relevance

This is not an active paper family. Its durable contribution is the signal to
outcome record: a candidate must be traceable through observation, exit, and
cost-aware outcome before it can influence later research.
