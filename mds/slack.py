from __future__ import annotations

import json
import logging
import math
import random
import shlex
import threading
import time
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from slack_bolt import App
from slack_sdk.errors import SlackApiError
from typing import Any

from .config import Settings
from .fetch import download_memes, fetch_meme_candidates, source_extension
from .llm import LLMConfig
from .models import DownloadedMeme, Meme
from .moderate import moderate_memes

logger = logging.getLogger(__name__)

# meme command limits and posting comments
MAX_COMMAND_MEMES = 10
DEFAULT_COMMAND_MEMES = 10
COMMAND_USAGE = "Usage: /memes [count 1-10] [home|hot|random|search <topic>]"
CHANNEL_INVITE_MESSAGE = (
    "I'm not in this channel yet (╯°□°）╯︵ ┻━┻! Add MDS using `/invite @MDS`, "
    "then run `/memes` again."
)
COMMAND_FAILURE_MESSAGE = (
    "Something happened and the memes could not be posted. "
    "How unfortunate. (╯°□°）╯︵ ┻━┻"
)
DAILY_COMMENTS = (
    "*Daily memes for you! Yes YOU!*",
    "*Memes, memes, with a side of memes.*",
    "*Fresh memes for the day!*",
    "*Meme(s) of the day below:*",
    "*Waiter, waiter, more memes please!*",
    "*Your daily serving of the internet has arrived.*",
    "*These memes are better than doomscrolling, for sure.*",
    "*Today's forecast: Cloudy with a 100% chance of memes.*",
    "*Your regularly scheduled meme programming:*",
    "*Like takeout, but for memes.*",
    "*Kind of like a service that delivers memes... hmm... (say that again)*",
    "*But like who doesn't like memes?*",
    "*[insert comment about memes here]*",
    "*Go code some project after you enjoy these memes!*",
    "*And now, for a segue — to ~our sponsor~ these memes!* _(iykyk)_",
    "*You know what they say, a meme a day keeps the boredom away!*",
    "*Breaking news: the memes are here. More at 11.*",
    "*Works on my machine. The memes, I mean. Here they are:*",
    "*Eat, sleep, meme.*",
    "*Here are some memes to check out while you are procrastinating. For the fifth time.*",
)
COMMAND_COMMENTS = (
    "*Here are the memes <@%s> asked for:*",
    "*<@%s> summoned the memes. Behold.*",
    "*<@%s>'s requested meme delivery has arrived! _heh_*",
    "*Special delivery for <@%s> (and everyone else in this channel):*",
    "*Your scheduled programming has been interrupted by <@%s> and memes.*",
    "*<@%s> really likes memes, so much that they requested some for this channel!*",
    "*[insert comment about memes here]* _(requested by <@%s>)_",
    "*<@%s> was the memeposter... get it? Like Among Us?...",
    "*And now, for a segue to our sponsor: <@%s>*",
    "*<@%s>'s meme delivery:*",
    "*<@%s> is responsible for these memes.*",
    "*<@%s> woke up and chose memes.*",
    "*<@%s> just deployed memes to prod. On a FRIDAY.*",
    "*Fetching memes for <@%s>... 200 OK.*",
)


@dataclass(frozen=True, slots=True)
class UploadReference:
    """Identifiers used to link a source button back to its upload message."""

    file_id: str | None
    message_ts: str | None


def parse_meme_command(text: str) -> tuple[int, str, str | None]:
    """Parse optional count and ProgrammerHumor source arguments."""
    # parse the command text and keep quoted search topics together
    try:
        arguments = shlex.split(text or "")
    except ValueError as error:
        raise ValueError(f"{error}. {COMMAND_USAGE}") from error

    # get the requested meme count and check if it is within the limit
    count = DEFAULT_COMMAND_MEMES
    if arguments and arguments[0].isdigit():
        count = int(arguments.pop(0))
    if not 1 <= count <= MAX_COMMAND_MEMES:
        raise ValueError(f"Choose between 1 and {MAX_COMMAND_MEMES} memes. {COMMAND_USAGE}")

    # use the home feed if no source was provided
    source = arguments.pop(0).lower() if arguments else "home"
    if source in {"query", "search"}:
        # get the search topic and check if it is empty
        query = " ".join(arguments).strip()
        if not query:
            raise ValueError(f"A search topic is required. {COMMAND_USAGE}")
        return count, source, query
    # check if the source and remaining arguments are valid
    if source not in {"home", "hot", "random"} or arguments:
        raise ValueError(COMMAND_USAGE)
    return count, source, None


def _format_wait(seconds: float) -> str:
    """Format a cooldown duration in seconds or minutes"""
    # return waits under one minute as seconds, otherwise return minutes
    if seconds < 60:
        count = max(1, math.ceil(seconds))
        unit = "second" if count == 1 else "seconds"
        return f"{count} {unit}"
    count = max(1, math.ceil(seconds / 60))
    unit = "minute" if count == 1 else "minutes"
    return f"{count} {unit}"


@dataclass(frozen=True, slots=True)
class CooldownReservation:
    """Identify a request so cancellation is not able to clear a newer cooldown."""

    channel: str
    user: str | None
    requested_at: float


class CooldownLimiter:
    """Reserve slash-command slots independently per channel and user."""

    def __init__(
            self,
            channel_cooldown_seconds: int,
            user_cooldown_seconds: int,
            *,
            channel_cooldown_exempt_ids: Collection[str] = (),
            clock: Any = time.monotonic,
    ) -> None:
        # store the cooldown settings and request times
        self.channel_cooldown_seconds = channel_cooldown_seconds
        self.user_cooldown_seconds = user_cooldown_seconds
        self.channel_cooldown_exempt_ids = frozenset(channel_cooldown_exempt_ids)
        self._clock = clock
        self._lock = threading.Lock()
        self._channel_times: dict[str, CooldownReservation] = {}
        self._user_times: dict[str, CooldownReservation] = {}
        self._next_cleanup = 0.0

    def reserve(self, channel: str, user: str | None) -> CooldownReservation | str:
        """Reserve a request, or return the same user-facing cooldown message."""
        channel_cooldown_enabled = (
                self.channel_cooldown_seconds > 0 and channel not in self.channel_cooldown_exempt_ids
        )
        now = self._clock()
        with self._lock:
            # remove expired request times once per minute
            if now >= self._next_cleanup:
                self._prune(now)
                self._next_cleanup = now + 60.0

            # check the channel cooldown unless this channel is exempt
            channel_requested_at = self._channel_times.get(channel) if channel_cooldown_enabled else None
            channel_remaining = (
                self.channel_cooldown_seconds - (now - channel_requested_at.requested_at)
                if channel_requested_at is not None
                else 0.0
            )
            if channel_remaining > 0:
                return (
                    "This channel requested memes recently. (ㆆ _ ㆆ) "
                    f"Try again in about {_format_wait(channel_remaining)}."
                )

            # check if the user is still on cooldown in any channel
            user_requested_at = self._user_times.get(user) if user else None
            user_remaining = (
                self.user_cooldown_seconds - (now - user_requested_at.requested_at)
                if user_requested_at is not None
                else 0.0
            )
            if user_remaining > 0:
                return (
                    "You requested memes recently. (ㆆ _ ㆆ) "
                    f"Try again in about {_format_wait(user_remaining)}."
                )

            # save the accepted request time for the channel and user
            reservation = CooldownReservation(channel, user, now)
            if channel_cooldown_enabled:
                self._channel_times[channel] = reservation
            if user and self.user_cooldown_seconds > 0:
                self._user_times[user] = reservation
        return reservation

    def release(self, reservation: CooldownReservation) -> None:
        """Release only cooldowns still belonging to a blocked request."""
        with self._lock:
            if self._channel_times.get(reservation.channel) is reservation:
                del self._channel_times[reservation.channel]
            if reservation.user and self._user_times.get(reservation.user) is reservation:
                del self._user_times[reservation.user]

    def _prune(self, now: float) -> None:
        """Remove expired channel and user request times"""
        channel_cutoff = now - self.channel_cooldown_seconds
        user_cutoff = now - self.user_cooldown_seconds
        if self.channel_cooldown_seconds > 0:
            self._channel_times = {
                key: value
                for key, value in self._channel_times.items()
                if value.requested_at > channel_cutoff
            }
        else:
            self._channel_times.clear()
        if self.user_cooldown_seconds > 0:
            self._user_times = {
                key: value
                for key, value in self._user_times.items()
                if value.requested_at > user_cutoff
            }
        else:
            self._user_times.clear()


class SlackPost:
    """Coordinate meme fetching, moderation, downloading, and Slack publishing."""

    def __init__(self, client: Any, settings: Settings, llm_config: LLMConfig | None) -> None:
        self.client = client
        self.settings = settings
        self.llm_config = llm_config
        # store a separate posting lock for each channel
        self._channel_locks: dict[str, threading.Lock] = {}
        self._channel_locks_guard = threading.Lock()

    def _lock_for_channel(self, channel: str) -> threading.Lock:
        """Return the posting lock for a channel"""
        # create a channel lock if one does not exist and return it
        with self._channel_locks_guard:
            return self._channel_locks.setdefault(channel, threading.Lock())

    def fetch_candidates(
            self,
            meme_extensions: Sequence[str],
            fetch_limit: int,
    ) -> list[Meme]:
        """Fetch ranked feeds, interleave them, and deduplicate source URLs."""
        # fetch meme candidates from the requested sources
        return fetch_meme_candidates(meme_extensions, fetch_limit)

    def apply_moderation(
            self,
            candidates: Sequence[Meme],
            *,
            enabled: bool,
    ) -> list[Meme]:
        """Moderate the full candidate pool when enabled and preserve fail-open behavior."""
        outcome = moderate_memes(candidates, self.llm_config if enabled else None)
        result = outcome.result
        # get the approved memes in their original order
        approved = set(result.approved_indices)
        selected = [meme for index, meme in enumerate(candidates) if index in approved]
        return selected

    def download_selected(self, memes: Sequence[Meme]) -> list[DownloadedMeme]:
        """Download the selected memes for upload"""
        # download the selected meme images
        return download_memes(memes)

    def publish_uploads(
            self,
            channel: str,
            downloaded: Sequence[DownloadedMeme],
            comments: Sequence[str],
            requested_by: str | None,
    ) -> UploadReference:
        """Upload memes and return their Slack identifiers"""
        logger.info("Uploading memes to Slack channel %s...", channel)
        # choose a random comment and add the requester if needed
        initial_comment = random.choice(comments)
        if "%s" in initial_comment:
            initial_comment %= requested_by if requested_by else "the requester"
        # build the Slack file upload list
        file_uploads = [
            {
                "file": meme.content,
                "filename": meme.filename,
                "title": meme.meme.title,
                "alt_txt": meme.meme.title,
            }
            for meme in downloaded
        ]
        # upload all meme images in one Slack message
        upload_response = self.client.files_upload_v2(
            channel=channel,
            file_uploads=file_uploads,
            initial_comment=initial_comment,
        )
        logger.info("Uploaded %d memes to Slack", len(file_uploads))
        # return the file ID and message timestamp for the source button
        return UploadReference(
            file_id=self._first_upload_file_id(upload_response),
            message_ts=self._upload_message_ts(upload_response, channel),
        )

    @staticmethod
    def _first_upload_file_id(upload_response: Any) -> str | None:
        """Return the first file ID from a Slack upload response"""
        # get the uploaded files and check if the response is valid
        uploaded_files = upload_response.get("files", [])
        if not isinstance(uploaded_files, list):
            return None
        return next(
            (
                uploaded_file.get("id")
                for uploaded_file in uploaded_files
                if isinstance(uploaded_file, dict)
                   and isinstance(uploaded_file.get("id"), str)
            ),
            None,
        )

    def _upload_message_ts(self, upload_response: Any, channel: str) -> str | None:
        """Find the timestamp of the message created by a file upload."""
        uploaded_files = upload_response.get("files", [])
        if not isinstance(uploaded_files, list):
            uploaded_files = []

        # check the upload response for the message timestamp
        message_ts = self._message_ts_from_files(uploaded_files, channel)
        if message_ts is not None:
            return message_ts

        # files.completeUploadExternal commonly returns only IDs and titles.
        # Fetch one complete file object so its channel share identifies the
        # single message containing the whole multi-file upload.
        first_file_id = self._first_upload_file_id(upload_response)
        if first_file_id is None:
            logger.warning("Slack's upload response did not contain a file ID")
            return None

        # get the full file information if the upload response only has an ID
        message_ts = self._message_ts_for_file(first_file_id, channel)
        if message_ts is None:
            logger.info(
                "Slack file %s share timestamp was not ready for channel %s (its fine chill)",
                first_file_id,
                channel,
            )
        return message_ts

    def _message_ts_for_file(self, file_id: str, channel: str) -> str | None:
        """Retrieve a file's current share timestamp from Slack."""
        # get the full file information from Slack
        try:
            file_response = self.client.files_info(file=file_id)
        except Exception as error:
            logger.exception(
                "Could not retrieve Slack file %s to locate its upload message; "
                "make sure the bot has the files:read scope: %s",
                file_id,
                error,
            )
            return None

        # get the message timestamp from the returned file
        uploaded_file = file_response.get("file")
        return self._message_ts_from_files([uploaded_file], channel)

    @staticmethod
    def _message_ts_from_files(files: Sequence[Any], channel: str) -> str | None:
        """Extract a channel share timestamp from complete Slack file objects."""
        for uploaded_file in files:
            # check if the file and share information are valid dictionaries
            if not isinstance(uploaded_file, dict):
                continue
            shares = uploaded_file.get("shares", {})
            if not isinstance(shares, dict):
                continue
            for visibility in ("private", "public"):
                # check both private and public channel shares
                visible_shares = shares.get(visibility, {})
                if not isinstance(visible_shares, dict):
                    continue
                channel_shares = visible_shares.get(channel, [])
                if not isinstance(channel_shares, list):
                    continue
                for share in channel_shares:
                    # return the first valid timestamp for the channel
                    if isinstance(share, dict) and isinstance(share.get("ts"), str):
                        return share["ts"]
        return None

    def post_sources(
            self,
            channel: str,
            downloaded: Sequence[DownloadedMeme],
            upload_reference: UploadReference,
    ) -> None:
        """Post a button that shows the meme sources"""
        # create the button data with the source URLs and upload identifiers
        button_value: dict[str, Any] = {
            "source_urls": [meme.meme.source_url for meme in downloaded],
        }
        if upload_reference.message_ts is not None:
            button_value["origin_message_ts"] = upload_reference.message_ts
        if upload_reference.file_id is not None:
            button_value["origin_file_id"] = upload_reference.file_id
        # post the source button and log any errors
        try:
            self.client.chat_postMessage(
                channel=channel,
                text="Meme sources",
                blocks=[
                    {
                        "type": "actions",
                        "elements": [
                            {
                                "type": "button",
                                "text": {"type": "plain_text", "text": "Show sources"},
                                "action_id": "show_sources",
                                "value": json.dumps(button_value, separators=(",", ":")),
                            }
                        ],
                    }
                ],
                unfurl_links=False,
                unfurl_media=False,
            )
        except Exception as error:
            logger.exception(
                "Could not post the meme source-button message to Slack channel %s: %s",
                channel,
                error,
            )

    def _post_pipeline(
            self,
            *,
            channel: str,
            fetch_limit: int,
            post_limit: int,
            meme_extensions: Sequence[str],
            comments: Sequence[str],
            moderation_enabled: bool,
            requested_by: str | None = None,
    ) -> int:
        """Run the meme posting pipeline"""
        # fetch meme candidates and return if none were found
        candidates = self.fetch_candidates(meme_extensions, fetch_limit)
        if not candidates:
            logger.warning("No memes were found")
            return 0
        # moderate the candidates and return if none were approved
        approved = self.apply_moderation(candidates, enabled=moderation_enabled)
        if not approved:
            logger.warning("No memes were approved for posting")
            return 0
        # select the requested number of approved memes
        selected = approved[:post_limit]
        logger.info("Selected %d meme candidates for posting", len(selected))
        # download the selected memes and return if all downloads failed
        downloaded = self.download_selected(selected)
        if not downloaded:
            logger.warning("No meme images could be downloaded")
            return 0
        # upload the memes and post their source button
        upload_reference = self.publish_uploads(channel, downloaded, comments, requested_by)
        self.post_sources(channel, downloaded, upload_reference)
        logger.info("Done!")
        return len(downloaded)

    def _post_configured_channel(self, *, moderation_enabled: bool) -> int:
        """Post memes to the configured Slack channel"""
        channel = self.settings.slack_channel_id
        # lock the channel while posting the configured meme batch
        with self._lock_for_channel(channel):
            return self._post_pipeline(
                channel=channel,
                fetch_limit=25,
                post_limit=10,
                # fetch up to 25 memes by interleaving the home and hot feeds
                meme_extensions=("/", "/hot"),
                comments=DAILY_COMMENTS,
                moderation_enabled=moderation_enabled,
            )

    def post_scheduled(self) -> int:
        """Post the configured daily batch while holding only its channel lock."""
        # check the daily post moderation setting
        return self._post_configured_channel(
            moderation_enabled=(
                    self.settings.llm_enabled
                    and self.settings.llm_daily_post_moderation_enabled
            )
        )

    def post_manual(self) -> int:
        """Post a manual batch with the global moderation setting."""
        # use the global LLM moderation setting for manual posts
        return self._post_configured_channel(moderation_enabled=self.settings.llm_enabled)

    def post_command(
            self,
            channel: str,
            count: int,
            extension: str,
            requested_by: str | None,
    ) -> int:
        """Post a slash-command batch while holding only the requested channel lock."""
        # lock the requested channel and run the command posting pipeline
        with self._lock_for_channel(channel):
            return self._post_pipeline(
                channel=channel,
                fetch_limit=count,
                post_limit=count,
                meme_extensions=(extension,),
                comments=COMMAND_COMMENTS,
                moderation_enabled=(
                        self.settings.llm_enabled
                        and self.settings.llm_memes_command_moderation_enabled
                ),
                requested_by=requested_by,
            )


def create_slack_app(
        settings: Settings,
        llm_config: LLMConfig | None,
) -> tuple[App, SlackPost]:
    """Assemble Slack handlers and their explicit posting dependencies."""
    # create the Slack app, poster, and command cooldown limiter
    slack_app = App(token=settings.slack_bot_token)
    poster = SlackPost(slack_app.client, settings, llm_config)
    limiter = CooldownLimiter(
        settings.meme_command_channel_cooldown_seconds,
        settings.meme_command_user_cooldown_seconds,
        channel_cooldown_exempt_ids=settings.meme_command_channel_cooldown_exempt_ids,
    )

    @slack_app.command("/memes")
    def meme_command(ack, command, respond) -> None:
        """Handle the /memes command"""
        # parse the command arguments and return any validation errors
        try:
            count, source, query = parse_meme_command(command.get("text", ""))
            extension = source_extension(source, query)
        except ValueError as error:
            ack(text=str(error), response_type="ephemeral")
            return

        channel = command["channel_id"]
        user = command.get("user_id")
        # check the channel and user cooldowns
        reservation = limiter.reserve(channel, user)
        if isinstance(reservation, str):
            ack(text=reservation, response_type="ephemeral")
            return

        source_description = source
        if query:
            source_description = f"{source} for {query!r}"
        # acknowledge the command before fetching memes
        ack(
            text=(
                f"Fetching up to {count} {source_description} meme(s) "
                "from ProgrammerHumor..."
            ),
            response_type="ephemeral",
        )

        def reply(text: str) -> None:
            """Reply even when the bot cannot post directly to this channel"""
            try:
                response = respond(text=text, response_type="ephemeral")
                if response.status_code != 200:
                    logger.error(
                        "Could not send /memes response in channel %s (HTTP %s)",
                        channel,
                        response.status_code,
                    )
            except Exception as error:
                # Response URLs are credentials; keep them out of error logs.
                logger.error(
                    "Could not send /memes response in channel %s (%s)",
                    channel,
                    type(error).__name__,
                )

        try:
            # post the requested memes
            posted_count = poster.post_command(channel, count, extension, user)
        except Exception as error:
            if isinstance(error, SlackApiError) and error.response.get("error") in {
                "not_in_channel", "channel_not_found",
            }:
                # let the user invite MDS and retry without waiting for a cooldown
                limiter.release(reservation)
                logger.warning(
                    "Meme request blocked by channel access in %s: %s",
                    channel,
                    error.response.get("error"),
                )
                reply(CHANNEL_INVITE_MESSAGE)
            else:
                logger.exception(
                    "Meme slash command failed in channel %s: %s",
                    channel,
                    error,
                )
                reply(COMMAND_FAILURE_MESSAGE)
            return
        # notify the user if no memes were available to post
        if not posted_count:
            reply(
                "No memes were available to post for that source. "
                "How unfortunate. (╯°□°）╯︵ ┻━┻"
            )

    @slack_app.action("show_sources")
    def show_sources(ack, body, client) -> None:
        """Handle source button clicks"""
        # acknowledge the button click and get its saved data
        ack()
        channel = body["container"]["channel_id"]
        source_value = body["actions"][0].get("value", "[]")
        user = body["user"]["id"]
        logger.info("Source-button click from user %s in channel %s", user, channel)
        # parse the saved source and upload data
        try:
            saved_data = json.loads(source_value)
        except (TypeError, json.JSONDecodeError):
            logger.warning("Source-button click had invalid saved data")
            saved_data = []

        # Existing source buttons stored only the URL list. New buttons retain
        # the upload identifiers used to build its permalink immediately or
        # retry after Slack finishes populating the file's share metadata.
        if isinstance(saved_data, list):
            source_urls = saved_data
            origin_message_ts = None
            origin_file_id = None
        elif isinstance(saved_data, dict):
            source_urls = saved_data.get("source_urls", [])
            origin_message_ts = saved_data.get("origin_message_ts")
            origin_file_id = saved_data.get("origin_file_id")
        else:
            source_urls = []
            origin_message_ts = None
            origin_file_id = None

        # check the source URLs and format them as Slack links
        if (
                not isinstance(source_urls, list)
                or not source_urls
                or not all(isinstance(url, str) for url in source_urls)
        ):
            source_urls = []
            source_text = "The source list is no longer available."
        else:
            source_text = "\n".join(
                f"• <{url}|Meme {index}>"
                for index, url in enumerate(source_urls, start=1)
            )

        # get the message timestamp from the file ID if it was not saved
        origin_text = ""
        if (
                (not isinstance(origin_message_ts, str) or not origin_message_ts)
                and isinstance(origin_file_id, str)
                and origin_file_id
        ):
            origin_message_ts = poster._message_ts_for_file(origin_file_id, channel)
            if origin_message_ts is None:
                logger.warning(
                    "Slack file %s still has no share timestamp for channel %s",
                    origin_file_id,
                    channel,
                )
        # get the permalink to the original meme post
        if isinstance(origin_message_ts, str) and origin_message_ts:
            try:
                permalink_response = client.chat_getPermalink(
                    channel=channel,
                    message_ts=origin_message_ts,
                )
                permalink = permalink_response.get("permalink")
                if isinstance(permalink, str) and permalink:
                    origin_text = f"\n\n<{permalink}|Click to view the original post>"
            except Exception as error:
                logger.exception(
                    "Could not retrieve the original meme post permalink in channel %s: %s",
                    channel,
                    str(error),
                )
        # send the source URLs and original post link to the user
        client.chat_postEphemeral(
            channel=channel,
            user=user,
            text="Image sources",
            blocks=[
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Image sources:*\n{source_text}{origin_text}",
                    },
                }
            ],
            unfurl_links=False,
            unfurl_media=False,
        )
        logger.info("Sent %d source link(s) to user %s", len(source_urls), user)

    return slack_app, poster
