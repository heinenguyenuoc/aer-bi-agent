# Alberta Energy BI Agent (text-to-SQL, chạy miễn phí)

Bản làm lại của "AI-powered BI agent" bằng stack miễn phí, trên dữ liệu upstream oil & gas của Alberta.
Hỏi bằng ngôn ngữ tự nhiên → agent sinh SQL → kiểm tra an toàn → chạy trên DuckDB → trả bảng, biểu đồ, SQL.

```
Câu hỏi ──► prompt = data dictionary (YAML) + few-shot ──► LLM ──► SQL
                                                                    │
        ┌── lỗi: đưa lại cho LLM sửa (tối đa 2 lần) ◄── chạy trên DuckDB (read-only, timeout) ◄── SQL guard (sqlglot)
        ▼
  Streamlit: bảng + biểu đồ + SQL + 👍/👎         SQLite log ──► Admin console (log, token, drift, eval)
```

| Thành phần | Công nghệ | Tương ứng bản cũ ở Live Payments |
|---|---|---|
| Kho dữ liệu | DuckDB (1 file) | Redshift |
| LLM | Gemini / Groq / OpenRouter free tier, hoặc Ollama local, hoặc Claude | Bedrock + Claude |
| Data dictionary | `data_dictionary.yaml` | Data dictionary trên S3 |
| Semantic layer + drift | `agent/drift.py` | Semantic layer tự cập nhật |
| UI + Admin | Streamlit | Amplify + Admin Console |

## Chạy thử (5 phút)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python scripts/generate_fake_data.py        # sinh data giả -> data/aer.duckdb
export GEMINI_API_KEY=...                   # hoặc GROQ_API_KEY, xem bên dưới
streamlit run app.py
```

Chưa có API key? Chọn provider `mock` trong sidebar để xem giao diện (mock chỉ trả một câu SQL cố định).

### Chọn LLM miễn phí
- **Gemini**: lấy key ở Google AI Studio → `GEMINI_API_KEY`.
- **Groq**: lấy key ở console.groq.com → `GROQ_API_KEY`, `LLM_PROVIDER=groq`.
- **Ollama (local, không giới hạn)**: cài Ollama, `ollama pull qwen2.5-coder:7b`, `LLM_PROVIDER=ollama`. Model 7B chạy được trên laptop 16GB nhưng text-to-SQL kém hơn model cloud.
- **Claude**: `ANTHROPIC_API_KEY`, `LLM_PROVIDER=anthropic` (tính phí theo token, demo nhỏ thì rẻ).

Tên model mặc định trong `agent/llm.py` và hạn mức gói free đều đổi thường xuyên: kiểm tra trang của nhà cung cấp, đặt lại bằng `LLM_MODEL` hoặc ô Model trong sidebar.

## An toàn (hai lớp)
1. `agent/guard.py` (sqlglot): đúng 1 câu SELECT/CTE, chỉ bảng trong dictionary, chặn table function và hàm đọc file (`read_csv`...), tự thêm `LIMIT`.
2. `agent/db.py`: DuckDB mở `read_only=True` và `enable_external_access=False`, có timeout. Đã thử: kể cả bypass guard, DB vẫn từ chối `CREATE`, `COPY`, `read_csv`.

## Đánh giá (con số cho CV)
```bash
python eval/run_eval.py --provider groq --model llama-3.3-70b-versatile
python eval/run_eval.py --provider gemini
python eval/run_eval.py --gold-only      # kiểm tra SQL đáp án, không gọi LLM
```
26 câu (25 SQL + 1 câu ngoài phạm vi phải từ chối), đo execution accuracy: kết quả của agent khớp kết quả SQL đáp án (bỏ qua tên cột, thứ tự dòng; làm tròn 2 chữ số; cho phép cột thừa). Kết quả lưu ở `eval/results/` và hiện ở tab Eval của Admin. Mở rộng lên 30–50 câu bằng cách thêm vào `eval/questions.yaml`. Hãy báo accuracy theo từng model và theo nhóm độ khó (join nhiều bảng, so sánh theo thời gian) thay vì một con số duy nhất.

## Dữ liệu: giả và thật

**Bản demo dùng dữ liệu giả** (`scripts/generate_fake_data.py`): operator hư cấu, sản lượng mô phỏng bằng đường suy giảm Arps, giá WTI/AECO giả. Cấu trúc và đơn vị (m³ dầu/nước, e³m³ khí) theo quy ước Petrinex/AER, nên đổi sang dữ liệu thật chỉ cần thay bước nạp.

**Nguồn thật (public)** theo catalogue của AER, kiểm tra lại định dạng và điều khoản trước khi dùng:
- Petrinex Alberta Public Data: Well Infrastructure, Well Licence, Facility Infrastructure, Well-to-Facility Link, Business Associate, Field/Pool codes (cập nhật hằng tháng), và Conventional Volumetric Data (volume theo **facility**, gồm năm hiện tại cộng 4 năm trước, loại trừ oil sands).
- AER ST37 (danh sách giếng), ST102 (facility), ST103 (field/pool), ST104 (BA code), ST60 (batteries: dầu, khí, nước, flaring, venting theo tháng).

**Hạn chế quan trọng:** sản lượng theo **từng giếng** theo tháng (Well Production Data File – All Alberta) là sản phẩm trả phí của AER (catalogue niêm yết $750 cho mỗi file đơn lẻ), không phải dữ liệu mở. Vì vậy với dữ liệu thật, bảng đáng dùng là `fact_facility_monthly` (khớp cột ST60/Petrinex) cùng các bảng dim. `fact_well_production_monthly` chỉ có trong bản giả; khi chuyển sang dữ liệu thật hãy bỏ bảng này khỏi `data_dictionary.yaml` và sửa các câu eval liên quan.

Các bước chuyển sang dữ liệu thật:
1. Tải CSV từ Petrinex/AER, nạp vào DuckDB (`CREATE TABLE ... AS SELECT * FROM read_csv(...)`) với tên bảng/cột như trong dictionary (hoặc sửa dictionary cho khớp).
2. Chạy tab **Schema drift** trong Admin để thấy ngay cột nào lệch giữa dictionary và DB.
3. Chạy lại eval; sửa `eval/questions.yaml` cho phù hợp dữ liệu thật.

AER ghi rõ người dùng được dùng file để xử lý nội bộ hoặc cho khách hàng, nhưng không được sao chép nguyên file hoặc phần lớn file để bán lại. Đừng commit file dữ liệu thật lên repo public (`.gitignore` đã loại `data/*.duckdb`).

## Cấu trúc
```
app.py                    Streamlit: trang Ask + Admin
config.yaml               đường dẫn, giới hạn, provider mặc định
data_dictionary.yaml      semantic layer: bảng, cột, đơn vị, metric, luật, few-shot
agent/pipeline.py         prompt, vòng sinh/sửa SQL, log
agent/guard.py            kiểm tra SQL (sqlglot)
agent/db.py               DuckDB read-only + timeout
agent/llm.py              client LLM (urllib, không cần SDK)
agent/logger.py           log SQLite, feedback
agent/drift.py            so dictionary với schema thật
scripts/generate_fake_data.py
eval/questions.yaml, eval/run_eval.py
```

## Ý tưởng làm tiếp
- Tab "Pending SQL review": admin duyệt SQL đúng rồi tự thêm vào few-shot (vòng học từ feedback 👍).
- Chọn bảng liên quan bằng embedding khi schema lớn (hiện cả dictionary vừa trong ~2k token nên chưa cần).
- Deploy lên Streamlit Community Cloud hoặc Hugging Face Spaces (đặt API key trong secrets).
