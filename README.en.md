# DeskBot Hardware

[简体中文](README.md) · [English](README.en.md)

**A new shell, a new character for your desktop companion.**

Firmware and expression tooling built on ESP-VoCat. The device recognizes shells, reports body state and performs expressions. A desktop client manages characters, voice and memory. Studio provides expression and motion authoring, previews, bindings and packaging.

## Progress · November 5, 2026

| Area | Available now | Next |
| --- | --- | --- |
| Device | XiaoZhi voice, head touch, circular expressions and official motion; E3c-8 installed, readback, restart and live state verified | Neutral request and interaction acceptance |
| Studio | Node/circular previews, character drafts, packaging, live state cards, a neutral button and receipts; live USB state verified on the device | Neutral request and receipts while online and idle |
| USB interface | One serial owner; sessions, state queries, neutral requests and original application scheduling; device receipts separate from host timeouts | Neutral request acceptance, then the installed catalog |
| Storage | N32R16: 32 MiB Flash, 16 MiB PSRAM; active expressions migrated to an 8 MiB partition | New shy animation and character packages |
| Shell recognition | Fixed A/B layouts, removal/reinstallation, stable windows and confidence validated | Reinstallation tests and persistent registration after base repair |
| Character client | Offline body-fact, character-binding and expression contracts | Character confirmation, then DeepSeek and voice services |

The device runs **E3c-8 App + E1.40 bootloader + E1.18 assets + upper-assets-8m partition table**. App readback, boot, restart and live USB state queries passed. The neutral request and physical interactions await an online idle session. Exact E3c-7 recovery and the accepted E1.42 App are retained. See [E3c-8 results](docs/deskbot-studio-e3c8-20261105.md). See [baseline status](docs/baseline-status.md) and the [changelog](CHANGELOG.en.md).

## Studio and previews

![Behavior, expression semantics, EAF resources, circular layout and motion branches](docs/images/expression-pipeline.svg)

| Entry | Purpose |
| --- | --- |
| [DeskBot Studio](artifacts/deskbot-studio-e3c6-20261105/index.html) | Overview, nodes, assets, characters, live state, expression requests and publishing |
| [Node previews](artifacts/expression-node-preview-e1-19-20261024/index.html) | Inspect animations, compare replacements and trace changed connections |
| [Character expression editor](artifacts/role-expression-workbench-e1-31-20261029/index.html) | Map expression semantics to resources per character |
| [Studio guide](docs/deskbot-studio-e3c6-20261105.md) | Local startup, state queries and expression receipts |
| [Visualization index](docs/visualizations.md) | Previews, reviews and machine-readable reports |

Download the repository to open public HTML previews, or run Studio locally:

```powershell
python -m pip install -r tests/requirements-body-facts-validation.txt
& .\scripts\start-deskbot-studio-e3c.ps1
```

The start script checks Offline Python and the IDF Python with pyserial. On another computer, use `serve-deskbot-studio-e3c.py --public --serial-python <Python path with pyserial>`. Open `http://127.0.0.1:8766/`. Public previews include official assets. Packaging and USB observation require the local inputs described in the guide. Licensing for the new shy candidate images is being reviewed; public artifacts retain provenance, bindings and capacity data.

## Product flow

```mermaid
flowchart LR
    A[Install a shell with a fixed magnet layout] --> B[Device identifies shell and body state]
    B --> C[Client matches a character\nOwner confirms new shells]
    C --> D[Character profile\nPersonality, voice, memory and expression bindings]
    D --> E[DeepSeek and voice services]
    E --> F[Device plays voice, expressions, lights and motion]
    F --> G[Client records feedback and character evolution]
    W[Studio\nAuthor, preview, bind and publish] --> D
    W --> F
```

This is the product target. Current work connects Studio to the head unit. Shell registration resumes after base repair; client integration and service replacement follow.

## Development sequence

1. **Complete expression and interaction acceptance**: [E3c-8](docs/deskbot-studio-e3c8-20261105.md) provides verified live state queries. Once online and idle, verify neutral receipts, screen playback, conversation, menus, head touch and sleep/wake.
2. **Expose the installed catalog**: confirm device resources, then add existing expressions and motions.
3. **Complete shell registration**: fixed-layout reliability, persistent profiles, confirmation and naming.
4. **Connect character profiles**: map shell IDs to characters, load expression/motion bindings and show the active character.
5. **Integrate DeepSeek and voice**: connect speech recognition, Agent and speech synthesis incrementally, then character evolution.

Each increment follows scope → offline tests → isolated builds and comparisons → backup/deployment → physical acceptance. See the [development plan](docs/development-plan-20261104.md), [architecture](docs/architecture.md) and [action guide](docs/development-pipeline-20261102.md).

## Development checks

```powershell
python scripts/check-publication.py
python scripts/run-offline-checks.py
```

Portable checks use Python 3.11+; native C/C++ tests require a compiler. Firmware uses ESP-IDF 5.5.5; see [firmware/body](firmware/body/README.md). On the development Windows machine, run `scripts/preflight-deskbot-toolchain.ps1` first. Tests, builds, installation records and physical acceptance are recorded separately.

| Directory | Contents |
| --- | --- |
| `firmware/body` / `firmware/releases` | S3 firmware, locked dependencies and N32R16 target |
| `firmware/base` | Historical C61 additions and upstream references |
| `protocol` | Body facts, shells, characters, expressions and USB contracts |
| `scripts` / `tools` / `tests` | Builds, Studio, diagnostics and regression tests |
| `artifacts` / `docs` | Public previews, results, architecture and plans |
| `gateway` | Early client connection prototype |

O2f2 and E1.13 remain historical acceptance anchors. Device backups, NVS, raw serial logs, credentials and build caches stay local. See the [publication record](docs/publication-20261105.md).

## Licensing

New project documentation, protocols and tools use [MIT](LICENSE). ESP-VoCat, XiaoZhi, ESP-IDF components, fonts and third-party animations retain their licenses; see [THIRD_PARTY.md](THIRD_PARTY.md).
