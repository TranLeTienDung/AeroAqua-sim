# AeroAqua — Mô phỏng hệ thống thu sương tưới tiêu

**AeroAqua** mô phỏng một cụm **lưới thu sương** (fog harvesting) tự động: lưới có trục xoay bám theo hướng gió để đón sương nhiều nhất, nước chảy về bồn chứa bằng trọng lực, rồi được dùng để tưới cho một khu vườn. Toàn bộ phần "thông minh" nằm trong một bộ điều khiển tại chỗ (mô phỏng ESP32), không cần máy chủ trung tâm.

Dự án gồm hai dạng: **mô phỏng 3D bằng Python** (Ursina) và **phiên bản web** chạy trực tiếp trên trình duyệt.

---

## ✨ Tính năng mô phỏng

Bộ điều khiển tại chỗ thực hiện 3 việc chính:

1. **Trục xoay bám hướng gió** — tự xoay lưới để hứng được nhiều sương nhất.
2. **Tự bảo vệ khi gió mạnh** — xoay nghiêng lưới khi gió vượt ngưỡng an toàn, rồi quay lại thu sương khi gió dịu.
3. **Dự báo 24 giờ** — ước lượng lượng nước thu được trong 24 giờ tới từ chính số đo của cảm biến (học chu kỳ ngày–đêm).

Mô hình theo dõi: độ ẩm, gió, hàm lượng nước trong sương, mực nước trong bồn, nhu cầu tưới và sức khỏe cây trồng. Có 3 kịch bản thời tiết: **normal**, **drought** (hạn), **storm** (bão).

---

## 📁 Cấu trúc dự án

```
Starup_Project/
├── main.py                 # Mô phỏng 3D (1 lưới) — giao diện Ursina
├── simulation.py           # Lõi mô phỏng, độc lập đồ họa
├── web/
│   ├── index.html          # Phiên bản web của mô phỏng
│   └── aeroaqua-sim.html   # Biến thể (fragment) cùng nội dung
├── Huong-dan-su-dung.docx  # Hướng dẫn sử dụng
└── requirements.txt
```

> Bản **1 lưới** (`main.py` / `simulation.py`) là bản đơn giản hóa: 1 lưới thu sương có trục xoay, 1 bồn chứa, 1 vườn — không Master Node, không trọng số ưu tiên, không bơm chuyển nước.

---

## 🚀 Chạy bản 3D (Python)

Yêu cầu: **Python 3.10+**

```bash
# (khuyến nghị) tạo môi trường ảo
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS / Linux

# cài phụ thuộc
pip install -r requirements.txt

# chạy
python main.py
```

### Tùy chọn dòng lệnh

| Tùy chọn | Ý nghĩa |
|----------|---------|
| `--scenario normal\|drought\|storm` | Kịch bản thời tiết lúc mở |
| `--warp 600` | Tua trước N phút mô phỏng rồi mới hiển thị |
| `--shot out.png` | Chụp 1 ảnh sau vài giây rồi thoát (để kiểm thử) |

### Điều khiển

- **Chuột phải kéo:** xoay camera · **Lăn chuột:** zoom · **Chuột giữa kéo:** di chuyển
- **Space:** tạm dừng · **1 / 2 / 3:** đổi kịch bản · **P:** bảng thông số thiết kế

### So sánh trục xoay BẬT / TẮT

```bash
python simulation.py
```

---

## 🌐 Chạy bản web

Mở trực tiếp `web/index.html` bằng trình duyệt, hoặc chạy một máy chủ tĩnh:

```bash
cd web
python -m http.server 8000
# rồi mở http://localhost:8000
```

---

## ⚙️ Tùy chỉnh thông số

Mọi hằng số thiết kế (diện tích lưới, hiệu suất thu, dung tích bồn, nhu cầu tưới, ngưỡng gió bảo vệ...) nằm ở đầu file [`simulation.py`](simulation.py) và có thể chỉnh trực tiếp để thử các cấu hình khác nhau. Trong bản 3D, nhiều thông số cũng xem/chỉnh được qua bảng **Thông số thiết kế** (phím `P`).

---

## 📄 Tài liệu

- Hướng dẫn sử dụng: [`Huong-dan-su-dung.docx`](Huong-dan-su-dung.docx)
