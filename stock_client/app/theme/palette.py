"""Semantic colors for programmatic use (status dots, result pills, sparkline stroke).

Keep in sync with app/theme/qss/*.qss — QSS handles widget chrome; this dict
handles anything drawn in code.
"""

DARK = {
    "window": "#1E1F22",
    "surface": "#26282C",
    "card": "#2E3136",
    "border": "#3A3D42",
    "text": "#E6E6E6",
    "muted": "#9AA0A6",
    "accent": "#F1553B",
    "accent_pressed": "#D6452C",
    "success": "#3FB950",
    "warning": "#D29922",
    "danger": "#F85149",
}

LIGHT = {
    "window": "#F7F8FA",
    "surface": "#FFFFFF",
    "card": "#FFFFFF",
    "border": "#DDE1E6",
    "text": "#24292F",
    "muted": "#57606A",
    "accent": "#D6452C",
    "accent_pressed": "#B83A24",
    "success": "#1A7F37",
    "warning": "#9A6700",
    "danger": "#CF222E",
}

THEMES = {"dark": DARK, "light": LIGHT}


def current(theme: str) -> dict:
    """Return the semantic color dict for a theme name (falls back to dark)."""
    return THEMES.get(theme, DARK)
