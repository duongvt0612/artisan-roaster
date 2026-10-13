# Đính kèm .alog và PDF vào mẻ rang ERP (Giai đoạn 2)

## Context

Giai đoạn 1 (đã làm, chưa commit): người rang bấm START → chọn lệnh sản xuất → ERP tạo mẻ (có `roundId`) → dừng ghi → hộp "Kết thúc mẻ" nhập kg thành phẩm. Profile `.alog` đã mang `erpRoundId`, `erpCommandCode`, `erpRoundNumber` (chỉ khi đúng là bản ghi của mẻ đó). Xem `2026-10-13-roasting-batch-design.md`.

Giai đoạn 2: mỗi mẻ rang trên ERP có kèm đường rang của Artisan (`.alog`) và báo cáo PDF, gắn chính xác vào đúng mẻ; người ở văn phòng xem PDF và tải cả hai trên màn "Nhật ký rang xay" của ERP.

Đã chốt với chủ repo (không bàn lại):
- Gửi **tự động ngay khi kết thúc mẻ**, không phụ thuộc người rang có Save hay không.
- Trên ERP: xem PDF (tab mới), tải PDF và `.alog`. Không vẽ đường rang trên web.
- Gửi thất bại: **hàng đợi bền trên máy, tự gửi lại**.
- Cài `pyfulgur` đúng phiên bản trong `src/requirements.txt` (đã được đồng ý, thực hiện khi bắt đầu làm) để tạo và kiểm chứng PDF thật.
- Mỗi mẻ giữ **một file mới nhất cho mỗi loại**, không lưu lịch sử phiên bản. Quyền xem/tải dùng `production-commands:view` có sẵn, không thêm quyền mới.

## Hiện trạng đã kiểm chứng

- `.alog` ghi bằng `serialize()` = `repr(dict)` (Python literal, đọc bằng `ast.literal_eval`), **không phải JSON** (`artisanlib/util.py:1361`). Hàm dựng profile là `getProfile()`; PDF bằng `roastReport(pdf_filename=…)` (`main.py`), dùng `pyfulgur` (`htmltext2pdf`) và vẽ biểu đồ matplotlib nên phải chạy trên luồng giao diện.
- `pyfulgur` có trong `src/requirements.txt` nhưng chưa cài trên Python của máy dev.
- Thư mục dữ liệu cục bộ của Artisan: `artisanlib.util.getDataDirectory()` (`AppLocalDataLocation`).
- ERP lưu file lên đĩa bằng mẫu multipart có sẵn (`internal/shared/evidence`, `legal/adapters/http/files.go`: `CapRequestBody`, `ParseMultipartForm`, kiểm đuôi và MIME).
- Màn "Nhật ký rang xay": `frontend/src/views/production-logs/` (`RoastingLogs.vue`, `components/RoastingLogListTable.vue`, service `production-command.service.ts`, endpoint `GET /production-command/roasting-logs`).

## Thiết kế

### 1. ERP backend (`lvg-erp/backend-go`, module `production`)

**Migration `000073_production_round_files`:** bảng `production_round_files(id uuid PK, round_id uuid NOT NULL → production_rounds ON DELETE CASCADE, kind varchar(10) CHECK in ('alog','pdf'), filename, content_type, size_bytes bigint, sha256 char(64), stored_path text, uploaded_by uuid → users, created_at)` với `UNIQUE (round_id, kind)`.

**API:**
| API | Việc | Quyền |
|---|---|---|
| `POST /api/artisan/rounds/{id}/files` (multipart `kind`, `sha256`, `file`) | Gửi một file của mẻ | `roasters:operate` + `production-commands:complete`; chỉ người bắt đầu mẻ (hoặc có `roasters:force-release`) |
| `GET /api/production-command/rounds/{id}/files` | Liệt kê file của mẻ | `production-commands:view` |
| `GET /api/production-command/round-files/{roundId}/{fileId}` | PDF `Content-Disposition: inline`, `.alog` `attachment` | `production-commands:view` |

**Quy tắc khi nhận file:**
- Mẻ phải tồn tại và ở trạng thái `completed`; sai quyền: 403; mẻ không có: 404; mẻ chưa hoàn thành: 409 `ROUND_NOT_COMPLETED`.
- Giới hạn 20 MB mỗi file (chặn từ đầu request bằng `CapRequestBody`), `kind` chỉ `alog|pdf`, `sha256` do client khai phải khớp nội dung thật (ERP tự băm lại), không khớp: 400.
- **Gắn chính xác vào mẻ:** PDF phải bắt đầu bằng `%PDF-`. `.alog` là UTF-8, bắt đầu bằng `{`, và văn bản phải chứa đúng `'erpRoundId': '<id của mẻ>'` (không parse định dạng Python); không khớp: 400 `FILE_ROUND_MISMATCH`.
- Idempotent: đã có file cùng `(round, kind)` và cùng `sha256` → 200, không ghi lại. Khác nội dung → ghi file mới, thay hàng, xoá file cũ sau khi commit.
- Ghi file bằng tên ngẫu nhiên do server đặt (không dùng tên client) trong thư mục lưu trữ của ERP, ghi vào file tạm rồi đổi tên, DB commit sau khi file đã nằm đúng chỗ.
- Mọi lỗi nghiệp vụ trả mã máy đọc được ở `errors[0]` như các API Artisan khác.

**Nhật ký rang xay:** `roasting-logs` trả kèm `files: [{id, kind, filename, sizeBytes}]` cho từng mẻ (một truy vấn gộp theo danh sách id mẻ của trang, không N+1).

### 2. ERP frontend (`lvg-erp/frontend`)
- `production-command.service.ts`: thêm kiểu `RoundFile`, hàm mở/tải file (tải qua `fetch` có Bearer rồi mở `blob:` vì route cần JWT; không đặt token lên URL).
- `RoastingLogListTable.vue`: thêm cột **"Tệp"**: nút "Xem PDF" (tab mới) và "Tải .alog"; mẻ chưa có file thì ô trống. Giữ nguyên ngôn ngữ giao diện CoreUI hiện có.

### 3. Artisan (`src/erp/`)
- `attachments.py` (hàng đợi + luồng nền): thư mục `<getDataDirectory()>/erp_upload_queue/`; mỗi việc là cặp `<id>.bin` + `<id>.json` (`roundId`, `kind`, `filename`, `sha256`, `attempts`, `next_try_at`). Ghi nguyên tử (file tạm rồi đổi tên). Luồng `erp-upload` gửi lần lượt; xong thì xoá việc.
- Thử lại: lỗi mạng / 5xx / 429 → giãn dần 30 s, 60 s, 2 min … tối đa 10 phút. 401 → tạm dừng tới khi đăng nhập lại. 4xx khác (mẻ không có, không phải của mình, file bị từ chối) → **lỗi vĩnh viễn**: không thử lại, giữ file trong thư mục `rejected/` kèm lý do và báo cho người dùng một lần. Khởi động lại Artisan quét hàng đợi và chạy tiếp.
- `roast_files.py`: dựng file cho mẻ vừa kết thúc: `.alog` = `getProfile(generate_hash=True)` → `serialize` vào hàng đợi; PDF = `roastReport(pdf_filename=…)` trên luồng giao diện (con trỏ chờ, vài giây). PDF lỗi hoặc thiếu `pyfulgur` thì vẫn gửi `.alog`, ghi log, không chặn gì. Chỉ tạo khi `profile_link` còn khớp bản ghi hiện hiển thị (cùng `roastepoch`), nếu người rang đã RESET / mở profile khác thì không tạo (tránh gửi nhầm).
- `roast_flow.finish_batch`: sau khi ERP nhận kg, nếu **đã dừng ghi** thì dựng và xếp hàng ngay; nếu **còn ghi** thì đặt cờ "chờ" và dựng ở `roast_stopped`. Một mẻ chỉ dựng một lần.
- Dải thông báo: thêm "· N tệp chưa gửi lên ERP" khi hàng đợi chưa rỗng; thông báo ngắn ở thanh trạng thái khi một mẻ gửi xong.
- Thoát Artisan khi hàng đợi còn việc: không chặn, việc ở lại cho lần sau.

## Thứ tự thực hiện (tự làm trực tiếp, không dùng sub agent để sửa code)
1. ERP: migration, sqlc, domain/app/postgres/http cho 3 API (viết test trước), mở rộng `roasting-logs`; chạy migrate trên DB local.
2. ERP frontend: service + cột "Tệp".
3. Artisan: `attachments.py` (+test), `roast_files.py` (+test), nối vào `roast_flow`/dải thông báo.
4. Cài `pyfulgur` (đúng phiên bản trong `requirements.txt`).
5. Kiểm thử end-to-end thật: PDF thật, gửi thật, xem/tải trên ERP; kịch bản mất mạng giữa chừng và tắt/mở lại Artisan; chạy app và ERP frontend để chủ repo xem.

## Kiểm chứng
- **ERP:** test đơn vị (validate, mã lỗi), HTTP (quyền, multipart, giới hạn dung lượng), tích hợp Postgres thật (idempotent theo `sha256`, thay file, sai `erpRoundId`, sai người, mẻ chưa hoàn thành, hai lần gửi song song cùng `(round, kind)` chỉ còn một hàng, xoá mẻ thì mất hàng), `go vet`, `gofmt`, toàn bộ test hiện có.
- **Artisan:** test hàng đợi bền (khởi động lại giữa chừng, lỗi tạm / vĩnh viễn, 401, trùng, ghi nguyên tử), dựng file (PDF lỗi vẫn gửi `.alog`, bản ghi khác thì không dựng), smoke test, ruff/mypy sạch trên `erp/`.
- **Thật:** `main()` offscreen chạy trọn luồng đến khi file nằm trên ERP và tải về được; ERP frontend chạy local, xem PDF và tải `.alog`; chủ repo xem trên màn hình thật.

## Giả định và rủi ro
- `.alog` gửi ở định dạng gốc (Python literal) để Artisan mở lại được; ERP không hiểu nội dung, chỉ kiểm `erpRoundId` và kích thước.
- Tạo PDF chạy trên luồng giao diện nên Artisan đứng hình vài giây sau khi kết thúc mẻ (người rang đang cân/đổ nhân, không đang ghi). Nếu thấy quá lâu thì đưa về chế độ tạo ở nền ở lần sau.
- Hai mẻ kết thúc liên tiếp rất nhanh: việc dựng file chạy tuần tự trên luồng giao diện, mỗi mẻ một lần.
- Chưa kiểm chứng: Windows, PyInstaller đóng gói `pyfulgur`.
- Không commit nếu chủ repo chưa yêu cầu.
