# KSP1: bai hoc tu PR #3 va ghi chu dung AI

## 1. Bai hoc tu 6 nhom loi (PR #3 ksp1/fix-groups, da dong, khong merge)

PR #3 lam tu main @ c02777d. Moi nhom loi mot commit, cong 1 commit tai lieu va 1 commit test:

| # | Nhom loi | Noi dung sua |
|---|---|---|
| 1 | Pham vi theo nhom (section scope) | Phep gop chi tinh trong nhom duoc nhac ten. Con so nam trong nhan dong khong con bi coi la nguong loc |
| 2 | Tieng Viet (co dau va khong dau) | Tu vung thong ke, 'cua nam/nu', 'tu X den Y tuoi', 'la bao nhieu' la hoi gia tri |
| 3 | Top N va ket qua hoa | Tra ve danh sach k phan tu, giu du cac ket qua hoa, lay cot tra ve tu cau huong dan |
| 4 | Chieu so sanh | 'tu A den B' tinh theo B - A, giua hai sheet hoac hai cot |
| 5 | Dieu kien loc | between, phu dinh, 'equal to', nam sinh truoc/sau, loai tru, 'Count ...' |
| 6 | Cau bi dong va cau ngan | Loc theo gia tri chu, 'hon bao nhieu', nhan cot theo goc tu chung |

### Vi sao PR #3 bi dong
- Tren dev, synth v3 va synth v4, ket qua giu nguyen hoac tang nhe. Synth v5 (bo KSP1 tu viet) tang rat manh, tu 45 len 117/123.
- QA cham v2 theo tung commit cong don va thay diem v2 bat dau giam tu commit section scope (nhom 1). Khong commit nao sau do keo v2 len lai. EL dong PR.
- Commit tieng Viet co mot luat dinh dang qua sat voi mot kieu cau cu the (khong phai luat ngon ngu chung), nen bi bo han va se khong dua lai.

### Bai hoc
1. Dung cham bang bo do chinh minh viet. Synth v5 va test do KSP1 viet theo cung cach hieu voi code, nen diem tang nhung khong tong quat hoa duoc. Tu nay cau hoi test do figma bro viet, dap an tinh bang code tren Public data 1.
2. Moi PR chi sua mot nhom. Gop 6 nhom vao mot PR khien khong biet nhom nao gay giam diem, va phai bo ca PR. Bay gio moi nhom la mot PR nho, PR nao lam giam v2 hoac holdout thi bo PR do.
3. Luat cang rong cang de bat nham. Luat section scope va cac luat 'tu X den Y' co the khop vao cau khong lien quan. Can test ca cau khong duoc kich hoat luat.
4. Khong viet luat cho mot cau hay mot dinh dang rieng. Luat nao chi co tac dung voi mot kieu cau duy nhat thi coi la hoc thuoc de, va bo.
5. Khong them truong schema neu chua dung. Cac truong format.thousands, args.delta, year>/year< di theo PR #3, nen khong co tren main.
6. Khong dung lai test da bi danh dau trung voi bo paraphrase cua QA.

## 2. Synth v5

- Bo du lieu tong hop do KSP1 tu viet, khong phai cua QA. Co 123 cau tren 3 bien the du lieu, schema giong vong 1: bang nguoi co header gop 2 dong, bang thong ke co nhom va dong Total, nhieu sheet theo ky.
- Cac ho cau hoi: dong nghia, dao trat tu, bi dong, tieng Viet co dau va khong dau, don vi/dinh dang, phu dinh, top N, hoa, khoang, nhieu dieu kien, nhieu sheet, gop ngam.
- Script Python thuan (openpyxl, random voi seed co dinh nen chay ra ket qua nhu nhau). Cau hoi sinh tu template, khong goi LLM hay API nao. Dap an duoc tinh thang tu du lieu vua sinh. Co co y tao ket qua hoa.
- Han che (bai hoc 1): template va code do cung mot nguoi viet, nen synth v5 chi dung de kiem tra loi hoi quy, khong dung de do kha nang tong quat hoa.

## 3. Ghi chu dung AI cho cac PR nho cua KSP1

- Model luc chay: qwen/qwen3.7-flash qua OpenRouter, chi lam fallback khi co OPENROUTER_API_KEY. Model chi sinh plan JSON, dap an do code tinh.
- Prompt: src/prompts/plan_system.txt, src/prompts/plan_user.txt, src/plan_schema.json. Neu PR nao doi prompt thi ghi ro trong mo ta PR do.
- Chua chay voi model that: khong co lan goi API nao. Test LLM dung mock hoac fake LLM.
- Khong huan luyen. Moi luat la luat ngon ngu chung, khong gan voi file, id hay cau hoi cu the.
- Test: cau hoi do figma bro viet tu Public data 1. Cau nao code chung khong qua duoc thi danh xfail va ghi ly do. Cau xfail khong tinh la dat.
- v2 dung lam cong chon: QA cham v2 va holdout cho tung PR, chi bao tong diem. PR lam giam diem thi bi bo. Vi vay v2 khong con la phep do sach.
- Phep do sach duy nhat la bo v3 do QA niem phong (hash ghi trong docs/ai-usage/). Bo nay chay mot lan tren SHA cuoi sau khi dung code.
- KSP1 khong mo qa/, v2 hay holdout.
- Luc phat trien: code va test do agent KSP1 (Grok Bot) viet theo chi dao cua doi.
