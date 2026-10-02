"""Shared warning cards and runtime diagnostics for Textual applications."""

from collections.abc import Callable
from dataclasses import dataclass
from functools import cached_property
from typing import ClassVar

from textual import events, on
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.notifications import SeverityLevel
from textual.screen import ModalScreen
from textual.widgets import Button, Static, TabbedContent, TabPane

from pist.ui.context_menu import ContextMenuScreen

RIGHT_MOUSE_BUTTON = 3


@dataclass(frozen=True, eq=False)
class RuntimeMessage:
    """One distinct warning or error, including repeated identical messages."""

    message: str
    title: str
    severity: SeverityLevel

    @property
    def text(self) -> str:
        return f'{self.title}\n{self.message}' if self.title else self.message


class DiagnosticAdded(Message):
    """Transfer a runtime diagnostic to the app's UI event loop."""

    def __init__(self, diagnostic: RuntimeMessage) -> None:
        super().__init__()
        self.diagnostic = diagnostic


class MessageActionScreen(ContextMenuScreen[None]):
    """Copy the selected card through a cursor-anchored context menu."""

    def __init__(self, text: str, screen_x: int, screen_y: int) -> None:
        super().__init__(screen_x, screen_y)
        self._text = text

    def compose(self) -> ComposeResult:
        with Vertical(classes='context-menu-dialog'):
            yield Button('复制', id='message-action-copy')

    @on(Button.Pressed, '#message-action-copy')
    def copy_message(self, event: Button.Pressed) -> None:
        event.stop()
        self.app.copy_to_clipboard(self._text)
        self.dismiss()


class MessageCard(Horizontal):
    """One copyable diagnostic, optionally removable from runtime history."""

    def __init__(self, text: str, *, runtime_message: RuntimeMessage | None = None) -> None:
        super().__init__(classes='message-card')
        if runtime_message is not None and runtime_message.severity == 'error':
            self.add_class('message-error')
        self.text = text
        self.runtime_message = runtime_message

    def compose(self) -> ComposeResult:
        yield Static(self.text, markup=False, classes='message-text')
        if self.runtime_message is not None:
            with Vertical(classes='message-actions'):
                yield Button('清除', classes='message-clear', compact=True)

    @on(events.Click)
    def open_context_menu(self, event: events.Click) -> None:
        if event.button == RIGHT_MOUSE_BUTTON:
            event.stop()
            screen_x = event.x if event.screen_x is None else event.screen_x
            screen_y = event.y if event.screen_y is None else event.screen_y
            self.app.push_screen(MessageActionScreen(self.text, screen_x, screen_y))

    @on(Button.Pressed, '.message-clear')
    def clear_message(self, event: Button.Pressed) -> None:
        event.stop()
        self.post_message(self.ClearRequested(self))

    class ClearRequested(Message):
        def __init__(self, card: MessageCard) -> None:
            super().__init__()
            self.card = card


class MessagesScreen(ModalScreen[None]):
    """Show loading warnings and live runtime messages in separate tabs."""

    CSS_PATH = 'styles/messages.tcss'
    BINDINGS: ClassVar = [('escape', 'dismiss', '关闭')]

    def __init__(
        self,
        loading_warnings: tuple[str, ...],
        runtime_messages: tuple[RuntimeMessage, ...],
        clear_runtime_message: Callable[[RuntimeMessage], None],
    ) -> None:
        super().__init__()
        self._loading_warnings = loading_warnings
        self._runtime_messages = runtime_messages
        self._clear_runtime_message = clear_runtime_message

    def compose(self) -> ComposeResult:
        with Vertical(id='messages-dialog'):
            yield Static('警告与错误', id='messages-title')
            initial = 'loading-messages' if self._loading_warnings else 'runtime-messages'
            with TabbedContent(initial=initial, id='messages-tabs'):
                with (
                    TabPane('Mod 加载', id='loading-messages'),
                    VerticalScroll(id='loading-message-list'),
                ):
                    for warning in self._loading_warnings:
                        yield MessageCard(warning)
                    if not self._loading_warnings:
                        yield Static('暂无 Mod 加载警告。', classes='message-empty')
                with (
                    TabPane('运行时', id='runtime-messages'),
                    VerticalScroll(id='runtime-message-list'),
                ):
                    for message in self._runtime_messages:
                        yield MessageCard(message.text, runtime_message=message)
                    yield Static('当前会话暂无运行时警告或错误。', id='runtime-messages-empty')
            with Horizontal(id='messages-actions'):
                yield Button('关闭', id='messages-close', variant='primary')

    def on_mount(self) -> None:
        self._refresh_empty_state()

    def _refresh_empty_state(self) -> None:
        container = self.query_one('#runtime-message-list', VerticalScroll)
        self.query_one('#runtime-messages-empty').display = not bool(container.query(MessageCard))

    async def add_runtime_message(self, message: RuntimeMessage) -> None:
        container = self.query_one('#runtime-message-list', VerticalScroll)
        await container.mount(MessageCard(message.text, runtime_message=message))
        self._refresh_empty_state()

    @on(MessageCard.ClearRequested)
    async def clear_runtime_message(self, event: MessageCard.ClearRequested) -> None:
        event.stop()
        card = event.card
        if card.runtime_message is not None:
            self._clear_runtime_message(card.runtime_message)
            await card.remove()
            self._refresh_empty_state()

    @on(Button.Pressed, '#messages-close')
    def close(self) -> None:
        self.dismiss(None)


class DiagnosticApp[ReturnType](App[ReturnType]):
    """Retain runtime diagnostics and expose a shared message window."""

    BINDINGS: ClassVar = [('f2', 'show_messages', '警告与错误')]

    @cached_property
    def _runtime_messages(self) -> list[RuntimeMessage]:
        return []

    @property
    def loading_warnings(self) -> tuple[str, ...]:
        return ()

    @property
    def diagnostic_count(self) -> int:
        return len(self.loading_warnings) + len(self._runtime_messages)

    def notify(
        self,
        message: str,
        *,
        title: str = '',
        severity: SeverityLevel = 'information',
        timeout: float | None = None,
        markup: bool = True,
    ) -> None:
        if severity in ('warning', 'error'):
            self.post_message(DiagnosticAdded(RuntimeMessage(message, title, severity)))
        super().notify(message, title=title, severity=severity, timeout=timeout, markup=markup)

    async def on_diagnostic_added(self, event: DiagnosticAdded) -> None:
        self._runtime_messages.append(event.diagnostic)
        self.diagnostics_changed()
        for screen in self.screen_stack:
            if isinstance(screen, MessagesScreen):
                await screen.add_runtime_message(event.diagnostic)

    def _clear_runtime_message(self, message: RuntimeMessage) -> None:
        self._runtime_messages.remove(message)
        self.diagnostics_changed()

    def diagnostics_changed(self) -> None:
        """Refresh any application-specific diagnostic count indicator."""

    def action_show_messages(self) -> None:
        if not isinstance(self.screen, MessagesScreen):
            self.push_screen(
                MessagesScreen(
                    self.loading_warnings,
                    tuple(self._runtime_messages),
                    self._clear_runtime_message,
                )
            )
