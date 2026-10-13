# Cấu hình máy chủ ERP theo môi trường

Artisan (bản ERP-only) **không có máy chủ mặc định**. Mỗi bản build mang địa chỉ ERP của môi trường nó nhắm tới
(`production`, `staging`, ...). Không có cấu hình thì Artisan từ chối đăng nhập và báo rõ, không đoán máy chủ.

## Thứ tự ưu tiên khi chạy
1. Biến môi trường (dành cho dev, test, E2E):
   - `ERP_API_BASE_URL` — ví dụ `http://localhost:10301/api`
   - `ERP_ENVIRONMENT` — tên môi trường; không đặt thì suy ra từ host (`localhost`, `erp-api-example-vn`, ...)
   - `ERP_SOCKET_URL` — chỉ khi realtime (Socket.IO) không nằm cùng host với API
2. File `erp-config.json` đóng cùng gói build (xem bên dưới).
3. Không có cả hai: màn hình "Bản Artisan này chưa được cấu hình máy chủ ERP", thoát, không gọi mạng.

Địa chỉ phải là `https://`; riêng `localhost`, `127.0.0.1`, `::1` được dùng `http://` (mật khẩu không đi qua HTTP thường tới máy lạ).
Người dùng cuối **không có chỗ nào để sửa** địa chỉ này sau khi cài: muốn đổi môi trường thì cài bản build khác.

## Tạo một bản build cho một môi trường
```bash
# macOS / Linux
ERP_API_BASE_URL=https://erp-api.example.vn/api ERP_ENVIRONMENT=production ./build-macos3.sh
# Windows
set ERP_API_BASE_URL=https://erp-api.example.vn/api
set ERP_ENVIRONMENT=production
build-win3-pi.bat
```
Script build gọi `src/make-erp-config.py` để ghi `src/erp-config.json` (`apiBaseUrl`, `environment`, tuỳ chọn `socketUrl`), và các file
`artisan-*.spec` đưa nó vào gói. **Thiếu file thì build dừng** với thông báo rõ, nên không thể tạo installer chưa biết máy chủ của mình.
`erp-config.json` không nằm trong git (chỉ có `erp-config.example.json`).

## Chạy từ mã nguồn (dev)
```bash
cd src
ERP_API_BASE_URL=http://localhost:10301/api python3 artisan.py
```

## Môi trường tách biệt nhau thế nào
Tên môi trường đi vào những chỗ không được dùng chung giữa hai bản build trên cùng một máy:
- keychain của hệ điều hành: dịch vụ `lvg-erp:<môi trường>` (đăng nhập bản staging không ghi đè / lộ cho bản production);
- hàng đợi tệp chờ gửi lên ERP: `<thư mục dữ liệu Artisan>/erp_upload_queue/<môi trường>/`;
- máy rang chọn lần trước: khoá `lastRoasterId.<môi trường>`.

Mã thiết bị (`deviceId`) là của máy tính nên dùng chung cho mọi môi trường.

Khác `production` thì tiêu đề cửa sổ có thêm `[STAGING]` (tên môi trường viết hoa) và màn hình đăng nhập ghi "MÔI TRƯỜNG STAGING",
để không ai rang nhầm trên máy chủ thử.
