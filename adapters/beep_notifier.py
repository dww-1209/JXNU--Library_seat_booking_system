from __future__ import annotations

import sys
import threading
import time

from domain.ports import NotifierPort
from services.log_bus import LogBus, get_log_bus


class BeepNotifier(NotifierPort):
    """Windows acoustic notification adapter using winsound.Beep."""

    def __init__(
        self,
        enabled: bool = True,
        blocking: bool = False,
        bus: LogBus | None = None,
    ) -> None:
        self._enabled = enabled
        self._blocking = blocking
        self._bus = bus or get_log_bus()

    @staticmethod
    def _play_tones(success: bool) -> None:
        if sys.platform != "win32":
            return
        try:
            import winsound

            if success:
                for freq, dur in ((1046, 160), (1318, 160), (1568, 240)):
                    winsound.Beep(freq, dur)
                    time.sleep(0.06)
            else:
                for freq, dur in ((520, 200), (420, 260)):
                    winsound.Beep(freq, dur)
                    time.sleep(0.08)
        except Exception:
            pass

    def notify(self, success: bool, title: str, message: str) -> None:
        """Log completion notice and emit audible chime on Windows."""
        level_fn = self._bus.info if success else self._bus.warning
        level_fn(
            "NOTIFY",
            f"{title} - {message}",
            detail={"success": success, "title": title},
        )
        if not self._enabled or sys.platform != "win32":
            return

        if self._blocking:
            self._play_tones(success)
        else:
            worker = threading.Thread(
                target=self._play_tones,
                args=(success,),
                name="chaoxing-beep-notifier",
                daemon=True,
            )
            worker.start()
