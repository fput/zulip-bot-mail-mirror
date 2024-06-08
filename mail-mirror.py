#!/usr/bin/env python
"""
Mail -> Zulip Mirror Bot
~~~~~~~~~~~~~~~~~~~~~~~~

A bot that queries an IMAP server and mirrors new mails to Zulip.

Some functions are based on Zulip's mail handling code.
https://github.com/zulip/zulip (Apache License 2.0)

TODO: AsyncIO and IDLE using https://github.com/bamthomas/aioimaplib

:copyright: (c) 2018 by Florentin Putz.
:license: EUPL-1.2+
"""

from typing import Optional, Generator, Tuple

import bot_config

import email
from email import policy
from email.message import EmailMessage
from email.header import decode_header
from imaplib import IMAP4_SSL
import logging
import re
import html2text  # type: ignore
import talon  # type: ignore
import zulip  # type: ignore


class EmailMirrorError(Exception):
    """Custom exception for mail mirroring errors."""
    pass


def remove_subject_prefixes(subject: str, prefixes: Tuple[str, ...]) -> str:
    """Removes unwanted prefixes from the subject case insensitively.

    Args:
        subject: The subject of the mail.
        prefixes: A tuple of prefixes to remove.

    Returns:
        The subject with unwanted prefixes removed.

    Example:
    >>> remove_subject_prefixes("Re: Fwd: Test", ("Fwd:", "Re:"))
    "Test"
    """
    def remove_prefixes_once(text: str) -> str:
        for prefix in prefixes:
            if text.lower().startswith(prefix.lower()):
                return text[len(prefix):].lstrip()
        return text

    while any(subject.lower().startswith(prefix.lower()) for prefix in prefixes):
        subject = remove_prefixes_once(subject)

    return subject


def get_zulip_topics_by_stream(client: zulip.Client,
                               stream: str) -> Generator[str, None, None]:
    """Yields all topic names in the given Zulip stream.

    Args:
        client: The Zulip client instance.
        stream: The name of the Zulip stream.

    Yields:
        The topic names in the stream.
    """
    # Get the stream id for the zulip stream
    response = client.get_stream_id(stream)
    stream_id = response["stream_id"]
    # Get topic for this stream id
    response = client.get_stream_topics(stream_id)
    for topic in response['topics']:
        yield topic['name']


def process_message(message: EmailMessage) -> None:
    """Mirrors an incoming mail to Zulip.

    Args:
        message: The mail to mirror.

    Raises:
        EmailMirrorError: If the message could not be sent to Zulip.
    """
    subject = extract_email_subject(message)
    client = zulip.Client(config_file=bot_config.ZULIPRC)


    # Extract the mail's subject
    # --------------------------
    # Remove all prefixes from subject
    subject = remove_subject_prefixes(subject, bot_config.UNWANTED_SUBJECT_PREFIXES).strip()

    # Don't remove quotations by default.
    # If this is a reply to an already mirrored message, skip all quotations
    remove_quotations = subject in get_zulip_topics_by_stream(client, bot_config.ZULIP_STREAM)

    # Extract the mail's body
    # -----------------------
    body = extract_email_body(message, remove_quotations)
    # Remove null characters, since Zulip will reject
    body = body.replace("\x00", "")
    body = filter_footers(body).strip() or '(No email body)'

    # Format the message for Zulip
    # ----------------------------
    body = quote_each_line(body)
    body = bot_config.ZULIP_MESSAGE_FORMAT.format(sender=message["From"], body=body)

    zulip_message = {
        "type": "stream",
        "to": bot_config.ZULIP_STREAM,
        "subject": subject,
        "content": body,
    }

    logging.debug("\n")
    logging.debug("\n")
    logging.debug(f"Mirroring mail with subject: {subject}")
    logging.debug(f"... Body:\n {body}")
    logging.debug("\n")
    response = client.send_message(zulip_message)

    if response["result"] != "success":
        raise EmailMirrorError("Failed to send message to Zulip."
                               f"{response['code']}: {response['msg']}")
    else:
        logging.info("Successfully mirrored mail with subject:"
                     f"{subject}")
        logging.debug("\n")


def extract_email_subject(message: EmailMessage) -> str:
    """Extracts the subject from a mail.

    Args:
        message: The mail message.

    Returns:
        The decoded and cleaned subject of the mail.
    """
    subject_header = str(message.get("Subject", "")).strip() or "(no topic)"
    encoded_subject, encoding = decode_header(subject_header)[0]

    try:
        topic = encoded_subject.decode(encoding) if encoding else str(encoded_subject)
    except (UnicodeDecodeError, LookupError):
        topic = "(unreadable subject)"

    return topic.strip()



def extract_email_body(message: EmailMessage,
                       remove_quotations: bool = False) -> str:
    """Extracts the body from a mail.

        Can handle both plaintext and HTML mails.

    Args:
        message: The mail message.
        remove_quotations: Whether to remove quotations from the mail body.

    Returns:
        The cleaned and formatted mail body.
    """
    plaintext_content = get_message_part_by_type(message, "text/plain")
    html_content = get_message_part_by_type(message, "text/html")

    if plaintext_content:
        # If the message contains a plaintext version of the body, use
        # that.
        if remove_quotations:
            plaintext_content = talon.quotations.extract_from_plain(
                plaintext_content)
        if plaintext_content:
            if plaintext_content.startswith("__") and html_content:
                pass
            else:
                return plaintext_content

    if html_content:
        if remove_quotations:
            html_content = talon.quotations.extract_from_html(html_content)

        # Convert HTML to Markdown
        h = html2text.HTML2Text()
        h.body_width = 0
        h.emphasis_mark = "*"
        return h.handle(html_content)

    #raise EmailMirrorError(f"Unable to find E-Mail body: {message}")
    return ''


def filter_footers(text: str) -> str:
    """Filters footers from the mail body.

    Args:
        text: The mail body text.

    Returns:
        The mail body without footers.
    """
    # Split the text into sections separated by "--..." or "__..." lines
    sections = re.split(r'\n--.*\n|\n__.*\n', text)

    if len(sections) in (1,2):
        # Only body, or exactly one footer? -> just return the body
        return sections[0].strip()

    useful_sections = [
        section for section in sections
        if not any(keyword in section.splitlines() for keyword in bot_config.FOOTER_FILTER_KEYWORDS)
    ]

    return "\n".join(useful_sections).strip()


def quote_each_line(text: str) -> str:
    """Quotes each line of the given text.

    Args:
        text: The text to quote.

    Returns:
        The quoted text.
    """
    lines = [f"> {line}" for line in text.splitlines()]
    return "\n".join(lines)


def get_imap_messages(
        delete_afterwards: bool = False
) -> Generator[EmailMessage, None, None]:
    """Yields all new mails.

    Based on:
    https://github.com/zulip/zulip/blob/a2a695dfa7a3fbd9d406dcce9c6299e41c6a445d/zerver/management/commands/email_mirror.py

    Args:
        delete_afterwards: Whether to delete the mails after processing.

    Yields:
        The new mails.
    """
    mb = IMAP4_SSL(bot_config.IMAP_SERVER)
    mb.login(bot_config.IMAP_USER, bot_config.IMAP_PASSWORD)
    try:
        mb.select()
        try:
            _, msg_ids = mb.search(None, 'ALL')
            for msg_id in msg_ids[0].split():
                _, msg_data = mb.fetch(msg_id, '(RFC822)')
                msg_as_bytes = msg_data[0][1]
                msg: EmailMessage = email.message_from_bytes(  # type: ignore
                    msg_as_bytes, policy=policy.default)
                yield msg
                if delete_afterwards:
                    mb.store(msg_id, '+FLAGS', '\\Deleted')
            mb.expunge()
        finally:
            mb.close()
    finally:
        mb.logout()


def get_message_part_by_type(message: EmailMessage,
                             content_type: str) -> Optional[str]:
    """Gets a specific part of the mail by content type.

    Args:
        message: The mail message.
        content_type: The desired content type (e.g., "text/plain", "text/html").

    Returns:
        The content of the desired type, if found.
    """
    # Source: Zulip's mail handling code
    charsets = message.get_charsets()

    for idx, part in enumerate(message.walk()):
        if part.get_content_type() == content_type:
            content = part.get_payload(decode=True)
            assert isinstance(content, bytes)
            if charsets[idx]:
                return content.decode(charsets[idx], errors="ignore")
    return None


def main() -> None:
    """Main function to process and mirror incoming mails to Zulip."""
    log_config = {
        "format": "%(asctime)s %(levelname)-8s %(message)s",
        "datefmt": "%d-%H:%M:%S"
    }
    log_config["level"] = bot_config.LOGLEVEL  # type: ignore
    logging.basicConfig(**log_config)  # type: ignore
    logging.getLogger("zulip").setLevel(logging.WARNING)

    talon.init()

    try:
        for message in get_imap_messages(bot_config.REMOVE_MIRRORED_MAILS):
            try:
                process_message(message)
            except EmailMirrorError as e:
                logging.error(f"Error while processing incoming E-Mail: {str(e)}")
    except KeyboardInterrupt:
        print("Exiting... (keyboard interrupt)")

    logging.info("Exited.")


if __name__ == "__main__":
    main()
