import json
import logging
import random
import shlex
import threading

from slack_bolt import App

from .config import LLM_MODEL, SLACK_BOT_TOKEN, SLACK_CHANNEL_ID
from .fetch import download_memes, get_memes, source_extension
from .moderate import moderate_memes

post_lock = threading.Lock()
app = App(token=SLACK_BOT_TOKEN)
logger = logging.getLogger(__name__)

MAX_COMMAND_MEMES = 10
DEFAULT_COMMAND_MEMES = 10
COMMAND_USAGE = "Usage: /memes [count 1-10] [home|hot|random|search <topic>]"
DAILY_COMMENTS = (
    "*Daily memes for you! Yes YOU!*",
    "*Memes, memes, with a side of memes.*",
    "*Fresh memes for the day!*",
    "*Meme(s) of the day below:*",
    "*Waiter, waiter, more memes please!*",
    "*Your daily serving of the internet has arrived*",
    "*A fresh batch of memes just landed*",
    "*These memes are better than doomscrolling, for sure.*",
    "*Today's forecast: Cloudy with a 100% chance of memes.*",
    "*Your regularly scheduled meme programming:*",
    "*Memes, memes, memes, with a side of memes.*",
    "*Like takeout, but for memes.*",
    "*Fresh batch of memes, hot off the internet.*",
    "*Kind of like a service that delivers memes... hmm... (say that again)*",
    "*But like who doesn't like memes?*",
    "*[insert comment about memes here]*",
    "*Go code some project after you enjoy these memes!*",
    "*And now, for a segue — to ~our sponsor~ these memes!* _(iykyk)_",
    "*You know what they say, a meme a day keeps the boredom away!*",
    "*Addicted to memes? Here are some for you!*",
)
COMMAND_COMMENTS = (
    "*Here are the memes <@%s> asked for:*",
    "*A custom batch of memes for <@%s>, coming right up!*",
    "*<@%s>'s requested meme delivery has arrived! _heh_*",
    "*Freshly fetched for <@%s> and this channel!*",
    "*Enjoy these memes, <@%s>!*",
    "*<@%s> really likes memes, so much that they requested some for this channel!*",
    "*<@%s>, the meme connoisseur, requested these memes for you!*",
    "*Give <@%s> a round of applause for requesting these memes!*",
    "*[insert comment about memes here]* _(requested by <@%s>)_",
    "*<@%s> was the memeposter... get it? Like Among Us?..._",
    "*And now, for a segue to our sponsor: <@%s>*",
)


def parse_meme_command(text: str) -> tuple[int, str, str | None]:
    """parse the optional count and ProgrammerHumor source from slash-command input"""
    try:
        arguments = shlex.split(text or "")
    except ValueError as error:
        raise ValueError(f"{error}. {COMMAND_USAGE}") from error

    count = DEFAULT_COMMAND_MEMES
    if arguments and arguments[0].isdigit():
        count = int(arguments.pop(0))

    if not 1 <= count <= MAX_COMMAND_MEMES:
        raise ValueError(f"Choose between 1 and {MAX_COMMAND_MEMES} memes. {COMMAND_USAGE}")

    source = arguments.pop(0).lower() if arguments else "home"
    if source in {"query", "search"}:
        query = " ".join(arguments).strip()
        if not query:
            raise ValueError(f"A search topic is required. {COMMAND_USAGE}")
        return count, source, query

    if source not in {"home", "hot", "random"} or arguments:
        raise ValueError(COMMAND_USAGE)
    return count, source, None


def _post_memes(
        *,
        channel: str,
        fetch_limit: int,
        post_limit: int,
        meme_extensions: tuple[str, ...],
        comments: tuple[str, ...],
        requested_by: str | None = None,
) -> int:
    """fetch, moderate, select, and upload memes to the given Slack channel"""
    logger.info("Fetching memes from ProgrammerHumor...")
    fetched_memes = [
        get_memes(limit=fetch_limit, memes_extension=extension)
        for extension in meme_extensions
    ]
    if len(fetched_memes) == 1:
        memes = fetched_memes[0]
    else:
        # interleave the scheduled post's home and hot rankings
        # first post -> first home, first hot, second post -> second home, second hot, etc.
        # until the fetch limit or meme limit
        # this is good because it ensures that both sources
        # are represented in the final post, and that the most popular memes
        # from each source are prioritized over the less popular ones
        # and also because the home memes are always less popular than the hot memes
        # so you will mostly never get home memes without this ordering
        memes = []
        for index in range(max(map(len, fetched_memes), default=0)):
            for source_memes in fetched_memes:
                if index < len(source_memes):
                    memes.append(source_memes[index])
        memes = memes[:fetch_limit]

    if not memes:
        logger.warning("No memes were found")
        return 0
    logger.info("Scraped %d eligible meme(s) from ProgrammerHumor", len(memes))
    logger.info("Moderating fetched memes w/ model %s...", LLM_MODEL)
    moderation = moderate_memes(memes)
    approved_memes = [
        meme
        for index, meme in enumerate(memes)
        if index in moderation.approved_indices
    ]
    logger.info("Approved %d of %d fetched meme candidates", len(approved_memes), len(memes))
    if not approved_memes:
        logger.warning("No memes were approved for posting")
        return 0

    selected_memes = approved_memes[:post_limit]
    logger.info("Selected %d meme candidates for posting", len(selected_memes))
    logger.info("Downloading selected memes...")
    file_uploads, downloaded_memes = download_memes(selected_memes)
    logger.info("Downloaded %d meme images", len(file_uploads))
    if not file_uploads:
        logger.warning("No meme images could be downloaded")
        return 0

    logger.info("Uploading memes to Slack channel %s...", channel)
    initial_comment = random.choice(comments)
    if "%s" in initial_comment:
        requester = f"{requested_by}" if requested_by else "the requester"
        initial_comment %= requester
    app.client.files_upload_v2(
        channel=channel,
        file_uploads=file_uploads,
        initial_comment=initial_comment,
    )
    logger.info("Uploaded %d memes to Slack", len(file_uploads))

    try:
        app.client.chat_postMessage(
            channel=channel,
            text="Meme sources",
            blocks=[{
                "type": "actions",
                "elements": [{
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "Show sources",
                    },
                    "action_id": "show_sources",
                    "value": json.dumps([meme["url"] for meme in downloaded_memes], separators=(",", ":")),
                }],
            }],
            unfurl_links=False,
            unfurl_media=False,
        )
    except Exception:
        logger.exception("Could not post the meme source-button message to Slack channel %s", channel)
    logger.info("Done!")
    return len(file_uploads)


def post_memes() -> None:
    """fetch, moderate, select, and post the scheduled batch of memes"""
    with post_lock:
        _post_memes(
            channel=SLACK_CHANNEL_ID,
            fetch_limit=25,
            post_limit=10,
            meme_extensions=("/", "/hot"),
            comments=DAILY_COMMENTS,
        )


@app.command("/memes")
def meme_command(ack, command) -> None:
    """fetch & post a number of memes selected by the user into the invoking channel in Slack"""
    try:
        count, source, query = parse_meme_command(command.get("text", ""))
        extension = source_extension(source, query)
    except ValueError as error:
        ack(text=str(error), response_type="ephemeral")
        return

    source_description = source
    if query:
        source_description = f"{source} for {query!r}"
    ack(
        text=f"Fetching up to {count} {source_description} meme(s) from ProgrammerHumor...",
        response_type="ephemeral",
    )

    channel = command["channel_id"]
    user = command.get("user_id")
    with post_lock:
        try:
            posted_count = _post_memes(
                channel=channel,
                fetch_limit=count,
                post_limit=count,
                meme_extensions=(extension,),
                comments=COMMAND_COMMENTS,
                requested_by=user,
            )
        except Exception as e:
            logger.exception("Meme slash command failed in channel %s , %s", channel, str(e))
            if user:
                app.client.chat_postEphemeral(
                    channel=channel,
                    user=user,
                    text="Something happened and the memes could not be posted. How unfortunate. (╯°□°）╯︵ ┻━┻",
                )
            return

    if not posted_count and user:
        app.client.chat_postEphemeral(
            channel=channel,
            user=user,
            text="No memes were available to post for that source. (╯°□°）╯︵ ┻━┻",
        )


@app.action("show_sources")
def show_sources(ack, body, client) -> None:
    """handle the button click to show meme sources"""
    ack()
    # extract the channel and source URLs from the button click payload
    channel = body["container"]["channel_id"]
    source_value = body["actions"][0].get("value", "[]")
    logger.info("Source-button click from user %s in channel %s", body["user"]["id"], channel)
    try:
        source_urls = json.loads(source_value)
    except (TypeError, json.JSONDecodeError):
        logger.warning("Source-button click had invalid saved data")
        source_urls = []

    if not source_urls or not all(isinstance(url, str) for url in source_urls):
        source_text = "The source list is no longer available."
    else:
        source_text = "\n".join(
            f"• <{url}|Meme {index}>"
            for index, url in enumerate(source_urls, start=1)
        )

    client.chat_postEphemeral(
        channel=channel,
        user=body["user"]["id"],
        text="Image sources",
        blocks=[{
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Image sources:*\n{source_text}",
            },
        }],
        unfurl_links=False,
        unfurl_media=False,
    )
    logger.info("Sent %d source link(s) to user %s", len(source_urls), body["user"]["id"])
