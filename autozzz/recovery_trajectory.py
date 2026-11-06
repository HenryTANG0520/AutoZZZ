"""Read-only Area Patrol trajectory on already captured native frames."""
from __future__ import annotations

from collections import deque
from queue import Empty, Full, Queue
import threading
import time

from .recovery_battle_clock import BattleClockWitness, _segment_clock
from .recovery_observation import MAX_CAPTURE_SECONDS, MAX_GAP_SECONDS


def scene_plane(image):
    """Retain only the playable scene, excluding the clock, UID and most UI."""
    import cv2

    height, width = image.shape[:2]
    if min(height, width) <= 0 or width * 9 != height * 16 or image.shape != (height, width, 3) or image.dtype.name != 'uint8':
        raise ValueError('invalid scene image')
    scene = cv2.resize(image, (640, 360), interpolation=cv2.INTER_AREA)[42:287, 64:576]
    return cv2.cvtColor(scene, cv2.COLOR_RGB2GRAY)


def moving_scene_link(before, after):
    """Require forward and backward local matches with actual scene motion."""
    import cv2
    import numpy as np

    points = cv2.goodFeaturesToTrack(before, maxCorners=96, qualityLevel=.015, minDistance=8, blockSize=7)
    if points is None or len(points) < 16:
        return False
    forward, matched, error = cv2.calcOpticalFlowPyrLK(before, after, points, None, winSize=(21, 21), maxLevel=2)
    back, returned, _ = cv2.calcOpticalFlowPyrLK(after, before, forward, None, winSize=(21, 21), maxLevel=2)
    original = points.reshape(-1, 2)
    movement = np.linalg.norm(forward.reshape(-1, 2) - original, axis=1)
    error_back = np.linalg.norm(back.reshape(-1, 2) - original, axis=1)
    tracks = (matched.ravel() > 0) & (returned.ravel() > 0) & (error_back <= 1.25) & (error.ravel() <= 15) & (movement <= 48)
    moving = tracks & (movement >= 1.25)

    def zones(mask):
        return len({(int(x // 128), int(y // 82)) for x, y in original[mask]})

    return bool(np.count_nonzero(tracks) >= 10 and zones(tracks) >= 3
                and np.count_nonzero(moving) >= 3 and zones(moving) >= 2)


def _clock(image):
    import cv2

    if image.shape[:2] != (1080, 1920):
        image = cv2.resize(image, (1920, 1080), interpolation=cv2.INTER_AREA)
    return _segment_clock(image)


def _same_scope(first, second):
    return (first is not None and second is not None and first[1] == second[1]
            and len(first[0]) == len(second[0]) and all(a is b for a, b in zip(first[0], second[0])))


class AreaPatrolTrajectory:
    """Bounded observer. Producers pass receipts or already verified frozen samples."""

    def __init__(self):
        self._events = Queue(maxsize=3)
        self._stopped, self._overflow = threading.Event(), threading.Event()
        self._claim_at = self._first_frozen = None
        self._frozen_scope = None
        self._result = ('collecting', None, 0, (), 0., 0.)
        try:
            threading.Thread(target=self._observe, name='AutoZZZ-AreaPatrolTrajectory', daemon=True).start()
        except RuntimeError:
            self._stopped.set()
            self._result = ('unverified', 'diagnostic_worker_unavailable', 0, (), 0., 0.)

    def _offer(self, kind, *data):
        if self._stopped.is_set() or self._overflow.is_set():
            return
        try:
            self._events.put_nowait((kind, data))
        except Full:
            self._overflow.set()

    def original(self, scope, receipt, frame):
        timing = receipt.epoch, receipt.sequence, receipt.started, receipt.finished
        self._offer('original', scope, timing, frame)

    def frozen(self, scope, epoch, anchor):
        self._offer('freeze', scope, epoch, anchor)

    def sample(self, scope, frame, elapsed):
        self._offer('sample', scope, frame, elapsed)

    def claim(self, at):
        if self._claim_at is None:
            self._claim_at = at

    def reject(self, reason):
        self._offer('reject', reason)

    def close(self):
        self._stopped.set()

    def status(self, scope):
        state, reason, original, frozen, finished, work_ms = self._result
        claim_at = self._claim_at
        claimed = sum(claim_at is not None and start >= claim_at for start in frozen)
        if self._overflow.is_set():
            state, reason = 'unverified', 'diagnostic_queue_overrun'
        elif state == 'frozen' and not _same_scope(scope, self._frozen_scope):
            state, reason = 'unverified', 'trajectory_scope_changed'
        elif state == 'frozen' and not 0 <= time.perf_counter() - finished <= MAX_GAP_SECONDS:
            state, reason = 'unverified', 'trajectory_stale'
        elif state == 'frozen' and claimed >= 2 and self._first_frozen < claim_at:
            state = 'observed'
        return dict(state=state, reason=reason, original_samples=original,
                    frozen_samples=len(frozen), after_claim=claimed,
                    max_work_ms=round(work_ms, 3), input_authorized=False)

    def _observe(self):
        clock = BattleClockWitness(MAX_GAP_SECONDS)
        scope = receipt = previous = plane = None
        frozen = deque(maxlen=32)
        before, max_work = 0, 0.
        blocked = False
        while not self._stopped.is_set() and not self._overflow.is_set():
            try:
                kind, data = self._events.get(timeout=.10)
            except Empty:
                continue
            began = time.perf_counter()
            try:
                if kind == 'reject':
                    raise ValueError(data[0])
                if kind == 'freeze':
                    key, epoch, anchor = data
                    if (blocked or before < 3 or not clock.progressed or anchor is not previous
                            or not _same_scope(key, scope) or epoch != receipt[0] + 1
                            or not 0 <= began - anchor.finished_at <= MAX_GAP_SECONDS):
                        raise ValueError('trajectory_anchor_unverified')
                    blocked = True
                    self._frozen_scope = scope
                    self._result = ('frozen', None, before, (), anchor.finished_at, max_work)
                    continue
                if kind == 'original':
                    key, current, frame = data
                    epoch, sequence, started, finished = current
                    if (blocked or key is None or not started <= frame.started_at <= frame.finished_at <= finished):
                        raise ValueError('capture_receipt_unverified')
                    if (not _same_scope(key, scope) or receipt is not None
                            and (epoch != receipt[0] or sequence != receipt[1] + 1)):
                        clock, previous, plane, before = BattleClockWitness(MAX_GAP_SECONDS), None, None, 0
                    scope, receipt = key, current
                    elapsed = _clock(frame.image)
                    if previous is None and (elapsed is None or elapsed < 500):
                        raise ValueError('opening_scene_ambiguous')
                else:
                    key, frame, elapsed = data
                    if not blocked or not _same_scope(key, scope):
                        raise ValueError('trajectory_scope_changed')
                start, finish = frame.started_at, frame.finished_at
                if (not 0 <= finish - start <= MAX_CAPTURE_SECONDS or not 0 <= began - finish <= MAX_GAP_SECONDS
                        or previous is not None and (frame.identity != previous.identity or start < previous.finished_at)):
                    raise ValueError('trajectory_frame_unverified')
                problem = clock.observe(elapsed, start, finish)
                if problem is not None:
                    raise ValueError(problem)
                next_plane = scene_plane(frame.image)
                if plane is not None and not moving_scene_link(plane, next_plane):
                    raise ValueError('moving_scene_unlinked')
                if time.perf_counter() - finish > MAX_GAP_SECONDS:
                    raise ValueError('trajectory_processing_stale')
                previous, plane = frame, next_plane
                if blocked:
                    if self._first_frozen is None:
                        self._first_frozen = start
                    frozen.append(start)
                else:
                    before += 1
                max_work = max(max_work, (time.perf_counter() - began) * 1000)
                self._result = ('frozen' if blocked else 'collecting', None, before, tuple(frozen), finish, max_work)
            except Exception as exc:
                reason = str(exc) if type(exc) is ValueError else 'trajectory_processing_failed'
                self._result = ('unverified', reason, before, tuple(frozen), 0., max_work)
                if kind != 'original' or blocked:
                    self._stopped.set()
                    return
                clock, previous, plane, before = BattleClockWitness(MAX_GAP_SECONDS), None, None, 0
