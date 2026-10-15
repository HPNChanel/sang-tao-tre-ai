# Khai báo dùng AI (điều kiện 6)

Solver mặc định là code. Model chỉ là fallback.

- **Model lúc chạy:** `qwen/qwen3.7-flash` qua OpenRouter.
- **Khi nào gọi:** chỉ khi biến môi trường `OPENROUTER_API_KEY` được đặt. Không có key thì không có lời gọi mạng.
- **Model làm gì:** chỉ phát một plan JSON. Đáp án do code tính (`src/executor.py`), không do model tính.
- **Prompt lúc chạy:** nằm trong `src/prompts/`.
- **Chưa chạy với model thật:** chưa có lần gọi API OpenRouter nào. Mọi test LLM dùng transport giả trong `tests/fake_llm.py` (không mạng, không key thật).

## Prompt KSP2 trong PR này

| Prompt | Mục đích |
|---|---|
| `src/prompts/plan_system.txt` | System prompt: chuyển câu hỏi thành đúng một JSON plan, không tính đáp án, không viết code, không giải thích. PR này thêm năm quy tắc: tên cột `Parent \| Child` và `Parent \| *`, cột `["*"]`, đường dẫn dòng `Sheet!path`, `args.ties` (`all` / `first`), `args.by` (kể cả `"age"`) và `args.return`. |
| `src/prompts/plan_user.txt` | User prompt: nhét schema, tóm tắt bảng và câu hỏi vào một mẫu. File này không đổi trong PR này. |

Schema trên đĩa `src/plan_schema.json` không bị sửa. Lúc gửi, `compact_schema` bỏ `description`, `$schema`, `$id` và `title` rồi nén JSON. Ràng buộc còn lại giữ nguyên. `reasoning` gửi đi với `{"enabled": false}`. Log chi phí tách `completion_tokens` và `reasoning_tokens` theo từng attempt.

## Bai hoc tu 6 nhom loi & synth v5 (KSP1)

**Placeholder.** Bài học từ 6 nhóm lỗi và synth v5 của KSP1 sẽ được gắn vào mục này trước khi merge. Mục này cố ý để trống trong commit này.
