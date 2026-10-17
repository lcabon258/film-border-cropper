# Film Border Cropper

Windows 本機底片翻拍裁切工具。現在基準為 **V1 Preview 4**。

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
python test_removal.py
node test_frontend.cjs
```

Preview 4 的測試範圍是9項影像／專案、3項路由、8項專案管理、8項移除安全檢查與29項前端邏輯檢查。原生Windows新視窗尚待實機確認。這些檢查不是獨立照片辨識準確率。

## 版本管理

- 程式碼與測試使用 Git 提交管理。
- 可攜 EXE／ZIP 適合附在 GitHub Releases，不提交到程式碼歷史。
- 照片、個人專案、完整執行環境與匯出結果不提交。
- 正式版前仍需獨立辨識率、更多中繼資料與乾淨 Windows 驗收。

目前未決定對外授權條款；不要在未獲擁有者決定前擅自加入開源授權。


### 從專案移除照片

單張可按「從專案移除這張」；多張可勾選縮圖，再按「從專案移除選取照片」。確認後從目前專案清單移除並自動保存。匯入、辨識或輸出進行中暫停此操作。

為保留恢復機會，專案內的照片副本、預覽及編輯紀錄移至 `Removed/<時間與識別碼>/`；外部匯入來源與 `Cropped` 既有輸出不變。Removed 不會自動清空，目前尚無介面內還原按鈕。可重新匯入來源照片，但會視為新照片，原有角點與確認狀態不會自動恢復。Removed 內保留 `removed_items.json` 供後續恢復使用。
