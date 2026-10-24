# Releasing

CI runs on every push to `main` (protocol, distros, shellcheck, `.deb`, `.rpm`),
but it **does not publish** anything. A release is created only when a `v*` tag
is pushed; `.github/workflows/release.yml` then builds the packages and
publishes the GitHub release.

## 1. Bump the version (four places)

- `kassist.py` → `VERSION = "X.Y.Z"`
- `debian/changelog` → new top entry `kassist (X.Y.Z) unstable; urgency=medium`
  (native package: no Debian revision)
- `packaging/kassist.spec` → `Version: X.Y.Z`
- `README.md` → the example package filenames
  (`kassist_X.Y.Z_all.deb`, `kassist-X.Y.Z-1.fcXX.noarch.rpm`)

## 2. Release notes

Create `docs/release-notes/vX.Y.Z.md` (highlights, install, notes). It becomes
the release body.

## 3. Check, commit, tag

```bash
bash tests/ci.sh
python3 tools/selftest.py

git add -A
git commit -m "Release X.Y.Z"
git tag -a vX.Y.Z -m "kassist X.Y.Z"
git push origin main
git push origin vX.Y.Z        # this triggers the release workflow
```

The workflow refuses to publish when the tag does not match `VERSION`,
`debian/changelog` or the spec, or when the notes file is missing.

## 4. If the release must be redone

Only while **no release exists** for the tag:

```bash
git tag -f -a vX.Y.Z -m "kassist X.Y.Z"
git push -f origin vX.Y.Z
```

After a release is published, never move the tag: bump the patch version
(`X.Y.Z+1`) and repeat.

## What stays manual

- the version bump and the notes;
- the GitHub repository description/topics;
- COPR is not used (the `.rpm` is attached to the release).
