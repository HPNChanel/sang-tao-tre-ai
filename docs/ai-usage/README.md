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

## Khai báo theo từng PR

Model trên mọi PR: `qwen/qwen3.7-flash` qua OpenRouter. Không PR nào đã chạy với model thật.

| PR | Trạng thái | Prompt | Ghi chú |
|---|---|---|---|
| #1 | merged, `e1ab186` | Không thêm prompt mới. Prompt có sẵn nằm trong `src/prompts/`. | Chưa chạy với model thật |
| #2 | KSP1, merged, `c02777d` | `src/prompts/plan_system.txt`, `src/prompts/plan_user.txt`, `src/plan_schema.json` | Synth v3 là Python thuần điền template, không gọi LLM. Đổi code thêm duy nhất: `os.environ.get` thành `os.getenv`. Chưa chạy với model thật |
| #3 | KSP1, fix-groups, CLOSED, không merge | Không sửa `src/prompts/` | Đóng vì điểm v2 giảm. Chưa chạy với model thật |
| #4 | KSP1, fg-vietnamese | Không sửa `src/prompts/` | Code do Cursor cloud agent viết theo chỉ đạo của KSP1. Model `qwen/qwen3.7-flash` qua OpenRouter chỉ dùng làm fallback cho `plan(strict=False)`. Chưa chạy với model thật |
| #5 | KSP2, PR này | Prompt chính và fake LLM ghi trong file này | Nén schema chỉ lúc gửi (`plan_schema.json` trên đĩa không đổi), parser vững, tắt reasoning, tách log chi phí, fake LLM và test. Model `qwen/qwen3.7-flash` qua OpenRouter. Chưa chạy với model thật |
| PR nhỏ của KSP1 | từng PR | Mỗi PR ghi prompt chính của chính nó. PM gom trước khi zip. | Chưa chạy với model thật |

PR #4 (fg-vietnamese) được miễn điều kiện 2 (số câu sai-mà-tự-tin trên v2 không giảm): lý do là không bộ nào tụt điểm và bộ test độc lập fv tăng từ 0/13 lên 11/13. Theo luật merge mới: không bộ nào tụt + có cải thiện đo được trên bộ test độc lập thì được merge.

## Bai hoc tu 6 nhom loi & synth v5 (KSP1)

Nội dung nằm ở [`ksp1-lessons.md`](ksp1-lessons.md).
