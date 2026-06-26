# GoldbergEmu Forks Repack

Automatically repacks Detanup01 and Alex47exe forks of Goldberg Emulator to avoid Windows Defender flags by repacking ColdClientLoader files separately.

- [Detanup01/gbe_fork](https://github.com/Detanup01/gbe_fork)
- [alex47exe/gse_fork](https://github.com/alex47exe/gse_fork)

**ColdClientLoader** is split into its own asset so the main **Detanup01** and **alex47exe** repacks are not flagged by Windows Defender. Users who need ColdClientLoader can download `ColdClientLoader-*-win.7z` (built from Detanup01 only).

## Release assets

| Asset | Source | Contents |
|-------|--------|----------|
| `Detanup01-*-win.7z` | Detanup01 | Main repack — no ColdClientLoader |
| `alex47exe-*-win.7z` | alex47exe | Main repack — no ColdClientLoader |
| `ColdClientLoader-*-win.7z` | Detanup01 | `release/steamclient_experimental/` + ColdClientLoader  |

