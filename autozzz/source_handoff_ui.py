"""Show the claimed task's paused state until the original worker exits."""
from __future__ import annotations

import queue
import threading
from tkinter import ttk


def show_recovered_charge(root, task, open_normal):
    root.title('AutoZZZ · 原体力刷本')
    panel = ttk.Frame(root, padding=24)
    panel.pack(fill='both', expand=True)
    ttk.Label(panel, text='已认领原体力刷本，保持暂停。').pack(anchor='w')
    ttk.Label(panel, text=f'原任务 {task.job}').pack(anchor='w', pady=(4, 10))
    status = ttk.Label(panel, text='可停止原任务，或等待原任务到期。')
    status.pack(anchor='w')
    events = queue.SimpleQueue()
    thread = None
    closing = False

    def request_stop():
        nonlocal thread
        if thread is not None or not task.worker.alive():
            return
        stop.state(['disabled'])
        status.configure(text='等待原任务停止并清理…')

        def send_stop():
            try:
                task.stop()
            except (OSError, RuntimeError, ValueError):
                events.put('未确认停止回执，等待原进程退出…')

        thread = threading.Thread(target=send_stop, name='AutoZZZ-RecoveredStop', daemon=False)
        thread.start()

    def close():
        nonlocal closing
        closing = True
        request_stop()

    def poll():
        try:
            status.configure(text=events.get_nowait())
        except queue.Empty:
            pass
        if task.worker.alive() or (thread is not None and thread.is_alive()):
            root.after(100, poll)
        elif closing:
            root.destroy()
        else:
            root.unbind('<F10>')
            panel.destroy()
            open_normal()

    stop = ttk.Button(panel, text='停止 F10', command=request_stop)
    stop.pack(anchor='w', pady=(12, 0))
    root.bind('<F10>', lambda _: request_stop())
    root.protocol('WM_DELETE_WINDOW', close)
    root.after(100, poll)
