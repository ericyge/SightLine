# Purple (nebulaONE agent)

Owner: D. The "Sightline Setup" agent is built in Purple's own UI, not in this repo. This folder holds its text files.

Put here:
- `agent-instructions.md`: the agent's system instructions (3 questions in, profile JSON out)
- `knowledge.md`: the agent's knowledge file
- `sample-profile.json`: example of the JSON the agent must produce (already here)
- `guidance-style-guide.md`: how each guidance style should sound

The agent's output is POSTed to the backend:
```powershell
curl.exe -X POST -H "Content-Type: application/json" --data "@purple/sample-profile.json" http://localhost:8000/api/profile
```
Shape: `{ watchlist:[{name,description,home_spot}], rooms:[str], guidance_style:"clock_face"|"left_right"|"beeps_only" }` (see `docs/CONTRACT.md`).

TODO(D): write the instructions and knowledge file.
