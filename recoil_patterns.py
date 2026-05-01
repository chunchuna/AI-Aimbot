"""
CS2 Recoil Pattern Data
========================
Per-bullet recoil compensation offsets for common CS2 weapons.

Data sourced from two verified open-source CS2 anti-recoil projects:
  - NoRecoil-CS2 (AHK) — per-bullet delta mouse moves with per-bullet timing
  - CS2-No-Recoil-LOGITECH (Lua) — sub-frame micro-steps aggregated per bullet

Format: each weapon has a list of (dx, dy, delay_ms) tuples:
  - dx, dy  = mouse delta to COMPENSATE recoil for this bullet
              (positive dy = move mouse DOWN to counter upward recoil)
              (positive dx = move mouse RIGHT to counter leftward pull)
  - delay_ms = time in milliseconds between this bullet and the next

These values are raw pixel deltas at a reference sens of ~2.5 @ default DPI.
The caller should multiply by (recoil_strength) to scale for their sensitivity.
Bullet index 0 = first shot (usually no recoil).
"""

# fmt: off

RECOIL_DATA = {
    "AK-47": {
        "mag_size": 30,
        "pattern": [
            # Extracted from NoRecoil-CS2: AK-47, Sleep 99ms per bullet
            # Phase 1: slow vertical climb (bullets 1-4)
            (0, 0, 99),        # bullet 1: first shot, no recoil
            (-4, 7, 99),       # bullet 2: slight pull down-right
            (4, 19, 99),       # bullet 3: strong upward kick begins
            (-3, 29, 99),      # bullet 4: heavy vertical recoil
            (-1, 31, 99),      # bullet 5: peak vertical
            # Phase 2: peak vertical + lateral drift (bullets 6-10)
            (13, 31, 99),      # bullet 6: peak + strong right pull
            (8, 28, 99),       # bullet 7: still heavy vertical
            (13, 21, 99),      # bullet 8: vertical easing, right drift
            (-17, 12, 99),     # bullet 9: sharp left correction
            (-42, -3, 99),     # bullet 10: strong left, slight down
            # Phase 3: lateral oscillation (bullets 11-20)
            (-21, 2, 99),      # bullet 11: continuing left
            (12, 11, 99),      # bullet 12: swing right + up
            (-15, 7, 99),      # bullet 13: left again
            (-26, -8, 99),     # bullet 14: strong left + slight down
            (-3, 4, 99),       # bullet 15: settling
            (40, 1, 99),       # bullet 16: strong right swing
            (19, 7, 99),       # bullet 17: right + up
            (14, 10, 99),      # bullet 18: continuing right
            (27, 0, 99),       # bullet 19: strong right
            (33, -10, 99),     # bullet 20: right + down recovery
            # Phase 4: tail recovery (bullets 21-30)
            (-21, -2, 99),     # bullet 21: left correction
            (7, 3, 99),        # bullet 22: slight right
            (-7, 9, 99),       # bullet 23: left + up
            (-8, 4, 99),       # bullet 24: left
            (19, -3, 99),      # bullet 25: right + down
            (5, 6, 99),        # bullet 26
            (-20, -1, 99),     # bullet 27: left
            (-33, -4, 99),     # bullet 28: strong left
            (-45, -21, 99),    # bullet 29: very strong left + down
            (-14, 1, 80),      # bullet 30: final left
        ],
    },
    "M4A4": {
        "mag_size": 30,
        "pattern": [
            # Extracted from NoRecoil-CS2: M4A4, Sleep ~87-88ms per bullet
            (0, 0, 88),        # bullet 1
            (2, 7, 88),        # bullet 2
            (0, 9, 87),        # bullet 3
            (-6, 16, 87),      # bullet 4
            (7, 21, 87),       # bullet 5
            (-9, 23, 87),      # bullet 6
            (-5, 27, 87),      # bullet 7
            (16, 15, 88),      # bullet 8
            (11, 13, 88),      # bullet 9
            (22, 5, 88),       # bullet 10
            (-4, 11, 88),      # bullet 11
            (-18, 6, 88),      # bullet 12
            (-30, -4, 88),     # bullet 13
            (-24, 0, 88),      # bullet 14
            (-25, -6, 88),     # bullet 15
            (0, 4, 87),        # bullet 16
            (8, 4, 87),        # bullet 17
            (-11, 1, 87),      # bullet 18
            (-13, -2, 87),     # bullet 19
            (2, 2, 88),        # bullet 20
            (33, -1, 88),      # bullet 21
            (10, 6, 88),       # bullet 22
            (27, 3, 88),       # bullet 23
            (10, 2, 88),       # bullet 24
            (11, 0, 88),       # bullet 25
            (-12, 0, 87),      # bullet 26
            (6, 5, 87),        # bullet 27
            (4, 5, 87),        # bullet 28
            (3, 1, 87),        # bullet 29
            (4, -1, 87),       # bullet 30
        ],
    },
    "M4A1-S": {
        "mag_size": 20,
        "pattern": [
            # Extracted from NoRecoil-CS2: M4A1-S, Sleep 88ms per bullet
            (0, 0, 88),        # bullet 1
            (1, 6, 88),        # bullet 2
            (0, 4, 88),        # bullet 3
            (-4, 14, 88),      # bullet 4
            (4, 18, 88),       # bullet 5
            (-6, 21, 88),      # bullet 6
            (-4, 24, 88),      # bullet 7
            (14, 14, 88),      # bullet 8
            (8, 12, 88),       # bullet 9
            (18, 5, 88),       # bullet 10
            (-4, 10, 88),      # bullet 11
            (-14, 5, 88),      # bullet 12
            (-25, -3, 88),     # bullet 13
            (-19, 0, 88),      # bullet 14
            (-22, -3, 88),     # bullet 15
            (1, 3, 88),        # bullet 16
            (8, 3, 88),        # bullet 17
            (-9, 1, 88),       # bullet 18
            (-13, -2, 88),     # bullet 19
            (3, 2, 88),        # bullet 20
        ],
    },
    "Galil AR": {
        "mag_size": 35,
        "pattern": [
            # Extracted from NoRecoil-CS2: Galil, Sleep 90ms per bullet
            (0, 0, 90),        # bullet 1
            (4, 4, 90),        # bullet 2
            (-2, 5, 90),       # bullet 3
            (6, 10, 90),       # bullet 4
            (12, 15, 90),      # bullet 5
            (-1, 21, 90),      # bullet 6
            (2, 24, 90),       # bullet 7
            (6, 16, 90),       # bullet 8
            (11, 10, 90),      # bullet 9
            (-4, 14, 90),      # bullet 10
            (-22, 8, 90),      # bullet 11
            (-30, -3, 90),     # bullet 12
            (-29, -13, 90),    # bullet 13
            (-9, 8, 90),       # bullet 14
            (-12, 2, 90),      # bullet 15
            (-7, 1, 50),       # bullet 16: shorter delay
            (0, 1, 90),        # bullet 17
            (4, 7, 90),        # bullet 18
            (25, 7, 90),       # bullet 19
            (14, 4, 90),       # bullet 20
            (25, -3, 90),      # bullet 21
            (31, -9, 90),      # bullet 22
            (6, 3, 90),        # bullet 23
            (-12, 3, 90),      # bullet 24
            (13, -1, 90),      # bullet 25
            (10, -1, 90),      # bullet 26
            (16, -4, 90),      # bullet 27
            (-9, 5, 90),       # bullet 28
            (-32, -5, 90),     # bullet 29
            (-24, -3, 90),     # bullet 30
            (-15, 5, 90),      # bullet 31
            (6, 8, 90),        # bullet 32
            (-14, -3, 90),     # bullet 33
            (-24, -14, 90),    # bullet 34
            (-13, -1, 90),     # bullet 35
        ],
    },
    "FAMAS": {
        "mag_size": 25,
        "pattern": [
            # Extracted from NoRecoil-CS2: Famas, Sleep ~87-88ms per bullet
            (0, 0, 88),        # bullet 1
            (-4, 5, 88),       # bullet 2
            (1, 4, 88),        # bullet 3
            (-6, 10, 88),      # bullet 4
            (-1, 17, 88),      # bullet 5
            (0, 20, 88),       # bullet 6
            (14, 18, 88),      # bullet 7
            (16, 12, 88),      # bullet 8
            (-6, 12, 88),      # bullet 9
            (-20, 8, 88),      # bullet 10
            (-16, 5, 88),      # bullet 11
            (-13, 2, 88),      # bullet 12
            (4, 5, 87),        # bullet 13
            (23, 4, 88),       # bullet 14
            (12, 6, 88),       # bullet 15
            (20, -3, 88),      # bullet 16
            (5, 0, 88),        # bullet 17
            (15, 0, 88),       # bullet 18
            (3, 5, 80),        # bullet 19
            (-4, 3, 88),       # bullet 20
            (-25, -1, 80),     # bullet 21
            (-3, 2, 84),       # bullet 22
            (11, 0, 80),       # bullet 23
            (15, -7, 88),      # bullet 24
            (15, -10, 88),     # bullet 25
        ],
    },
    "UMP-45": {
        "mag_size": 25,
        "pattern": [
            # Extracted from NoRecoil-CS2: UMP-45, Sleep 90ms per bullet
            (0, 0, 90),        # bullet 1
            (-1, 6, 90),       # bullet 2
            (-4, 8, 90),       # bullet 3
            (-2, 18, 90),      # bullet 4
            (-4, 23, 90),      # bullet 5
            (-9, 23, 90),      # bullet 6
            (-3, 26, 90),      # bullet 7
            (11, 17, 90),      # bullet 8
            (-4, 12, 90),      # bullet 9
            (9, 13, 90),       # bullet 10
            (18, 8, 90),       # bullet 11
            (15, 5, 90),       # bullet 12
            (-1, 3, 90),       # bullet 13
            (5, 6, 90),        # bullet 14
            (0, 6, 90),        # bullet 15
            (9, -3, 90),       # bullet 16
            (5, -1, 90),       # bullet 17
            (-12, 4, 90),      # bullet 18
            (-19, 1, 85),      # bullet 19
            (-1, -2, 90),      # bullet 20
            (15, -5, 90),      # bullet 21
            (17, -2, 85),      # bullet 22
            (-6, 3, 90),       # bullet 23
            (-20, -2, 90),     # bullet 24
            (-3, -1, 90),      # bullet 25
        ],
    },
    "SG 553": {
        "mag_size": 30,
        "pattern": [
            # Extracted from NoRecoil-CS2: SG 553, Sleep ~88-89ms per bullet
            (0, 0, 89),        # bullet 1
            (-4, 9, 89),       # bullet 2
            (-13, 15, 89),     # bullet 3
            (-9, 25, 89),      # bullet 4
            (-6, 29, 88),      # bullet 5
            (-8, 31, 88),      # bullet 6
            (-7, 36, 80),      # bullet 7: peak vertical
            (-20, 14, 80),     # bullet 8: sharp left
            (14, 17, 89),      # bullet 9: right correction
            (-8, 12, 88),      # bullet 10
            (-15, 8, 89),      # bullet 11
            (-5, 5, 89),       # bullet 12
            (6, 5, 88),        # bullet 13
            (-8, 6, 89),       # bullet 14
            (2, 11, 88),       # bullet 15
            (-14, -6, 89),     # bullet 16: downward correction
            (-20, -17, 89),    # bullet 17: strong down+left
            (-18, -9, 88),     # bullet 18
            (-8, -2, 89),      # bullet 19
            (41, 3, 88),       # bullet 20: strong right swing
            (56, -5, 89),      # bullet 21: very strong right
            (43, -1, 88),      # bullet 22
            (18, 9, 89),       # bullet 23
            (14, 9, 88),       # bullet 24
            (6, 7, 89),        # bullet 25
            (21, -3, 95),      # bullet 26
            (29, -4, 89),      # bullet 27
            (-6, 8, 89),       # bullet 28
            (-15, 5, 89),      # bullet 29
            (-38, -5, 89),     # bullet 30
        ],
    },
    "AUG": {
        "mag_size": 30,
        "pattern": [
            # Extracted from NoRecoil-CS2: AUG, Sleep ~88-89ms per bullet
            (0, 0, 89),        # bullet 1
            (5, 6, 89),        # bullet 2
            (0, 13, 89),       # bullet 3
            (-5, 22, 89),      # bullet 4
            (-7, 26, 88),      # bullet 5
            (5, 29, 88),       # bullet 6
            (9, 30, 80),       # bullet 7: peak vertical
            (14, 21, 80),      # bullet 8
            (6, 15, 89),       # bullet 9
            (14, 13, 88),      # bullet 10
            (-16, 11, 89),     # bullet 11: left correction
            (-5, 6, 89),       # bullet 12
            (13, 0, 88),       # bullet 13
            (1, 6, 89),        # bullet 14
            (-22, 5, 88),      # bullet 15: left
            (-38, -11, 89),    # bullet 16: strong left+down
            (-31, -13, 89),    # bullet 17
            (-3, 6, 88),       # bullet 18
            (-5, 5, 89),       # bullet 19
            (-9, 0, 88),       # bullet 20
            (24, 1, 89),       # bullet 21: right swing
            (32, 3, 88),       # bullet 22
            (15, 6, 89),       # bullet 23
            (-5, 1, 88),       # bullet 24
            (0, 0, 89),        # bullet 25
            (0, 0, 88),        # bullet 26
            (0, 0, 89),        # bullet 27
            (0, 0, 88),        # bullet 28
            (0, 0, 89),        # bullet 29
            (0, 0, 88),        # bullet 30
        ],
    },
    # ===========================================================================
    # VALORANT Weapons — 瓦洛兰特武器压枪数据
    # ===========================================================================
    # Reference: varolant_script.lua Vandal data + known Valorant recoil specs.
    # At reference 800 DPI / 0.5 in-game sens.  verticalBase ≈ 3.5 px/bullet.
    # Positive dy = move mouse DOWN (compensate upward recoil).
    # Positive dx = move mouse RIGHT (compensate leftward pull).
    # ===========================================================================

    # --- Vandal: 9.75 RPS, 25 rounds, 102ms between shots ---
    # Stage pattern from Lua: 5 stages × 5 bullets each
    # Stage 1 (1-5):  vert×0.9, horiz=0
    # Stage 2 (6-10): vert×1.0, horiz±0.3
    # Stage 3 (11-15):vert×1.2, horiz±0.8 alternating R/L
    # Stage 4 (16-20):vert×1.3, horiz±1.2 alternating R/L
    # Stage 5 (21-25):vert×1.5, horiz±1.5 alternating R/L
    "Vandal (瓦)": {
        "mag_size": 25,
        "pattern": [
            # Stage 1: bullets 1-5 — tight grouping, mostly vertical
            (0, 0, 102),       # bullet 1: first shot, perfect accuracy
            (0, 3, 102),       # bullet 2
            (0, 3, 102),       # bullet 3
            (0, 3, 102),       # bullet 4
            (0, 3, 102),       # bullet 5
            # Stage 2: bullets 6-10 — slight horizontal drift begins
            (1, 4, 102),       # bullet 6
            (-1, 4, 102),      # bullet 7
            (1, 3, 102),       # bullet 8
            (-1, 4, 102),      # bullet 9
            (1, 3, 102),       # bullet 10
            # Stage 3: bullets 11-15 — right-left alternation starts
            (3, 4, 101),       # bullet 11: right pull
            (3, 4, 101),       # bullet 12: right
            (-2, 4, 101),      # bullet 13: left
            (-2, 5, 101),      # bullet 14: left
            (3, 4, 101),       # bullet 15: right
            # Stage 4: bullets 16-20 — stronger lateral movement
            (4, 5, 100),       # bullet 16: right
            (4, 5, 100),       # bullet 17: right
            (-3, 5, 100),      # bullet 18: left
            (-3, 4, 100),      # bullet 19: left
            (4, 5, 100),       # bullet 20: right
            # Stage 5: bullets 21-25 — max recoil, zigzag
            (5, 5, 99),        # bullet 21: right
            (5, 5, 99),        # bullet 22: right
            (-4, 5, 99),       # bullet 23: left
            (-4, 5, 99),       # bullet 24: left
            (5, 5, 99),        # bullet 25: right
        ],
    },

    # --- Phantom: 11 RPS, 30 rounds, 90ms between shots ---
    # Tighter grouping than Vandal, less vertical recoil, moderate horizontal
    "Phantom (幻)": {
        "mag_size": 30,
        "pattern": [
            # Stage 1: bullets 1-6 — very tight
            (0, 0, 90),       # bullet 1
            (0, 2, 90),       # bullet 2
            (0, 3, 90),       # bullet 3
            (0, 3, 90),       # bullet 4
            (1, 3, 90),       # bullet 5
            (-1, 3, 90),      # bullet 6
            # Stage 2: bullets 7-12 — moderate vertical
            (1, 4, 90),       # bullet 7
            (-1, 4, 90),      # bullet 8
            (2, 3, 90),       # bullet 9
            (-1, 3, 90),      # bullet 10
            (1, 4, 90),       # bullet 11
            (-2, 3, 90),      # bullet 12
            # Stage 3: bullets 13-18 — horizontal drift increases
            (3, 3, 90),       # bullet 13: right pull
            (2, 3, 90),       # bullet 14
            (-2, 3, 90),      # bullet 15: left
            (-3, 3, 90),      # bullet 16
            (2, 3, 90),       # bullet 17: right
            (3, 2, 90),       # bullet 18
            # Stage 4: bullets 19-24 — wider pattern
            (-3, 3, 90),      # bullet 19: left
            (-2, 3, 90),      # bullet 20
            (3, 3, 90),       # bullet 21: right
            (2, 3, 90),       # bullet 22
            (-3, 2, 90),      # bullet 23: left
            (-2, 3, 90),      # bullet 24
            # Stage 5: bullets 25-30 — tail
            (3, 2, 90),       # bullet 25
            (2, 2, 90),       # bullet 26
            (-2, 2, 90),      # bullet 27
            (-3, 2, 90),      # bullet 28
            (2, 2, 90),       # bullet 29
            (1, 2, 90),       # bullet 30
        ],
    },

    # --- Spectre: 13.33 RPS, 30 rounds, 75ms between shots ---
    # SMG — fast fire, low recoil, moderate spread
    "Spectre (幽)": {
        "mag_size": 30,
        "pattern": [
            (0, 0, 75),       # bullet 1
            (0, 2, 75),       # bullet 2
            (0, 2, 75),       # bullet 3
            (1, 2, 75),       # bullet 4
            (-1, 3, 75),      # bullet 5
            (1, 3, 75),       # bullet 6
            (-1, 3, 75),      # bullet 7
            (2, 2, 75),       # bullet 8
            (-1, 2, 75),      # bullet 9
            (1, 3, 75),       # bullet 10
            (-2, 2, 75),      # bullet 11
            (2, 2, 75),       # bullet 12
            (-1, 2, 75),      # bullet 13
            (2, 2, 75),       # bullet 14
            (-2, 2, 75),      # bullet 15
            (1, 2, 75),       # bullet 16
            (-1, 2, 75),      # bullet 17
            (2, 1, 75),       # bullet 18
            (-2, 2, 75),      # bullet 19
            (1, 1, 75),       # bullet 20
            (-1, 2, 75),      # bullet 21
            (2, 1, 75),       # bullet 22
            (-2, 1, 75),      # bullet 23
            (1, 1, 75),       # bullet 24
            (-1, 1, 75),      # bullet 25
            (1, 1, 75),       # bullet 26
            (-1, 1, 75),      # bullet 27
            (1, 1, 75),       # bullet 28
            (0, 1, 75),       # bullet 29
            (0, 1, 75),       # bullet 30
        ],
    },

    # --- Stinger: 16 RPS, 20 rounds, 62ms between shots ---
    # SMG — very fast fire, light recoil per bullet but accumulates
    "Stinger (刺)": {
        "mag_size": 20,
        "pattern": [
            (0, 0, 62),       # bullet 1
            (0, 2, 62),       # bullet 2
            (0, 2, 62),       # bullet 3
            (1, 2, 62),       # bullet 4
            (-1, 2, 62),      # bullet 5
            (1, 3, 62),       # bullet 6
            (-1, 3, 62),      # bullet 7
            (1, 2, 62),       # bullet 8
            (-1, 2, 62),      # bullet 9
            (2, 2, 62),       # bullet 10
            (-2, 2, 62),      # bullet 11
            (1, 2, 62),       # bullet 12
            (-1, 2, 62),      # bullet 13
            (2, 1, 62),       # bullet 14
            (-2, 2, 62),      # bullet 15
            (1, 1, 62),       # bullet 16
            (-1, 1, 62),      # bullet 17
            (1, 1, 62),       # bullet 18
            (-1, 1, 62),      # bullet 19
            (0, 1, 62),       # bullet 20
        ],
    },

    # --- Bulldog: 9.15 RPS, 24 rounds, 109ms between shots ---
    # Rifle — moderate recoil, slight left-right sway
    "Bulldog (斗)": {
        "mag_size": 24,
        "pattern": [
            (0, 0, 109),      # bullet 1
            (0, 3, 109),      # bullet 2
            (0, 3, 109),      # bullet 3
            (1, 4, 109),      # bullet 4
            (-1, 4, 109),     # bullet 5
            (1, 4, 109),      # bullet 6
            (-1, 4, 109),     # bullet 7
            (2, 4, 109),      # bullet 8
            (-2, 3, 109),     # bullet 9
            (2, 3, 109),      # bullet 10
            (-2, 3, 109),     # bullet 11
            (3, 3, 109),      # bullet 12
            (-2, 3, 109),     # bullet 13
            (2, 3, 109),      # bullet 14
            (-3, 2, 109),     # bullet 15
            (2, 3, 109),      # bullet 16
            (-2, 2, 109),     # bullet 17
            (3, 2, 109),      # bullet 18
            (-3, 2, 109),     # bullet 19
            (2, 2, 109),      # bullet 20
            (-2, 2, 109),     # bullet 21
            (2, 2, 109),      # bullet 22
            (-1, 2, 109),     # bullet 23
            (1, 2, 109),      # bullet 24
        ],
    },

    # --- Guardian: 6.5 RPS, 12 rounds, 153ms between shots ---
    # Semi-auto DMR — strong per-shot recoil, resets between shots
    "Guardian (卫)": {
        "mag_size": 12,
        "pattern": [
            (0, 0, 153),      # bullet 1
            (0, 5, 153),      # bullet 2
            (0, 5, 153),      # bullet 3
            (1, 5, 153),      # bullet 4
            (-1, 5, 153),     # bullet 5
            (1, 5, 153),      # bullet 6
            (-1, 5, 153),     # bullet 7
            (1, 5, 153),      # bullet 8
            (-1, 5, 153),     # bullet 9
            (1, 5, 153),      # bullet 10
            (-1, 5, 153),     # bullet 11
            (0, 5, 153),      # bullet 12
        ],
    },

    # --- Odin: 12 RPS → 15.6 RPS (spins up), 100 rounds ---
    # Heavy MG — starts slow, speeds up, heavy recoil
    "Odin (奥)": {
        "mag_size": 100,
        "pattern": [
            # Spin-up phase: bullets 1-10 (83ms between shots)
            (0, 0, 83),       # bullet 1
            (0, 4, 83),       # bullet 2
            (0, 4, 83),       # bullet 3
            (1, 5, 83),       # bullet 4
            (-1, 5, 83),      # bullet 5
            (1, 5, 83),       # bullet 6
            (-1, 5, 80),      # bullet 7
            (2, 5, 78),       # bullet 8
            (-2, 5, 75),      # bullet 9
            (2, 5, 72),       # bullet 10
            # Full speed phase: bullets 11-30 (64ms between shots)
            (-2, 4, 64),      # bullet 11
            (3, 4, 64),       # bullet 12
            (-2, 4, 64),      # bullet 13
            (2, 4, 64),       # bullet 14
            (-3, 4, 64),      # bullet 15
            (3, 3, 64),       # bullet 16
            (-2, 3, 64),      # bullet 17
            (2, 3, 64),       # bullet 18
            (-3, 3, 64),      # bullet 19
            (2, 3, 64),       # bullet 20
            (-2, 3, 64),      # bullet 21
            (3, 3, 64),       # bullet 22
            (-2, 3, 64),      # bullet 23
            (2, 3, 64),       # bullet 24
            (-2, 2, 64),      # bullet 25
            (2, 2, 64),       # bullet 26
            (-2, 2, 64),      # bullet 27
            (2, 2, 64),       # bullet 28
            (-2, 2, 64),      # bullet 29
            (2, 2, 64),       # bullet 30
            # Sustained fire: repeat last pattern (simplified, bullets 31+)
            (-2, 2, 64),      # bullet 31
            (2, 2, 64),       # bullet 32
            (-2, 2, 64),      # bullet 33
            (2, 2, 64),       # bullet 34
            (-2, 2, 64),      # bullet 35
            (2, 2, 64),       # bullet 36
            (-2, 2, 64),      # bullet 37
            (2, 2, 64),       # bullet 38
            (-2, 2, 64),      # bullet 39
            (2, 2, 64),       # bullet 40
        ],
    },

    # --- Ares: 10 RPS → 13 RPS (spins up), 50 rounds ---
    # Light MG — lighter than Odin, still spins up
    "Ares (战)": {
        "mag_size": 50,
        "pattern": [
            # Spin-up phase: bullets 1-8 (100ms between shots)
            (0, 0, 100),      # bullet 1
            (0, 3, 100),      # bullet 2
            (0, 3, 96),       # bullet 3
            (1, 4, 92),       # bullet 4
            (-1, 4, 88),      # bullet 5
            (1, 4, 84),       # bullet 6
            (-1, 4, 80),      # bullet 7
            (1, 4, 77),       # bullet 8
            # Full speed phase: bullets 9-30 (77ms between shots)
            (-1, 3, 77),      # bullet 9
            (2, 3, 77),       # bullet 10
            (-2, 3, 77),      # bullet 11
            (2, 3, 77),       # bullet 12
            (-1, 3, 77),      # bullet 13
            (2, 3, 77),       # bullet 14
            (-2, 3, 77),      # bullet 15
            (1, 2, 77),       # bullet 16
            (-1, 2, 77),      # bullet 17
            (2, 2, 77),       # bullet 18
            (-2, 2, 77),      # bullet 19
            (1, 2, 77),       # bullet 20
            (-1, 2, 77),      # bullet 21
            (2, 2, 77),       # bullet 22
            (-1, 2, 77),      # bullet 23
            (1, 2, 77),       # bullet 24
            (-1, 2, 77),      # bullet 25
            (1, 2, 77),       # bullet 26
            (-1, 2, 77),      # bullet 27
            (1, 2, 77),       # bullet 28
            (-1, 2, 77),      # bullet 29
            (1, 2, 77),       # bullet 30
        ],
    },

    # --- Disable recoil compensation ---
    "关闭 (Off)": {
        "mag_size": 0,
        "pattern": [],
    },
}

# fmt: on


# ============================================================================
# Rigid Recoil Data — ported from FullExternal-CS2-No-Recoil
# ============================================================================
# These are the RAW per-bullet delta arrays from the C++ project.
# Values are at a reference sensitivity; the caller divides by CS2 sensitivity.
# Format: {"weapon": {"size": N, "RawX": [...], "RawY": [...],
#           "delays": {smoothness_name: {"steps": int, "delay": [us1, us2]}}}}
# delay[0] = microseconds to sleep between each sub-step
# delay[1] = microseconds to sleep after all sub-steps for this bullet
# fmt: off

RIGID_RECOIL_DATA = {
    "AK-47": {
        "size": 31,
        "RawX": [0, 0, 0, 0, 0, 40, 40, -40, -90, -30, -20, -20, -20, 0, 80, 30, 50, 50, 30, 20, -20, -10, 0, 10, 0, -40, -90, -70, -30, -10, 0],
        "RawY": [0, 40, 40, 80, 80, 80, 80, 20, -10, 20, 0, 0, -10, 20, 30, -10, 20, 0, -10, -10, 10, 10, 10, 0, 10, -10, 0, -50, 10, -10, 0],
    },
    "M4A4": {
        "size": 31,
        "RawX": [0, 0, 0, 0, 0, -10, 10, 20, 20, 30, -40, -40, -40, -40, -40, -50, 0, 30, 30, 20, 60, 30, 40, 20, 10, 0, 0, 10, 10, 0, 0],
        "RawY": [0, 10, 30, 40, 40, 60, 60, 60, 30, 20, 20, 20, 0, -10, 0, 10, 10, 0, 0, 0, 10, 0, 0, 10, 0, 10, 10, 0, 0, 0, 0],
    },
    "M4A1-S": {
        "size": 26,
        "RawX": [0, 0, 0, 0, 0, -10, 0, 30, 10, 30, -10, -40, -20, -30, -20, -20, -30, -30, 10, -10, 0, 20, 40, 60, 10, 0],
        "RawY": [0, 10, 10, 30, 30, 40, 40, 50, 10, 10, 10, 20, 0, -10, 0, 0, -10, 0, 10, 0, 10, 0, 0, 20, 0, 0],
    },
}

RIGID_WEAPON_NAMES = list(RIGID_RECOIL_DATA.keys()) + ["关闭 (Off)"]

# fmt: on


def get_rigid_weapon_data(weapon_name, cs2_sensitivity):
    """
    Get the sensitivity-adjusted X/Y arrays for rigid recoil.
    Uses floats internally for precision; caller handles rounding per sub-step.
    Returns (X_float[], Y_float[], size) or (None, None, 0) if weapon not found.
    """
    data = RIGID_RECOIL_DATA.get(weapon_name)
    if data is None:
        return None, None, 0
    sens = max(cs2_sensitivity, 0.01)
    size = data["size"]
    X = [data["RawX"][i] / sens for i in range(size)]
    Y = [data["RawY"][i] / sens for i in range(size)]
    return X, Y, size


# Weapon display names for GUI
WEAPON_NAMES = list(RECOIL_DATA.keys())


def get_bullet_delta(weapon_name, bullet_index):
    """
    Get the per-bullet recoil compensation delta for a given weapon and bullet.

    Returns (dx, dy) — the mouse delta to apply for THIS bullet to counteract
    its recoil. Positive dy = move mouse down. Values should be multiplied by
    recoil_strength by the caller.
    """
    data = RECOIL_DATA.get(weapon_name)
    if data is None or not data["pattern"]:
        return (0, 0)
    pattern = data["pattern"]
    if bullet_index >= len(pattern):
        return (0, 0)  # past magazine, no compensation
    p = pattern[bullet_index]
    return (p[0], p[1])


def get_fire_interval_ms(weapon_name, bullet_index=0):
    """
    Get milliseconds between this bullet and the next.
    Each bullet can have its own timing (CS2 fire rate is not perfectly uniform).
    Falls back to 100ms if bullet_index is out of range.
    """
    data = RECOIL_DATA.get(weapon_name)
    if data is None or not data["pattern"]:
        return 0
    pattern = data["pattern"]
    if bullet_index < len(pattern):
        return pattern[bullet_index][2]
    return 100  # default fallback


def get_mag_size(weapon_name):
    """Get magazine size for a weapon."""
    data = RECOIL_DATA.get(weapon_name)
    if data is None:
        return 0
    return data["mag_size"]


# Legacy compatibility
def get_recoil_offset(weapon_name, bullet_index):
    """
    Get the cumulative recoil compensation offset up to a given bullet.
    Returns (cumulative_dx, cumulative_dy).
    """
    data = RECOIL_DATA.get(weapon_name)
    if data is None or not data["pattern"]:
        return (0, 0)
    pattern = data["pattern"]
    idx = min(bullet_index, len(pattern) - 1)
    cum_dx = sum(p[0] for p in pattern[:idx + 1])
    cum_dy = sum(p[1] for p in pattern[:idx + 1])
    return (cum_dx, cum_dy)
