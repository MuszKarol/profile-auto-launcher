"""Best-effort native desktop notifications, no extra dependencies.

Windows: WinRT toast via a hidden PowerShell; macOS: osascript;
Linux: notify-send. Failures are swallowed — a missing notifier must
never break a profile run. Disable entirely with PAL_NOTIFY=0.
"""
from __future__ import annotations

import os
import subprocess

from launcher.config import PLATFORM

_TOAST_PS = """
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
$xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$texts = $xml.GetElementsByTagName('text')
$texts.Item(0).AppendChild($xml.CreateTextNode('{title}')) | Out-Null
$texts.Item(1).AppendChild($xml.CreateTextNode('{body}')) | Out-Null
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('Profile Auto Launcher').Show($toast)
"""


def _ps_quote(text: str) -> str:
    return text.replace("'", "''")


def notify(title: str, body: str) -> None:
    from launcher import settings

    if not settings.load().notifications:
        return
    try:
        if PLATFORM == "windows":
            script = _TOAST_PS.format(title=_ps_quote(title), body=_ps_quote(body))
            CREATE_NO_WINDOW = 0x08000000
            subprocess.Popen(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                creationflags=CREATE_NO_WINDOW,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        elif PLATFORM == "darwin":
            osa = 'display notification "{}" with title "{}"'.format(
                body.replace('"', '\\"'), title.replace('"', '\\"')
            )
            subprocess.Popen(
                ["osascript", "-e", osa],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        else:
            subprocess.Popen(
                ["notify-send", "--app-name=Profile Auto Launcher", title, body],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
    except OSError:
        pass
