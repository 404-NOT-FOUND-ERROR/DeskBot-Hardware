# Changelog

[简体中文](CHANGELOG.md) · [English](CHANGELOG.en.md)

## 2026-11-22 · Layered expressions and mainline synchronization

- Integrated 29 layered expressions from AirBoat revision `22bb4746`, smoothly scaling the 480 master to the 360 display. Includes entry/body/exit, interruption, cancellation and independent eye timing.
- The complete role package uses about 3.95 MiB, leaving about 4.05 MiB in the 8 MiB partition. Idle measures about 18 fps; authored timing is 25 fps and actions advance by elapsed time.
- Passed 17 focused tests, 87 reference poses, 586 direct-sampling comparisons and dirty-region reconstruction. Installation readback, boot, eight USB and two Wi-Fi dispatches passed; physical appearance and daily regression await acceptance.
- Synchronized the unified Studio, per-asset mirroring, owner-confirmed installation, client synchronization, Wi-Fi touch/expressions, XiaoZhi life-query/sharing tools, daily-service interfaces and tests.
- Reconciled Studio's cached firmware binding after adoption and updated bilingual overviews, architecture, pipeline and public evidence.

Records: [Layered integration](docs/layered-face-integration.md) · [Publication](docs/publication-20261122.md).

## 2026-11-21 · Complete new expression role

- Adapted 29 AirBoat expressions from candidate `0d087e8` into round-screen EAF assets, replacing the round character. Studio shares the decoded previews, three-phase transitions, mirror options and playback controls.
- The complete resident package uses about 6.55 MiB. The original role remains on the computer, with paired App/resource recovery and shared fonts, icons and wake-word models.
- Passed 50 focused tests, 39 browser checks and peer review, plus installation readback, boot, USB/Wi-Fi dispatch and user acceptance of display and daily interactions.

Record: [Role migration and acceptance](docs/airboat-role-migration.md).

## 2026-11-10 · E3f-4e accepted and E3f-5 shy playback installed

- The owner accepted expressions, menus, conversation, touch and sleep on E3f-4e / E1.45. This combination is the current rollback target.
- E3f-5 enables the existing shy playback option: 211 offline, 30 browser and 32 deployment checks, paired builds and independent review passed.
- The COM4 App installation passed readback, NVS, other Flash ranges, boot identity, the 37-entry directory and four dispatches: eat, neutral, embarrassed, eat. The owner accepted this playback sequence, menus, conversation, touch and sleep/wake.
- E3f-6 node playback and asset layouts are ready for the next device test.

Records: [baseline](docs/baseline-status.md) · [installation and recovery](docs/studio-shy-installation-e3f5.md) · [roadmap](docs/roadmap.md).

## 2026-11-06 · E3c-3a query-enabled interaction acceptance

- The query-enabled App was installed on COM4; App readback, NVS, non-App ranges and boot checks passed.
- The user confirmed 60 seconds idle, five menu cycles, three head touches, and sleep/wake. The device was offline, so conversation-time menus were skipped. Base tests and the 30-minute regression remain untested.
- One read-only `open` request timed out without establishing a session, so no state query was sent. The firmware supports `state` but not Studio's required `display` query.
- The probe did not save the serial write byte count or raw reply. Only the host timeout is confirmed; its cause remains unknown.

Record: [query-enabled live result](artifacts/ui-menu-regression-20261106/e3c3a-query-on-live-result.json) · [test sheet](docs/device-test-e3c3a-20261106.md).

## 2026-11-06 · E3c-3a query-disabled live acceptance

- The query-disabled build passed COM4 App write, readback, protected-range, NVS and boot checks.
- The user confirmed idle, five menu cycles, menu use during and after conversation, head touch, and sleep/wake. Ten minutes of logs showed no reset or receiver-task exit.
- This build remains the rollback target for the query-enabled build.

Record: [live result](artifacts/ui-menu-regression-20261106/e3c3a-live-result.json) · [query-enabled plan](artifacts/ui-menu-regression-20261106/e3c3a-query-preparation.json).

## 2026-11-06 · E3c-3a offline validation preparation

- Added the test sheet, passive logging and E3c-2 recovery instructions; 71 deployment transaction tests passed.
- Updated the architecture and execution guide to separate current and historical candidates, verify the active feature, and test each change with menu and interaction checks.
- Corrected only USB VFS path selection and startup logs in the frozen E3c-3 source; both query builds passed review.
- Eight receiver tests and 14 deployment tests passed. The original receive loop, 6 KiB stack and priority remain.
- The query-disabled plan covers only the COM4 App area and keeps E3c-2 as rollback. Live preflight checks the current full backup and NVS.
- This was offline work; the device remains on E3c-2. Next, test menus under continuous reception and state queries.

Record: [menu regression and test steps](docs/ui-menu-regression-20261106.md).

## 2026-11-06 · Menu regression comparison

- E3c-3 was written and booted, but USB input was unavailable; the state query was not reached and its menu was not tested.
- E3c-2 was restored. App readback, protected ranges, NVS, boot and the user-confirmed menu retest passed; the menu-freeze cause remains open.
- Next, verify USB VFS registration, driver ownership and the receive task before repeating the E3c-3 menu comparison.
- E3d-2 publication recheck: 33 focused tests passed; the full offline suite ran 1,294 tests (1,292 passed, two conditional skips).

Record: [menu regression](docs/ui-menu-regression-20261106.md).

## 2026-11-05 · E3d-2 live device catalog

- Added a resource-owner snapshot, USB paging and a live Studio catalog using the existing serial session.
- Publish the catalog after every page and the complete SHA-256 pass; rereads, disconnects, session expiry and catalog changes clear old results.
- Passed 33 host, production C++, workbench and evidence checks, plus 40 browser checks. The full offline suite ran 1,294 tests: 1,292 passed, two skipped.
- Both disabled/enabled firmware builds passed. Apps are 4,510,480 B / 4,517,968 B; bootloader, partition table, models and NVS configuration match.
- Archived reports retain their original bytes. Shared-source revisions have separate before/after digests and reasons. The device remains on E3c-8; physical integration follows later.

Results: [E3d-2](docs/deskbot-studio-e3d2-20261105.md). Next: E3c-8 neutral and interaction acceptance → catalog deployment → same-session catalog comparison.

## 2026-11-05 · E3d-1 catalog and draft comparisons

- Added a verified directory: 28 expression semantics, 18 EAF files, five motions and one listening-state animation.
- Compare resource snapshots with character drafts using search, motion filters, circular animation previews and node-editor links.
- Verified `surprised → shocked.eaf` and nine semantics sharing Happy; resources, request capabilities and drafts have separate records.
- Added catalog paging within the existing USB session and an offline receiver; two entries per page, complete SHA-256 verification after assembly.
- Passed 37 focused Python tests and 25 browser checks; the full offline suite ran 1,256 tests (1,254 passed, two skipped). The device remains on E3c-8; neutral playback and interaction acceptance resume when online.

Results: [E3d-1](docs/deskbot-studio-e3d1-20261105.md). Next: an owner-published device snapshot and paged reads.

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
