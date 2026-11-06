"""Local update transactions remain available when the SDK cannot connect."""
import os
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

from .config import ROOT
from .localization import tr
from .onedragon_plugins_ui import scroll_table
from .program_update_recovery import discover_transactions, inspect_transaction
from .theme import wrapped_label


_STAGES = {
    'handoff': '交接待核对', 'cancelled': '已取消交接', 'preparing_sdk': '准备区服快照',
    'arming': '设置联合门槛', 'exchanging': '交换程序目录', 'publishing': '提交区服选择',
    'restoring': '恢复原版本中', 'finishing': '等待释放区服门槛', 'completed': '更新已完成',
    'rolled_back': '已恢复原版本', 'needs_recovery': '需要恢复原版本', 'needs_review': '事务需要核对',
}


class ProgramUpdateRecoveryWindow:
    def __init__(self, ui):
        self.window = tk.Toplevel(ui.root)
        self.window.title('AutoZZZ · 本机更新事务')
        self.window.transient(ui.root)
        scale = lambda size: round(size * ui.ui_scale)
        self.window.minsize(scale(620), scale(340))
        self.window.columnconfigure(0, weight=1)
        self.window.rowconfigure(0, weight=1)
        frame, self.table = scroll_table(self.window, columns=('old', 'new', 'phase', 'created'),
                                         show='headings', style='Config.Treeview', selectmode='browse')
        frame.grid(row=0, column=0, sticky='nsew', padx=scale(16), pady=scale(12))
        for key, text, width in (('old', '原版本', 115), ('new', '更新版本', 115),
                                 ('phase', '阶段', 155), ('created', '时间（UTC）', 175)):
            self.table.heading(key, text=text)
            self.table.column(key, width=scale(width), minwidth=scale(75))
        self.table.bind('<<TreeviewSelect>>', lambda _: self.show_selected())
        footer = ttk.Frame(self.window, padding=(scale(16), scale(6), scale(16), scale(12)))
        footer.grid(row=1, column=0, sticky='ew')
        footer.columnconfigure(0, weight=1)
        self.detail = tk.StringVar(self.window)
        wrapped_label(footer, textvariable=self.detail, style='Hint.TLabel').grid(row=0, column=0, columnspan=3, sticky='ew', pady=(0,scale(10)))
        ttk.Button(footer, text='刷新', command=self.refresh).grid(row=1, column=0, sticky='w')
        self.folder = ttk.Button(footer, text='打开事务目录…', command=self.open_folder)
        self.folder.grid(row=1, column=1, padx=scale(8))
        ttk.Button(footer, text='关闭', command=self.window.destroy).grid(row=1, column=2)
        self.rows = {}
        self.refresh()

    def refresh(self):
        previous = self.table.selection()
        self.rows = {Path(row['transaction']).name: row for row in discover_transactions(ROOT)}
        self.table.delete(*self.table.get_children())
        for key, row in self.rows.items():
            self.table.insert('', 'end', iid=key, values=(row['old_version'], row['new_version'],
                              tr(_STAGES[row['stage']]), row['created'][:19].replace('T', ' ')))
        selected = previous[0] if previous and previous[0] in self.rows else next(iter(self.rows), None)
        if selected is not None:
            self.table.selection_set(selected)
        self.show_selected()

    def selected(self):
        selected = self.table.selection()
        return self.rows.get(selected[0]) if selected else None

    def show_selected(self):
        row = self.selected()
        self.folder.state(['!disabled'] if row else ['disabled'])
        if row is None:
            self.detail.set(tr('此安装目录没有本机更新事务。'))
            return
        lines = [tr(_STAGES[row['stage']]), row['transaction']]
        if row['actions']:
            lines.append(tr('关闭所有 AutoZZZ 进程后，在事务目录中运行所选恢复脚本。'))
            lines.append('  /  '.join(Path(path).name for path in row['actions'].values()))
        elif row['stage'] not in ('completed', 'rolled_back', 'cancelled'):
            lines.append(tr('当前没有可用的恢复脚本。'))
        if row['recovery_result'] is not None:
            lines.append(tr('已保存恢复结果')+' · recovery-result.json')
        if row['helper_result'] and row['helper_result']['state'] == 'failed' and row['stage'] not in ('completed','rolled_back'):
            lines.append(tr('上次更新助手报告失败')+' · helper.log')
        self.detail.set('\n'.join(lines))

    def open_folder(self):
        row = self.selected()
        if row is None:
            return
        try:
            if inspect_transaction(row['transaction'], target=ROOT) is None:
                raise ValueError('程序更新事务已变化，请刷新。')
            os.startfile(row['transaction'])
        except (KeyError, OSError, TypeError, ValueError) as exc:
            messagebox.showerror('AutoZZZ · 本机更新事务', str(exc), parent=self.window)


def open_program_update_recovery(ui):
    previous = getattr(ui, 'program_update_recovery_window', None)
    if previous is not None and previous.window.winfo_exists():
        previous.refresh()
        previous.window.deiconify()
        previous.window.lift()
        return previous
    ui.program_update_recovery_window = ProgramUpdateRecoveryWindow(ui)
    return ui.program_update_recovery_window
