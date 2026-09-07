# Releasing

Cutting a release is one commit and one tag. `.github/workflows/release.yml`
does the rest: tests, sdist + wheel, frozen binaries for four targets, the
Windows installer, provenance attestations, PyPI upload and a GitHub Release
with checksums.

```bash
# 1. bump the version in pyproject.toml, move CHANGELOG's "Unreleased"
#    heading to the new version, commit both
git commit -am "Release 1.0.0"

# 2. tag it — the tag must match the pyproject version or the run fails fast
git tag v1.0.0
git push origin main --tags
```

Watch the run under **Actions → Release**. Nothing is published until every
build and test job has passed.

## Trying it without publishing

**Actions → Release → Run workflow** with *dry run* left on builds and attests
everything but skips both publish jobs. Use this after changing the workflow
or the PyInstaller spec.

Locally:

```bash
pip install build twine pyinstaller ".[full]"
python -m build && python -m twine check dist/*
pyinstaller packaging/palaunch.spec       # -> dist/palaunch, dist/palaunchw
```

The Windows installer needs [Inno Setup](https://jrsoftware.org/isinfo.php)
and the frozen binaries in `dist/`:

```powershell
iscc /DVersion=1.0.0 packaging\palaunch.iss   # -> dist-installer\palaunch-setup-1.0.0.exe
```

## One-time setup

### PyPI (Trusted Publishing)

No API token is stored anywhere. PyPI verifies the workflow's OIDC identity
instead, so a leaked repository secret cannot be used to publish.

1. Create the project on PyPI (the first release can be uploaded manually, or
   use [pending publishers](https://pypi.org/manage/account/publishing/) to
   claim the name from CI directly).
2. **Manage project → Publishing → Add a new publisher**:

   | Field | Value |
   |-------|-------|
   | Owner | `MuszKarol` |
   | Repository | `profile-auto-launcher` |
   | Workflow | `release.yml` |
   | Environment | `pypi` |

3. In the repository, create an environment named `pypi`
   (**Settings → Environments**). Adding required reviewers there turns the
   upload into a manual approval gate.

### Attestations

Already on. Every artifact gets a Sigstore provenance attestation through
`actions/attest-build-provenance`, which needs no keys or secrets — the
signing identity is the workflow run itself. Anyone can check that a download
came from this repository's CI:

```bash
gh attestation verify palaunch-1.0.0-linux-x86_64.tar.gz --repo MuszKarol/profile-auto-launcher
sha256sum -c SHA256SUMS.txt --ignore-missing
```

### Platform code signing (optional)

Attestations prove where a binary came from. They do **not** stop macOS
Gatekeeper or Windows SmartScreen from warning about an unidentified
developer — that needs a platform certificate, which costs money and is tied
to a person or company.

The workflow signs when the secrets exist and ships unsigned binaries when
they don't, so you can add this later without touching the workflow.

**macOS** (Apple Developer Program, ~99 USD/year):

| Secret | What it is |
|--------|------------|
| `MACOS_CERTIFICATE` | base64 of your "Developer ID Application" `.p12` |
| `MACOS_CERTIFICATE_PASSWORD` | password for that `.p12` |
| `MACOS_SIGNING_IDENTITY` | e.g. `Developer ID Application: Your Name (TEAMID)` |
| `APPLE_ID`, `APPLE_TEAM_ID`, `APPLE_APP_PASSWORD` | notarisation; optional, but without it Gatekeeper still complains |

```bash
base64 -i DeveloperID.p12 | pbcopy    # value for MACOS_CERTIFICATE
```

**Windows** (code-signing certificate from any CA):

| Secret | What it is |
|--------|------------|
| `WINDOWS_CERTIFICATE` | base64 of the `.pfx` |
| `WINDOWS_CERTIFICATE_PASSWORD` | password for the `.pfx` |

The binaries are signed before Inno Setup packages them, so the installer
carries signed contents; signing the installer itself is one more `signtool`
call in the same step if you need it.

> An OV certificate does not reset SmartScreen reputation immediately — new
> binaries stay flagged until enough downloads accumulate. An EV certificate
> starts with reputation, and costs considerably more.

## What gets published

| Artifact | Where |
|----------|-------|
| `profile_auto_launcher-<v>.tar.gz`, `…-py3-none-any.whl` | PyPI + the GitHub Release |
| `palaunch-<v>-linux-x86_64.tar.gz` | GitHub Release |
| `palaunch-<v>-macos-arm64.tar.gz`, `…-macos-x86_64.tar.gz` | GitHub Release |
| `palaunch-<v>-windows-x86_64.zip` | GitHub Release |
| `palaunch-setup-<v>.exe` (Inno Setup installer) | GitHub Release |
| `SHA256SUMS.txt` | GitHub Release |

Each archive carries both binaries (`palaunch` and the console-free
`palaunchw`), the sample profiles, the example plugin, the README, the
changelog, `docs/` and the licence — enough to run without installing Python.
Running `./palaunch install` from an unpacked archive opens the same setup
window the Windows installer runs.

## If a release goes wrong

- **Tag/version mismatch** — the `verify` job fails before anything is built.
  Fix `pyproject.toml`, delete the tag (`git push --delete origin v1.0.0`),
  re-tag.
- **PyPI rejects the upload** — a version can never be re-uploaded. Bump to
  the next patch version and tag again.
- **A binary job fails** — the publish jobs depend on all of them, so nothing
  is released. Fix and re-tag.
