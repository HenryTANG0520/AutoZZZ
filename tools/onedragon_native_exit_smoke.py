"""编译并关闭本工具创建的隐藏窗口，验证真实 WM_CLOSE 与进程句柄。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from autozzz.run_lock import RunLock
from autozzz.windows import GameWindow,user32,Geometry

SOURCE=r'''
using System;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Threading;
class Fixture {
    delegate IntPtr Proc(IntPtr hwnd,uint msg,IntPtr w,IntPtr l);
    [StructLayout(LayoutKind.Sequential,CharSet=CharSet.Unicode)] struct WC {
        public uint style; public Proc proc; public int extraClass,extraWindow;
        public IntPtr instance,icon,cursor,background; public string menu,name;
    }
    [StructLayout(LayoutKind.Sequential)] struct Point {public int x,y;}
    [StructLayout(LayoutKind.Sequential)] struct Msg {
        public IntPtr hwnd;public uint msg;public IntPtr w,l;public uint time;public Point point;public uint reserved;
    }
    [DllImport("kernel32.dll",CharSet=CharSet.Unicode)] static extern IntPtr GetModuleHandle(string name);
    [DllImport("user32.dll",CharSet=CharSet.Unicode)] static extern ushort RegisterClass(ref WC value);
    [DllImport("user32.dll",CharSet=CharSet.Unicode)] static extern IntPtr CreateWindowEx(uint ex,string name,string title,uint style,int x,int y,int w,int h,IntPtr parent,IntPtr menu,IntPtr instance,IntPtr data);
    [DllImport("user32.dll")] static extern bool DestroyWindow(IntPtr hwnd);
    [DllImport("user32.dll")] static extern void PostQuitMessage(int code);
    [DllImport("user32.dll",CharSet=CharSet.Unicode)] static extern IntPtr DefWindowProc(IntPtr hwnd,uint msg,IntPtr w,IntPtr l);
    [DllImport("user32.dll",CharSet=CharSet.Unicode)] static extern int GetMessage(out Msg msg,IntPtr hwnd,uint min,uint max);
    [DllImport("user32.dll",CharSet=CharSet.Unicode)] static extern IntPtr DispatchMessage(ref Msg msg);
    static Proc callback=OnMessage;
    static IntPtr OnMessage(IntPtr hwnd,uint msg,IntPtr w,IntPtr l) {
        if(msg==16){DestroyWindow(hwnd);return IntPtr.Zero;}
        if(msg==2){PostQuitMessage(0);return IntPtr.Zero;}
        return DefWindowProc(hwnd,msg,w,l);
    }
    static void Main() {
        WC wc=new WC();wc.proc=callback;wc.instance=GetModuleHandle(null);wc.name="AutoZZZOwnExitFixture";
        if(RegisterClass(ref wc)==0)throw new Exception("RegisterClass failed");
        IntPtr hwnd=CreateWindowEx(0,wc.name,"AutoZZZ isolated close verification",0,0,0,960,540,IntPtr.Zero,IntPtr.Zero,wc.instance,IntPtr.Zero);
        if(hwnd==IntPtr.Zero)throw new Exception("CreateWindow failed");
        Console.WriteLine("{\"hwnd\":"+hwnd.ToInt64()+",\"pid\":"+Process.GetCurrentProcess().Id+"}");Console.Out.Flush();
        Msg message;while(GetMessage(out message,IntPtr.Zero,0,0)>0)DispatchMessage(ref message);
        Thread.Sleep(1500); // 保留窗口已销毁但原进程尚未退出的可验证时段。
    }
}
'''


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--data',type=Path,required=True);args=parser.parse_args()
    data=args.data.resolve()
    if data.exists():raise SystemExit('请使用新的隔离验证目录。')
    data.mkdir(parents=True);(data/'.autozzz-native-exit-test').write_text('owned native fixture',encoding='utf-8')
    source=data/'Fixture.cs';executable=data/'ZenlessZoneZero.exe'
    source.write_text(SOURCE,encoding='utf-8')
    compiler=Path('C:/Windows/Microsoft.NET/Framework64/v4.0.30319/csc.exe')
    if not compiler.is_file():raise SystemExit('缺少本机 .NET C# 编译器；未启动测试程序。')
    flags=getattr(subprocess,'CREATE_NO_WINDOW',0)
    subprocess.run([str(compiler),'/nologo','/target:exe','/platform:x64','/out:'+str(executable),str(source)],check=True,capture_output=True,creationflags=flags)
    process=None;watch=None
    with RunLock():
        foreground=user32.GetForegroundWindow()
        try:
            process=subprocess.Popen([str(executable)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,creationflags=flags)
            info=json.loads(process.stdout.readline());assert info['pid']==process.pid
            window=GameWindow(info['hwnd']);assert Path(window.path).resolve()==executable
            assert not user32.IsWindowVisible(window.hwnd)
            window.guard=lambda *_,**__:Geometry(0,0,960,540)  # 只对自建隐藏窗口替代前台条件；实际身份校验保留。
            posts=[];original=user32.PostMessageW
            def post(hwnd,message,w,l):
                assert hwnd==info['hwnd'] and message==0x10
                posts.append(message);return original(hwnd,message,w,l)
            def stopped(action):raise RuntimeError('owned fixture stopped before submission')
            with patch.object(user32,'PostMessageW',side_effect=post):
                try:window.request_close(lambda:None,submit=stopped);raise AssertionError('stopped close sent input')
                except RuntimeError as exc:assert 'stopped before submission' in str(exc)
                assert not posts and process.poll() is None
                watch=window.request_close(lambda:None,submit=lambda action:action())
            deadline=time.monotonic()+5
            while user32.IsWindow(window.hwnd) and time.monotonic()<deadline:time.sleep(.01)
            assert not user32.IsWindow(window.hwnd) and process.poll() is None and not watch.exited()
            while not watch.exited() and time.monotonic()<deadline:time.sleep(.01)
            assert watch.exited() and process.wait(timeout=5)==0
            assert posts==[0x10] and user32.GetForegroundWindow()==foreground
        finally:
            if watch is not None:watch.close()
            if process is not None:
                if process.poll() is None:process.terminate()  # 仅清理上面 Popen 创建的测试进程。
                process.communicate(timeout=5)
    result=dict(checks=['owned_hidden_window_identity','control_guard_prevents_actual_post','one_actual_wm_close',
                        'destroyed_window_is_not_process_exit','retained_handle_confirms_actual_exit','foreground_unchanged'],
                game_accessed=False,game_input_sent=False)
    (data/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result))


if __name__=='__main__':main()
