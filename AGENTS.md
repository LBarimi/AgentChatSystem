# Repository workflow

- When requested work is complete, review the staged diff, commit related changes together, and push the commits to the current branch's configured remote. This is the user's standing preference; do not ask again for routine commits and pushes.
- Separate independent work into descriptive commits. Include relevant tests and translations with each feature, and update the README and release notes when behavior changes.
- Run the checks relevant to the changed code before committing. Preserve unrelated work and avoid force pushes or rewriting published history.
- Keep `bin/settings.ini`, runtime databases, uploads, logs, generated release archives, and personal client settings out of Git. Commit `bin/settings.example.ini` for defaults.
- Keep the project version at `1.0.0` and the license MIT unless the user requests otherwise.
