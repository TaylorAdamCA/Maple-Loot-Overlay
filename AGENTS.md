# Maple Loot Overlay

This is a separate read-only analytics fork. Never modify, import, deploy, start, stop or send commands to the sibling MaplePatrolVM or MapleFramework helpers. Do not send game inputs or read game memory.

Keep upstream bug fixes small and separate from fork features. Retain the MIT notices. Use a separate data directory and mutex; do not migrate upstream or helper settings. Default external sharing and update checks off for this personal fork.

Unreadable values are unknown, never zero. Distinguish confirmed collected-item counts, inventory changes, cash change, estimated loot value, supply consumption and profit. Do not count sales twice. Preserve evidence and corrections. Tests must include missing/duplicate/stale readings, stack rearrangement and accounting boundaries.

Use test-first behavior changes and independent adversarial review. Claude reviews must record actual returned model IDs and findings. No live-game test is required to run pure logic and recorded-image tests. Report calibration limitations explicitly.
