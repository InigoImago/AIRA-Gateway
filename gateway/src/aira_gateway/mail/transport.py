"""Handing a mail to the installation's relay (`FRD-409` FR-8).

The standard library's SMTP client, in a worker thread: it blocks, so it never runs on the event
loop, and a timeout bounds how long a relay that stops answering can hold that thread.
"""

from __future__ import annotations

import asyncio
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Literal, Protocol

#: How long one conversation with the relay may take, the connection included.
TIMEOUT_SECONDS = 15.0

#: The longest reason a failed attempt records (`MailNotice.last_error`).
MAX_REASON = 255

Security = Literal["starttls", "tls", "none"]


class MailUndeliverable(RuntimeError):
    """The relay could not be reached, or did not take the mail. Said without any address."""


class Relay(Protocol):
    """Where a composed mail goes. The SMTP relay in production, a recorder in a test."""

    async def send(self, message: EmailMessage) -> None: ...


@dataclass(frozen=True, slots=True)
class SmtpRelay:
    """The installation's relay, as the gateway's settings describe it."""

    host: str
    port: int
    security: Security = "starttls"
    username: str = ""
    password: str = ""

    async def send(self, message: EmailMessage) -> None:
        await asyncio.to_thread(self._send, message)

    def _send(self, message: EmailMessage) -> None:
        context = ssl.create_default_context()
        try:
            client = (
                smtplib.SMTP_SSL(self.host, self.port, timeout=TIMEOUT_SECONDS, context=context)
                if self.security == "tls"
                else smtplib.SMTP(self.host, self.port, timeout=TIMEOUT_SECONDS)
            )
            with client:
                if self.security == "starttls":
                    # Refuses a relay that cannot upgrade, rather than sending in the clear anyway:
                    # `none` is the setting for a relay that offers no TLS.
                    client.starttls(context=context)
                if self.username:
                    client.login(self.username, self.password)
                refused = client.send_message(message)
        except (OSError, smtplib.SMTPException) as exc:
            raise MailUndeliverable(reason(exc)) from exc
        if refused:
            raise MailUndeliverable("the relay refused the recipient")


def reason(exc: BaseException) -> str:
    """Why an attempt failed, as a notice records it: the kind of failure and the relay's code,
    never its text — `SMTPRecipientsRefused` carries the addresses it refused."""
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return "the relay refused the recipient"
    if isinstance(exc, smtplib.SMTPResponseException):
        return f"the relay answered {exc.smtp_code} ({type(exc).__name__})"[:MAX_REASON]
    if isinstance(exc, smtplib.SMTPException):
        return f"the relay could not be used ({type(exc).__name__})"[:MAX_REASON]
    return f"the relay could not be reached ({type(exc).__name__})"[:MAX_REASON]
