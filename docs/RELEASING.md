# Releasing paper-verify

The release workflow (`.github/workflows/release.yml`) publishes to PyPI via
**trusted publishing** (no API token in the repo) and attaches the web-chat
skill zip to the GitHub release. Until the one-time PyPI link below is done,
the PyPI step fails at auth — expected, documented, nothing silent.

## One-time setup (maintainer)

1. Create/log in to a PyPI account → <https://pypi.org/manage/account/publishing/>.
2. Add a **pending publisher**:
   - PyPI project name: `paper-verify`
   - Owner: `nolainjin` / Repository: `paper-verify`
   - Workflow name: `release.yml`
   - Environment: `pypi`
3. In the GitHub repo: Settings → Environments → create `pypi` (optionally
   require reviewers).

## Each release

1. Bump `version` in `pyproject.toml` (e.g. `0.2.0`); update README if the
   surface changed. Commit to `main`.
2. Tag and push:

   ```bash
   git tag v0.2.0 && git push origin v0.2.0
   ```

3. The workflow builds sdist+wheel, publishes to PyPI, creates the GitHub
   release, and attaches `paper-verify-webchat-skill.zip`.
4. Verify: `uvx paper-verify --list-profiles` resolves from PyPI and the
   release page shows the zip.

## After first PyPI release

Update README install sections from `git+` forms to plain
`uvx paper-verify` / `pip install paper-verify[...]` (keep the git form as the
"latest from source" alternative).
