"""Handing a mail to the relay (`FRD-409` FR-8).

The real `smtplib` conversation, against a small SMTP server on a local port: what the relay is
told, and what a notice records when it does not take the mail — never an address.
"""

from __future__ import annotations

import socket
import socketserver
import threading
from collections.abc import Iterator
from email.message import EmailMessage

import pytest

from aira_gateway.mail import MailUndeliverable, SmtpRelay


class _Relay(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, *, refuse_recipients: bool = False) -> None:
        super().__init__(("127.0.0.1", 0), _Conversation)
        self.refuse_recipients = refuse_recipients
        self.envelope: list[str] = []
        self.received: list[str] = []


class _Conversation(socketserver.StreamRequestHandler):
    """Enough of SMTP for `smtplib`: no STARTTLS and no AUTH on offer."""

    server: _Relay

    def handle(self) -> None:
        self._say("220 relay.test ESMTP")
        data: list[bytes] | None = None
        while line := self.rfile.readline():
            if data is not None:
                if line == b".\r\n":
                    self.server.received.append(b"".join(data).decode())
                    data = None
                    self._say("250 queued")
                else:
                    data.append(line)
                continue
            command = line.strip().decode()
            verb = command.split(" ", 1)[0].upper()
            if verb == "EHLO":
                self._say("250-relay.test", "250 8BITMIME")
            elif verb == "MAIL":
                self.server.envelope.append(command)
                self._say("250 ok")
            elif verb == "RCPT":
                if self.server.refuse_recipients:
                    self._say("550 5.1.1 <ada@example.com>: no such user")
                else:
                    self.server.envelope.append(command)
                    self._say("250 ok")
            elif verb == "DATA":
                data = []
                self._say("354 go ahead")
            elif verb == "QUIT":
                self._say("221 bye")
                return
            else:
                self._say("250 ok" if verb in {"RSET", "NOOP"} else "502 not here")

    def _say(self, *lines: str) -> None:
        self.wfile.write("".join(f"{line}\r\n" for line in lines).encode())


def _running(relay: _Relay) -> Iterator[_Relay]:
    thread = threading.Thread(target=relay.serve_forever, daemon=True)
    thread.start()
    try:
        yield relay
    finally:
        relay.shutdown()
        relay.server_close()


@pytest.fixture
def relay() -> Iterator[_Relay]:
    yield from _running(_Relay())


@pytest.fixture
def refusing_relay() -> Iterator[_Relay]:
    yield from _running(_Relay(refuse_recipients=True))


def _mail() -> EmailMessage:
    message = EmailMessage()
    message["From"] = "AIRA <aira@example.com>"
    message["To"] = "ada@example.com"
    message["Subject"] = 'AIRA: your monthly budget in "Kundenservice" is used up'
    message.set_content('Hello,\n\nyour monthly budget in the use case "Kundenservice" — Ü.\n')
    return message


def _port(relay: _Relay) -> int:
    return int(relay.server_address[1])


async def test_a_relay_without_tls_takes_the_mail_when_told_to_use_none(relay: _Relay) -> None:
    await SmtpRelay("127.0.0.1", _port(relay), "none").send(_mail())

    sender, recipient = relay.envelope
    # `smtplib` writes its verbs in lower case, which SMTP allows.
    assert sender.lower().startswith("mail from:<aira@example.com>")
    assert recipient.lower() == "rcpt to:<ada@example.com>"
    (received,) = relay.received
    assert "Subject: AIRA: your monthly budget" in received


async def test_starttls_refuses_a_relay_that_cannot_upgrade_rather_than_send_in_the_clear(
    relay: _Relay,
) -> None:
    with pytest.raises(MailUndeliverable, match="SMTPNotSupportedError"):
        await SmtpRelay("127.0.0.1", _port(relay), "starttls").send(_mail())

    assert relay.received == []


async def test_a_login_the_relay_does_not_offer_is_a_failure_not_an_anonymous_send(
    relay: _Relay,
) -> None:
    with pytest.raises(MailUndeliverable):
        await SmtpRelay("127.0.0.1", _port(relay), "none", "aira", "s3cret").send(_mail())

    assert relay.received == []


async def test_a_refused_recipient_is_said_without_the_address(refusing_relay: _Relay) -> None:
    with pytest.raises(MailUndeliverable) as refused:
        await SmtpRelay("127.0.0.1", _port(refusing_relay), "none").send(_mail())

    assert str(refused.value) == "the relay refused the recipient"
    assert "@" not in str(refused.value)


async def test_a_relay_nobody_answers_at_is_a_failure_that_names_its_kind() -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        free = probe.getsockname()[1]

    with pytest.raises(MailUndeliverable, match="could not be reached"):
        await SmtpRelay("127.0.0.1", free, "none").send(_mail())
