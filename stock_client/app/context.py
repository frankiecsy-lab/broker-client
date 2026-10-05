"""AppContext — bundles app-wide singletons, passed to page factories."""

from dataclasses import dataclass


@dataclass
class AppContext:
    """Single object threaded into every page factory so adding a future page
    never means threading four separate arguments around."""

    settings: object      # AppSettings
    translator: object    # Translator (app.i18n)
    theme_mgr: object     # ThemeManager (app.theme)
    worker: object        # AsyncWorker (app.async_bridge); None until the bridge starts
    broker_dir: str       # path to the broker_client repo

    def t(self, key: str, **fmt):
        """Convenience passthrough so pages can write ctx.t('testtool.run')."""
        return self.translator.t(key, **fmt)
