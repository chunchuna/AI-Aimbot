"""
CS2 Recoil Pattern Data
========================
Per-bullet recoil compensation offsets for common CS2 weapons.

Each weapon has a list of (dx, dy) tuples representing the COMPENSATION
mouse movement needed per bullet (i.e., the inverse of the recoil pattern).
- Positive dy = move mouse DOWN (compensate upward recoil)
- Positive dx = move mouse RIGHT (compensate leftward recoil)

These values are NORMALIZED to a reference sensitivity of 1.0 at 800 DPI
on a 1920x1080 display. The actual mouse movement should be scaled by:
    actual_move = pattern_offset * (recoil_strength / sensitivity_scale)

Data sourced from community-measured CS2 spray patterns (csstats.gg,
Steam community guides, and Logitech macro community data).

Fire rate (rounds per minute) is used to estimate bullet index from
hold duration.
"""

# fmt: off

# Each entry: list of (dx, dy) per bullet, where bullet 0 = first shot
# dy > 0 means compensate downward (recoil goes up, so mouse goes down)
# dx > 0 means compensate rightward

RECOIL_DATA = {
    "AK-47": {
        "fire_rate": 600,  # RPM
        "mag_size": 30,
        # (dx, dy) compensation per bullet — normalized
        "pattern": [
            (0, 0),       # bullet 1: no recoil
            (0, -6),      # bullet 2
            (0, -7),      # 3
            (0, -8),      # 4
            (0, -8),      # 5
            (0, -9),      # 6
            (-2, -10),    # 7
            (-3, -9),     # 8
            (-5, -8),     # 9
            (-7, -7),     # 10
            (-8, -4),     # 11
            (-6, -3),     # 12
            (-3, -2),     # 13
            (1, -3),      # 14
            (5, -4),      # 15
            (8, -5),      # 16
            (10, -3),     # 17
            (9, -2),      # 18
            (6, -1),      # 19
            (3, 0),       # 20
            (-2, -1),     # 21
            (-6, -2),     # 22
            (-8, -1),     # 23
            (-6, 0),      # 24
            (-2, 1),      # 25
            (3, 0),       # 26
            (6, -1),      # 27
            (5, 0),       # 28
            (2, 1),       # 29
            (0, 0),       # 30
        ],
    },
    "M4A4": {
        "fire_rate": 666,
        "mag_size": 30,
        "pattern": [
            (0, 0),
            (0, -5),
            (0, -6),
            (0, -7),
            (-1, -7),
            (-2, -8),
            (-3, -7),
            (-4, -6),
            (-5, -5),
            (-4, -4),
            (-2, -3),
            (0, -3),
            (2, -4),
            (5, -4),
            (7, -3),
            (8, -2),
            (7, -1),
            (4, -1),
            (1, 0),
            (-2, -1),
            (-5, -2),
            (-7, -1),
            (-6, 0),
            (-3, 1),
            (0, 0),
            (3, -1),
            (5, 0),
            (4, 1),
            (2, 0),
            (0, 0),
        ],
    },
    "M4A1-S": {
        "fire_rate": 600,
        "mag_size": 25,
        "pattern": [
            (0, 0),
            (0, -5),
            (0, -6),
            (0, -6),
            (-1, -7),
            (-1, -7),
            (-2, -6),
            (-3, -5),
            (-3, -4),
            (-2, -3),
            (-1, -3),
            (1, -3),
            (3, -3),
            (5, -2),
            (6, -1),
            (5, -1),
            (3, 0),
            (1, 0),
            (-1, -1),
            (-3, -1),
            (-4, 0),
            (-3, 1),
            (-1, 0),
            (1, 0),
            (0, 0),
        ],
    },
    "Galil AR": {
        "fire_rate": 666,
        "mag_size": 35,
        "pattern": [
            (0, 0),
            (0, -4),
            (0, -5),
            (0, -6),
            (-1, -6),
            (-2, -6),
            (-3, -5),
            (-4, -4),
            (-3, -3),
            (-1, -3),
            (1, -3),
            (3, -3),
            (5, -3),
            (6, -2),
            (5, -1),
            (3, -1),
            (1, 0),
            (-1, -1),
            (-3, -1),
            (-4, -1),
            (-3, 0),
            (-1, 0),
            (1, -1),
            (3, -1),
            (4, 0),
            (3, 0),
            (1, 0),
            (-1, 0),
            (-2, 0),
            (-1, 0),
            (0, 0),
            (1, 0),
            (1, 0),
            (0, 0),
            (0, 0),
        ],
    },
    "FAMAS": {
        "fire_rate": 666,
        "mag_size": 25,
        "pattern": [
            (0, 0),
            (0, -4),
            (0, -5),
            (0, -5),
            (-1, -6),
            (-2, -5),
            (-2, -4),
            (-1, -3),
            (0, -3),
            (2, -3),
            (3, -2),
            (4, -1),
            (3, -1),
            (1, 0),
            (-1, -1),
            (-3, -1),
            (-4, 0),
            (-3, 0),
            (-1, 0),
            (1, -1),
            (2, 0),
            (2, 0),
            (1, 0),
            (0, 0),
            (0, 0),
        ],
    },
    "SG 553": {
        "fire_rate": 666,
        "mag_size": 30,
        "pattern": [
            (0, 0),
            (0, -5),
            (0, -6),
            (-1, -7),
            (-2, -7),
            (-3, -7),
            (-4, -6),
            (-5, -5),
            (-4, -4),
            (-2, -3),
            (0, -3),
            (2, -3),
            (4, -3),
            (6, -2),
            (7, -1),
            (6, -1),
            (4, 0),
            (1, 0),
            (-2, -1),
            (-4, -1),
            (-5, 0),
            (-4, 0),
            (-2, 0),
            (0, -1),
            (2, 0),
            (3, 0),
            (2, 0),
            (1, 0),
            (0, 0),
            (0, 0),
        ],
    },
    "AUG": {
        "fire_rate": 666,
        "mag_size": 30,
        "pattern": [
            (0, 0),
            (0, -4),
            (0, -5),
            (-1, -6),
            (-1, -6),
            (-2, -6),
            (-3, -5),
            (-3, -4),
            (-2, -3),
            (-1, -3),
            (1, -3),
            (3, -3),
            (4, -2),
            (5, -1),
            (4, -1),
            (2, 0),
            (0, -1),
            (-2, -1),
            (-3, 0),
            (-2, 0),
            (-1, 0),
            (1, -1),
            (2, 0),
            (2, 0),
            (1, 0),
            (0, 0),
            (-1, 0),
            (-1, 0),
            (0, 0),
            (0, 0),
        ],
    },
    "关闭 (Off)": {
        "fire_rate": 0,
        "mag_size": 0,
        "pattern": [],
    },
}

# fmt: on

# Weapon display names for GUI
WEAPON_NAMES = list(RECOIL_DATA.keys())

def get_recoil_offset(weapon_name, bullet_index):
    """
    Get the cumulative recoil compensation offset for a given weapon and bullet index.

    Returns (cumulative_dx, cumulative_dy) — the total offset from the first bullet's
    aim point that should be applied to counteract recoil up to this bullet.
    """
    data = RECOIL_DATA.get(weapon_name)
    if data is None or not data["pattern"]:
        return (0, 0)
    pattern = data["pattern"]
    # Clamp to mag size
    idx = min(bullet_index, len(pattern) - 1)
    # Sum all offsets from bullet 0 to idx (cumulative)
    # Pattern stores recoil direction; negate to get compensation direction
    cum_dx = -sum(p[0] for p in pattern[:idx + 1])
    cum_dy = -sum(p[1] for p in pattern[:idx + 1])
    return (cum_dx, cum_dy)

def get_fire_interval_ms(weapon_name):
    """Get milliseconds between bullets for a weapon."""
    data = RECOIL_DATA.get(weapon_name)
    if data is None or data["fire_rate"] <= 0:
        return 0
    return 60000.0 / data["fire_rate"]

def get_mag_size(weapon_name):
    """Get magazine size for a weapon."""
    data = RECOIL_DATA.get(weapon_name)
    if data is None:
        return 0
    return data["mag_size"]
