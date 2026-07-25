#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2025 Calibre-Web contributors
# Copyright (C) 2024-2025 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

import os
import sys


# Add local source and helper-script paths before importing any cps module.
path = os.path.dirname(os.path.abspath(__file__))
scripts_path = os.path.join(path, "scripts")
sys.path.insert(0, path)
if scripts_path not in sys.path:
    sys.path.insert(1, scripts_path)

from cps.main import main


def hide_console_windows():
    import ctypes

    kernel32 = ctypes.WinDLL('kernel32')
    user32 = ctypes.WinDLL('user32')

    SW_HIDE = 0

    hWnd = kernel32.GetConsoleWindow()
    if hWnd:
        user32.ShowWindow(hWnd, SW_HIDE)


if __name__ == '__main__':
    if os.name == "nt":
        hide_console_windows()
    main()



