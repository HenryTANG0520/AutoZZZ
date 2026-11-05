"""Actual screenshot preference form at three DPI scales, with SDK-generated fields."""
import argparse
import json
from pathlib import Path
import sys
import tkinter as tk

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from autozzz import windows,theme
from autozzz.overlay import NativePresentation
from autozzz.onedragon_forms import ScalarForm
from onedragon_configuration_gui_smoke import layout


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--fields',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    fields=json.loads(args.fields.read_text(encoding='utf-8'));foreground=windows.user32.GetForegroundWindow();cases=[]
    for scale in (1.,1.5,2.):
        root=tk.Tk();root.withdraw();root.tk.call('tk','scaling',scale*96/72)
        native=NativePresentation(protect_capture=False);handle=native.prepare(root)
        form=ScalarForm(root,fields,lambda:None,scale);form.frame.pack(fill='both',expand=True,padx=round(20*scale),pady=round(16*scale))
        try:
            for width,height in ((680,330),(520,280)):
                root.geometry(f'{round(width*scale)}x{round(height*scale)}');root.deiconify();root.update();layout(root)
                cases.append([scale,width,height])
            values=form.values();assert values['key_screenshot']=='Ctrl+Shift+P' and values['copy_screenshot'] is True
            field=form.controls['key_screenshot'];field.delete(0,'end');field.insert(0,'F11')
            assert form.values()['key_screenshot']=='F11'
        finally:root.destroy()
    assert foreground==windows.user32.GetForegroundWindow(),'Changed foreground'
    result=dict(layout_cases=cases,actual_schema_fields=True,editable_hotkey=True,copy_toggle=True,game_accessed=False,input_sent=False)
    (args.output/'result.json').write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result))


if __name__=='__main__':main()
