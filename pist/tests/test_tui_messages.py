import asyncio

from textual.widgets import Button, TabbedContent

from pist.ui.messages import MessageActionScreen, MessageCard, MessagesScreen
from pist.ui.tui import RefreshableCssApp


def test_runtime_cards_survive_toasts_and_support_individual_copy_and_clear() -> None:
    async def check() -> None:
        app = RefreshableCssApp[None]()
        async with app.run_test(size=(100, 36)) as pilot:
            app.notify('保存失败：[room] 中文错误', severity='error', markup=False, timeout=0.01)
            app.notify('保存失败：[room] 中文错误', severity='error', markup=False, timeout=0.01)
            app.notify('普通提示', timeout=0.01)
            await pilot.pause(0.05)
            await pilot.press('f2')
            screen = app.screen
            assert isinstance(screen, MessagesScreen)
            cards = list(screen.query('#runtime-message-list MessageCard').results(MessageCard))
            assert len(cards) == 2
            assert '[room] 中文错误' in cards[0].text
            await pilot.click(cards[1].query_one('.message-text'), button=3)
            assert isinstance(app.screen, MessageActionScreen)
            await pilot.click('#message-action-copy')
            assert app.clipboard == cards[1].text
            assert app.screen is screen
            await pilot.click(cards[1].query_one('.message-clear', Button))
            assert app.diagnostic_count == 1
            assert list(screen.query(MessageCard)) == [cards[0]]
            app.notify('实时新增', severity='warning', timeout=0.01)
            await pilot.pause()
            cards = list(screen.query(MessageCard).results(MessageCard))
            assert len(cards) == 2
            assert '实时新增' in cards[1].text
            await pilot.press('escape')
            await pilot.press('f2')
            cards = list(app.screen.query(MessageCard).results(MessageCard))
            assert len(cards) == 2
            for card in cards:
                await pilot.click(card.query_one('.message-clear', Button))
            assert app.diagnostic_count == 0
            assert app.screen.query_one('#runtime-messages-empty').display
            await pilot.click('#messages-close')
            assert not isinstance(app.screen, MessagesScreen)

    asyncio.run(check())


def test_loading_warnings_are_copyable_but_not_clearable() -> None:
    class TestApp(RefreshableCssApp[None]):
        @property
        def loading_warnings(self) -> tuple[str, ...]:
            return ('加载错误：[a] 中文\n第二行',)

    async def check() -> None:
        app = TestApp()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.press('f2')
            assert app.screen.query_one(TabbedContent).active == 'loading-messages'
            card = app.screen.query_one(MessageCard)
            assert not card.query('.message-clear')
            await pilot.click(card.query_one('.message-text'), button=3)
            await pilot.press('escape')
            assert isinstance(app.screen, MessagesScreen)
            assert app.clipboard == ''
            text = card.query_one('.message-text')
            await pilot.click(text, offset=(text.size.width - 1, 0), button=3)
            await pilot.pause()
            menu = app.screen.query_one('.context-menu-dialog')
            assert menu.region.right <= app.screen.size.width
            await pilot.click(offset=(0, 0))
            assert isinstance(app.screen, MessagesScreen)
            assert app.clipboard == ''
            await pilot.click(text, button=3)
            await pilot.click('#message-action-copy')
            assert app.clipboard == app.loading_warnings[0]
            await pilot.click('#--content-tab-runtime-messages')
            assert app.screen.query_one('#runtime-messages-empty').display
            await pilot.press('f2')
            assert sum(isinstance(screen, MessagesScreen) for screen in app.screen_stack) == 1

    asyncio.run(check())
