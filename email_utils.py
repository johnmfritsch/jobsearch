"""
email_utils.py — Shared email sending utilities for GardenBuddy.
Handles base64 image → CID attachment conversion and HTML email construction.
"""
import os
import re
import base64
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders

SECRETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'secrets')


def load_gmail_credentials():
    with open(os.path.join(SECRETS_DIR, 'gmail_app_password.txt'), 'r') as f:
        line = f.read().strip()
    user, password = line.split(':', 1)
    return user.strip(), password.strip()


def html_to_cid(body_html, cid_prefix='img'):
    """Extract base64 images from HTML body, replace with CID references.
    Returns (clean_html, cid_parts) where cid_parts is a list of
    (cid, mime_type, b64_data, ext) tuples."""
    img_pattern = re.compile(r'src="data:(image/[^;]+);base64,([^"]+)"')
    cid_parts = []

    def replace_with_cid(match):
        mime_type = match.group(1)
        b64_data  = match.group(2)
        cid       = f'{cid_prefix}_{len(cid_parts)}'
        ext       = mime_type.split('/')[-1].replace('jpeg', 'jpg')
        cid_parts.append((cid, mime_type, b64_data, ext))
        return f'src="cid:{cid}"'

    clean_html = img_pattern.sub(replace_with_cid, body_html)
    return clean_html, cid_parts


def build_html_email(sender, to_addrs, subject, body_html,
                     extra_header_html='', cid_prefix='img'):
    """Build a MIMEMultipart email with HTML body and CID image attachments.

    body_html     — Quill HTML content (may contain base64 images)
    extra_header_html — optional HTML block prepended above body_html (e.g. metadata table)

    Deliberately NO Bcc header support: hidden recipients are passed as the
    explicit envelope list via send_email(msg, to_addrs=[...]) instead. A Bcc
    header that never exists can't leak — the old header-based approach only
    stayed private because smtplib.send_message happens to strip Bcc before
    transmitting (a sendmail()/as_string() refactor would have exposed it).
    """
    clean_html, cid_parts = html_to_cid(body_html, cid_prefix=cid_prefix)

    msg = MIMEMultipart('related')
    msg['From']    = sender
    msg['To']      = ', '.join(to_addrs) if isinstance(to_addrs, list) else to_addrs
    msg['Subject'] = subject

    full_html = f'''<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<style>
body {{ font-family:sans-serif; font-size:15px; color:#222; max-width:680px; margin:0 auto; padding:16px; }}
p {{ margin:0 0 8px 0; padding:0; }}
ul, ol {{ margin:0 0 8px 0; padding-left:20px; }}
table.meta {{ border-collapse:collapse; margin-bottom:16px; font-size:13px; }}
table.meta td {{ padding:3px 12px 3px 0; vertical-align:top; }}
table.meta td:first-child {{ font-weight:bold; white-space:nowrap; }}
</style>
</head>
<body>
{extra_header_html}
{clean_html}
<hr style="margin-top:32px;border:none;border-top:1px solid #ddd;">
<p style="font-size:11px;color:#999;margin-top:8px;">
  Sent via GardenBuddy &mdash; <a href="https://fritsch-nas.myasustor.com:7783/GardenBuddy" style="color:#999;">fritsch-nas.myasustor.com</a>
</p>
</body></html>'''

    alt = MIMEMultipart('alternative')
    alt.attach(MIMEText(full_html, 'html'))
    msg.attach(alt)

    for cid, mime_type, b64_data, ext in cid_parts:
        img_data = base64.b64decode(b64_data)
        img_part = MIMEBase('image', ext)
        img_part.set_payload(img_data)
        encoders.encode_base64(img_part)
        img_part.add_header('Content-ID', f'<{cid}>')
        img_part.add_header('Content-Disposition', 'inline', filename=f'{cid}.{ext}')
        msg.attach(img_part)

    return msg


def send_email(msg, to_addrs=None):
    """Send a pre-built email message via Gmail SMTP.

    to_addrs — optional explicit envelope recipient list. Pass this for
    hidden-recipient (Bcc-style) sends: the listed addresses receive the mail
    but never appear in any header. When None, recipients come from the
    message's To/Cc headers (normal visible-recipient send).

    A timeout is REQUIRED: without it, a slow/unreachable Gmail SMTP would block
    the calling Gunicorn worker thread indefinitely (the timeout passed to the
    SMTP constructor applies to connect AND all subsequent socket operations —
    starttls/login/send_message). See the 2026-07-08 wedged-worker incident.
    """
    sender, app_password = load_gmail_credentials()
    with smtplib.SMTP('smtp.gmail.com', 587, timeout=20) as server:
        server.starttls()
        server.login(sender, app_password)
        server.send_message(msg, to_addrs=to_addrs)
