"""Restore theme settings and persist changes made through Textual's own controls."""

from textual.app import App

from pist.settings import PistSettings, SettingsStore


def configure_theme[ReturnType](
    app: App[ReturnType], settings: PistSettings, store: SettingsStore | None
) -> None:
    """Apply a registered theme and save subsequent changes to the supplied store."""
    if settings.theme in app.available_themes:
        app.theme = settings.theme
    else:
        app.notify(f'主题不可用：{settings.theme}，使用默认主题。', severity='warning')
    if store is None:
        return

    def save_theme(theme: str) -> None:
        try:
            current = store.load()
            store.save(current.model_copy(update={'theme': theme}))
        except (OSError, ValueError) as error:
            app.notify(f'主题已切换，但保存设置失败：{error}', severity='warning')

    # Initial restoration is not a user change; never rewrite settings on startup.
    app.watch(app, 'theme', save_theme, init=False)
