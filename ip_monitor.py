#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
实时IP监测工具 - Windows桌面应用（桌面悬浮版）
功能：
  - 在桌面右下角显示两行IP信息（可拖动）
  - 实时监测境内外IP地址
  - 支持多API自动切换和重试机制
  - IP变化时颜色变化+声音提醒
  - 右键菜单：详情、刷新、设置、历史、重置位置、退出
"""

import json
import os
import re
import sys
import time
import queue
import threading
import argparse
import ctypes
from ctypes import wintypes
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Tuple

# ctypes.wintypes 兼容
if not hasattr(ctypes, 'wintypes'):
    ctypes.wintypes = wintypes

import requests
from PIL import Image, ImageDraw, ImageFont
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext


def _setup_tk_window(root: tk.Tk, base_w: int, base_h: int,
                     min_w: int, min_h: int) -> Tuple[int, int]:
    """按当前屏幕和 Tk 缩放设置窗口大小，并居中放入可见区域。"""
    try:
        root.update_idletasks()
        scale = float(root.tk.call('tk', 'scaling')) / (96.0 / 72.0)
    except Exception:
        scale = 1.0
    scale = max(1.0, min(scale, 1.8))
    try:
        sw = max(320, root.winfo_screenwidth())
        sh = max(240, root.winfo_screenheight())
    except Exception:
        sw, sh = 1024, 768
    w = max(min_w, int(round(base_w * scale)))
    h = max(min_h, int(round(base_h * scale)))
    w = min(w, max(min_w, sw - 80))
    h = min(h, max(min_h, sh - 100))
    x = max(0, (sw - w) // 2)
    y = max(0, (sh - h) // 2)
    root.minsize(min(min_w, w), min(min_h, h))
    root.geometry(f"{w}x{h}+{x}+{y}")
    return w, h


# ==================== Win32 API 常量 ====================

WM_USER = 0x0400
WM_NULL = 0x0000
WM_QUIT = 0x0012
WM_PAINT = 0x000F
WM_TIMER = 0x0113
WM_COMMAND = 0x0111
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
WM_MOVE = 0x0003
WM_SIZE = 0x0005
WM_WINDOWPOSCHANGING = 0x0046
WM_DISPLAYCHANGE = 0x007E
WM_SETTINGCHANGE = 0x001A
WM_DPICHANGED = 0x02E0
WM_CONTEXTMENU = 0x007B
WM_NCHITTEST = 0x0084
WM_NCLBUTTONDOWN = 0x00A1
WM_NCLBUTTONUP = 0x00A2
WM_NCLBUTTONDBLCLK = 0x00A3
WM_MOVING = 0x0216
WM_ERASEBKGND = 0x0014
WM_SHELLHOOK = 0x0318
WM_CREATE = 0x0001
WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_MOUSEMOVE = 0x0200
WM_RBUTTONDOWN = 0x0204
WM_MBUTTONUP = 0x0208
HSHELL_WINDOWMOVED = 4
HSHELL_TASKMAN = 4

# 窗口类样式
CS_DBLCLKS = 0x0008
CS_HREDRAW = 0x0002
CS_VREDRAW = 0x0001

# 窗口样式
WS_OVERLAPPED = 0x00000000
WS_POPUP = 0x80000000
WS_CHILD = 0x40000000
WS_MINIMIZE = 0x20000000
WS_VISIBLE = 0x10000000
WS_CLIPSIBLINGS = 0x04000000
WS_CLIPCHILDREN = 0x02000000

# 扩展窗口样式
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_TOPMOST = 0x00000008
WS_EX_NOACTIVATE = 0x08000000
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_APPWINDOW = 0x00040000

GWLP_WNDPROC = -4
GWL_EXSTYLE = -20
GWL_STYLE = -16

TPM_LEFTALIGN = 0x0000
TPM_RETURNCMD = 0x0100
TPM_RIGHTBUTTON = 0x0002
TPM_NONOTIFY = 0x0080

LWA_COLORKEY = 0x00000001
LWA_ALPHA = 0x00000002

HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
SWP_NOZORDER = 0x0004
SWP_HIDEWINDOW = 0x0080

MF_ENABLED = 0x0
MF_GRAYED = 0x1
MF_SEPARATOR = 0x0800
MF_POPUP = 0x00000010

# 鼠标光标
IDC_HAND = 32649
IDC_ARROW = 32512
IDC_SIZEALL = 32646  # 四向箭头，提示窗口可拖动

SM_CXSCREEN = 0
SM_CYSCREEN = 1
SM_CXFULLSCREEN = 16
SM_CYFULLSCREEN = 17
SM_CXSMICON = 49
SM_CYSMICON = 50
SM_CYMINTRACK = 0x22
SM_CXMINTRACK = 0x21

SPI_GETWORKAREA = 48
MONITOR_DEFAULTTONEAREST = 2

SWP = ctypes.c_uint
PUL = ctypes.POINTER(ctypes.c_ulong)


# ==================== Win32 结构体 ====================

class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class POINT(ctypes.Structure):
    _fields_ = [
        ("x", ctypes.c_long),
        ("y", ctypes.c_long),
    ]


class WINDOWPOS(ctypes.Structure):
    _fields_ = [
        ("hwnd", ctypes.c_void_p),
        ("hwndInsertAfter", ctypes.c_void_p),
        ("x", ctypes.c_int),
        ("y", ctypes.c_int),
        ("cx", ctypes.c_int),
        ("cy", ctypes.c_int),
        ("flags", ctypes.c_uint),
    ]


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", ctypes.c_void_p),
        ("message", ctypes.c_uint),
        ("wParam", ctypes.c_ulong),
        ("lParam", ctypes.c_long),
        ("time", ctypes.c_ulong),
        ("pt", POINT),
    ]


class LOGFONTW(ctypes.Structure):
    _fields_ = [
        ("lfHeight", ctypes.c_long),
        ("lfWidth", ctypes.c_long),
        ("lfEscapement", ctypes.c_long),
        ("lfOrientation", ctypes.c_long),
        ("lfWeight", ctypes.c_long),
        ("lfItalic", ctypes.c_byte),
        ("lfUnderline", ctypes.c_byte),
        ("lfStrikeOut", ctypes.c_byte),
        ("lfCharSet", ctypes.c_byte),
        ("lfOutPrecision", ctypes.c_byte),
        ("lfClipPrecision", ctypes.c_byte),
        ("lfQuality", ctypes.c_byte),
        ("lfPitchAndFamily", ctypes.c_byte),
        ("lfFaceName", ctypes.c_wchar * 32),
    ]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", ctypes.c_uint),
        ("lpfnWndProc", ctypes.c_void_p),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", ctypes.c_void_p),
        ("hIcon", ctypes.c_void_p),
        ("hCursor", ctypes.c_void_p),
        ("hbrBackground", ctypes.c_void_p),
        ("lpszMenuName", ctypes.c_wchar_p),
        ("lpszClassName", ctypes.c_wchar_p),
    ]


class PAINTSTRUCT(ctypes.Structure):
    _fields_ = [
        ("hdc", ctypes.c_void_p),
        ("fErase", ctypes.c_int),
        ("rcPaint", RECT),
        ("fRestore", ctypes.c_int),
        ("fIncUpdate", ctypes.c_int),
        ("rgbReserved", ctypes.c_byte * 32),
    ]


user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

GetModuleHandleW = kernel32.GetModuleHandleW
GetModuleHandleW.restype = ctypes.c_void_p
GetModuleHandleW.argtypes = [ctypes.c_wchar_p]

RegisterClassW = user32.RegisterClassW
RegisterClassW.restype = ctypes.c_uint
RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]

CreateWindowExW = user32.CreateWindowExW
CreateWindowExW.restype = ctypes.c_void_p
CreateWindowExW.argtypes = [
    ctypes.c_uint, ctypes.c_wchar_p, ctypes.c_wchar_p,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p
]

ShowWindow = user32.ShowWindow
ShowWindow.restype = ctypes.wintypes.BOOL
ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]

SetWindowPos = user32.SetWindowPos
SetWindowPos.restype = ctypes.wintypes.BOOL
SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]

GetWindowRect = user32.GetWindowRect
GetWindowRect.restype = ctypes.wintypes.BOOL
GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(RECT)]

SetLayeredWindowAttributes = user32.SetLayeredWindowAttributes
SetLayeredWindowAttributes.restype = ctypes.wintypes.BOOL
SetLayeredWindowAttributes.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ubyte, ctypes.c_uint]

GetDC = user32.GetDC
GetDC.restype = ctypes.c_void_p
GetDC.argtypes = [ctypes.c_void_p]

ReleaseDC = user32.ReleaseDC
ReleaseDC.restype = ctypes.c_int
ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]

GetSystemMetrics = user32.GetSystemMetrics
GetSystemMetrics.restype = ctypes.c_int
GetSystemMetrics.argtypes = [ctypes.c_int]

SystemParametersInfoW = user32.SystemParametersInfoW
SystemParametersInfoW.restype = ctypes.wintypes.BOOL
SystemParametersInfoW.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]

GetCursorPos = user32.GetCursorPos
GetCursorPos.restype = ctypes.wintypes.BOOL
GetCursorPos.argtypes = [ctypes.POINTER(POINT)]

ScreenToClient = user32.ScreenToClient
ScreenToClient.restype = ctypes.wintypes.BOOL
ScreenToClient.argtypes = [ctypes.c_void_p, ctypes.POINTER(POINT)]

GetAsyncKeyState = user32.GetAsyncKeyState
GetAsyncKeyState.restype = ctypes.c_short
GetAsyncKeyState.argtypes = [ctypes.c_int]

FindWindowW = user32.FindWindowW
FindWindowW.restype = ctypes.c_void_p
FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]

FindWindowExW = user32.FindWindowExW
FindWindowExW.restype = ctypes.c_void_p
FindWindowExW.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p]

GetWindowLongW = user32.GetWindowLongW
GetWindowLongW.restype = ctypes.c_long
GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]

SetWindowLongW = user32.SetWindowLongW
SetWindowLongW.restype = ctypes.c_long
SetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_long]

CallWindowProcW = user32.CallWindowProcW
CallWindowProcW.restype = ctypes.c_long
CallWindowProcW.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint, ctypes.c_long, ctypes.c_long]

DefWindowProcW = user32.DefWindowProcW
DefWindowProcW.restype = ctypes.c_long
DefWindowProcW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_long, ctypes.c_long]

RegisterShellHookWindow = user32.RegisterShellHookWindow
RegisterShellHookWindow.restype = ctypes.wintypes.BOOL
RegisterShellHookWindow.argtypes = [ctypes.c_void_p]

DeregisterShellHookWindow = user32.DeregisterShellHookWindow
DeregisterShellHookWindow.restype = ctypes.wintypes.BOOL
DeregisterShellHookWindow.argtypes = [ctypes.c_void_p]

GetMessageW = user32.GetMessageW
GetMessageW.restype = ctypes.wintypes.BOOL
GetMessageW.argtypes = [ctypes.POINTER(MSG), ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint]

PeekMessageW = user32.PeekMessageW
PeekMessageW.restype = ctypes.wintypes.BOOL
PeekMessageW.argtypes = [ctypes.POINTER(MSG), ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint]

TranslateMessage = user32.TranslateMessage
TranslateMessage.restype = ctypes.wintypes.BOOL
TranslateMessage.argtypes = [ctypes.POINTER(MSG)]

DispatchMessageW = user32.DispatchMessageW
DispatchMessageW.restype = ctypes.c_long
DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]

PostMessageW = user32.PostMessageW
PostMessageW.restype = ctypes.wintypes.BOOL
PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_long, ctypes.c_long]

TrackPopupMenu = user32.TrackPopupMenu
TrackPopupMenu.restype = ctypes.c_uint
TrackPopupMenu.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_int, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]

SetForegroundWindow = user32.SetForegroundWindow
SetForegroundWindow.restype = ctypes.wintypes.BOOL
SetForegroundWindow.argtypes = [ctypes.c_void_p]

SetActiveWindow = user32.SetActiveWindow
SetActiveWindow.restype = ctypes.c_void_p
SetActiveWindow.argtypes = [ctypes.c_void_p]

IsWindow = user32.IsWindow
IsWindow.restype = ctypes.wintypes.BOOL
IsWindow.argtypes = [ctypes.c_void_p]

DestroyWindow = user32.DestroyWindow
DestroyWindow.restype = ctypes.wintypes.BOOL
DestroyWindow.argtypes = [ctypes.c_void_p]

CreatePopupMenu = user32.CreatePopupMenu
CreatePopupMenu.restype = ctypes.c_void_p
CreatePopupMenu.argtypes = []

AppendMenuW = user32.AppendMenuW
AppendMenuW.restype = ctypes.wintypes.BOOL
AppendMenuW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint, ctypes.c_wchar_p]

DestroyMenu = user32.DestroyMenu
DestroyMenu.restype = ctypes.wintypes.BOOL
DestroyMenu.argtypes = [ctypes.c_void_p]

SetTimer = user32.SetTimer
SetTimer.restype = ctypes.c_uint
SetTimer.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p]

KillTimer = user32.KillTimer
KillTimer.restype = ctypes.wintypes.BOOL
KillTimer.argtypes = [ctypes.c_void_p, ctypes.c_uint]

InvalidateRect = user32.InvalidateRect
InvalidateRect.restype = ctypes.wintypes.BOOL
InvalidateRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(RECT), ctypes.c_bool]

UpdateWindow = user32.UpdateWindow
UpdateWindow.restype = ctypes.wintypes.BOOL
UpdateWindow.argtypes = [ctypes.c_void_p]

IsWindowVisible = user32.IsWindowVisible
IsWindowVisible.restype = ctypes.wintypes.BOOL
IsWindowVisible.argtypes = [ctypes.c_void_p]

GetClientRect = user32.GetClientRect
GetClientRect.restype = ctypes.wintypes.BOOL
GetClientRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(RECT)]

RedrawWindow = user32.RedrawWindow
RedrawWindow.restype = ctypes.wintypes.BOOL
RedrawWindow.argtypes = [ctypes.c_void_p, ctypes.POINTER(RECT), ctypes.c_void_p, ctypes.c_uint]

DrawMenuBar = user32.DrawMenuBar
DrawMenuBar.restype = ctypes.wintypes.BOOL
DrawMenuBar.argtypes = [ctypes.c_void_p]

MonitorFromWindow = user32.MonitorFromWindow
MonitorFromWindow.restype = ctypes.c_void_p
MonitorFromWindow.argtypes = [ctypes.c_void_p, ctypes.c_uint]

MonitorFromPoint = user32.MonitorFromPoint
MonitorFromPoint.restype = ctypes.c_void_p
MonitorFromPoint.argtypes = [POINT, ctypes.c_uint]

class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_ulong),
        ("rcMonitor", RECT),
        ("rcWork", RECT),
        ("dwFlags", ctypes.c_ulong),
    ]

class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", ctypes.c_uint32),
        ("biWidth", ctypes.c_long),
        ("biHeight", ctypes.c_long),
        ("biPlanes", ctypes.c_ushort),
        ("biBitCount", ctypes.c_ushort),
        ("biCompression", ctypes.c_uint32),
        ("biSizeImage", ctypes.c_uint32),
        ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long),
        ("biClrUsed", ctypes.c_uint32),
        ("biClrImportant", ctypes.c_uint32),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [
        ("bmiHeader", BITMAPINFOHEADER),
        ("bmiColors", ctypes.c_uint32 * 1),
    ]


class ICONINFO(ctypes.Structure):
    _fields_ = [
        ("fIcon", ctypes.wintypes.BOOL),
        ("xHotspot", ctypes.c_uint32),
        ("yHotspot", ctypes.c_uint32),
        ("hbmMask", ctypes.c_void_p),
        ("hbmColor", ctypes.c_void_p),
    ]

GetMonitorInfoW = user32.GetMonitorInfoW
GetMonitorInfoW.restype = ctypes.wintypes.BOOL
GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(MONITORINFO)]

# 显式加载 gdi32.dll（PyInstaller 打包后 ctypes.windll.gdi32 可能不可靠）
# 注意：FillRect 和 DrawTextW 在 gdi32 的 API Set 中找不到，必须用 user32
gdi32 = ctypes.WinDLL("gdi32.dll")
gdi32.CreateSolidBrush.restype = ctypes.c_void_p
gdi32.CreateSolidBrush.argtypes = [ctypes.c_ulong]
gdi32.DeleteObject.restype = ctypes.c_int
gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
gdi32.CreatePen.restype = ctypes.c_void_p
gdi32.CreatePen.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_ulong]
gdi32.SelectObject.restype = ctypes.c_void_p
gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
gdi32.GetStockObject.restype = ctypes.c_void_p
gdi32.GetStockObject.argtypes = [ctypes.c_int]
gdi32.MoveToEx.restype = ctypes.c_int
gdi32.MoveToEx.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
gdi32.LineTo.restype = ctypes.c_int
gdi32.LineTo.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
gdi32.SetBkMode.restype = ctypes.c_int
gdi32.SetBkMode.argtypes = [ctypes.c_void_p, ctypes.c_int]
gdi32.SetTextColor.restype = ctypes.c_ulong
gdi32.SetTextColor.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
# 关键：必须显式绑定 Ellipse/Rectangle 的 argtypes，否则 ctypes 默认用 c_int 接收 HDC
# 会因为 HDC 高位符号扩展（>0x80000000）而 OverflowError: int too long to convert
gdi32.Ellipse.restype = ctypes.c_int
gdi32.Ellipse.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
gdi32.Rectangle.restype = ctypes.c_int
gdi32.Rectangle.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
gdi32.CreateFontIndirectW.restype = ctypes.c_void_p
gdi32.CreateFontIndirectW.argtypes = [ctypes.POINTER(LOGFONTW)]
gdi32.CreateDIBSection.restype = ctypes.c_void_p
gdi32.CreateDIBSection.argtypes = [
    ctypes.c_void_p, ctypes.POINTER(BITMAPINFO), ctypes.c_uint,
    ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, ctypes.c_uint32
]
gdi32.CreateBitmap.restype = ctypes.c_void_p
gdi32.CreateBitmap.argtypes = [
    ctypes.c_int, ctypes.c_int, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p
]

# FillRect 和 DrawTextW 必须从 user32 拿（gdi32 API Set 中找不到）
user32.FillRect.restype = ctypes.c_int
user32.FillRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(RECT), ctypes.c_void_p]
user32.DrawTextW.restype = ctypes.c_int
user32.DrawTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int, ctypes.POINTER(RECT), ctypes.c_uint]

LoadImageW = user32.LoadImageW
LoadImageW.restype = ctypes.c_void_p
LoadImageW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_int, ctypes.c_int, ctypes.c_uint]

CreateIconIndirect = user32.CreateIconIndirect
CreateIconIndirect.restype = ctypes.c_void_p
CreateIconIndirect.argtypes = [ctypes.POINTER(ICONINFO)]

OleInitialize = ctypes.windll.ole32.OleInitialize
OleInitialize.restype = ctypes.HRESULT
OleInitialize.argtypes = [ctypes.c_void_p]

OleUninitialize = ctypes.windll.ole32.OleUninitialize

SHGetSpecialFolderPathW = ctypes.windll.shell32.SHGetSpecialFolderPathW
SHGetSpecialFolderPathW.restype = ctypes.wintypes.BOOL
SHGetSpecialFolderPathW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int, ctypes.wintypes.BOOL]

# shell32 完整绑定（托盘图标用）
shell32 = ctypes.WinDLL("shell32.dll")
Shell_NotifyIconW = shell32.Shell_NotifyIconW
Shell_NotifyIconW.restype = ctypes.wintypes.BOOL
Shell_NotifyIconW.argtypes = [ctypes.c_uint, ctypes.c_void_p]

DestroyIcon = user32.DestroyIcon
DestroyIcon.restype = ctypes.wintypes.BOOL
DestroyIcon.argtypes = [ctypes.c_void_p]

get_system_metrics = GetSystemMetrics
screen_w = get_system_metrics(SM_CXSCREEN)
screen_h = get_system_metrics(SM_CYSCREEN)

try:
    OleInitialize(None)
    _ole_ok = True
except Exception:
    _ole_ok = False


def _enable_process_dpi_awareness():
    """尽早启用 DPI 感知，避免窗口创建后再切换导致尺寸失真。"""
    try:
        SetProcessDpiAwarenessContext = getattr(user32, 'SetProcessDpiAwarenessContext', None)
        if SetProcessDpiAwarenessContext:
            SetProcessDpiAwarenessContext.restype = ctypes.wintypes.BOOL
            SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
            if SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):  # PER_MONITOR_AWARE_V2
                return
    except Exception:
        pass

    try:
        shcore = ctypes.WinDLL("shcore.dll")
        SetProcessDpiAwareness = getattr(shcore, 'SetProcessDpiAwareness', None)
        if SetProcessDpiAwareness:
            SetProcessDpiAwareness.restype = ctypes.HRESULT
            SetProcessDpiAwareness.argtypes = [ctypes.c_int]
            SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
            return
    except Exception:
        pass

    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass


# ==================== 数据模型 ====================

@dataclass
class IPInfo:
    """IP信息数据类"""
    ip: str = ""
    location: str = ""
    success: bool = False
    api_used: str = ""
    check_time: datetime = field(default_factory=datetime.now)


@dataclass
class CheckResult:
    """检测结果数据类"""
    domestic: IPInfo = field(default_factory=IPInfo)
    foreign: IPInfo = field(default_factory=IPInfo)
    is_same: bool = False
    timestamp: datetime = field(default_factory=datetime.now)


# ==================== 配置管理 ====================

class ConfigManager:
    """配置文件管理器"""

    DEFAULT_CONFIG = {
        "check_interval": 30,
        "domestic_apis": [
            "https://myip.ipip.net",
            "https://www.cip.cc",
            "https://ip.sb/geoip"
        ],
        "foreign_apis": [
            "https://api.ipify.org?format=json",
            "https://ipinfo.io/json",
            "https://api.ip.sb/geoip"
        ],
        "alert_on_change": True,
        "alert_sound": True,
        "timeout": 5,
        "max_history": 100
    }

    def __init__(self, config_path: str = "config.json"):
        self.config_path = config_path
        self.config = self.DEFAULT_CONFIG.copy()
        self.load()

    def load(self):
        """加载配置文件"""
        try:
            if os.path.exists(self.config_path):
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    user_config = json.load(f)
                    for key in self.DEFAULT_CONFIG:
                        if key in user_config:
                            self.config[key] = user_config[key]
                    self._validate_config()
                print(f"[配置] 已加载配置文件: {self.config_path}")
            else:
                print(f"[配置] 配置文件不存在，使用默认配置")
                self.save()
        except json.JSONDecodeError as e:
            print(f"[配置] 配置文件JSON格式错误: {e}，使用默认配置")
        except Exception as e:
            print(f"[配置] 加载失败: {e}，使用默认配置")

    def _validate_config(self):
        """验证配置项的合法性"""
        try:
            interval = self.config.get('check_interval', 30)
            if not isinstance(interval, int) or interval < 5 or interval > 3600:
                print(f"[配置] 检测间隔值无效: {interval}，使用默认值30")
                self.config['check_interval'] = 30

            timeout = self.config.get('timeout', 5)
            if not isinstance(timeout, int) or timeout < 1 or timeout > 30:
                print(f"[配置] 超时时间无效: {timeout}，使用默认值5")
                self.config['timeout'] = 5

            domestic_apis = self.config.get('domestic_apis', [])
            foreign_apis = self.config.get('foreign_apis', [])
            if not isinstance(domestic_apis, list) or len(domestic_apis) == 0:
                self.config['domestic_apis'] = self.DEFAULT_CONFIG['domestic_apis']
            if not isinstance(foreign_apis, list) or len(foreign_apis) == 0:
                self.config['foreign_apis'] = self.DEFAULT_CONFIG['foreign_apis']

            max_history = self.config.get('max_history', 100)
            if not isinstance(max_history, int) or max_history < 10 or max_history > 10000:
                self.config['max_history'] = 100

        except Exception as e:
            print(f"[配置] 验证过程出错: {e}，使用默认配置")

    def save(self):
        """保存配置到文件"""
        try:
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, ensure_ascii=False, indent=2)
            print(f"[配置] 已保存配置文件")
        except Exception as e:
            print(f"[配置] 保存失败: {e}")

    def get(self, key: str, default=None):
        return self.config.get(key, default)

    def set(self, key: str, value):
        self.config[key] = value


# ==================== IP检测器 ====================

class IPDetector:
    """IP地址检测器"""

    IP_PATTERN = re.compile(r'\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b')

    def __init__(self, config: ConfigManager):
        self.config = config
        self.timeout = config.get('timeout', 5)

    def _make_request(self, url: str) -> Optional[str]:
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            response = requests.get(url, headers=headers, timeout=self.timeout)
            response.encoding = 'utf-8'
            return response.text
        except requests.exceptions.Timeout:
            print(f"[检测] 请求超时: {url}")
            return None
        except requests.exceptions.ConnectionError:
            print(f"[检测] 连接失败: {url}")
            return None
        except Exception as e:
            print(f"[检测] 请求异常: {url} - {e}")
            return None

    def _extract_ip(self, text: str) -> Optional[str]:
        if not text:
            return None
        match = self.IP_PATTERN.search(text)
        if match:
            ip = match.group()
            if self._is_valid_ip(ip):
                return ip
        return None

    def _is_valid_ip(self, ip: str) -> bool:
        try:
            parts = ip.split('.')
            if len(parts) != 4:
                return False
            for part in parts:
                num = int(part)
                if num < 0 or num > 255:
                    return False
            if ip.startswith('0.') or ip == '255.255.255.255':
                return False
            return True
        except (ValueError, AttributeError):
            return False

    def _parse_domestic_response(self, text: str, api_url: str) -> IPInfo:
        info = IPInfo(api_used=api_url, check_time=datetime.now())
        try:
            if 'ipip.net' in api_url:
                ip_match = re.search(r'当前\s*IP[：:]\s*([\d.]+)', text)
                loc_match = re.search(r'来自于[：:]\s*(.+)', text)
                if ip_match:
                    info.ip = ip_match.group(1).strip()
                    info.location = loc_match.group(1).strip() if loc_match else "未知"
                    info.success = True
            elif 'cip.cc' in api_url:
                lines = [l.strip() for l in text.split('\n') if l.strip()]
                for line in lines:
                    if 'IP' in line.upper():
                        ip = self._extract_ip(line)
                        if ip:
                            info.ip = ip
                    if '地址' in line or '地理位置' in line or '位置' in line:
                        parts = line.split('：', 1) if '：' in line else line.split(':', 1)
                        if len(parts) > 1 and not info.location:
                            info.location = parts[1].strip()
                if info.ip:
                    info.success = True
                    if not info.location:
                        info.location = "未知"
            elif 'ip.sb' in api_url:
                data = json.loads(text)
                info.ip = data.get('ip', '')
                city = data.get('city', '')
                country = data.get('country', '')
                region = data.get('region', '')
                if city and country:
                    info.location = f"{country} {region} {city}".strip()
                elif country:
                    info.location = f"{country} {region}".strip()
                else:
                    info.location = "未知"
                info.success = bool(info.ip)
            else:
                ip = self._extract_ip(text)
                if ip:
                    info.ip = ip
                    info.location = "已检测"
                    info.success = True
        except json.JSONDecodeError:
            ip = self._extract_ip(text)
            if ip:
                info.ip = ip
                info.location = "已检测"
                info.success = True
        except Exception as e:
            print(f"[检测] 解析异常 ({api_url}): {e}")
        return info

    def _parse_foreign_response(self, text: str, api_url: str) -> IPInfo:
        info = IPInfo(api_used=api_url, check_time=datetime.now())
        try:
            if 'ipify' in api_url:
                data = json.loads(text)
                info.ip = data.get('ip', '')
                info.location = "Unknown"
                info.success = bool(info.ip)
            elif 'ipinfo.io' in api_url:
                data = json.loads(text)
                info.ip = data.get('ip', '')
                country = data.get('country', '')
                city = data.get('city', '')
                org = data.get('org', '')
                if country and city:
                    info.location = f"{country} {city}"
                elif country:
                    info.location = country
                else:
                    info.location = org or "Unknown"
                info.success = bool(info.ip)
            elif 'ip.sb' in api_url:
                data = json.loads(text)
                info.ip = data.get('ip', '')
                country = data.get('country', '')
                city = data.get('city', '')
                if isinstance(country, str):
                    if city:
                        info.location = f"{country} {city}".strip()
                    else:
                        info.location = country
                elif isinstance(country, dict):
                    info.location = country.get('en', country.get('zh-CN', 'Unknown'))
                else:
                    info.location = "Unknown"
                info.success = bool(info.ip)
            else:
                ip = self._extract_ip(text)
                if ip:
                    info.ip = ip
                    info.location = "Unknown"
                    info.success = True
        except json.JSONDecodeError:
            ip = self._extract_ip(text)
            if ip:
                info.ip = ip
                info.location = "Unknown"
                info.success = True
        except Exception as e:
            print(f"[检测] 解析异常 ({api_url}): {e}")
        return info

    def detect_domestic(self) -> IPInfo:
        apis = self.config.get('domestic_apis', [])
        for api_url in apis:
            print(f"[检测] 尝试国内API: {api_url}")
            text = self._make_request(api_url)
            if text is not None:
                info = self._parse_domestic_response(text, api_url)
                if info.success:
                    print(f"[检测] 国内IP检测成功: {info.ip} ({info.location})")
                    return info
        print("[检测] 国内IP检测失败")
        return IPInfo(ip="检测失败", location="", success=False, check_time=datetime.now())

    def detect_foreign(self) -> IPInfo:
        apis = self.config.get('foreign_apis', [])
        for api_url in apis:
            print(f"[检测] 尝试国外API: {api_url}")
            text = self._make_request(api_url)
            if text is not None:
                info = self._parse_foreign_response(text, api_url)
                if info.success:
                    print(f"[检测] 国外IP检测成功: {info.ip} ({info.location})")
                    return info
        print("[检测] 国外IP检测失败")
        return IPInfo(ip="检测失败", location="", success=False, check_time=datetime.now())

    def check_both(self) -> CheckResult:
        domestic = self.detect_domestic()
        foreign = self.detect_foreign()
        is_same = (domestic.success and foreign.success and
                   domestic.ip != "检测失败" and foreign.ip != "检测失败" and
                   domestic.ip == foreign.ip)
        result = CheckResult(
            domestic=domestic,
            foreign=foreign,
            is_same=is_same,
            timestamp=datetime.now()
        )
        return result


# ==================== 系统托盘图标 ====================

# 托盘图标相关常量
NIM_ADD = 0x00000000
NIM_MODIFY = 0x00000001
NIM_DELETE = 0x00000002
NIM_SETVERSION = 0x00000004
NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004
NIF_INFO = 0x00000010
NIIF_INFO = 0x00000001
WM_USER = 0x0400
APP_TRAY_MSG = WM_USER + 20
ID_TRAY_SHOW = 1001
ID_TRAY_HIDE = 1002
ID_TRAY_CENTER = 1003
ID_TRAY_REFRESH = 1004
ID_TRAY_QUIT = 1005


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint),
        ("hWnd", ctypes.c_void_p),
        ("uID", ctypes.c_uint),
        ("uFlags", ctypes.c_uint),
        ("uCallbackMessage", ctypes.c_uint),
        ("hIcon", ctypes.c_void_p),
        ("szTip", ctypes.c_wchar * 128),
        ("dwState", ctypes.c_uint),
        ("dwStateMask", ctypes.c_uint),
        ("szInfo", ctypes.c_wchar * 256),
        ("uTimeoutOrVersion", ctypes.c_uint),
        ("szInfoTitle", ctypes.c_wchar * 64),
        ("dwInfoFlags", ctypes.c_uint),
        ("guidItem", ctypes.c_byte * 16),
        ("hBalloonIcon", ctypes.c_void_p),
    ]


class SystemTrayIcon:
    """系统托盘图标 - 兜底入口，用户找不到悬浮窗时可通过托盘菜单操作
    直接复用主悬浮窗的 hwnd 当消息接收者（避免单独创建消息窗口的 PyInstaller 兼容问题）"""

    # 辅助窗口类名（用于弹出菜单，非分层，避免 DWM 导致菜单文字空白）
    _MENU_HELPER_CLASS = "IPMonitor_MenuHelper"

    def __init__(self, app: 'IPMonitorApp'):
        self.app = app
        self.hwnd = None
        self.hmenu = None
        self.nid = None
        self.registered = False
        self._menu_helper_hwnd = None  # 非分层辅助窗口，专用于 TrackPopupMenu owner
        self._menu_helper_wndproc = None
        self._ico_path = None
        self._hicon = None
        self._menu_window = None
        self._menu_thread = None
        self._create_icon()
        # 等悬浮窗就绪再注册
        self._try_register()

    def _get_tray_icon_size(self) -> Tuple[int, int]:
        """获取当前系统托盘期望的小图标尺寸。"""
        try:
            w = GetSystemMetrics(SM_CXSMICON)
            h = GetSystemMetrics(SM_CYSMICON)
            if w > 0 and h > 0:
                return w, h
        except Exception:
            pass
        scale = 1.0
        try:
            if self.app and self.app.widget:
                scale = self.app.widget._scale()
        except Exception:
            pass
        size = max(16, min(64, int(round(16 * scale))))
        return size, size

    def _pil_to_hicon(self, image: Image.Image) -> Optional[int]:
        """把 RGBA PIL 图像直接转换成 Win32 HICON，避免 ICO 加载/缩放变空白。"""
        try:
            img = image.convert("RGBA")
            w, h = img.size
            bgra = bytearray()
            pixels = img.load()
            for y in range(h - 1, -1, -1):
                for x in range(w):
                    r, g, b, a = pixels[x, y]
                    bgra.extend((b, g, r, a))

            bmi = BITMAPINFO()
            bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            bmi.bmiHeader.biWidth = w
            bmi.bmiHeader.biHeight = h
            bmi.bmiHeader.biPlanes = 1
            bmi.bmiHeader.biBitCount = 32
            bmi.bmiHeader.biCompression = 0  # BI_RGB
            bits = ctypes.c_void_p()
            hbm_color = gdi32.CreateDIBSection(
                None, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
            if not hbm_color or not bits:
                return None
            ctypes.memmove(bits, bytes(bgra), len(bgra))
            mask_stride = ((w + 31) // 32) * 4
            mask = bytes(mask_stride * h)
            hbm_mask = gdi32.CreateBitmap(w, h, 1, 1, mask)
            if not hbm_mask:
                gdi32.DeleteObject(hbm_color)
                return None

            icon_info = ICONINFO()
            icon_info.fIcon = True
            icon_info.xHotspot = 0
            icon_info.yHotspot = 0
            icon_info.hbmMask = hbm_mask
            icon_info.hbmColor = hbm_color
            hicon = CreateIconIndirect(ctypes.byref(icon_info))
            gdi32.DeleteObject(hbm_color)
            gdi32.DeleteObject(hbm_mask)
            return hicon or None
        except Exception as e:
            self.app.logger.log(f"[托盘] PIL 转 HICON 失败: {e}", warning=True)
            return None

    def _try_register(self):
        """尝试注册托盘图标，复用主悬浮窗 hwnd"""
        try:
            # 等待主悬浮窗创建
            for _ in range(50):  # 最多等 5 秒
                if self.app.widget and self.app.widget.hwnd:
                    break
                time.sleep(0.1)
            if not (self.app.widget and self.app.widget.hwnd):
                self.app.logger.log("[托盘] 主悬浮窗未就绪，托盘图标注册放弃", warning=True)
                return
            # 复用主悬浮窗的 hwnd
            self.hwnd = self.app.widget.hwnd
            self._register()
        except Exception as e:
            self.app.logger.log(f"[托盘] 初始化失败: {e}", error=True)

    def _make_tray_icon_image(self, size: int) -> Image.Image:
        """生成在小尺寸下仍清晰可见的托盘图标。"""
        img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        pad = max(1, size // 10)
        radius = max(3, size // 5)
        draw.rounded_rectangle(
            [(pad, pad), (size - pad - 1, size - pad - 1)],
            radius=radius,
            fill=(13, 91, 190, 255),
            outline=(255, 255, 255, 255),
            width=max(1, size // 14))
        inner = max(1, size // 7)
        draw.rounded_rectangle(
            [(pad + inner, pad + inner),
             (size - pad - inner - 1, size - pad - inner - 1)],
            radius=max(2, radius // 2),
            outline=(0, 43, 100, 255),
            width=max(1, size // 22))
        dot_r = max(3, size // 6)
        dot_x = size - pad - dot_r
        dot_y = pad
        draw.ellipse(
            (dot_x, dot_y, dot_x + dot_r, dot_y + dot_r),
            fill=(34, 197, 94, 255),
            outline=(255, 255, 255, 255),
            width=max(1, size // 24))
        try:
            font = ImageFont.truetype("arialbd.ttf", max(8, int(size * 0.46)))
        except Exception:
            try:
                font = ImageFont.truetype("arial.ttf", max(8, int(size * 0.46)))
            except Exception:
                font = ImageFont.load_default()
        text = "IP"
        bbox = draw.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        tx = (size - tw) // 2 - bbox[0]
        ty = (size - th) // 2 - bbox[1] + max(0, size // 24)
        draw.text((tx, ty), text, fill=(255, 255, 255, 255), font=font)
        return img

    def _create_icon(self):
        """用 PIL 动态生成托盘图标（避免依赖外部 ico 文件）。"""
        try:
            # 保存到 EXE 同目录
            if getattr(sys, 'frozen', False):
                base_dir = os.path.dirname(os.path.abspath(sys.executable))
            else:
                base_dir = os.path.dirname(os.path.abspath(__file__))
            ico_path = os.path.join(base_dir, "_ipmonitor_tray.ico")
            sizes = [(16, 16), (20, 20), (24, 24), (32, 32), (40, 40),
                     (48, 48), (64, 64), (128, 128), (256, 256)]
            images = [self._make_tray_icon_image(s[0]) for s in sizes]
            images[-1].save(ico_path, format='ICO', sizes=sizes,
                            append_images=images[:-1])
            self._ico_path = ico_path
            self.app.logger.log(f"[托盘] 图标文件已生成: {ico_path} size={os.path.getsize(ico_path)}B")
        except Exception as e:
            self.app.logger.log(f"[托盘] 生成图标失败: {e}", warning=True)
            import traceback
            self.app.logger.log(traceback.format_exc(), warning=True)
            self._ico_path = None

    def _register(self):
        """向系统注册托盘图标"""
        try:
            nid = NOTIFYICONDATAW()
            nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
            nid.hWnd = self.hwnd
            nid.uID = 1
            nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
            nid.uCallbackMessage = APP_TRAY_MSG

            # 优先尝试加载生成的 ICO 文件，失败时回退到系统内置图标
            hicon = 0
            try:
                icon_w, icon_h = self._get_tray_icon_size()
                hicon = self._pil_to_hicon(self._make_tray_icon_image(max(icon_w, icon_h)))
                self.app.logger.log(
                    f"[托盘] CreateIconIndirect(内存) size={icon_w}x{icon_h} hIcon={hicon}")
            except Exception as e:
                self.app.logger.log(f"[托盘] 内存图标创建异常: {e}", warning=True)

            if not hicon and self._ico_path and os.path.exists(self._ico_path):
                try:
                    icon_w, icon_h = self._get_tray_icon_size()
                    # LR_LOADFROMFILE=0x00000010
                    hicon = user32.LoadImageW(
                        None, self._ico_path, 1,  # IMAGE_ICON
                        icon_w, icon_h, 0x00000010 | 0x00000040)
                    self.app.logger.log(
                        f"[托盘] LoadImageW(自定义) size={icon_w}x{icon_h} hIcon={hicon}")
                except Exception as e:
                    self.app.logger.log(f"[托盘] LoadImageW 异常: {e}", warning=True)

            if not hicon:
                # 回退：使用系统内置的 IDI_INFORMATION 感叹号图标（一定可见）
                hicon = user32.LoadIconW(0, 32516)  # IDI_INFORMATION
                self.app.logger.log(f"[托盘] 回退到系统图标 IDI_INFORMATION hIcon={hicon}")

            nid.hIcon = hicon
            self._hicon = hicon

            tip = "IP Monitor - 双击显示悬浮窗"
            nid.szTip = tip[:127]

            ok = shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))
            self.nid = nid
            self.registered = bool(ok)
            if self.registered:
                try:
                    nid.uTimeoutOrVersion = 4  # NOTIFYICON_VERSION_4
                    shell32.Shell_NotifyIconW(NIM_SETVERSION, ctypes.byref(nid))
                    nid.uFlags = NIF_ICON | NIF_TIP
                    nid.hIcon = hicon
                    nid.szTip = tip[:127]
                    shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))
                except Exception:
                    pass
            self.app.logger.log(
                f"[托盘] Shell_NotifyIconW(NIM_ADD) 返回={ok} "
                f"hIcon={nid.hIcon} hwnd={self.hwnd} tip='{tip}'")
        except Exception as e:
            self.app.logger.log(f"[托盘] 注册失败: {e}", error=True)
            import traceback
            self.app.logger.log(traceback.format_exc(), error=True)

    def _ensure_menu_helper(self):
        """确保存在非分层辅助窗口（TrackPopupMenu 的 owner）"""
        if self._menu_helper_hwnd and user32.IsWindow(self._menu_helper_hwnd):
            return self._menu_helper_hwnd

        # 辅助窗口 WndProc：只处理 WM_NCCREATE 返回 TRUE，其余走 DefWindowProcW
        @ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_uint,
                            ctypes.c_long, ctypes.c_long)
        def _helper_wndproc(hwnd, msg, wparam, lparam):
            if msg == 0x0081:  # WM_NCCREATE
                return 1
            return DefWindowProcW(hwnd, msg, wparam, lparam)
        self._menu_helper_wndproc = _helper_wndproc

        wc = WNDCLASSW()
        wc.lpfnWndProc = ctypes.cast(self._menu_helper_wndproc, ctypes.c_void_p)
        wc.lpszClassName = self._MENU_HELPER_CLASS
        wc.hInstance = GetModuleHandleW(None)

        atom = RegisterClassW(ctypes.byref(wc))
        self.app.logger.log(f"[托盘] 辅助窗口 RegisterClassW atom={atom}")

        # 创建非分层工具窗口作为菜单 owner。某些 DPI/DWM 组合下，
        # 完全不可见的 owner 会导致 TrackPopupMenu 文字绘制为空白。
        helper = CreateWindowExW(
            WS_EX_TOOLWINDOW | WS_EX_TOPMOST,
            self._MENU_HELPER_CLASS, "", 0x80000000,  # WS_POPUP
            -32000, -32000, 2, 2,
            None, None, GetModuleHandleW(None), None)
        self._menu_helper_hwnd = helper
        self.app.logger.log(
            f"[托盘] 辅助窗口创建 hwnd={helper} valid={bool(helper)}")
        return helper

    def _wndproc(self, hwnd, msg, wparam, lparam):
        """托盘消息处理（在主悬浮窗的 WndProc 中被调用）"""
        try:
            if msg == APP_TRAY_MSG:
                event = lparam & 0xFFFF
                self.app.logger.log(f"[托盘] 收到事件: 0x{event:X}")
                if event == WM_RBUTTONUP:
                    self._show_context_menu()
                elif event == WM_LBUTTONDBLCLK:
                    self.app.widget.show_at_center()
                return True
        except Exception as e:
            self.app.logger.log(f"[托盘] 消息处理异常: {e}", error=True)
        return False

    def _show_context_menu(self):
        """显示托盘右键菜单"""
        try:
            pt = POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            self._show_tk_context_menu(pt.x, pt.y)
            return
        except Exception as e:
            self.app.logger.log(f"[托盘] Tk 菜单失败，回退原生菜单: {e}", warning=True)

        """显示托盘右键菜单（原生回退）"""
        try:
            if self.hmenu:
                DestroyMenu(self.hmenu)
            self.hmenu = CreatePopupMenu()

            items = [
                (MF_ENABLED, ID_TRAY_SHOW, "显示悬浮窗"),
                (MF_ENABLED, ID_TRAY_CENTER, "窗口移到屏幕中央"),
                (MF_ENABLED, ID_TRAY_REFRESH, "立即刷新检测"),
                (MF_SEPARATOR, 0, ""),
                (MF_ENABLED, ID_TRAY_HIDE, "隐藏悬浮窗"),
                (MF_SEPARATOR, 0, ""),
                (MF_ENABLED, ID_TRAY_QUIT, "退出程序"),
            ]
            for flags, cmd_id, text in items:
                ok = AppendMenuW(self.hmenu, flags, cmd_id, text)
                if not ok:
                    err = ctypes.get_last_error()
                    self.app.logger.log(
                        f"[托盘] AppendMenuW 失败 id={cmd_id} text='{text}' err={err}",
                        error=True)

            # 获取鼠标位置
            pt = POINT()
            user32.GetCursorPos(ctypes.byref(pt))

            # 使用非分层辅助窗口作为 TrackPopupMenu 的 owner
            # 解决 WS_EX_LAYERED 窗口导致 DWM 合成时菜单文字渲染为空白的问题
            helper = self._ensure_menu_helper()
            SetWindowPos(helper, HWND_TOPMOST, pt.x, pt.y, 2, 2,
                         SWP_NOACTIVATE | SWP_SHOWWINDOW)
            user32.SetForegroundWindow(helper)
            SetActiveWindow(helper)
            DrawMenuBar(helper)

            # TPM_RETURNCMD | TPM_RIGHTBUTTON；让系统正常通知 owner，避免菜单文字空白。
            cmd = TrackPopupMenu(
                self.hmenu, TPM_RETURNCMD | TPM_RIGHTBUTTON,
                pt.x, pt.y, 0, helper, None)

            user32.PostMessageW(helper, WM_NULL, 0, 0)
            SetWindowPos(helper, HWND_NOTOPMOST, -32000, -32000, 2, 2,
                         SWP_NOACTIVATE | SWP_HIDEWINDOW)
            self._handle_menu_command(cmd)
        except Exception as e:
            self.app.logger.log(f"[托盘] 显示菜单失败: {e}", error=True)

    def _show_tk_context_menu(self, x: int, y: int):
        """使用 Tk 自绘托盘菜单，规避原生菜单在部分 DPI/DWM 环境下文字空白。"""
        if self._menu_thread and self._menu_thread.is_alive():
            try:
                if self._menu_window:
                    self._menu_window.after(0, self._menu_window.destroy)
            except Exception:
                pass

        def _run():
            try:
                root = tk.Tk()
                self._menu_window = root
                root.overrideredirect(True)
                root.attributes("-topmost", True)
                root.configure(bg="#f7f8fa")
                root.protocol("WM_DELETE_WINDOW", root.destroy)

                scale = 1.0
                try:
                    if self.app and self.app.widget:
                        scale = self.app.widget._scale()
                except Exception:
                    pass
                scale = max(1.0, min(scale, 1.45))
                try:
                    root.tk.call('tk', 'scaling', 1.0)
                except Exception:
                    pass
                width = int(244 * scale)
                row_h = int(34 * scale)
                pad = int(8 * scale)
                font = ("微软雅黑", max(11, int(11 * scale)))

                frame = tk.Frame(root, bg="#f7f8fa", highlightthickness=1,
                                 highlightbackground="#c9ced6", bd=0)
                frame.pack(fill=tk.BOTH, expand=True)

                items = [
                    ("显示悬浮窗", ID_TRAY_SHOW),
                    ("窗口移到屏幕中央", ID_TRAY_CENTER),
                    ("立即刷新检测", ID_TRAY_REFRESH),
                    (None, None),
                    ("隐藏悬浮窗", ID_TRAY_HIDE),
                    (None, None),
                    ("退出程序", ID_TRAY_QUIT),
                ]

                def invoke(cmd_id):
                    try:
                        root.destroy()
                    except Exception:
                        pass
                    self._handle_menu_command(cmd_id)

                for text, cmd_id in items:
                    if text is None:
                        sep = tk.Frame(frame, height=1, bg="#d8dde6")
                        sep.pack(fill=tk.X, padx=pad, pady=max(2, pad // 2))
                        continue
                    label = tk.Label(frame, text=text, anchor=tk.W, bg="#f7f8fa",
                                     fg="#20242a", font=font, padx=pad * 2,
                                     pady=0, height=1)
                    label.pack(fill=tk.X, ipady=max(5, row_h // 7))
                    label.bind("<Enter>", lambda e, w=label: w.configure(bg="#e6f0ff"))
                    label.bind("<Leave>", lambda e, w=label: w.configure(bg="#f7f8fa"))
                    label.bind("<Button-1>", lambda e, c=cmd_id: invoke(c))

                root.update_idletasks()
                menu_w = max(width, root.winfo_reqwidth())
                menu_h = root.winfo_reqheight()
                sw = root.winfo_screenwidth()
                sh = root.winfo_screenheight()
                px = min(max(0, x), max(0, sw - menu_w - 4))
                py = min(max(0, y - menu_h), max(0, sh - menu_h - 4))
                root.geometry(f"{menu_w}x{menu_h}+{px}+{py}")

                def close_if_focus_lost(event=None):
                    try:
                        root.after(120, lambda: root.destroy() if root.focus_displayof() is None else None)
                    except Exception:
                        pass

                root.bind("<Escape>", lambda e: root.destroy())
                root.bind("<FocusOut>", close_if_focus_lost)
                root.focus_force()
                self.app.logger.log(
                    f"[托盘] Tk 菜单显示: pos=({px},{py}) size={menu_w}x{menu_h} scale={scale}")
                root.mainloop()
            except Exception as e:
                self.app.logger.log(f"[托盘] Tk 菜单线程异常: {e}", error=True)
            finally:
                self._menu_window = None

        self._menu_thread = threading.Thread(target=_run, daemon=True)
        self._menu_thread.start()

    def _handle_menu_command(self, cmd):
        """处理菜单命令"""
        try:
            if cmd == ID_TRAY_SHOW:
                self.app.logger.log("[托盘] 用户点击 '显示悬浮窗'")
                self.app.widget.show()
            elif cmd == ID_TRAY_CENTER:
                self.app.logger.log("[托盘] 用户点击 '窗口移到屏幕中央'")
                self.app.widget.show_at_center()
            elif cmd == ID_TRAY_REFRESH:
                self.app.logger.log("[托盘] 用户点击 '立即刷新检测'")
                self.app.manual_check()
            elif cmd == ID_TRAY_HIDE:
                self.app.logger.log("[托盘] 用户点击 '隐藏悬浮窗'")
                self.app.widget.hide()
            elif cmd == ID_TRAY_QUIT:
                self.app.logger.log("[托盘] 用户点击 '退出程序'")
                self.app._do_quit()
        except Exception as e:
            self.app.logger.log(f"[托盘] 处理菜单命令异常: {e}", error=True)

    def show_balloon(self, title: str, message: str):
        """显示气泡通知"""
        try:
            if not self.registered:
                return
            nid = self.nid
            nid.uFlags = nid.uFlags | NIF_INFO
            nid.dwInfoFlags = NIIF_INFO
            nid.szInfoTitle = title[:63]
            nid.szInfo = message[:255]
            shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))
            # 恢复标志
            nid.uFlags = nid.uFlags & ~NIF_INFO
        except Exception as e:
            self.app.logger.log(f"[托盘] 气泡通知失败: {e}", warning=True)

    def _cleanup(self):
        """清理（已废弃 - 托盘改用主悬浮窗 hwnd）"""
        pass

    def destroy(self):
        """外部调用：销毁托盘"""
        try:
            if self.registered and self.nid:
                shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self.nid))
                self.registered = False
            if self.hmenu:
                DestroyMenu(self.hmenu)
                self.hmenu = None
            if self._menu_helper_hwnd:
                DestroyWindow(self._menu_helper_hwnd)
                self._menu_helper_hwnd = None
            if self._hicon:
                try:
                    DestroyIcon(self._hicon)
                except Exception:
                    pass
                self._hicon = None
        except Exception as e:
            self.app.logger.log(f"[托盘] 销毁失败: {e}", warning=True)


# ==================== 任务栏悬浮窗口 ====================

class DesktopWidget:
    """
    桌面右下角悬浮窗口
    始终置顶，可拖动，类似360悬浮助手/腾讯桌面整理
    """

    WIDGET_WIDTH = 300
    WIDGET_HEIGHT = 72
    BORDER_RADIUS = 0         # 透明背景无圆角
    EDGE_MARGIN = 20        # 距屏幕工作区边缘距离

    # 菜单ID
    MENU_SHOW_DETAIL = 1
    MENU_REFRESH = 2
    MENU_SEP1 = 3
    MENU_SETTINGS = 4
    MENU_HISTORY = 5
    MENU_SEP2 = 6
    MENU_MOVE = 7
    MENU_QUIT = 8

    # 定时器ID
    TIMER_AUTOHIDE = 1001   # 暂留扩展位（未启用）
    TIMER_RECLAMP = 1002    # 工作区变化时重夹位置

    def __init__(self, app: 'IPMonitorApp'):
        self.app = app
        self.hwnd = None
        self.wndproc_old = None
        self.menu = None
        self._visible = False
        self._dpi_scale = self._get_dpi_scale()
        self._w, self._h = self._calculate_widget_size()
        self._user_moved = False  # 用户是否主动移动过窗口（用于区分单击/拖动）
        self._menu_helper_hwnd = None  # 非分层辅助窗口，专用于 TrackPopupMenu owner
        self._menu_helper_wndproc = None

        # 关键：用闭包创建稳定的 WndProc 回调，避免 self.bound method 在 PyInstaller
        # 打包后地址失效的问题
        outer = self

        @ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_uint, ctypes.c_long, ctypes.c_long)
        def _wndproc_cb(hwnd, msg, wparam, lparam):
            return outer._wndproc(hwnd, msg, wparam, lparam)

        self._wndproc_callback = _wndproc_cb

    def _get_dpi_scale(self) -> float:
        """获取DPI缩放比例，优先启用 Per-Monitor DPI 感知。"""
        _enable_process_dpi_awareness()

        try:
            GetDpiForSystem = getattr(user32, 'GetDpiForSystem', None)
            if GetDpiForSystem:
                GetDpiForSystem.restype = ctypes.c_uint
                GetDpiForSystem.argtypes = []
                dpi = GetDpiForSystem()
                if dpi > 0:
                    return dpi / 96.0
        except Exception:
            pass

        return 1.0

    def _scale(self) -> float:
        return max(1.0, min(self._dpi_scale, 2.5))

    def _calculate_widget_size(self, work_area: Optional[Tuple[int, int, int, int]] = None) -> Tuple[int, int]:
        """根据当前 DPI 和工作区尺寸计算悬浮窗大小。"""
        wa_l, wa_t, wa_r, wa_b = work_area or self._get_work_area()
        wa_w = max(320, wa_r - wa_l)
        wa_h = max(240, wa_b - wa_t)
        scale = self._scale()
        w = int(round(self.WIDGET_WIDTH * scale))
        h = int(round(self.WIDGET_HEIGHT * scale))
        margin = max(8, int(self.EDGE_MARGIN * scale))
        min_w = min(max(220, int(220 * min(scale, 1.35))), max(160, wa_w - margin * 2))
        min_h = min(max(58, int(58 * min(scale, 1.35))), max(44, wa_h - margin * 2))
        w = max(min_w, min(w, max(160, wa_w - margin * 2)))
        h = max(min_h, min(h, max(44, wa_h - margin * 2)))
        return w, h

    def _refresh_screen_metrics(self):
        """分辨率、缩放或任务栏变化后刷新窗口尺寸。"""
        try:
            if self.hwnd:
                GetDpiForWindow = getattr(user32, 'GetDpiForWindow', None)
                if GetDpiForWindow:
                    GetDpiForWindow.restype = ctypes.c_uint
                    GetDpiForWindow.argtypes = [ctypes.c_void_p]
                    dpi = GetDpiForWindow(self.hwnd)
                    if dpi > 0:
                        self._dpi_scale = dpi / 96.0
                    else:
                        self._dpi_scale = self._get_dpi_scale()
                else:
                    self._dpi_scale = self._get_dpi_scale()
            else:
                self._dpi_scale = self._get_dpi_scale()
        except Exception:
            self._dpi_scale = self._get_dpi_scale()
        self._w, self._h = self._calculate_widget_size()

    def _get_close_rect(self) -> Tuple[int, int, int, int]:
        """返回关闭按钮命中区域，随 DPI 缩放。"""
        scale = self._scale()
        close_size = max(18, int(18 * scale))
        pad_x = max(8, int(8 * scale))
        pad_y = max(6, int(6 * scale))
        return self._w - close_size - pad_x, pad_y, self._w - pad_x, pad_y + close_size

    def _get_handle_rect(self) -> Tuple[int, int, int, int]:
        """返回拖动手柄的命中区域，随 DPI 缩放。"""
        scale = self._scale()
        left = max(4, int(4 * scale))
        top = max(4, int(4 * scale))
        width = max(30, int(30 * scale))
        height = max(24, int(24 * scale))
        return left, top, left + width, top + height

    def _screen_point_to_client(self, lparam) -> Tuple[int, int]:
        """把 WM_NCHITTEST 的屏幕坐标转换成客户区坐标。"""
        pt = POINT(
            ctypes.c_short(lparam & 0xFFFF).value,
            ctypes.c_short((lparam >> 16) & 0xFFFF).value
        )
        try:
            if self.hwnd and ScreenToClient(self.hwnd, ctypes.byref(pt)):
                return pt.x, pt.y
        except Exception:
            pass
        return pt.x, pt.y

    def _get_work_area(self) -> Tuple[int, int, int, int]:
        """获取当前窗口所在显示器的工作区（去掉任务栏）。"""
        try:
            hmonitor = None
            if self.hwnd:
                hmonitor = MonitorFromWindow(self.hwnd, MONITOR_DEFAULTTONEAREST)
            if not hmonitor:
                pt = POINT(0, 0)
                GetCursorPos(ctypes.byref(pt))
                hmonitor = MonitorFromPoint(pt, MONITOR_DEFAULTTONEAREST)
            if hmonitor:
                info = MONITORINFO()
                info.cbSize = ctypes.sizeof(MONITORINFO)
                if GetMonitorInfoW(hmonitor, ctypes.byref(info)):
                    r = info.rcWork
                    return r.left, r.top, r.right, r.bottom
        except Exception:
            pass

        try:
            rect = RECT()
            ok = SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(rect), 0)
            if ok:
                return rect.left, rect.top, rect.right, rect.bottom
        except Exception:
            pass
        # 兜底：使用主屏尺寸
        w = GetSystemMetrics(SM_CXSCREEN)
        h = GetSystemMetrics(SM_CYSCREEN)
        return 0, 0, w, h

    def _enumerate_monitors(self) -> str:
        """枚举所有显示器，便于诊断多屏/缩放问题"""
        lines = []
        try:
            MonitorEnumProc = ctypes.WINFUNCTYPE(
                ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
                ctypes.POINTER(RECT), ctypes.c_void_p)
            monitors = []

            def cb(hMonitor, hdcMonitor, lprcMonitor, dwData):
                try:
                    rect = lprcMonitor.contents
                    lines.append(
                        f"  Monitor hMon={hMonitor}: "
                        f"({rect.left},{rect.top})-({rect.right},{rect.bottom}) "
                        f"size={rect.right-rect.left}x{rect.bottom-rect.top}")
                except Exception as ex:
                    lines.append(f"  Monitor cb error: {ex}")
                monitors.append(hMonitor)
                return 1

            user32.EnumDisplayMonitors(None, None, MonitorEnumProc(cb), 0)
            return "\n".join(lines) if lines else "  (EnumDisplayMonitors returned 0)"
        except Exception as e:
            return f"  (enumerate failed: {e})"

    def _get_widget_pos(self) -> Tuple[int, int, int, int]:
        """计算窗口默认位置：屏幕工作区中央偏上（首次启动）
        修改原因：右下角位置用户不容易看到，先放到屏幕中央显眼位置
        """
        wa_l, wa_t, wa_r, wa_b = self._get_work_area()
        wa_w = wa_r - wa_l
        wa_h = wa_b - wa_t
        self._w, self._h = self._calculate_widget_size()

        # 位置：右上角，距离右边/顶部各 60px（更显眼）
        margin = max(8, min(60, self.EDGE_MARGIN + int(20 * min(self._dpi_scale, 2.0))))
        x = wa_l + wa_w - self._w - margin
        y = wa_t + margin

        # 诊断日志：屏幕工作区 + 显示器列表
        try:
            self.app.logger.log(
                f"[窗口] 屏幕工作区: left={wa_l}, top={wa_t}, right={wa_r}, bottom={wa_b}, "
                f"尺寸={wa_w}x{wa_h}")
            self.app.logger.log(
                f"[窗口] 屏幕原始尺寸: {GetSystemMetrics(SM_CXSCREEN)}x{GetSystemMetrics(SM_CYSCREEN)}")
            self.app.logger.log(
                f"[窗口] DPI 缩放: {self._dpi_scale}")
            self.app.logger.log(f"[窗口] 显示器列表:\n{self._enumerate_monitors()}")
        except Exception:
            pass

        return x, y, self._w, self._h

    def _clamp_into_work_area(self) -> Tuple[int, int, int, int]:
        """将窗口位置夹回工作区（保证不被任务栏遮挡）"""
        if not self.hwnd:
            return self._get_widget_pos()

        self._refresh_screen_metrics()
        rect = RECT()
        GetWindowRect(self.hwnd, ctypes.byref(rect))
        wa_l, wa_t, wa_r, wa_b = self._get_work_area()
        wa_w = wa_r - wa_l
        wa_h = wa_b - wa_t

        x = rect.left
        y = rect.top
        # 边界夹紧
        margin = max(4, min(12, self.EDGE_MARGIN // 2))
        if x + self._w > wa_r - margin:
            x = wa_r - self._w - margin
        if x < wa_l + margin:
            x = wa_l + margin
        if y + self._h > wa_b - margin:
            y = wa_b - self._h - margin
        if y < wa_t + margin:
            y = wa_t + margin

        return x, y, self._w, self._h

    def _create_menu(self):
        """创建右键菜单"""
        if self.menu:
            DestroyMenu(self.menu)
        self.menu = CreatePopupMenu()

        AppendMenuW(self.menu, MF_ENABLED, self.MENU_SHOW_DETAIL, "显示详情")
        AppendMenuW(self.menu, MF_ENABLED, self.MENU_REFRESH, "刷新检测")
        AppendMenuW(self.menu, MF_SEPARATOR, self.MENU_SEP1, "")
        AppendMenuW(self.menu, MF_ENABLED, self.MENU_SETTINGS, "设置")
        AppendMenuW(self.menu, MF_ENABLED, self.MENU_HISTORY, "历史记录")
        AppendMenuW(self.menu, MF_SEPARATOR, self.MENU_SEP2, "")
        AppendMenuW(self.menu, MF_ENABLED, self.MENU_MOVE, "重置到默认位置")
        AppendMenuW(self.menu, MF_ENABLED, self.MENU_QUIT, "退出")

    def _ensure_menu_helper(self):
        """确保存在非分层辅助窗口（TrackPopupMenu 的 owner）"""
        if self._menu_helper_hwnd and user32.IsWindow(self._menu_helper_hwnd):
            return self._menu_helper_hwnd

        @ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_uint,
                            ctypes.c_long, ctypes.c_long)
        def _helper_wndproc(hwnd, msg, wparam, lparam):
            if msg == 0x0081:  # WM_NCCREATE
                return 1
            return DefWindowProcW(hwnd, msg, wparam, lparam)
        self._menu_helper_wndproc = _helper_wndproc

        wc = WNDCLASSW()
        wc.lpfnWndProc = ctypes.cast(self._menu_helper_wndproc, ctypes.c_void_p)
        wc.lpszClassName = "IPMonitor_WidgetMenuHelper"
        wc.hInstance = GetModuleHandleW(None)
        RegisterClassW(ctypes.byref(wc))

        helper = CreateWindowExW(
            0, wc.lpszClassName, "", 0x80000000,  # WS_POPUP
            0, 0, 1, 1,
            None, None, GetModuleHandleW(None), None)
        self._menu_helper_hwnd = helper
        return helper

    def _show_menu(self):
        """显示右键菜单"""
        self._create_menu()
        cursor = POINT()
        GetCursorPos(ctypes.byref(cursor))

        # 使用非分层辅助窗口作为 TrackPopupMenu 的 owner
        helper = self._ensure_menu_helper()
        user32.SetForegroundWindow(helper)

        cmd = TrackPopupMenu(self.menu,
                             TPM_LEFTALIGN | TPM_RETURNCMD | TPM_NONOTIFY,
                             cursor.x, cursor.y, 0, helper, None)
        user32.PostMessageW(helper, WM_NULL, 0, 0)

        if cmd == self.MENU_SHOW_DETAIL:
            self.app.detail_window.show()
        elif cmd == self.MENU_REFRESH:
            self.app.manual_check()
        elif cmd == self.MENU_SETTINGS:
            self.app.settings_window.show()
        elif cmd == self.MENU_HISTORY:
            self.app.detail_window.show()
        elif cmd == self.MENU_MOVE:
            self._user_moved = False
            self._reset_position()
        elif cmd == self.MENU_QUIT:
            self.app._do_quit()

    def _reset_position(self):
        """重置到默认位置（屏幕右下角）"""
        if not self.hwnd:
            return
        x, y, w, h = self._get_widget_pos()
        SetWindowPos(self.hwnd, HWND_TOPMOST, x, y, w, h,
                     SWP_NOACTIVATE | SWP_SHOWWINDOW)
        InvalidateRect(self.hwnd, None, True)

    def _wndproc(self, hwnd, msg, wparam, lparam):
        """窗口消息处理"""
        try:
            return self._wndproc_impl(hwnd, msg, wparam, lparam)
        except Exception as e:
            import traceback
            try:
                self.app.logger.log(
                    f"[WndProc] 异常 msg={hex(msg)} err={e}", error=True)
                self.app.logger.log(traceback.format_exc(), error=True)
            except Exception:
                pass
            return 0

    def _wndproc_impl(self, hwnd, msg, wparam, lparam):
        """窗口消息处理（实际实现）"""
        # 调试日志：记录每条收到的消息（仅前 15 条）
        try:
            self._msg_count = getattr(self, '_msg_count', 0) + 1
            if self._msg_count <= 15:
                self.app.logger.log(
                    f"[WndProc] msg#{self._msg_count} 消息码=0x{msg:04X} wparam=0x{wparam:04X}")
        except Exception:
            pass

        # WM_NCCREATE 必须 return TRUE，否则 CreateWindowExW 失败
        if msg == 0x0081:  # WM_NCCREATE
            return 1
        if msg == 0x0082:  # WM_NCCALCSIZE
            return 0
        if msg == 0x0001:  # WM_CREATE
            return 0
        # WM_WINDOWPOSCHANGING 显式吞掉，避免 DefWindowProcW 在 ctypes 下 access violation
        if msg == WM_WINDOWPOSCHANGING:
            return 0
        if msg == 0x0047:  # WM_WINDOWPOSCHANGED
            return 0
        if msg == 0x0007:  # WM_SETFOCUS
            return 0
        if msg == 0x0008:  # WM_KILLFOCUS
            return 0
        if msg == 0x001C:  # WM_ACTIVATEAPP
            return 0
        if msg == 0x0086:  # WM_NCACTIVATE
            return 0
        if msg == 0x0281:  # WM_IME_SETCONTEXT
            return 0
        if msg == 0x0282:  # WM_IME_NOTIFY
            return 0
        if msg == 0x0085:  # WM_NCPAINT
            return 0
        if msg == 0x0018:  # WM_SHOWWINDOW
            return 0

        if msg == WM_RBUTTONUP or msg == WM_CONTEXTMENU:
            self._show_menu()
            return 0

        elif msg == WM_LBUTTONDOWN:
            # 仅处理关闭按钮点击（关闭按钮区域由 WM_NCHITTEST 返回 HTCLIENT）
            mx = ctypes.c_short(lparam & 0xFFFF).value
            my = ctypes.c_short((lparam >> 16) & 0xFFFF).value
            close_l, close_t, close_r, close_b = self._get_close_rect()
            if close_l <= mx <= close_r and close_t <= my <= close_b:
                self.app.logger.log("[窗口] 用户点击关闭按钮，隐藏悬浮窗（托盘继续运行）")
                self.hide()
                return 0
            # 其他区域的 LBUTTONDOWN 由 HTCAPTION 原生拖动接管，此处不拦截

        elif msg == WM_NCLBUTTONUP:
            # 非客户区鼠标释放：如果是单击（非拖动），触发刷新检测
            # HTCAPTION 模式下，短按释放 = 单击
            if not self._user_moved:
                try:
                    self.app.manual_check()
                except Exception:
                    pass
            self._user_moved = False  # 重置，为下次点击准备
            return 0

        elif msg == WM_NCLBUTTONDBLCLK:
            # 非客户区双击 → 显示详情窗口
            self.app.detail_window.show()
            return 0

        elif msg == WM_NCHITTEST:
            # 关闭按钮区域返回 HTCLIENT（让 LBUTTONDOWN 处理关闭）
            # 手柄区域返回 HTCAPTION → Windows 原生拖动，零抖动
            mx, my = self._screen_point_to_client(lparam)
            close_l, close_t, close_r, close_b = self._get_close_rect()
            if close_l <= mx <= close_r and close_t <= my <= close_b:
                return 1  # HTCLIENT
            handle_l, handle_t, handle_r, handle_b = self._get_handle_rect()
            if handle_l <= mx <= handle_r and handle_t <= my <= handle_b:
                return 2  # HTCAPTION
            return 1  # HTCLIENT

        elif msg == WM_MOVING:
            # 用户正在拖动窗口（HTCAPTION 原生触发）
            self._user_moved = True
            return 0

        elif msg == WM_ERASEBKGND:
            return 1  # 阻止背景擦除，避免闪烁

        elif msg == WM_DISPLAYCHANGE:
            def delayed_reclamp():
                time.sleep(0.3)
                self._refresh_screen_metrics()
                self._reclamp_position()
            threading.Thread(target=delayed_reclamp, daemon=True).start()
            return 0

        elif msg == WM_DPICHANGED:
            dpi_x = wparam & 0xFFFF
            if dpi_x > 0:
                self._dpi_scale = dpi_x / 96.0
            self._w, self._h = self._calculate_widget_size()
            try:
                suggested = ctypes.cast(lparam, ctypes.POINTER(RECT)).contents
                x, y = suggested.left, suggested.top
            except Exception:
                x, y, _, _ = self._clamp_into_work_area()
            wa_l, wa_t, wa_r, wa_b = self._get_work_area()
            margin = max(4, int(6 * self._scale()))
            x = max(wa_l + margin, min(x, wa_r - self._w - margin))
            y = max(wa_t + margin, min(y, wa_b - self._h - margin))
            flags = SWP_NOACTIVATE
            if self._visible:
                flags |= SWP_SHOWWINDOW
            SetWindowPos(self.hwnd, HWND_TOPMOST, x, y, self._w, self._h,
                         flags)
            if self._visible:
                InvalidateRect(self.hwnd, None, True)
            return 0

        elif msg == WM_SETTINGCHANGE:
            self._refresh_screen_metrics()
            self._reclamp_position()
            if self._visible:
                InvalidateRect(self.hwnd, None, True)
            return 0

        elif msg == WM_TIMER:
            if wparam == self.TIMER_RECLAMP:
                self._reclamp_position()
            return 0

        elif msg == WM_COMMAND:
            menu_id = wparam & 0xFFFF
            if menu_id == self.MENU_SHOW_DETAIL:
                self.app.detail_window.show()
            elif menu_id == self.MENU_REFRESH:
                self.app.manual_check()
            elif menu_id == self.MENU_SETTINGS:
                self.app.settings_window.show()
            elif menu_id == self.MENU_HISTORY:
                self.app.detail_window.show()
            elif menu_id == self.MENU_MOVE:
                self._user_moved = False
                self._reset_position()
            elif menu_id == self.MENU_QUIT:
                self.app._do_quit()
            return 0

        elif msg == APP_TRAY_MSG:
            # 托盘消息（共用主悬浮窗 hwnd）
            try:
                if self.app and self.app.tray:
                    if self.app.tray._wndproc(self.hwnd, msg, wparam, lparam):
                        return 0
            except Exception:
                pass
            return 0

        elif msg == 0x000F:  # WM_PAINT
            try:
                ps = PAINTSTRUCT()
                begin = ctypes.windll.user32.BeginPaint
                begin.argtypes = [ctypes.c_void_p, ctypes.POINTER(PAINTSTRUCT)]
                begin.restype = ctypes.c_void_p
                end = ctypes.windll.user32.EndPaint
                end.argtypes = [ctypes.c_void_p, ctypes.POINTER(PAINTSTRUCT)]
                begin(self.hwnd, ctypes.byref(ps))
                is_normal = self.app.last_result.is_same if self.app.last_result else True
                self._draw_frame(ps.hdc, is_normal)
                end(self.hwnd, ctypes.byref(ps))
            except Exception as e:
                import traceback
                try:
                    self.app.logger.log(f"[WM_PAINT] 异常: {e}", error=True)
                    self.app.logger.log(traceback.format_exc(), error=True)
                except Exception:
                    pass
            return 0

        # 兜底：使用 DefWindowProcW 代替 CallWindowProcW（self.wndproc_old 未设置）
        return DefWindowProcW(hwnd, msg, wparam, lparam)

    def _reclamp_position(self, show: bool = False):
        """夹紧窗口位置到工作区；默认不改变隐藏/显示状态。"""
        if not self.hwnd:
            return
        x, y, w, h = self._clamp_into_work_area()
        flags = SWP_NOACTIVATE
        if show:
            flags |= SWP_SHOWWINDOW
        SetWindowPos(self.hwnd, HWND_TOPMOST, x, y, w, h,
                     flags)

    def _draw_frame(self, hdc, is_normal: bool):
        """绘制窗口内容"""
        try:
            self._draw_frame_impl(hdc, is_normal)
        except Exception as e:
            import traceback
            try:
                self.app.logger.log(f"[绘制] 异常: {e}", error=True)
                self.app.logger.log(traceback.format_exc(), error=True)
            except Exception:
                pass

    def _draw_frame_impl(self, hdc, is_normal: bool):
        # 调试日志：每次 WM_PAINT 都打印一次
        try:
            self._paint_count = getattr(self, '_paint_count', 0) + 1
            if self._paint_count <= 3:
                self.app.logger.log(
                    f"[绘制] WM_PAINT 第 {self._paint_count} 次 hdc={hdc} "
                    f"is_normal={is_normal} size={self._w}x{self._h}")
        except Exception:
            pass

        # 工具函数：把 (r, g, b) 转为 COLORREF
        def RGB(r, g, b):
            return r | (g << 8) | (b << 16)

        w, h = self._w, self._h

        # 主题色
        color_text = (40, 40, 50)
        color_normal = (46, 204, 113)
        color_alert = (231, 76, 60)
        color_label_cn = (40, 120, 200)
        color_label_en = (160, 80, 180)
        color_dim = (130, 130, 140)
        accent = color_normal if is_normal else color_alert
        # 按钮的细线颜色
        color_btn = (110, 110, 120)
        color_btn_close = (200, 70, 70)
        color_hit_bg = (248, 248, 250)

        # 1) 填充洋红色背景（色键色，SetLayeredWindowAttributes 会把它变透明）
        TRANSPARENT_KEY = RGB(255, 0, 255)  # 0xFF00FF
        brush_bg = gdi32.CreateSolidBrush(TRANSPARENT_KEY)
        rect_bg = RECT(0, 0, w, h)
        user32.FillRect(hdc, ctypes.byref(rect_bg), brush_bg)
        gdi32.DeleteObject(brush_bg)

        # 2) 细边框（深灰色 1px，方便看出窗口边界，背景仍透明）
        pen_border = gdi32.CreatePen(0, 1, RGB(120, 120, 130))
        old_pen_b = gdi32.SelectObject(hdc, pen_border)
        brush_null = gdi32.GetStockObject(5)  # NULL_BRUSH
        old_brush = gdi32.SelectObject(hdc, brush_null)
        gdi32.Rectangle(hdc, 0, 0, w - 1, h - 1)
        gdi32.SelectObject(hdc, old_brush)
        gdi32.SelectObject(hdc, old_pen_b)
        gdi32.DeleteObject(pen_border)

        scale = self._scale()
        close_size = max(12, int(12 * scale))
        close_x, close_y, close_r, close_b = self._get_close_rect()
        top_pad = max(4, int(4 * scale))
        line_gap = max(20, int(20 * scale))
        line1_top = max(close_b + max(4, int(4 * scale)), int(24 * scale))
        if line1_top + line_gap * 2 > h - max(2, int(2 * scale)):
            line_gap = max(16, (h - line1_top - max(2, int(2 * scale))) // 2)
        label_x = max(36, int(36 * scale))
        ip_x = label_x + max(28, int(28 * scale))
        content_right = max(ip_x + 80, close_x - max(6, int(6 * scale)))
        available_w = max(80, content_right - ip_x)
        country_min_w = max(42, int(44 * scale))
        ip_w = min(max(96, int(120 * scale)), available_w)
        if available_w - ip_w < country_min_w:
            ip_w = max(70, available_w - country_min_w)
        country_x = min(content_right, ip_x + ip_w)

        # 3) 左上角移动手柄：画出非透明命中底色，否则色键窗口只能点中线条。
        handle_l, handle_t, handle_r, handle_b = self._get_handle_rect()
        handle_w = handle_r - handle_l
        pen_bg = gdi32.CreatePen(0, 1, RGB(*color_btn))
        brush_bg = gdi32.CreateSolidBrush(RGB(*color_hit_bg))
        old_pen_bg = gdi32.SelectObject(hdc, pen_bg)
        old_brush_handle = gdi32.SelectObject(hdc, brush_bg)
        gdi32.Rectangle(hdc, handle_l, handle_t, handle_r, handle_b)
        if old_brush_handle:
            gdi32.SelectObject(hdc, old_brush_handle)
        if old_pen_bg:
            gdi32.SelectObject(hdc, old_pen_bg)
        gdi32.DeleteObject(pen_bg)
        gdi32.DeleteObject(brush_bg)

        pen = gdi32.CreatePen(0, 1, RGB(*color_btn))
        old_pen = gdi32.SelectObject(hdc, pen)
        for i in range(3):
            line_y = handle_t + max(6, int(6 * scale)) + i * max(5, int(5 * scale))
            gdi32.MoveToEx(hdc, handle_l + max(6, int(6 * scale)), line_y, None)
            gdi32.LineTo(hdc, handle_l + handle_w - max(6, int(6 * scale)), line_y)
        gdi32.SelectObject(hdc, old_pen)
        gdi32.DeleteObject(pen)

        # 4) 右上角关闭按钮 "X"（两条对角线，红色，醒目）
        close_size = min(close_r - close_x, close_b - close_y)
        pen_bg = gdi32.CreatePen(0, 1, RGB(*color_btn_close))
        brush_bg = gdi32.CreateSolidBrush(RGB(*color_hit_bg))
        old_pen_bg = gdi32.SelectObject(hdc, pen_bg)
        old_brush_close = gdi32.SelectObject(hdc, brush_bg)
        gdi32.Rectangle(hdc, close_x, close_y, close_r, close_b)
        if old_brush_close:
            gdi32.SelectObject(hdc, old_brush_close)
        if old_pen_bg:
            gdi32.SelectObject(hdc, old_pen_bg)
        gdi32.DeleteObject(pen_bg)
        gdi32.DeleteObject(brush_bg)

        pen = gdi32.CreatePen(0, 2, RGB(*color_btn_close))
        old_pen = gdi32.SelectObject(hdc, pen)
        inset = max(4, int(4 * scale))
        gdi32.MoveToEx(hdc, close_x + inset, close_y + inset, None)
        gdi32.LineTo(hdc, close_x + close_size - inset, close_y + close_size - inset)
        gdi32.MoveToEx(hdc, close_x + close_size - inset, close_y + inset, None)
        gdi32.LineTo(hdc, close_x + inset, close_y + close_size - inset)
        gdi32.SelectObject(hdc, old_pen)
        gdi32.DeleteObject(pen)

        # 4) 状态指示小圆点（左侧小色块，反映代理状态：绿=正常/红=异常）
        pen = gdi32.CreatePen(0, 1, RGB(*accent))
        brush = gdi32.CreateSolidBrush(RGB(*accent))
        old_pen = gdi32.SelectObject(hdc, pen)
        old_brush = gdi32.SelectObject(hdc, brush)
        dot_x = max(24, int(24 * scale))
        dot_y = line1_top + max(6, int(6 * scale))
        dot_size = max(6, int(6 * scale))
        gdi32.Ellipse(hdc, dot_x, dot_y, dot_x + dot_size, dot_y + dot_size)
        gdi32.SelectObject(hdc, old_brush)
        gdi32.SelectObject(hdc, old_pen)
        gdi32.DeleteObject(brush)
        gdi32.DeleteObject(pen)

        # 5) 绘制 CN/EN 标签 + IP + 国家名
        gdi32.SetBkMode(hdc, 1)  # TRANSPARENT

        line1, line2 = self._get_display_lines()  # ((ip1, country1), (ip2, country2))
        cn_ip, cn_country = line1
        en_ip, en_country = line2

        # CN 行
        self._draw_colored_text(hdc, "CN", label_x, line1_top, ip_x - 4, line1_top + line_gap,
                                color_label_cn, bold=True)
        # IP 部分
        self._draw_colored_text(hdc, cn_ip, ip_x, line1_top, country_x, line1_top + line_gap,
                                color_text, bold=False)
        # 国家名（更浅的颜色，紧随 IP 之后）
        if cn_country and country_x < content_right:
            self._draw_colored_text(hdc, " " + cn_country, country_x, line1_top, content_right, line1_top + line_gap,
                                    color_dim, bold=False)

        # EN 行
        line2_top = line1_top + line_gap
        self._draw_colored_text(hdc, "EN", label_x, line2_top, ip_x - 4, min(h - 2, line2_top + line_gap),
                                color_label_en, bold=True)
        self._draw_colored_text(hdc, en_ip, ip_x, line2_top, country_x, min(h - 2, line2_top + line_gap),
                                color_dim, bold=False)
        if en_country and country_x < content_right:
            self._draw_colored_text(hdc, " " + en_country, country_x, line2_top, content_right, min(h - 2, line2_top + line_gap),
                                    color_dim, bold=False)

    def _draw_colored_text(self, hdc, text, left, top, right, bottom, color_rgb,
                           bold=False, font_size_delta: int = 0):
        """绘制带颜色的文本（始终使用系统默认 GUI 字体，规避 PyInstaller 打包后
        CreateFontIndirectW 失效问题）"""
        # COLORREF = R | G<<8 | B<<16
        def RGB(r, g, b):
            return r | (g << 8) | (b << 16)

        # 始终使用系统默认 GUI 字体 - 该字体在所有 Windows 机器上一定可用
        # DEFAULT_GUI_FONT = 17
        hfont = gdi32.GetStockObject(17)
        if hfont:
            old_font = gdi32.SelectObject(hdc, hfont)
        else:
            old_font = None

        gdi32.SetTextColor(hdc, RGB(color_rgb[0], color_rgb[1], color_rgb[2]))
        gdi32.SetBkMode(hdc, 1)  # TRANSPARENT
        r = RECT()
        r.left = left
        r.top = top
        r.right = right
        r.bottom = bottom
        # DT_LEFT | DT_VCENTER | DT_SINGLELINE | DT_NOPREFIX | DT_END_ELLIPSIS
        user32.DrawTextW(hdc, text, -1, ctypes.byref(r), 0x8824)

        if old_font is not None:
            gdi32.SelectObject(hdc, old_font)

    def _get_text_width(self, hdc, text, font_size):
        """估算文本宽度（中英文混合）"""
        return font_size * 0.6 * len(text) * self._dpi_scale

    def _get_display_lines(self) -> Tuple[Tuple[str, str], Tuple[str, str]]:
        """获取要显示的两行文本，返回 ((ip, country), (ip, country))"""
        if not self.app.last_result:
            return ("--", ""), ("--", "")

        d = self.app.last_result.domestic
        f_info = self.app.last_result.foreign

        if d.success and d.ip != "检测失败":
            country1 = self._extract_country(d.location)
            line1 = (d.ip, country1)
        else:
            line1 = ("失败", "")

        if f_info.success and f_info.ip != "检测失败":
            country2 = self._extract_country(f_info.location)
            line2 = (f_info.ip, country2)
        else:
            line2 = ("失败", "")

        return line1, line2

    # ISO 3166-1 alpha-2 / alpha-3 国家代码 → 中文国名映射
    COUNTRY_CODE_TO_CN = {
        "CN": "中国", "HK": "中国香港", "MO": "中国澳门", "TW": "中国台湾",
        "US": "美国", "CA": "加拿大", "MX": "墨西哥",
        "GB": "英国", "UK": "英国", "IE": "爱尔兰", "FR": "法国", "DE": "德国",
        "IT": "意大利", "ES": "西班牙", "PT": "葡萄牙", "NL": "荷兰",
        "BE": "比利时", "LU": "卢森堡", "CH": "瑞士", "AT": "奥地利",
        "SE": "瑞典", "NO": "挪威", "FI": "芬兰", "DK": "丹麦", "IS": "冰岛",
        "PL": "波兰", "CZ": "捷克", "SK": "斯洛伐克", "HU": "匈牙利",
        "RO": "罗马尼亚", "BG": "保加利亚", "GR": "希腊", "RU": "俄罗斯",
        "UA": "乌克兰", "TR": "土耳其",
        "JP": "日本", "KR": "韩国", "KP": "朝鲜", "MN": "蒙古",
        "IN": "印度", "PK": "巴基斯坦", "BD": "孟加拉国", "LK": "斯里兰卡",
        "NP": "尼泊尔", "BT": "不丹", "MV": "马尔代夫", "AF": "阿富汗",
        "TH": "泰国", "VN": "越南", "MY": "马来西亚", "SG": "新加坡",
        "ID": "印度尼西亚", "PH": "菲律宾", "KH": "柬埔寨", "LA": "老挝",
        "MM": "缅甸", "BN": "文莱", "TL": "东帝汶",
        "AU": "澳大利亚", "NZ": "新西兰", "PG": "巴布亚新几内亚", "FJ": "斐济",
        "AE": "阿联酋", "SA": "沙特阿拉伯", "IL": "以色列", "IR": "伊朗",
        "IQ": "伊拉克", "SY": "叙利亚", "JO": "约旦", "LB": "黎巴嫩",
        "KW": "科威特", "QA": "卡塔尔", "BH": "巴林", "OM": "阿曼", "YE": "也门",
        "EG": "埃及", "ZA": "南非", "NG": "尼日利亚", "KE": "肯尼亚",
        "ET": "埃塞俄比亚", "MA": "摩洛哥", "DZ": "阿尔及利亚", "TN": "突尼斯",
        "GH": "加纳", "TZ": "坦桑尼亚", "UG": "乌干达", "ZW": "津巴布韦",
        "AO": "安哥拉", "SD": "苏丹",
        "BR": "巴西", "AR": "阿根廷", "CL": "智利", "CO": "哥伦比亚",
        "PE": "秘鲁", "VE": "委内瑞拉", "UY": "乌拉圭", "PY": "巴拉圭",
        "BO": "玻利维亚", "EC": "厄瓜多尔",
    }

    @staticmethod
    def _extract_country(location: str) -> str:
        """从 location 字段提取国家名（中文优先），返回中文国名"""
        if not location or location in ("未知", "Unknown", "已检测"):
            return ""
        first = location.strip().split(None, 1)[0]
        if not first:
            return ""
        # 已是中文（首字符不是 ASCII 字母）→ 直接返回
        if not first.isascii():
            return first
        # 首段为英文国家码 → 查表转中文
        mapped = DesktopWidget.COUNTRY_CODE_TO_CN.get(first.upper())
        if mapped:
            return mapped
        return first


    def update(self, is_normal: bool):
        """更新窗口显示"""
        if not self.hwnd:
            return
        user32.InvalidateRect(self.hwnd, None, True)

    def show(self):
        """显示窗口"""
        try:
            self._show_impl()
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            try:
                self.app.logger.log(f"[窗口] show() 异常: {e}", error=True)
                self.app.logger.log(f"[窗口] Traceback:\n{tb}", error=True)
            except Exception:
                pass
            # 不让异常杀死进程，保留主消息循环运行
            self._visible = False

    def _show_impl(self):
        if self.hwnd:
            # 已存在，重新夹紧位置并显示
            self._reclamp_position(show=True)
            ShowWindow(self.hwnd, 5)  # SW_SHOW
            try:
                SetTimer(self.hwnd, self.TIMER_RECLAMP, 3000, None)
            except Exception:
                pass
            self._visible = True
            return

        # 计算窗口位置（屏幕工作区右下角）
        x, y, w, h = self._get_widget_pos()
        self.app.logger.log(f"[窗口] 计算位置: x={x}, y={y}, w={w}, h={h}")

        # 注册窗口类
        wc = WNDCLASSW()
        wc.style = CS_DBLCLKS | CS_HREDRAW | CS_VREDRAW
        wc.lpfnWndProc = ctypes.cast(self._wndproc_callback, ctypes.c_void_p)
        wc.cbClsExtra = 0
        wc.cbWndExtra = 0
        wc.hInstance = GetModuleHandleW(None)
        wc.hIcon = None
        wc.hCursor = user32.LoadCursorW(0, IDC_SIZEALL)  # 四向箭头，提示整个窗口可拖动
        wc.hbrBackground = None
        wc.lpszMenuName = None
        wc.lpszClassName = "IPMonitorDesktopWidget"

        # 调试日志：WndProc 指针
        try:
            wpc_ptr = ctypes.cast(self._wndproc_callback, ctypes.c_void_p).value
            self.app.logger.log(
                f"[窗口] WndProc 指针: 0x{wpc_ptr:X} 类型={type(self._wndproc_callback).__name__}")
        except Exception as e:
            self.app.logger.log(f"[窗口] 取 WndProc 指针失败: {e}", warning=True)

        # 防止重复注册
        try:
            atom = RegisterClassW(ctypes.byref(wc))
            self.app.logger.log(f"[窗口] RegisterClassW atom={atom}")
        except Exception as e:
            self.app.logger.log(f"[窗口] RegisterClassW 失败（可能已注册）: {e}")

        # 创建悬浮窗口（WS_EX_LAYERED 用于实现背景透明）
        self.app.logger.log(
            f"[窗口] CreateWindowExW 参数: class=IPMonitorDesktopWidget "
            f"style=0x{WS_POPUP | WS_VISIBLE:X} exstyle=0x{WS_EX_TOOLWINDOW | WS_EX_TOPMOST | WS_EX_LAYERED:X} "
            f"x={x}, y={y}, w={w}, h={h}")
        create_ret = CreateWindowExW(
            WS_EX_TOOLWINDOW | WS_EX_TOPMOST | WS_EX_LAYERED,
            "IPMonitorDesktopWidget",
            "IP Monitor",
            WS_POPUP | WS_VISIBLE,
            x, y, w, h,
            0, 0,
            GetModuleHandleW(None),
            None
        )
        self.app.logger.log(
            f"[窗口] CreateWindowExW 返回: type={type(create_ret).__name__} "
            f"value=0x{create_ret or 0:X} bool={bool(create_ret)}")
        self.hwnd = create_ret

        if not self.hwnd:
            err = ctypes.windll.kernel32.GetLastError()
            self.app.logger.log(f"[窗口] 创建失败！GetLastError={err}", error=True)
            return

        self.app.logger.log(f"[窗口] 创建成功 hwnd={self.hwnd}")

        # 设置分层窗口透明（背景用洋红色 0xFF00FF 作为色键，绘制时背景填洋红即变透明）
        try:
            color_key = 0xFF00FF  # RGB(255, 0, 255) 洋红
            la_ret = SetLayeredWindowAttributes(self.hwnd, color_key, 0, LWA_COLORKEY)
            err = ctypes.windll.kernel32.GetLastError()
            self.app.logger.log(
                f"[窗口] SetLayeredWindowAttributes(color=0x{color_key:X}) ret={la_ret} err={err}")
        except Exception as e:
            self.app.logger.log(f"[窗口] SetLayeredWindowAttributes 异常: {e}", error=True)

        # 强制显示（不依赖 CreateWindowExW 中的 WS_VISIBLE 标志）
        try:
            sw_ret = ShowWindow(self.hwnd, 5)  # SW_SHOW
            self.app.logger.log(f"[窗口] ShowWindow ret={sw_ret}")
        except Exception as e:
            self.app.logger.log(f"[窗口] ShowWindow 异常: {e}", error=True)

        # 强制刷新位置
        try:
            sp_ret = SetWindowPos(self.hwnd, HWND_TOPMOST, x, y, w, h,
                                  SWP_NOACTIVATE | SWP_SHOWWINDOW)
            self.app.logger.log(f"[窗口] SetWindowPos ret={sp_ret}")
        except Exception as e:
            self.app.logger.log(f"[窗口] SetWindowPos 异常: {e}", error=True)

        # 强制发送 WM_PAINT
        try:
            uw_ret = UpdateWindow(self.hwnd)
            self.app.logger.log(f"[窗口] UpdateWindow ret={uw_ret}")
        except Exception as e:
            self.app.logger.log(f"[窗口] UpdateWindow 异常: {e}", error=True)

        # 检查 IsWindowVisible
        try:
            visible = IsWindowVisible(self.hwnd)
            self.app.logger.log(f"[窗口] IsWindowVisible={visible}")
        except Exception as e:
            self.app.logger.log(f"[窗口] IsWindowVisible 异常: {e}", warning=True)

        # 设置工作区变化时的定时夹紧（低频检查）
        try:
            SetTimer(self.hwnd, self.TIMER_RECLAMP, 3000, None)
        except Exception as e:
            self.app.logger.log(f"[窗口] SetTimer 异常: {e}", warning=True)

        # 触发 WM_PAINT
        try:
            InvalidateRect(self.hwnd, None, True)
        except Exception as e:
            self.app.logger.log(f"[窗口] InvalidateRect 异常: {e}", warning=True)

        self._visible = True
        self.app.logger.log(f"[窗口] 桌面悬浮窗已激活: hwnd={self.hwnd}, 位置=({x},{y}), 尺寸={w}x{h}")

    def show_at_center(self):
        """强制将窗口显示在屏幕中央（兜底用 - 用户找不到窗口时用）"""
        try:
            wa_l, wa_t, wa_r, wa_b = self._get_work_area()
            wa_w = wa_r - wa_l
            wa_h = wa_b - wa_t
            x = wa_l + (wa_w - self._w) // 2
            y = wa_t + (wa_h - self._h) // 2
            self.app.logger.log(
                f"[窗口] 强制居中: 新位置=({x},{y}), 尺寸={self._w}x{self._h}")
            if not self.hwnd:
                # 还没创建窗口，先创建
                self._show_impl()
                return
            # 窗口已存在，移动到中心并显示
            SetWindowPos(self.hwnd, HWND_TOPMOST, x, y, self._w, self._h,
                         SWP_NOACTIVATE | SWP_SHOWWINDOW)
            ShowWindow(self.hwnd, 5)  # SW_SHOW
            InvalidateRect(self.hwnd, None, True)
            self._visible = True
        except Exception as e:
            self.app.logger.log(f"[窗口] show_at_center 异常: {e}", error=True)

    def hide(self):
        """隐藏窗口"""
        if self.hwnd:
            try:
                KillTimer(self.hwnd, self.TIMER_RECLAMP)
            except Exception:
                pass
            ShowWindow(self.hwnd, 0)  # SW_HIDE
            self._visible = False

    def destroy(self):
        """销毁窗口"""
        if self.hwnd:
            try:
                KillTimer(self.hwnd, self.TIMER_RECLAMP)
            except Exception:
                pass
            user32.DestroyWindow(self.hwnd)
            self.hwnd = None
        if self.menu:
            try:
                DestroyMenu(self.menu)
            except Exception:
                pass
            self.menu = None


# ==================== 启动日志（文件） ====================

class StartupLogger:
    """启动日志写入文件 - 避免多线程 Tk 冲突"""

    def __init__(self, log_path: str = "ip_monitor.log"):
        self.log_path = log_path
        self._lock = threading.Lock()

    def log(self, message: str, error: bool = False, warning: bool = False):
        tag = "ERROR" if error else ("WARN" if warning else "INFO")
        line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [{tag}] {message}"
        with self._lock:
            try:
                with open(self.log_path, 'a', encoding='utf-8') as f:
                    f.write(line + "\n")
            except Exception:
                pass
        try:
            print(line)
        except Exception:
            pass


# ==================== 详情窗口（子线程模式） ====================

class DetailWindow:
    """IP详情展示窗口 - 在独立线程中创建 Tk"""

    def __init__(self, app: 'IPMonitorApp'):
        self.app = app
        self.window = None
        self.is_visible = False
        self._thread = None
        self._tk = None
        self._ui_queue = queue.Queue()
        self._history_text = None
        self._status_label = None
        self._domestic_label = None
        self._foreign_label = None
        self._proxy_label = None
        self._runtime_label = None
        self._stats_label = None
        self._next_label = None
        self._error_label = None

    def _create_window(self):
        """在独立线程中创建详情窗口"""
        if self._thread and self._thread.is_alive():
            # 窗口已存在，置前
            self.bring_to_front()
            return

        def _run():
            try:
                self._tk = tk.Tk()
                self._tk.title("IP监测详情")
                self._tk.resizable(True, True)
                _setup_tk_window(self._tk, 560, 440, 420, 320)

                self._tk.protocol("WM_DELETE_WINDOW", self._on_close)
                self.window = self._tk

                main_frame = ttk.Frame(self._tk, padding=15)
                main_frame.pack(fill=tk.BOTH, expand=True)

                ttk.Label(main_frame, text="IP监测详情",
                          font=("微软雅黑", 14, "bold")).pack(pady=(0, 10))

                info_frame = ttk.LabelFrame(main_frame, text="当前状态", padding=10)
                info_frame.pack(fill=tk.X, pady=(0, 10))

                self._status_label = ttk.Label(info_frame, text="检测中...",
                                               font=("Consolas", 11))
                self._status_label.pack(anchor=tk.W)

                self._domestic_label = ttk.Label(info_frame, text="境内IP: 检测中...",
                                                 font=("微软雅黑", 10),
                                                 wraplength=900)
                self._domestic_label.pack(anchor=tk.W, pady=2)

                self._foreign_label = ttk.Label(info_frame, text="境外IP: 检测中...",
                                                font=("微软雅黑", 10),
                                                wraplength=900)
                self._foreign_label.pack(anchor=tk.W, pady=2)

                self._proxy_label = ttk.Label(info_frame, text="代理状态: 检测中...",
                                              font=("微软雅黑", 10))
                self._proxy_label.pack(anchor=tk.W, pady=2)

                runtime_frame = ttk.LabelFrame(main_frame, text="运行详情", padding=10)
                runtime_frame.pack(fill=tk.X, pady=(0, 10))

                self._runtime_label = ttk.Label(runtime_frame, text="运行状态: 初始化中...",
                                                font=("微软雅黑", 10), wraplength=900)
                self._runtime_label.pack(anchor=tk.W, pady=2)

                self._stats_label = ttk.Label(runtime_frame, text="统计: 初始化中...",
                                              font=("微软雅黑", 10), wraplength=900)
                self._stats_label.pack(anchor=tk.W, pady=2)

                self._next_label = ttk.Label(runtime_frame, text="下次检测: 计算中...",
                                             font=("微软雅黑", 10), wraplength=900)
                self._next_label.pack(anchor=tk.W, pady=2)

                self._error_label = ttk.Label(runtime_frame, text="最近错误: 无",
                                              font=("微软雅黑", 10), wraplength=900)
                self._error_label.pack(anchor=tk.W, pady=2)

                hist_frame = ttk.LabelFrame(main_frame, text="变化历史", padding=10)
                hist_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))

                scroll_y = ttk.Scrollbar(hist_frame)
                scroll_y.pack(side=tk.RIGHT, fill=tk.Y)

                self._history_text = tk.Text(hist_frame, height=8, font=("Consolas", 9),
                                             wrap=tk.NONE,
                                             yscrollcommand=scroll_y.set, state=tk.DISABLED)
                self._history_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
                scroll_y.config(command=self._history_text.yview)

                btn_frame = ttk.Frame(main_frame)
                btn_frame.pack(fill=tk.X)

                ttk.Button(btn_frame, text="立即刷新",
                           command=self.app.manual_check).pack(side=tk.LEFT)
                ttk.Button(btn_frame, text="清空历史",
                           command=self._clear_history).pack(side=tk.LEFT, padx=(10, 0))
                ttk.Button(btn_frame, text="关闭",
                           command=self._on_close).pack(side=tk.RIGHT)

                def _update_wraplength(event=None):
                    try:
                        wrap = max(260, info_frame.winfo_width() - 28)
                        self._domestic_label.configure(wraplength=wrap)
                        self._foreign_label.configure(wraplength=wrap)
                        if self._runtime_label:
                            self._runtime_label.configure(wraplength=wrap)
                        if self._stats_label:
                            self._stats_label.configure(wraplength=wrap)
                        if self._next_label:
                            self._next_label.configure(wraplength=wrap)
                        if self._error_label:
                            self._error_label.configure(wraplength=wrap)
                    except Exception:
                        pass

                info_frame.bind("<Configure>", _update_wraplength)
                _update_wraplength()

                self.is_visible = True
                self._refresh_data()
                self._schedule_poll()

                self._tk.mainloop()
            except Exception as e:
                print(f"[详情窗] 异常: {e}")
            finally:
                self.is_visible = False
                self.window = None
                self._tk = None

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()

    def _schedule_poll(self):
        """定时轮询 UI 队列"""
        if not self._tk:
            return
        try:
            self._drain_ui_queue()
            self._refresh_runtime_data()
            self._tk.after(500, self._schedule_poll)
        except Exception:
            pass

    def _drain_ui_queue(self):
        """处理 UI 队列"""
        try:
            while True:
                cmd, args = self._ui_queue.get_nowait()
                if cmd == 'refresh':
                    self._refresh_data()
                elif cmd == 'clear':
                    self._clear_history_internal()
        except queue.Empty:
            pass
        except Exception:
            pass

    def _refresh_data(self):
        """刷新显示数据"""
        if not self._history_text or not self._status_label:
            return
        result = self.app.last_result
        self._refresh_runtime_data()

        if result:
            self._status_label.config(
                text=f"检测时间: {result.timestamp.strftime('%Y-%m-%d %H:%M:%S')}")

            d = result.domestic
            f_info = result.foreign
            domestic_ok = d.success and d.ip != "检测失败"
            foreign_ok = f_info.success and f_info.ip != "检测失败"

            self._domestic_label.config(
                text=f"境内IP: {d.ip}  ({d.location})",
                foreground="green" if domestic_ok else "red")
            self._foreign_label.config(
                text=f"境外IP: {f_info.ip}  ({f_info.location})",
                foreground="green" if foreign_ok else "red")

            proxy_text = (f"代理状态: {'✓ 一致（代理正常）' if result.is_same else '✗ 不一致（代理可能已断开）'}")
            self._proxy_label.config(text=proxy_text,
                                     foreground="green" if result.is_same else "red")
        else:
            self._status_label.config(text="尚未完成首次检测", foreground="gray")
            self._domestic_label.config(text="境内IP: 检测中...", foreground="gray")
            self._foreign_label.config(text="境外IP: 检测中...", foreground="gray")
            self._proxy_label.config(text="代理状态: 检测中...", foreground="gray")

        # 历史
        try:
            self._history_text.config(state=tk.NORMAL)
            self._history_text.delete("1.0", tk.END)
            history = self.app.change_history[-20:]
            for record in reversed(history):
                t = record['time'].strftime('%H:%M:%S')
                dt = record.get('domestic_ip', '')
                ft = record.get('foreign_ip', '')
                old_dt = record.get('old_domestic_ip', '')
                old_ft = record.get('old_foreign_ip', '')
                same = record.get('is_same', False)
                status = '✓' if same else '✗'
                tp = record.get('type', '')
                self._history_text.insert(tk.END, f"[{t}] {status} {tp}\n")
                if old_dt or old_ft:
                    self._history_text.insert(tk.END, f"    CN: {old_dt or '-'} -> {dt}\n")
                    self._history_text.insert(tk.END, f"    EN: {old_ft or '-'} -> {ft}\n\n")
                else:
                    self._history_text.insert(tk.END, f"    CN: {dt}  EN: {ft}\n\n")
            if not history:
                self._history_text.insert(tk.END, "暂无历史记录\n")
            self._history_text.config(state=tk.DISABLED)
        except Exception:
            pass

    def _refresh_runtime_data(self):
        """刷新程序运行详情。"""
        try:
            if not self._runtime_label:
                return
            now = datetime.now()
            uptime_seconds = max(0, int((now - self.app.start_time).total_seconds()))
            hours, rem = divmod(uptime_seconds, 3600)
            minutes, seconds = divmod(rem, 60)
            uptime_text = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
            checking_text = "检测中" if self.app._checking else "空闲"
            self._runtime_label.config(
                text=f"运行状态: {checking_text}  |  启动时间: {self.app.start_time.strftime('%Y-%m-%d %H:%M:%S')}  |  已运行: {uptime_text}")

            self._stats_label.config(
                text=(f"统计: 检测 {self.app.check_count} 次，成功 {self.app.check_success_count} 次，"
                      f"失败 {self.app.check_failure_count} 次，IP变化 {self.app.change_count} 次，"
                      f"最近耗时 {self.app.last_check_duration:.2f}s"))

            if self.app.next_check_time:
                remain = max(0, int((self.app.next_check_time - now).total_seconds()))
                self._next_label.config(
                    text=f"下次检测: {self.app.next_check_time.strftime('%Y-%m-%d %H:%M:%S')}（约 {remain}s 后）")
            else:
                self._next_label.config(text="下次检测: 尚未安排")

            last_error = self.app.last_check_error or "无"
            color = "red" if self.app.last_check_error else "gray"
            self._error_label.config(text=f"最近错误: {last_error}", foreground=color)
        except Exception:
            pass

    def _clear_history_internal(self):
        """内部清空历史（线程中执行）"""
        self.app.change_history.clear()
        self._refresh_data()

    def _clear_history(self):
        """清空历史按钮回调"""
        from tkinter import messagebox
        if messagebox.askyesno("确认", "确定清空所有历史记录？"):
            try:
                self._ui_queue.put_nowait(('clear', None))
            except Exception:
                pass

    def show(self):
        self._create_window()

    def bring_to_front(self):
        if self._tk:
            try:
                self._tk.after(0, lambda: (
                    self._tk.lift(),
                    self._tk.attributes('-topmost', True)
                ))
            except Exception:
                pass

    def _on_close(self):
        try:
            if self._tk:
                self._tk.quit()
                self._tk.destroy()
        except Exception:
            pass
        self.is_visible = False
        self.window = None
        self._tk = None

    def hide(self):
        self._on_close()

    def update_data(self):
        """通知窗口刷新数据（线程安全）"""
        try:
            self._ui_queue.put_nowait(('refresh', None))
        except Exception:
            pass


# ==================== IP变化告警窗口（子线程模式） ====================

class AlertWindow:
    """IP变化告警弹窗 - 独立线程创建 Tk，避免阻塞主消息循环。"""

    def __init__(self, app: 'IPMonitorApp'):
        self.app = app
        self._thread = None
        self._tk = None
        self._queue = queue.Queue()

    def show(self, record: Dict):
        try:
            self._queue.put_nowait(record)
        except Exception:
            pass
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        try:
            record = self._queue.get_nowait()
        except Exception:
            return
        try:
            self._tk = tk.Tk()
            self._tk.title("IP变化告警")
            self._tk.resizable(False, False)
            _setup_tk_window(self._tk, 520, 300, 420, 260)
            self._tk.attributes("-topmost", True)
            self._tk.protocol("WM_DELETE_WINDOW", self._on_close)

            main = ttk.Frame(self._tk, padding=18)
            main.pack(fill=tk.BOTH, expand=True)

            ttk.Label(main, text="检测到 IP 变化",
                      font=("微软雅黑", 16, "bold"),
                      foreground="#d32f2f").pack(anchor=tk.W, pady=(0, 12))

            ttk.Label(main, text=f"变化类型: {record.get('type', '')}",
                      font=("微软雅黑", 11)).pack(anchor=tk.W, pady=2)
            ttk.Label(main, text=f"检测时间: {record.get('time').strftime('%Y-%m-%d %H:%M:%S')}",
                      font=("微软雅黑", 10)).pack(anchor=tk.W, pady=2)

            detail = tk.Text(main, height=5, font=("Consolas", 10), wrap=tk.NONE)
            detail.pack(fill=tk.BOTH, expand=True, pady=(10, 12))
            detail.insert(tk.END, f"CN: {record.get('old_domestic_ip') or '-'} -> {record.get('domestic_ip')}\n")
            detail.insert(tk.END, f"EN: {record.get('old_foreign_ip') or '-'} -> {record.get('foreign_ip')}\n")
            detail.insert(tk.END, f"状态: {'一致' if record.get('is_same') else '不一致'}\n")
            detail.configure(state=tk.DISABLED)

            buttons = ttk.Frame(main)
            buttons.pack(fill=tk.X)
            ttk.Button(buttons, text="查看详情",
                       command=lambda: (self.app.detail_window.show(), self._on_close())).pack(side=tk.LEFT)
            ttk.Button(buttons, text="立即刷新",
                       command=self.app.manual_check).pack(side=tk.LEFT, padx=(8, 0))
            ttk.Button(buttons, text="关闭",
                       command=self._on_close).pack(side=tk.RIGHT)

            self._tk.after(15000, self._on_close)
            self._tk.mainloop()
        except Exception as e:
            try:
                self.app.logger.log(f"[告警] 弹窗失败: {e}", warning=True)
            except Exception:
                pass
        finally:
            self._tk = None

    def _on_close(self):
        try:
            if self._tk:
                self._tk.quit()
                self._tk.destroy()
        except Exception:
            pass


# ==================== 设置窗口（子线程模式） ====================

class SettingsWindow:
    """设置窗口 - 在独立线程中创建 Tk"""

    def __init__(self, app: 'IPMonitorApp'):
        self.app = app
        self._thread = None
        self._tk = None
        self.is_visible = False

    def show(self):
        if self._thread and self._thread.is_alive():
            self._bring_to_front()
            return

        def _run():
            try:
                self._tk = tk.Tk()
                self._tk.title("IP监测设置")
                self._tk.resizable(True, True)
                _setup_tk_window(self._tk, 480, 520, 380, 360)

                self._tk.protocol("WM_DELETE_WINDOW", self._on_close)

                main_frame = ttk.Frame(self._tk, padding=15)
                main_frame.pack(fill=tk.BOTH, expand=True)

                ttk.Label(main_frame, text="检测设置",
                          font=("微软雅黑", 12, "bold")).pack(pady=(0, 10))

                # 检测间隔
                ttk.Label(main_frame, text="检测间隔（秒）:").pack(anchor=tk.W)
                interval_var = tk.IntVar(value=self.app.config.get('check_interval', 30))
                ttk.Spinbox(main_frame, from_=5, to=3600,
                            textvariable=interval_var, width=20).pack(fill=tk.X, pady=(2, 8))

                # 超时
                ttk.Label(main_frame, text="请求超时（秒）:").pack(anchor=tk.W)
                timeout_var = tk.IntVar(value=self.app.config.get('timeout', 5))
                ttk.Spinbox(main_frame, from_=1, to=30,
                            textvariable=timeout_var, width=20).pack(fill=tk.X, pady=(2, 8))

                # 告警
                alert_change_var = tk.BooleanVar(value=self.app.config.get('alert_on_change', True))
                ttk.Checkbutton(main_frame, text="IP变化时显示通知",
                                variable=alert_change_var).pack(anchor=tk.W, pady=2)
                alert_sound_var = tk.BooleanVar(value=self.app.config.get('alert_sound', True))
                ttk.Checkbutton(main_frame, text="IP变化时播放声音",
                                variable=alert_sound_var).pack(anchor=tk.W, pady=2)

                # API 列表
                ttk.Label(main_frame, text="国内API（每行一个）:").pack(anchor=tk.W, pady=(8, 2))
                domestic_text = tk.Text(main_frame, height=4, font=("Consolas", 9), wrap=tk.NONE)
                domestic_text.pack(fill=tk.BOTH, expand=True, pady=(0, 5))
                apis = self.app.config.get('domestic_apis', [])
                domestic_text.insert("1.0", "\n".join(apis))

                ttk.Label(main_frame, text="国外API（每行一个）:").pack(anchor=tk.W, pady=(4, 2))
                foreign_text = tk.Text(main_frame, height=4, font=("Consolas", 9), wrap=tk.NONE)
                foreign_text.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
                apis = self.app.config.get('foreign_apis', [])
                foreign_text.insert("1.0", "\n".join(apis))

                btn_frame = ttk.Frame(main_frame)
                btn_frame.pack(fill=tk.X)

                def _do_save():
                    from tkinter import messagebox
                    try:
                        interval = interval_var.get()
                        if interval < 5 or interval > 3600:
                            messagebox.showerror("错误", "检测间隔必须在5-3600秒之间")
                            return
                        timeout = timeout_var.get()
                        if timeout < 1 or timeout > 30:
                            messagebox.showerror("错误", "超时时间必须在1-30秒之间")
                            return
                        domestic = [l.strip() for l in domestic_text.get("1.0", tk.END).split("\n") if l.strip()]
                        foreign = [l.strip() for l in foreign_text.get("1.0", tk.END).split("\n") if l.strip()]
                        if not domestic or not foreign:
                            messagebox.showerror("错误", "API列表不能为空")
                            return

                        self.app.config.set('check_interval', interval)
                        self.app.config.set('timeout', timeout)
                        self.app.config.set('alert_on_change', alert_change_var.get())
                        self.app.config.set('alert_sound', alert_sound_var.get())
                        self.app.config.set('domestic_apis', domestic)
                        self.app.config.set('foreign_apis', foreign)
                        self.app.config.save()
                        self.app.detector.timeout = timeout
                        messagebox.showinfo("成功", "设置已保存")
                        self._on_close()

                        if self.app._timer:
                            try:
                                self.app._timer.cancel()
                            except Exception:
                                pass
                        self.app._schedule_next_check()
                    except Exception as e:
                        messagebox.showerror("错误", f"保存失败: {e}")

                ttk.Button(btn_frame, text="保存", command=_do_save).pack(side=tk.RIGHT)
                ttk.Button(btn_frame, text="取消",
                           command=self._on_close).pack(side=tk.RIGHT, padx=(0, 5))

                self.is_visible = True
                self._tk.mainloop()
            except Exception as e:
                print(f"[设置窗] 异常: {e}")
            finally:
                self.is_visible = False
                self._tk = None

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()

    def _bring_to_front(self):
        if self._tk:
            try:
                self._tk.after(0, lambda: (
                    self._tk.lift(),
                    self._tk.attributes('-topmost', True)
                ))
            except Exception:
                pass

    def _on_close(self):
        try:
            if self._tk:
                self._tk.quit()
                self._tk.destroy()
        except Exception:
            pass
        self.is_visible = False
        self._tk = None


# ==================== 主应用程序 ====================

class IPMonitorApp:
    """IP监控主应用程序"""

    def __init__(self, config_path: str = "config.json", interval_override: int = None):
        self.config = ConfigManager(config_path)
        if interval_override:
            self.config.set('check_interval', interval_override)

        self.detector = IPDetector(self.config)

        # 关键：PyInstaller 打包后用 sys.executable 所在目录
        if getattr(sys, 'frozen', False):
            exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        else:
            exe_dir = os.path.dirname(os.path.abspath(__file__))
        self.logger = StartupLogger(os.path.join(exe_dir, "ip_monitor.log"))

        self.detail_window = DetailWindow(self)
        self.alert_window = AlertWindow(self)
        self.settings_window = SettingsWindow(self)
        self.widget = DesktopWidget(self)
        self.tray = None  # 延迟到 widget.show() 之后再创建，避免消息循环未启动

        self.start_time = datetime.now()
        self.last_result: Optional[CheckResult] = None
        self.last_domestic_ip: str = ""
        self.last_foreign_ip: str = ""
        self.change_history: List[Dict] = []
        self.max_history = self.config.get('max_history', 100)
        self.check_count = 0
        self.check_success_count = 0
        self.check_failure_count = 0
        self.change_count = 0
        self.last_check_duration = 0.0
        self.last_check_error = ""
        self.next_check_time: Optional[datetime] = None

        self._timer: Optional[threading.Timer] = None
        self._running = True
        self._lock = threading.Lock()
        self._checking = False

        # 启动时立即检测
        self._do_check()

    def _play_alert_sound(self):
        """播放告警声音"""
        if not self.config.get('alert_sound', True):
            return
        try:
            import winsound
            winsound.PlaySound("SystemAsterisk", winsound.SND_ALIAS | winsound.SND_ASYNC)
        except ImportError:
            try:
                print('\a')
            except Exception:
                pass
        except Exception as e:
            print(f"[声音] 播放失败: {e}")

    def _check_for_changes(self, result: CheckResult):
        changed = False
        change_parts = []
        old_domestic_ip = self.last_domestic_ip
        old_foreign_ip = self.last_foreign_ip

        if result.domestic.success and result.domestic.ip != "检测失败":
            if self.last_domestic_ip and result.domestic.ip != self.last_domestic_ip:
                changed = True
                change_parts.append("国内IP变化")

        if result.foreign.success and result.foreign.ip != "检测失败":
            if self.last_foreign_ip and result.foreign.ip != self.last_foreign_ip:
                changed = True
                change_parts.append("国外IP变化")

        if changed:
            change_type = "双重变化" if len(change_parts) > 1 else change_parts[0]
            record = {
                'time': result.timestamp,
                'type': change_type,
                'old_domestic_ip': old_domestic_ip,
                'old_foreign_ip': old_foreign_ip,
                'domestic_ip': result.domestic.ip,
                'foreign_ip': result.foreign.ip,
                'is_same': result.is_same
            }

            with self._lock:
                self.change_history.append(record)
                if len(self.change_history) > self.max_history:
                    self.change_history = self.change_history[-self.max_history:]
                self.change_count += 1

            if self.config.get('alert_on_change', True):
                self._trigger_change_alert(record)

        if result.domestic.success and result.domestic.ip != "检测失败":
            self.last_domestic_ip = result.domestic.ip
        if result.foreign.success and result.foreign.ip != "检测失败":
            self.last_foreign_ip = result.foreign.ip

    def _trigger_change_alert(self, record: Dict):
        """触发 IP 变化告警：声音、托盘气泡、置顶弹窗、日志。"""
        try:
            self._play_alert_sound()
            title = f"IP变化告警: {record.get('type', '')}"
            message = (
                f"CN: {record.get('old_domestic_ip') or '-'} -> {record.get('domestic_ip')}\n"
                f"EN: {record.get('old_foreign_ip') or '-'} -> {record.get('foreign_ip')}"
            )
            self.logger.log(f"[告警] {title} | {message.replace(chr(10), ' | ')}", warning=True)
            if self.tray:
                self.tray.show_balloon(title, message)
            self.alert_window.show(record)
        except Exception as e:
            self.logger.log(f"[告警] 触发失败: {e}", warning=True)

    def _do_check(self):
        """执行一次IP检测"""
        if self._checking:
            self.logger.log("[检测] 上次检测尚未完成，跳过本次")
            self._schedule_next_check()
            return

        self._checking = True
        started = time.time()
        self.check_count += 1
        self.last_check_error = ""
        self.logger.log(f"开始IP检测...")

        try:
            result = self.detector.check_both()
            self.last_check_duration = time.time() - started

            with self._lock:
                self.last_result = result

            self._check_for_changes(result)
            if ((result.domestic.success and result.domestic.ip != "检测失败") or
                    (result.foreign.success and result.foreign.ip != "检测失败")):
                self.check_success_count += 1
            else:
                self.check_failure_count += 1
                self.last_check_error = "境内/境外 IP 均检测失败"

            # 更新悬浮窗口
            is_normal = result.is_same
            self.widget.update(is_normal)

            # 更新详情窗口（通过队列）
            if self.detail_window.is_visible:
                self.detail_window.update_data()

            self.logger.log(
                f"完成 - 国内: {result.domestic.ip} | 国外: {result.foreign.ip} | "
                f"状态: {'一致' if result.is_same else '不一致'}")

        except Exception as e:
            self.last_check_duration = time.time() - started
            self.check_failure_count += 1
            self.last_check_error = str(e)
            self.logger.log(f"检测过程异常: {e}", error=True)
        finally:
            self._checking = False

        self._schedule_next_check()

    def _schedule_next_check(self):
        """安排下一次定时检测"""
        if not self._running:
            return

        if self._timer and self._timer.is_alive():
            try:
                self._timer.cancel()
            except Exception:
                pass

        interval = self.config.get('check_interval', 30)
        self.next_check_time = datetime.now() + timedelta(seconds=interval)
        self._timer = threading.Timer(interval, self._do_check)
        self._timer.daemon = True
        self._timer.start()

    def manual_check(self):
        """手动触发一次检测"""
        if not self._running:
            return
        threading.Thread(target=self._do_check, daemon=True).start()

    def _do_quit(self):
        """退出程序（由菜单调用）"""
        self.logger.log("正在关闭程序...")
        self._running = False

        if self._timer:
            try:
                self._timer.cancel()
            except Exception:
                pass

        self.widget.destroy()

        if self.tray:
            try:
                self.tray.destroy()
            except Exception:
                pass

        if self.detail_window.is_visible:
            try:
                self.detail_window._on_close()
            except Exception:
                pass

        try:
            self.alert_window._on_close()
        except Exception:
            pass

        if self.settings_window.is_visible:
            try:
                self.settings_window._on_close()
            except Exception:
                pass

        try:
            OleUninitialize()
        except Exception:
            pass

        sys.exit(0)

    def run(self):
        """启动应用程序"""
        print("=" * 60)
        print("  实时IP监测工具 v1.2 (桌面悬浮版)")
        print("=" * 60)
        print(f"  配置文件: {self.config.config_path}")
        print(f"  检测间隔: {self.config.get('check_interval')} 秒")
        print(f"  请求超时: {self.config.get('timeout')} 秒")
        print(f"  告警通知: {'开启' if self.config.get('alert_on_change') else '关闭'}")
        print("=" * 60)
        print("\n[启动] 程序已启动，桌面右下角显示IP悬浮窗")
        print("[提示] 拖动悬浮窗调整位置，双击打开详情，右键打开菜单\n")

        # 进入 Windows 消息循环
        msg = MSG()
        while self._running:
            ret = PeekMessageW(ctypes.byref(msg), 0, 0, 0, 0x0001)  # PM_REMOVE
            if ret:
                if msg.message == 0x0012:  # WM_QUIT
                    break
                TranslateMessage(ctypes.byref(msg))
                DispatchMessageW(ctypes.byref(msg))
            else:
                time.sleep(0.05)


# ==================== 命令行参数解析 ====================

def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description='实时IP监测工具 - 监控境内外IP地址变化（任务栏悬浮版）',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument('--config', '-c', type=str, default='config.json',
                        help='指定配置文件路径 (默认: config.json)')
    parser.add_argument('--interval', '-i', type=int, default=None,
                        help='覆盖检测间隔（秒），范围 5-3600 (默认: 使用配置文件值)')
    return parser.parse_args()


# ==================== 程序入口 ====================

def main():
    _enable_process_dpi_awareness()
    args = parse_args()

    if args.interval is not None:
        if args.interval < 5 or args.interval > 3600:
            print(f"错误: 检测间隔必须在 5-3600 秒之间，当前值: {args.interval}")
            sys.exit(1)

    # 关键：PyInstaller 打包后 __file__ 指向临时解压目录
    # 必须用 sys.executable 获取 EXE 所在目录
    if getattr(sys, 'frozen', False):
        script_dir = os.path.dirname(os.path.abspath(sys.executable))
    else:
        script_dir = os.path.dirname(os.path.abspath(__file__))
    if not os.path.isabs(args.config):
        config_path = os.path.join(script_dir, args.config)
    else:
        config_path = args.config

    # 文件日志
    log_path = os.path.join(script_dir, "ip_monitor.log")
    logger = StartupLogger(log_path)
    logger.log(f"=" * 50)
    logger.log(f"IP Monitor 启动 v1.1 任务栏悬浮版")
    logger.log(f"配置文件: {config_path}")
    logger.log(f"工作目录: {script_dir}")
    logger.log(f"Python: {sys.version.split()[0]}")
    logger.log(f"命令行: interval={args.interval}")
    logger.log(f"=" * 50)

    try:
        # 启动应用
        app = IPMonitorApp(config_path=config_path, interval_override=args.interval)
        logger.log(f"应用对象创建完成")

        # 显示任务栏悬浮窗口
        app.widget.show()
        if app.widget.hwnd:
            x, y, w, h = app.widget._get_widget_pos()
            logger.log(f"任务栏悬浮窗口已创建: hwnd={app.widget.hwnd}, 位置=({x},{y}), 尺寸={w}x{h}")
        else:
            logger.log("任务栏悬浮窗口创建失败！请检查 ip_monitor.log 中的详细错误", error=True)

        # 创建系统托盘图标（兜底入口，找不到悬浮窗时用）
        try:
            app.tray = SystemTrayIcon(app)
            if app.tray.registered:
                logger.log("系统托盘图标已创建 - 右键托盘图标可'显示悬浮窗'/'移到中央'/'退出'")
            else:
                logger.log("系统托盘图标注册失败，悬浮窗是唯一入口", warning=True)
        except Exception as e:
            logger.log(f"创建系统托盘图标失败: {e}", warning=True)

        # 等待首次检测
        logger.log("等待首次IP检测...")
        for i in range(40):  # 最多等 20 秒
            time.sleep(0.5)
            if app.last_result:
                d = app.last_result.domestic
                f = app.last_result.foreign
                logger.log(f"首次检测完成")
                logger.log(f"   CN: {d.ip}  ({d.location})")
                logger.log(f"   EN: {f.ip}  ({f.location})")
                logger.log(f"   代理状态: {'正常（一致）' if app.last_result.is_same else '异常（不一致）'}")
                break
        else:
            logger.log("20秒内未完成首次检测（可能网络问题）", warning=True)

        logger.log("=" * 50)
        logger.log("【使用说明】")
        logger.log("  左键单击: 立即刷新检测")
        logger.log("  左键双击: 打开详情窗口")
        logger.log("  右键: 弹出菜单")
        logger.log("  日志查看: 打开同目录的 ip_monitor.log")
        logger.log("=" * 50)

        # 启动主程序（消息循环）
        app.run()

    except KeyboardInterrupt:
        logger.log("用户中断程序")
        sys.exit(0)
    except Exception as e:
        logger.log(f"致命错误: {e}", error=True)
        import traceback
        logger.log(traceback.format_exc(), error=True)
        sys.exit(1)


if __name__ == '__main__':
    main()
