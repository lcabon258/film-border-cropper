# Film Border Cropper

Windows 本機底片翻拍裁切工具。現在基準為 **V1 Preview 3**。

## 功能

- 本機辨識 35mm 片框；四角拖曳、旋轉、內縮與透視校正。
- 開啟專案、從照片資料夾建立專案、多檔／資料夾加入目前專案。
- 人工確認／保留拒絕、自動跳下一張、背景批次輸出。
- JPEG／PNG／TIFF、8/16-bit；專案 JSON 自動保存。
- Windows 可攜版啟動器；原始照片不覆寫。

## 開發啟動

使用 Windows Python 3.12，安裝 requirements.txt 後執行：

```text
python -m pip install -r requirements.txt
python app/server.py --workspace Projects/MyFilm
```

瀏覽器會開啟本機頁面。Windows 原生專案選擇需要已建置的 FilmBorderCropper.exe；建置說明見 build_windows.ps1、build_portable.py。現有打包腳本仍需在一般全新開發環境補驗，不宣稱已完成 GitHub 自動建置。

## 測試

```text
python test_app.py
python test_routes.py
python test_projects.py
node test_frontend.cjs
```

Preview 3 的測試範圍是9項影像／專案、3項路由、8項專案管理與24項前端邏輯檢查。原生Windows新視窗尚待實機確認。這些檢查不是獨立照片辨識準確率。

## 版本管理

- 程式碼與測試使用 Git 提交管理。
- 可攜 EXE／ZIP 適合附在 GitHub Releases，不提交到程式碼歷史。
- 照片、個人專案、完整執行環境與匯出結果不提交。
- 正式版前仍需獨立辨識率、更多中繼資料與乾淨 Windows 驗收。

目前未決定對外授權條款；不要在未獲擁有者決定前擅自加入開源授權。
