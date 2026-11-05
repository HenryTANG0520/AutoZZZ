"""Explicit per-game Windows Auto HDR preferences and interrupted recovery."""
import json
import tkinter as tk
from tkinter import ttk,messagebox


class HdrWindow:
    def __init__(self,owner):
        self.owner=owner;self.snapshot=None;self.timer=None
        self.window=tk.Toplevel(owner.root);self.window.title('AutoZZZ · Auto HDR')
        self.window.geometry('680x400');self.window.minsize(460,320)
        frame=ttk.Frame(self.window,padding=20);frame.pack(fill='both',expand=True);frame.columnconfigure(0,weight=1)
        self.title=tk.StringVar(self.window,'读取该账号的游戏偏好…')
        ttk.Label(frame,textvariable=self.title,wraplength=600).grid(row=0,column=0,sticky='ew')
        self.details=tk.Text(frame,height=9,wrap='word',state='disabled');self.details.grid(row=1,column=0,sticky='nsew',pady=14);frame.rowconfigure(1,weight=1)
        row=ttk.Frame(frame);row.grid(row=2,column=0,sticky='ew')
        row.columnconfigure((0,1),weight=1)
        self.buttons=[]
        for label,command in (('读取状态',self.reload),('启用 Auto HDR',lambda:self.change(True)),('禁用 Auto HDR',lambda:self.change(False)),('恢复中断修改',self.recover)):
            button=ttk.Button(row,text=label,command=command);index=len(self.buttons)
            button.grid(row=index//2,column=index%2,sticky='ew',padx=(0,8),pady=4);self.buttons.append(button)
        ttk.Label(frame,text='只修改当前游戏的 Windows Auto HDR 偏好；重启游戏后生效。',wraplength=600).grid(row=3,column=0,sticky='ew',pady=(14,0))
        self.reload()
        self.window.bind('<Destroy>',self.destroyed,add='+')
        self.timer=self.window.after(100,self.poll)

    def destroyed(self,event):
        if event.widget is self.window and self.timer is not None:
            self.window.after_cancel(self.timer);self.timer=None

    def poll(self):
        if self.timer is not None:self.window.after_cancel(self.timer);self.timer=None
        if not self.window.winfo_exists():return
        if self.owner.closing or self.owner.closed:self.window.destroy();return
        for index,button in enumerate(self.buttons):
            pending=self.snapshot and self.snapshot['pending']
            enabled=not self.owner.busy and(index==0 or self.snapshot and(index in (1,2)and not pending or index==3 and pending))
            button.configure(state='normal'if enabled else'disabled')
        self.timer=self.window.after(100,self.poll)

    def show(self,value):
        if not self.window.winfo_exists():return
        self.snapshot=value;pending=value['pending']
        self.title.set(value['path'])
        lines=['注册表当前值：'+json.dumps(value['value'],ensure_ascii=False),'游戏内效果：尚未实测']
        if pending:
            lines+=['中断记录：'+value['journal_path'],'原值：'+json.dumps(pending['before'],ensure_ascii=False),'本次写入：'+json.dumps(pending['after'],ensure_ascii=False)]
            if value['conflict']:lines+=['系统偏好存在外部修改，自动恢复将拒绝覆盖。']
        self.details.configure(state='normal');self.details.delete('1.0','end');self.details.insert('1.0','\n'.join(lines));self.details.configure(state='disabled')
        for index,button in enumerate(self.buttons):button.configure(state='normal'if index==0 or index in (1,2)and not pending or index==3 and pending else'disabled')

    def request(self,method,**params):
        if self.owner.busy or self.owner.closing:return
        for button in self.buttons:button.configure(state='disabled')
        self.owner._run(lambda client:self.owner.call(client,method,**params),self.show)

    def reload(self):self.request('game_hdr_status')

    def change(self,enabled):
        if self.snapshot is None:return
        if messagebox.askyesno('修改该游戏的 Auto HDR',('启用'if enabled else'禁用')+' Auto HDR？此操作修改 Windows 中该游戏的 GPU 偏好。',parent=self.window):
            self.request('game_hdr_set',enabled=enabled,revision=self.snapshot['revision'],confirmed=True)

    def recover(self):
        if self.snapshot is None or self.snapshot['pending']is None:return
        if messagebox.askyesno('恢复中断修改','恢复所列事务的原始 GPU 偏好？外部修改不会被覆盖。',parent=self.window):
            self.request('game_hdr_recover',journal_digest=self.snapshot['journal_digest'],confirmed=True)
