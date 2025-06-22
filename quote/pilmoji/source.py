from abc import ABC, abstractmethod
from io import BytesIO
from pathlib import Path
from typing import Any, ClassVar
from urllib.error import HTTPError
from urllib.parse import quote_plus
from urllib.request import Request, urlopen
import time
import ssl
import socket

try:
    import requests

    _has_requests = True
except ImportError:
    requests = None
    _has_requests = False

__all__ = (
    "AppleEmojiSource",
    "BaseSource",
    "DiscordEmojiSourceMixin",
    "EmojiCDNSource",
    "FacebookEmojiSource",
    "GoogleEmojiSource",
    "HTTPBasedSource",
    "RobustGoogleEmojiSource",
    "Twemoji",
    "TwemojiEmojiSource",
    "TwitterEmojiSource",
)


class BaseSource(ABC):
    """The base class for an emoji image source."""

    @abstractmethod
    def get_emoji(self, emoji: str, /) -> BytesIO | None:
        """Retrieves a :class:`io.BytesIO` stream for the image of the given emoji.

        Parameters
        ----------
        emoji: str
            The emoji to retrieve.

        Returns
        -------
        :class:`io.BytesIO`
            A bytes stream of the emoji.
        None
            An image for the emoji could not be found.
        """
        raise NotImplementedError

    @abstractmethod
    def get_discord_emoji(self, id: int, /) -> BytesIO | None:
        """Retrieves a :class:`io.BytesIO` stream for the image of the given Discord emoji.

        Parameters
        ----------
        id: int
            The snowflake ID of the Discord emoji.

        Returns
        -------
        :class:`io.BytesIO`
            A bytes stream of the emoji.
        None
            An image for the emoji could not be found.
        """
        raise NotImplementedError

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}>"


class HTTPBasedSource(BaseSource):
    """Represents an HTTP-based source."""

    REQUEST_KWARGS: ClassVar[dict[str, Any]] = {
        "headers": {"User-Agent": "Mozilla/5.0"}
    }

    def __init__(self) -> None:
        if _has_requests:
            self._requests_session = requests.Session()

    def request(self, url: str, max_retries: int = 3, timeout: int = 10) -> bytes:
        """Makes a GET request to the given URL with retry mechanism.

        If the `requests` library is installed, it will be used.
        If it is not installed, :meth:`urllib.request.urlopen` will be used instead.

        Parameters
        ----------
        url: str
            The URL to request from.
        max_retries: int
            Maximum number of retry attempts. Defaults to 3.
        timeout: int
            Request timeout in seconds. Defaults to 10.

        Returns
        -------
        bytes

        Raises
        ------
        Union[:class:`requests.HTTPError`, :class:`urllib.error.HTTPError`]
            There was an error requesting from the URL.
        """
        last_exception = None

        for attempt in range(max_retries + 1):
            try:
                if _has_requests:
                    kwargs = self.REQUEST_KWARGS.copy()
                    kwargs["timeout"] = timeout
                    with self._requests_session.get(url, **kwargs) as response:
                        if response.ok:
                            return response.content
                        else:
                            response.raise_for_status()
                else:
                    req = Request(url, **self.REQUEST_KWARGS)
                    with urlopen(req, timeout=timeout) as response:
                        return response.read()

            except (
                HTTPError,
                socket.timeout,
                ssl.SSLError,
                ConnectionError,
                OSError,
            ) as e:
                last_exception = e
                if attempt < max_retries:
                    wait_time = (2**attempt) * 0.5
                    time.sleep(wait_time)
                    continue
                else:
                    break
            except Exception as e:
                last_exception = e
                break

        if last_exception:
            raise last_exception
        else:
            raise HTTPError(url, 500, "Unknown error occurred", None, None)

    @abstractmethod
    def get_emoji(self, emoji: str, /) -> BytesIO | None:
        raise NotImplementedError

    @abstractmethod
    def get_discord_emoji(self, id: int, /) -> BytesIO | None:
        raise NotImplementedError


class DiscordEmojiSourceMixin(HTTPBasedSource):
    """A mixin that adds Discord emoji functionality to another source."""

    BASE_DISCORD_EMOJI_URL: ClassVar[str] = "https://cdn.discordapp.com/emojis/"

    @abstractmethod
    def get_emoji(self, emoji: str, /) -> BytesIO | None:
        raise NotImplementedError

    def get_discord_emoji(self, id: int, /) -> BytesIO | None:
        url = self.BASE_DISCORD_EMOJI_URL + str(id) + ".png"

        try:
            return BytesIO(self.request(url, max_retries=2, timeout=5))
        except Exception as e:
            try:
                from zhenxun.services.log import logger

                logger.warning(
                    f"Discord表情符号下载失败: {id} from {url}: {e}", "群聊语录"
                )
            except ImportError:
                print(f"Warning: Failed to download Discord emoji {id} from {url}: {e}")
            return None


class EmojiCDNSource(DiscordEmojiSourceMixin):
    """A base source that fetches emojis from https://emojicdn.elk.sh/."""

    BASE_EMOJI_CDN_URL: ClassVar[str] = "https://emojicdn.elk.sh/"
    STYLE: ClassVar[str] = None
    CACHE_DIR: ClassVar[str] = "emoji_cache"

    def __init__(self, disk_cache=False):
        super().__init__()
        self.disk_cache = disk_cache
        if self.disk_cache:
            self.cache_dir = Path(self.CACHE_DIR)
            self.cache_dir.mkdir(exist_ok=True)

    def get_emoji(self, emoji: str, /) -> BytesIO | None:
        if self.STYLE is None:
            raise TypeError("STYLE class variable unfilled.")

        if self.disk_cache:
            cache_file = self.cache_dir / f"{emoji}_{self.STYLE}.png"

            if cache_file.exists():
                with cache_file.open("rb") as f:
                    return BytesIO(f.read())
            else:
                url = (
                    self.BASE_EMOJI_CDN_URL
                    + quote_plus(emoji)
                    + "?style="
                    + quote_plus(self.STYLE)
                )

                try:
                    data = self.request(url, max_retries=2, timeout=5)
                    with cache_file.open("wb") as f:
                        f.write(data)
                    return BytesIO(data)
                except Exception as e:
                    try:
                        from zhenxun.services.log import logger

                        logger.warning(
                            f"表情符号下载失败: {emoji} from {url}: {e}", "群聊语录"
                        )
                    except ImportError:
                        print(
                            f"Warning: Failed to download emoji {emoji} from {url}: {e}"
                        )
                    return None
        else:
            url = (
                self.BASE_EMOJI_CDN_URL
                + quote_plus(emoji)
                + "?style="
                + quote_plus(self.STYLE)
            )

            try:
                return BytesIO(self.request(url, max_retries=2, timeout=5))
            except Exception as e:
                try:
                    from zhenxun.services.log import logger

                    logger.warning(
                        f"表情符号下载失败: {emoji} from {url}: {e}", "群聊语录"
                    )
                except ImportError:
                    print(f"Warning: Failed to download emoji {emoji} from {url}: {e}")
                return None


class TwitterEmojiSource(EmojiCDNSource):
    """A source that uses Twitter-style emojis. These are also the ones used in Discord."""

    STYLE = "twitter"


class AppleEmojiSource(EmojiCDNSource):
    """A source that uses Apple emojis."""

    STYLE = "apple"


class GoogleEmojiSource(EmojiCDNSource):
    """A source that uses Google emojis."""

    STYLE = "google"


class RobustGoogleEmojiSource(GoogleEmojiSource):
    """A robust Google emoji source with fallback strategies."""

    def __init__(self, disk_cache=True, enable_fallback=True):
        """
        初始化健壮的Google表情符号源

        Parameters
        ----------
        disk_cache: bool
            是否启用磁盘缓存，默认为True
        enable_fallback: bool
            是否启用降级策略，默认为True
        """
        super().__init__(disk_cache=disk_cache)
        self.enable_fallback = enable_fallback
        self._failed_emojis = set()

    def get_emoji(self, emoji: str, /) -> BytesIO | None:
        if emoji in self._failed_emojis:
            return None

        result = super().get_emoji(emoji)

        if result is None:
            self._failed_emojis.add(emoji)

        return result


class FacebookEmojiSource(EmojiCDNSource):
    """A source that uses Facebook emojis."""

    STYLE = "facebook"


TwemojiEmojiSource = Twemoji = TwitterEmojiSource
