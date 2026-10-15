# KSP1: bài học từ PR #3 và ghi chú dùng AI

## 1. Bài học từ 6 nhóm lỗi (PR #3 ksp1/fix-groups, đã đóng, không merge)

PR #3 làm từ main @ c02777d. Mỗi nhóm lỗi một commit, cộng 1 commit tài liệu và 1 commit test:

| # | Nhóm lỗi | Nội dung sửa |
|---|---|---|
| 1 | Phạm vi theo nhóm (section scope) | Phép gộp chỉ tính trong nhóm được nhắc tên. Con số nằm trong nhãn dòng không còn bị coi là ngưỡng lọc |
| 2 | Tiếng Việt (có dấu và không dấu) | Từ vựng thống kê, 'của nam/nữ', 'từ X đến Y tuổi', 'là bao nhiêu' là hỏi giá trị |
| 3 | Top N và kết quả hòa | Trả về danh sách k phần tử, giữ đủ các kết quả hòa, lấy cột trả về từ câu hướng dẫn |
| 4 | Chiều so sánh | 'từ A đến B' tính theo B - A, giữa hai sheet hoặc hai cột |
| 5 | Điều kiện lọc | between, phủ định, 'equal to', năm sinh trước/sau, loại trừ, 'Count ...' |
| 6 | Câu bị động và câu ngắn | Lọc theo giá trị chữ, 'hơn bao nhiêu', nhãn cột theo gốc từ chung |

### Vì sao PR #3 bị đóng
- Trên dev, synth v3 và synth v4, kết quả giữ nguyên hoặc tăng nhẹ. Synth v5 (bộ KSP1 tự viết) tăng rất mạnh, từ 45 lên 117/123.
- QA chấm v2 theo từng commit cộng dồn và thấy điểm v2 bắt đầu giảm từ commit section scope (nhóm 1). Không commit nào sau đó kéo v2 lên lại. EL đóng PR.
- Commit tiếng Việt có một luật định dạng quá sát với một kiểu câu cụ thể (không phải luật ngôn ngữ chung), nên bị bỏ hẳn và sẽ không đưa lại.

### Bài học
1. Đừng chấm bằng bộ do chính mình viết. Synth v5 và test do KSP1 viết theo cùng cách hiểu với code, nên điểm tăng nhưng không tổng quát hóa được. Từ nay câu hỏi test do figma bro viết, đáp án tính bằng code trên Public data 1.
2. Moi PR chi sua mot nhom. Gop 6 nhom vao mot PR khien khong biet nhom nao gay giam diem, va phai bo ca PR. Bay gio moi nhom la mot PR nho, PR nao lam giam v2 hoac holdout thi bo PR do.
3. Luật càng rộng càng dễ bắt nhầm. Luật section scope và các luật 'từ X đến Y' có thể khớp vào câu không liên quan. Cần test cả câu không được kích hoạt luật.
4. Không viết luật cho một câu hay một định dạng riêng. Luật nào chỉ có tác dụng với một kiểu câu duy nhất thì coi là học thuộc đề, và bỏ.
5. Không thêm trường schema nếu chưa dùng. Các trường format.thousands, args.delta, year>/year< đi theo PR #3, nên không có trên main.
6. Không dùng lại test đã bị đánh dấu trùng với bộ paraphrase của QA.

## 2. Synth v5

- Bộ dữ liệu tổng hợp do KSP1 tự viết, không phải của QA. Có 123 câu trên 3 biến thể dữ liệu, schema giống vòng 1: bảng người có header gộp 2 dòng, bảng thống kê có nhóm và dòng Total, nhiều sheet theo kỳ.
- Các họ câu hỏi: đồng nghĩa, đảo trật tự, bị động, tiếng Việt có dấu và không dấu, đơn vị/định dạng, phủ định, top N, hòa, khoảng, nhiều điều kiện, nhiều sheet, gộp ngầm.
- Script Python thuần (openpyxl, random với seed cố định nên chạy ra kết quả như nhau). Câu hỏi sinh từ template, không gọi LLM hay API nào. Đáp án được tính thẳng từ dữ liệu vừa sinh. Có cố ý tạo kết quả hòa.
- Hạn chế (bài học 1): template và code do cùng một người viết, nên synth v5 chỉ dùng để kiểm tra lỗi hồi quy, không dùng để đo khả năng tổng quát hóa.

## 3. Ghi chú dùng AI cho các PR nhỏ của KSP1

- Model luc chay: qwen/qwen3.7-flash qua OpenRouter, chi lam fallback khi co OPENROUTER_API_KEY. Model chi sinh plan JSON, dap an do code tinh.
- Prompt: src/prompts/plan_system.txt, src/prompts/plan_user.txt, src/plan_schema.json. Nếu PR nào đổi prompt thì ghi rõ trong mô tả PR đó.
- Chưa chạy với model thật: không có lần gọi API nào. Test LLM dùng mock hoặc fake LLM.
- Không huấn luyện. Mọi luật là luật ngôn ngữ chung, không gắn với file, id hay câu hỏi cụ thể.
- Test: câu hỏi do figma bro viết từ Public data 1. Câu nào code chung không qua được thì đánh xfail và ghi lý do. Câu xfail không tính là đạt.
- v2 dung lam cong chon: QA cham v2 va holdout cho tung PR, chi bao tong diem. PR lam giam diem thi bi bo. Vi vay v2 khong con la phep do sach.
- Phép đo sạch duy nhất là bộ v3 do QA niêm phong (hash ghi trong docs/ai-usage/). Bộ này chạy một lần trên SHA cuối sau khi dừng code.
- KSP1 khong mo qa/, v2 hay holdout.
- Lúc phát triển: code và test do agent KSP1 (Grok Bot) viết theo chỉ đạo của đội.
