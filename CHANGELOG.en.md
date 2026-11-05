# Changelog

[简体中文](CHANGELOG.md) · [English](CHANGELOG.en.md)

## 2026-11-05 · E3c-7/8 live USB transport

- Corrected the secondary-console path and single-byte read cadence; each tick drains at most 512 B, then yields for 10 ms.
- E3c-8 installed on COM4. App readback, protected ranges, NVS, boot, restart and live Studio state queries passed.
- 336 focused checks, 1,219 offline tests (1,217 passed, 2 skipped), both firmware configurations and the independent audit passed.
- Retained exact recovery and updated architecture, environment and throughput guidance.
- Neutral requests and physical interactions await an online idle session; base tests follow its repair.

Results: [E3c-8](docs/deskbot-studio-e3c8-20261105.md).

## 2026-11-05 · E3c-6 Studio integration

- Added application, page and player state cards, one neutral-expression button and recent device receipts.
- One USB process handles observation and explicit requests. Reboot, disconnect and lease expiry clear old requests; expression requests are sent once.
- Device acceptance and player dispatch are shown separately from host timeouts; late terminal receipts update the result.
- Made C++ fixture conditionals compatible with Linux GCC and made browser style checks wait for the current node.
- Passed 45 focused tests and 27 browser checks, including four production C++ endpoint cases, narrow layouts, dark theme and reduced motion. The full offline suite ran 1,182 tests: 1,180 passed and two were conditionally skipped.
- Firmware source and the installed E3b App are unchanged. E3c deployment and physical interaction acceptance come next.

Results: [E3c-6](docs/deskbot-studio-e3c6-20261105.md).

## 2026-11-05 · E3c-5 single-expression request

- Added one `neutral` request over the existing USB session; the original `Application::Schedule` calls the existing player.
- `accepted` means queued and `dispatched` means the player API was called. Busy, expired, disconnected, duplicate and identity changes return explicit results.
- Bounded to one pending request, one receipt and 64 deduplication entries. Original UI, audio, touch, DOA, sleep and player ownership remain unchanged.
- 42 focused tests plus prior regressions (254 total) passed; the full offline suite ran 1137 tests with 2 conditional skips; both builds and the independent audit passed.
- This increment is offline only: no serial access or device flashing. Next, connect the Studio state card and button, then run physical regression.

Result: [E3c-5](docs/deskbot-studio-e3c5-20261105.md).

## 2026-11-05 · USB state and display prerequisites

- E3c-2: fixed-size application/page snapshots with settled, transitioning and unknown states.
- E3c-3: USB state queries, PC validation and cache management; 178 offline tests, two builds and independent audit passed.
- E3c-4: player initialization and display-prerequisite queries; 34 new tests and 212 focused regression tests, two builds and an independent audit passed. Actual sleep remains unknown; expression control follows separately.
- Updated bilingual home pages, full development plan, mainline, action guide and publication record.
- Service tests create their temporary parent directories for fresh clones; browser test paths are configurable.
- The device remains on E3b revision 2. This update covers offline development and publication.

Results: [E3c-3](docs/deskbot-studio-e3c3-20261105.md) · [E3c-4](docs/deskbot-studio-e3c4-20261105.md).

## 2026-11-04 · Unified Studio and USB identity

- E2a: unified overview, nodes, assets, characters and publishing.
- E2b: reproducible packages with N32R16 target and capacity checks.
- E2c: expression catalog, request/receipt contracts and desktop simulation.
- E3a: read-only USB observation. E3b: identity, version and capacity announcements.
- E3b revision 2 installed; App readback, boot, identity, restart and reconnect passed. Physical interaction acceptance is pending.
- E3c-1: sessions, leases, rate limiting and disconnect handling; both configurations audited.
- E1.44/45: shy-animation adaptation, mirrored single-eye candidate and 8 MiB package preparation.

## 2026-10-29—11-03 · N32R16 and 8 MiB resources

- Confirmed 32 MiB Flash / 16 MiB PSRAM and validated OPI_STR capacity configuration and boot.
- Completed upper-region reads, known-pattern CPU access and exact restoration tests.
- Migrated E1.18 to an 8 MiB active partition, retaining speech models and previous resources.
- Preserved the accepted E1.42 recovery App and migration recovery materials. Continuous-mapping physical tests remain scheduled separately.

## 2026-10-24—28 · Node previews and character expressions

- E1.15—18: deployed and accepted `surprised → shocked.eaf` after package and official-player checks.
- E1.19—24: per-node previews, changed connections, draft export, package audits and mapping coverage.
- E1.25—30: expression-call observations, snapshot visualization and host capture contracts.
- E1.31—34: character drafts, new-animation import and layout/compression review.

Earlier work includes O2f2, A/B magnetic recognition, base events and accepted E1.13 lossless resource optimization. See [feature inventory](docs/feature-inventory.md) and [baseline records](docs/baseline-status.md).
