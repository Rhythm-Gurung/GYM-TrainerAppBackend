import logging
from typing import Any

from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils.html import strip_tags

from core.settings.environments import DEFAULT_FROM_EMAIL

logger = logging.getLogger(__name__)


def send_emails(
    template: str,
    recipient_list: list[str],
    subject: str,
    context: dict[str, Any],
    metadata: dict[str, Any] = None
):
    to_email = recipient_list[0] if recipient_list else None
    if not to_email:
        return False

    html_content = render_to_string(template, context)
    text_content = strip_tags(html_content)

    from_email = "GymJam <" + DEFAULT_FROM_EMAIL + ">"

    email = EmailMultiAlternatives(
        subject=subject,
        body=text_content,
        from_email=from_email,
        to=[to_email],
        reply_to=[DEFAULT_FROM_EMAIL],
        headers={
            "X-Mailer": "My Project Mail Service",  # CHANGE THIS
            "X-Priority": "3",
            "Precedence": "bulk",
        }
    )

    email.attach_alternative(html_content, "text/html")

    try:
        email.send()
        logger.info("Email sent successfully to %s", to_email)
        return True
    except Exception as e:
        logger.error("Failed to send email to %s: %s", to_email, str(e))
        raise
