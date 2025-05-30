from __future__ import annotations

from enum import Enum
import re
from typing import TYPE_CHECKING, Final, NamedTuple

import emoji
import PIL
from PIL import ImageFont

if TYPE_CHECKING:
    from .core import FontT

language_pack: dict[str, str] = {
    data["en"]: emj
    for emj, data in emoji.EMOJI_DATA.items()
    if "en" in data and data["status"] <= emoji.STATUS["fully_qualified"]
}
_UNICODE_EMOJI_REGEX = "|".join(
    map(re.escape, sorted(language_pack.values(), key=len, reverse=True))
)
_DISCORD_EMOJI_REGEX = "<a?:[a-zA-Z0-9_]{1,32}:[0-9]{17,22}>"

EMOJI_REGEX: Final[re.Pattern[str]] = re.compile(
    f"({_UNICODE_EMOJI_REGEX}|{_DISCORD_EMOJI_REGEX})"
)

__all__ = ("EMOJI_REGEX", "Node", "NodeType", "getsize", "to_nodes")


class NodeType(Enum):
    """|enum|

    Represents the type of a :class:`~.Node`.

    Attributes
    ----------
    text
        This node is a raw text node.
    emoji
        This node is a unicode emoji.
    discord_emoji
        This node is a Discord emoji.
    """

    text = 0
    emoji = 1
    discord_emoji = 2


class Node(NamedTuple):
    """Represents a parsed node inside of a string.

    Attributes
    ----------
    type: :class:`~.NodeType`
        The type of this node.
    content: str
        The contents of this node.
    """

    type: NodeType
    content: str

    def __repr__(self) -> str:
        return f"<Node type={self.type.name!r} content={self.content!r}>"


def _parse_line(line: str, /) -> list[Node]:
    nodes = []

    for i, chunk in enumerate(EMOJI_REGEX.split(line)):
        if not chunk:
            continue

        if not i % 2:
            nodes.append(Node(NodeType.text, chunk))
            continue

        if len(chunk) > 18:
            node = Node(NodeType.discord_emoji, chunk.split(":")[-1][:-1])
        else:
            node = Node(NodeType.emoji, chunk)

        nodes.append(node)

    return nodes


def to_nodes(text: str, /) -> list[list[Node]]:
    """Parses a string of text into :class:`~.Node`s.

    This method will return a nested list, each element of the list
    being a list of :class:`~.Node`s and representing a line in the string.

    The string ``'Hello\nworld'`` would return something similar to
    ``[[Node('Hello')], [Node('world')]]``.

    Parameters
    ----------
    text: str
        The text to parse into nodes.

    Returns
    -------
    List[List[:class:`~.Node`]]
    """
    return [_parse_line(line) for line in text.splitlines()]


def getsize(
    text: str, font: FontT = None, *, spacing: int = 4, emoji_scale_factor: float = 1
) -> tuple[int, int]:
    """Return the width and height of the text when rendered.
    This method supports multiline text.

    Parameters
    ----------
    text: str
        The text to use.
    font
        The font of the text.
    spacing: int
        The spacing between lines, in pixels.
        Defaults to `4`.
    emoji_scale_factor: float
        The rescaling factor for emojis.
        Defaults to `1`.
    """
    if font is None:
        font = ImageFont.load_default()

    x, y = 0, 0
    nodes = to_nodes(text)

    for line in nodes:
        this_x = 0
        for node in line:
            content = node.content

            if node.type is not NodeType.text:
                width = int(emoji_scale_factor * font.size)
            elif tuple(int(part) for part in PIL.__version__.split(".")) >= (9, 2, 0):
                width = int(font.getlength(content))
            else:
                width, _ = font.getsize(content)

            this_x += width

        y += spacing + font.size

        if this_x > x:
            x = this_x

    return x, y - spacing
