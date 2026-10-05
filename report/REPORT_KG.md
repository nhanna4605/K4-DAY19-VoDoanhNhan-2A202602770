# Báo cáo Day 19 — Flat RAG vs GraphRAG

**Họ tên:** Võ Doanh Nhân  **MSSV:** 2A202602770  **Ngày:** 05/10/2026

> Kỳ vọng và thang điểm: `SUBMISSION.md`. Mọi số liệu dưới đây khớp với `ket_qua_benchmark_kg.txt` (ontology v2 tự thiết kế, chạy `python bench_kg.py --judge`). Bản so sánh với ontology gợi ý nằm ở `ket_qua_benchmark_kg.hint.txt` (chạy `KG_ONTOLOGY=hint python bench_kg.py --judge --out ket_qua_benchmark_kg.hint.txt`, cùng code cuối cùng). Bản thiết kế ontology nộp riêng ở `report/ONTOLOGY.md`. Truy vấn và kết quả nguyên văn dùng làm bằng chứng lưu ở `report/evidence/`.

**Lưu ý về môi trường đo.** Chat/embedding gọi qua một cổng API trung gian tương thích OpenAI (`gpt-4o-mini`, `text-embedding-3-small`). Cột USD do `src/llm.py` tính từ số token nhân bảng giá công bố, không phải hóa đơn thật. Cột giây bị ảnh hưởng mạnh bởi độ trễ của cổng: cùng một bước embed 176 chunk của Flat RAG mất 656,8 s ở lần chạy này nhưng 1 506,7 s ở lần chạy gợi ý, nên **không nên đọc cột giây như một khác biệt giữa hai pipeline**. Kết quả có nhiễu giữa các lần chạy vì bước trích xuất bằng LLM không tất định (xem E5).

## 1. Chi phí (10 điểm)

Hai bảng dán nguyên từ `ket_qua_benchmark_kg.txt`:

```
Chat model: openai:gpt-4o-mini | Embedding: openai:text-embedding-3-small | top_k=3 | chunk_size=800 | chunks=176 | KG: 201 nodes / 604 rels

== Indexing (one-off)
pipeline  calls    in_tok  out_tok       USD  seconds
flat        176     56072        0   0.00112    656.8
graph       196     91958     4682   0.00931    760.9

== Querying (mean per question)
pipeline  recall  judge   in_tok  out_tok       USD  seconds
flat        0.43   1.00      694       48   0.00013     4.42
graph       0.89   1.67     4282      109   0.00070     4.45
```

| Chỉ số | Flat | Graph | Graph / Flat |
| --- | --- | --- | --- |
| Indexing USD | 0,00112 | 0,00931 | ×8,31 |
| Indexing giây | 656,8 | 760,9 | ×1,16 |
| Mỗi câu: USD | 0,00013 | 0,00070 | ×5,38 |
| Mỗi câu: giây | 4,42 | 4,45 | ×1,01 |
| Mỗi câu: in_tok | 694 | 4 282 | ×6,17 |

**Chi phí tăng thêm đến từ đâu?**
- *Dựng chỉ mục:* cả hai pipeline đều embed 176 chunk (56 072 token). GraphRAG thêm đúng **20 lần gọi LLM** (mỗi bài báo một lần để trích JSON), tức +35 886 token vào và +4 682 token ra, +$0,00819. Phần luật dựng bằng regex nên **0 lần gọi LLM**.
- *Mỗi câu hỏi:* GraphRAG gửi cùng 3 chunk như Flat RAG **cộng** các dữ kiện từ graph (với Q4, Q5 khoảng 13 000–14 600 ký tự), nên token vào gấp 6,17 lần và token ra gấp 2,27 lần (câu trả lời dài hơn).
- *Ước tính hòa vốn:* chi phí thêm cho n câu là `0,00819 + n × 0,00057` USD (0,00057 = 0,00070 − 0,00013). Với 6 câu thêm khoảng $0,0116; với 100 câu, bình quân còn thêm khoảng $0,00065/câu vì chi phí dựng chia đều. Con số tuyệt đối rất nhỏ; điều cần cân nhắc là độ phức tạp vận hành (Neo4j, ontology, trích xuất) chứ không phải tiền.

## 2. Từng câu hỏi (10 điểm)

| Câu | Loại | Flat recall / judge | Graph recall / judge | Thắng | Vì sao (1 câu) |
| --- | --- | --- | --- | --- | --- |
| Q1 | single-hop-law | 1,00 / 2 | 1,00 / 2 | Hòa | Định nghĩa "tiền chất" nằm gọn trong một chunk luật nên vector search đủ |
| Q2 | single-hop-news | 1,00 / 2 | 1,00 / 2 | Hòa | Hai bị cáo tử hình nằm trong một bài báo, một chunk là đủ |
| Q3 | cross-kb | 0,00 / 0 | 1,00 / 2 | **Graph** | Tên người chỉ có trong tin, khung phạt chỉ có trong luật; Flat trả "Không đủ thông tin", Graph đi người → vụ → tội → Điều 251 |
| Q4 | cross-kb | 0,00 / 0 | 0,67 / 1 | **Graph** (chưa đúng) | Graph nối được Hoàng Nato tới tội tổ chức sử dụng chất (Điều 255) nhưng vẫn trả "tối đa 7 năm" thay vì chung thân (lỗi E2, mục 3) |
| Q5 | cross-kb-multi-hop | 0,60 / 1 | 1,00 / 2 | **Graph** | Graph chọn đúng Điều 250 khoản 4 theo khối lượng MDMA (9 600 g), Flat gọi nhầm "khoản b)" |
| Q6 | aggregation | 0,00 / 1 | 0,67 / 1 | Graph (recall), hòa (judge) | Cả hai đều chỉ nêu được một phần các vụ; recall và judge mâu thuẫn (E4) và Graph bỏ sót một vụ có trong graph (E5) |

**Quy luật:** câu chỉ cần **một nguồn** (Q1, Q2) thì Flat RAG ngang Graph và rẻ hơn; câu cần **ghép dữ kiện của hai KB** (Q3–Q5) thì Graph thắng rõ vì không một chunk nào chứa cả tên người lẫn khung phạt; câu **gom nhóm** (Q6) phụ thuộc chất lượng trích xuất, Graph chỉ hơn một phần.

### So với ontology gợi ý (cùng code, cùng dữ liệu)

| Chỉ số | Gợi ý (`ket_qua_benchmark_kg.hint.txt`) | v2 (`ket_qua_benchmark_kg.txt`) |
| --- | --- | --- |
| Graph: recall / judge | 0,94 / 1,67 | 0,89 / 1,67 |
| Graph: in_tok mỗi câu | 3 883 | 4 282 |
| Graph: USD mỗi câu | 0,00062 | 0,00070 |
| Flat: recall / judge | 0,43 / 1,00 | 0,43 / 1,00 |

**Nhận xét trung thực:** trên điểm benchmark tổng hợp, ontology v2 **không** tốt hơn ontology gợi ý: judge bằng nhau, recall thấp hơn 0,05 (chỉ do Q6: 1,00 → 0,67, xem E5) và token tăng khoảng 10%. Chênh lệch này nằm trong mức nhiễu của trích xuất LLM không tất định, nên tôi **không** kết luận ontology nào tốt hơn về điểm số. Lợi ích của v2 nằm ở **chất lượng dữ liệu graph** (chi tiết ở `ONTOLOGY.md` mục 7 và lỗi E3 bên dưới), không ở điểm benchmark.

## 3. Phân tích lỗi (20 điểm)

### Lỗi E3: Trùng thực thể (`Substance`)

- **Hiện tượng:** ontology gợi ý tạo 16 node `Substance`, trong đó cùng một chất bị tách thành nhiều node: `Ketamine`/`ketamine`, `Methamphetamine`/`methamphetamine`, `MDMA`/`thuốc lắc`, và hai tên chung chung `ma túy`/`chất ma túy`.
- **Bằng chứng:** (graph dựng bằng `KG_ONTOLOGY=hint`, file `report/evidence/evidence_hint.txt`)

```cypher
MATCH (s:Substance) RETURN count(s) AS substances;
MATCH (s:Substance) WITH toLower(s.name) AS k, collect(s.name) AS names WHERE size(names) > 1 RETURN k, names;
```

```
{"substances": 16}
{"k": "methamphetamine", "names": ["Methamphetamine", "methamphetamine"]}
{"k": "ketamine", "names": ["Ketamine", "ketamine"]}
```

  Trên graph v2 (cùng hai truy vấn, `report/evidence/evidence_v2.txt`): `{"substances": 12}` và **0 dòng** trùng.
- **Nguyên nhân:** nằm ở **bước trích xuất và khóa định danh của ontology**. `add_news_case` `MERGE` theo `s.name` do LLM tự viết; prompt có đưa "DANH SÁCH CHẤT" nhưng LLM không luôn tuân thủ, và khác với tội danh (có `link_entity`) chất không qua bước chuẩn hóa nào, nên `MERGE` coi `Ketamine` và `ketamine` là hai khóa khác nhau.
- **Đề xuất sửa (đã làm trong ontology v2):** `canonical_substance()` chuẩn hóa chữ thường, gộp alias (`thuốc lắc` → `MDMA`), đưa tên chung chung về một node và dùng `difflib` ngưỡng 0,85 cho chất đã biết. Đánh đổi: phải bảo trì bảng alias bằng tay, chất lạ (vd. `etomidate`) vẫn thành một node riêng.

### Lỗi E2: Thiếu ngữ cảnh luật cho câu hỏi "tối đa" (Q4)

- **Hiện tượng:** Q4 hỏi mức phạt tù **tối đa** cho hành vi của 'Hoàng Nato'. Graph trả "tối đa 7 năm theo Điều 255" (judge 1), trong khi khung cao nhất của Điều 255 là "20 năm hoặc tù chung thân".
- **Bằng chứng:** câu trả lời trích nguyên văn từ `ket_qua_benchmark_kg.hint.txt` và `ket_qua_benchmark_kg.txt`, mục `Q4 graph`: *"Hành vi này có thể bị phạt tù tối đa 7 năm theo Điều 255 Bộ luật Hình sự."* Cypher cho thấy graph **có** khoản chung thân:

```cypher
MATCH (:Article {id:'Điều 255 BLHS'})-[:HAS_CLAUSE]->(c:Clause) RETURN c.number AS khoan, c.severity AS severity, c.penalty AS penalty ORDER BY khoan;
```

```
{'khoan': 1, 'severity': 7,  'penalty': 'phạt tù từ 02 năm đến 07 năm'}
{'khoan': 4, 'severity': 90, 'penalty': 'phạt tù 20 năm hoặc tù chung thân'}   (các khoản 2, 3, 5 lược bớt; xem evidence_v2.txt)
```

  Nhưng `context(Q4)` trên ontology gợi ý chỉ đưa vào **khoản 1** của mỗi Điều: `khoản theo Điều: {'Điều 249': [1], 'Điều 251': [1], 'Điều 255': [1]}` (`evidence_hint.txt`).
- **Nguyên nhân:** ở bước **Cypher của KG-3**: quy tắc lọc khoản ("khoản 1 + khoản nhắc chất của vụ") bỏ sót khoản nặng nhất khi câu hỏi không nhắc chất nào. Vì vậy câu trả lời chỉ thấy khung cơ bản 02–07 năm. Ngoài ra Hoàng Nato bị nối với ba tội (Điều 249, 251, 255), nên "tối đa" còn mơ hồ về Điều nào.
- **Đề xuất sửa và kết quả đo được:** ontology v2 thêm `Clause.severity` và quy tắc "tối đa/cao nhất" trong `context()`. Kết quả: `context(Q4)` giờ có `'Điều 255': [1, 4]`. **Nhưng câu trả lời cuối vẫn là "7 năm"**: sửa ở tầng truy hồi chưa chuyển thành sửa ở câu trả lời. Giả thuyết (chưa kiểm chứng): ngữ cảnh dài 13 134 ký tự gồm 10 khoản của ba Điều, và `gpt-4o-mini` neo vào khoản 1. Hướng tiếp theo: rút gọn ngữ cảnh, đặt khoản nặng nhất lên đầu, hoặc thêm hướng dẫn "nêu khung cao nhất" vào `GRAPH_PROMPT`. Đánh đổi: prompt đặc thù hơn, có nguy cơ làm sai các câu khác.

### Lỗi E4: Phép đo sai (recall mâu thuẫn judge ở Q6)

- **Hiện tượng:** ở Q6, `recall` và `judge` không nhất quán: Flat có `recall=0,00` nhưng `judge=1`; Graph (ontology gợi ý) có `recall=1,00` nhưng `judge=1`.
- **Bằng chứng:** câu trả lời Flat (`ket_qua_benchmark_kg.txt`, `Q6 flat`) nêu ba vụ nhưng bằng tên khác với `must_include`: *"Vụ việc của Đức … Vụ việc của Thành … Vụ việc của Đông …"*. Chuỗi bắt buộc là `Cái Quang Huy`, `Lê Minh Thành`, `Pháp y tâm thần` nên `keyword_recall` ra 0 dù câu trả lời đúng một phần (judge 1). Chiều ngược lại: câu trả lời `Q6 graph` của lần chạy gợi ý chứa đủ ba chuỗi (`recall=1,00`) kèm thêm vụ Sầm Sơn không được nêu tên trong đáp án chuẩn; judge chỉ cho 1 (lý do của judge không được ghi trong file kết quả, nên tôi không khẳng định nguyên nhân).
- **Nguyên nhân:** nằm ở **bước phép đo**: `keyword_recall` khớp chuỗi con nguyên văn, không chịu được biến thể tên ("Thành" so với "Lê Minh Thành", "vụ của Đức" so với "Cái Quang Huy") và không phạt phần thừa; còn judge chấm theo đáp án chuẩn mà đáp án này chỉ liệt kê ba vụ.
- **Đề xuất sửa:** đưa `must_include` về dạng có alias (ví dụ `["Lê Minh Thành", "Thành"]`) hoặc chuẩn hóa tên trước khi so; báo cả recall lẫn judge thay vì một điểm duy nhất; kiểm đáp án chuẩn bằng Cypher trước khi dùng. Đánh đổi: tốn thêm công thiết kế bộ đánh giá. (Tôi không sửa `bench_kg.py` hay `benchmark_kg.json` vì SUBMISSION.md cấm; đây là đề xuất.)

### Lỗi E5: LLM lệch với graph (Q6, câu `aggregation`)

- **Hiện tượng:** graph (ontology v2) có **5** vụ liên quan MDMA nhưng câu trả lời của GraphRAG chỉ liệt kê **4**, thiếu "Vụ án tại Viện Pháp y tâm thần Trung ương" nên `recall=0,67`.
- **Bằng chứng:** Cypher trả lời thẳng câu hỏi (`report/evidence/evidence_v2.txt`):

```cypher
MATCH (k:Case)-[i:INVOLVES]->(:Substance {name:'MDMA'}) RETURN k.name AS vu, k.doc_id AS doc, i.amount AS amount ORDER BY vu;
```

```
Vụ bắt giang hồ 'Hoàng Nato' và 126 người liên quan 8 đường dây ma túy | news-100260920221957595 | các loại ma túy tổng hợp
Vụ góp tiền mua ma túy tại Hà Nội | news-100260918080821054 | 5 viên
Vụ tổ chức sử dụng ma túy tại Sầm Sơn | news-100260930085028036 | 0,686g
Vụ vận chuyển ma túy từ Đức về Việt Nam | news-100260917203001265 | 9.6kg
Vụ án tại Viện Pháp y tâm thần Trung ương | news-100260924105118645 |
(5 vụ)
```

  Câu trả lời của GraphRAG (`Q6 graph`, `ket_qua_benchmark_kg.txt`) liệt kê: vụ Đức, vụ Hà Nội, vụ Sầm Sơn, vụ Hoàng Nato, và **không** có vụ Viện Pháp y. Khi chạy lại với ontology gợi ý, câu trả lời lại liệt kê vụ Viện Pháp y nhưng bỏ vụ Hoàng Nato (`ket_qua_benchmark_kg.hint.txt`), tức lỗi **không giống nhau giữa các lần chạy**.
- **Nguyên nhân:** nằm ở **prompt trả lời / LLM**, không phải ở graph: bước tổng hợp của `context()` lấy đủ mọi vụ MDMA: tôi kiểm lại trên graph v2 dựng lại sau đó (198 node, khác chút so với graph của lần benchmark, do trích xuất LLM không tất định) thì `context(Q6)` chứa đủ **5/5** dữ kiện "liên quan MDMA", kể cả vụ Viện Pháp y. Như vậy phần sót nằm ở `gpt-4o-mini` khi viết câu trả lời (chọn 4 trong 5, khác nhau giữa các lần chạy). Một yếu tố cộng thêm: số vụ phụ thuộc cách LLM tách `Case` khi trích xuất (Hoàng Nato thành 4 `Case` cho 4 bài báo; theo tiêu đề, bài "Sầm Sơn" và bài "Viện Pháp y" cùng nói về bê bối ở Viện Pháp y tâm thần), nên đếm theo `Case` không ổn định.
- **Đề xuất sửa:** với câu gom nhóm, trả lời **trực tiếp từ Cypher** (đếm và liệt kê trong code rồi chỉ nhờ LLM diễn đạt) thay vì nhờ LLM đọc danh sách; gộp `Case` trùng theo người/bị cáo hoặc theo `doc_id`. Đánh đổi: thêm một nhánh xử lý riêng cho loại câu hỏi này.

## 4. Kết luận (5 điểm)

**Khi nào nên dùng KG:** khi câu hỏi cần **ghép dữ kiện nằm ở hai nguồn khác nhau** và có một khóa nối rõ ràng. Trên bộ này: Q3, Q4, Q5 (cross-kb) Flat RAG thua hẳn (recall 0,00 / 0,00 / 0,60 so với 1,00 / 0,67 / 1,00 của Graph; judge trung bình toàn bộ 1,00 so với 1,67), vì tên người chỉ có trong tin tức còn khung phạt chỉ có trong luật. KG càng đáng khi một nguồn rất đều (văn bản luật) để dựng bằng regex với **0 lần gọi LLM**, và khi số câu hỏi nhiều: chi phí thêm mỗi câu hội tụ về khoảng $0,0006.

**Khi nào Flat RAG đủ:** câu hỏi một nguồn (Q1, Q2 ngang nhau, recall 1,00, judge 2) hoặc kho nhỏ, ít cần suy luận qua nhiều tài liệu. Flat rẻ hơn khoảng 5,4 lần mỗi câu và 8,3 lần khi dựng chỉ mục, và không cần Neo4j.

**Điều kiện và giới hạn của kết luận này:** chỉ 6 câu, một lần chạy mỗi ontology, trích xuất bằng LLM không tất định (recall của hai ontology khác nhau 0,05 chỉ vì Q6), và cột USD/giây đo qua cổng API trung gian. GraphRAG cũng **không** tự sửa được mọi câu: Q4 vẫn sai dù graph đủ dữ kiện, và câu gom nhóm (Q6) bị sót vụ. Vì vậy với kho lớn hơn hoặc thay đổi thường xuyên, chi phí duy trì ontology (khóa định danh, bảng alias, chuẩn hóa tên) mới là chi phí thật sự của KG.

## 5. Tự kiểm

Đầu ra `pytest tests/ -q` (chạy ngày 05/10/2026):

```
................................................                         [100%]
48 passed in 0.10s
```

Đầu ra `python bench_kg.py --check` (7 dòng `[OK]`):

```
[OK] Dữ liệu: 18 điều luật, 20 bài báo
[OK] KG-1 link_entity
[OK] Neo4j kết nối được
[OK] KG-2 build_graph: 146 node / 511 cạnh, đường xuyên 2 KB dài 2 cạnh
[OK] KG-3 context: 13 dữ kiện, có Điều 251
[OK] KG-4 GraphRAGAgent.answer
[OK] Chi phí check: 1 lần gọi LLM, $0.00064. Graph nhỏ (luật + 1 bài) vẫn còn trong Neo4j để bạn xem; chạy --judge để dựng graph đầy đủ.
```

Ba ảnh Neo4j (chụp thật từ `http://localhost:7474`, graph của lần chạy `ket_qua_benchmark_kg.txt`): `report/img/kg_count.png` (Q-A, đếm node), `report/img/kg_cross_kb.png` (Q-B, đường người → vụ → tội → Điều luật), `report/img/kg_my_case.png` (Q-D với người tôi tự chọn: **Cái Quang Huy**).

**Vấn đề gặp phải:** Docker Desktop chưa chạy khi bắt đầu nên không kết nối được Neo4j (`failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine`); đã mở Docker Desktop rồi `docker run` container `neo4j-drug-kg`. Máy dùng Python 3.12 thay vì 3.11 mà bài ghi; 48 test vẫn qua. Key API thuộc một cổng trung gian tương thích OpenAI nên phải đặt thêm `OPENAI_BASE_URL` trong `.env` (không commit).
