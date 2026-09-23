"""Campaign collection list widgets for the map browser."""

from collections.abc import Iterable

from rich.text import Text
from textual import events, on
from textual.message import Message
from textual.widgets import ListItem, ListView, Static

from pist.game import campaigns as game_campaigns

LEFT_MOUSE_BUTTON = 1


class CampaignItem(ListItem):
    """One selectable campaign in the flat campaign list."""

    def __init__(self, campaign: game_campaigns.LoadedCampaign, label: Text) -> None:
        self.campaign = campaign
        super().__init__(Static(label))


class CampaignList(ListView):
    """The flat campaign selector shown beside its details."""

    def __init__(
        self, items: Iterable[CampaignItem], *, id: str, initial_index: int | None = 0
    ) -> None:
        super().__init__(*items, id=id, initial_index=initial_index)


class CampaignListToggle(Static):
    """One non-selecting direction control for the campaign collections."""

    class Clicked(Message):
        """Request switching the campaign collection in one direction."""

        def __init__(self, direction: int) -> None:
            self.direction = direction
            super().__init__()

    def __init__(self, direction: int) -> None:
        self.direction = direction
        super().__init__('◂' if direction < 0 else '▸', classes='campaign-list-toggle')

    @on(events.Click)
    def clicked(self, event: events.Click) -> None:
        event.stop()
        if event.button == LEFT_MOUSE_BUTTON:
            self.post_message(self.Clicked(self.direction))
