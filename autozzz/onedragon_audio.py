"""Bind audio dodge to the default render loopback and serialize recorder restarts."""
from __future__ import annotations

from contextvars import copy_context
from functools import partial
from threading import RLock


FIXED_REVISION = 'f80b68456306d2afb97be72436dcda27f2ed6b59'
WASAPI_REVISION = '60084153d4a63be17da7bc995eff9ea14efd25d1'


def install_audio_dodge(ctx, revision):
    if revision not in (FIXED_REVISION, WASAPI_REVISION):
        return
    from zzz_od.auto_battle import auto_battle_dodge_context as source

    dodge = ctx.auto_battle_context.dodge_context
    recorder = dodge._audio_recorder
    loop = partial(_fixed_record_loop, recorder) if revision == FIXED_REVISION else recorder._record_loop
    lifecycle = _RecorderLifecycle(recorder, loop, source._dodge_check_executor)
    recorder.start_running_async = lifecycle.start
    recorder.stop_running = lifecycle.stop
    dodge.check_dodge_audio = partial(lifecycle.check, dodge.check_dodge_audio)


class _RecorderLifecycle:
    def __init__(self, recorder, loop, executor):
        self.recorder, self.loop, self.executor = recorder, loop, executor
        self.lock = RLock()
        self.worker = self.pending = None

    def start(self):
        with self.lock:
            if self.worker is None:
                self._start()
            elif not self.recorder.running:
                self.pending = copy_context()

    def stop(self):
        with self.lock:
            self.pending = None
            self.recorder.running = False
            if self.worker is not None:
                self.worker.cancel()

    def check(self, check_audio, screenshot_time):
        if not self.lock.acquire(blocking=False):
            return False
        try:
            # A native read can return one final sample while its stream drains.
            if not self.recorder.running:
                return False
            return check_audio(screenshot_time) and self.recorder.running
        finally:
            self.lock.release()

    def _start(self):
        self.recorder.clear_audio()
        self.recorder.running = True
        try:
            self.worker = self.executor.submit(self._run)
        except RuntimeError:
            self.recorder.running = False
            raise
        self.worker.add_done_callback(self._finished)

    def _run(self):
        with self.lock:
            if not self.recorder.running:
                return
        self.loop()

    def _finished(self, worker):
        from one_dragon.utils.thread_utils import handle_future_result

        with self.lock:
            self.recorder.running = False
            self.worker = None
            pending, self.pending = self.pending, None
            if pending is not None:
                # AutoZZZ captures the current execution epoch in executor submissions.
                pending.run(self._start)
        if not worker.cancelled():
            handle_future_result(worker)


def _fixed_record_loop(recorder):
    import librosa
    import soundcard
    from one_dragon.utils.log_utils import log

    try:
        # Official #2691 selector, scoped to this recorder instance.
        speaker = soundcard.default_speaker()
        endpoint = next((mic for mic in soundcard.all_microphones(include_loopback=True)
                         if mic.isloopback and mic.id == speaker.id), None)
        if endpoint is None:
            raise RuntimeError(f'未找到默认扬声器 [{speaker.name}] 的 loopback 设备')
        with endpoint.recorder(samplerate=recorder._sample_rate, channels=recorder._used_channel) as stream:
            while recorder.running:
                chunk = stream.record(numframes=recorder._chunk_size).T
                if recorder._used_channel > 1:
                    chunk = librosa.to_mono(chunk)
                with recorder._update_audio_lock:
                    recorder.latest_audio[:-len(chunk)] = recorder.latest_audio[len(chunk):]
                    recorder.latest_audio[-len(chunk):] = chunk
    except RuntimeError as exc:
        log.warning('音频录制异常，已停止声音闪避识别', exc_info=True)
        if recorder._error_callback is not None:
            recorder._error_callback(exc)
    finally:
        recorder.running = False
