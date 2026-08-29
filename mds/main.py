import os
import random

from dotenv import load_dotenv
import requests
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from slack_sdk.errors import SlackApiError

from .fetch import get_memes

load_dotenv()


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(
            f"Missing {name}. Add it to .env before starting the Slack app."
        )
    return value


SLACK_BOT_TOKEN = require_env("SLACK_BOT_TOKEN")
SLACK_APP_TOKEN = require_env("SLACK_APP_TOKEN")
SLACK_CHANNEL_ID = require_env("SLACK_CHANNEL_ID")

app = App(token=SLACK_BOT_TOKEN)
sources_by_message: dict[tuple[str, str], list[dict[str, str]]] = {}


def post_memes() -> None:
    memes = get_memes(limit=10)
    channel = SLACK_CHANNEL_ID

    if not memes:
        return

    file_uploads, memes = download_memes(memes)
    if not file_uploads:
        return
    comments = ["*Daily memes for you! Yes YOU!*", "*Memes, Memes, with a side of Memes*",
                "*Fresh memes for the day*"]
    app.client.files_upload_v2(
        channel=channel,
        file_uploads=file_uploads,
        initial_comment=random.choice(comments),
    )

    response = app.client.chat_postMessage(
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
            }],
        }],
        unfurl_links=False,
        unfurl_media=False,
    )

    sources_by_message[(channel, response["ts"])] = memes


def download_memes(
        memes: list[dict[str, str]],
) -> tuple[list[dict[str, str | bytes]], list[dict[str, str]]]:
    file_uploads = []
    valid_memes = []

    for index, meme in enumerate(memes, start=1):
        try:
            response = requests.get(meme["image"], timeout=15)
            response.raise_for_status()
            file_uploads.append({
                "file": response.content,
                "filename": f"meme-{index}.jpg",
                "title": meme["title"],
                "alt_txt": meme["title"],
            })
            valid_memes.append(meme)
        except requests.RequestException as error:
            print(f"Skipping image {meme['image']}: {error}")

    return file_uploads, valid_memes


@app.action("show_sources")
def show_sources(ack, body, client) -> None:
    ack()

    channel = body["container"]["channel_id"]
    message_ts = body["container"]["message_ts"]
    memes = sources_by_message.get((channel, message_ts), [])

    if not memes:
        source_text = "The source list is no longer available."
    else:
        source_text = "\n".join(
            f"• <{meme['url']}|{meme['title']}>"
            for meme in memes
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


if __name__ == "__main__":
    try:
        post_memes()
        SocketModeHandler(
            app,
            SLACK_APP_TOKEN,
        ).start()
    except SlackApiError as error:
        print(f"Slack error: {error.response.get('error')}")
        raise
