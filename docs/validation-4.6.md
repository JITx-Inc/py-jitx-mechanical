# JITX 4.6 validation

Local validation on November 2, 2026, on macOS ARM64, of base commit `95f8b6e`
plus the dependency change that adds this record. AI-generated changes and record.

## Environment

| Artifact | Tested version |
| --- | --- |
| `jitx` | `4.6.0`, JITX main feed |
| `jitxcore` | `4.6.0`, JITX main feed |
| `jitxlib-standard` | `4.6.0`, installed-wheel check only |
| Native runtime | `4.6.0-rc.17`, confirmed by `jitx runtime introspect` |
| Python | `3.12.13`, `3.13.8`, `3.14.8` |
| ezdxf / pytest | `1.4.5` / `9.2.0` |

An unpinned install from the JITX main feed resolved jitx 4.6.0 and jitxcore
4.6.0 on each Python version. The stable feed does not carry the 4.6.0 packages.

No native 4.6.0 release exists; `4.6.0-rc.17` is the newest 4.6 runtime. It came
from the macOS release asset, whose SHA-256 digest matched the published
`73cca7bb…7babc`, and was selected through `JITX_ROOT` with an isolated
installation root. The default runtime was not changed.

## Dependency bounds

`jitx>=4.6.0,<4.7` and `jitxcore>=4.6.0,<4.7` admit only final releases. A floor
that names a pre-release would also let pip select later pre-releases and the
`.devN` builds on the JITX main feed.

Captured geometry inside placed circuits is exported correctly only from jitx
`4.6.0rc9` onward. `test_live_placed_circuits_keep_pours_and_via_masks_in_place`
covers both cases:

| jitx | Native | Code pour in a circuit placed at (5, 3) | Via opening untented on the bottom, in a bottom-side circuit |
| --- | --- | --- | --- |
| `4.6.0rc4` | `4.6.0-rc.6` | (9, 5)–(11, 7): offset twice | `Soldermask_Top` |
| `4.6.0rc10` | `4.6.0-rc.6` | (4, 2)–(6, 4) | `Soldermask_Bottom` |
| `4.6.0` | `4.6.0-rc.17` | (4, 2)–(6, 4) | `Soldermask_Bottom` |

## Executed checks

```sh
pip install --extra-index-url https://pypi.jitx.com/jitx/main/+simple -e '.[dev]'
jitx runtime start --project <scratch project> --bg
JITX_MECHANICAL_RUNTIME_URI=<printed uri> pytest -q
ruff check src tests && ruff format --check src tests
python -m pyright --pythonpath "$(command -v python)"
python -m build
```

- **Suite:** 100 passed on each Python version, including both live capture tests.
  Ruff and Pyright reported no findings.
- **Release workflow steps:** with hatch 1.16.2, `hatch fmt --check`,
  `hatch run types:check`, `hatch test -py 3.12 --cover` (98 passed, 2 skipped
  live tests), and `hatch build` passed. The test environment resolved jitx 4.6.0
  and jitxcore 4.6.0.
- **Installed wheel:** the wheel metadata requires `jitx<4.7,>=4.6.0` and
  `jitxcore<4.7,>=4.6.0`. Installed in a clean Python 3.12 environment with
  `jitxlib-standard==4.6.0`, it passed `pip check` and `tests/test_plugin.py`
  (8 tests).
- **CLI exports:** I generated two boards with `jitx-mechanical import`: a
  synthetic EMN with plated and unplated holes, top and bottom keepouts, a cutout
  and a note, imported with `--hole-policy component`, and a synthetic DXF with
  slots and holes. Each was exported through the installed `jitx design export dxf`:
  - Each command ran alone under a 90-second bound and finished in about a second.
  - Python deprecation and runtime warnings were errors, except Typer 0.25.1's two
    deprecated Click 8.5.0 stream imports.
  - The outputs passed `ezdxf` audit.
  - A repeat without `--overwrite` exited 1 and left the file unchanged.
    `--overwrite` replaced it.
- **Back-to-back exports:** 16 consecutive exports alternating between the two
  designs completed.
- **Byte variability:** repeated exports differ in ezdxf header timestamps, GUIDs,
  and the order of two OBJECTS-section records. Geometry entities are identical.

## Remaining gates

1. **Batch export is unqualified.** On jitx 4.6.0rc10 with native rc.17, the STEP
   exporter's back-to-back exports of a routed design stalled in 3 of 16 runs.
   Each stall was inside `SyncRuntime.submit`, before any exporter code ran. This
   library's runs did not stall, but its designs had no routes or pours. The cause
   is unresolved upstream.
2. **No CAD application has opened the output.** Only ezdxf has read it back.

CI runs the offline suite and the installed-wheel plugin checks. It does not run
the live capture tests or a native runtime.
