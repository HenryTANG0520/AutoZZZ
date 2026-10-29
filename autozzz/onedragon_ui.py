"""AutoZZZ 原生条件策略编辑窗口。"""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from pathlib import Path
from typing import Any, Callable

from . import theme
from .config import ROOT
from .onedragon_runtime import OneDragonClient, RequestPending, RuntimePaths


class StrategyWindow:
    def __init__(self, ui: Any):
        self.ui = ui
        self.window = tk.Toplevel(ui.root)
        self.window.title("AutoZZZ · 条件策略")
        d = lambda value: round(value * ui.ui_scale)
        width = min(d(960), ui.root.winfo_screenwidth() - d(40))
        height = min(d(720), ui.root.winfo_screenheight() - d(70))
        self.window.geometry(f"{width}x{height}")
        self.window.minsize(min(d(760), width), min(d(560), height))
        self.window.configure(background=theme.PANEL)
        self.window.columnconfigure(0, weight=1)
        self.window.rowconfigure(2, weight=1)
        self.busy = self.closing = self.closed = self.dirty = False
        self.worker: threading.Thread | None = None
        self.events: queue.Queue = queue.Queue()
        self.controls: list[ttk.Widget] = []
        self.names: list[str] = []
        self.loaded_name = ""
        self.merged = True
        self.policy_kind='auto_battle'
        self.kind=tk.StringVar(self.window,'自动战斗')
        self.name = tk.StringVar(self.window)
        self.status = tk.StringVar(self.window, "正在读取策略…")
        server = "global" if ui.server.get() == "国际服" else "cn"
        self.paths = RuntimePaths.discover(ROOT, server)
        self.poll_token: str | None = None
        header = ttk.Frame(self.window, padding=16)
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(1, weight=1)
        ttk.Label(header, text="条件策略", style="PageTitle.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(header, text="国服" if server=="cn" else "国际服", style="Hint.TLabel").grid(row=0, column=1, sticky="e")
        form = ttk.Frame(self.window, padding=(16, 0, 16, 12))
        form.grid(row=1, column=0, sticky="ew")
        form.columnconfigure(1, weight=1)
        ttk.Label(form, text="策略名称").grid(row=0, column=0, padx=(0, 12))
        self.selector = ttk.Combobox(form, textvariable=self.name, state="normal")
        self.selector.grid(row=0, column=1, sticky="ew")
        self.controls.append(self.selector)
        self._button(form, "读取", self.load).grid(row=0, column=2, padx=(12, 0))
        self._button(form, "导入 YAML…", self.import_file).grid(row=0, column=3, padx=(8, 0))
        ttk.Label(form,text='策略类型').grid(row=1,column=0,padx=(0,12),pady=(12,0))
        self.kind_selector=ttk.Combobox(form,textvariable=self.kind,values=('自动战斗','闪避'),state='readonly',width=16)
        self.kind_selector.grid(row=1,column=1,sticky='w',pady=(12,0));self.controls.append(self.kind_selector)
        self.kind_selector.bind('<<ComboboxSelected>>',self.change_kind)
        area = ttk.Frame(self.window, padding=(16, 0))
        area.grid(row=2, column=0, sticky="nsew")
        self.editor = tk.Text(area, wrap="none", undo=True, font=("Consolas", 11),
                              background=theme.FIELD, foreground=theme.TEXT,
                              insertbackground=theme.ACCENT, borderwidth=0, padx=12, pady=12)
        vertical = ttk.Scrollbar(area, command=self.editor.yview)
        horizontal = ttk.Scrollbar(area, orient="horizontal", command=self.editor.xview)
        self.editor.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        area.columnconfigure(0, weight=1)
        area.rowconfigure(0, weight=1)
        self.editor.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        self.editor.bind("<<Modified>>", self._modified)
        footer = ttk.Frame(self.window, padding=16)
        footer.grid(row=3, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)
        theme.wrapped_label(footer, text="内置策略需改名另存，保留作者信息。", style="Hint.TLabel").grid(row=0, column=0, sticky="ew")
        theme.wrapped_label(footer, textvariable=self.status, style="Hint.TLabel").grid(row=1, column=0, sticky="ew", pady=(6, 12))
        buttons = ttk.Frame(footer)
        buttons.grid(row=2, column=0, sticky="w")
        self._button(buttons, "校验草稿", self.validate).pack(side="left", padx=(0, 8))
        self._button(buttons, "保存策略", self.save).pack(side="left", padx=(0, 8))
        self.default_button=self._button(buttons, "设为条件战斗默认", self.select)
        self.default_button.pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="关闭", command=self.close).pack(side="left")
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.window.bind("<Destroy>", self._destroyed, add="+")
        self._run(self._initial, self._loaded)
        self._poll()

    def _button(self, parent: ttk.Frame, text: str, command: Callable) -> ttk.Button:
        button = ttk.Button(parent, text=text, command=command)
        self.controls.append(button)
        return button

    def _modified(self, _event: Any = None) -> None:
        if self.editor.edit_modified():
            self.dirty = True
            self.editor.edit_modified(False)

    def _set_text(self, content: str) -> None:
        self.editor.configure(state="normal")
        self.editor.delete("1.0", "end")
        self.editor.insert("1.0", content)
        self.editor.edit_modified(False)
        self.dirty = False
        if self.busy:
            self.editor.configure(state="disabled")

    def _run(self, operation: Callable, success: Callable) -> None:
        if self.busy or self.closed:
            return
        self.busy = True
        self.status.set("正在处理…")
        for control in self.controls:
            control.state(["disabled"])
        self.editor.configure(state="disabled")

        def work() -> None:
            client = OneDragonClient(self.paths)
            try:
                client.start()
                result = operation(client)
                self.events.put((success, result, None))
            except Exception as exc:
                self.events.put((None, None, str(exc)))
            finally:
                try:
                    while True:
                        try:
                            client.close(timeout=5)
                            break
                        except RequestPending:
                            self.events.put((None, None, "仍在等待原进程退出…"))
                except Exception as exc:
                    self.events.put((None, None, str(exc)))
                self.events.put(("done", None, None))

        self.worker = threading.Thread(target=work, name="AutoZZZ-Strategy-Configuration", daemon=False)
        self.worker.start()

    @staticmethod
    def _call(client: OneDragonClient, method: str, **params: Any) -> Any:
        request = client.submit(method, **params)
        while True:
            try:
                return client.result(request, timeout=5)
            except RequestPending:
                continue  # 查询同一请求；不能因等待超时重复保存或初始化。

    def _initial(self, client: OneDragonClient,kind: str = 'auto_battle') -> dict:
        policies = self._call(client, "policies",kind=kind)
        config = self._call(client, "configuration", app_id="battle_assistant")
        name = config["fields"]["auto_battle_config" if kind=='auto_battle' else 'dodge_assistant_config']
        merged = config["fields"]["use_merged_file"]
        return dict(policies=policies, merged=merged,kind=kind,
                    policy=self._call(client, "policy_read", name=name, merged=merged,kind=kind))

    def change_kind(self,_event=None):
        selected={'自动战斗':'auto_battle','闪避':'dodge'}[self.kind.get()]
        if self.busy or selected==self.policy_kind:return
        if self.dirty and not messagebox.askokcancel('放弃草稿','切换策略类型将替换当前未保存草稿。',parent=self.window):
            self.kind.set('自动战斗' if self.policy_kind=='auto_battle' else '闪避');return
        self._run(lambda client:self._initial(client,selected),self._loaded)

    def _loaded(self, result: dict) -> None:
        if "policies" in result:
            self.policy_kind=result.get('kind','auto_battle')
            self.kind.set('自动战斗' if self.policy_kind=='auto_battle' else '闪避')
            self.default_button.configure(text='设为条件战斗默认' if self.policy_kind=='auto_battle' else '设为闪避默认')
            self.names = [policy["name"] for policy in result["policies"]]
            self.selector.configure(values=self.names)
            self.merged = result["merged"]
        policy = result.get("policy", result)
        self.name.set(policy["name"])
        self.loaded_name = policy["name"]
        self._set_text(policy["content"])
        self.status.set("已读取 · 尚未修改")

    def load(self) -> None:
        if self.dirty and not messagebox.askokcancel("放弃草稿", "读取其他策略将替换当前未保存草稿。", parent=self.window):
            return
        name = self.name.get()
        self._run(lambda client: self._call(client, "policy_read", name=name, merged=self.merged,kind=self.policy_kind), self._loaded)

    def import_file(self) -> None:
        if self.dirty and not messagebox.askokcancel("放弃草稿", "导入将替换当前未保存草稿。", parent=self.window):
            return
        path = filedialog.askopenfilename(parent=self.window, filetypes=[("YAML 条件策略", "*.yml *.yaml")])
        if path:
            try:
                if Path(path).stat().st_size > 1024 * 1024:
                    raise ValueError("策略文件不能超过 1 MiB。")
                content = Path(path).read_text(encoding="utf-8-sig")
                if len(content.encode("utf-8")) > 1024 * 1024:
                    raise ValueError("策略文件不能超过 1 MiB。")
                self._set_text(content)
                self.name.set(Path(path).stem)
                self.loaded_name = ""
                self.dirty = True
                self.status.set("已导入 · 未保存")
            except (OSError, ValueError) as exc:
                self.status.set(str(exc))

    def validate(self) -> None:
        content = self.editor.get("1.0", "end-1c")
        # 草稿校验使用单独方法，不写用户策略。
        self._run(lambda client: self._call(client, "policy_check", content=content,kind=self.policy_kind),
                  lambda result: self.status.set(f"校验通过 · {len(result['scenes'])} 个场景"))

    def save(self) -> None:
        name, content = self.name.get(), self.editor.get("1.0", "end-1c")
        def saved(result: dict) -> None:
            self.dirty = False
            self.loaded_name = name
            self.names = sorted(set(self.names + [name]))
            self.selector.configure(values=self.names)
            self.status.set(f"已保存 · {len(result['scenes'])} 个场景")
        self._run(lambda client: self._call(client, "policy_save", name=name, content=content,kind=self.policy_kind), saved)

    def select(self) -> None:
        if self.dirty or self.name.get() != self.loaded_name:
            self.status.set("请先读取或保存该策略，再设为默认。")
            return
        name = self.name.get()
        field='auto_battle_config' if self.policy_kind=='auto_battle' else 'dodge_assistant_config'
        self._run(lambda client: self._call(client, "set_battle_options", values={field: name}),
                  lambda _result: self.status.set('已设为条件战斗默认策略' if self.policy_kind=='auto_battle' else '已设为闪避默认策略'))

    def _poll(self) -> None:
        while not self.events.empty():
            callback, result, error = self.events.get_nowait()
            if callback == "done":
                self.busy = False
                for control in self.controls:
                    control.state(["!disabled"])
                self.kind_selector.configure(state='readonly')
                self.editor.configure(state="normal")
                if self.closing:
                    self.window.destroy()
                    return
            elif error:
                self.kind.set('自动战斗' if self.policy_kind=='auto_battle' else '闪避')
                self.status.set(error)
            elif callback and not self.closing:
                callback(result)
        if not self.closed:
            self.poll_token = self.window.after(80, self._poll)

    def close(self) -> bool:
        if self.closed:
            return True
        if not self.busy and self.dirty:
            if not messagebox.askokcancel("放弃草稿", "关闭后将放弃未保存的策略修改。", parent=self.window):
                return False
        self.closing = True
        if self.busy:
            self.status.set("等待当前配置操作和进程退出…")
        else:
            self.window.destroy()
        return True

    def _destroyed(self, event: Any) -> None:
        if event.widget is self.window:
            self.closed = True
            if self.poll_token:
                self.window.after_cancel(self.poll_token)


def open_strategies(ui: Any) -> None:
    current = getattr(ui, "condition_strategies", None)
    if current is not None and not current.closed:
        current.window.deiconify()
        current.window.lift()
        return
    ui.condition_strategies = StrategyWindow(ui)
