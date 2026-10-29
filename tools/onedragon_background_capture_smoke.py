"""只截取本工具创建的原生窗口，验证真实 PrintWindow、进程绑定及不抢焦点。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace as NS
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

SOURCE=r'''
using System;
using System.Diagnostics;
using System.Runtime.InteropServices;
class Fixture {
    delegate IntPtr Proc(IntPtr hwnd,uint msg,IntPtr w,IntPtr l);
    [StructLayout(LayoutKind.Sequential,CharSet=CharSet.Unicode)] struct WC {
        public uint style;public Proc proc;public int extraClass,extraWindow;
        public IntPtr instance,icon,cursor,background;public string menu,name;
    }
    [StructLayout(LayoutKind.Sequential)] struct Point {public int x,y;}
    [StructLayout(LayoutKind.Sequential)] struct Msg {public IntPtr hwnd;public uint msg;public IntPtr w,l;public uint time;public Point point;public uint reserved;}
    [StructLayout(LayoutKind.Sequential)] struct Rect {public int left,top,right,bottom;}
    [DllImport("kernel32.dll",CharSet=CharSet.Unicode)] static extern IntPtr GetModuleHandle(string name);
    [DllImport("user32.dll",CharSet=CharSet.Unicode)] static extern ushort RegisterClass(ref WC value);
    [DllImport("user32.dll",CharSet=CharSet.Unicode)] static extern IntPtr CreateWindowEx(uint ex,string name,string title,uint style,int x,int y,int w,int h,IntPtr parent,IntPtr menu,IntPtr instance,IntPtr data);
    [DllImport("user32.dll")] static extern bool DestroyWindow(IntPtr hwnd);
    [DllImport("user32.dll")] static extern bool GetClientRect(IntPtr hwnd,out Rect rect);
    [DllImport("user32.dll")] static extern int FillRect(IntPtr dc,ref Rect rect,IntPtr brush);
    [DllImport("user32.dll")] static extern IntPtr GetDC(IntPtr hwnd);
    [DllImport("user32.dll")] static extern int ReleaseDC(IntPtr hwnd,IntPtr dc);
    [DllImport("user32.dll")] static extern bool ValidateRect(IntPtr hwnd,IntPtr rect);
    [DllImport("gdi32.dll")] static extern IntPtr CreateSolidBrush(uint color);
    [DllImport("gdi32.dll")] static extern bool DeleteObject(IntPtr value);
    [DllImport("user32.dll")] static extern void PostQuitMessage(int code);
    [DllImport("user32.dll")] static extern IntPtr SetTimer(IntPtr hwnd,IntPtr id,uint delay,IntPtr callback);
    [DllImport("user32.dll")] static extern bool SetWindowPos(IntPtr hwnd,IntPtr after,int x,int y,int w,int h,uint flags);
    [DllImport("user32.dll")] static extern bool SetProcessDPIAware();
    [DllImport("user32.dll",CharSet=CharSet.Unicode)] static extern IntPtr DefWindowProc(IntPtr hwnd,uint msg,IntPtr w,IntPtr l);
    [DllImport("user32.dll",CharSet=CharSet.Unicode)] static extern int GetMessage(out Msg msg,IntPtr hwnd,uint min,uint max);
    [DllImport("user32.dll",CharSet=CharSet.Unicode)] static extern IntPtr DispatchMessage(ref Msg msg);
    static Proc callback=OnMessage;
    static void Paint(IntPtr hwnd,IntPtr dc) {
        Rect rect;GetClientRect(hwnd,out rect);int right=rect.right;rect.right=right/2;
        IntPtr red=CreateSolidBrush(0x002020c0);FillRect(dc,ref rect,red);DeleteObject(red);
        rect.left=right/2;rect.right=right;IntPtr blue=CreateSolidBrush(0x00c02020);FillRect(dc,ref rect,blue);DeleteObject(blue);
    }
    static IntPtr OnMessage(IntPtr hwnd,uint msg,IntPtr w,IntPtr l) {
        if(msg==0x317 || msg==0x318) {
            Console.Error.WriteLine("print-message "+msg+" dc="+w.ToInt64());
            Paint(hwnd,w);return IntPtr.Zero;
        }
        if(msg==0xF){IntPtr dc=GetDC(hwnd);Paint(hwnd,dc);ReleaseDC(hwnd,dc);ValidateRect(hwnd,IntPtr.Zero);return IntPtr.Zero;}
        if(msg==0x14){Paint(hwnd,w);return new IntPtr(1);}
        if(msg==16 || msg==0x113){DestroyWindow(hwnd);return IntPtr.Zero;}
        if(msg==2){PostQuitMessage(0);return IntPtr.Zero;}
        return DefWindowProc(hwnd,msg,w,l);
    }
    static void Main() {
        SetProcessDPIAware();
        WC wc=new WC();wc.proc=callback;wc.instance=GetModuleHandle(null);wc.name="AutoZZZOwnCaptureFixture";
        if(RegisterClass(ref wc)==0)throw new Exception("RegisterClass failed");
        IntPtr hwnd=CreateWindowEx(0x08000080,wc.name,"AutoZZZ isolated capture verification",0x90000000,-30000,-30000,320,180,IntPtr.Zero,IntPtr.Zero,wc.instance,IntPtr.Zero);
        if(hwnd==IntPtr.Zero)throw new Exception("CreateWindow failed");SetTimer(hwnd,new IntPtr(1),20000,IntPtr.Zero);
        SetWindowPos(hwnd,new IntPtr(1),20,20,320,180,0x50); // 最底层、不激活，允许窗口完成实际渲染。
        IntPtr dc=GetDC(hwnd);Paint(hwnd,dc);ReleaseDC(hwnd,dc);
        Console.WriteLine("{\"hwnd\":"+hwnd.ToInt64()+",\"pid\":"+Process.GetCurrentProcess().Id+"}");Console.Out.Flush();
        Msg message;while(GetMessage(out message,IntPtr.Zero,0,0)>0)DispatchMessage(ref message);
    }
}
'''


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--data',type=Path,required=True);args=parser.parse_args()
    data=args.data.resolve()
    if data.exists():raise ValueError('请使用新的隔离验证目录。')
    data.mkdir(parents=True);source=data/'Capture.cs';executable=data/'CaptureFixture.exe';source.write_text(SOURCE,encoding='utf-8')
    compiler=Path('C:/Windows/Microsoft.NET/Framework64/v4.0.30319/csc.exe')
    if not compiler.is_file():raise RuntimeError('缺少本机 C# 编译器。')
    flags=getattr(subprocess,'CREATE_NO_WINDOW',0)
    subprocess.run([str(compiler),'/nologo','/target:exe','/platform:x64','/out:'+str(executable),str(source)],check=True,capture_output=True,creationflags=flags)
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'vendor/onedragon/src'))
    from autozzz.onedragon_host import BoundGameWindow,WindowCapture
    from autozzz.windows import GameWindow,user32,kernel32,process_path
    from one_dragon.envs.env_config import ScreenshotMethodEnum
    foreground=user32.GetForegroundWindow();process=None;window=None;capture=None
    try:
        process=subprocess.Popen([str(executable)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,creationflags=flags)
        info=json.loads(process.stdout.readline());assert info['pid']==process.pid and process_path(info['hwnd']).casefold()==str(executable).casefold()
        time.sleep(.3)  # 允许测试窗口的消息循环和桌面合成器完成首帧。
        window=BoundGameWindow(1920,1080);window.bound=GameWindow(info['hwnd']);window.process_id=info['pid']
        window.process_handle=kernel32.OpenProcess(0x101000,False,info['pid']);assert window.process_handle and window.identity_valid();window.init_win()
        capture=WindowCapture(NS(using_background=True),window,1920,1080)
        def forbidden(*_,**__):raise AssertionError('只读窗口截图不得发出输入或激活窗口。')
        with patch('autozzz.windows.user32.SendInput',side_effect=forbidden),patch('autozzz.windows.user32.SetForegroundWindow',side_effect=forbidden):
            assert capture.init_screenshot('auto')==ScreenshotMethodEnum.PRINT_WINDOW.value.value
            image=capture.get_screenshot();assert image is not None and image.shape==(1080,1920,3)
            (data/'pixels.json').write_text(json.dumps(dict(min=int(image.min()),max=int(image.max()),mean=float(image.mean()))),encoding='utf-8')
            left=image[540,480].tolist();right=image[540,1440].tolist()
            assert left[0]>left[2]+80 and right[2]>right[0]+80,(left,right)
            independent=capture.get_screenshot(independent=True)
            assert independent is not None and independent.shape==image.shape
            assert independent[540,480,0]>independent[540,480,2]+80
            assert independent[540,1440,2]>independent[540,1440,0]+80
            assert user32.GetForegroundWindow()==foreground
            import cv2
            assert cv2.imwrite(str(data/'owned-window.png'),cv2.cvtColor(image,cv2.COLOR_RGB2BGR))
        result=dict(actual_print_window=True,actual_retained_process_identity=True,source_window_capture=True,foreground_unchanged=True,
                    independent_capture=True,bottom_owned_window=True,game_accessed=False,game_input_sent=False)
        (data/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False))
    finally:
        if capture is not None:capture.cleanup()
        if window is not None:window.clear_binding()
        if process is not None:
            if process.poll() is None:
                if 'info' in locals() and user32.IsWindow(info['hwnd']) and process_path(info['hwnd']).casefold()==str(executable).casefold():user32.PostMessageW(info['hwnd'],16,0,0)
                try:process.wait(timeout=5)
                except subprocess.TimeoutExpired:process.terminate();process.wait(timeout=5)  # 仅本工具创建、保有句柄的测试程序。
            (data/'fixture-stderr.log').write_text(process.stderr.read(),encoding='utf-8')
            assert user32.GetForegroundWindow()==foreground


if __name__=='__main__':main()
