# Bàn giao cho Codex

Cây này là `main` tại `19eb2670b42131553b87ff38a9c75b1197e7f92e` (PR #16). Chỉ mô tả code đã có trên SHA đó. PR đang mở chưa nằm trong cây này.

## 1. Mục tiêu bài toán

Trả lời câu hỏi trên các bảng trong file xlsx. `run.py` đọc `queries.json`, ghi `submission.csv` với cột `id` và `answer` (đúng header và thứ tự id của `sample_submission.csv` nếu file đó có).

Mỗi câu được chấm một trong ba loại:

- **Đúng:** ô answer khác rỗng và khớp đáp án.
- **Sai tự tin:** ô answer khác rỗng nhưng sai.
- **Trống:** chuỗi rỗng.

Trống tốt hơn sai tự tin. Planner trả `None` khi confidence dưới `conventions.PLAN_CONFIDENCE_THRESHOLD` (`0.5`), hoặc khi guard trong `_blank_reasons` kéo confidence về `0`. `run.py` ghi `""` khi không có đáp án. Không đoán cho có chữ.

## 2. Kiến trúc

Luồng một câu, không có khóa LLM:

1. `run.py` `main` đọc `queries.json`, tách id trùng (`_dedupe_queries`), tìm workbook (`_resolve_workbook`: `xlsx_files/` rồi thư mục data; khớp tên không phân biệt hoa thường, rồi cùng stem với đuôi spreadsheet).
2. `TableCache` + `_preload` parse mỗi workbook một lần. `load_tables` (`src/xlsx_tables.py`) chạy trong process con khi có fork, giới hạn `--preload-timeout`.
3. `answer_one` → `_rule_answer` → `seams.code_plan` → `code_planner.plan`.
4. `code_planner.plan` gọi `planner.plan_with_confidence`. Dưới `0.5` thì in `[plan] low confidence` và trả `None`.
5. Có plan thì `seams.execute` → `plan_executor.execute` → `executor.answer` (gọi `executor.execute` rồi `answer_format`). `plan_executor` còn kiểm hình dạng sau khi chạy (`answer_type.answer_ok`, bật bằng `POST_EXEC_TYPE_CHECK`). Không khớp thì trả `None`.
6. Ô trống được ghi ra CSV. Log JSONL nằm cạnh file out (hoặc `--log`).

Có `OPENROUTER_API_KEY` và rule planner trả `None`: `answer_one` gọi `_answer_llm` → `_llm_then_rule`. Hàm đó gọi `llm_client.llm_plan` (tóm tắt bảng bằng `xlsx_tables.summarize`, schema đã nén). Plan phải qua `plan_validate.validate`. Hợp lệ thì `seams.execute`. Mọi lỗi (mạng, JSON, schema, executor, trần chi phí) đều rơi về `_rule_answer(..., strict=False)`, tức `code_planner.plan(..., strict=False)`. Không có khóa thì không gọi mạng; đường không khóa vốn đã là `strict=None`, và `conventions.strict_active` tắt evidence mode.

Model lúc chạy chỉ `qwen/qwen3.7-flash` qua OpenRouter. `llm_client.MODEL` đọc `OPENROUTER_MODEL`, mặc định đúng tên đó. Trần `COST_CAP_USD` mặc định `0.02`, kẹp tối đa `0.09`. Tối đa 3 attempt, `reasoning` tắt. Prompt: `src/prompts/plan_system.txt`, `src/prompts/plan_user.txt`. Schema trên đĩa không bị nén; `compact_schema` chỉ nén lúc gửi.

### Planner và guard (PR #16)

`planner._plan_with_confidence`: nếu `vi.is_vietnamese` thì `vi.translate` trước, rồi `_plan_core`. Hệ số tiếng Việt `0.9` khi ít nhất 2 keyword, không thì `0.6`. Cụm phép toán mới không phân giải được thì confidence `0` (`op:ambiguous` / `op:abstain`).

`_blank_reasons` chạy trước hai guard còn lại, mỗi plan một lần:

- `blank_guards.ranking_ready`: `rank` / `nth` / `topk_agg` đã có `args.by` và `args.order` là `asc` hoặc `desc`; `argmax` / `argmin` đã có cột đo. Không đòi N. Không đối chiếu lại tên cột. Guard không chạy.
- `blank_guards.single_superlative`: `argmax` / `argmin`, hoặc `nth` với `n <= 1`. Guard không chạy.
- Không thì `shape_mismatch` → lý do `blank:shape` (câu xin danh sách mà op là `sum`/`mean`/`count`/`max`/`min`/`topk_agg`, hoặc câu xin một số mà op là `rank`/`argmax`/`argmin`).
- `_nth_measure_missing`: `nth` từ hạng 2, câu có tín hiệu hạng (`nth_signal`), mà `args.by` trống → `blank:nth-column`.

Guard top-N và guard lookup không phạm vi dòng đã bỏ khỏi PR #16. Không có nhánh `by == "age"` trong guard. Chỗ đặt `args.by = "age"` cũ vẫn nằm ở planner (top-N oldest/youngest và `_order_args`) và `executor._order` đọc giá trị đó. Không thêm chỗ mới.

`_plan_core` chọn view, cột, dòng, rồi op trong schema: `lookup`, `values`, `sum`, `diff`, `mean`, `max`, `min`, `argmax`, `argmin`, `rank`, `nth`, `topk_agg`, `count`, `compare`, `ratio`, `share`.

### File và hàm chính

| File | Việc |
|---|---|
| `run.py` | `main`, `answer_one`, `_rule_answer`, `_llm_then_rule`, `_resolve_workbook`, `TableCache`, `_preload` |
| `src/xlsx_tables.py` | `load_tables`, `_row_kind`, `_split_tables`, `_build`, `summarize`; class `Table` |
| `src/seams.py` | `code_plan`, `execute`, `have_executor` |
| `src/code_planner.py` | `plan`, `plan_with_confidence` |
| `src/planner.py` | `plan_with_confidence`, `_plan_with_confidence`, `_blank_reasons`, `_plan_core`, `_nth_measure_missing` |
| `src/blank_guards.py` | `ranking_ready`, `single_superlative`, `shape_mismatch`, `nth_signal` |
| `src/plan_executor.py` | `execute` |
| `src/executor.py` | `execute`, `answer`, `_apply_where`, `_order` |
| `src/plan_schema.json` | schema plan v1 |
| `src/plan_validate.py` | `validate` |
| `src/prompts/plan_system.txt`, `plan_user.txt` | prompt lúc chạy |
| `src/llm_client.py` | `enabled`, `llm_plan`, `compact_schema`, `Budget` |
| `src/vi.py` | `fold`, `is_vietnamese`, `translate` |
| `src/conventions.py` | ngưỡng `0.5`, `strict_active`, `POST_EXEC_TYPE_CHECK` |
| `src/answer_type.py` | `expected_type`, `answer_ok` |
| `src/answer_format.py` | `parse_instruction`, `format_answer` |

`docs/plan_schema.md` giải thích op. Công thức không cache: `src/formula_eval.py`. Dòng tổng: `src/subtotals.py`. Khớp chữ: `src/textmatch.py`.

## 3. Cách chạy local

Cài: `python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt` (`pandas`, `openpyxl`, `pytest`).

Không khóa, solver chỉ chạy code:

```
python3 run.py --data "<thư mục có queries.json>" --out "<thư mục>/submission.csv"
```

Đối số hiện có trong `run.py`:

| Đối số | Ý nghĩa |
|---|---|
| `--data` | bắt buộc; thư mục có `queries.json` |
| `--out` | bắt buộc; đường dẫn `submission.csv` |
| `--log` | JSONL; mặc định `<thư mục out>/run_log.jsonl` |
| `--query-timeout` | giây cho một process con; mặc định `20` hoặc `DS2_QUERY_TIMEOUT`; `0` tắt |
| `--preload-timeout` | giây parse một workbook vào cache; mặc định `120` hoặc `DS2_PRELOAD_TIMEOUT` |
| `--cache-rss-mb` | ngân sách RSS / frame, MiB; mặc định `640` hoặc `DS2_CACHE_RSS_MB` |
| `--no-cache` | không giữ bảng đã parse |
| `--in-process` | không fork (`DS2_IN_PROCESS=1` hoặc `DS2_START_METHOD` khác `fork` cũng vậy) |
| `--flush-every` | ghi lại CSV mỗi N câu; mặc định `1` |

Log tiến độ, timeout, cảnh báo LLM/executor ghi stderr. Khóa tùy chọn, không ghi vào source: `OPENROUTER_API_KEY`. Có thể đặt `COST_CAP_USD`, `QWEN_PRICE_IN_PER_M`, `QWEN_PRICE_OUT_PER_M`.

#19 trên GitHub là PR bàn giao này (`ksp1/handover-codex`), chỉ tài liệu.

PR adapter (KSP2, nhánh `ksp2/submission-adapter`) thêm Dockerfile và sáu đối số cuộc thi `--xlsx-dir --queries --output --openrouter-key --openrouter-model --openrouter-base-url`, log stderr, và giữ `--data` / `--out`. Image: `docker build --platform=linux/amd64`, rồi `docker run` với đúng sáu đối số đó. Bản nộp tối nay = main `47c1e20`. PR #18 không vào bản nộp.

Gói thử dataset 2, không gọi LLM: `scripts/dry_run_dataset2.sh INPUT OUTPUT_DIR`. Zip chỉ chứa `submission.csv` ở gốc; `scripts/check_zip.sh` kiểm tra.

## 4. Cách test

```
python3 -m pytest -q
```

Trên cây `19eb267`, khi không có `data/Public data 1`: **146 passed, 24 skipped**. 24 skip là 10 ca `tests/test_fg_topn.py`, 13 ca `tests/test_fg_vietnamese.py`, và `test_cli_end_to_end` trong `tests/test_run.py`. Chúng chạy khi có `data/Public data 1` hoặc `AIPROJ_DATA_DIR`.

`score.py` không có trong repo, không có trên các nhánh origin. Không bịa lệnh. Khi QA có bộ đo và file đáp án, phân loại từng câu là đúng / sai tự tin / trống như mục 1. Bộ nào làm tăng số câu sai tự tin, hoặc làm mất câu vốn đúng, thì không đạt.

Trong repo chỉ có pytest. `data/Public data 1` không được commit. Nhật ký cũ `prompts/dev/ksp1-2026-10-15.md` ghi DEV 14/14 và synth v2 80/80 trên các SHA trước #16; đó không phải điểm của `19eb267`. Bộ v3 niêm (`docs/ai-usage/v3-seal.md`): 39 câu, sha256 `45d652ccb2860bfdb7a81f5a2177621c299d97cd63ec53bd09622cd4306375d5`, chạy một lần trên SHA cuối. Không mở `qa/`, v2, v3, holdout, variant-check-1610.

## 5. Các cổng chấm

Mỗi PR:

- Không có câu mới thành sai tự tin.
- Không mất câu vốn đúng.
- Câu trong test mới phải giống câu của mọi bộ đo dưới `0.85` (PR #17 dùng `SequenceMatcher`; cây này không có script quét sẵn).
- Mô tả PR có mục `Khai báo dùng AI (AI usage)`.
- Mô tả PR ghi đúng số pytest vừa chạy trên đúng SHA.
- Một PR, một nguyên nhân.
- QA PASS đúng SHA được merge, không PASS một SHA khác.

## 6. PR đã merge và đang mở

- **#16 đã merge**, SHA `19eb2670b42131553b87ff38a9c75b1197e7f92e`, nhánh `ksp1/blank-guards`. Guard còn lại: hình dạng đáp án (`blank:shape`) và cột xếp hạng khi nth từ hạng 2 mà thiếu `by` (`blank:nth-column`). Plan đã đủ cột đo và hướng thì guard không chạy. Guard top-N và guard lookup không phạm vi dòng chuyển backlog. Pytest trên SHA này: 146 passed, 24 skipped.
- **#18 draft, không vào bản nộp.** Nhánh `ksp1/title-row-header`, head `6f82333464824bd8c8170eb9480fc6faf529b703`. Chi tiết QA ở mục sau.
- **#17 đang mở**, KSP2, nhánh `ksp2/vi-filter-fix`, head `d4d83acd040285956de1d78e31ca849e165c1508`. Lọc dòng tiếng Việt: nhận ra điều kiện mà không gắn được đúng một cột và một giá trị thì để trống. Mô tả PR ghi nhánh vẫn dựa trên `cf37c01`, chưa rebase lên `19eb267`.
- **#19** là PR bàn giao này (`ksp1/handover-codex`), chỉ tài liệu. Không nằm trong bản nộp.
- **PR adapter** (KSP2, nhánh `ksp2/submission-adapter`) là #20 merge `47c1e20`. Dockerfile và sáu đối số cuộc thi. Bản nộp tối nay = main `47c1e20`.

## PR #18 (không vào bản nộp)

Nhánh `ksp1/title-row-header`, head `6f82333464824bd8c8170eb9480fc6faf529b703`, draft. Không merge, không đưa vào bản nộp tối nay.

Loader bỏ dòng tiêu đề và banner năm phía trên header thật.

Trên head này, `_blank_reasons` trả `blank:refined-header` trước các guard khác khi `View.title_skipped` đúng (loader gán `Table.title_skipped` nếu đã bỏ caption hoặc nâng banner năm) và plan là một trong các dạng: đếm; lọc rồi tính (`where`, `range`, `scope`, hoặc `leaf_only` cùng `sum`/`mean`/`max`/`min`, hoặc `topk_agg`); cặp giá trị (`values` đúng hai ô); hiệu số năm. Không có `title_skipped` thì guard không chạy.

QA chấm một bộ đo kín. Số liệu là đúng / sai tự tin / trống:

| Cây | Đúng | Sai tự tin | Trống |
|---|---:|---:|---:|
| main `19eb267` | 6 | 29 | 45 |
| head `6f82333` | 12 | 36 | 32 |

Tám câu trống thành sai tự tin. Sáu câu trống thành đúng. Không mất câu đúng.

Sai tự tin theo dạng: đếm 4 → 8, cặp giá trị 1 → 4, lọc rồi tính 6 → 7, hiệu số năm không đổi.

`blank:refined-header` không chặn được các dạng nó nhắm tới. Việc cần tìm: vì sao planner không đi qua `_blank_reasons`, hoặc bảng không mang dấu `title_skipped`.

Việc tiếp cho Codex: sửa, rồi để QA chấm lại đủ 11 bộ.

## 7. Việc còn dở

- Guard top-N và guard lookup không phạm vi dòng: backlog sau #16. Chưa có trong `blank_guards.py`.
- Cách nói hạng tăng dần trong tiếng Việt. `vi.translate` đã map `từ thấp đến cao` và `tăng dần` thành `ascending`, `từ cao đến thấp` và `giảm dần` thành `descending`. Planner nhận chữ `ascending` thành `args.order = "asc"` ở nhánh rank. `từ nhỏ đến lớn` không trúng keyword (0 hit, chỉ bỏ dấu). `tăng` đứng riêng thành `increase`, không thành chiều xếp tăng.
- Ba lỗ loader trên `19eb267`, kiểm bằng bảng tạm, không phải bằng bộ đo:
  - Bảng một cột: `_row_kind` coi hàng không có ô bên phải cột nhãn là section hoặc note, không có hàng data, `load_tables` trả về rỗng.
  - Hàng cuối chỉ có nhãn: không vào dataframe và không vào `notes`.
  - Bảng toàn chữ: hàng không có số bị coi là header, không có hàng data, không ra bảng.
- PR #18: `blank:refined-header` chưa chặn đếm, cặp giá trị, lọc rồi tính, hiệu số năm trên bộ đo kín (mục PR #18). Sửa xong thì QA chấm lại đủ 11 bộ. Không sửa bằng luật gắn file, id, câu, hoặc tên cột. PR này không vào bản nộp.
Với 6 tham số BTC, CSV ghi theo thứ tự `queries.json`, không theo file mẫu; Codex xác nhận lại với luật BTC trước 12:00 17/10.

## 8. Luật repo

Repo công khai vì ban tổ chức clone. Không commit khóa, `qa/`, dữ liệu holdout, bộ v3 đã niêm, hay đường dẫn máy như `/workspace` hoặc `/home/box`. Trước khi push, quét `git log -p` của commit sắp đẩy.

Không huấn luyện. Không luật gắn tên file, id câu, nội dung một câu, hoặc tên cột. Không thêm chỗ dùng `by == "age"`. Model lúc chạy chỉ `qwen/qwen3.7-flash` qua OpenRouter, chỉ làm fallback `plan(strict=False)`.
