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

## Release runbook (agent)

When the maintainer asks to release `X.Y.Z`, reproduce exactly this sequence.
The build/upload is done by `.github/workflows/release.yml` on the tag push —
do **not** create the release by hand.

1. Start clean:
   ```bash
   git switch main && git pull --ff-only
   git status --short          # must be empty
   ```

2. Bump the four version places:
   ```bash
   sed -i 's/^VERSION = ".*"/VERSION = "X.Y.Z"/' kassist.py
   sed -i 's/^Version:.*/Version:        X.Y.Z/' packaging/kassist.spec
   # debian/changelog: prepend an entry
   #   kassist (X.Y.Z) unstable; urgency=medium
   #
   #     * <summary>.
   #
   #    -- Conrado Almeida <conrado.almeida@outlook.com>  $(date -R)
   # README.md: update the example package filenames
   ```

3. Write `docs/release-notes/vX.Y.Z.md` (shape: `docs/release-notes/v0.1.0.md`).

4. Run the checks:
   ```bash
   bash tests/ci.sh
   python3 -m compileall -q kassist.py tools tests
   shellcheck install.sh tests/ci.sh packaging/kassist-setup debian/postinst
   ```

5. Commit and push:
   ```bash
   git add -A && git commit -m "Release X.Y.Z"
   git tag -a vX.Y.Z -m "kassist X.Y.Z"
   git push origin main
   git push origin vX.Y.Z
   ```

6. Wait for the **Release** workflow and verify the assets:
   ```bash
   gh run watch                 # or the Actions page
   gh release view vX.Y.Z
   # without gh:
   curl -s https://api.github.com/repos/ConradoAlmeida/Keychron-Assist-Linux/releases/tags/vX.Y.Z
   ```

If the workflow fails **before** the release exists, fix and move the tag
(`git tag -f … && git push -f origin …`). If the release exists, never move the
tag: bump the patch version and repeat.

## Constraints

- No new Python dependencies; the daemon must stay stdlib-only.
- No root for the daemon; sudo only for the udev rule and dependencies.
- Keep the server on `127.0.0.1` and the Origin allowlist intact.
- Support Python 3.9+ (CI matrix).
- Preserve the `binds.json` schema and the Launcher protocol behavior.
- The udev rule grants `uaccess` for evdev and hidraw; don't weaken it to
  world-readable devices.
