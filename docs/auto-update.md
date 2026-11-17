# Artisan portable tự cập nhật (launcher + CircleCI + ERP)

Áp dụng cho bản **portable trên Windows** (thư mục PyInstaller onedir, không cài đặt) chạy trên các máy tính nối máy rang.
Cơ chế đi theo app mobile `lvg-erp-mobile`: CI build → upload lên danh sách phiên bản của ERP → app tự cập nhật, cờ `isForceUpdate` quyết định có bắt buộc hay không.

## 1. Bố cục thư mục trên máy rang

```
C:\LVG\Artisan\
  Artisan.exe                   launcher (Go, thư mục launcher/ của repo) — shortcut trỏ vào đây
  current.json                  {"current": "125", "previous": "120", "blocked": null}
  launcher.log                  nhật ký của launcher
  versions\125\artisan.exe ...  mỗi bản phát hành một thư mục (tên = versionCode = số pipeline CircleCI)
  versions\125\.healthy         Artisan ghi khi đã khởi động tới màn đăng nhập
  versions\130.partial\         đang tải, launcher bỏ qua
```

Đặt thư mục ở chỗ người dùng **có quyền ghi** (không đặt trong `Program Files`). Dữ liệu và cài đặt của Artisan nằm ở `%LOCALAPPDATA%` và registry, không nằm trong thư mục này, nên đổi bản không mất gì.

## 2. Hoạt động

| Lúc | Việc |
|---|---|
| Mở `Artisan.exe` | Launcher chạy `versions\<current>\artisan.exe` (bản hỏng/thiếu → `previous` → bản số lớn nhất), truyền `LVG_LAUNCHER_ROOT`, `LVG_LAUNCHER_EXE`. |
| Artisan vừa khởi động | Ghi `.healthy` cho bản đang chạy, dọn các bản không còn cần (giữ current, previous, bản đang chạy). |
| Sau đăng nhập ERP, trước khi chọn máy rang | Gọi `GET /api/app-version/latest?app=artisan-windows`, so `versionCode` với `buildCode` của bản đang chạy. |
| Có bản mới, `isForceUpdate = true` | Hộp tiến trình chặn: tải zip, kiểm **SHA-256**, giải nén vào `versions\<mới>`, đổi `current.json`, khởi động lại qua launcher. Lỗi → Thử lại / Thoát (bản bắt buộc nên không cho chạy tiếp bản cũ). |
| Có bản mới, không bắt buộc | Tải ngầm, xong thì báo ở thanh thông báo; **lần mở sau** chạy bản mới. Mẻ rang đang chạy không bị ảnh hưởng. |
| Bản mới không khởi động được | Artisan thoát mà chưa ghi `.healthy` → launcher quay về `previous`, ghi bản hỏng vào `blocked` (Artisan không tải lại bản đó), báo người dùng. Bản sửa lỗi có số lớn hơn sẽ được cập nhật bình thường. |
| Menu **Help → Check for Updates** | Kiểm tra ngay, tải ngầm nếu có bản mới. |

Chạy từ mã nguồn hoặc bản portable cũ không có launcher: tự cập nhật tắt.

## 3. Phát hành

1. Merge vào `dev` (môi trường dev) hoặc `master` (production).
2. CircleCI: bấm **approve** (`approve_dev` / `approve_prod`) → build launcher → build Windows → publish:
   1. `POST $APP_VERSIONS_URL/direct-upload` (khai báo version, SHA-256, size) → ERP trả URL PUT có chữ ký tới R2 (hạn 1 giờ);
   2. `PUT` zip **thẳng lên R2** (không qua VPS, không qua proxy Cloudflare nên không vướng giới hạn 100MB);
   3. `POST $APP_VERSIONS_URL/direct-upload/complete` → ERP kiểm object có và đúng size, tạo bản ở trạng thái "đang đẩy";
   4. worker ERP đọc lại file trên R2, kiểm SHA-256: khớp thì công bố (máy mới thấy), lệch thì xoá file và đánh lỗi.
3. Bản mới hiện trên màn **Phiên bản ứng dụng** của ERP với ứng dụng *Artisan Windows (zip)*; cột R2 chuyển "đã lên R2" khi worker kiểm xong.
4. Muốn bắt buộc: bật **Force update** cho bản đó trên web.

Artifact của job `windows_build` (tab Artifacts trên CircleCI):

- `artisan-win-<version>-<build>.zip` + `.json`: bản phát hành (đã upload).
- `Artisan-portable-<version>-<build>.zip`: **bộ cài lần đầu** (launcher + `current.json` + `versions\<build>\`).

Upload tay trên màn admin (chọn ứng dụng Artisan, file zip + file `.json`) đi qua VPS nên chỉ dùng cho bản nhỏ hơn giới hạn body (dev); bản thật luôn đi đường CI.

### Context CircleCI

Hai context `artisan-dev`, `artisan-prod`, cùng tên biến:

| Biến | Ý nghĩa |
|---|---|
| `ERP_API_BASE_URL`, `ERP_ENVIRONMENT`, (`ERP_SOCKET_URL`) | ERP mà bản build nói chuyện (đóng vào `erp-config.json`) |
| `APP_VERSIONS_URL` | `https://<erp-api host>/api/app-version` — API phát hành của ERP (chỉ JSON nhỏ đi qua đây) |
| `APP_VERSIONS_TOKEN` | trùng `APP_VERSIONS_TOKEN` của backend ERP |

Phía ERP cần: R2 phát hành đã cấu hình (`MOBILE_RELEASE_R2_*`) cho **cả `backend-go` lẫn `worker-go`**, khoá R2 có quyền đọc + ghi bucket đó (API ký URL PUT bằng khoá này). Thiếu R2 thì bước 1 trả 503. Chi tiết: `lvg-erp/MOBILE_FCM_AND_UPDATES_SETUP.md` mục 2.6.

AppVeyor (CI của Artisan gốc) đã bị xoá khỏi bản fork (`.appveyor.yml`, `.ci/upload.sh`): repo fork không nối với AppVeyor. Khi merge từ upstream, xung đột "upstream sửa / mình xoá" ở `.appveyor.yml` thì giữ việc xoá.

## 4. Chuyển một máy từ bản portable cũ (làm tay một lần)

1. Đóng Artisan.
2. Giải nén `Artisan-portable-<version>-<build>.zip` vào `C:\LVG\` (ra `C:\LVG\Artisan\`).
3. Đổi shortcut (Desktop, Start, Startup) trỏ vào `C:\LVG\Artisan\Artisan.exe`. Nếu máy mở file `.alog` bằng Artisan thì đổi liên kết file sang launcher này.
4. Mở Artisan, đăng nhập như bình thường. Thư mục portable cũ có thể xoá sau khi chạy ổn.

**Máy mới (chưa từng chạy Artisan):** bộ cài NSIS của bản gốc tự chạy *Microsoft Visual C++ Redistributable*; bản portable thì không. Máy đang chạy được bản portable cũ thì nhiều khả năng đã có runtime này. Với máy mới, chạy một lần (quyền admin) `versions\<số bản>\vc_redist.x64.exe` có sẵn trong bộ cài lần đầu, rồi mới mở Artisan. Chưa kiểm trên một máy Windows sạch xem thiếu runtime thì Artisan có chạy được không.

## 5. Rollback

- Bản mới lỗi nhưng **vẫn khởi động được**: phát hành một bản sửa (số pipeline mới lớn hơn). Xoá bản lỗi trên web chỉ ngăn máy *chưa* tải; máy đã tải vẫn chạy bản đó tới khi có bản mới hơn.
- Bản mới **không khởi động được**: launcher tự quay về bản trước (mục 2).
- Quay tay một máy: sửa `current.json` (`current` = thư mục bản muốn chạy).

## 6. Mã nguồn

| Phần | File |
|---|---|
| Tải, kiểm, cài, `current.json`, dọn | `src/erp/updater.py` |
| Khi nào cập nhật (sau đăng nhập, ngầm, menu) | `src/erp/update_flow.py`, móc trong `src/artisanlib/main.py` (`ensure_update`, `checkUpdate`) |
| Build number trong `erp-config.json` | `src/make-erp-config.py --build-code` / `ERP_BUILD_CODE`, đọc bằng `erp.config.build_code()` |
| Đóng zip + metadata + bộ cài lần đầu | `src/make-portable-zip.py`, gọi từ `build-win3-pi.bat` khi `ARTISAN_PORTABLE=1` |
| Launcher | `launcher/` (`go test ./...`, `launcher/build.sh <thư mục>`) |
| CI | `.circleci/config.yml` (job `publish`: direct-upload → PUT R2 → complete), `.ci/circleci-build-win.bat` |
| Phía ERP | `lvg-erp`: cột `app_versions.app` (migration 000129), `MOBILE_FCM_AND_UPDATES_SETUP.md` mục 2.6 |
