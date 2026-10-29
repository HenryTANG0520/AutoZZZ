"""通知时机与发送渠道的原生表单。"""
from __future__ import annotations

import copy
import queue
import tkinter as tk
from tkinter import messagebox, ttk

from . import theme
from .config import ROOT
from .onedragon_forms import Choice, ScalarForm, enable_scroll
from .onedragon_notify import LIFECYCLE, DETAIL
from .onedragon_runtime import RuntimePaths
from .onedragon_session import RuntimeSession


class NotificationForm:
    def __init__(self, parent, fields, changed, scale, channels):
        self.frame = ttk.Frame(parent)
        self.frame.bind("<Destroy>", self.disposed, add="+")
        self.frame.columnconfigure(0, weight=1)
        d = lambda n: round(n * scale)
        self.scalar = ScalarForm(self.frame, [f for f in fields if f["kind"] != "notification_modes"], changed, scale)
        self.scalar.frame.grid(row=0, column=0, sticky="ew")
        ttk.Button(self.frame, text="发送渠道…", command=channels).grid(row=1, column=0, sticky="w", pady=(0, d(18)))
        field = next(f for f in fields if f["kind"] == "notification_modes")
        self.rows = copy.deepcopy(field["value"])
        names = {option["value"]: option["label"] for option in field["options"]}
        area = ttk.LabelFrame(self.frame, text="应用通知时机", padding=d(12))
        area.grid(row=2, column=0, sticky="nsew")
        area.columnconfigure(0, weight=1)
        self.table = ttk.Treeview(area, columns=("name", "lifecycle", "detail"), show="headings", height=7,
                                 selectmode="browse", style="Config.Treeview")
        for key, title, width in (("name", "应用", 250), ("lifecycle", "开始 / 结束", 150), ("detail", "节点细节", 120)):
            self.table.heading(key, text=title)
            self.table.column(key, width=d(width), minwidth=d(80), stretch=key == "name")
        self.table.grid(row=0, column=0, sticky="ew")
        scroll = ttk.Scrollbar(area, command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set)
        scroll.grid(row=0, column=1, sticky="ns")
        self.names, self.changed = names, changed
        for row in self.rows: self.table.insert("", "end", iid=row["app_id"], values=self.display(row))
        editor = ttk.Frame(area)
        editor.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(d(12), 0))
        self.lifecycle = Choice(editor, [dict(label=k, value=v) for k, v in LIFECYCLE], "off")
        self.detail = Choice(editor, [dict(label=k, value=v) for k, v in DETAIL], "off")
        for col, (name, control) in enumerate((("开始 / 结束", self.lifecycle), ("节点细节", self.detail))):
            ttk.Label(editor, text=name).grid(row=col, column=0, sticky="w", padx=(0, d(12)), pady=(0, d(8)))
            control.widget.configure(width=14)
            control.widget.grid(row=col, column=1, sticky="w", pady=(0, d(8)))
            control.widget.bind("<<ComboboxSelected>>", self.edit)
        ttk.Button(editor, text="应用到全部", command=self.all).grid(row=2, column=0, columnspan=2, sticky="w", pady=(d(4), 0))
        self.table.bind("<<TreeviewSelect>>", self.select)
        if self.rows: self.table.selection_set(self.rows[0]["app_id"])

    def display(self, row):
        return self.names[row["app_id"]], dict((v, k) for k, v in LIFECYCLE).get(row["lifecycle"], row["lifecycle"]), dict((v, k) for k, v in DETAIL).get(row["detail"], row["detail"])

    def select(self, _event=None):
        selected = self.table.selection()
        if not selected: return
        row = next(r for r in self.rows if r["app_id"] == selected[0])
        self.lifecycle.variable.set(dict((v, k) for k, v in LIFECYCLE).get(row["lifecycle"], row["lifecycle"]))
        self.detail.variable.set(dict((v, k) for k, v in DETAIL).get(row["detail"], row["detail"]))

    def edit(self, _event=None, all_rows=False):
        selected = set(self.table.selection())
        if not selected: return
        for row in self.rows:
            if all_rows or row["app_id"] in selected:
                row.update(lifecycle=self.lifecycle.get(), detail=self.detail.get())
                self.table.item(row["app_id"], values=self.display(row))
        self.changed()

    def all(self): self.edit(all_rows=True)

    def values(self): return dict(self.scalar.values(), notification_modes=copy.deepcopy(self.rows))

    def disposed(self, event):
        if event.widget is self.frame:
            # 显式在 Tk 主线程释放变量，避免循环引用由后台 RPC 线程回收。
            self.lifecycle.variable = self.detail.variable = None
            self.lifecycle = self.detail = None
            self.changed = None


class ChannelsWindow:
    def __init__(self, ui, profile):
        self.ui, self.profile = ui, profile
        self.connected = self.busy = self.closed = self.closing = self.dirty = False
        self.description, self.form, self.callback = None, None, None
        self.events, self.inputs, self.traces = queue.Queue(), {}, []
        self.frozen = []
        self.current_id = "_settings"
        self.channels = {}
        self.poll_token = None
        self.window = tk.Toplevel(ui.root)
        self.window.title("AutoZZZ · 发送渠道")
        d = lambda n: round(n * ui.ui_scale)
        width, height = min(d(920), ui.root.winfo_screenwidth() - d(50)), min(d(740), ui.root.winfo_screenheight() - d(90))
        self.window.geometry(f"{width}x{height}")
        self.window.minsize(min(d(720), width), min(d(560), height))
        self.window.transient(ui.root)
        self.window.configure(background=theme.PANEL)
        self.window.columnconfigure(0, weight=1)
        self.window.rowconfigure(1, weight=1)
        head = ttk.Frame(self.window, padding=d(18))
        head.grid(row=0, column=0, sticky="ew")
        head.columnconfigure(0, weight=1)
        ttk.Label(head, text="发送渠道", style="PageTitle.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(head, text="国服" if profile == "cn" else "国际服", style="Hint.TLabel").grid(row=0, column=1, padx=d(18))
        self.selector = ttk.Combobox(head, state="readonly", width=22)
        self.selector.grid(row=0, column=2)
        self.selector.bind("<<ComboboxSelected>>", self.select)
        body = ttk.Frame(self.window, padding=(d(18), 0))
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(body, background=theme.PANEL, highlightthickness=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(body, command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scroll.set)
        scroll.grid(row=0, column=1, sticky="ns")
        self.content = ttk.Frame(self.canvas, padding=(0, 0, d(12), 0))
        self.content.columnconfigure(0, weight=1)
        self.anchor = self.canvas.create_window(0, 0, window=self.content, anchor="nw")
        self.content.bind("<Configure>", lambda _: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self.anchor, width=e.width))
        foot = ttk.Frame(self.window, padding=d(18))
        foot.grid(row=2, column=0, sticky="ew")
        foot.columnconfigure(0, weight=1)
        self.status = tk.StringVar(self.window, "正在读取…")
        theme.wrapped_label(foot, textvariable=self.status, style="Hint.TLabel").grid(row=0, column=0, sticky="ew", padx=(0, d(12)))
        self.save_button = ttk.Button(foot, text="保存渠道", command=self.save)
        self.save_button.grid(row=0, column=1)
        self.reload_button = ttk.Button(foot, text="重新读取", command=self.reload)
        self.reload_button.grid(row=0, column=2, padx=d(8))
        self.test_button = ttk.Button(foot, text="发送测试通知", command=self.test)
        self.test_button.grid(row=0, column=3)
        self.session = RuntimeSession(RuntimePaths.discover(ROOT, profile), self.events.put)
        self.worker = self.session.worker
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.window.bind("<Destroy>", self.disposed, add="+")
        self.buttons()
        self.poll_token = self.window.after(80, self.poll)

    def buttons(self):
        allowed = self.connected and not self.busy and not self.closing
        self.selector.state(["!disabled"] if allowed else ["disabled"])
        self.save_button.state(["!disabled"] if allowed and self.dirty else ["disabled"])
        self.reload_button.state(["!disabled"] if allowed and self.description else ["disabled"])
        self.test_button.state(["!disabled"] if allowed and self.description and self.description.get("valid") and not self.dirty else ["disabled"])
        self.save_button.configure(text="保存设置" if self.current_id == "_settings" else "保存渠道", style="Accent.TButton" if self.dirty else "TButton")

    def request(self, method, callback, **params):
        if self.busy or self.closing or not self.connected: return
        self.busy, self.callback = True, callback
        def freeze(widget):
            if isinstance(widget, (ttk.Entry, ttk.Combobox, ttk.Checkbutton, ttk.Button)):
                self.frozen.append((widget, widget.state()))
                widget.state(["disabled"])
            elif isinstance(widget, tk.Text):
                self.frozen.append((widget, widget.cget("state")))
                widget.configure(state="disabled")
            for child in widget.winfo_children(): freeze(child)
        freeze(self.content)
        self.status.set("正在发送…" if method == "notification_test" else "正在读取…" if method == "notification_channel" else "正在保存…")
        self.session.send("rpc", method=method, tag=method, params=params)
        self.buttons()

    def loaded(self, result):
        self.channels = {"发送设置": "_settings", **{row["name"]: row["id"] for row in result}}
        self.selector.configure(values=list(self.channels))
        self.selector.set("发送设置")
        self.reload()

    def allow_leave(self):
        return not self.dirty or messagebox.askyesno("未保存修改", "放弃当前渠道未保存的修改？", parent=self.window)

    def select(self, _event=None):
        if self.busy: return
        if not self.allow_leave():
            self.selector.set(next(k for k, v in self.channels.items() if v == self.current_id))
            return
        self.current_id = self.channels[self.selector.get()]
        self.dirty = False
        self.reload()

    def reload(self):
        if self.allow_leave(): self.request("notification_channel", self.render, channel_id=self.current_id)

    def clear(self):
        for variable, token in self.traces: variable.trace_remove("write", token)
        self.traces.clear()
        if self.content.winfo_exists():
            for child in self.content.winfo_children(): child.destroy()
        self.inputs.clear()

    def render(self, result):
        self.clear()
        self.description = result
        self.current_id = result["id"]
        label = next((name for name, key in self.channels.items() if key == self.current_id), None)
        if label is not None: self.selector.set(label)
        self.dirty = False
        self.form = None
        d = lambda n: round(n * self.ui.ui_scale)
        if result["id"] == "_settings":
            self.form = ScalarForm(self.content, result["fields"], self.changed, self.ui.ui_scale)
            self.form.frame.grid(row=0, column=0, sticky="ew")
        else:
            self.enabled = tk.BooleanVar(self.content, result["enabled"])
            ttk.Checkbutton(self.content, text="使用此渠道", variable=self.enabled, command=self.changed, style="Config.TCheckbutton").grid(row=0, column=0, sticky="w", pady=(0, d(14)))
            theme.wrapped_label(self.content, text="已存内容留空保留；清除需勾选。", style="Hint.TLabel").grid(row=1, column=0, sticky="ew", pady=(0, d(12)))
            self.show_inputs = tk.BooleanVar(self.content, False)
            ttk.Checkbutton(self.content, text="显示本次输入", variable=self.show_inputs, command=self.visibility).grid(row=2, column=0, sticky="w", pady=(0, d(12)))
            row = 3
            if result["id"] == "QQ_BOT":
                theme.wrapped_label(self.content, text="收件方需已有 OpenID；QQ 网关绑定尚未接入。", style="Hint.TLabel").grid(row=row, column=0, sticky="ew", pady=(0, d(12)))
                row += 1
            for field in result["fields"]:
                line = ttk.Frame(self.content)
                line.grid(row=row, column=0, sticky="ew", pady=(0, d(18)))
                line.columnconfigure(0, minsize=d(205))
                line.columnconfigure(1, weight=1)
                ttk.Label(line, text=field["label"], wraplength=d(195)).grid(row=0, column=0, sticky="nw", padx=(0, d(12)), pady=d(4))
                if field["kind"] == "enum":
                    choice = Choice(line, field["options"], field["value"])
                    choice.widget.grid(row=0, column=1, sticky="ew")
                    self.trace(choice.variable)
                    self.inputs[field["name"]] = (choice.get, None)
                else:
                    container = ttk.Frame(line)
                    container.grid(row=0, column=1, sticky="ew")
                    if field["kind"] == "private_text":
                        widget = tk.Text(container, height=4, wrap="word", font=("Microsoft YaHei UI", 10), background=theme.FIELD,
                                         foreground=theme.TEXT, insertbackground=theme.ACCENT, padx=d(8), pady=d(6), borderwidth=0)
                        widget.pack(fill="x")
                        widget.insert("1.0", field["value"])
                        widget.edit_modified(False)
                        def modified(_event, text=widget):
                            if text.edit_modified(): text.edit_modified(False); self.changed()
                        widget.bind("<<Modified>>", modified)
                        reader = lambda text=widget: text.get("1.0", "end-1c")
                    else:
                        variable = tk.StringVar(container, field["value"])
                        widget = ttk.Entry(container, textvariable=variable, show="●")
                        widget.pack(fill="x")
                        self.trace(variable)
                        reader = variable.get
                    clear = tk.BooleanVar(container, False)
                    check = ttk.Checkbutton(container, text="清除已存值", variable=clear, command=self.changed)
                    check.pack(anchor="w", pady=(d(5), 0))
                    if not field["configured"]: check.state(["disabled"])
                    if field.get("format"): theme.wrapped_label(container, text=field["format"], style="Hint.TLabel").pack(fill="x", pady=(d(5), 0))
                    self.inputs[field["name"]] = (reader, clear, widget)
                row += 1
        enable_scroll(self.canvas, self.content)
        self.canvas.yview_moveto(0)
        self.status.set("已读取" if result["id"] == "_settings" else
                        "已启用" if result["enabled"] and result["valid"] else result.get("error") or "未启用")
        self.buttons()

    def trace(self, variable): self.traces.append((variable, variable.trace_add("write", lambda *_: self.changed())))

    def visibility(self):
        for entry in self.inputs.values():
            if len(entry) == 3 and isinstance(entry[2], ttk.Entry): entry[2].configure(show="" if self.show_inputs.get() else "●")

    def changed(self):
        self.dirty = True
        self.status.set("未保存")
        self.buttons()

    def values(self):
        if self.form is not None: return self.form.values()
        values = dict(enabled=self.enabled.get())
        for name, entry in self.inputs.items():
            values[name] = entry[0]()
            if entry[1] is not None: values["clear_" + name] = entry[1].get()
        return values

    def save(self):
        if self.description is None: return
        try: values = self.values()
        except ValueError as exc: self.status.set(str(exc)); return
        self.request("notification_channel_save", self.render, channel_id=self.current_id, values=values, revision=self.description["revision"])

    def test(self):
        if self.description is None or self.dirty: return
        def tested(result):
            outcome = result["test_result"]
            self.status.set("测试通知已发送" if outcome["success"] else "测试通知发送失败：" + outcome["detail"])
        self.request("notification_test", tested,
                     channel_id=self.current_id, revision=self.description["revision"])

    def poll(self):
        if self.closed: return
        try:
            while True:
                event = self.events.get_nowait()
                if event["type"] == "connected":
                    self.connected = True
                    self.request("notification_channels", self.loaded)
                elif event["type"] == "rpc_result":
                    self.busy = False
                    self.thaw()
                    callback, self.callback = self.callback, None
                    if not self.closing and callback:
                        try: callback(event["result"])
                        except Exception as exc: self.status.set("显示失败：" + str(exc))
                elif event["type"] == "error":
                    self.busy = False
                    self.thaw()
                    self.callback = None
                    self.status.set(event["message"])
                elif event["type"] == "closed":
                    self.closed = True
                    self.window.destroy()
                    return
                self.buttons()
        except queue.Empty: pass
        self.poll_token = self.window.after(80, self.poll)

    def thaw(self):
        for widget, state in self.frozen:
            if widget.winfo_exists():
                if isinstance(widget, tk.Text): widget.configure(state=state)
                else:
                    widget.state(["!disabled", "!readonly"])
                    widget.state(state)
        self.frozen.clear()

    def disposed(self, event):
        if event.widget is self.window:
            if self.poll_token is not None: self.window.after_cancel(self.poll_token); self.poll_token = None
            self.clear()
            self.enabled = self.show_inputs = self.status = None

    def close(self):
        if self.closing: return True
        if not self.allow_leave(): return False
        self.closing = True
        self.status.set("正在等待发送与进程退出…")
        self.session.close()
        self.buttons()
        if not self.worker.is_alive(): self.closed = True; self.window.destroy()
        return True


def open_channels(ui, profile):
    existing = getattr(ui, "notification_window", None)
    if existing is not None and not existing.closed:
        if existing.profile != profile:
            if not existing.close(): return existing
            def reopen():
                if ui.closing: return
                if existing.closed and not existing.worker.is_alive(): open_channels(ui, profile)
                else: ui.root.after(80, reopen)
            ui.root.after(80, reopen)
            return existing
        existing.window.deiconify()
        existing.window.lift()
        return existing
    ui.notification_window = ChannelsWindow(ui, profile)
    return ui.notification_window
