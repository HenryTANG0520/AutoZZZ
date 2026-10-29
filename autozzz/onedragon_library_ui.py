"""原生方案与路线资料库：草稿、表单、地图选点和可恢复归档。"""
from __future__ import annotations

import base64
import copy
import io
from pathlib import Path
import queue
import time
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from . import theme
from .config import ROOT
from .onedragon_forms import Choice, ScalarForm, enable_scroll
from .onedragon_runtime import RuntimePaths
from .onedragon_session import RuntimeSession
from .onedragon_map_ui import MapEditor

KINDS = {"迷失之地方案": "lost_void_challenge", "枯萎之都方案": "hollow_zero_challenge", "巡逻路线组": "world_patrol_route_list", "巡逻路线": "world_patrol_route", "大地图": "world_patrol_map"}


class DiagnosticsDialog:
    def __init__(self, parent, issues, scale):
        d = lambda n: round(n * scale)
        self.window = tk.Toplevel(parent); self.window.title("资料问题"); self.window.transient(parent)
        self.window.configure(background=theme.PANEL)
        self.window.geometry(f"{min(d(760), parent.winfo_screenwidth()-d(50))}x{min(d(460), parent.winfo_screenheight()-d(100))}")
        self.window.columnconfigure(0, weight=1); self.window.rowconfigure(0, weight=1)
        body = ttk.Frame(self.window, padding=d(18)); body.grid(row=0, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1); body.rowconfigure(0, weight=1)
        self.text = tk.Text(body, wrap="word", background=theme.PANEL, foreground=theme.TEXT,
                            relief="flat", padx=d(8), pady=d(8), font=("Microsoft YaHei UI", 10))
        self.text.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(body, command=self.text.yview); scroll.grid(row=0, column=1, sticky="ns")
        self.text.configure(yscrollcommand=scroll.set)
        self.text.insert("1.0", "\n\n".join(row["file"] + "\n" + row["error"] for row in issues))
        self.text.configure(state="disabled")
        ttk.Button(body, text="关闭", command=self.window.destroy).grid(row=1, column=0, columnspan=2, sticky="e", pady=(d(12), 0))
        self.window.bind("<Escape>", lambda _: self.window.destroy()); self.window.grab_set()


class SelectionDialog:
    def __init__(self, parent, title, rows, accept, scale, confirm_label="打开恢复草稿"):
        d = lambda n: round(n * scale)
        self.rows, self.accept = rows, accept
        self.window = tk.Toplevel(parent)
        self.window.title(title); self.window.transient(parent)
        self.window.configure(background=theme.PANEL)
        self.window.geometry(f"{min(d(680), parent.winfo_screenwidth()-d(50))}x{min(d(430), parent.winfo_screenheight()-d(100))}")
        self.window.columnconfigure(0, weight=1); self.window.rowconfigure(0, weight=1)
        body = ttk.Frame(self.window, padding=d(16)); body.grid(row=0, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1); body.rowconfigure(0, weight=1)
        self.table = ttk.Treeview(body, columns=("name", "time"), show="headings", selectmode="browse", style="Config.Treeview")
        self.table.heading("name", text="名称"); self.table.heading("time", text="归档时间")
        self.table.column("name", width=d(320), minwidth=d(100)); self.table.column("time", width=d(220), minwidth=d(100))
        self.table.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(body, command=self.table.yview); scroll.grid(row=0, column=1, sticky="ns"); self.table.configure(yscrollcommand=scroll.set)
        for i, row in enumerate(rows): self.table.insert("", "end", iid=str(i), values=(row["key"], row["created_at"]))
        if rows: self.table.selection_set("0")
        actions = ttk.Frame(self.window, padding=d(16)); actions.grid(row=1, column=0, sticky="e")
        ttk.Button(actions, text=confirm_label, command=self.confirm).pack(side="left", padx=(0, d(8)))
        ttk.Button(actions, text="取消", command=self.window.destroy).pack(side="left")
        self.table.bind("<Double-1>", lambda _: self.confirm())
        self.window.bind("<Return>", lambda _: self.confirm())
        self.window.bind("<Destroy>", self.disposed, add="+")
        self.window.grab_set()

    def confirm(self):
        selected = self.table.selection()
        if not selected: return
        row, callback = self.rows[int(selected[0])], self.accept
        self.window.destroy(); callback(row)

    def disposed(self, event):
        if event.widget is self.window: self.accept = None


class CoordinateDialog:
    def __init__(self, parent, row, width, height, accept, scale):
        self.accept = accept
        self.window = tk.Toplevel(parent); self.window.title("移动坐标"); self.window.transient(parent)
        self.window.configure(background=theme.PANEL)
        d = lambda n: round(n * scale)
        self.window.columnconfigure(0, weight=1)
        body = ttk.Frame(self.window, padding=d(18)); body.grid(row=0, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        values = row["data"] if row else [0, 0]
        self.form = ScalarForm(body, [dict(name=key, label=key.upper() + "（地图像素）", value=values[i], kind="int", options=[], minimum=0, maximum=maximum-1)
                                      for i, (key, maximum) in enumerate((("x", width), ("y", height)))], lambda: None, scale)
        self.form.frame.grid(row=0, column=0, sticky="ew")
        self.status = tk.StringVar(self.window)
        theme.wrapped_label(body, textvariable=self.status, style="Hint.TLabel").grid(row=1, column=0, sticky="ew")
        actions = ttk.Frame(body); actions.grid(row=2, column=0, sticky="e", pady=(d(10), 0))
        ttk.Button(actions, text="确定", command=self.confirm).pack(side="left", padx=(0, d(8)))
        ttk.Button(actions, text="取消", command=self.window.destroy).pack(side="left")
        self.window.bind("<Return>", lambda _: self.confirm())
        self.window.bind("<Destroy>", self.disposed, add="+")
        self.window.grab_set()

    def confirm(self):
        try:
            from .onedragon_library_rules import coordinate
            values = self.form.values()
            for field in self.form.fields: coordinate(values[field["name"]], field["maximum"]+1)
        except ValueError as exc: self.status.set(str(exc)); return
        callback = self.accept
        self.window.destroy(); callback(dict(op_type="move", data=[str(values["x"]), str(values["y"])]))

    def disposed(self, event):
        if event.widget is self.window: self.form = self.status = self.accept = None


class RouteEditor:
    def __init__(self, parent, document, preview, changed, scale, locate=None, debug=None):
        from PIL import Image
        self.changed, self.preview, self.scale = changed, preview, scale
        self.rows = copy.deepcopy(document["data"]["op_list"])
        self.data = copy.deepcopy(document["data"])
        self.enabled, self.zoom, self.photo, self.dialog = True, 1., None, None
        self.traces = []
        self.candidate = None
        self.image = Image.open(io.BytesIO(base64.b64decode(preview["png"], validate=True))).convert("RGB")
        self.frame = ttk.Frame(parent); self.frame.columnconfigure(0, weight=1)
        self.frame.bind("<Destroy>", self.disposed, add="+")
        d = lambda n: round(n * scale)
        top = ttk.Frame(self.frame); top.grid(row=0, column=0, sticky="ew", pady=(0, d(12))); top.columnconfigure(1, weight=1)
        ttk.Label(top, text="起始传送点").grid(row=0, column=0, padx=(0, d(12)))
        self.teleport = Choice(top, [dict(label=p, value=p) for p in preview["teleport_points"]], self.data["tp_name"])
        self.teleport.widget.grid(row=0, column=1, sticky="ew")
        def teleport_changed(*_): self.changed(); self.draw()
        self.traces.append((self.teleport.variable, self.teleport.variable.trace_add("write", teleport_changed)))
        tools = ttk.Frame(self.frame); tools.grid(row=1, column=0, sticky="ew", pady=(0, d(6)))
        ttk.Label(tools, text=f"{preview['width']} × {preview['height']} 像素", style="Hint.TLabel").pack(side="left")
        ttk.Button(tools, text="适应地图", command=self.fit).pack(side="right")
        map_frame = ttk.Frame(self.frame); map_frame.grid(row=4, column=0, sticky="ew"); map_frame.columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(map_frame, height=d(270), background="#141a1b", highlightthickness=0)
        self.canvas.grid(row=0, column=0, sticky="ew")
        horizontal = ttk.Scrollbar(map_frame, orient="horizontal", command=self.canvas.xview); horizontal.grid(row=1, column=0, sticky="ew")
        vertical = ttk.Scrollbar(map_frame, command=self.canvas.yview); vertical.grid(row=0, column=1, sticky="ns")
        self.canvas.configure(xscrollcommand=horizontal.set, yscrollcommand=vertical.set)
        self.canvas.bind("<Configure>", lambda _: self.draw())
        self.canvas.bind("<Button-1>", self.add_at)
        self.canvas.bind("<MouseWheel>", self.wheel)
        self.canvas.bind("<ButtonPress-3>", lambda e: self.canvas.scan_mark(e.x, e.y))
        self.canvas.bind("<B3-Motion>", lambda e: self.canvas.scan_dragto(e.x, e.y, gain=1))
        theme.wrapped_label(self.frame, text="点击地图添加移动点 · 滚轮缩放 · 右键拖动", style="Hint.TLabel").grid(row=5, column=0, sticky="ew", pady=(d(6), d(12)))
        table_frame = ttk.Frame(self.frame); table_frame.grid(row=6, column=0, sticky="ew"); table_frame.columnconfigure(0, weight=1)
        self.table = ttk.Treeview(table_frame, columns=("step", "x", "y"), show="headings", height=5, selectmode="browse", style="Config.Treeview")
        for key, label in (("step", "移动点"), ("x", "X"), ("y", "Y")):
            self.table.heading(key, text=label); self.table.column(key, width=d(90), minwidth=d(45))
        self.table.grid(row=0, column=0, sticky="ew")
        scroll = ttk.Scrollbar(table_frame, command=self.table.yview); scroll.grid(row=0, column=1, sticky="ns"); self.table.configure(yscrollcommand=scroll.set)
        self.table.bind("<<TreeviewSelect>>", lambda _: self.draw())
        self.table.bind("<Double-1>", lambda _: self.edit())
        buttons = ttk.Frame(self.frame); buttons.grid(row=7, column=0, sticky="w", pady=(d(8), 0))
        self.buttons = {}
        for name, command in (("添加坐标…", lambda: self.edit(True)), ("编辑…", self.edit), ("上移", lambda: self.move(-1)), ("下移", lambda: self.move(1)), ("移除", self.remove)):
            button = ttk.Button(buttons, text=name, command=command); button.pack(side="left", padx=(0, d(6))); self.buttons[name] = button
        record = ttk.Frame(self.frame); record.grid(row=2, column=0, sticky="w", pady=(d(6),0))
        if locate is not None:
            for name, command in (("从游戏识别", lambda: locate(False)), ("从截图识别…", lambda: locate(True)), ("追加到路线", self.adopt)):
                button=ttk.Button(record,text=name,command=command); button.pack(side="left",padx=(0,d(6)));self.buttons[name]=button
        if debug is not None:
            button=ttk.Button(record,text="调试路线…",command=debug);button.pack(side="left");self.buttons["调试路线…"]=button
        self.candidate_text = tk.StringVar(self.frame)
        theme.wrapped_label(self.frame,textvariable=self.candidate_text,style="Hint.TLabel").grid(row=3,column=0,sticky="ew",pady=(d(7),d(10)))
        self.refresh()
        self.set_candidate(None)

    def selected(self):
        values = self.table.selection()
        return int(values[0]) if values else None

    def refresh(self, selected=None):
        self.table.delete(*self.table.get_children())
        for i, row in enumerate(self.rows): self.table.insert("", "end", iid=str(i), values=(i+1, *row["data"]))
        if self.rows:
            index = min(max(0, selected or 0), len(self.rows)-1); self.table.selection_set(str(index)); self.table.see(str(index))
        self.draw()

    def draw(self):
        if not self.canvas.winfo_exists(): return
        from PIL import ImageTk, Image
        fit = min(max(1, self.canvas.winfo_width()-24) / self.preview["width"], max(1, self.canvas.winfo_height()-24) / self.preview["height"])
        self.ratio = min(fit * self.zoom, (12_000_000 / (self.preview["width"] * self.preview["height"])) ** .5)
        size = tuple(max(1, round(self.preview[k] * self.ratio)) for k in ("width", "height"))
        self.photo = ImageTk.PhotoImage(self.image.resize(size, Image.Resampling.NEAREST), master=self.canvas)
        self.canvas.delete("all"); self.canvas.create_image(12, 12, image=self.photo, anchor="nw", tags="map")
        self.canvas.configure(scrollregion=(0, 0, size[0]+24, size[1]+24))
        points = []
        try: teleport = self.teleport.get()
        except ValueError: teleport = None
        icon = next((row for row in self.preview["icons"] if row["icon_name"] == teleport), None)
        if icon: points.append(tuple(12 + v * self.ratio for v in icon["tp_pos"]))
        selected = self.selected()
        for i, row in enumerate(self.rows):
            try: x, y = [12 + int(v) * self.ratio for v in row["data"]]
            except (ValueError, TypeError): continue
            points.append((x, y)); color = "#f6ce43" if i == selected else "#58d8a7"
            self.canvas.create_oval(x-4, y-4, x+4, y+4, fill=color, outline="")
            self.canvas.create_text(x+7, y-7, anchor="sw", text=str(i+1), fill=color)
        if len(points) > 1: self.canvas.create_line(*[v for point in points for v in point], fill="#58d8a7", width=2, tags="route")
        self.canvas.tag_lower("route")
        if self.canvas.find_withtag("route"): self.canvas.tag_raise("route", "map")
        if self.candidate is not None:
            x,y=[12+v*self.ratio for v in self.candidate["position"]]
            self.canvas.create_oval(x-8,y-8,x+8,y+8,outline="#ffb454",width=3)
            self.canvas.create_text(x+10,y+10,anchor="nw",text="候选",fill="#ffb454")

    def anchor(self):
        from .onedragon_library_rules import coordinate
        if self.rows: point=self.rows[-1]["data"]
        else:
            name=self.teleport.get()
            point=next(row["tp_pos"] for row in self.preview["icons"] if row["icon_name"]==name)
        return [coordinate(value,self.preview[key]) for value,key in zip(point,("width","height"))]

    def set_candidate(self,result):
        self.candidate=copy.deepcopy(result) if result and result.get("found") else None
        if "追加到路线" in self.buttons:self.buttons["追加到路线"].state(["!disabled"] if self.candidate else ["disabled"])
        self.candidate_text.set(f"候选坐标 {self.candidate['position'][0]}, {self.candidate['position'][1]} · 匹配值 {self.candidate['match_value']}" if self.candidate else "")
        self.draw()

    def adopt(self):
        if not self.enabled or self.candidate is None:return
        self.rows.append(dict(op_type="move",data=list(map(str,self.candidate["position"]))))
        self.set_candidate(None);self.refresh(len(self.rows)-1);self.changed()

    def fit(self): self.zoom=1.; self.draw(); self.canvas.xview_moveto(0); self.canvas.yview_moveto(0)

    def wheel(self, event):
        self.zoom = min(8., max(.25, self.zoom * (1.2 if event.delta > 0 else 1/1.2))); self.draw()
        return "break"

    def add_at(self, event):
        if not self.enabled: return
        x, y = (self.canvas.canvasx(event.x)-12)/self.ratio, (self.canvas.canvasy(event.y)-12)/self.ratio
        if not 0 <= x < self.preview["width"] or not 0 <= y < self.preview["height"]: return
        self.rows.append(dict(op_type="move", data=[str(min(self.preview["width"]-1, round(x))), str(min(self.preview["height"]-1, round(y)))]))
        self.refresh(len(self.rows)-1); self.changed()

    def edit(self, new=False):
        if not self.enabled: return
        index = self.selected()
        if not new and index is None: return
        if self.dialog is not None and self.dialog.window.winfo_exists(): self.dialog.window.lift(); return
        def accept(row):
            if new: self.rows.append(row)
            else: self.rows[index] = row
            self.refresh(len(self.rows)-1 if new else index); self.changed()
        self.dialog = CoordinateDialog(self.frame.winfo_toplevel(), None if new else self.rows[index], self.preview["width"], self.preview["height"], accept, self.scale)

    def move(self, offset):
        index = self.selected()
        if self.enabled and index is not None and 0 <= index+offset < len(self.rows):
            self.rows[index], self.rows[index+offset] = self.rows[index+offset], self.rows[index]
            self.refresh(index+offset); self.changed()

    def remove(self):
        index = self.selected()
        if self.enabled and index is not None: self.rows.pop(index); self.refresh(index); self.changed()

    def values(self): return dict(self.data, tp_name=self.teleport.get(), op_list=copy.deepcopy(self.rows))

    def disposed(self, event):
        if event.widget is not self.frame: return
        for variable, token in self.traces: variable.trace_remove("write", token)
        self.traces.clear()
        self.teleport.variable = None
        self.photo = self.image = self.changed = None
        self.candidate_text=None


class GroupEditor:
    def __init__(self, parent, document, catalog, changed, scale):
        self.frame = ttk.Frame(parent); self.frame.columnconfigure(0, weight=1)
        self.rows = list(document["data"]["route_items"])
        self.key, self.changed, self.enabled = document["key"], changed, True
        self.routes = {row["key"]: row for row in catalog["items"] if row["kind"] == "world_patrol_route"}
        d = lambda n: round(n * scale)
        self.kind = Choice(self.frame, [dict(label="按顺序执行", value="whitelist"), dict(label="排除这些路线", value="blacklist")], document["data"]["list_type"])
        self.kind.widget.grid(row=0, column=0, sticky="ew", pady=(0, d(10)))
        self.trace = self.kind.variable.trace_add("write", lambda *_: changed())
        self.table = ttk.Treeview(self.frame, columns=("route",), show="headings", height=6, selectmode="browse", style="Config.Treeview")
        self.table.heading("route", text="路线顺序"); self.table.column("route", width=d(390), minwidth=d(120))
        self.table.grid(row=1, column=0, sticky="ew")
        scroll = ttk.Scrollbar(self.frame, command=self.table.yview); scroll.grid(row=1, column=1, sticky="ns"); self.table.configure(yscrollcommand=scroll.set)
        actions = ttk.Frame(self.frame); actions.grid(row=2, column=0, sticky="w", pady=(d(8), d(18)))
        for name, command in (("上移", lambda: self.move(-1)), ("下移", lambda: self.move(1)), ("移除", self.remove)):
            ttk.Button(actions, text=name, command=command).pack(side="left", padx=(0, d(6)))
        self.areas = [dict(label=a["entry_name"] + " · " + a["name"], value=a["id"]) for a in catalog["areas"] if any(r["area_id"] == a["id"] for r in self.routes.values())]
        self.area = Choice(self.frame, self.areas, self.areas[0]["value"] if self.areas else "")
        self.area.widget.grid(row=3, column=0, sticky="ew", pady=(0, d(8)))
        self.area.widget.bind("<<ComboboxSelected>>", lambda _: self.available())
        self.source = ttk.Treeview(self.frame, columns=("route",), show="headings", height=5, selectmode="extended", style="Config.Treeview")
        self.source.heading("route", text="区域内可选路线"); self.source.column("route", width=d(390), minwidth=d(120))
        self.source.grid(row=4, column=0, sticky="ew")
        scroll = ttk.Scrollbar(self.frame, command=self.source.yview); scroll.grid(row=4, column=1, sticky="ns"); self.source.configure(yscrollcommand=scroll.set)
        actions = ttk.Frame(self.frame); actions.grid(row=5, column=0, sticky="w", pady=(d(8), 0))
        ttk.Button(actions, text="添加选中", command=self.add).pack(side="left", padx=(0, d(6)))
        ttk.Button(actions, text="添加整个区域", command=lambda: self.add(True)).pack(side="left")
        self.source.bind("<Double-1>", lambda _: self.add())
        self.frame.bind("<Destroy>", self.disposed, add="+")
        self.refresh(); self.available()

    def refresh(self, selected=0):
        self.table.delete(*self.table.get_children())
        for i, key in enumerate(self.rows): self.table.insert("", "end", iid=str(i), values=(self.routes.get(key, {}).get("name", "缺失 · " + key),))
        if self.rows: self.table.selection_set(str(min(max(0, selected), len(self.rows)-1)))

    def available(self):
        self.source.delete(*self.source.get_children())
        if not self.areas: return
        for key, route in self.routes.items():
            if route["area_id"] == self.area.get(): self.source.insert("", "end", iid=key, values=(route["name"],))

    def add(self, entire=False):
        if not self.enabled: return
        keys = self.source.get_children() if entire else self.source.selection()
        additions = [key for key in keys if key not in self.rows]
        if additions: self.rows.extend(additions); self.refresh(len(self.rows)-1); self.changed()

    def move(self, offset):
        selected = self.table.selection()
        if not self.enabled or not selected: return
        index = int(selected[0])
        if 0 <= index+offset < len(self.rows):
            self.rows[index], self.rows[index+offset] = self.rows[index+offset], self.rows[index]
            self.refresh(index+offset); self.changed()

    def remove(self):
        selected = self.table.selection()
        if self.enabled and selected: index=int(selected[0]); self.rows.pop(index); self.refresh(index); self.changed()

    def values(self): return dict(name=self.key, list_type=self.kind.get(), route_items=list(self.rows))

    def disposed(self, event):
        if event.widget is self.frame:
            self.kind.variable.trace_remove("write", self.trace)
            self.kind.variable = self.area.variable = self.changed = None


class LibraryWindow:
    def __init__(self, ui, profile, kind="lost_void_challenge"):
        self.ui, self.profile, self.kind = ui, profile, kind
        self.busy = self.connected = self.closed = self.closing = self.dirty = False
        self.document, self.editor, self.callback, self.dialog = None, None, None, None
        self.catalog, self.frozen, self.events = dict(items=[], archives=[], areas=[]), [], queue.Queue()
        self.capture=None;self.capture_cancel=False;self.capture_pause=None;self.next_capture_poll=0.
        self.map_view=None
        self.window = tk.Toplevel(ui.root); self.window.title("AutoZZZ · 方案与路线"); self.window.transient(ui.root)
        self.window.configure(background=theme.PANEL)
        d = lambda n: round(n * ui.ui_scale)
        self.window.geometry(f"{min(d(1100), ui.root.winfo_screenwidth()-d(50))}x{min(d(810), ui.root.winfo_screenheight()-d(90))}")
        self.window.minsize(min(d(900), ui.root.winfo_screenwidth()-d(50)), min(d(650), ui.root.winfo_screenheight()-d(90)))
        self.window.columnconfigure(0, weight=1); self.window.rowconfigure(1, weight=1)
        header = ttk.Frame(self.window, padding=d(18)); header.grid(row=0, column=0, sticky="ew"); header.columnconfigure(0, weight=1)
        ttk.Label(header, text="方案与路线", style="PageTitle.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(header, text="国服" if profile == "cn" else "国际服", style="Hint.TLabel").grid(row=0, column=1, padx=d(16))
        self.menu_button = ttk.Menubutton(header, text="资料操作")
        self.menu = tk.Menu(self.menu_button, tearoff=False)
        self.menu_button.configure(menu=self.menu); self.menu_button.grid(row=0, column=2)
        for label, command in (("新建…", self.new), ("另存为…", self.copy), ("改名…", self.rename), ("导入 YAML…", self.import_file), ("导出 YAML…", self.export_file), ("从归档恢复…", self.archives), ("归档 / 恢复内置", self.remove)):
            self.menu.add_command(label=label, command=command)
        self.menu.add_command(label="资料问题…", command=self.diagnostics)
        self.menu.add_command(label="恢复中断的地图保存…",command=self.recover_map)
        body = ttk.Frame(self.window, padding=(d(18), 0)); body.grid(row=1, column=0, sticky="nsew"); body.columnconfigure(1, weight=1); body.rowconfigure(0, weight=1)
        nav = ttk.Frame(body); nav.grid(row=0, column=0, sticky="ns", padx=(0, d(18))); nav.rowconfigure(2, weight=1)
        self.kind_var = tk.StringVar(self.window, next(label for label, value in KINDS.items() if value == kind))
        self.selector = ttk.Combobox(nav, textvariable=self.kind_var, values=list(KINDS), state="readonly", width=19)
        self.selector.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, d(10))); self.selector.bind("<<ComboboxSelected>>", self.change_kind)
        route_filter = ttk.Frame(nav); route_filter.grid(row=1, column=0, columnspan=2, sticky="ew")
        self.area_var = tk.StringVar(self.window)
        self.area_selector = ttk.Combobox(route_filter, textvariable=self.area_var, state="readonly", width=19)
        self.area_selector.pack(fill="x", pady=(0, d(10))); self.area_selector.bind("<<ComboboxSelected>>", self.change_area)
        self.table = ttk.Treeview(nav, show="tree", selectmode="browse", height=10, style="Config.Treeview"); self.table.column("#0", width=d(225), minwidth=d(150), stretch=False)
        self.table.grid(row=2, column=0, sticky="ns"); self.table.bind("<<TreeviewSelect>>", self.select)
        scroll = ttk.Scrollbar(nav, command=self.table.yview); scroll.grid(row=2, column=1, sticky="ns"); self.table.configure(yscrollcommand=scroll.set)
        right = ttk.Frame(body); right.grid(row=0, column=1, sticky="nsew"); right.columnconfigure(0, weight=1); right.rowconfigure(2, weight=1)
        self.title, self.info, self.status = tk.StringVar(self.window), tk.StringVar(self.window), tk.StringVar(self.window, "正在读取资料…")
        theme.wrapped_label(right, textvariable=self.title, style="ConfigTitle.TLabel").grid(row=0, column=0, sticky="ew", pady=(0, d(10)))
        self.info_label = theme.wrapped_label(right, textvariable=self.info, style="Hint.TLabel"); self.info_label.grid(row=1, column=0, sticky="ew", pady=(0, d(12)))
        self.canvas = tk.Canvas(right, background=theme.PANEL, highlightthickness=0); self.canvas.grid(row=2, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(right, command=self.canvas.yview); scroll.grid(row=2, column=1, sticky="ns"); self.canvas.configure(yscrollcommand=scroll.set)
        self.content = ttk.Frame(self.canvas, padding=(0, 0, d(12), 0)); self.content.columnconfigure(0, weight=1)
        anchor = self.canvas.create_window(0, 0, window=self.content, anchor="nw")
        self.content.bind("<Configure>", lambda _: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(anchor, width=e.width))
        footer = ttk.Frame(self.window, padding=d(18)); footer.grid(row=2, column=0, sticky="ew"); footer.columnconfigure(0, weight=1)
        theme.wrapped_label(footer, textvariable=self.status, style="Hint.TLabel").grid(row=0, column=0, sticky="ew", padx=(0, d(12)))
        self.save_button = ttk.Button(footer, text="保存资料", command=self.save); self.save_button.grid(row=0, column=1)
        self.reload_button = ttk.Button(footer, text="重新读取", command=self.reload); self.reload_button.grid(row=0, column=2, padx=d(8))
        self.close_button = ttk.Button(footer, text="关闭", command=self.close); self.close_button.grid(row=0, column=3)
        self.capture_actions=ttk.Frame(footer);self.capture_actions.grid(row=1,column=0,columnspan=4,sticky="e",pady=(d(10),0))
        self.capture_pause_button=ttk.Button(self.capture_actions,text="暂停截图",command=self.pause_capture);self.capture_pause_button.pack(side="left",padx=(0,d(8)))
        self.capture_cancel_button=ttk.Button(self.capture_actions,text="取消截图",command=self.cancel_capture);self.capture_cancel_button.pack(side="left")
        self.controls = [self.menu_button, self.selector, self.area_selector, self.table, self.save_button, self.reload_button]
        self.session = RuntimeSession(RuntimePaths.discover(ROOT, profile), self.events.put)
        self.worker = self.session.worker
        self.window.protocol("WM_DELETE_WINDOW", self.close); self.window.bind("<Destroy>", self.disposed, add="+")
        self.poll_token = self.window.after(80, self.poll); self.buttons()

    def buttons(self):
        allowed = self.connected and not self.busy and not self.closing and self.capture is None
        if self.capture is None:self.capture_actions.grid_remove()
        else:
            self.capture_actions.grid()
            self.capture_pause_button.configure(text="继续截图" if self.capture.get("control",{}).get("manual_paused") else "暂停截图")
            for widget in (self.capture_pause_button,self.capture_cancel_button):widget.state(["disabled"] if self.closing or self.capture_cancel else ["!disabled"])
        for widget in self.controls: widget.state(["!disabled"] if allowed else ["disabled"])
        self.save_button.state(["!disabled"] if allowed and self.document is not None and self.dirty else ["disabled"])
        self.reload_button.state(["!disabled"] if allowed and self.document is not None else ["disabled"])
        self.save_button.configure(style="Accent.TButton" if self.dirty else "TButton")
        document = self.document
        for index in range(7): self.menu.entryconfigure(index, state="normal" if allowed else "disabled")
        issue_count = len(self.catalog.get("issues", []))
        self.menu.entryconfigure(7, label=f"资料问题（{issue_count}）…", state="normal" if allowed and issue_count else "disabled")
        self.menu.entryconfigure(8,state='normal' if allowed and self.catalog.get('map_pending') else 'disabled')
        for index in (1, 2, 4, 6):
            if document is None: self.menu.entryconfigure(index, state="disabled")
        if document is not None:
            if self.dirty or document.get("draft"):
                for index in (2, 4, 6): self.menu.entryconfigure(index, state="disabled")
            if document["builtin"] or self.kind == "world_patrol_route": self.menu.entryconfigure(2, state="disabled")
            self.menu.entryconfigure(6, label="恢复内置" if document["builtin"] else "归档资料")
            if document["builtin"] and not document["overridden"]: self.menu.entryconfigure(6, state="disabled")
        if self.kind in ("world_patrol_route","world_patrol_map"): self.area_selector.master.grid()
        else: self.area_selector.master.grid_remove()
        if self.kind=='world_patrol_map':
            for index in (0,1,2,3,4,5):self.menu.entryconfigure(index,state='disabled')
            self.menu.entryconfigure(6,label='移除地图')
            if document is not None and not document.get('preview',{}).get('width'):self.menu.entryconfigure(6,state='disabled')

    def request(self, method, callback, **params):
        if self.busy or self.closing or not self.connected: return
        self.busy, self.callback = True, callback
        self.freeze()
        self.status.set("正在处理资料…")
        self.session.send("rpc", method=method, tag=method, params=params); self.buttons()

    def freeze(self):
        if self.frozen:return
        def freeze(widget):
            if isinstance(widget, (ttk.Entry, ttk.Combobox, ttk.Button, ttk.Checkbutton)):
                self.frozen.append((widget, widget.state())); widget.state(["disabled"])
            elif isinstance(widget, tk.Text): self.frozen.append((widget, widget.cget("state"))); widget.configure(state="disabled")
            for child in widget.winfo_children(): freeze(child)
        freeze(self.content)
        if isinstance(self.editor, (RouteEditor, GroupEditor, MapEditor)): self.editor.enabled = False

    def thaw(self):
        for widget, state in self.frozen:
            if widget.winfo_exists():
                if isinstance(widget, tk.Text): widget.configure(state=state)
                else: widget.state(["!disabled", "!readonly"]); widget.state(state)
        self.frozen.clear()
        if isinstance(self.editor, (RouteEditor, GroupEditor, MapEditor)): self.editor.enabled = True

    def loaded(self, catalog, preferred=""):
        self.catalog = catalog
        areas = [a["entry_name"] + " · " + a["name"] for a in catalog["areas"]]
        self.area_selector.configure(values=areas)
        if self.area_var.get() not in areas and areas: self.area_var.set(areas[0])
        self.refresh_nav(preferred)
        if catalog.get("issues"): self.status.set(f"{len(catalog['issues'])} 项资料无法读取；请选择其他资料或修复文件。")

    def current_area(self):
        return next((a["id"] for a in self.catalog["areas"] if a["entry_name"] + " · " + a["name"] == self.area_var.get()), "")

    def refresh_nav(self, preferred="", auto_select=True):
        if self.kind=='world_patrol_map':
            entries=list(dict.fromkeys(a['entry_name'] for a in self.catalog['areas']))
            self.area_selector.configure(values=entries)
            preferred_area=next((a for a in self.catalog['areas'] if a['id']==preferred),None)
            if preferred_area:self.area_var.set(preferred_area['entry_name'])
            elif self.area_var.get() not in entries and entries:self.area_var.set(entries[0])
        else:
            areas=[a['entry_name']+' · '+a['name'] for a in self.catalog['areas']]
            self.area_selector.configure(values=areas)
            if self.area_var.get() not in areas and areas:self.area_var.set(areas[0])
        self.table.delete(*self.table.get_children())
        for row in self.catalog["items"]:
            if self.kind=='world_patrol_map':
                area=next((a for a in self.catalog['areas'] if a['id']==row['key']),None)
                if area is None or area['entry_name']!=self.area_var.get():continue
            if row["kind"] == self.kind and (self.kind != "world_patrol_route" or row["area_id"] == self.current_area()):
                self.table.insert("", "end", iid=row["key"], text=row.get("short_name", row["name"]) if self.kind in ("world_patrol_route","world_patrol_map") else row["name"])
        values = self.table.get_children()
        if preferred in values: self.table.selection_set(preferred)
        elif values and auto_select: self.table.selection_set(values[0])
        if self.table.selection():self.table.see(self.table.selection()[0])
        if auto_select: self.select()
        self.buttons()

    def allow_leave(self):
        return not self.dirty or messagebox.askyesno("未保存修改", "放弃当前资料的未保存修改？", parent=self.window)

    def change_kind(self, _event=None):
        chosen = KINDS[self.kind_var.get()]
        if chosen == self.kind: return
        if self.busy or self.capture is not None or not self.allow_leave():
            self.kind_var.set(next(k for k, v in KINDS.items() if v == self.kind)); return
        self.kind = chosen; self.clear(); self.document = None; self.refresh_nav()

    def change_area(self, _event=None):
        if self.busy or self.capture is not None or not self.allow_leave():
            if self.document is not None:
                area = self.document["data"].get("tp_area_id")
                if self.kind=='world_patrol_map':area=self.document['key']
                row = next((r for r in self.catalog["areas"] if r["id"] == area), None)
                if row: self.area_var.set(row['entry_name'] if self.kind=='world_patrol_map' else row["entry_name"] + " · " + row["name"])
            return
        self.clear(); self.document = None; self.refresh_nav()

    def select(self, _event=None):
        selected = self.table.selection()
        if self.capture is not None:
            if self.document is not None and selected != (self.document["key"],) and self.table.exists(self.document["key"]):
                self.table.selection_set(self.document["key"])
            return
        if self.busy or not selected: return
        if self.document is not None and selected[0] == self.document["key"] and self.document["kind"] == self.kind: return
        if not self.allow_leave():
            if self.document is not None and self.table.exists(self.document["key"]): self.table.selection_set(self.document["key"])
            return
        key = selected[0]; self.clear(); self.document = None
        self.request("library_read", self.display, kind=self.kind, key=key)

    def clear(self):
        if self.content.winfo_exists():
            for widget in self.content.winfo_children(): widget.destroy()
        self.editor, self.dirty = None, False
        self.title.set(""); self.info.set("")

    def display(self, document):
        if document["kind"] == "world_patrol_route": self.request("library_map", lambda preview: self.render(document, preview), area_id=document["data"]["tp_area_id"])
        else: self.render(document)

    def render(self, document, preview=None):
        self.clear(); self.document = copy.deepcopy(document)
        self.kind=document["kind"]; self.kind_var.set(next(k for k,v in KINDS.items() if v==self.kind))
        if self.kind == "world_patrol_route":
            area = next((a for a in self.catalog["areas"] if a["id"] == document["data"]["tp_area_id"]), None)
            if area: self.area_var.set(area["entry_name"] + " · " + area["name"])
        self.refresh_nav(document["key"], auto_select=False)
        self.title.set(document["name"])
        metadata = "内置资料 · 保存为本地覆盖" if document["builtin"] else "自定义资料"
        author = document["data"].get("author")
        if author: metadata += " · 作者：" + ("、".join(map(str, author)) if isinstance(author, list) else str(author))
        self.info.set('地图资料' if document['kind']=='world_patrol_map' else metadata)
        if document["kind"] == "world_patrol_route": self.editor = RouteEditor(self.content, document, preview, self.changed, self.ui.ui_scale, self.locate, self.debug)
        elif document['kind']=='world_patrol_map':
            view=self.map_view[1] if self.map_view is not None and self.map_view[0]==document['key'] else None
            self.editor=MapEditor(self.content,document,self.map_action,self.map_capture,self.map_archives,self.ui.ui_scale,view)
        elif document["kind"] == "world_patrol_route_list": self.editor = GroupEditor(self.content, document, self.catalog, self.changed, self.ui.ui_scale)
        else: self.editor = ScalarForm(self.content, document["fields"], self.changed, self.ui.ui_scale)
        self.editor.frame.grid(row=0, column=0, sticky="ew")
        enable_scroll(self.canvas, self.content); self.canvas.yview_moveto(0)
        self.dirty = bool(document.get("draft"))
        self.status.set("恢复草稿 · 保存后生效" if self.dirty and document.get("archive_id") else "新草稿 · 尚未保存" if self.dirty else "已读取")
        self.buttons()

    def changed(self): self.dirty=True; self.status.set("未保存"); self.buttons()

    def values(self):
        if self.document is None or self.editor is None: raise ValueError("请先读取或新建资料。")
        if isinstance(self.editor, (RouteEditor, GroupEditor, MapEditor)): return self.editor.values()
        return dict(copy.deepcopy(self.document["data"]), **self.editor.values())

    def save(self):
        if self.document is None: return
        try: data = self.values()
        except ValueError as exc: self.status.set(str(exc)); return
        source = self.document.get("source", {})
        self.request("library_save", self.saved, kind=self.document["kind"], key=self.document["key"], data=data, revision=self.document["revision"],
                     source_key=source.get("key", ""), source_revision=source.get("revision", ""))

    def saved(self, document):
        self.display(document)
        # 更新左侧目录只重新读取目录，不覆盖刚返回的草稿或重新触发文件选择。
        self.updated_key = document["key"]
        self.status.set(document.get("notice") or "已保存")
        self.ui.onedragon_panel.status.set("资料已更新 · 读取配置可刷新选项")
        if document["kind"] != "world_patrol_route": self.refresh_catalog(document)
        else: self.after_map_saved = document

    def refresh_catalog(self, document=None):
        def loaded(catalog):
            self.catalog = catalog
            self.refresh_nav(document['key'] if document else '',auto_select=False)
            if document: self.status.set(document.get("notice") or "已保存")
        self.request("library_catalog", loaded)

    def reload(self):
        if self.document is not None and self.allow_leave(): self.request("library_read", self.display, kind=self.document["kind"], key=self.document["key"])

    def name_prompt(self, title, initial=""):
        return simpledialog.askstring(title, "名称（1—100 字）", initialvalue=initial, parent=self.window)

    def new(self):
        if self.busy or not self.allow_leave(): return
        key = "" if self.kind == "world_patrol_route" else self.name_prompt("新建资料")
        if key is None: return
        self.request("library_draft", self.display, kind=self.kind, key=key, area_id=self.current_area())

    def copy(self):
        if self.busy or self.document is None: return
        try: data = self.values()
        except ValueError as exc: self.status.set(str(exc)); return
        key = "" if self.kind == "world_patrol_route" else self.name_prompt("另存为", self.document["key"] + "_副本")
        if key is None: return
        source = self.document.get("source", {}) if self.document.get("draft") else dict(key=self.document["key"], revision=self.document["revision"])
        self.request("library_draft", self.display, kind=self.kind, key=key, source_key=source.get("key", ""), source_revision=source.get("revision", ""), area_id=self.current_area(), data=data)

    def rename(self):
        if self.busy or self.document is None: return
        if self.dirty: self.status.set("请先保存当前草稿，再改名。") ; return
        if self.document["builtin"] or self.kind == "world_patrol_route": self.status.set("内置资料或固定路线编号请使用另存为。") ; return
        key = self.name_prompt("改名", self.document["key"])
        if key is not None: self.request("library_rename", self.saved, kind=self.kind, key=self.document["key"], name=key, revision=self.document["revision"])

    def remove(self):
        if self.busy or self.document is None or self.dirty: return
        label = "恢复内置" if self.document["builtin"] else "归档"
        if not messagebox.askyesno(label, f"{label}“{self.document['key']}”？当前版本会保留在归档中。", parent=self.window): return
        def removed(result):
            self.clear(); self.document = None
            self.request("library_catalog", lambda catalog: self.loaded(catalog))
        self.request("library_remove", removed, kind=self.kind, key=self.document["key"], revision=self.document["revision"])

    def import_file(self):
        if self.busy or not self.allow_leave(): return
        path = filedialog.askopenfilename(parent=self.window, filetypes=[("YAML", "*.yml *.yaml")])
        if not path: return
        try:
            source = Path(path)
            if source.stat().st_size > 768 * 1024: raise ValueError("导入文件过大。")
            text = source.read_text(encoding="utf-8-sig")
        except (ValueError, OSError, UnicodeError) as exc: self.status.set(str(exc)); return
        key = "" if self.kind == "world_patrol_route" else self.name_prompt("导入为", source.stem)
        if key is not None: self.request("library_draft", self.display, kind=self.kind, key=key, area_id=self.current_area(), text=text)

    def export_file(self):
        if self.busy or self.document is None: return
        if self.dirty: self.status.set("请先保存草稿，再导出。") ; return
        path = filedialog.asksaveasfilename(parent=self.window, initialfile=self.document["key"] + ".yml", defaultextension=".yml", filetypes=[("YAML", "*.yml")])
        if not path: return
        def exported(result):
            try: Path(path).write_text(result["text"], encoding="utf-8")
            except OSError as exc: self.status.set("导出失败：" + str(exc)); return
            self.status.set("已导出")
        self.request("library_export", exported, kind=self.kind, key=self.document["key"], revision=self.document["revision"])

    def archives(self):
        if self.busy or not self.allow_leave(): return
        def loaded(catalog):
            self.catalog = catalog
            if not catalog["archives"]: self.status.set("暂无归档"); return
            self.dialog = SelectionDialog(self.window, "从归档恢复", catalog["archives"], lambda row: self.request("library_archive_read", self.restore_draft, identifier=row["id"]), self.ui.ui_scale)
        self.request("library_catalog", loaded)

    def diagnostics(self):
        if not self.busy and not self.closing and self.catalog.get("issues"):
            self.dialog = DiagnosticsDialog(self.window, self.catalog["issues"], self.ui.ui_scale)

    def locate(self, from_file=False):
        if self.busy or self.capture is not None or not isinstance(self.editor,RouteEditor):return
        self.editor.set_candidate(None)
        try: anchor=self.editor.anchor()
        except (ValueError,StopIteration,KeyError,TypeError) as exc:self.status.set("请先修正路线起点："+str(exc));return
        params=dict(area_id=self.document["data"]["tp_area_id"],anchor=anchor)
        if from_file:
            path=filedialog.askopenfilename(parent=self.window,filetypes=[("客户区截图","*.png *.jpg *.jpeg")])
            if not path:return
            try:
                from .onedragon_route_recorder import encode_client_image
                params["image"]=encode_client_image(path)
            except Exception as exc:self.status.set(str(exc));return
            self.request("route_locate",self.position_result,**params)
        else:
            from .config import GAME_PATHS
            params["game_path"]=self.ui.game_path.get() if self.ui.server.get()==("国服" if self.profile=="cn" else "国际服") else GAME_PATHS[self.profile]
            self.capture_cancel=False;self.capture_pause=None
            self.request("route_capture_start",self.capture_result,**params)

    def position_result(self,result):
        if isinstance(self.editor,RouteEditor):self.editor.set_candidate(result)
        self.status.set("已识别候选位置" if result.get("found") else "未能匹配位置，请核实所选地图和起点。")

    def capture_result(self,result):
        if result["busy"]:
            self.capture=result;self.freeze();self.next_capture_poll=time.monotonic()+.15
            self.status.set("正在取消截图…" if self.capture_cancel else {"focus_wait":"等待游戏恢复焦点 · F9 暂停 / F10 停止","paused":"截图已暂停 · F9 继续","success":"等待截图器退出…"}.get(result["state"],"正在识别位置 · F9 暂停 / F10 停止"))
        else:
            self.capture=None;self.capture_cancel=False;self.capture_pause=None;self.thaw()
            if result["state"]=="success" and result.get("result") is not None:
                if result.get('kind')=='map':self.map_action('captured',result['result'])
                else:self.position_result(result["result"])
            else:self.status.set(result.get("error") or "截图已取消")
        self.buttons()

    def pause_capture(self):
        if self.capture is not None:
            self.capture_pause=not self.capture.get("control",{}).get("manual_paused",False);self.next_capture_poll=0.

    def cancel_capture(self):
        if self.capture is not None:self.capture_cancel=True;self.next_capture_poll=0.;self.buttons()

    def map_action(self,action,value=None):
        if self.busy or self.capture is not None or not isinstance(self.editor,MapEditor):return
        if action=='invalid_import':self.status.set(str(value));return
        self.map_view=(self.document['key'],self.editor.view())
        if action=='import' and len(value)==2:
            def first_imported(document):
                self.display(document)
                self.request('map_edit',self.display,data=document['data'],action='append_frame',value=value[1])
            self.request('map_edit',first_imported,data=self.editor.values(),action='import',value=[value[0]])
            return
        self.request('map_edit',self.display,data=self.editor.values(),action=action,value=value)

    def map_capture(self,rotate):
        if self.busy or self.capture is not None or not isinstance(self.editor,MapEditor):return
        from .config import GAME_PATHS
        game_path=self.ui.game_path.get() if self.ui.server.get()==('国服' if self.profile=='cn' else '国际服') else GAME_PATHS[self.profile]
        self.map_view=(self.document['key'],self.editor.view())
        self.capture_cancel=False;self.capture_pause=None
        self.request('map_capture_start',self.capture_result,data=self.editor.values(),game_path=game_path,rotate=rotate)

    def map_archives(self):
        if self.busy or self.capture is not None or self.document is None or not self.allow_leave():return
        key,revision=self.document['key'],self.document['revision']
        def loaded(result):
            if result['issues']:
                existing={row['file']:row for row in self.catalog.get('issues',[])}
                existing.update({row['file']:row for row in result['issues']});self.catalog['issues']=list(existing.values())
                self.status.set(f"{len(result['issues'])} 项地图归档无法读取，可从资料问题查看。")
            if not result['items']:
                if result['issues']:self.dialog=DiagnosticsDialog(self.window,result['issues'],self.ui.ui_scale)
                else:self.status.set('暂无地图归档')
                return
            def selected(row):
                if not row['restorable']:self.status.set('归档后地图或路线已有修改，不能覆盖。');return
                if messagebox.askyesno('恢复地图归档',f"恢复此地图归档，并同时恢复 {row['route_count']} 条关联路线？",parent=self.window):
                    self.request('map_restore',self.display,key=key,identifier=row['id'],revision=revision)
            rows=[dict(row,key=row['key']+(' · 可恢复' if row['restorable'] else ' · 已有后续修改')) for row in result['items']]
            self.dialog=SelectionDialog(self.window,'恢复地图归档',rows,selected,self.ui.ui_scale,confirm_label='恢复此归档')
        self.request('map_archives',loaded,key=key)

    def recover_map(self):
        if self.busy or self.capture is not None or not self.allow_leave():return
        if not messagebox.askyesno('恢复中断保存','恢复上次中断保存之前的地图和关联路线？',parent=self.window):return
        def recovered(result):
            key=result.get('area_id')
            if key:
                self.kind='world_patrol_map';self.kind_var.set('大地图')
                self.request('library_read',self.display,kind=self.kind,key=key)
            else:self.status.set('没有待恢复的地图保存。')
            self.catalog['map_pending']=False
        self.request('map_recover',recovered)

    def debug(self):
        if self.busy or self.capture is not None or self.document is None:return
        if self.dirty or self.document.get("draft"):self.status.set("请先保存路线，再打开调试。");return
        count=len(self.document["data"]["op_list"])
        if not count:self.status.set("请先添加移动点。");return
        existing=getattr(self.ui,"execution_window",None)
        if existing is not None and not existing.closed:self.status.set("请先关闭当前运行窗口，再打开路线调试。");return
        index=simpledialog.askinteger("路线调试","已完成的移动点数（0 从起点传送；大于 0 须已在对应点位附近）",initialvalue=0,minvalue=0,maxvalue=count-1,parent=self.window)
        if index is None:return
        from .onedragon_run_ui import open_execution
        open_execution(self.ui,"world_patrol","路线调试",self.profile,route_debug=dict(key=self.document["key"],revision=self.document["revision"],start_index=index))

    def restore_draft(self, document):
        self.kind = document["kind"]; self.kind_var.set(next(k for k, v in KINDS.items() if v == self.kind)); self.display(document)

    def poll(self):
        if self.closed: return
        try:
            while True:
                event = self.events.get_nowait()
                if event["type"] == "connected": self.connected=True; self.request("library_catalog", self.loaded)
                elif event["type"] == "rpc_result":
                    self.busy=False
                    if self.capture is None:self.thaw()
                    callback, self.callback = self.callback, None
                    if not self.closing and callback:
                        try: callback(event["result"])
                        except Exception as exc: self.status.set("资料显示失败：" + str(exc))
                elif event["type"] == "error":
                    self.busy=False
                    if self.capture is None:self.thaw()
                    self.callback=None; self.status.set(event["message"])
                elif event["type"] == "closed": self.closed=True; self.window.destroy(); return
                self.buttons()
        except queue.Empty: pass
        if self.capture is not None and not self.busy and not self.closing and time.monotonic()>=self.next_capture_poll:
            prefix='map_capture_' if self.capture.get('kind')=='map' else 'route_capture_'
            method=prefix+('cancel' if self.capture_cancel else 'pause' if self.capture_pause is not None else 'status')
            params=dict(job_id=self.capture["job_id"])
            if method.endswith('_pause'):params["paused"]=self.capture_pause;self.capture_pause=None
            self.next_capture_poll=time.monotonic()+.3
            self.request(method,self.capture_result,**params)
        # 地图读取保持同一请求；完成后才刷新目录，避免覆盖 display 的回调。
        pending = getattr(self, "after_map_saved", None)
        if pending is not None and not self.busy and not self.closing:
            self.after_map_saved=None; self.refresh_catalog(pending)
        self.poll_token = self.window.after(80, self.poll)

    def close(self):
        if self.closing: return True
        if not self.allow_leave(): return False
        self.closing=True; self.status.set("正在等待资料处理和原进程退出…"); self.session.close(); self.buttons()
        if not self.worker.is_alive(): self.closed=True; self.window.destroy()
        return True

    def disposed(self, event):
        if event.widget is self.window:
            if self.poll_token is not None: self.window.after_cancel(self.poll_token); self.poll_token=None
            self.clear(); self.kind_var=self.area_var=self.title=self.info=self.status=None


def open_library(ui, profile, kind="lost_void_challenge"):
    existing = getattr(ui, "library_window", None)
    if existing is not None and not existing.closed:
        if existing.profile != profile:
            if not existing.close(): return existing
            def reopen():
                if ui.closing: return
                if existing.closed and not existing.worker.is_alive(): open_library(ui, profile, kind)
                else: ui.root.after(80, reopen)
            ui.root.after(80, reopen); return existing
        existing.window.deiconify(); existing.window.lift()
        if existing.kind != kind:
            existing.kind_var.set(next(k for k, v in KINDS.items() if v == kind)); existing.change_kind()
        return existing
    ui.library_window = LibraryWindow(ui, profile, kind)
    return ui.library_window
