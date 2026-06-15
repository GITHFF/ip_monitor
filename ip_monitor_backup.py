#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
实时IP监测工具 - Windows桌面应用
功能：
  - 系统托盘运行，实时监测境内外IP地址
  - 支持多API自动切换和重试机制
  - IP变化时气泡通知+声音提醒
  - 右键菜单：设置、刷新、历史、退出
  - 双击显示详细信息窗口
"""

import json
import os
import re
import sys
import time
import threading
import argparse
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Tuple

import requests
from PIL import Image, ImageDraw, ImageFont
import pystray
from pystray import MenuItem, Menu
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext


# ==================== 数据模型 ====================

@dataclass
class IPInfo:
    """IP信息数据类"""
    ip: str = ""
    location: str = ""  # 归属地或国家
    success: bool = False
    api_used: str = ""
    check_time: datetime = field(default_factory=datetime.now)


@dataclass
class CheckResult:
    """检测结果数据类"""
    domestic: IPInfo = field(default_factory=IPInfo)
    foreign: IPInfo = field(default_factory=IPInfo)
    is_same: bool = False  # 境内外IP是否一致（代理状态）
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
                    # 合并用户配置，保留默认值
                    for key in self.DEFAULT_CONFIG:
                        if key in user_config:
                            self.config[key] = user_config[key]

                    # 验证关键配置项
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
            # 验证检测间隔
            interval = self.config.get('check_interval', 30)
            if not isinstance(interval, int) or interval < 5 or interval > 3600:
                print(f"[配置] 检测间隔值无效: {interval}，使用默认值30")
                self.config['check_interval'] = 30

            # 验证超时时间
            timeout = self.config.get('timeout', 5)
            if not isinstance(timeout, int) or timeout < 1 or timeout > 30:
                print(f"[配置] 超时时间无效: {timeout}，使用默认值5")
                self.config['timeout'] = 5

            # 验证API列表
            domestic_apis = self.config.get('domestic_apis', [])
            foreign_apis = self.config.get('foreign_apis', [])
            if not isinstance(domestic_apis, list) or len(domestic_apis) == 0:
                print(f"[配置] 国内API列表为空，使用默认值")
                self.config['domestic_apis'] = self.DEFAULT_CONFIG['domestic_apis']
            if not isinstance(foreign_apis, list) or len(foreign_apis) == 0:
                print(f"[配置] 国外API列表为空，使用默认值")
                self.config['foreign_apis'] = self.DEFAULT_CONFIG['foreign_apis']

            # 验证历史记录数
            max_history = self.config.get('max_history', 100)
            if not isinstance(max_history, int) or max_history < 10 or max_history > 10000:
                print(f"[配置] 历史记录数无效: {max_history}，使用默认值100")
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
        """获取配置项"""
        return self.config.get(key, default)
    
    def set(self, key: str, value):
        """设置配置项"""
        self.config[key] = value


# ==================== IP检测器 ====================

class IPDetector:
    """IP地址检测器 - 处理境内外IP的API调用和结果解析"""
    
    # 正则表达式模式，用于从文本中提取IP地址
    IP_PATTERN = re.compile(r'\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b')
    
    def __init__(self, config: ConfigManager):
        self.config = config
        self.timeout = config.get('timeout', 5)
    
    def _make_request(self, url: str) -> Optional[str]:
        """
        发送HTTP请求获取响应内容
        返回响应文本或None（请求失败）
        """
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
        """从文本中提取第一个有效的IP地址"""
        if not text:
            return None
        match = self.IP_PATTERN.search(text)
        if match:
            ip = match.group()
            # 验证IP有效性（排除无效IP）
            if self._is_valid_ip(ip):
                return ip
        return None

    def _is_valid_ip(self, ip: str) -> bool:
        """验证IP地址是否有效"""
        try:
            parts = ip.split('.')
            if len(parts) != 4:
                return False
            for part in parts:
                num = int(part)
                if num < 0 or num > 255:
                    return False
                # 排除特殊地址
            # 排除0.0.0.0、255.255.255.255等特殊地址
            if ip.startswith('0.') or ip == '255.255.255.255':
                return False
            return True
        except (ValueError, AttributeError):
            return False
    
    def _parse_domestic_response(self, text: str, api_url: str) -> IPInfo:
        """
        解析国内API的返回结果
        不同API有不同的返回格式
        """
        info = IPInfo(api_used=api_url, check_time=datetime.now())
        
        try:
            # 格式1: myip.ipip.net 返回格式: "当前 IP：x.x.x.x  来自于：xx省 xx市 电信"
            if 'ipip.net' in api_url:
                ip_match = re.search(r'当前\s*IP[：:]\s*([\d.]+)', text)
                loc_match = re.search(r'来自于[：:]\s*(.+)', text)
                if ip_match:
                    info.ip = ip_match.group(1).strip()
                    info.location = loc_match.group(1).strip() if loc_match else "未知"
                    info.success = True
            
            # 格式2: cip.cc 返回格式: 多行文本包含IP和位置
            elif 'cip.cc' in api_url:
                lines = [l.strip() for l in text.split('\n') if l.strip()]
                for line in lines:
                    if line.startswith('IP') or 'IP' in line.upper():
                        ip = self._extract_ip(line)
                        if ip:
                            info.ip = ip
                    if '地址' in line or '地理位置' in line or '位置' in line:
                        # 提取冒号后面的内容
                        parts = line.split('：', 1) if '：' in line else line.split(':', 1)
                        if len(parts) > 1 and not info.location:
                            info.location = parts[1].strip()
                if info.ip:
                    info.success = True
                    if not info.location:
                        info.location = "未知"
            
            # 格式3: ip.sb/geoip 返回JSON格式
            # ip.sb实际返回格式: {"ip":"x.x.x.x","country_code":"XX","country":"CountryName","region":"Region","city":"City",...}
            elif 'ip.sb' in api_url:
                data = json.loads(text)
                info.ip = data.get('ip', '')
                # ip.sb的city和country都是字符串类型
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
            
            # 默认尝试通用解析
            else:
                ip = self._extract_ip(text)
                if ip:
                    info.ip = ip
                    info.location = "已检测"
                    info.success = True
        
        except json.JSONDecodeError:
            # JSON解析失败，尝试文本提取
            ip = self._extract_ip(text)
            if ip:
                info.ip = ip
                info.location = "已检测"
                info.success = True
        except Exception as e:
            print(f"[检测] 解析异常 ({api_url}): {e}")
        
        return info
    
    def _parse_foreign_response(self, text: str, api_url: str) -> IPInfo:
        """
        解析国外API的返回结果
        """
        info = IPInfo(api_used=api_url, check_time=datetime.now())
        
        try:
            # 格式1: ipify 返回纯JSON {"ip":"x.x.x.x"}
            if 'ipify' in api_url:
                data = json.loads(text)
                info.ip = data.get('ip', '')
                info.location = "Unknown"  # ipify不提供位置信息
                info.success = bool(info.ip)
            
            # 格式2: ipinfo.io 返回详细JSON
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
            
            # 格式3: ip.sb/geoip 返回JSON
            # ip.sb实际返回格式: {"ip":"x.x.x.x","country_code":"XX","country":"CountryName","region":"Region","city":"City",...}
            elif 'ip.sb' in api_url:
                data = json.loads(text)
                info.ip = data.get('ip', '')
                country = data.get('country', '')
                city = data.get('city', '')
                region = data.get('region', '')
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
            
            # 默认通用解析
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
        """
        检测国内IP地址
        遍历所有配置的国内API，直到成功或全部失败
        """
        apis = self.config.get('domestic_apis', [])
        for api_url in apis:
            print(f"[检测] 尝试国内API: {api_url}")
            text = self._make_request(api_url)
            if text is not None:
                info = self._parse_domestic_response(text, api_url)
                if info.success:
                    print(f"[检测] 国内IP检测成功: {info.ip} ({info.location})")
                    return info
        
        # 所有API都失败
        print("[检测] 国内IP检测失败")
        return IPInfo(ip="检测失败", location="", success=False, check_time=datetime.now())
    
    def detect_foreign(self) -> IPInfo:
        """
        检测国外IP地址
        遍历所有配置的国外API，直到成功或全部失败
        """
        apis = self.config.get('foreign_apis', [])
        for api_url in apis:
            print(f"[检测] 尝试国外API: {api_url}")
            text = self._make_request(api_url)
            if text is not None:
                info = self._parse_foreign_response(text, api_url)
                if info.success:
                    print(f"[检测] 国外IP检测成功: {info.ip} ({info.location})")
                    return info
        
        # 所有API都失败
        print("[检测] 国外IP检测失败")
        return IPInfo(ip="检测失败", location="", success=False, check_time=datetime.now())
    
    def check_both(self) -> CheckResult:
        """
        同时检测境内外IP并返回完整结果
        """
        domestic = self.detect_domestic()
        foreign = self.detect_foreign()
        
        # 判断境内外IP是否一致（用于判断代理状态）
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


# ==================== 图标生成器 ====================

class IconGenerator:
    """动态生成托盘图标"""
    
    @staticmethod
    def create_icon(is_normal: bool = True, size: int = 64) -> Image.Image:
        """
        生成IP监控图标
        is_normal: True=绿色(正常), False=红色(异常)
        """
        # 创建图像
        img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        
        # 选择颜色
        if is_normal:
            color = (46, 204, 113, 255)  # 绿色 - 正常
            border_color = (39, 174, 96, 255)
        else:
            color = (231, 76, 60, 255)   # 红色 - 异常
            border_color = (192, 57, 43, 255)
        
        # 绘制圆角矩形背景
        margin = 4
        radius = 12
        draw.rounded_rectangle(
            [margin, margin, size-margin, size-margin],
            radius=radius,
            fill=color,
            outline=border_color,
            width=2
        )
        
        # 绘制IP文字（简化显示为 "iP"）
        try:
            font_size = size // 3
            font = ImageFont.truetype("arial.ttf", font_size)
        except (IOError, OSError):
            # 如果系统没有arial.ttf，使用默认字体
            try:
                font = ImageFont.truetype("msyh.ttc", font_size)
            except (IOError, OSError):
                font = ImageFont.load_default()
        
        text = "iP"
        bbox = draw.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        x = (size - tw) // 2
        y = (size - th) // 2 - 2
        
        # 白色文字
        draw.text((x, y), text, fill=(255, 255, 255, 255), font=font)
        
        return img
    
    @staticmethod
    def create_status_dot(is_normal: bool = True, dot_size: int = 8) -> Image.Image:
        """
        创建小状态点图标（用于在标题旁显示状态）
        """
        img = Image.new('RGBA', (dot_size, dot_size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        
        color = (46, 204, 113, 255) if is_normal else (231, 76, 60, 255)
        
        draw.ellipse([0, 0, dot_size-1, dot_size-1], fill=color)
        
        return img


# ==================== 详情窗口 ====================

class DetailWindow:
    """IP详情展示窗口"""
    
    def __init__(self, app: 'IPMonitorApp'):
        self.app = app
        self.window = None
        self.is_visible = False
    
    def show(self):
        """显示详情窗口"""
        if self.is_visible:
            # 如果已经显示，则带到前台
            self.window.lift()
            self.window.focus_force()
            return
        
        self.is_visible = True
        self.window = tk.Toplevel()
        self.window.title("IP Monitor - 详细信息")
        self.window.geometry("600x500")
        self.window.protocol("WM_DELETE_WINDOW", self.hide)
        
        # 设置窗口图标
        try:
            icon_img = IconGenerator.create_icon(self.app.last_result.is_same if self.app.last_result else True, 32)
            # 转换为tkinter可用的格式需要额外处理，这里简化处理
        except Exception as e:
            print(f"[UI] 设置图标失败: {e}")
        
        self._build_ui()
        self._refresh_data()
    
    def hide(self):
        """隐藏窗口"""
        self.is_visible = False
        if self.window:
            self.window.withdraw()
    
    def _build_ui(self):
        """构建UI界面"""
        # 主框架
        main_frame = ttk.Frame(self.window, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # ===== 当前IP状态区域 =====
        status_frame = ttk.LabelFrame(main_frame, text="当前IP状态", padding="10")
        status_frame.pack(fill=tk.X, pady=(0, 10))
        
        # 国内IP
        ttk.Label(status_frame, text="国内IP:", font=('Microsoft YaHei', 10, 'bold')).grid(row=0, column=0, sticky='w', pady=2)
        self.domestic_var = tk.StringVar(value="--")
        ttk.Label(status_frame, textvariable=self.domestic_var, foreground='blue').grid(row=0, column=1, sticky='w', pady=2, padx=(10, 0))
        
        ttk.Label(status_frame, text="归属地:", font=('Microsoft YaHei', 10)).grid(row=1, column=0, sticky='w', pady=2)
        self.domestic_loc_var = tk.StringVar(value="--")
        ttk.Label(status_frame, textvariable=self.domestic_loc_var).grid(row=1, column=1, sticky='w', pady=2, padx=(10, 0))
        
        ttk.Label(status_frame, text="使用API:", font=('Microsoft YaHei', 9)).grid(row=2, column=0, sticky='w', pady=2)
        self.domestic_api_var = tk.StringVar(value="--")
        ttk.Label(status_frame, textvariable=self.domestic_api_var, foreground='gray').grid(row=2, column=1, sticky='w', pady=2, padx=(10, 0))
        
        # 分隔线
        ttk.Separator(status_frame, orient='horizontal').grid(row=3, column=0, columnspan=2, sticky='ew', pady=8)
        
        # 国外IP
        ttk.Label(status_frame, text="国外IP:", font=('Microsoft YaHei', 10, 'bold')).grid(row=4, column=0, sticky='w', pady=2)
        self.foreign_var = tk.StringVar(value="--")
        ttk.Label(status_frame, textvariable=self.foreign_var, foreground='green').grid(row=4, column=1, sticky='w', pady=2, padx=(10, 0))
        
        ttk.Label(status_frame, text="国家:", font=('Microsoft YaHei', 10)).grid(row=5, column=0, sticky='w', pady=2)
        self.foreign_loc_var = tk.StringVar(value="--")
        ttk.Label(status_frame, textvariable=self.foreign_loc_var).grid(row=5, column=1, sticky='w', pady=2, padx=(10, 0))
        
        ttk.Label(status_frame, text="使用API:", font=('Microsoft YaHei', 9)).grid(row=6, column=0, sticky='w', pady=2)
        self.foreign_api_var = tk.StringVar(value="--")
        ttk.Label(status_frame, textvariable=self.foreign_api_var, foreground='gray').grid(row=6, column=1, sticky='w', pady=2, padx=(10, 0))
        
        # 代理状态指示
        self.proxy_status_var = tk.StringVar(value="")
        self.proxy_label = ttk.Label(status_frame, textvariable=self.proxy_status_var, 
                                      font=('Microsoft YaHei', 11, 'bold'))
        self.proxy_label.grid(row=7, column=0, columnspan=2, sticky='ew', pady=(10, 0))
        
        # 最后检测时间
        self.check_time_var = tk.StringVar(value="")
        ttk.Label(status_frame, textvariable=self.check_time_var, 
                  font=('Microsoft YaHei', 9), foreground='gray').grid(row=8, column=0, columnspan=2, sticky='e')
        
        # ===== 历史记录区域 =====
        history_frame = ttk.LabelFrame(main_frame, text="变化历史记录", padding="10")
        history_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
        
        # 历史记录文本框
        self.history_text = scrolledtext.ScrolledText(history_frame, height=12, width=70, 
                                                      font=('Consolas', 9))
        self.history_text.pack(fill=tk.BOTH, expand=True)
        
        # 操作按钮
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill=tk.X)
        
        ttk.Button(btn_frame, text="立即刷新", command=self._on_refresh).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(btn_frame, text="清空历史", command=self._on_clear_history).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="关闭", command=self.hide).pack(side=tk.RIGHT)
    
    def _refresh_data(self):
        """刷新显示的数据"""
        if not self.app.last_result:
            return
        
        result = self.app.last_result
        
        # 更新国内IP信息
        d = result.domestic
        self.domestic_var.set(d.ip if d.success else "检测失败")
        self.domestic_loc_var.set(d.location if d.location else "--")
        self.domestic_api_var.set(d.api_used.split('/')[-1] if d.api_used else "--")
        
        # 更新国外IP信息
        f_info = result.foreign
        self.foreign_var.set(f_info.ip if f_info.success else "检测失败")
        self.foreign_loc_var.set(f_info.location if f_info.location else "--")
        self.foreign_api_var.set(f_info.api_used.split('/')[-1] if f_info.api_used else "--")
        
        # 更新代理状态
        if result.is_same:
            self.proxy_status_var.set("✓ 境内外IP一致 (代理正常工作)")
            self.proxy_label.configure(foreground='green')
        elif d.success and f_info.success:
            self.proxy_status_var.set("✗ 境内外IP不一致 (可能未启用代理)")
            self.proxy_label.configure(foreground='red')
        else:
            self.proxy_status_var.set("⚠ 部分检测失败")
            self.proxy_label.configure(foreground='orange')
        
        # 更新时间
        self.check_time_var.set(f"最后检测时间: {result.timestamp.strftime('%Y-%m-%d %H:%M:%S')}")
        
        # 更新历史记录
        self._update_history_display()
    
    def _update_history_display(self):
        """更新历史记录显示"""
        self.history_text.delete(1.0, tk.END)
        
        history = self.app.change_history
        if not history:
            self.history_text.insert(tk.END, "(暂无变化记录)")
            return
        
        # 显示最近的变化记录
        header = f"{'时间':<20} {'类型':<8} {'国内IP':<18} {'国外IP':<18} {'状态'}\n"
        self.history_text.insert(tk.END, header)
        self.history_text.insert(tk.END, "-" * 90 + "\n")
        
        for record in reversed(history[-50:]):  # 最近50条
            time_str = record['time'].strftime('%Y-%m-%d %H:%M:%S')
            change_type = record['type']
            dom_ip = record.get('domestic_ip', '--')[:16]
            for_ip = record.get('foreign_ip', '--')[:16]
            status = "一致" if record.get('is_same') else "不一致"
            
            line = f"{time_str:<20} {change_type:<8} {dom_ip:<18} {for_ip:<18} {status}\n"
            self.history_text.insert(tk.END, line)
    
    def _on_refresh(self):
        """手动刷新按钮"""
        self.app.manual_check()
        # 延迟一下再刷新UI，等待检测完成
        self.window.after(3000, self._refresh_data)
    
    def _on_clear_history(self):
        """清空历史记录"""
        if messagebox.askyesno("确认", "确定要清空所有历史记录吗？"):
            self.app.change_history.clear()
            self._update_history_display()


# ==================== 设置窗口 ====================

class SettingsWindow:
    """设置窗口"""
    
    def __init__(self, app: 'IPMonitorApp'):
        self.app = app
        self.window = None
    
    def show(self):
        """显示设置窗口"""
        if self.window and self.window.winfo_exists():
            self.window.lift()
            self.window.focus_force()
            return
        
        self.window = tk.Toplevel()
        self.window.title("设置")
        self.window.geometry("450x400")
        self.window.resizable(False, False)
        
        self._build_ui()
    
    def _build_ui(self):
        """构建设置界面"""
        main_frame = ttk.Frame(self.window, padding="15")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # 检测间隔
        row = 0
        ttk.Label(main_frame, text="检测间隔 (秒):").grid(row=row, column=0, sticky='w', pady=5)
        self.interval_var = tk.StringVar(value=str(self.app.config.get('check_interval', 30)))
        interval_spin = ttk.Spinbox(main_frame, from_=10, to=3600, textvariable=self.interval_var, width=15)
        interval_spin.grid(row=row, column=1, sticky='w', pady=5, padx=(10, 0))
        
        # 请求超时
        row += 1
        ttk.Label(main_frame, text="请求超时 (秒):").grid(row=row, column=0, sticky='w', pady=5)
        self.timeout_var = tk.StringVar(value=str(self.app.config.get('timeout', 5)))
        timeout_spin = ttk.Spinbox(main_frame, from_=3, to=30, textvariable=self.timeout_var, width=15)
        timeout_spin.grid(row=row, column=1, sticky='w', pady=5, padx=(10, 0))
        
        # 告警开关
        row += 1
        self.alert_var = tk.BooleanVar(value=self.app.config.get('alert_on_change', True))
        ttk.Checkbutton(main_frame, text="IP变化时弹出通知", variable=self.alert_var).grid(
            row=row, column=0, columnspan=2, sticky='w', pady=5)
        
        # 声音开关
        row += 1
        self.sound_var = tk.BooleanVar(value=self.app.config.get('alert_sound', True))
        ttk.Checkbutton(main_frame, text="通知时播放提示音", variable=self.sound_var).grid(
            row=row, column=0, columnspan=2, sticky='w', pady=5)
        
        # 分隔线
        row += 1
        ttk.Separator(main_frame, orient='horizontal').grid(row=row, column=0, columnspan=2, 
                                                              sticky='ew', pady=10)
        
        # API配置说明
        row += 1
        ttk.Label(main_frame, text="API配置 (需编辑config.json):", 
                  font=('Microsoft YaHei', 9, 'bold')).grid(row=row, column=0, columnspan=2, sticky='w')
        
        row += 1
        api_text = (
            "• 国内API: myip.ipip.net, cip.cc, ip.sb\n"
            "• 国外API: ipify.org, ipinfo.io, ip.sb\n"
            f"• 配置文件路径: {self.app.config.config_path}"
        )
        ttk.Label(main_frame, text=api_text, foreground='gray',
                  justify=tk.LEFT).grid(row=row, column=0, columnspan=2, sticky='w', pady=5)
        
        # 按钮
        row += 1
        btn_frame = ttk.Frame(main_frame)
        btn_frame.grid(row=row, column=0, columnspan=2, sticky='ew', pady=(15, 0))
        
        ttk.Button(btn_frame, text="保存设置", command=self._save_settings).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(btn_frame, text="取消", command=self.window.destroy).pack(side=tk.LEFT)
        ttk.Button(btn_frame, text="打开配置文件", command=self._open_config).pack(side=tk.RIGHT)
    
    def _save_settings(self):
        """保存设置"""
        try:
            interval = int(self.interval_var.get())
            timeout = int(self.timeout_var.get())
            
            if interval < 10 or interval > 3600:
                messagebox.showerror("错误", "检测间隔必须在 10-3600 秒之间")
                return
            if timeout < 3 or timeout > 30:
                messagebox.showerror("错误", "超时时间必须在 3-30 秒之间")
                return
            
            self.app.config.set('check_interval', interval)
            self.app.config.set('timeout', timeout)
            self.app.config.set('alert_on_change', self.alert_var.get())
            self.app.config.set('alert_sound', self.sound_var.get())
            self.app.config.save()
            
            # 更新检测器的超时设置
            self.app.detector.timeout = timeout
            
            # 如果间隔改变，重启定时器
            if hasattr(self.app, '_timer') and self.app._timer:
                self.app._schedule_next_check()
            
            messagebox.showinfo("成功", "设置已保存！")
            self.window.destroy()
            
        except ValueError:
            messagebox.showerror("错误", "请输入有效的数字")
    
    def _open_config(self):
        """打开配置文件"""
        import subprocess
        import os
        config_path = os.path.abspath(self.app.config.config_path)
        try:
            os.startfile(config_path)
        except Exception as e:
            messagebox.showerror("错误", f"无法打开配置文件:\n{e}")


# ==================== 主应用程序 ====================

class IPMonitorApp:
    """IP监控主应用程序"""
    
    def __init__(self, config_path: str = "config.json", interval_override: int = None):
        # 初始化配置
        self.config = ConfigManager(config_path)
        
        # 如果命令行指定了间隔，覆盖配置文件
        if interval_override:
            self.config.set('check_interval', interval_override)
        
        # 初始化组件
        self.detector = IPDetector(self.config)
        self.detail_window = DetailWindow(self)
        self.settings_window = SettingsWindow(self)
        
        # 状态变量
        self.last_result: Optional[CheckResult] = None
        self.last_domestic_ip: str = ""
        self.last_foreign_ip: str = ""
        self.change_history: List[Dict] = []
        self.max_history = self.config.get('max_history', 100)

        # 定时器相关
        self._timer: Optional[threading.Timer] = None
        self._running = True
        self._lock = threading.Lock()
        self._checking = False  # 防止重复检测的标志
        
        # 托盘图标
        self.icon: Optional[pystray.Icon] = None
        
        # 启动时立即执行一次检测
        self._do_check()
    
    def get_tray_title(self) -> str:
        """
        生成托盘图标的标题文本（双行显示）
        """
        if not self.last_result:
            return "CN: 检测中...\nEN: 检测中..."
        
        d = self.last_result.domestic
        f_info = self.last_result.foreign
        
        # 第一行：国内IP
        if d.success and d.ip != "检测失败":
            line1 = f"CN: {d.ip} ({d.location})"
        else:
            line1 = "CN: 检测失败"
        
        # 第二行：国外IP
        if f_info.success and f_info.ip != "检测失败":
            line2 = f"EN: {f_info.ip} ({f_info.location})"
        else:
            line2 = "EN: 检测失败"
        
        return f"{line1}\n{line2}"
    
    def update_tray_icon(self):
        """更新托盘图标和标题"""
        if not self.icon:
            return
        
        # 根据检测结果选择图标颜色
        is_normal = self.last_result.is_same if self.last_result else False
        new_image = IconGenerator.create_icon(is_normal=is_normal)
        
        # 更新图标
        self.icon.icon = new_image
        # 更新标题
        self.icon.title = self.get_tray_title()
    
    def _show_notification(self, title: str, message: str):
        """显示系统通知"""
        if self.icon:
            try:
                self.icon.notify(message, title)
            except Exception as e:
                print(f"[通知] 发送失败: {e}")
    
    def _play_alert_sound(self):
        """播放告警声音"""
        if not self.config.get('alert_sound', True):
            return

        try:
            import winsound
            winsound.PlaySound("SystemAsterisk", winsound.SND_ALIAS | winsound.SND_ASYNC)
        except ImportError:
            print("[声音] winsound模块不可用（非Windows系统）")
        except Exception as e:
            print(f"[声音] 播放失败: {e}")
            # 备选方案：使用print发出蜂鸣声
            try:
                print('\a')
            except Exception:
                pass
    
    def _check_for_changes(self, result: CheckResult):
        """检查IP是否发生变化"""
        changed = False
        change_type = ""
        
        # 检查国内IP变化
        if result.domestic.success and result.domestic.ip != "检测失败":
            if self.last_domestic_ip and result.domestic.ip != self.last_domestic_ip:
                changed = True
                change_type = "国内IP变化"
        
        # 检查国外IP变化
        if result.foreign.success and result.foreign.ip != "检测失败":
            if self.last_foreign_ip and result.foreign.ip != self.last_foreign_ip:
                changed = True
                change_type = "国外IP变化" if not change_type else "双重变化"
        
        # 记录变化
        if changed:
            record = {
                'time': result.timestamp,
                'type': change_type,
                'domestic_ip': result.domestic.ip,
                'foreign_ip': result.foreign.ip,
                'is_same': result.is_same
            }
            
            with self._lock:
                self.change_history.append(record)
                # 限制历史记录数量
                if len(self.change_history) > self.max_history:
                    self.change_history = self.change_history[-self.max_history:]
            
            # 发送告警
            if self.config.get('alert_on_change', True):
                msg_parts = []
                if "国内" in change_type or "双重" in change_type:
                    msg_parts.append(f"国内: {result.domestic.ip} ({result.domestic.location})")
                if "国外" in change_type or "双重" in change_type:
                    msg_parts.append(f"国外: {result.foreign.ip} ({result.foreign.location})")
                
                status = "✓ 一致(代理正常)" if result.is_same else "✗ 不一致(可能无代理)"
                
                notification_msg = "\n".join(msg_parts) + f"\n{status}"
                self._show_notification("IP地址变化", notification_msg)
                self._play_alert_sound()
        
        # 更新最后已知IP
        if result.domestic.success and result.domestic.ip != "检测失败":
            self.last_domestic_ip = result.domestic.ip
        if result.foreign.success and result.foreign.ip != "检测失败":
            self.last_foreign_ip = result.foreign.ip
    
    def _do_check(self):
        """执行一次IP检测（在后台线程中运行）"""
        # 防止重复检测
        if self._checking:
            print("[检测] 上次检测尚未完成，跳过本次")
            self._schedule_next_check()
            return

        self._checking = True
        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] 开始IP检测...")

        try:
            # 执行检测
            result = self.detector.check_both()

            with self._lock:
                self.last_result = result

            # 检查是否有变化
            self._check_for_changes(result)

            # 更新托盘显示（线程安全）
            self.update_tray_icon()

            # 更新详情窗口（如果打开着）
            if self.detail_window.is_visible:
                try:
                    # 在主线程中更新UI
                    if self.detail_window.window and self.detail_window.window.winfo_exists():
                        self.detail_window.window.after(0, self.detail_window._refresh_data)
                except Exception as e:
                    print(f"[UI] 更新详情窗口失败: {e}")

            print(f"[完成] 国内: {result.domestic.ip} | 国外: {result.foreign.ip} | "
                  f"状态: {'一致' if result.is_same else '不一致'}")

        except Exception as e:
            print(f"[错误] 检测过程异常: {e}")
        finally:
            self._checking = False

        # 安排下一次检测
        self._schedule_next_check()
    
    def _schedule_next_check(self):
        """安排下一次定时检测"""
        if not self._running:
            return

        # 取消之前的定时器（如果有）
        if self._timer and self._timer.is_alive():
            try:
                self._timer.cancel()
            except Exception as e:
                print(f"[定时器] 取消旧定时器失败: {e}")

        interval = self.config.get('check_interval', 30)

        def run_check():
            if self._running:
                self._do_check()

        self._timer = threading.Timer(interval, run_check)
        self._timer.daemon = True
        self._timer.start()

        print(f"[计划] 下次检测将在 {interval} 秒后执行")
    
    def manual_check(self):
        """手动触发一次检测"""
        print("[手动] 用户触发了手动检测")
        # 在新线程中执行，避免阻塞
        threading.Thread(target=self._do_check, daemon=True).start()
    
    # ==================== 托盘菜单回调 ====================
    
    def _on_show_detail(self, icon, item):
        """显示详情窗口"""
        self.detail_window.show()
    
    def _on_show_settings(self, icon, item):
        """显示设置窗口"""
        self.settings_window.show()
    
    def _on_refresh(self, icon, item):
        """手动刷新"""
        self.manual_check()
    
    def _on_show_history(self, icon, item):
        """显示历史记录（通过详情窗口）"""
        self.detail_window.show()
    
    def _on_quit(self, icon, item):
        """退出程序"""
        print("\n[退出] 正在关闭程序...")
        self._running = False
        
        # 停止定时器
        if self._timer:
            self._timer.cancel()
        
        # 停止托盘图标
        if self.icon:
            self.icon.stop()
        
        # 关闭详情窗口
        if self.detail_window.is_visible and self.detail_window.window:
            self.detail_window.window.destroy()
        
        sys.exit(0)
    
    def on_left_click(self, icon):
        """左键单击事件 - 刷新"""
        self.manual_check()
    
    def on_double_click(self, icon):
        """双击事件 - 显示详情"""
        self._on_show_detail(icon, None)
    
    def run(self):
        """启动应用程序"""
        print("=" * 60)
        print("  实时IP监测工具 v1.0")
        print("=" * 60)
        print(f"  配置文件: {self.config.config_path}")
        print(f"  检测间隔: {self.config.get('check_interval')} 秒")
        print(f"  请求超时: {self.config.get('timeout')} 秒")
        print(f"  告警通知: {'开启' if self.config.get('alert_on_change') else '关闭'}")
        print("=" * 60)
        
        # 创建托盘图标
        is_normal = self.last_result.is_same if self.last_result else True
        image = IconGenerator.create_icon(is_normal=is_normal)
        
        # 创建菜单
        menu = Menu(
            MenuItem('显示详情', self._on_show_detail),
            MenuItem('刷新检测', self._on_refresh),
            Menu.SEPARATOR,
            MenuItem('设置', self._on_show_settings),
            MenuItem('历史记录', self._on_show_history),
            Menu.SEPARATOR,
            MenuItem('退出', self._on_quit)
        )
        
        # 创建图标实例
        self.icon = pystray.Icon(
            name="IPMonitor",
            icon=image,
            title=self.get_tray_title(),
            menu=menu
        )
        
        # 设置点击事件
        self.icon.on_activate = self.on_left_click  # 单击
        # 注意：pystray的双击需要通过自定义方式实现
        # 这里我们用单击来刷新，通过菜单查看详情
        
        print("\n[启动] 程序已启动，正在系统托盘中运行...")
        print("[提示] 左键单击托盘图标可刷新，右键打开菜单\n")
        
        # 运行托盘图标（阻塞）
        self.icon.run()


# ==================== 命令行参数解析 ====================

def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description='实时IP监测工具 - 监控境内外IP地址变化',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python ip_monitor.py                    # 使用默认配置
  python ip_monitor.py --interval 60       # 每60秒检测一次
  python ipmonitor.py --config myconf.json # 使用指定配置文件
        """
    )
    
    parser.add_argument(
        '--config', '-c',
        type=str,
        default='config.json',
        help='指定配置文件路径 (默认: config.json)'
    )
    
    parser.add_argument(
        '--interval', '-i',
        type=int,
        default=None,
        help='覆盖检测间隔（秒），范围 10-3600 (默认: 使用配置文件值)'
    )
    
    return parser.parse_args()


# ==================== 程序入口 ====================

def main():
    """主函数"""
    # 解析命令行参数
    args = parse_args()
    
    # 验证间隔参数
    if args.interval is not None:
        if args.interval < 10 or args.interval > 3600:
            print(f"错误: 检测间隔必须在 10-3600 秒之间，当前值: {args.interval}")
            sys.exit(1)
    
    # 确保配置文件路径正确
    script_dir = os.path.dirname(os.path.abspath(__file__))
    if not os.path.isabs(args.config):
        config_path = os.path.join(script_dir, args.config)
    else:
        config_path = args.config
    
    print(f"[初始化] 配置文件路径: {config_path}")
    
    try:
        # 创建应用实例并运行
        app = IPMonitorApp(config_path=config_path, interval_override=args.interval)
        app.run()
        
    except KeyboardInterrupt:
        print("\n[中断] 用户中断程序")
        sys.exit(0)
    except Exception as e:
        print(f"[致命错误] {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
