import base64
import logging
import smtplib
import ssl
import urllib.error
import urllib.request
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone
from typing import Any

try:
    from udi_interface import LOGGER
except ImportError:
    LOGGER = logging.getLogger(__name__)

TYPE_DESCRIPTIONS = {
    "spike": "Sudden Spike / Surge",
    "autonomous_spike": "Statistical Outlier Spike",
    "stuck_watchdog": "Silent / Stuck Sensor",
    "slow_creep": "Continuous Creep / Leak",
    "contextual_hourly": "Hour-of-Day Deviation",
    "threshold": "Threshold Limit Exceeded",
}

TYPE_CODES = {
    "spike": 1,
    "autonomous_spike": 1,
    "stuck_watchdog": 2,
    "slow_creep": 3,
    "contextual_hourly": 4,
    "threshold": 5,
}


def format_alert_message(alert: dict[str, Any], device_name: str | None = None) -> tuple[str, str, str]:
    """Compile structured alert message into (subject, text_body, html_body)."""
    node_id = alert.get("node_id", "Unknown")
    control = alert.get("control", "ST")
    severity = str(alert.get("severity", "WARNING")).upper()
    task_type = alert.get("task_type", "spike")
    type_desc = TYPE_DESCRIPTIONS.get(task_type, task_type.replace("_", " ").title())
    score = alert.get("score", 85)
    val = alert.get("value")
    val_str = f"{val:.1f}" if isinstance(val, (int, float)) else str(val if val is not None else "N/A")
    details = alert.get("details", {})
    task_name = alert.get("task_name") or alert.get("task_id", "Monitor Task")

    ts_ms = alert.get("timestamp_ms")
    if ts_ms:
        dt = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
        dt_str = dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    else:
        dt_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    friendly_label = device_name or alert.get("device_name") or node_id

    # 1. Subject Line
    subject = f"[ALERT - {severity}] {friendly_label} ({control}): {type_desc} ({val_str})"

    # 2. Diagnostics details
    diag_lines = []
    if "z_score" in details:
        diag_lines.append(f"- Z-Score:       {details['z_score']}z (Deviation from normal)")
    if "mean" in details or "hourly_mean" in details:
        mean_v = details.get("mean") or details.get("hourly_mean")
        diag_lines.append(f"- Baseline Mean: {mean_v}")
    if "stddev" in details or "hourly_std" in details:
        std_v = details.get("stddev") or details.get("hourly_std")
        diag_lines.append(f"- Std Dev (std): {std_v}")
    if "rate_per_second" in details:
        rate_val = float(details["rate_per_second"])
        diag_lines.append(f"- Rate of Change:{rate_val:.2f}/sec")
    if "silent_minutes" in details:
        diag_lines.append(f"- Time Silent:   {details['silent_minutes']} minutes")
    if "min_value_observed" in details:
        diag_lines.append(f"- Min Observed:  {details['min_value_observed']} (Floor did not reach zero)")
    if "reason" in details:
        diag_lines.append(f"- Note:          {details['reason']}")

    diag_text = "\n".join(diag_lines) if diag_lines else "- Standard statistical deviation threshold reached."

    # 3. Plain Text Body
    text_body = f"""ANOMALY DETECTED BY UDI MONITOR-ALERT
============================================================
Device:      {node_id} [{friendly_label}]
Parameter:   {control}
Current Val: {val_str}
Triggered:   {dt_str}
Severity:    {severity}
Rule:        {task_name} ({type_desc})
Confidence:  {score}%

STATISTICAL DIAGNOSTICS:
------------------------------------------------------------
{diag_text}

STATUS:
------------------------------------------------------------
Duplicate alerts throttled by task cooldown.
Review in IoX Admin Console or PG3x Dashboard.
============================================================
"""

    # 4. HTML Body
    badge_color = "#e53935" if severity == "CRITICAL" else "#fb8c00"
    clean_lines = [line.lstrip("- ").strip() for line in diag_lines]
    diag_html = "".join(f"<li>{cl}</li>" for cl in clean_lines) if clean_lines else "<li>Standard statistical deviation threshold reached.</li>"
    html_body = f"""<!DOCTYPE html>
<html>
<head>
<style>
  body {{ font-family: Arial, sans-serif; background-color: #f4f6f9; margin: 0; padding: 20px; }}
  .card {{ background: #ffffff; border-radius: 8px; max-width: 600px; margin: 0 auto; padding: 24px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); border-left: 6px solid {badge_color}; }}
  h2 {{ margin-top: 0; color: #1e293b; }}
  .badge {{ display: inline-block; padding: 4px 10px; border-radius: 4px; font-weight: bold; color: white; background: {badge_color}; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 16px; }}
  td {{ padding: 8px; border-bottom: 1px solid #e2e8f0; }}
  .label {{ font-weight: bold; color: #475569; width: 35%; }}
  .diag {{ background: #f8fafc; border-radius: 6px; padding: 12px; margin-top: 16px; border: 1px solid #e2e8f0; }}
  .footer {{ font-size: 12px; color: #94a3b8; margin-top: 20px; text-align: center; }}
</style>
</head>
<body>
  <div class="card">
    <div style="display: flex; justify-content: space-between; align-items: center;">
      <h2>⚠️ Anomaly Detected</h2>
      <span class="badge">{severity}</span>
    </div>
    <table>
      <tr><td class="label">Device</td><td><strong>{friendly_label}</strong> ({node_id})</td></tr>
      <tr><td class="label">Parameter</td><td>{control}</td></tr>
      <tr><td class="label">Current Reading</td><td><span style="font-size: 18px; font-weight: bold; color: #0f172a;">{val_str}</span></td></tr>
      <tr><td class="label">Anomaly Type</td><td>{type_desc}</td></tr>
      <tr><td class="label">Alert Rule</td><td>{task_name}</td></tr>
      <tr><td class="label">Detection Time</td><td>{dt_str}</td></tr>
    </table>

    <div class="diag">
      <strong style="color: #334155;">Statistical Diagnostics:</strong>
      <ul style="margin: 8px 0 0 0; padding-left: 20px; color: #475569;">
        {diag_html}
      </ul>
    </div>

    <div class="footer">
      UDI MonitorAlert Node Server &bull; Universal Devices eISY
    </div>
  </div>
</body>
</html>
"""

    return subject, text_body, html_body


def send_email_notification(
    config: dict[str, Any],
    subject: str,
    text_body: str,
    html_body: str | None = None,
) -> bool:
    """Send notification via SMTP."""
    host = config.get("smtp_host")
    port = int(config.get("smtp_port") or 587)
    user = config.get("smtp_user")
    password = config.get("smtp_password")
    to_addr = config.get("notify_email_to")
    from_addr = config.get("smtp_from") or user or "alerts@eisy.local"

    if not host or not to_addr:
        LOGGER.warning("Email notification skipped: smtp_host or notify_email_to missing.")
        return False

    recipients = [addr.strip() for addr in to_addr.split(",") if addr.strip()]
    if not recipients:
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = ", ".join(recipients)

    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    if html_body:
        msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        if port == 465:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(host, port, context=context, timeout=10) as server:
                if user and password:
                    server.login(user, password)
                server.sendmail(from_addr, recipients, msg.as_string())
        else:
            with smtplib.SMTP(host, port, timeout=10) as server:
                server.ehlo()
                try:
                    context = ssl.create_default_context()
                    server.starttls(context=context)
                    server.ehlo()
                except (smtplib.SMTPNotSupportedError, RuntimeError):
                    pass
                if user and password:
                    server.login(user, password)
                server.sendmail(from_addr, recipients, msg.as_string())

        LOGGER.info("Email notification sent successfully to %s: %s", recipients, subject)
        return True
    except Exception as exc:
        LOGGER.warning("Failed to send email notification to %s: %s", recipients, exc)
        return False


def send_udmobile_iox_notification(
    config: dict[str, Any],
    alert: dict[str, Any],
    subject: str,
    body: str,
) -> bool:
    """Trigger IoX built-in notification / UD Mobile push via REST."""
    host = config.get("host")
    port = config.get("port")
    user = config.get("username")
    pwd = config.get("password")
    secure = bool(config.get("secure", False))

    content_id = config.get("notify_udmobile_content_id") or "1"
    recipient_id = config.get("notify_udmobile_recipient_id") or "1"

    if not host or not user or not pwd:
        LOGGER.debug("IoX notification skipped: connection credentials missing.")
        return False

    proto = "https" if secure else "http"
    url = f"{proto}://{host}:{port}/rest/networking/notify/{content_id}/{recipient_id}"

    try:
        auth_str = f"{user}:{pwd}"
        b64_auth = base64.b64encode(auth_str.encode("utf-8")).decode("ascii")
        req = urllib.request.Request(
            url,
            headers={"Authorization": f"Basic {b64_auth}"},
        )
        ctx = None
        if secure:
            ctx = ssl._create_unverified_context()

        with urllib.request.urlopen(req, timeout=5, context=ctx) as resp:
            status = getattr(resp, "status", None) or resp.getcode()
            if status in (200, 204):
                LOGGER.info("IoX UD Mobile notification triggered: content_id=%s recipient_id=%s", content_id, recipient_id)
                return True
            else:
                LOGGER.warning("IoX notification endpoint returned status %s", status)
                return False
    except Exception as exc:
        LOGGER.warning("Failed to trigger IoX notification: %s", exc)
        return False


def dispatch_alert(
    alert: dict[str, Any],
    config: dict[str, Any],
    device_name: str | None = None,
) -> dict[str, bool]:
    """Format and send alert across configured channels (email, udmobile)."""
    channels_str = str(config.get("notify_channels", "email,udmobile")).lower()
    channels = {c.strip() for c in channels_str.split(",") if c.strip()}

    subject, text_body, html_body = format_alert_message(alert, device_name=device_name)
    results = {}

    if "email" in channels:
        results["email"] = send_email_notification(config, subject, text_body, html_body)

    if "udmobile" in channels or "iox" in channels:
        results["udmobile"] = send_udmobile_iox_notification(config, alert, subject, text_body)

    return results
