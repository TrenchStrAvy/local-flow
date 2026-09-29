from __future__ import annotations

import unittest

import numpy as np

import flow


class RecorderBufferTests(unittest.TestCase):
    """Feed the recorder 2-D (frames, channels) blocks like sounddevice does
    and make sure snapshot/drop/stop keep working, without a microphone."""

    def _rec(self):
        r = flow.Recorder()
        for i in range(3):
            r._on_audio(np.full((1600, 1), i + 1, dtype=np.float32))
        return r

    def test_snapshot_is_flat(self) -> None:
        a = self._rec().snapshot()
        self.assertEqual(a.shape, (4800,))
        self.assertEqual(a[0], 1.0)
        self.assertEqual(a[-1], 3.0)

    def test_drop_then_more_audio(self) -> None:
        r = self._rec()
        r.drop(2000)
        r._on_audio(np.full((1600, 1), 4, dtype=np.float32))   # 2-D again
        a = r.snapshot()
        self.assertEqual(a.shape, (4400,))
        self.assertEqual(a[0], 2.0)
        self.assertEqual(a[-1], 4.0)

    def test_drop_everything(self) -> None:
        r = self._rec()
        r.drop(10_000)
        self.assertEqual(r.snapshot().shape, (0,))
        r._on_audio(np.full((1600, 1), 5, dtype=np.float32))
        self.assertEqual(r.snapshot().shape, (1600,))


if __name__ == "__main__":
    unittest.main()


class HotkeyTests(unittest.TestCase):
    """The listener must ignore the app's own synthetic keystrokes."""

    def _app(self):
        app = flow.FlowApp.__new__(flow.FlowApp)
        app.__dict__.update(recording=True, locked=False, _hotkey_down=True,
                            overlay=None, menubar=None, preview="off",
                            typer=None, _preview_state={}, target=None)
        app._stop_recording = lambda: app.__dict__.update(recording=False)
        return app

    def test_injected_a_does_not_toggle_lock(self) -> None:
        app = self._app()

        class A:
            vk = flow.LOCK_KEYCODE
        app.on_press(A(), injected=True)
        self.assertFalse(app.locked)
        app.on_press(A(), injected=False)
        self.assertTrue(app.locked)
        app.on_release(flow.HOTKEY, injected=True)
        self.assertTrue(app.recording)

    def test_option_release_stops_only_when_unlocked(self) -> None:
        app = self._app()
        app.locked = True
        app.on_release(flow.HOTKEY)
        self.assertTrue(app.recording)
        app.locked = False
        app.on_release(flow.HOTKEY)
        self.assertFalse(app.recording)


class SplitTests(unittest.TestCase):
    def test_last_pause_and_utterance_end(self) -> None:
        # 100 ms frames: speech 0-19, gap 20-25 (0.6 s), speech 26-39,
        # gap 40-54 (1.5 s), speech 55-59
        rms = np.full(60, 0.1)
        rms[20:26] = 0.0
        rms[40:55] = 0.0
        F = flow.FRAME
        # the last 0.4 s+ pause is the 1.5 s one; cut in its middle
        self.assertEqual(flow.FlowApp._last_pause(rms), (40 + 16 // 2) * F)
        # sentence end (1.2 s+ gap): the first such gap is also that one
        self.assertEqual(flow.FlowApp._utterance_end(rms), (40 + 12 // 2) * F)
        # no long gap → no sentence end, but the short pause still splits
        self.assertIsNone(flow.FlowApp._utterance_end(rms[:40]))
        self.assertEqual(flow.FlowApp._last_pause(rms[:40]), (20 + 7 // 2) * F)
        self.assertIsNone(flow.FlowApp._last_pause(np.full(30, 0.1)))
