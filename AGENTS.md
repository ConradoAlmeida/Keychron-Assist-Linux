# AGENTS.md

Instructions for coding agents working on this repository.

## What this is

`kassist` is an unofficial local helper for the Keychron Launcher website on
Linux. It serves the app list over a local WebSocket (`127.0.0.1:40003`),
stores the Quick Start bindings and opens the bound app when a keyboard key
fires — through GNOME custom shortcuts or by reading evdev directly.

Keep the daemon **rootless** and **stdlib-only** (no third-party Python
packages), and keep compatibility with the Launcher protocol (see `README.md`)
and the `binds.json` format.

## Language

Code, comments, CLI output and repository docs are in **English**. Talk to the
maintainer in **Portuguese**.

## Layout

| path | what |
|---|---|
| `kassist.py` | daemon + CLI (stdlib only) |
| `install.sh` | per-user installer (`--udev`, `--rules`, `--browser`, `--uninstall`) |
| `debian/` | Debian/Ubuntu package (debhelper) |
| `packaging/kassist.spec` | Fedora RPM spec (COPR-ready) |
| `packaging/kassist-setup` | per-user setup shipped by both packages |
| `tests/ci.sh` | headless protocol slice (what CI runs) |
| `tools/selftest.py` | environment diagnostics + `--simulate` |
| `docs/release-notes/` | release bodies, one file per tag |
| `RELEASING.md` | the human release checklist |

## Checks before committing

```bash
bash tests/ci.sh
python3 -m compileall -q kassist.py tools tests
shellcheck install.sh tests/ci.sh packaging/kassist-setup debian/postinst
```

The CI (`.github/workflows/ci.yml`) also builds the `.deb` (debian:13) and the
`.rpm` (fedora:44) on every push. Do not commit build artifacts.

## Releases (automated on tag)

The publication itself is automated by `.github/workflows/release.yml`, which
runs **only when a `v*` tag is pushed**. It validates the tag against the
version files, builds the packages, generates `SHA256SUMS` and creates the
GitHub release with the workflow token.

Steps (full checklist in `RELEASING.md`):

1. Bump the version in all four places: `kassist.py` (`VERSION`),
   `debian/changelog`, `packaging/kassist.spec`, and the README package
   examples.
2. Add `docs/release-notes/vX.Y.Z.md` (it becomes the release body).
3. Commit, then:
   ```bash
   git tag -a vX.Y.Z -m "kassist X.Y.Z"
   git push origin main
   git push origin vX.Y.Z
   ```
4. Wait for the **Release** workflow and confirm the assets (`.deb`, `.rpm`,
   `SHA256SUMS`) on the release page.

Do **not** create or upload releases by hand, and never move a tag after a
release exists — bump the patch version instead.

## Constraints

- No new Python dependencies; the daemon must stay stdlib-only.
- No root for the daemon; sudo only for the udev rule and dependencies.
- Keep the server on `127.0.0.1` and the Origin allowlist intact.
- Support Python 3.9+ (CI matrix).
- Preserve the `binds.json` schema and the Launcher protocol behavior.
- The udev rule grants `uaccess` for evdev and hidraw; don't weaken it to
  world-readable devices.
