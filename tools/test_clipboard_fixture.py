"""离线回放专用剪贴板，不能调用 Windows 剪贴板。"""


class Clipboard:
    def __init__(self,value):self.value,self.sequence,self.opened=value,0,False
    def OpenClipboard(self):assert not self.opened;self.opened=True
    def CloseClipboard(self):assert self.opened;self.opened=False
    def IsClipboardFormatAvailable(self,fmt):assert self.opened and fmt==13;return self.value is not None
    def GetClipboardData(self,fmt):assert self.opened and fmt==13;return self.value
    def GetClipboardSequenceNumber(self):return self.sequence
    def EmptyClipboard(self):assert self.opened;self.value=None;self.sequence+=1
    def SetClipboardText(self,text,fmt):assert self.opened and fmt==13;self.value=text;self.sequence+=1
