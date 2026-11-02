# JITX 4.6 validation

Local validation on October 30, 2026, of commit `4549bde` on macOS ARM64.
AI-generated changes and record.

## Environment

| Artifact | Tested version |
| --- | --- |
| `jitx` | `4.6.0rc10`, JITX main feed |
| `jitxcore` | `4.6.0rc3`, JITX main feed |
| `jitxlib-standard` | `4.6.0rc3`, installed-wheel check only |
| Native runtime | `4.6.0-rc.17`, confirmed by `jitx runtime introspect` |
| Python | `3.12.13`, `3.13.8`, `3.14.8` |
| ezdxf / pytest | `1.4.5` / `9.2.0` |

The native runtime came from the `4.6.0-rc.17` macOS release asset. Its
SHA-256 digest matched the published `73cca7bb…7babc`. It was selected through
`JITX_ROOT` with an isolated installation root; the default runtime was not changed.

## Minimum version

Before jitx `4.6.0rc9`, captured geometry inside placed circuits was exported in
the wrong place. `test_live_placed_circuits_keep_pours_and_via_masks_in_place`
covers both cases:

| jitx | Native | Code pour in a circuit placed at (5, 3) | Via opening untented on the bottom, in a bottom-side circuit |
| --- | --- | --- | --- |
| `4.6.0rc4` | `4.6.0-rc.6` | (9, 5)–(11, 7): offset twice | `Soldermask_Top` |
| `4.6.0rc10` | `4.6.0-rc.6` | (4, 2)–(6, 4) | `Soldermask_Bottom` |
| `4.6.0rc10` | `4.6.0-rc.17` | (4, 2)–(6, 4) | `Soldermask_Bottom` |

The difference follows the Python package, so the floor is `jitx>=4.6.0rc10`.
jitx rc10 itself requires `jitxcore>=4.6.0rc3`.

## Executed checks

```sh
pip install --extra-index-url https://pypi.jitx.com/jitx/main/+simple -e '.[dev]' 'jitx==4.6.0rc10' 'jitxcore==4.6.0rc3'
jitx runtime start --project <scratch project> --bg
JITX_MECHANICAL_RUNTIME_URI=<printed uri> pytest -q
ruff check src tests && ruff format --check src tests
python -m pyright --pythonpath "$(command -v python)"
python -m build
```

- **Suite:** 146 passed on each Python version, including both live capture tests.
  Ruff and Pyright reported no findings.
- **pytest 9.2:** it rejects `pytest.approx` on nested sequences, which broke two
  existing assertions. They now compare point by point.
- **Installed wheel:** installed with `jitxlib-standard==4.6.0rc3` in a clean
  Python 3.12 environment. `pip check` passed, `tests/test_plugin.py` passed
  (8 tests), and an unpinned install resolved jitx rc10 with jitxcore rc3.
- **CLI exports:** I used `jitx-mechanical import` to generate boards from
  `test1.emn`, `board_c.emn`, and `board_b.emn` (the last with
  `--hole-policy component`). Each was exported through the installed
  `jitx design export dxf`:
  - Each command ran alone under a 90-second bound and finished in 0.7–2.4 seconds.
  - Python deprecation and runtime warnings were errors, except Typer 0.25.1's two
    deprecated Click 8.5.0 stream imports.
  - The outputs passed `ezdxf` audit. Their layer and entity counts (1, 525, and
    4870 entities) matched exports from the checkout and from jitx rc4.
  - A repeat without `--overwrite` exited 1 and left the file unchanged.
    `--overwrite` replaced it.
- **Byte variability:** repeated exports differ in ezdxf header timestamps, GUIDs,
  and the order of two OBJECTS-section records. Geometry entities are identical.
- **Back-to-back exports:** 16 consecutive exports of the same design completed.

## Release gates

This record qualifies release candidates only. Before a release on production 4.6:

1. **Raise the floors to final releases.** Change them to `jitx>=4.6.0` and
   `jitxcore>=4.6.0`. While a floor names a pre-release, pip also accepts later
   pre-releases and the `.devN` builds on the main feed.
2. **Install from the release feed.** Point the README and CI at the feed that
   publishes 4.6.0. The stable feed currently carries jitx 4.4.x only.
3. **Repeat these checks** against the production Python packages and native runtime.
4. **Batch export is unqualified.** On the same stack, the STEP exporter's
   back-to-back exports of a routed design stalled in 3 of 16 runs. Each stall was
   inside `SyncRuntime.submit`, before any exporter code ran. This library's own
   runs did not stall, but its designs had no routes or pours. The cause is
   unresolved upstream.
5. **No CAD application has opened the output.** Only ezdxf has read it back.
6. **Publishing is not set up.** The repository has no publishing workflow or
   release tag.

CI runs the offline suite and the installed-wheel plugin checks. It does not run
the live capture tests or a native runtime.
