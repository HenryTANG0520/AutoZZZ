"""原生大地图草稿：取景、拼接、图标和缩放均在保存前可查看。"""
from __future__ import annotations
import base64
import copy
import io
import math
import tkinter as tk
from tkinter import filedialog,ttk
import uuid

from . import theme
from .onedragon_forms import ScalarForm


class MapDialog:
    def __init__(self,parent,title,fields,accept,scale):
        self.accept=accept;self.prepare=None
        d=lambda n:round(n*scale)
        self.window=tk.Toplevel(parent);self.window.title(title);self.window.transient(parent);self.window.configure(background=theme.PANEL)
        self.window.columnconfigure(0,weight=1)
        self.window.geometry(f"{min(d(600),parent.winfo_screenwidth()-d(50))}x{min(d(650),parent.winfo_screenheight()-d(90))}")
        self.window.rowconfigure(0,weight=1)
        body=ttk.Frame(self.window,padding=d(18));body.grid(row=0,column=0,sticky='nsew');body.columnconfigure(0,weight=1);body.rowconfigure(0,weight=1)
        canvas=tk.Canvas(body,background=theme.PANEL,highlightthickness=0);canvas.grid(row=0,column=0,sticky='nsew')
        scroll=ttk.Scrollbar(body,command=canvas.yview);scroll.grid(row=0,column=1,sticky='ns');canvas.configure(yscrollcommand=scroll.set)
        self.form=ScalarForm(canvas,fields,lambda:None,scale);anchor=canvas.create_window(0,0,window=self.form.frame,anchor='nw')
        self.form.frame.bind('<Configure>',lambda _:canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>',lambda e:canvas.itemconfigure(anchor,width=e.width))
        from .onedragon_forms import enable_scroll
        enable_scroll(canvas,self.form.frame)
        self.status=tk.StringVar(self.window)
        theme.wrapped_label(body,textvariable=self.status,style='Hint.TLabel').grid(row=1,column=0,columnspan=2,sticky='ew',pady=(d(10),0))
        actions=ttk.Frame(body);actions.grid(row=2,column=0,columnspan=2,sticky='e',pady=(d(12),0))
        ttk.Button(actions,text='确定',command=self.confirm).pack(side='left',padx=(0,d(8)))
        ttk.Button(actions,text='取消',command=self.window.destroy).pack(side='left')
        self.window.bind('<Destroy>',self.disposed,add='+');self.window.grab_set()

    def confirm(self):
        try:
            if self.prepare is not None:self.prepare()
            values=self.form.values()
            for field in self.form.fields:
                if field['kind'] not in ('int','float'):continue
                number=values[field['name']]
                if not math.isfinite(number) or field.get('minimum') is not None and number<field['minimum'] or field.get('maximum') is not None and number>field['maximum']:
                    raise ValueError(f"{field['label']} 须在 {field.get('minimum','不限')}—{field.get('maximum','不限')} 之间。")
        except ValueError as exc:self.status.set(str(exc));return
        callback=self.accept
        try:callback(values)
        except ValueError as exc:self.status.set(str(exc));return
        self.window.destroy()

    def disposed(self,event):
        if event.widget is self.window:self.form=self.status=self.accept=self.prepare=None


class MapEditor:
    def __init__(self,parent,document,action,capture,archives,scale,view=None):
        from PIL import Image
        self.document=copy.deepcopy(document);self.preview=document['preview'];self.action=action;self.capture=capture;self.archives=archives
        self.enabled=True;self.zoom=1. if view is None else view[0];self.pending_view=view;self.ratio=1.;self.dialog=None
        self.photo=self.mini_photo=self.overlay_photo=None;self.view_photos=[]
        self.image=None if not self.preview['png'] else Image.open(io.BytesIO(base64.b64decode(self.preview['png']))).convert('RGB')
        self.mini=None if not self.preview['mini_png'] else Image.open(io.BytesIO(base64.b64decode(self.preview['mini_png']))).convert('L')
        self.frame=ttk.Frame(parent);self.frame.columnconfigure(0,weight=1);self.frame.bind('<Destroy>',self.disposed,add='+')
        self.scale=scale;d=lambda n:round(n*scale)
        self.buttons={}
        first=ttk.Frame(self.frame);first.grid(row=0,column=0,sticky='w',pady=(0,d(8)))
        for label,command in (('单帧取景',lambda:self.capture(False)),('转镜头取景',lambda:self.capture(True)),('导入截图…',self.import_frames),('参数…',self.settings)):
            button=ttk.Button(first,text=label,command=command);button.pack(side='left',padx=(0,d(6)));self.buttons[label]=button
        second=ttk.Frame(self.frame);second.grid(row=1,column=0,sticky='w',pady=(0,d(10)))
        for label,command in (('定位',lambda:self.send('locate')),('合并到地图',lambda:self.send('merge')),('回退',lambda:self.send('undo')),('位置偏移…',self.offset),('缩放…',self.resize)):
            button=ttk.Button(second,text=label,command=command);button.pack(side='left',padx=(0,d(6)));self.buttons[label]=button
        info=f"{self.preview['width']} × {self.preview['height']} 像素 · 关联路线 {self.preview['route_count']} 条" if self.image else '尚无地图，请先取景或导入截图。'
        theme.wrapped_label(self.frame,text=info,style='Hint.TLabel').grid(row=2,column=0,sticky='ew',pady=(0,d(8)))
        area=ttk.Frame(self.frame);area.grid(row=3,column=0,sticky='ew');area.columnconfigure(0,weight=1)
        self.canvas=tk.Canvas(area,height=d(310),background='#141a1b',highlightthickness=0);self.canvas.grid(row=0,column=0,sticky='ew')
        vertical=ttk.Scrollbar(area,command=self.canvas.yview);vertical.grid(row=0,column=1,sticky='ns')
        horizontal=ttk.Scrollbar(area,orient='horizontal',command=self.canvas.xview);horizontal.grid(row=1,column=0,sticky='ew')
        self.canvas.configure(xscrollcommand=horizontal.set,yscrollcommand=vertical.set)
        self.canvas.bind('<Configure>',lambda _:self.draw());self.canvas.bind('<Button-1>',self.click)
        self.canvas.bind('<MouseWheel>',self.wheel);self.canvas.bind('<ButtonPress-3>',lambda e:self.canvas.scan_mark(e.x,e.y))
        self.canvas.bind('<B3-Motion>',lambda e:self.canvas.scan_dragto(e.x,e.y,gain=1))
        options=ttk.Frame(self.frame);options.grid(row=4,column=0,sticky='ew',pady=(d(8),d(8)));options.columnconfigure(1,weight=1)
        self.overlay=tk.BooleanVar(self.frame,True)
        ttk.Checkbutton(options,text='叠加小地图',variable=self.overlay,command=self.draw).grid(row=0,column=0,sticky='w',padx=(0,d(12)))
        ttk.Button(options,text='适应地图',command=self.fit).grid(row=0,column=2,sticky='e')
        text='点击地图设置候选中心 · 滚轮缩放 · 右键拖动'
        if self.preview['candidate'] is not None:text+=f"\n候选 {self.preview['candidate'][0]}, {self.preview['candidate'][1]} · 匹配值 {self.preview['match_value']:.3g}"
        if self.preview['warning']:text+='\n'+self.preview['warning']
        theme.wrapped_label(self.frame,text=text,style='Hint.TLabel').grid(row=5,column=0,sticky='ew',pady=(0,d(10)))
        self.mini_label=ttk.Frame(self.frame)
        if self.mini is not None:
            from PIL import ImageTk
            views=[]
            for i,encoded in enumerate(self.preview.get('views',[])):
                with Image.open(io.BytesIO(base64.b64decode(encoded))) as header:views.append((f'视角 {i+1}',header.copy()))
            views.append(('道路',self.mini.copy()))
            for i,(label,image) in enumerate(views):
                image.thumbnail((d(120),d(120)));photo=ImageTk.PhotoImage(image,master=self.frame);self.view_photos.append(photo)
                ttk.Label(self.mini_label,text=label,style='Hint.TLabel').grid(row=0,column=i,sticky='w',padx=(0,d(12)))
                ttk.Label(self.mini_label,image=photo).grid(row=1,column=i,sticky='w',padx=(0,d(12)))
            self.mini_label.grid(row=6,column=0,sticky='w',pady=(0,d(12)))
        table_frame=ttk.Frame(self.frame);table_frame.grid(row=7,column=0,sticky='ew');table_frame.columnconfigure(0,weight=1)
        self.table=ttk.Treeview(table_frame,columns=('name','type','position','landing'),show='headings',height=5,selectmode='browse',style='Config.Treeview')
        for name,label,width in (('name','名称',140),('type','图标',80),('position','中心坐标',100),('landing','落地点',100)):
            self.table.heading(name,text=label);self.table.column(name,width=d(width),minwidth=d(55))
        self.table.grid(row=0,column=0,sticky='ew')
        scroll=ttk.Scrollbar(table_frame,command=self.table.yview);scroll.grid(row=0,column=1,sticky='ns');self.table.configure(yscrollcommand=scroll.set)
        for row in self.preview['icons']:
            label=next((i['label'] for i in self.preview['templates'] if i['value']==row['template_id']),row['template_id'])
            self.table.insert('','end',iid=row['id'],values=(row['icon_name'] or '未命名',label,', '.join(map(str,row['lm_pos'])),', '.join(map(str,row['tp_pos']))))
        self.table.bind('<Double-1>',lambda _:self.edit_icon());self.table.bind('<<TreeviewSelect>>',lambda _:self.draw())
        edits=ttk.Frame(self.frame);edits.grid(row=8,column=0,sticky='w',pady=(d(8),0))
        for label,command in (('添加图标…',lambda:self.edit_icon(True)),('编辑图标…',self.edit_icon),('移除图标',self.remove_icon),('恢复归档…',self.archives)):
            button=ttk.Button(edits,text=label,command=command);button.pack(side='left',padx=(0,d(6)));self.buttons[label]=button
        if self.image is None:
            for name in ('定位','位置偏移…','缩放…','添加图标…','编辑图标…','移除图标'):self.buttons[name].state(['disabled'])
        if self.mini is None:
            for name in ('定位','合并到地图'):self.buttons[name].state(['disabled'])
        if not self.preview['undo']:self.buttons['回退'].state(['disabled'])
        self.draw()

    def values(self):return dict(self.document['data'])
    def view(self):return (self.zoom,self.canvas.xview()[0],self.canvas.yview()[0])
    def send(self,action,value=None):
        if self.enabled:self.action(action,value)

    def draw(self):
        if not self.canvas.winfo_exists():return
        from PIL import Image,ImageTk
        self.canvas.delete('all')
        if self.image is None:return
        width,height=self.preview['width'],self.preview['height']
        fit=min(max(1,self.canvas.winfo_width()-24)/width,max(1,self.canvas.winfo_height()-24)/height)
        self.ratio=min(fit*self.zoom,(12_000_000/(width*height))**.5)
        size=(max(1,round(width*self.ratio)),max(1,round(height*self.ratio)))
        self.photo=ImageTk.PhotoImage(self.image.resize(size,Image.Resampling.NEAREST),master=self.canvas)
        self.canvas.create_image(12,12,image=self.photo,anchor='nw');self.canvas.configure(scrollregion=(0,0,size[0]+24,size[1]+24))
        candidate=self.preview['candidate']
        if self.overlay.get() and self.mini is not None and candidate is not None:
            original=self.mini;pixels=(max(1,round(original.width*self.ratio)),max(1,round(original.height*self.ratio)))
            mask=original.resize(pixels,Image.Resampling.NEAREST);overlay=Image.new('RGBA',pixels,(45,225,155,0));overlay.putalpha(mask.point(lambda n:180 if n else 0))
            self.overlay_photo=ImageTk.PhotoImage(overlay,master=self.canvas)
            self.canvas.create_image(12+(candidate[0]-original.width//2)*self.ratio,12+(candidate[1]-original.height//2)*self.ratio,image=self.overlay_photo,anchor='nw')
        selection=self.table.selection() if hasattr(self,'table') else ()
        for row in self.preview['icons']:
            x,y=[12+value*self.ratio for value in row['lm_pos']];color='#f6ce43' if row['id'] in selection else '#58d8a7'
            self.canvas.create_oval(x-4,y-4,x+4,y+4,fill=color,outline='');self.canvas.create_text(x+6,y-6,text=row['icon_name'],anchor='sw',fill=color)
        point=candidate or self.preview['anchor']
        if point is not None:
            x,y=[12+value*self.ratio for value in point];self.canvas.create_oval(x-7,y-7,x+7,y+7,outline='#ffb454',width=2)
        if self.pending_view is not None and self.canvas.winfo_width()>100:
            _,x,y=self.pending_view;self.pending_view=None;self.canvas.xview_moveto(x);self.canvas.yview_moveto(y)

    def fit(self):self.zoom=1.;self.draw();self.canvas.xview_moveto(0);self.canvas.yview_moveto(0)
    def wheel(self,event):self.zoom=min(8.,max(.25,self.zoom*(1.2 if event.delta>0 else 1/1.2)));self.draw();return 'break'
    def click(self,event):
        if not self.enabled or self.image is None:return
        x,y=(self.canvas.canvasx(event.x)-12)/self.ratio,(self.canvas.canvasy(event.y)-12)/self.ratio
        if 0<=x<self.preview['width'] and 0<=y<self.preview['height']:
            self.send('anchor',[min(self.preview['width']-1,round(x)),min(self.preview['height']-1,round(y))])

    def import_frames(self):
        if not self.enabled:return
        paths=filedialog.askopenfilenames(parent=self.frame.winfo_toplevel(),title='同一位置的 1—2 张客户区截图',filetypes=[('客户区截图','*.png *.jpg *.jpeg')])
        if not paths:return
        if len(paths)>2:self.send('invalid_import','请选择同一位置的 1—2 张客户区截图。');return
        from .onedragon_route_recorder import encode_client_image
        try:images=[encode_client_image(path) for path in paths]
        except Exception as exc:self.send('invalid_import',str(exc));return
        self.send('import',images)

    def dialog_open(self,title,fields,accept):
        if not self.enabled:return
        if self.dialog is not None and self.dialog.window.winfo_exists():self.dialog.window.lift();return
        self.dialog=MapDialog(self.frame.winfo_toplevel(),title,fields,accept,self.scale)

    def offset(self):
        fields=[dict(name=name,label=label,kind='int',value=0,options=[],minimum=-10000,maximum=10000) for name,label in (('x','X 偏移（像素）'),('y','Y 偏移（像素）'))]
        self.dialog_open('位置偏移',fields,lambda values:self.send('offset',[values['x'],values['y']]))

    def resize(self):
        field=dict(name='percent',label='当前尺寸的（%）',kind='float',value=100.,options=[],minimum=25,maximum=400,hint=f"保存时同步关联路线坐标（本区域 {self.preview['route_count']} 条）。")
        self.dialog_open('缩放地图',[field],lambda values:self.send('scale',values['percent']))

    def settings(self):
        fields=[dict(name='threshold',label='图标匹配阈值',kind='float',value=self.preview['threshold'],options=[],minimum=.1,maximum=1.,hint='作用于之后的取景。'),
                dict(name='use_icons',label='优先用图标定位',kind='bool',value=self.preview['use_icons'],options=[])]
        self.dialog_open('定位参数',fields,lambda values:self.send('settings',values))

    def edit_icon(self,new=False):
        selection=self.table.selection()
        if not new and not selection:return
        point=self.preview['candidate'] or self.preview['anchor']
        if point is None:return
        original=dict(id=uuid.uuid4().hex,icon_name='',template_id='map_icon_01',lm_pos=point,tp_pos=point) if new else next(row for row in self.preview['icons'] if row['id']==selection[0])
        fields=[dict(name='name',label='名称（传送点必填）',kind='str',value=original['icon_name'],options=[]),dict(name='template',label='图标',kind='enum',value=original['template_id'],options=self.preview['templates'])]
        for prefix,label,point in (('lm','图标中心',original['lm_pos']),('tp','落地点',original['tp_pos'])):
            for i,axis in enumerate(('x','y')):fields.append(dict(name=prefix+'_'+axis,label=label+' '+axis.upper()+'（像素）',kind='int',value=point[i],options=[],minimum=0,maximum=self.preview['width' if i==0 else 'height']-1))
        fields.append(dict(name='same',label='落地点与图标中心相同',kind='bool',value=original['lm_pos']==original['tp_pos'],options=[]))
        def accept(values):
            name=values['name']
            if name!=name.strip() or len(name)>100 or any(ord(c)<32 for c in name):raise ValueError('名称须为不含控制字符的 0—100 字文本。')
            if values['template']=='map_icon_01' and not name:raise ValueError('请填写传送点名称。')
            lm=[values['lm_x'],values['lm_y']];tp=lm if values['same'] else [values['tp_x'],values['tp_y']]
            replacement=dict(id=original['id'],icon_name=values['name'],template_id=values['template'],lm_pos=lm,tp_pos=tp)
            rows=copy.deepcopy(self.preview['icons'])
            if new:rows.append(replacement)
            else:rows=[replacement if row['id']==original['id'] else row for row in rows]
            self.send('icons',rows)
        self.dialog_open('添加图标' if new else '编辑图标',fields,accept)
        if self.dialog is not None:
            form=self.dialog.form
            def sync_landing(_event=None):
                same=form.readers['same']()
                for axis in ('x','y'):
                    field=form.controls['tp_'+axis];field.state(['!disabled'])
                    if same:field.delete(0,'end');field.insert(0,form.controls['lm_'+axis].get());field.state(['disabled'])
            self.dialog.prepare=sync_landing
            form.controls['same'].configure(command=sync_landing)
            for axis in ('x','y'):form.controls['lm_'+axis].bind('<KeyRelease>',sync_landing,add='+')
            sync_landing()

    def remove_icon(self):
        selection=self.table.selection()
        if selection:self.send('icons',[row for row in self.preview['icons'] if row['id']!=selection[0]])

    def disposed(self,event):
        if event.widget is self.frame:
            self.photo=self.overlay_photo=self.mini_photo=self.image=self.mini=self.overlay=None
            self.view_photos.clear()
            self.action=self.capture=self.archives=None
