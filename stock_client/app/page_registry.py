"""PAGE_REGISTRY — single source of truth for top-nav pages.

Adding a future feature page = one register_page() call + a factory module.
Insertion order defines the nav order.
"""

# key -> {"title_i18n": <i18n key>, "factory": callable(ctx) -> QWidget}
PAGE_REGISTRY: dict = {}


def register_page(key: str, title_i18n_key: str, factory):
    PAGE_REGISTRY[key] = {"title_i18n": title_i18n_key, "factory": factory}


def page_keys() -> list:
    return list(PAGE_REGISTRY.keys())
