"""Interactive compendium surfaces reconstructed from ZZZ menu references."""
from __future__ import annotations

import re
import tkinter as tk
from tkinter import ttk
from PIL import Image, ImageDraw

from . import theme as t


class Header(t.Surface):
    def paint(self, p):
        p.asset("zzz-logo.png", (22, 12, 61, 51))
        p.text((77, 17), "快捷手册", 27)
        p.text((212, 26), "AUTOZZZ", 14, fill=t.MUTED, latin=True)
        p.text((p.width - 26, 30), "绳匠的每日委托", 15, fill=t.MUTED, anchor="rm")
        p.line((20, p.height - 2, p.width - 20, p.height - 2), "#252525")


class Compendium(t.Surface):
    def __init__(self, parent, scale):
        super().__init__(parent, scale, background="#171717")
        self.section = "home"
        self.content = ttk.Frame(self)
        self.tabs = ttk.Frame(self)
        self.bind("<Configure>", self._position, add="+")

    def show_section(self, name):
        self.section = name
        self._position()
        self.redraw()

    def _position(self, _event=None):
        d = lambda n: round(n * self.scale)
        width, height = self.winfo_width(), self.winfo_height()
        left = 34 if self.section == "settings" else 130
        self.content.place(x=d(left), y=d(88), width=max(1, width-d(left+46)), height=max(1, height-d(124)))
        self.tabs.place(relx=1, x=-d(54), y=d(17), anchor="ne")

    def paint(self, p):
        w, h = p.width, p.height
        p.stripes((0, 0, w, h), "#212121", 7)
        for inset in (0, 5, 10):
            p.box((13+inset, 48-inset*.5, w-15-inset, h-10-inset*.5), 28,
                  fill="#262626", outline="#080808", width=3)
        p.box((14, 48, w-30, h-15), 24, fill=t.PANEL, outline="#0b0b0b", width=3)
        p.box((21, 55, w-37, h-22), 19, fill=t.PANEL, outline="#505050", width=1)
        p.dots((38, 62, w-51, h-31), "#3b3b3b", 5)
        for x in range(38, round(w-58), 22):
            p.line((x, 60, x+12, 60), "#222222", 1)
            p.line((x, h-28, x+12, h-28), "#202020", 1)
        for y in range(79, round(h-40), 22):
            p.line((27, y, 27, y+12), "#202020")
            p.line((w-43, y, w-43, y+12), "#202020")
        words = {"home": "ERRANDS", "tasks": "CUSTOM SCHEDULE", "combat": "TRAINING", "logs": "RECORDS", "settings": "OPTIONS"}
        p.text((132 if self.section != "settings" else 42, 67), "+  " + words[self.section] + "  +", 10, fill="#121212")
        if self.section != "settings":
            self._spine(p, h)

    def _spine(self, p, height):
        p.box((32, 67, 110, height-36), 15, fill=t.ORANGE, outline="#202020", width=3)
        p.box((39, 69, 98, height-70), 12, fill="#191919", outline="#6c2f14", width=2)
        p.text((45, height-60), "RANDOM", 9, fill=t.BG, latin=True)
        p.text((45, height-49), "PLAY", 9, fill=t.BG, latin=True)
        # Vertical lettering and the familiar hanging orange handheld are code
        # geometry, not a screen capture with a player's fixed progress baked in.
        size = round(32*p.factor)
        face = t.bitmap_font(size, True)
        text = "SCHEDULE"
        extent = face.getbbox(text)
        label = Image.new("RGBA", (round(face.getlength(text))+8, extent[3]+8))
        ImageDraw.Draw(label).text((3, 0), text, font=face, fill=t.ORANGE)
        label = label.rotate(-90, expand=True)
        available = max(100, round((height-230)*p.factor))
        if label.height > available:
            label = label.resize((round(label.width*available/label.height), available), Image.Resampling.LANCZOS)
        p.image.paste(label, p.coords((51, 192)), label)
        for y in (66, 90, 114):
            p.box((37, y, 62, y+31), 9, fill="#171717", outline="#7a7771", width=3)
            p.line((40, y+4, 57, y+21), "#050505", 10)
            p.line((43, y+4, 60, y+21), "#3d3d3d", 3)
        p.box((36, 139, 91, 211), 12, fill="#222222", outline="#aaa69a", width=3)
        p.box((40, 144, 87, 205), 8, fill=t.ORANGE, outline="#efbc8c", width=2)
        p.box((46, 151, 81, 176), 5, fill="#d3d5bd", outline="#373d34", width=3)
        p.box((50, 155, 77, 172), 2, fill="#79adb1")
        p.line((54, 162, 61, 168, 72, 158), "#e3f7ed", 3)
        p.line((50, 189, 65, 189), "#171717", 6)
        p.line((57, 182, 57, 196), "#171717", 6)
        p.line((50, 189, 65, 189), "#dfdcd1", 3)
        p.line((57, 182, 57, 196), "#dfdcd1", 3)
        p.box((74, 183, 81, 190), 4, fill="#e6dfce", outline="#181818")
        p.box((69, 194, 75, 200), 3, fill="#e6dfce", outline="#181818")


class EngagementMeter(t.Surface):
    def __init__(self, parent, scale, variable):
        super().__init__(parent, scale, height=round(78*scale))
        self.variable = variable
        self._trace = variable.trace_add("write", lambda *_: self.redraw())
        self.bind("<Destroy>", self._untrace, add="+")

    def _untrace(self, event):
        if event.widget is self:
            self.variable.trace_remove("write", self._trace)

    @property
    def progress(self):
        match = re.search(r"活跃度\s*(\d+)", self.variable.get())
        return min(400, int(match[1])) if match else None

    def paint(self, p):
        w = p.width
        p.box((1, 1, w-1, 71), 35, fill="#171717", outline="#817b22", width=2)
        p.box((7, 7, w-7, 65), 29, fill="#242424", outline="#535353", width=2)
        p.stripes((27, 18, 143, 53), "#2e2e2e", 5)
        p.text((84, 18), "今日最大活跃度", 12, anchor="mt", fill="#cccccc")
        p.text((84, 35), "400", 25, anchor="mt", latin=True, stroke=1)
        start, end = 200, w-62
        positions = [start+(end-start)*i/3 for i in range(4)]
        p.box((start-18, 28, end+18, 44), 8, fill="#111111", outline="#3a3a3a", width=2)
        progress = self.progress
        verified = "每日已验证" in self.variable.get()
        if progress:
            fraction = min(1., max(0., (progress-100)/300))
            p.box((start-4, 33, start+(end-start)*fraction+4, 39), 3, fill=t.SUCCESS)
        for i, x in enumerate(positions, 1):
            active = progress is not None and progress >= i*100
            color = t.SUCCESS if active else "#777777"
            p.box((x-18, 18, x+18, 54), 18, fill="#080808", outline="#424242", width=2)
            p.box((x-14, 22, x+14, 50), 14, fill="#191919", outline=color, width=2)
            if verified and active:
                p.asset("daily_completed.png", (x-12, 24, x+12, 48), template=True)
            else:
                p.poly((x-8, 32, x-5, 28, x-3, 34, x+1, 28, x+4, 34, x+7, 30,
                        x+8, 39, x+3, 44, x-3, 44, x-8, 39), color)
            p.text((x, 55), str(i*100), 13, anchor="mm", latin=True, stroke=1)


TITLES = {"scratch": "进行一次刮刮卡\n或领取饼铺盲盒", "coffee": "喝一杯咖啡", "video": "让录像店\n开始营业",
          "daily": "领取今日\n活跃度奖励", "weekly": "领取丽都周纪\n已有奖励", "city_fund": "领取丽都城募\n已有奖励"}
DESCRIPTIONS = {"scratch": "领取当日免费签到奖励", "coffee": "选择今日免费饮品", "video": "按设置选择已拥有宣传员",
                "daily": "核实 400 活跃与四档奖励", "weekly": "已完成任务与里程奖励", "city_fund": "成长任务与等级回馈"}


class TaskBoard(t.Surface):
    """Six live task cards, with the small Treeview interface used by the runner.

    Arrows, wheel and keyboard reach every card. Clicking opens the existing
    full-detail dialog; the drawn buttons do not dispatch hidden game actions.
    """
    def __init__(self, parent, scale, ui, task_names):
        super().__init__(parent, scale, takefocus=True, cursor="hand2", height=round(310*scale))
        self.ui, self.names = ui, task_names
        self.records = {name: dict(values=(label, "等待" if ui.tasks[name].get() else "跳过", "尚未开始"), tags=())
                        for name, label in task_names.items()}
        self.offset, self.selected, self.hover = 0, None, None
        self.hitboxes = {}
        self._traces = [(v, v.trace_add("write", lambda *_: self.redraw())) for v in ui.tasks.values()]
        self.bind("<Destroy>", self._untrace, add="+")
        self.bind("<Button-1>", self._click)
        self.bind("<Motion>", self._motion)
        self.bind("<Leave>", self._leave)
        self.bind("<MouseWheel>", lambda event: self.scroll(1 if event.delta < 0 else -1))
        self.bind("<Right>", lambda _: self.move_selection(1))
        self.bind("<Left>", lambda _: self.move_selection(-1))
        self.bind("<Home>", lambda _: self.move_selection(-6))
        self.bind("<End>", lambda _: self.move_selection(6))
        self.bind("<Return>", lambda _: self._activate(self.selected or self.visible_tasks[0]))
        self.bind("<space>", lambda _: self._activate(self.selected or self.visible_tasks[0]))
        self.bind("<FocusIn>", lambda _: self.redraw())
        self.bind("<FocusOut>", lambda _: self.redraw())

    def _untrace(self, event):
        if event.widget is self:
            for variable, token in self._traces:
                variable.trace_remove("write", token)

    @property
    def visible_tasks(self):
        return list(self.names)[self.offset:self.offset+3]

    def item(self, iid, option=None, **kwargs):
        record = self.records[iid]
        if kwargs:
            record.update(kwargs)
            self.redraw()
        return record[option] if option else dict(record)

    def selection(self):
        return (self.selected,) if self.selected else ()

    def scroll(self, delta):
        self.offset = max(0, min(len(self.names)-3, self.offset+delta))
        self.redraw()
        return "break"

    def move_selection(self, delta):
        keys = list(self.names)
        current = keys.index(self.selected) if self.selected else self.offset
        current = max(0, min(len(keys)-1, current+delta))
        self.selected = keys[current]
        self.offset = max(0, min(len(keys)-3, current if current < self.offset else max(self.offset, current-2)))
        self.redraw()
        return "break"

    def _target(self, event):
        x, y = event.x/self.scale, event.y/self.scale
        return next((name for name, (x1, y1, x2, y2) in self.hitboxes.items() if x1 <= x <= x2 and y1 <= y <= y2), None)

    def _motion(self, event):
        target = self._target(event)
        if target != self.hover:
            self.hover = target
            self.redraw()

    def _leave(self, _event):
        self.hover = None
        self.redraw()

    def _click(self, event):
        self.focus_set()
        self._activate(self._target(event))

    def _activate(self, target):
        if target == "previous":
            self.scroll(-1)
        elif target == "next":
            self.scroll(1)
        elif target == "schedule":
            self.ui._show_page("tasks")
        elif target in self.records:
            self.selected = target
            self.redraw()
            self.event_generate("<<TreeviewSelect>>")

    def paint(self, p):
        w, h = p.width, p.height
        p.box((0, 0, w, h-1), 20, fill="#111111", outline="#484848", width=2)
        p.box((5, 5, w-5, h-7), 16, fill="#1b1b1b")
        self.hitboxes = {}
        bottom = h-38
        gap = 12
        schedule = max(192, (w-56)*.245)
        card_width = (w-schedule-5*gap)/3
        self._schedule(p, 13, 14, schedule, bottom)
        for i, key in enumerate(self.visible_tasks):
            x = 13+schedule+gap+i*(card_width+gap)
            self._task_card(p, key, x, 14, card_width, bottom)
        y = h-19
        p.text((20, y), "DAILY SCHEDULE", 10, fill="#737373", anchor="lm", latin=True)
        p.text((w/2, y), f"{self.offset+1} — {self.offset+3}  /  {len(self.names)}", 12, fill=t.MUTED, anchor="mm")
        for name, x, direction, available in (("previous", w-76, -1, self.offset > 0), ("next", w-34, 1, self.offset < len(self.names)-3)):
            color = t.ACCENT if self.hover == name and available else (t.TEXT if available else "#525252")
            p.box((x-17, y-13, x+17, y+13), 13, fill="#101010", outline="#535353")
            p.line((x-direction*3, y-6, x+direction*3, y, x-direction*3, y+6), color, 3)
            self.hitboxes[name] = (x-17, y-13, x+17, y+13)

    def _schedule(self, p, x, y, width, bottom):
        p.box((x, y, x+width, bottom), 13, fill="#444444", outline="#0c0c0c", width=2)
        p.box((x+9, y+30, x+width-9, bottom-59), 9, fill="#282828")
        p.box((x+8, y-6, x+31, y+24), 3, fill=t.ORANGE)
        p.text((x+20, y+8), "★", 16, fill=t.BG, anchor="mm")
        p.text((x+40, y+10), "本次委托", 13, fill="#bdbdbd")
        p.text((x+18, y+43), "自定义日程", 21, stroke=1)
        labels = ("每日签到", "喝杯咖啡", "录像店营业", "活跃度奖励", "丽都周纪", "丽都城募")
        step = min(29, max(20, (bottom-y-125)/6))
        for i, (key, label) in enumerate(zip(self.names, labels)):
            top = y+83+i*step
            selected = self.ui.tasks[key].get()
            p.box((x+19, top, x+29, top+10), 2, fill="#d1d1cc" if selected else "#282828", outline="#93938d")
            if selected:
                p.line((x+21, top+5, x+24, top+8, x+28, top+2), "#333333", 1.5)
            p.text((x+39, top+5), label, 13, anchor="lm", fill="#d2d2cc" if selected else "#737373")
        p.text((x+19, bottom-78), "每日电量消耗  0", 12, fill=t.ACCENT)
        self._pill(p, (x+14, bottom-44, x+width-14, bottom-12), "修改委托", self.hover == "schedule")
        self.hitboxes["schedule"] = (x, y, x+width, bottom)

    def _task_card(self, p, key, x, y, width, bottom):
        status, detail = self.records[key]["values"][1:]
        selected = self.ui.tasks[key].get()
        if status in ("等待", "跳过"):
            status = "等待" if selected else "跳过"
        done = status in ("完成", "全部完成")
        color = t.SUCCESS if done else (t.ERROR if status == "失败" else t.ACCENT if status == "进行中" else t.MUTED)
        focused = self.selected == key and self.focus_get() == self
        p.box((x, y, x+width, bottom), 13, fill="#434343", outline=t.ACCENT if focused else "#080808", width=2)
        p.box((x+8, y+30, x+width-8, bottom-59), 9, fill="#222222")
        p.stripes((x+16, y+38, x+width-16, bottom-70), "#252525", 6)
        p.text((x+14, y+9), "进度："+("1 / 1" if done else "— / 1"), 13, fill="#aaaaaa")
        title = p.wrap(TITLES[key], width-31, 21, 3)
        p.text((x+16, y+43), title, 21, stroke=1)
        p.text((x+16, y+127), p.wrap(DESCRIPTIONS[key], width-32, 12, 2), 12, fill="#9e9e9a")
        p.text((x+17, bottom-111), status, 23, fill=color, stroke=1)
        p.text((x+17, bottom-80), "DAILY COMMISSION", 8, fill="#a59735", latin=True)
        if done:
            p.asset("daily_completed.png", (x+16, bottom-39, x+36, bottom-19), template=True)
            p.text((x+43, bottom-28), "已完成", 17, fill=t.SUCCESS, anchor="lm")
        else:
            self._pill(p, (x+13, bottom-44, x+width-13, bottom-12), "查看详情", self.hover == key)
        self.hitboxes[key] = (x, y, x+width, bottom)

    @staticmethod
    def _pill(p, rect, label, active):
        x1, y1, x2, y2 = rect
        p.box(rect, 17, fill="#0b0b0b", outline=t.ACCENT if active else "#777777", width=1)
        p.box((x1+3, y1+3, x2-3, y2-3), 14, fill="#0d0d0d", outline="#333333")
        p.text(((x1+x2)/2, (y1+y2)/2), label, 16, anchor="mm")


class SettingRow(t.Surface):
    """The game's full-width grey pill row, with a native accessible editor."""
    def __init__(self, parent, scale, title, *, value_width=290):
        super().__init__(parent, scale, height=round(55*scale), background=t.FIELD)
        self.title, self.value_width = title, value_width
        self.control = None
        self.bind("<Configure>", self._place_control, add="+")

    def mount(self, control):
        self.control = control
        self._place_control()
        return control

    def _place_control(self, _event=None):
        if self.control:
            d = lambda v: round(v*self.scale)
            width = min(d(self.value_width), max(d(140), self.winfo_width()//2))
            self.control.place(relx=1, x=-d(15), rely=.5, anchor="e", width=width)

    def paint(self, p):
        p.box((0, 5, p.width, p.height-5), 24, fill="#272727", outline="#3b3b3b")
        p.text((20, p.height/2), self.title, 17, anchor="lm")


class InsetPanel(t.Surface):
    def __init__(self, parent, scale, **kwargs):
        super().__init__(parent, scale, **kwargs)
        self.content = ttk.Frame(self, style="Card.TFrame")
        self.bind("<Configure>", self._position, add="+")

    def _position(self, _event=None):
        edge = round(18*self.scale)
        self.content.place(x=edge, y=edge, width=max(1, self.winfo_width()-2*edge), height=max(1, self.winfo_height()-2*edge))

    def paint(self, p):
        p.box((1, 1, p.width-1, p.height-1), 25, fill="#090909", outline="#696969", width=2)
        p.box((6, 6, p.width-6, p.height-6), 20, fill=t.FIELD, outline="#050505", width=3)


def show_task_detail(ui, title, status, detail):
    """A bounded, scrollable result sheet: long OCR errors cannot grow the footer."""
    previous = getattr(ui, "detail_window", None)
    if previous is not None and previous.winfo_exists():
        previous.destroy()
    root, scale = ui.root, ui.ui_scale
    d = lambda n: round(n*scale)
    dialog = tk.Toplevel(root)
    ui.detail_window = dialog
    dialog.title(title)
    dialog.configure(bg=t.FIELD)
    t._window_identity(dialog)
    dialog.transient(root)
    width, height = min(d(650), root.winfo_screenwidth()-d(60)), min(d(420), root.winfo_screenheight()-d(90))
    x = max(0, min(root.winfo_screenwidth()-width, root.winfo_rootx()+(root.winfo_width()-width)//2))
    y = max(0, min(root.winfo_screenheight()-height-d(40), root.winfo_rooty()+(root.winfo_height()-height)//2))
    dialog.geometry(f"{width}x{height}+{x}+{y}")
    dialog.minsize(min(d(460), width), min(d(260), height))
    dialog.columnconfigure(0, weight=1)
    dialog.rowconfigure(2, weight=1)
    ttk.Label(dialog, text=title, style="CardTitle.TLabel", font=(t.FONT, 17)).grid(row=0, column=0, sticky="w", padx=d(22), pady=(d(20), d(7)))
    ttk.Label(dialog, text=status, style="Card.TLabel", foreground=t.SUCCESS if status == "完成" else t.MUTED).grid(row=1, column=0, sticky="w", padx=d(22), pady=(0, d(14)))
    area = ttk.Frame(dialog, style="Card.TFrame")
    area.grid(row=2, column=0, sticky="nsew", padx=d(22))
    text = tk.Text(area, wrap="word", font=("Microsoft YaHei UI", 11), bg=t.BG, fg=t.TEXT,
                   selectbackground=t.ACCENT, selectforeground=t.BG, borderwidth=0, highlightthickness=0,
                   padx=d(14), pady=d(12), spacing1=d(4))
    ui.detail_text = text
    scroll = ttk.Scrollbar(area, orient="vertical", command=text.yview)
    text.configure(yscrollcommand=scroll.set)
    scroll.pack(side="right", fill="y")
    text.pack(fill="both", expand=True)
    text.insert("1.0", detail)
    text.configure(state="disabled")
    close = ttk.Button(dialog, text="关闭", command=dialog.destroy, style="Accent.TButton")
    close.grid(row=3, column=0, sticky="e", padx=d(22), pady=d(16))
    dialog.bind("<Escape>", lambda _: dialog.destroy())
    dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
    dialog.grab_set()
    close.focus_set()
