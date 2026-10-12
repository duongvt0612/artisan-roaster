# Chọn và chiếm máy rang (lease) sau khi đăng nhập ERP

## Context

Bước 1 (đăng nhập ERP bắt buộc) đã xong. Bước 2: ngay sau khi đăng nhập, Artisan phải lấy danh sách máy rang từ ERP, **bắt buộc** người dùng chọn một máy, và **tại một thời điểm chỉ một phiên đã đăng nhập được chiếm một máy rang**. Đây là tiền đề cho phần rang ở bước sau. Mỗi máy rang nối với một PC Windows, nên Artisan cần một định danh thiết bị ổn định.

Đã chốt với chủ repo (không bàn lại):
- Cơ chế **A**: lease trong Postgres + HTTP; websocket (Socket.IO) chỉ để đẩy tin, không nằm trong đường đúng đắn của lock.
- Làm cả hai phía: sửa `lvg-erp/backend-go` và Artisan.
- Không ép máy–PC ở ERP, chỉ nhớ máy chọn lần trước ở Artisan.
- Có "ép nhả" bằng permission riêng + ghi log.

Hiện trạng đã kiểm chứng:
- ERP có `GET /api/equipment/roasting-machines` (`equipments:view`) nhưng không có trạng thái "đang bị chiếm"; `production_rounds.equipment_id` đã trỏ tới `equipments`.
- Backend Go: Go 1.25, Postgres (pgx + **sqlc**, `db/queries`, schema liệt kê trong `sqlc.yaml`), migration `db/migrations/0000NN_*.{up,down}.sql` (mới nhất `000070`), chạy bằng `make migrate` (`go run ./cmd/migrate up`). Test tích hợp dùng `dbtest.Open` (tag `integration`) và có mẫu nguyên tử `internal/catalog/adapters/postgres/bankconfig_atomic_integration_test.go`.
- Realtime: Socket.IO gateway `internal/platform/adapters/socketio/gateway.go`; chỉ giao event vào room khớp `deliverableRoom`, join qua `join-room` có allowlist + kiểm quyền; room nằm trong RAM **một replica**. Phát event bằng `realtime.Publisher` (`internal/shared/realtime`), lấy qua `realtimeFor(a.broker)` trong `cmd/api/app.go`.
- Permission được seed bằng migration (mẫu `000069_dashboard_my_work_permissions.up.sql`: `INSERT … ON CONFLICT DO NOTHING` vào `permissions` rồi `role_permissions`, ADMIN allow=true).
- Handler lấy user bằng `identityhttp.UserFrom(r)`; route gắn quyền bằng `mount(path, handler, "perm")` qua `a.withAuth`. `SessionService.Logout` (`identity/app/session.go:83`) hiện chỉ thu hồi refresh token.
- Backend local: tiến trình `api` cổng **10301** chạy bằng **`air`** (tự build lại khi sửa code), DB `localhost:15432` trong Docker (đã xác nhận là DB local, `APP_ENV=development`).
- Artisan: chưa có `python-socketio` (đã có `websocket-client`, `requests`, `keyring`).

## Thiết kế

### ERP (`lvg-erp/backend-go`) — module mới `internal/roaster/{domain,app,adapters/postgres,adapters/http}` (theo bố cục `catalog`)
- **Migration `000071_roaster_leases`**:
  - `roaster_leases(equipment_id uuid PK → equipments ON DELETE CASCADE, holder_user_id uuid NULL → users, session_id text NULL, device_id text NULL, device_name text NULL, version bigint NOT NULL DEFAULT 0, acquired_at, lease_expires_at, updated_at)`. Hàng **không xoá** khi nhả để `version` luôn tăng.
  - `roaster_lease_events(id, equipment_id, user_id, session_id, device_id, event, reason, created_at)` với `event ∈ claimed | released | taken_over | force_released | logout_released`. Không ghi heartbeat.
  - Permission `roasters:operate` và `roasters:force-release`, seed theo mẫu `000069` (ADMIN allow=true, vai khác allow=false). Thêm file vào `sqlc.yaml`.
- **Claim (nguyên tử, một câu lệnh)**: `INSERT … ON CONFLICT (equipment_id) DO UPDATE … WHERE` thành công khi: chưa có chủ, hoặc `lease_expires_at <= now()`, hoặc cùng `session_id` (idempotent, giữ `version`), hoặc cùng `device_id` + cùng user (mở lại ngay sau khi sập). Đổi chủ thì `version + 1`. Điều kiện máy là `roasting_machine` và `is_active` kiểm trong cùng câu lệnh (CTE). Giờ lấy theo `now()` của DB, không tin đồng hồ máy khách.
- **Heartbeat**: `UPDATE … SET lease_expires_at = now()+ttl WHERE equipment_id AND session_id AND version` — **không đòi lease còn hạn** (version chưa đổi nghĩa là chưa ai chiếm), nên chập chờn mạng không mất máy oan. Không khớp thì trả `LEASE_LOST`.
- **Release**: nhả khi `session_id` + `version` khớp, idempotent. **Force-release**: cần `roasters:force-release`, `version + 1` (chặn phiên cũ), ghi event kèm lý do, đẩy `roaster:lease-lost` cho chủ cũ.
- **Trạng thái "đang bị chiếm"** tính lúc đọc (`holder_user_id` có và `lease_expires_at > now()`), không có job quét.
- **API** (envelope `{success,errors,errorMessages,data}`): `GET /api/roasters` (máy rang đang hoạt động + `status`, `heldBy{userId,fullName,deviceName,since}`, `mine`), `POST /api/roasters/{id}/claim`, `…/heartbeat`, `…/release`, `…/force-release`. Quyền: `roasters:operate`, riêng force-release cần `roasters:force-release`. Lỗi 409 mang mã máy đọc được ở `errors[0]`: `ROASTER_OCCUPIED`, `LEASE_LOST` (không đổi envelope); client gọi lại `GET /roasters` để biết người đang giữ. `claim` trả `{version, leaseExpiresAt, ttlSeconds, heartbeatSeconds}`; TTL/heartbeat từ env `ROASTER_LEASE_TTL_SECONDS` (30) và `ROASTER_HEARTBEAT_SECONDS` (10).
- **Logout**: thêm port `LeaseReleaser` trong `identity/app/port.go` (identity không import `roaster`), `SessionService.Logout` nhả mọi lease của user (ghi `logout_released`).
- **Realtime**: thêm room `roasters` vào `deliverableRoom` và allowlist `join-room` (kiểm `roasters:operate`); event `roaster:changed` vào room `roasters`, `roaster:lease-lost` vào `user:<id>`. Mỗi chỗ thêm có test khoá theo quy ước của gateway. Phát lỗi không làm hỏng lock (chỉ log).
- **Wiring**: tạo service trong `cmd/api/app.go` cạnh `a.eq`, đăng ký route qua `a.withAuth`.

### Artisan (`src/erp/`)
- `device.py`: `device_id` UUID bền (QSettings), `session_id` mới mỗi lần chạy, tên PC, máy chọn lần trước.
- `client.py` / `session.py`: thêm request có Bearer, **tự refresh access token một lần khi 401** (hiện chỉ có login/refresh/logout).
- `roasters.py`: `list_roasters / claim / heartbeat / release / force_release`, ánh xạ mã lỗi thành `RoasterOccupied`, `LeaseLost`, `NetworkError`.
- `lease.py` (`LeaseManager`, QObject): heartbeat thread nền mỗi 10s (nhận `heartbeatSeconds` từ server); trạng thái `HELD → SUSPECT` (không heartbeat thành công > ~25s) `→ LOST`; phát signal `suspect/recovered/lost`; khi mạng về heartbeat quyết định thật. Mất lease thì **không xoá dữ liệu đang ghi cục bộ**.
- `realtime.py`: client Socket.IO (`python-socketio`), auth bằng access token, join `roasters` và nhận `roaster:changed` / `roaster:lease-lost`; đứt kết nối thì tự quay về poll 15s. Chỉ là tối ưu, mất websocket vẫn đúng.
- `roaster_dialog.py`: danh sách máy, trạng thái bằng **chữ + biểu tượng** (không chỉ màu), máy bị người khác giữ thì không chọn được và ghi "Đang dùng bởi X từ HH:mm"; chọn sẵn máy lần trước; "Ép nhả" chỉ hiện khi user có `roasters:force-release` (lấy từ permission trong profile ERP), có hộp xác nhận + nhập lý do; **đóng dialog = thoát app**. Dùng lại `build_stylesheet` của `erp/login.py` cho cùng ngôn ngữ thị giác.
- `gate.py`: thêm `require_roaster(aw)` chạy sau `require_login`. `main.py`: gọi sau cổng đăng nhập, trước `setActivationWindow`/`show`; **Viewer bỏ qua** việc chọn máy (chỉ đăng nhập). Nhả lease khi thoát (`closeApp`) và khi "Đăng xuất ERP". Mất quyền giữa chừng: banner đỏ không che dữ liệu + nút "Chọn lại máy", đặt cờ `aw.erp_lease_ok` để bước rang sau chặn điều khiển.
- Phụ thuộc: thêm `python-socketio` (và client `websocket-client` đã có) vào `requirements.txt`, kiểm `hiddenimports` của các file `.spec` PyInstaller.

## Thứ tự thực hiện (tự làm trực tiếp, không dùng sub agent để sửa code)
0. Tạo nhánh mới `feature/roaster-lease` trong `lvg-erp` từ nhánh hiện tại (`feature/lark-in-app-login`, đang sạch) để không lẫn với việc Lark; lưu thiết kế này vào `docs/superpowers/specs/2026-10-12-roaster-lease-design.md` của repo Artisan.
1. ERP lượt 1: migration + sqlc + domain/app/postgres, kèm test nguyên tử và test đơn vị (viết test trước).
2. ERP lượt 2: HTTP + permission + logout hook + realtime + wiring; chạy `make migrate` trên DB local rồi để `air` tự build lại.
3. Artisan lượt 1: `device`, request có refresh, `roasters`, `lease` + test.
4. Artisan lượt 2: `realtime`, `roaster_dialog`, `gate`, `main.py`, banner + test.
5. Kiểm thử end-to-end hai phiên, rà soát, báo cáo.

## Kiểm chứng
- **ERP:** `go test ./internal/roaster/... ./internal/platform/adapters/socketio/... ./internal/identity/...`; test tích hợp `go test -tags=integration ./internal/roaster/adapters/postgres` (dùng `dbtest.Open`, tạo DB tạm, không đụng `lvg_erp`). Phải có: N goroutine cùng claim một máy → đúng 1 thắng; hết hạn → bị chiếm và `version` tăng; heartbeat với `version` cũ → `LEASE_LOST`; heartbeat sau khi hết hạn nhưng chưa ai chiếm → vẫn gia hạn được; cùng `device_id` mở lại → chiếm ngay; ép nhả chặn phiên cũ; logout nhả máy; máy không `roasting_machine`/không active bị từ chối.
- **Local thật** (backend `air` cổng 10301, tài khoản `admin`): `make migrate` (thay đổi DB local, chỉ làm sau khi bạn duyệt plan), script hai phiên khác `device_id` cùng tranh một máy nhiều vòng → luôn đúng một người thắng; chờ >30s để thấy lease hết hạn; đẩy event qua Socket.IO nhận được ở client thử.
- **Artisan:** `pytest test/unitary/erp` (HTTP giả + đồng hồ giả + Qt offscreen cho dialog), smoke `test_main_smoke.py` cập nhật (cổng chọn máy, Viewer bỏ qua), ruff không thêm cảnh báo. Chạy app thật: login → hiện danh sách máy → chọn → cửa sổ chính; mở phiên thứ hai (script) chiếm máy khác/cùng máy để xem trạng thái "Đang dùng bởi…", banner khi bị ép nhả.

## Giả định và rủi ro
- Permission `roasters:operate` cần được cấp cho role vận hành; migration chỉ cấp ADMIN, quản trị phải bật thêm cho role khác (sẽ báo rõ khi xong).
- Một user có thể giữ nhiều máy ở nhiều PC; chưa có luật "đang rang giữa chừng thì không cho chiếm" (thuộc bước rang, đã chừa chỗ qua `version`).
- Gateway Socket.IO chỉ đúng với một replica; vì vậy lock không phụ thuộc nó. Rate limit 100 req/60s/route/IP: mỗi máy ~6 heartbeat/phút, cần đo khi nhiều PC chung IP.
- `python-socketio` chưa cài trong môi trường Python hiện tại; cần `pip install` (thay đổi môi trường máy) — sẽ xin xác nhận lúc cài.
- Chưa kiểm chứng: `sqlc generate` có sẵn trên máy không; nếu không có thì viết tay theo phong cách `internal/shared/db/*.sql.go` hoặc dùng `go run`/`uvx` an toàn, báo lại trước khi làm.
- Không commit nếu bạn chưa yêu cầu; hai repo mỗi bên một commit riêng khi bạn đồng ý.
