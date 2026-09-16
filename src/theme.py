"""
Design tokens for the Battle VoiceMod interface.

Single source of truth for colour, type, spacing, radius and elevation, so no
screen ever hardcodes a hex value. The palette is capped on purpose: two brand
colours (the TikTok cyan/red this app is already associated with), one neutral
ramp and a single semantic error colour. Anything beyond that dilutes identity
and is what makes generated UIs look generic.
"""

import customtkinter as ctk

# --- Neutrals (layered dark: depth comes from tone, not from borders) ---
BG_CANVAS = "#0F0F12"
BG_SURFACE = "#16161A"
BG_SURFACE_RAISED = "#1E1E23"
BG_SURFACE_HOVER = "#232329"
BORDER_SUBTLE = "#2A2A31"
BORDER_STRONG = "#3F3F46"

# --- Text (off-white, never pure #FFFFFF over a dark surface) ---
TEXT_PRIMARY = "#E8E8ED"
TEXT_SECONDARY = "#A1A1AA"
TEXT_TERTIARY = "#71717A"
TEXT_ON_BRAND = "#0F0F12"

# --- Brand (the cap: these two, and nothing else decorative) ---
BRAND_PRIMARY = "#25F4EE"
BRAND_PRIMARY_HOVER = "#00D2CB"
BRAND_ACCENT = "#FE2C55"
BRAND_ACCENT_HOVER = "#E0244A"
BRAND_PRIMARY_DIM = "#123033"
BRAND_ACCENT_DIM = "#2A1620"

# --- Semantic (one) ---
STATE_ERROR = "#F87171"
STATE_ERROR_HOVER = "#E05A5A"
STATE_ERROR_DIM = "#27141A"
STATE_WARNING = "#FBBF24"

# --- Type scale: four steps with real jumps (11 / 13 / 16 / 22) ---
FONT_FAMILY = "Segoe UI"
SIZE_DISPLAY = 22
SIZE_TITLE = 16
SIZE_BODY = 13
SIZE_LABEL = 11

_FONT_CACHE = {}


def font(size: int = SIZE_BODY, weight: str = "normal") -> ctk.CTkFont:
    """Returns a cached CTkFont. Created lazily so a Tk root already exists."""
    key = (size, weight)
    cached = _FONT_CACHE.get(key)
    if cached is None:
        cached = ctk.CTkFont(family=FONT_FAMILY, size=size, weight=weight)
        _FONT_CACHE[key] = cached
    return cached


# Shorthand accessors for the four steps
def display() -> ctk.CTkFont:
    return font(SIZE_DISPLAY, "bold")


def title() -> ctk.CTkFont:
    return font(SIZE_TITLE, "bold")


def body(bold: bool = False) -> ctk.CTkFont:
    return font(SIZE_BODY, "bold" if bold else "normal")


def label() -> ctk.CTkFont:
    return font(SIZE_LABEL, "bold")


# --- Spacing (8px grid) ---
SPACE_1 = 4
SPACE_2 = 8
SPACE_3 = 12
SPACE_4 = 16
SPACE_5 = 24
SPACE_6 = 32

# --- Radii (a closed scale: three values, no 10s and 12s mixed at random) ---
RADIUS_SM = 6
RADIUS_MD = 8
RADIUS_LG = 12

# --- Control metrics ---
HEIGHT_CONTROL = 34
HEIGHT_CONTROL_SM = 28
HEIGHT_SLIDER = 16
HEIGHT_PROGRESS = 10
