## #17: nguyên văn 8 prompt (đã che)

Đã che id/đáp án bộ đo: id câu thay bằng `[id bộ đo]`, đáp án thay bằng `[đáp án]`, câu hỏi lấy từ bộ đo hoặc câu ví dụ thay bằng `[câu ví dụ]`. Phần còn lại giữ nguyên văn.

### Prompt 1
Bối cảnh: trên main 0022be1, câu tiếng Việt dạng chọn tập con (ví dụ: [câu ví dụ]) bị BỎ QUA BỘ LỌC mà vẫn trả lời CHẮC CHẮN: ra [đáp án] (cộng cả cột) thay vì [đáp án]. Đây là lỗi sai tự tin, phải sửa.

Hai yêu cầu của EL (quy tắc chung, KHÔNG gắn với câu cụ thể):
1. Nhận diện được điều kiện lọc trong câu tiếng Việt (loại "chỉ xét…", "những người… ở…", "thuộc… mà…", "làm ca…", ngưỡng "chưa tới…", "đạt đúng…").
2. Nếu ĐÃ nhận ra câu có điều kiện lọc nhưng KHÔNG ÁP ĐƯỢC bộ lọc (không xác định được cột/giá trị lọc một cách duy nhất), câu đó phải ĐỂ TRỐNG (độ tin cậy 0), tuyệt đối không cộng cả cột rồi trả lời.

Ràng buộc bắt buộc:
- KHÔNG được sửa code chỉ để khớp đúng [câu ví dụ]. Fix phải là quy tắc chung, dùng được cho mọi câu lọc tiếng Việt viết khác khuôn. QA sẽ đo độc lập bằng một bộ đo kín.
- KHÔNG NỚI LỎNG score.py dưới mọi hình thức.
- KHÔNG được làm bất kỳ bộ đo nào tụt điểm. Trước hết giảm sai tự tin; khi không chắc thì để trống; KHÔNG được làm thêm câu nào chuyển thành sai tự tin.
- Dòng thêm mới không chứa đường dẫn nội bộ, giá trị API key, thư mục QA, holdout, hay câu từ bộ ẩn.
- Test mới phải tự viết trên bảng tự dựng, không chép câu từ bộ đo.
- Mô tả PR có phần khai báo dùng AI.

Quy trình: điều tra planner/executor để hiểu vì sao bộ lọc tiếng Việt bị bỏ qua (plan không tạo filter node, hay execute bỏ filter?). Dựng bảng test tự dựng có cột ca/thành phố/điểm, tự xác nhận: câu lọc nhận ra đúng điều kiện thì ra đúng tổng nhóm; câu lọc mơ hồ thì để trống. python3 -m pytest -q phải xanh.

Sau khi xong: mở PR lên main, branch ksp2/vi-filter-fix, báo head SHA, kết quả pytest, tóm tắt nguyên nhân gốc + cách sửa (quy tắc chung), và xác nhận git log -p origin/main..HEAD sạch. Lưu ý phải để QA chấm, KHÔNG tự merge.

### Prompt 2
Cập nhật: `main` đã tiến tới `cf37c01` (PR #13 và PR #15 đã merge). Trước khi kết thúc, rebase nhánh `ksp2/vi-filter-fix` lên `cf37c01` (không phải `0022be1` nữa), giải quyết xung đột nếu có, chạy lại pytest cho sạch, rồi báo lại head SHA cuối cùng. Giữ nguyên nguyên tắc: nhận diện điều kiện lọc trong câu tiếng Việt; nếu nhận ra điều kiện mà không áp được (không xác định duy nhất cột/giá trị) thì để câu đó TRỐNG (confidence 0), tuyệt đối không cộng cả cột. Dùng luật chung, KHÔNG bám theo câu cụ thể hay id/tên cột cụ thể. Không đưa đường dẫn nội bộ, key, hay câu từ bộ ẩn vào repo.

### Prompt 3
QA đã gửi dạng lỗi cụ thể (chỉ mô tả dạng lỗi, không có đề/đáp án). Cả nhóm đều do planner trả lời confidence 0.9 dù lọc không được áp. Phạm vi của mình cần xử trong PR này (luật chung, KHÔNG bám id câu/tên cột):

1) Lọc tập con rồi tính (S1): giá trị đứng sau "thuộc" hoặc trùng giá trị của một cột phân loại PHẢI thành bộ lọc trước khi cộng/tính cột mục tiêu. Nếu nhận ra điều kiện mà không xác định duy nhất được cột/giá trị thì để TRỐNG, không cộng cả cột. Lưu ý ca giá trị nhóm trùng một phần với nhãn một dòng thực thể: không được chọn liền dòng đó.
2) Khoảng cột + điều kiện năm (S2): khi đề giới hạn một khoảng cột (ví dụ các tháng) và một năm, phải lấy đủ các cột trong khoảng (không chỉ hai đầu) và áp điều kiện năm; nếu không áp được thì để trống.
3) Đếm với ngưỡng viết bằng chữ tiếng Việt (hàng trăm) phải dịch đúng thành số, và điều kiện nhóm không được bỏ (hiện bị chuyển thành tra một ô).
4) Bộ chuyển số viết bằng chữ trong vi.py đang dịch nhầm từ giới tính "nam" thành số "five" — phải phân biệt "nam" (giới tính) với "năm"/số đếm, không ép thành số.
5) Câu top N của KSP1: phần điều kiện nhóm là của mình (KSP1 lo số phần tử). Đừng đụng phần đếm N của KSP1.

Lưu ý: 3 câu S1 còn lại là lỗi đọc bảng (header bị đọc thành dữ liệu) ở bước đọc bảng, KHÔNG thuộc phạm vi PR này — đừng sửa bước đọc bảng. Tất cả phải kiểm cả trên bảng tiêu đề nhiều dòng (giống dữ liệu thật), không chỉ bảng phẳng.

### Prompt 4
Nhắc: trong mô tả PR phải có mục "Khai báo dùng AI (AI usage)" và liệt kê các prompt chính giao cho agent — các PR trước từng FAIL vì thiếu mục này. Đảm bảo PR bao gồm mục này trước khi báo xong.

### Prompt 5
QA chấm PR #17 (eef8bed): FAIL. Cần sửa các điểm sau rồi báo head SHA mới (chỉ dạng lỗi, không có đề):

1) HỒI QUY nghiêm trọng — 7 câu đếm bị chuyển từ TRỐNG sang SAI TỰ TIN trên bộ kiểm:
   - 3 câu bảng nhân sự: ngưỡng kiểu "lớn hơn" + số viết bằng chữ KHÔNG áp được, nhưng bộ lọc ca lại VẪN áp → đếm sai mà vẫn tự tin. Không được áp một phần điều kiện rồi trả lời.
   - 4 câu bảng tỉnh: "vượt N nghìn" + một năm bị biến thành trả MỘT giá trị thay vì ĐẾM.
   - Gốc: bộ đọc ngưỡng hiện chỉ nhận "chưa / đúng bằng", CHƯA nhận "lớn hơn / vượt / trên / ít nhất" (và các biến thể so sánh >, >=). Thêm luật chung: (a) nhận diện đủ các toán tử so sánh trong câu tiếng Việt; (b) nếu có điều kiện so sánh mà KHÔNG áp được đầy đủ thì để TRỐNG (confidence 0), tuyệt đối không áp một phần rồi trả lời; (c) câu hỏi "bao nhiêu" thì phép phải là ĐẾM, không phải tra một ô.

2) HỒI QUY: hai bộ đo mỗi bộ tụt 1 câu, đều từ ĐÚNG sang TRỐNG — bộ lọc mới chặn nhầm câu đang đúng. Thu hẹp điều kiện chặn để không bắt nhầm câu đúng; chạy lại hai bộ đó, tìm và sửa câu bị chặn nhầm.

3) Trên bộ kiểm, CHƯA dạng nào trong S1, S2, đếm ngưỡng viết bằng chữ, nam→five, P4 thực sự được sửa/chuyển sang trống. Nghĩa là luật nhận diện chưa khớp cách diễn đạt thật. Rà lại để các luật này THỰC SỰ kích hoạt trên nhiều cách diễn đạt, không chỉ khuôn hẹp.

4) Mở rộng test tự dựng: hiện test trong tests/test_vi_row_filter.py chỉ theo schema bảng phẳng. Thêm bảng tự dựng có: cột phân loại nhóm, cột năm, cột tháng (để test khoảng cột + năm), và bảng có dòng nam/nữ (để test nam→five và gộp nhóm trước xếp hạng). Giữ cả bảng tiêu đề nhiều dòng.

5) LEAK: 1 câu test mới giống 0.89 với một câu của bộ đo. Đổi cách diễn đạt câu đếm đó trong test cho khác đi (<0.85).

Giữ nguyên: [id bộ đo] và [id bộ đo] vẫn đúng, các bộ khác không tụt. Vẫn: luật chung không bám id/tên cột, không đụng bước đọc bảng hay score.py, không đưa đường dẫn nội bộ, key, hay câu bộ ẩn vào repo. Giữ mục AI usage + các prompt chính trong mô tả PR. Chạy lại pytest cho sạch, rebase lên main cf37c01 (hoặc main mới nhất nếu đã đổi), rồi báo head SHA cuối cùng.

### Prompt 6
Bản a12bb92 chưa đạt QA, cần sửa thêm 3 điểm rồi re-run eval. KSP2 (mình) đã tự chạy eval không key, base cf37c01 vs head a12bb92:

1) Câu đếm có điều kiện vẫn áp dụng nửa vời. Cụ thể câu `[id bộ đo]` ([câu ví dụ]) chuyển từ TRỐNG sang SAI TỰ TIN: plan trả lời sai, đáp án đúng là [đáp án]. Nguyên nhân: chỉ một trong hai điều kiện được gắn (ngưỡng số viết bằng chữ trên cột tuổi, HOẶC bộ lọc ca), không gắn đủ cả hai. Luật phải là: gắn ĐỦ cả ngưỡng so sánh lẫn bộ lọc nhóm/ca rồi đếm số dòng; nếu còn sót bất kỳ điều kiện nào không gắn được vào plan thì ĐỂ TRỐNG (confidence 0), tuyệt đối không trả lời khi mới áp một phần. Rà lại toàn bộ nhánh câu đếm có so sánh để không còn ca nào áp nửa vời.

2) Làm mất 4 câu đang ĐÚNG trên một bộ đo (đúng -> trống): `[id bộ đo]`, `[id bộ đo]`, `[id bộ đo]`, `[id bộ đo]`. Có guard chặn quá tay. Thu hẹp đúng chỗ để 4 câu này đúng trở lại, trong khi vẫn giữ các sửa ở điểm 1. Trước khi coi là xong phải tự xác nhận: 0 câu đúng-hoặc-trống chuyển sang sai tự tin, và 0 câu đúng bị mất, trên cả hai bộ đo.

3) Dòng test còn giống 0.899 với câu `[id bộ đo]` ([câu ví dụ]). Ngưỡng 0.85 là luật cứng. Viết lại dòng test này (đổi bối cảnh, con số, cách diễn đạt) để độ giống với MỌI câu trong mọi bộ đo < 0.85. Kiểm lại sau khi viết.

Giữ nguyên: chỉ chạm src/planner.py, src/vi.py, src/executor.py, tests/test_vi_row_filter.py; KHÔNG đụng score.py, loader hay bước đọc bảng; không thêm thư viện; giữ mục "Khai báo dùng AI (AI usage)" và danh sách prompt trong mô tả PR; [id bộ đo] = [đáp án] phải vẫn đúng. Không để lộ đường dẫn nội bộ, key, hay câu của bộ kín trong repo. Sau khi sửa, chạy lại pytest và báo số, rồi gửi head SHA mới. ĐỪNG merge, đừng rebase (mình sẽ rebase lên main đã merge sau).

### Prompt 7
ac9f7bf vẫn chưa đạt, thực ra số sai tự tin tăng (từ 1 lên 3). KSP2 tự chạy eval không key, base cf37c01 vs head ac9f7bf. Cần sửa đúng 3 điểm sau, KHÔNG làm xóa đi các sửa đã tốt (similarity đã PASS 0.832, [id bộ đo] đúng, [id bộ đo] đã đúng lại):

1) **Câu lọc ra rỗng hoặc điều kiện chưa gắn đủ phải để TRỐNG, không được trả về 0.** Hai câu mới `[id bộ đo]` và `[id bộ đo]` đang trả lời `0` (blank → sai tự tin). Đây là dấu hiệu bộ lọc loại hết các dòng (hoặc gắn điều kiện sai) nhưng vẫn trả kết quả đếm = 0. Luật chung: khi áp bộ lọc mà tập kết quả rỗng VÀ không chắc mọi điều kiện được nhận dạng/gắn đúng (cột, giá trị, ngưỡng) thì confidence 0 (để trống), tuyệt đối không trả về đếm 0 như một đáp án tự tin.

2) **`[id bộ đo]` vẫn trả lời sai, đáp án đúng là [đáp án].** Câu: [câu ví dụ]. Phải gắn ĐỦ cả hai điều kiện (ngưỡng số viết bằng chữ so sánh lớn hơn trên cột tuổi, VÀ bộ lọc ca) rồi đếm dòng. Nếu chỉ gắn được một điều kiện thì để trống, không trả lời theo một điều kiện.

3) **Lấy lại 3 câu đang ĐÚNG bị mất trên một bộ đo:** `[id bộ đo]`, `[id bộ đo]`, `[id bộ đo]` (hiện đúng → trống). Guard nào đang chặn 3 câu này thì thu hẹp đúng chỗ, nhưng đừng nới đến mức để lọt lại các câu ở điểm 1/2.

Mục tiêu tự xác nhận trước khi báo xong (dù không có bộ đó trong môi trường, hãy viết test tự dựng mô phỏng đúng các ca này để kiểm logic): 0 câu đúng-hoặc-trống → sai tự tin, 0 câu đúng bị mất. Giữ nguyên phạm vi 4 file, không đụng score.py/loader/đọc bảng, không thêm thư viện, giữ AI usage, [id bộ đo] đúng, similarity < 0.85. Chạy pytest, báo số, gửi head SHA mới. ĐỪNG merge/rebase.

### Prompt 8
1c720177 gần đạt nhưng còn đúng 2 chỗ, KSP2 tự chạy eval (base cf37c01):

Còn sai tự tin: `[id bộ đo]` (trả sai, gold [đáp án]) — đã 5 lần chưa dứt. Còn mất đúng: `[id bộ đo]` (gold [đáp án]) và `[id bộ đo]` (gold [đáp án]) trên một bộ đo.

1) `[id bộ đo]` — hãy TRACE thật sâu, đừng đoán. Câu: [câu ví dụ]. Nó trả sai nghĩa là bộ lọc đang chỉ gắn ĐƯỢC MỘT trong hai điều kiện, rồi đếm. In ra plan sinh cho câu này: liệt kê từng điều kiện được nhận dạng, cột gắn vào, và toán tử. Xác định chính xác điều kiện nào BỊ RƠI: ngưỡng số viết bằng chữ so sánh lớn hơn trên cột tuổi, hay bộ lọc ca. Sửa đúng chỗ đó để cả hai cùng gắn (AND) rồi đếm. Nếu chỉ gắn được một thì để TRỐNG, tuyệt đối không trả lời.

2) `[id bộ đo]` và `[id bộ đo]` — đây KHÔNG phải câu đếm. Một câu Yes/No và một câu liệt kê hai giá trị. Guard chống over-block của bạn đang chặn luôn hai dạng này. Hãy thu hẹp guard để nó CHỈ áp cho loại câu đếm/lọc mà điều kiện không gắn đủ, không đụng đến câu Yes/No và câu liệt kê nhiều giá trị. Sau sửa, hai câu này phải đúng trở lại; đừng làm `[id bộ đo]`/`[id bộ đo]` quay về confident 0 hay làm `[id bộ đo]`/`[id bộ đo]` mất lại.

Giữ nguyên phạm vi 4 file, không đụng score.py/loader/đọc bảng, không thêm thư viện, giữ AI usage, [id bộ đo] đúng, similarity < 0.85. Thêm test mô phỏng cả hai ca này bằng bảng giả cùng hình dạng. Chạy pytest, ghi số vào body, gửi head SHA mới. ĐỪNG merge/rebase.
