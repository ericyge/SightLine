# Project documentation

Read these documents at the start of each new chat working on Sightline. The repository's
[AGENTS.md](../AGENTS.md) instructs agents to read their contents before planning or editing.
This is a repository instruction, not an application setting that automatically attaches files.

1. [Project overview](PROJECT_OVERVIEW.md): purpose, architecture, implementation status, and remaining work.
2. [API contract](CONTRACT.md): exact request/response fields, coordinates, nullable confidence, and errors.
3. [Team roles](ROLES.md): ownership, completed implementation, and outstanding demo checkpoints.
4. [Vision guide](VISION.md): local setup, mock mode, adding credentials, importing video, reference photos, and benchmarks.

Also read the root [README.md](../README.md) for startup instructions and
[SUMMARY.md](../SUMMARY.md) for the latest changes and what they mean for the user.
New Markdown documents added under `docs/` are also part of new-chat context; keep this index current.

## How to interpret status

The vision adapter and offline pipeline are implemented. The last backend test run during that
implementation passed 89 tests using mock detections, offline SDK responses, and a generated clip.
A subsequent first real Mk1.5 clip test confirmed authentication and structured requests on
eight frames, returning five sightings with no errors (see `VISION.md`). Broader visual
accuracy, latency benchmarks, other models, and phone hardware checks remain pending. Read the vision guide before switching to real mode, particularly
its separate mock/real database instructions and model-discovery step.

When code changes behavior, update the relevant guide and contract together. Keep operational
instructions in `VISION.md` and `README.md`, interfaces in `CONTRACT.md`, and ownership/checklists
in `ROLES.md`. Write the user-facing change explanation in root `SUMMARY.md`.
