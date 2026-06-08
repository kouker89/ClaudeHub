"""Send email via QQ SMTP — used by agents to notify users.

Credentials priority:
  1. Environment variables: CLAUDESESSION_EMAIL_USER / CLAUDESESSION_EMAIL_PASS
  2. Config file: tools/send-mail-config.json  ({"user": "...", "password": "..."})

Usage:
  python send-mail.py "subject" "body"
  python send-mail.py "subject" "body" recipient@example.com
"""

import json
import os
import smtplib
import sys
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path


def _load_credentials():
    """Load email credentials from env or config file."""
    user = os.environ.get("CLAUDESESSION_EMAIL_USER", "")
    password = os.environ.get("CLAUDESESSION_EMAIL_PASS", "")

    if user and password:
        return user, password

    config_path = Path(__file__).resolve().parent / "send-mail-config.json"
    if config_path.exists():
        try:
            cfg = json.loads(config_path.read_text(encoding="utf-8"))
            user = cfg.get("user", "")
            password = cfg.get("password", "")
        except Exception:
            pass

    return user, password


def send_email(subject: str, body: str, to_addr: str = None):
    """Send an email. Returns (success: bool, message: str)."""
    user, password = _load_credentials()
    if not user or not password:
        return False, "未配置邮箱凭据（环境变量或 send-mail-config.json）"

    if to_addr is None:
        to_addr = os.environ.get("CLAUDESESSION_EMAIL_TO", user)

    try:
        msg = MIMEMultipart()
        msg["From"] = user
        msg["To"] = to_addr
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain", "utf-8"))

        server = smtplib.SMTP_SSL("smtp.qq.com", 465, timeout=15)
        server.login(user, password)
        server.sendmail(user, [to_addr], msg.as_string())
        server.quit()
        return True, f"邮件已发送 → {to_addr}"
    except Exception as e:
        return False, f"发送失败: {e}"


def main():
    if len(sys.argv) < 3:
        print("Usage: python send-mail.py <subject> <body> [recipient]")
        sys.exit(1)

    subject = sys.argv[1]
    body = sys.argv[2]
    to_addr = sys.argv[3] if len(sys.argv) > 3 else None

    ok, msg = send_email(subject, body, to_addr)
    print(msg)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
