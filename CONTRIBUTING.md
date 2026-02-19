# Contributing

## Development Setup

```bash
git clone https://github.com/dw218192/repokit.git
cd repokit
ln -sfn "$PWD" test_driver/tools/framework   # or New-Item -ItemType Junction on Windows
./bootstrap.sh                                # or bootstrap.ps1
cd test_driver && ./repo test --verbose
```

The `test_driver/` directory is a dogfooding project — it consumes repokit as if it were a real consumer. Tests and the `publish` tool both run from there.

## Release Scheme

Repokit uses a two-branch model because it's consumed as a **git submodule**, not a package registry artifact. The `publish` tool in `test_driver/` acts as a packager — it strips dev-only files from `main` and produces a clean `release` branch that consumers point their submodule at.

### How it works

```
main (development)          release (packaged artifact)
  |                              |
  |  commit A                    |
  |  commit B                    |
  |  bump VERSION to 1.2.0       |
  |  push to main                |
  |      |                       |
  |   CI: test ───> pass ──> publish
  |                              |
  |                         strip dev files, copy the rest
  |                         commit "v1.2.0: ..."
  |                         tag v1.2.0
  |                         push release + tag
```

- **`main`** — development branch. Has tests, CI config, dev tooling, everything.
- **`release`** — the packaged output. This is an **orphan branch with intentionally unrelated history** — it shares no common ancestor with `main`. Think of it like a `dist/` directory that happens to live in a git branch. Each release commit is a full snapshot of the runtime files, not a merge or cherry-pick from `main`.

### Why not just tag main?

Consumers add repokit as a submodule. If they tracked `main`, they'd pull in `test_driver/`, `.github/`, and other dev-only files. A separate orphan branch gives consumers a clean tree with only runtime files — no dev history, no dev artifacts.

### Publishing a release

1. Bump `VERSION` (semver, e.g. `1.2.0`)
2. Commit and push to `main`
3. CI runs tests, then `./repo publish --push` which:
   - Checks out the `release` branch (creates it as an orphan if needed)
   - Removes all files, then copies only non-excluded files from `main`
   - Commits with message `v1.2.0: <main commit subject> (from main <sha>)`
   - Tags as `v1.2.0`
   - Pushes `release` branch and tag

If the tag already exists, publish is a no-op. If there are no file changes compared to the previous release, it skips the commit.
