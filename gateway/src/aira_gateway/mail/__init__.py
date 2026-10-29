"""Mail when a budget runs low, and when a use case's anomaly rules find something (`FRD-409`).

transport   handing a mail to the installation's relay
recipients  who a mail goes to, and the address the directory has for them
messages    what a mail says
notifier    the background round that owes and sends them
"""

from aira_gateway.mail.notifier import MailNotifier, build_notifier
from aira_gateway.mail.transport import MailUndeliverable, SmtpRelay

__all__ = ["MailNotifier", "MailUndeliverable", "SmtpRelay", "build_notifier"]
