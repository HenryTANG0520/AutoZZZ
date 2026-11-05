"""Presentation-only Tk adapters. Entry values, game names and logs stay raw."""
from __future__ import annotations
import functools
import re
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from .localization import tr, ui_language

_installed=False
_templates=None


def display_status(value):
    """Translate explicit status phrases/templates, retaining interpolated data."""
    global _templates
    if not isinstance(value,str)or ui_language()!='en':return value
    direct=tr(value)
    if direct!=value:return direct
    if _templates is None:
        from .localization_catalog import EN_UI
        from .localization_native import EN_NATIVE
        _templates=[]
        for source,target in {**EN_UI,**EN_NATIVE}.items():
            if '{'not in source:continue
            names=re.findall(r'\{(\w+)\}',source)
            if not names:continue
            chunks=re.split(r'\{\w+\}',source)
            pattern='^'+''.join(re.escape(chunk)+(f'(?P<{names[i]}>.+?)'if i<len(names)else'')for i,chunk in enumerate(chunks))+'$'
            _templates.append((re.compile(pattern),target))
    for pattern,target in _templates:
        match=pattern.fullmatch(value)
        if match:
            values=match.groupdict()
            if 'server'in values:values['server']=tr(values['server'])
            return target.format(**values)
    return value


class PresentationVar(tk.StringVar):
    """Tcl shows translated text; Python callers still receive source statuses.

    Only explicitly selected, output-only status variables use this type. No
    trace is installed and input variables/Combobox business values never use it.
    """
    def __init__(self,master=None,value=None,name=None):
        self.source=''if value is None else str(value)
        super().__init__(master,display_status(self.source),name)
    def set(self,value):
        self.source=str(value)
        super().set(display_status(self.source))
    def get(self):return self.source
    def set_status(self,status,detail=''):
        """Translate a status heading while retaining diagnostic/game text."""
        self.source=str(status)+(' · '+str(detail)if detail else'')
        super().set(display_status(str(status))+(' · '+str(detail)if detail else''))
    @property
    def display(self):return super().get()


def _options(options):
    values=dict(options)
    translate=values.pop('translate',True)
    if translate and 'text'in values:values['text']=tr(values['text'])
    return values


def install_tk_localization():
    """Install once, before building widgets. Never walks or rewrites user data."""
    global _installed
    if _installed:return
    _installed=True
    # Tk options, not arbitrary string variables or canvas/log content.
    for cls in (ttk.Label,ttk.Button,ttk.Checkbutton,ttk.Radiobutton,ttk.LabelFrame,ttk.Menubutton):
        original=cls.__init__
        @functools.wraps(original)
        def init(self,*args,_original=original,**kwargs):return _original(self,*args,**_options(kwargs))
        cls.__init__=init
    original=ttk.Widget.configure
    @functools.wraps(original)
    def configure(self,cnf=None,**kwargs):
        if isinstance(cnf,dict):cnf=_options(cnf)
        return original(self,cnf,**_options(kwargs))
    ttk.Widget.configure=configure;ttk.Widget.config=configure
    for cls,method in ((ttk.Notebook,'add'),(ttk.Notebook,'tab'),(ttk.Treeview,'heading')):
        original_method=getattr(cls,method)
        @functools.wraps(original_method)
        def wrapped(self,*args,_original=original_method,**kwargs):return _original(self,*args,**_options(kwargs))
        setattr(cls,method,wrapped)
    original_title=tk.Wm.wm_title
    @functools.wraps(original_title)
    def title(self,string=None):return original_title(self,tr(string)if string is not None else None)
    tk.Wm.wm_title=title;tk.Wm.title=title
    original_menu=tk.Menu.add
    @functools.wraps(original_menu)
    def menu_add(self,itemType,cnf=None,**kwargs):
        if isinstance(cnf,dict):
            cnf=dict(cnf)
            if 'label'in cnf:cnf['label']=tr(cnf['label'])
        if 'label'in kwargs:kwargs['label']=tr(kwargs['label'])
        return original_menu(self,itemType,cnf or {},**kwargs)
    tk.Menu.add=menu_add
    # Message text is exact vocabulary lookup only; exceptions/paths are preserved.
    original_show=messagebox._show
    @functools.wraps(original_show)
    def show(title=None,message=None,*args,**kwargs):return original_show(tr(title),tr(message),*args,**kwargs)
    messagebox._show=show
    original_dialog=filedialog._Dialog.__init__
    @functools.wraps(original_dialog)
    def dialog(self,master=None,**options):
        if 'title'in options:options['title']=tr(options['title'])
        return original_dialog(self,master,**options)
    filedialog._Dialog.__init__=dialog
