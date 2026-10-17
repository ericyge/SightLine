# Purple (nebulaONE agent)

Owner: D. The "Sightline Setup" agent is built in Purple's own UI, not in this repo. This folder holds the text you paste in and the tools to check its output.

| File | Use |
|---|---|
| `agent-instructions.md` | Paste into Purple as the agent's instructions (3 questions in, profile JSON out) |
| `knowledge.md` | Upload to Purple as the agent's knowledge file |
| `guidance-style-guide.md` | How each guidance style sounds (reference for the agent and the demo) |
| `sample-profile.json` | The exact example profile from the brief |
| `samples/` | More valid profiles: full household, kitchen with `left_right`, `beeps_only` |
| `validate_profile.py` | Checks the agent's output and can POST it to the backend |

## Workflow
1. In Purple, create the agent, paste `agent-instructions.md`, upload `knowledge.md`.
2. Chat with it. Copy its whole final reply (the JSON is pulled out of the code block automatically).
3. Validate, and send it to the running backend (PowerShell, from the repo root):
```powershell
Get-Clipboard | .\backend\.venv\Scripts\python purple\validate_profile.py - --post
```
   Or from a file: `.\backend\.venv\Scripts\python purple\validate_profile.py purple\samples\default-household.json --post`
4. Reload `/phone`. The room dropdown and watchlist now come from the profile.

The validator exits 0 when valid (warnings such as a thin description are only hints), 1 for errors, and 2 when it cannot find JSON.

Profile shape (see `docs/CONTRACT.md`): `{ watchlist:[{name,description,home_spot}], rooms:[str], guidance_style:"clock_face"|"left_right"|"beeps_only" }`

Note: `POST /api/profile` replaces the whole watchlist and rooms. Sightings already saved are kept.
