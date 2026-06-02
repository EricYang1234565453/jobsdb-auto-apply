@echo off
echo ============================================================
echo   JobsDB Cookie 快速提取指南
echo ============================================================
echo.
echo 步驟：
echo   1. 用 Chrome 打開 https://hk.jobsdb.com 並登錄
echo   2. 按 F12 打開 DevTools
echo   3. 點 Application 標籤
echo   4. 左側 Cookies ^> https://hk.jobsdb.com
echo   5. 找到以下 cookies，複製 Value：
echo.
echo   必填：
echo     - JobseekerSessionId
echo     - __cf_bm
echo.
echo   可選：
echo     - _cfuvid
echo     - JobseekerVisitorId
echo.
echo   6. 將值填入 config.ini 的 [cookies] 段
echo.
echo ============================================================
echo.
echo 或者，用 Console 面板一鍵複製所有 cookies：
echo   在 DevTools Console 中輸入：
echo     copy(document.cookie)
echo   然後粘貼給 AI，AI 會自動解析
echo.
pause
