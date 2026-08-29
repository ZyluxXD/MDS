import logging
import random
from slack_bolt import App
from .config import SLACK_BOT_TOKEN, SLACK_CHANNEL_ID
from .fetch import get_memes, view_count, download_memes
import json

app = App(token=SLACK_BOT_TOKEN)
logger = logging.getLogger(__name__)


def post_memes() -> None:
    """fetch memes and post them to the chosen Slack channel"""
    memes = get_memes(limit=10)
    memes.sort(key=lambda meme: view_count(meme["views"]), reverse=True)
    channel = SLACK_CHANNEL_ID

    if not memes:
        logger.warning("No memes were found")
        return

    logger.info("Received %d memes", len(memes))
    file_uploads, memes = download_memes(memes)
    if not file_uploads:
        logger.warning("No meme images could be downloaded")
        return
    # very amazing comments to accompany the memes
    comments = ["*Daily memes for you! Yes YOU!*", "*Memes, Memes, with a side of Memes*",
                "*Fresh memes for the day*", "*Meme(s) of the day below:*", "*Waiter Waiter, more memes please!*"]
    app.client.files_upload_v2(
        channel=channel,
        file_uploads=file_uploads,
        initial_comment=random.choice(comments),
    )
    logger.info("Uploaded %d memes to Slack", len(file_uploads))

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
                "value": json.dumps([meme["url"] for meme in memes], separators=(",", ":")),
            }],
        }],
        unfurl_links=False,
        unfurl_media=False,
    )


@app.action("show_sources")
def show_sources(ack, body, client) -> None:
    """handle the button click to show meme sources"""
    ack()

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
