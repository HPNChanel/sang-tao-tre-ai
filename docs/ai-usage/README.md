Đã che id/đáp án bộ đo. Bản nguyên văn giữ ngoài repo, bàn giao riêng cho chủ dự án.

# Khai báo dùng AI

Solver mặc định là code. Model chỉ là fallback. Không huấn luyện.

## #16

Đã merge, nằm trên main `19eb2670b42131553b87ff38a9c75b1197e7f92e`.

Bảy prompt, Cursor cloud agent (model mặc định). Cả bảy đã chạy. Mục đích: guard để trống khi hình dạng câu trả lời sai hoặc thiếu cột xếp hạng.

## #18

Draft. Không vào bản nộp. QA FAIL tại `6f82333464824bd8c8170eb9480fc6faf529b703`.

- Prompt 1: loader bỏ dòng tiêu đề phía trên header thật. Đã chạy.
- Prompt 2: rebase. Đã chạy.
- Prompt 3: huỷ, không vào code.
- Prompt 4: đánh dấu bảng và lý do `blank:refined-header`. Đã chạy, không vào bản nộp.

## #17

KSP2, head `d4d83acd040285956de1d78e31ca849e165c1508`.

Tám prompt, cả tám đã chạy. Mục đích: lọc dòng theo điều kiện tiếng Việt.

Nguyên văn đã che của 8 prompt #17 sẽ bổ sung sau.

## PR adapter

KSP2, nhánh `ksp2/submission-adapter`.

Một prompt, đã chạy. Dockerfile ở root và sáu đối số cuộc thi. Không đổi logic trả lời. SHA: #20 merge `47c1e20`.

## #19

Bàn giao, KSP1. Prompt viết `docs/HANDOVER_CODEX.md` và các prompt cập nhật file đó. Đã chạy. Chỉ tài liệu.

## LLM lúc chạy

Chỉ `qwen/qwen3.7-flash` qua OpenRouter, chỉ làm fallback khi `plan(strict=False)`. Không training.

## Bộ v3 niêm phong

sha256 `45d652ccb2860bfdb7a81f5a2177621c299d97cd63ec53bd09622cd4306375d5`. 39 câu. Chưa mở.
