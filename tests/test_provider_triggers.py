"""Shared provider URL and bot-sender trigger contracts."""

import pytest
from dishka import AsyncContainer

from hovorunv2.domain.enums import CommandName
from tests.conftest import create_private_media_message, get_media_command


@pytest.mark.parametrize(
    ("provider", "text", "expected"),
    [
        pytest.param(
            CommandName.TIKTOK, "https://www.tiktok.com/@fueltothe_max/video/7397394517311212818", True, id="tiktok-0"
        ),
        pytest.param(
            CommandName.TIKTOK, "Check this: https://vm.tiktok.com/ZMrBy9vGj/ cool right?", True, id="tiktok-1"
        ),
        pytest.param(CommandName.TIKTOK, "https://tiktok.com/@user/video/1234567890", True, id="tiktok-2"),
        pytest.param(CommandName.TIKTOK, "https://vt.tiktok.com/ZS9fuFD3q/", True, id="tiktok-3"),
        pytest.param(CommandName.TIKTOK, "https://www.tiktok.com/t/ZS9fuFD3q/", True, id="tiktok-4"),
        pytest.param(CommandName.TIKTOK, "Just a random text", False, id="tiktok-5"),
        pytest.param(CommandName.TIKTOK, "", False, id="tiktok-6"),
        pytest.param(CommandName.TIKTOK, None, False, id="tiktok-7"),
        pytest.param(
            CommandName.TWITTER, "Check this post: https://twitter.com/elonmusk/status/1234567890", True, id="twitter-8"
        ),
        pytest.param(
            CommandName.TWITTER, "Check this post: https://x.com/elonmusk/status/1234567890", True, id="twitter-9"
        ),
        pytest.param(CommandName.TWITTER, "No link here", False, id="twitter-10"),
        pytest.param(CommandName.TWITTER, "", False, id="twitter-11"),
        pytest.param(CommandName.TWITTER, None, False, id="twitter-12"),
        pytest.param(CommandName.THREADS, "https://www.threads.net/@zuck/post/CuW6-7Ky5jG", True, id="threads-13"),
        pytest.param(CommandName.THREADS, "https://www.threads.com/@zuck/post/CuW6-7Ky5jG", True, id="threads-14"),
        pytest.param(CommandName.THREADS, "https://threads.net/@user/post/123456", True, id="threads-15"),
        pytest.param(CommandName.THREADS, "https://threads.com/@user/post/123456", True, id="threads-16"),
        pytest.param(CommandName.THREADS, "http://threads.net/t/C_xyz123", True, id="threads-17"),
        pytest.param(CommandName.THREADS, "http://threads.com/t/C_xyz123", True, id="threads-18"),
        pytest.param(CommandName.THREADS, "https://www.threads.net/t/CuW6-7Ky5jG", True, id="threads-19"),
        pytest.param(CommandName.THREADS, "https://www.threads.com/t/CuW6-7Ky5jG", True, id="threads-20"),
        pytest.param(CommandName.THREADS, "No link here", False, id="threads-21"),
        pytest.param(CommandName.THREADS, "Invalid link: https://threads.net/user/12345", False, id="threads-22"),
        pytest.param(CommandName.THREADS, "", False, id="threads-23"),
        pytest.param(CommandName.THREADS, None, False, id="threads-24"),
        pytest.param(
            CommandName.BLUESKY, "https://bsky.app/profile/danabra.mov/post/3lj6v5szvj22a", True, id="bluesky-25"
        ),
        pytest.param(
            CommandName.BLUESKY,
            "Check this: https://www.bsky.app/profile/user.bsky.social/post/rkey123",
            True,
            id="bluesky-26",
        ),
        pytest.param(CommandName.BLUESKY, "No link here", False, id="bluesky-27"),
        pytest.param(CommandName.BLUESKY, "", False, id="bluesky-28"),
        pytest.param(CommandName.BLUESKY, None, False, id="bluesky-29"),
        pytest.param(CommandName.FACEBOOK, "https://www.facebook.com/user/posts/12345", True, id="facebook-30"),
        pytest.param(CommandName.FACEBOOK, "https://www.facebook.com/groups/id/posts/12345", True, id="facebook-31"),
        pytest.param(
            CommandName.FACEBOOK, "https://www.facebook.com/permalink.php?story_fbid=123", True, id="facebook-32"
        ),
        pytest.param(CommandName.FACEBOOK, "https://www.facebook.com/photo.php?fbid=123", True, id="facebook-33"),
        pytest.param(CommandName.FACEBOOK, "https://www.facebook.com/reel/123", True, id="facebook-34"),
        pytest.param(CommandName.FACEBOOK, "https://www.facebook.com/watch/?v=123", True, id="facebook-35"),
        pytest.param(CommandName.FACEBOOK, "https://www.facebook.com/share/p/1FuWE1yAik/", True, id="facebook-36"),
        pytest.param(CommandName.FACEBOOK, "No link here", False, id="facebook-37"),
        pytest.param(CommandName.FACEBOOK, "", False, id="facebook-38"),
        pytest.param(CommandName.FACEBOOK, None, False, id="facebook-39"),
        pytest.param(
            CommandName.INSTAGRAM, "Check this post: https://www.instagram.com/p/C-2v0r0nQ1A/", True, id="instagram-40"
        ),
        pytest.param(
            CommandName.INSTAGRAM,
            "Check this reel: https://www.instagram.com/reels/C-2v0r0nQ1A/",
            True,
            id="instagram-41",
        ),
        pytest.param(
            CommandName.INSTAGRAM, "Check this tv: https://www.instagram.com/tv/C-2v0r0nQ1A/", True, id="instagram-42"
        ),
        pytest.param(CommandName.INSTAGRAM, "No link here", False, id="instagram-43"),
        pytest.param(CommandName.INSTAGRAM, "Invalid link: https://instagram.com/user/", False, id="instagram-44"),
        pytest.param(CommandName.INSTAGRAM, "", False, id="instagram-45"),
        pytest.param(CommandName.INSTAGRAM, None, False, id="instagram-46"),
        pytest.param(
            CommandName.REDDIT, "https://www.reddit.com/r/python/comments/1abcde/cool_post_title/", True, id="reddit-47"
        ),
        pytest.param(
            CommandName.REDDIT, "https://old.reddit.com/r/linux/comments/1abcde/some_post", True, id="reddit-48"
        ),
        pytest.param(CommandName.REDDIT, "https://reddit.com/r/memes/s/AbCdEfGh12", True, id="reddit-49"),
        pytest.param(CommandName.REDDIT, "https://redd.it/1abcde", True, id="reddit-50"),
        pytest.param(
            CommandName.REDDIT, "https://www.reddit.com/user/spez/comments/1abcde/announcement/", True, id="reddit-51"
        ),
        pytest.param(CommandName.REDDIT, "https://www.reddit.com/r/python/", False, id="reddit-52"),
        pytest.param(CommandName.REDDIT, "No link here", False, id="reddit-53"),
        pytest.param(CommandName.REDDIT, "", False, id="reddit-54"),
        pytest.param(CommandName.REDDIT, None, False, id="reddit-55"),
        pytest.param(CommandName.YOUTUBE, "https://www.youtube.com/shorts/abc-123", True, id="youtube-56"),
        pytest.param(CommandName.YOUTUBE, "https://www.youtube.com/watch?v=dQw4w9WgXcQ", True, id="youtube-57"),
        pytest.param(CommandName.YOUTUBE, "https://youtu.be/dQw4w9WgXcQ", True, id="youtube-58"),
        pytest.param(CommandName.YOUTUBE, "https://www.youtube.com/post/Ugkx1234567890abcdef", True, id="youtube-59"),
        pytest.param(
            CommandName.YOUTUBE,
            "https://www.youtube.com/channel/UC12345/community?lb=Ugkx1234567890",
            True,
            id="youtube-60",
        ),
        pytest.param(
            CommandName.YOUTUBE, "https://www.youtube.com/@channel/community?lb=Ugkx1234567890", True, id="youtube-61"
        ),
        pytest.param(
            CommandName.YOUTUBE, "https://www.youtube.com/@channel/post/Ugkx1234567890abcdef", True, id="youtube-62"
        ),
        pytest.param(CommandName.YOUTUBE, "https://www.youtube.com/@channel", False, id="youtube-63"),
        pytest.param(CommandName.YOUTUBE, "No link here", False, id="youtube-64"),
        pytest.param(CommandName.YOUTUBE, "", False, id="youtube-65"),
        pytest.param(CommandName.YOUTUBE, None, False, id="youtube-66"),
    ],
)
async def test_is_triggered(
    test_container: AsyncContainer, provider: CommandName, text: str | None, expected: bool
) -> None:
    """Preserve every original provider URL case."""
    command = await get_media_command(test_container, provider)
    assert await command.is_triggered(create_private_media_message(text)) == expected


@pytest.mark.parametrize(
    ("provider", "text"),
    [
        pytest.param(
            CommandName.TIKTOK, "https://www.tiktok.com/@fueltothe_max/video/7397394517311212818", id="tiktok"
        ),
        pytest.param(
            CommandName.TWITTER, "Check this post: https://twitter.com/elonmusk/status/1234567890", id="twitter"
        ),
        pytest.param(CommandName.THREADS, "https://www.threads.net/@zuck/post/CuW6-7Ky5jG", id="threads"),
        pytest.param(CommandName.BLUESKY, "https://bsky.app/profile/danabra.mov/post/3lj6v5szvj22a", id="bluesky"),
        pytest.param(CommandName.FACEBOOK, "https://www.facebook.com/user/posts/12345", id="facebook"),
        pytest.param(
            CommandName.INSTAGRAM, "Check this post: https://www.instagram.com/p/C-2v0r0nQ1A/", id="instagram"
        ),
        pytest.param(
            CommandName.REDDIT, "https://www.reddit.com/r/python/comments/1abcde/cool_post_title/", id="reddit"
        ),
        pytest.param(CommandName.YOUTUBE, "https://www.youtube.com/shorts/abc-123", id="youtube"),
    ],
)
async def test_is_triggered_by_bot(test_container: AsyncContainer, provider: CommandName, text: str) -> None:
    """All providers ignore bot senders."""
    command = await get_media_command(test_container, provider)
    assert await command.is_triggered(create_private_media_message(text, is_bot=True)) is False
