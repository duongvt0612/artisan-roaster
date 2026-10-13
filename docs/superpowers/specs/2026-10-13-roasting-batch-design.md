# Bắt đầu / kết thúc mẻ rang gắn với lệnh sản xuất ERP (Giai đoạn 1: lõi mẻ rang)

## Context

Bước 1 (đăng nhập ERP) và bước 2 (chọn và chiếm máy rang bằng lease) đã xong và đã commit. Bước 3: người rang bấm **START** trên Artisan thì bắt buộc chọn **lệnh sản xuất** (gõ để tìm, hiển thị mã lệnh, thành phẩm, kg) để mẻ rang trên ERP được tạo đúng lệnh; sau **DROP** phải kết thúc mẻ trên ERP bằng kg bán thành phẩm thực tế.

Phạm vi lớn hơn bạn yêu cầu (upload `.alog`/profile gắn đúng mẻ, tự xuất PDF, màn "Nhật ký rang xay" xem/tải) đã được tách thành **hai giai đoạn, thiết kế chung, làm GD1 trước**:
- **GD1 (spec này):** lõi mẻ rang — ERP API riêng cho Artisan + hộp START / Kết thúc mẻ + xử lý mẻ dở.
- **GD2 (spec/kế hoạch riêng, sau):** bảng lưu file gắn `roundId` (`.alog` + PDF), upload/tải, hàng đợi gửi lại khi mất mạng, màn Nhật ký rang xay (frontend Vue) xem/tải; Artisan tự xuất PDF bằng `roastReport(pdf_filename)` (`main.py:23665`).

Đã chốt với chủ repo (không bàn lại):
- START (không phải ON) là lúc chọn lệnh và tạo mẻ ERP.
- Nhân đầu vào tự điền từ lệnh, người rang nhập kg.
- Kết thúc: hộp "Kết thúc mẻ" sau DROP/dừng ghi, bắt buộc nhập kg bán thành phẩm.
- Mẻ bỏ dở: ERP thêm API huỷ mẻ đang chạy; Artisan tự phát hiện mẻ dở khi mở lại và hỏi Tiếp tục/Huỷ.
- Kiến trúc A: endpoint đồng bộ mới `/api/artisan/...` trong module `production`, kiểm lease cùng giao dịch, tái dùng hàm tx round hiện có.
- Mất mạng/ERP lỗi thì START không chạy (nhất quán quyết định "chặn hoàn toàn khi offline" từ bước 1).

## Hiện trạng đã kiểm chứng

- ERP: lệnh sản xuất = `production_commands` (mã `LS-MMYY-NNNNNN`, `final_output_inventory_id`, `final_target_quantity`, status `pending|in_progress|completed|cancelled`, `category roasting|mixing_packaging`, `kind normal|test`). Mẻ = `production_rounds` (+ `production_round_inputs`) nằm trong `production_command_step_allocations`. Start/complete hiện chạy **bất đồng bộ qua RabbitMQ**, không trả `roundId`, không kiểm lease/máy, `equipmentId` tuỳ chọn, không có rule một mẻ đang chạy mỗi máy, không có huỷ mẻ đang chạy (`batch-cancel` chỉ huỷ mẻ đã hoàn thành chưa tính giá). `GET /api/production-command` không có `q`.
- Có sẵn hàm đồng bộ `Repo.StartRound` / `Repo.CompleteRound` (`internal/production/adapters/postgres/round_tx.go`) gọi `createRoundInTx` / `completeRoundInTx` trong `withTx`; `domain.ValidateWorkerBatchInputQuota`, `RoastingLossApplicable/LossRate/LossExceeded`.
- ERP lưu file lên đĩa bằng mẫu multipart (`shared/evidence`, `legal/adapters/http/files.go`) — dùng cho GD2.
- Frontend ERP (Vue 3 + CoreUI): `RoastingStartRoundModal.vue`, `RoastingCompleteRoundModal.vue`, mobile `RoastMobileView.vue` + `roast-mobile.helpers.ts` (chỉ rang lệnh `roasting` có đơn vị kg) — dùng làm mẫu từ ngữ ("Bán thành phẩm", "Hao hụt", "Màu bột", "Cà phê nhân", "Mẻ #N", nhãn trạng thái).
- Artisan: START = `canvas.ToggleRecorder` → `OnRecorder` (`canvas.py:14096/13904`); `OnRecorder` còn gọi trực tiếp ở `main.py:12442`; `markDrop` `:14961`; `OffRecorder` `:14015`; `reset` `:7747`; Weight In/Out `qmc.weight`; `qmc.ground_color` (→ "Màu bột").

## Thiết kế

### 1. ERP — API cho Artisan (`/api/artisan/...`, JWT + `roasters:operate`; start cần `production-commands:start`, complete cần `production-commands:complete`)

| API | Việc |
|---|---|
| `GET /roasting-commands?q=&limit=20` | Lệnh rang được: status `pending`/`in_progress`, category `roasting`, bước chưa `completed/skipped`, đơn vị vào/ra kg, gồm `kind=test` (nhãn "Mẻ thử"). `q` khớp mã lệnh, mã/tên thành phẩm, tên khách. Mỗi dòng: `commandId, code, kind, status, stepNumber, output{inventoryId,code,name,unit}, targetQuantity, producedQuantity, remainingQuantity, source{requestCode,customerName}, inputs[{inventoryId,code,name,allocationId,availableKg}]`. Đang thực hiện xếp trước. |
| `POST /rounds/start` | `{equipmentId, sessionId, version, commandId, stepNumber, inputs[{inventoryId,quantity}], notes?}` → 201 `{roundId, roundNumber, expectedOutputQuantity, startedAt}`. |
| `POST /rounds/{id}/complete` | `{outputQuantity, notes?, powderColor?}` → `{outputQuantity, expectedOutputQuantity, lossRate, lossExceeded}`. |
| `POST /rounds/{id}/cancel` | `{reason}` → huỷ mẻ `in_progress`, hoàn `consumed_quantity` của phân bổ, bước/lệnh quay về trạng thái đúng, ghi log. |
| `GET /roasters/{equipmentId}/active-round` | Mẻ đang chạy trên máy (lệnh, thành phẩm, kg nhân, người bắt đầu, giờ bắt đầu) hoặc rỗng. |

Quy tắc (một giao dịch DB):
- **Start:** khoá hàng `roaster_leases` của máy (`FOR UPDATE`), kiểm `(holder=user, session, version)` khớp (giống heartbeat: không đòi lease chưa hết hạn) → sai: 409 `LEASE_LOST`. Máy phải là `roasting_machine` đang `is_active`. Không được có mẻ `in_progress` nào khác trên máy → 409 `ROASTER_BUSY` kèm thông tin mẻ. Sau đó chạy logic start cũ (hạn mức nhân, trừ phân bổ, đánh dấu bước/lệnh `in_progress`). `pic` = user đăng nhập. Dự phòng thứ hai: partial unique index `(equipment_id) WHERE status='in_progress'` (migration kiểm dữ liệu trùng trước khi tạo).
- **Complete / cancel:** người bắt đầu mẻ luôn kết thúc/huỷ được mẻ của mình kể cả khi lease đã mất (dữ liệu thật đã phát sinh ở xưởng). Huỷ mẻ của người khác cần `roasters:force-release`.
- Mẻ lưu thêm `source='artisan'`, `artisan_session_id`, `roaster_lease_version`, `cancel_reason/cancelled_at/cancelled_by`.
- Vẫn phát realtime cũ `production-round:started|completed` và event huỷ tương ứng (kiểm allowlist/parity test trong `internal/shared/config/testdata` + `eventcatalog` nếu thêm event mới) nên ERP web/mobile không đổi.
- Lỗi theo envelope hiện có; mã máy đọc được ở `errors[0]`: `LEASE_LOST`, `ROASTER_BUSY`, `QUOTA_EXCEEDED`, `COMMAND_NOT_ROASTABLE`, `ROUND_NOT_RUNNING`.

### 2. Artisan (`src/erp/`)
- `rounds.py`: client 5 API, ánh xạ lỗi thành exception.
- `batch.py` (`BatchManager`): `IDLE → RUNNING → DONE/CANCELLED`; lưu file cục bộ (roundId, mã lệnh, giờ START); dùng `session_id`/`version` của `LeaseManager`.
- `start_dialog.py`: ô chọn lệnh gõ-để-tìm (debounce ~250ms, luồng nền), mỗi lựa chọn 2 dòng (`LS-… · Thành phẩm · N kg` / `Còn lại M kg · Đơn … · Khách … · Trạng thái`), nhãn "Mẻ thử"; bảng loại nhân tự điền (khả dụng, ô nhập kg, báo đỏ khi vượt khả dụng/công suất máy); nút "Bắt đầu rang" khoá tới khi hợp lệ. Thành công: điền Weight In + tiêu đề profile, rồi mới gọi `OnRecorder`. Lỗi (kể cả mất lease/mạng) → START không chạy.
- `finish_dialog.py`: tự hiện sau dừng ghi; đóng hộp thì dải xanh giữ nút "Kết thúc mẻ trên ERP". Hiện kg dự kiến, hao hụt tính sống (đỏ khi vượt ngưỡng), Màu bột lấy từ `ground_color`; "Hoàn thành mẻ rang" → điền Weight Out.
- `gate.py` thêm `before_start / before_reset / on_roast_stopped / resume_if_any`; hook trong `canvas.ToggleRecorder` (và guard `main.py:12442`), `reset`, `OffRecorder`, `closeApp`, đăng xuất.
- RESET khi mẻ đang chạy: hỏi "Huỷ mẻ trên ERP (nhập lý do)" / "Giữ lại". Không START mẻ mới và cảnh báo khi thoát/đăng xuất nếu còn mẻ chưa kết thúc.
- Mở lại sau sập: sau khi chiếm máy gọi `active-round`; mẻ của chính user → hộp Tiếp tục/Huỷ (nói rõ đường rang chưa lưu có thể đã mất); mẻ của người khác → chặn START, hiện tên và giờ bắt đầu.
- Dải xanh + tiêu đề cửa sổ thêm "Đang rang LS-… · N kg" (lưu ý bài học: Artisan có stylesheet `*` ở `main.py:6113`, kiểm màu chữ trên màn thật).
- Khi lưu profile `.alog` ghi khối `erp {roundId, commandCode, roundNumber}` (chuẩn bị cho GD2).

### 3. Thiết kế chung cho GD2 (chưa làm ở GD1, chỉ chừa chỗ)
- Bảng `production_round_files(id, round_id → production_rounds, kind alog|pdf, filename, size, sha256, stored_path, uploaded_by, created_at)`; upload multipart `POST /api/artisan/rounds/{id}/files` (idempotent theo `sha256`), tải `GET /api/production-command/rounds/{id}/files/{fileId}`; Artisan gửi sau `complete` và có hàng đợi thử lại; frontend Nhật ký rang xay thêm cột/nút xem & tải.

## Thứ tự thực hiện (tự làm trực tiếp, không dùng sub agent để sửa code)
0. Lưu spec này vào `docs/superpowers/specs/2026-10-13-roasting-batch-design.md` (repo Artisan). Tạo nhánh mới ở `lvg-erp` từ nhánh hiện tại (kiểm trạng thái sạch trước). Không commit nếu chưa được yêu cầu.
1. ERP lượt 1: migration `000072` (cột mới + partial unique index có preflight), sqlc, domain/app/postgres cho 5 API, viết test trước (đơn vị + integration có đồng thời).
2. ERP lượt 2: HTTP + wiring `cmd/api/app.go` + permission, realtime event huỷ nếu cần (+ allowlist/catalog), chạy migrate trên DB local sau khi duyệt (air tự build lại).
3. Artisan lượt 1: `rounds.py`, `batch.py` + test.
4. Artisan lượt 2: `start_dialog`, `finish_dialog`, `gate`, hook `canvas.py/main.py`, banner/tiêu đề, khối `erp` trong `.alog` + test, smoke test cập nhật.
5. Kiểm thử end-to-end thật, chạy app để bạn xem trên màn hình thật, rà soát, báo cáo.

## Kiểm chứng
- **ERP:** `go test ./internal/production/... ./internal/roaster/... ./internal/shared/...`; integration (`-tags=integration`, `dbtest.Open`): N phiên cùng START trên một máy → đúng 1 thắng, còn lại `ROASTER_BUSY`; version cũ/đã bị ép nhả → `LEASE_LOST`; vượt hạn mức nhân → `QUOTA_EXCEEDED`; cancel hoàn đúng `consumed_quantity` và đưa bước/lệnh về trạng thái đúng; người khác không huỷ được nếu thiếu `roasters:force-release`; complete bởi chính người bắt đầu khi lease đã mất vẫn thành công; test parity realtime nếu thêm event.
- **Local thật** (backend `air` cổng 10301, tài khoản `admin` truyền qua `E2E_USER`/`E2E_PASS`, không ghi mật khẩu vào code): script claim máy → search lệnh → start → complete/cancel → kiểm dữ liệu trong ERP; hai phiên tranh một máy.
- **Artisan:** `pytest test/unitary/erp` (HTTP giả, Qt offscreen), smoke `test_main_smoke.py` (chạy smoke trước), ruff/mypy sạch trên `erp/`; chạy `main.main()` offscreen end-to-end: login → chọn máy → START → chọn lệnh → DROP → kết thúc mẻ; kịch bản sập giữa mẻ rồi mở lại; mở app thật để bạn xác nhận hộp thoại và dải xanh đọc được.

## Giả định và rủi ro
- Quyền `production-commands:start/complete` + `roasters:operate` phải được cấp cho role vận hành rang (migration bước 2 chỉ cấp `roasters:operate` cho ADMIN); sẽ báo rõ khi xong.
- Partial unique index có thể thất bại nếu dữ liệu thật đã có hai mẻ `in_progress` cùng máy; migration kiểm trước và dừng với thông báo thay vì làm hỏng.
- Phiên bản Python/PyQt hiện tại: ô chọn lệnh dùng widget Qt chuẩn (QLineEdit + popup danh sách), không thêm thư viện.
- Chưa kiểm chứng: Windows, PyInstaller; hộp thoại mới sẽ được bạn xem trên màn hình thật.
- Hai việc đã đề xuất trước đó vẫn là việc riêng, chưa làm: đổi hậu tố tiêu đề "LABEL!ISTEN (Release Sponsor)" sang thương hiệu LVG; kiểm đóng gói PyInstaller cho python-socketio.

## Điều chỉnh khi triển khai (13/10/2026)

Những chỗ làm khác kế hoạch ban đầu, và lý do:

- **Không có unique index một-mẻ-một-máy.** Luồng ERP web/mobile hiện cho phép nhiều mẻ `in_progress` cùng một máy; unique index sẽ làm hỏng luồng đó. Luật do API Artisan giữ bằng cách khoá hàng lease trong cùng giao dịch (`ROASTER_BUSY`), kèm chỉ mục thường `idx_production_rounds_running_equipment` để tra nhanh.
- **Huỷ mẻ không đưa lệnh/bước về trạng thái cũ.** START tự chuyển lệnh `pending` → `in_progress` và ghi đè `start_date`; không khôi phục được ngày dự kiến ban đầu. Lệnh `in_progress` vẫn rang được nên để nguyên. Cũng không hoàn `consumed_quantity` vì mẻ Artisan không gắn phân bổ nguyên liệu (giống ERP web).
- **"Khả dụng" của nhân tính như màn ERP web:** các dòng phiếu xuất kho **đã hoàn thành** của lệnh khớp công thức, gom về mặt hàng cha, trừ nhân đã dùng ở các mẻ chưa huỷ (không dùng phân bổ nguyên liệu, vì web đã bỏ `materialAllocationId`). Bước START khoá bước rồi mới tính nên hai máy không rút quá một lượng nhân.
- **Ngưỡng hao hụt** (`tolerancePercentage`) trả về ngay ở START và `active-round` để Artisan tô đỏ đúng ngưỡng của công thức khi người rang nhập kg.
- **Ô chọn lệnh là danh sách luôn hiện dưới ô gõ** (không phải popup): gõ → lọc sau ~250ms → chọn dòng. Dễ dùng trên màn cảm ứng và không dính lỗi focus của popup.
- **Không lưu mẻ đang chạy ở máy cục bộ.** ERP là nguồn sự thật: khi mở lại Artisan hỏi `active-round` của máy. Artisan không vào được khi mất kết nối ERP nên lưu cục bộ không thêm giá trị.
- **Nút "Kết thúc mẻ trên ERP" trên dải thông báo xuất hiện ngay khi mẻ bắt đầu**; bấm trước DROP thì được nhắc chờ. Hộp kết thúc mẻ tự mở khi dừng ghi.
- **Phím Ctrl+Enter** trước đây gọi thẳng `OnRecorder()` (bỏ qua START), nay đi qua `ToggleRecorder()` để không có lối tắt vòng qua ERP.
- **Khối `erp` trong profile** là ba khoá phẳng `erpRoundId`, `erpCommandCode`, `erpRoundNumber`, chỉ ghi khi profile đúng là bản ghi của mẻ đó (so `roastepoch`), và vẫn ghi sau khi mẻ đã kết thúc.
