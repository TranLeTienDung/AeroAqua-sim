"""
AeroAqua - LÕI MÔ PHỎNG, BẢN ĐƠN GIẢN 1 LƯỚI (không phụ thuộc đồ hoạ)
====================================================================
Hệ thống chỉ gồm 1 cụm độc lập:
  * 1 lưới thu sương có trục xoay, cảm biến gió + độ ẩm gắn trên đỉnh trụ
  * 1 bộ điều khiển (ESP32) đặt ngay tại đế lưới
  * 1 bồn chứa, nước chảy về bằng trọng lực
  * 1 vườn được tưới từ bồn
KHÔNG có Master Node, KHÔNG có trọng số ưu tiên, KHÔNG bơm chuyển nước giữa
các bồn. (Bản 3 lưới cũ được giữ trong thư mục ban_3_luoi/.)

Phần "thông minh" còn lại nằm trọn trong bộ điều khiển tại chỗ:
  1. Trục xoay bám hướng gió để đón sương nhiều nhất
  2. Tự xoay nghiêng bảo vệ lưới khi gió quá mạnh
  3. Dự báo lượng nước thu được 24 giờ tới từ chính số đo của nó

Chạy riêng file này để so sánh trục xoay BẬT / TẮT:
    python simulation.py

Mọi hằng số bên dưới là GIẢ ĐỊNH mô phỏng, chỉnh tự do để thử thiết kế.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

# --------------------------------------------------------------------------
# THAM SỐ (giả định - chỉnh ở đây hoặc trên bảng "Thông số thiết kế")
# --------------------------------------------------------------------------
NET_AREA = 30.0              # m2 lưới
NET_EFFICIENCY = 0.20        # hiệu suất thu của lưới (20% lượng nước bay qua)
RH_FOG_START = 92.0          # % độ ẩm bắt đầu có sương
LWC_MAX = 0.45               # g/m3 - hàm lượng nước lỏng tối đa trong sương
PREVAILING_WIND_DIR = 0.0    # độ - hướng lưới khi cố định (gió thổi VỀ hướng này)
WIND_PROTECT_ON = 12.0       # m/s - gió mạnh hơn mức này: lưới xoay nghiêng bảo vệ
WIND_PROTECT_OFF = 10.0      # m/s - gió dịu xuống dưới mức này: quay lại thu sương
NET_TURN_RATE = 3.0          # độ / phút mô phỏng - tốc độ trục xoay

TANK_CAPACITY = 600.0        # L
TANK_START = 360.0           # L lúc bắt đầu
TANK_LOW = 0.20              # cảnh báo khi bồn dưới mức này
DEMAND_PER_DAY = 170.0       # L/ngày nhu cầu tưới của vườn
CROP_NAME = "Dâu tây"
IRRIGATION_HOURS = (6, 18)   # tưới trong khung giờ này
STRESS_PER_HOUR = 3.0        # % sức khoẻ cây mất mỗi giờ thiếu nước hoàn toàn
RECOVER_PER_HOUR = 0.4       # % hồi phục mỗi giờ được tưới đủ

RIVER_WH_PER_L = 0.82        # Wh/L - nếu phải bơm ngược dốc từ sông (cột áp ~150 m)

SENSOR_PERIOD = 5            # phút - chu kỳ đọc cảm biến mực nước
CONTROL_PERIOD = 10          # phút - chu kỳ lập dự báo

FORECAST_HOURS = 24          # tầm dự báo
PROFILE_ALPHA = 0.4          # trọng số của ngày mới nhất khi học chu kỳ ngày-đêm
ANOMALY_TAU_H = 6.0          # giờ - sai lệch hiện tại so với chu kỳ tắt dần theo hằng số này
K_PRIOR_WEIGHT = 2.0         # độ tin vào hệ số thiết kế trước khi có dữ liệu đo

SCENARIOS = {
    "normal":  {"label": "Mùa sương bình thường", "d_rh": 0.0,  "d_temp": 0.0, "wind_mul": 1.0},
    "drought": {"label": "Hạn El Niño",           "d_rh": -1.5, "d_temp": 2.0, "wind_mul": 0.9},
    "storm":   {"label": "Gió bão",               "d_rh": 1.0,  "d_temp": -1.0, "wind_mul": 3.2},
}


def clamp(x, lo, hi):
    return lo if x < lo else hi if x > hi else x


def ang_diff(a, b):
    """Góc lệch có dấu a-b, trong [-180, 180]."""
    return (a - b + 180.0) % 360.0 - 180.0


# --------------------------------------------------------------------------
class Forecaster:
    """
    BỘ DỰ BÁO LƯỢNG NƯỚC THU ĐƯỢC - chạy trên bộ điều khiển tại lưới.
    Chỉ dùng thông tin bộ điều khiển thật sự có: độ ẩm, tốc độ gió, hướng gió
    (cảm biến trên đỉnh trụ), mực nước bồn, lịch tưới và cấu hình trục xoay.
    KHÔNG đọc mô hình thời tiết hay sản lượng thật của mô phỏng.

      1. Học chu kỳ ngày-đêm: trung bình độ ẩm và gió theo từng giờ trong ngày.
      2. Dự báo từng giờ = chu kỳ đã học + sai lệch hiện tại (tắt dần).
      3. Đổi sang lít: lít/giờ = k x chỉ số sương. Hệ số k của lưới được hiệu
         chuẩn từ mực nước bồn tăng lên vào ban đêm (không tưới, bồn chưa đầy).
    """

    def __init__(self, sim):
        self.rh_prof = [None] * 24
        self.wind_prof = [None] * 24
        self._slot = int(sim.hour)
        self._sum_rh = self._sum_wind = 0.0
        self._cnt = 0
        self.k_num = 0.0
        self.k_den = 0.0
        self._idx_acc = 0.0          # tích phân chỉ số sương từ lần đọc mực nước trước
        self._clean = True
        self._prev_level = None
        self.hourly = [0.0] * FORECAST_HOURS     # L cho từng giờ tới
        self.total_6h = 0.0
        self.total_24h = 0.0
        self.minutes_learned = 0.0
        self._pending = []           # (thời điểm đến hạn, dự báo, tổng thu lúc phát)
        self.scored = []             # (dự báo, thực tế)
        self._last_issue_hour = -1

    @staticmethod
    def fog_index(rh, wind, wind_dir, auto_rotate):
        level = clamp((rh - RH_FOG_START) / 6.0, 0.0, 1.0)
        if level <= 0.0:
            return 0.0
        if auto_rotate:
            if wind > WIND_PROTECT_ON:
                return 0.0           # lưới sẽ xoay nghiêng bảo vệ
            facing = 1.0
        else:
            facing = abs(math.cos(math.radians(ang_diff(wind_dir, PREVAILING_WIND_DIR))))
        return level * wind * facing

    def k(self):
        """Hệ số lưới (L/giờ cho mỗi đơn vị chỉ số sương): pha giữa giá trị thiết
        kế và giá trị đo được, càng nhiều dữ liệu càng tin số đo."""
        prior = NET_EFFICIENCY * LWC_MAX * 3.6 * NET_AREA
        return (self.k_num + prior * K_PRIOR_WEIGHT) / (self.k_den + K_PRIOR_WEIGHT)

    def sample(self, sim, dt):
        """Gọi mỗi phút: lấy mẫu cảm biến thời tiết."""
        self.minutes_learned += dt
        slot = int(sim.hour)
        if slot != self._slot:
            if self._cnt:
                for prof, total in ((self.rh_prof, self._sum_rh), (self.wind_prof, self._sum_wind)):
                    mean = total / self._cnt
                    old = prof[self._slot]
                    prof[self._slot] = mean if old is None else old + PROFILE_ALPHA * (mean - old)
            self._slot = slot
            self._sum_rh = self._sum_wind = 0.0
            self._cnt = 0
        self._sum_rh += sim.rh
        self._sum_wind += sim.wind_speed
        self._cnt += 1
        self._idx_acc += self.fog_index(sim.rh, sim.wind_speed, sim.wind_dir, sim.auto_rotate) * dt / 60.0
        if IRRIGATION_HOURS[0] <= sim.hour < IRRIGATION_HOURS[1]:
            self._clean = False

    def on_level_reading(self, level_frac):
        """Gọi mỗi lần đọc cảm biến mực nước: hiệu chuẩn hệ số lưới."""
        prev = self._prev_level
        if (prev is not None and self._clean and self._idx_acc > 0.02
                and prev < 0.97 and level_frac < 0.97):
            self.k_num += max(0.0, level_frac - prev) * TANK_CAPACITY
            self.k_den += self._idx_acc
        self._prev_level = level_frac
        self._idx_acc = 0.0
        self._clean = True

    def _profile(self, prof, hour, fallback):
        """Nội suy tuyến tính chu kỳ đã học tại 'hour' (tâm ô giờ s là s+0.5)."""
        x = (hour - 0.5) % 24.0
        a, b = int(x) % 24, (int(x) + 1) % 24
        va = prof[a] if prof[a] is not None else fallback
        vb = prof[b] if prof[b] is not None else fallback
        return va + (vb - va) * (x - int(x))

    def predict(self, sim):
        now_h = sim.hour
        d_rh = sim.rh - self._profile(self.rh_prof, now_h, sim.rh)
        d_wind = sim.wind_speed - self._profile(self.wind_prof, now_h, sim.wind_speed)
        k = self.k()
        for j in range(FORECAST_HOURS):
            acc = 0.0
            for q in range(4):                       # 4 điểm mỗi giờ
                lead = j + (q + 0.5) / 4.0
                decay = math.exp(-lead / ANOMALY_TAU_H)
                h = (now_h + lead) % 24.0
                rh = self._profile(self.rh_prof, h, sim.rh) + d_rh * decay
                wind = max(0.0, self._profile(self.wind_prof, h, sim.wind_speed) + d_wind * decay)
                acc += self.fog_index(rh, wind, sim.wind_dir, sim.auto_rotate)
            self.hourly[j] = k * acc / 4.0
        self.total_6h = sum(self.hourly[:6])
        self.total_24h = sum(self.hourly)
        # chấm điểm: mỗi giờ phát 1 dự báo 6h, 6 giờ sau so với lượng thu thật
        while self._pending and self._pending[0][0] <= sim.t:
            _, pred, start = self._pending.pop(0)
            self.scored.append((pred, sim.harvested - start))
            del self.scored[:-48]
        hour_id = int(sim.t // 60)
        if hour_id != self._last_issue_hour:
            self._last_issue_hour = hour_id
            self._pending.append((sim.t + 360.0, self.total_6h, sim.harvested))

    def accuracy(self):
        """(số dự báo đã chấm, sai số tuyệt đối TB (L), sai số % theo tổng lượng thật)."""
        if not self.scored:
            return 0, 0.0, 0.0
        err = sum(abs(p - a) for p, a in self.scored)
        act = sum(a for _, a in self.scored)
        return len(self.scored), err / len(self.scored), (100.0 * err / act if act > 1.0 else 0.0)


# --------------------------------------------------------------------------
@dataclass
class Simulation:
    seed: int = 7
    auto_rotate: bool = True
    scenario: str = "normal"
    # điều chỉnh tay của người dùng (cộng thêm vào kịch bản)
    user_d_rh: float = 0.0
    user_wind_mul: float = 1.0
    user_wind_dir: float = PREVAILING_WIND_DIR

    t: float = 0.0                       # phút mô phỏng
    events: list[tuple[float, str, str]] = field(default_factory=list)

    def __post_init__(self):
        self.rng = random.Random(self.seed)
        # môi trường
        self.temp = 18.0
        self.rh = 90.0
        self.wind_speed = 4.0
        self.wind_dir = PREVAILING_WIND_DIR
        self.lwc = 0.0
        self._n_rh = self._n_wind = self._n_dir = 0.0
        # lưới
        self.net_yaw = PREVAILING_WIND_DIR
        self.protecting = False
        self.harvest_lph = 0.0
        # bồn + vườn
        self.level = min(TANK_START, TANK_CAPACITY)
        self.level_reading = self.frac       # số đo cảm biến mực nước gần nhất
        self.health = 100.0
        self.irrigation_lph = 0.0
        # thống kê
        self.harvested = 0.0
        self.overflow = 0.0
        self.irrigated = 0.0
        self.unmet = 0.0
        self.overload_hours = 0.0            # giờ lưới hứng thẳng gió vượt ngưỡng
        self._day_start = (0.0, 0.0, 0.0, 0.0)
        # cờ để ghi nhật ký 1 lần
        self._fog_on = False
        self._low_warned = False
        self._empty_warned = False
        self._full_warned = False
        self._shortage_day = -1
        self._update_env(0.0)
        self.fc = Forecaster(self)
        self.log("Hệ thống khởi động. Bộ điều khiển bắt đầu đọc cảm biến.", "info")

    # ---------------------------------------------------------------- tiện ích
    @property
    def hour(self) -> float:
        return (self.t / 60.0) % 24.0

    @property
    def day(self) -> int:
        return int(self.t // 1440) + 1

    @property
    def frac(self) -> float:
        return self.level / TANK_CAPACITY

    def clock(self) -> str:
        h = self.hour
        return f"Ngày {self.day}  {int(h):02d}:{int(h * 60) % 60:02d}"

    def log(self, text: str, kind: str = "info"):
        self.events.append((self.t, text, kind))
        if len(self.events) > 200:
            del self.events[:100]

    # ---------------------------------------------------------------- môi trường
    def _base_env(self, hour: float):
        sc = SCENARIOS[self.scenario]
        s = math.sin(2 * math.pi * (hour - 9.0) / 24.0)   # đỉnh lúc 15h, đáy lúc 3h
        temp = 18.0 + 6.0 * s + sc["d_temp"]
        rh = 84.0 - 14.0 * s + sc["d_rh"] + self.user_d_rh
        wind = (4.0 + 1.5 * math.sin(2 * math.pi * (hour - 2.0) / 24.0)) \
            * sc["wind_mul"] * self.user_wind_mul
        return temp, rh, max(0.0, wind)

    def _update_env(self, dt: float):
        r = self.rng
        k = math.sqrt(dt) if dt > 0 else 0.0
        # nhiễu chậm kiểu Ornstein-Uhlenbeck
        self._n_rh += -self._n_rh * dt / 180.0 + r.gauss(0, 0.12) * k
        self._n_wind += -self._n_wind * dt / 90.0 + r.gauss(0, 0.07) * k
        self._n_dir += -self._n_dir * dt / 240.0 + r.gauss(0, 1.6) * k
        temp, rh, wind = self._base_env(self.hour)
        self.temp = temp
        self.rh = clamp(rh + self._n_rh, 20.0, 100.0)
        self.wind_speed = max(0.0, wind * (1.0 + 0.25 * self._n_wind))
        self.wind_dir = (self.user_wind_dir + self._n_dir) % 360.0
        self.lwc = clamp((self.rh - RH_FOG_START) / 6.0, 0.0, 1.0) * LWC_MAX

    # ---------------------------------------------------------------- bước mô phỏng
    def step(self, dt: float = 1.0):
        """Tiến mô phỏng dt phút (nên dùng dt = 1)."""
        prev_t = self.t
        self.t += dt
        self._update_env(dt)
        self.fc.sample(self, dt)
        hrs = dt / 60.0

        # 1) Trục xoay
        was = self.protecting
        if self.auto_rotate:
            if self.wind_speed > WIND_PROTECT_ON:
                self.protecting = True
            elif self.wind_speed < WIND_PROTECT_OFF:
                self.protecting = False
            target = self.wind_dir + (90.0 if self.protecting else 0.0)
        else:
            self.protecting = False
            target = PREVAILING_WIND_DIR
        d = ang_diff(target, self.net_yaw)
        self.net_yaw = (self.net_yaw + clamp(d, -NET_TURN_RATE * dt, NET_TURN_RATE * dt)) % 360.0
        if self.protecting != was:
            self.log("Gió mạnh -> lưới xoay nghiêng bảo vệ" if self.protecting
                     else "Gió dịu -> lưới quay lại đón sương", "warn" if self.protecting else "info")

        # 2) Thu sương: hiệu suất x LWC x gió x diện tích x góc đón
        facing = abs(math.cos(math.radians(ang_diff(self.wind_dir, self.net_yaw))))
        self.harvest_lph = NET_EFFICIENCY * self.lwc * self.wind_speed * NET_AREA * facing * 3.6
        got = self.harvest_lph * hrs
        self.harvested += got
        self.level += got
        if self.wind_speed > WIND_PROTECT_ON and facing > 0.5:
            self.overload_hours += hrs
        if not self._fog_on and self.harvest_lph > 1.0:
            self._fog_on = True
            self.log(f"Có sương (độ ẩm {self.rh:.0f}%) -> lưới bắt đầu thu nước", "good")
        elif self._fog_on and self.lwc <= 0.0:
            self._fog_on = False
            self.log("Sương tan -> lưới ngừng thu", "info")

        # 3) Tưới + sức khoẻ cây
        self.irrigation_lph = 0.0
        if self.health > 0 and IRRIGATION_HOURS[0] <= self.hour < IRRIGATION_HOURS[1]:
            span = IRRIGATION_HOURS[1] - IRRIGATION_HOURS[0]
            need = DEMAND_PER_DAY / span * hrs * (1.0 + 0.03 * (self.temp - 20.0))
            give = min(need, self.level)
            self.level -= give
            self.irrigated += give
            self.irrigation_lph = give / hrs
            miss = need - give
            self.unmet += miss
            if miss > 1e-9:
                if not self._empty_warned:
                    self._empty_warned = True
                    self.log("Bồn CẠN -> vườn không được tưới đủ!", "bad")
                self.health -= STRESS_PER_HOUR * hrs * (miss / need)
                if self.health <= 0:
                    self.health = 0.0
                    self.log(f"Vườn {CROP_NAME}: cây đã chết vì thiếu nước!", "bad")
            else:
                self.health = min(100.0, self.health + RECOVER_PER_HOUR * hrs)

        # 4) Tràn bồn + cảnh báo mực nước
        if self.level > TANK_CAPACITY:
            self.overflow += self.level - TANK_CAPACITY
            self.level = TANK_CAPACITY
            if not self._full_warned:
                self._full_warned = True
                self.log("Bồn ĐẦY -> nước thu thêm bị tràn bỏ", "warn")
        elif self.frac < 0.9:
            self._full_warned = False
        if self.frac < TANK_LOW and not self._low_warned:
            self._low_warned = True
            self.log(f"Cảnh báo: bồn còn dưới {TANK_LOW * 100:.0f}%", "warn")
        elif self.frac > TANK_LOW + 0.1:
            self._low_warned = False
            self._empty_warned = False

        # 5) Đọc cảm biến mực nước + lập dự báo
        if int(self.t // SENSOR_PERIOD) != int(prev_t // SENSOR_PERIOD):
            self.level_reading = self.frac
            self.fc.on_level_reading(self.level_reading)
        if int(self.t // CONTROL_PERIOD) != int(prev_t // CONTROL_PERIOD):
            self.fc.predict(self)
            if self.balance_24h() < 0 and self._shortage_day != self.day and self.fc.minutes_learned > 720:
                self._shortage_day = self.day
                self.log(f"Dự báo: 24 giờ tới thiếu khoảng {-self.balance_24h():.0f} L nước tưới", "warn")

        # 6) Tổng kết cuối ngày
        if int(self.t // 1440) != int(prev_t // 1440):
            h0, i0, o0, u0 = self._day_start
            self.log(f"Tổng kết ngày {self.day - 1}: thu {self.harvested - h0:.0f} L, "
                     f"tưới {self.irrigated - i0:.0f} L, tràn {self.overflow - o0:.0f} L, "
                     f"thiếu {self.unmet - u0:.0f} L", "sum")
            self._day_start = (self.harvested, self.irrigated, self.overflow, self.unmet)

    def balance_24h(self) -> float:
        """Nước trong bồn + dự báo thu 24h - nhu cầu tưới 24h (âm = sẽ thiếu)."""
        need = DEMAND_PER_DAY if self.health > 0 else 0.0
        return self.level_reading * TANK_CAPACITY + self.fc.total_24h - need

    # ---------------------------------------------------------------- thống kê
    def stats(self) -> dict:
        return {
            "harvested": self.harvested,
            "overflow": self.overflow,
            "irrigated": self.irrigated,
            "unmet": self.unmet,
            "river_wh": self.irrigated * RIVER_WH_PER_L,   # điện nếu bơm lượng này từ sông
            "overload_hours": self.overload_hours,
            "health": self.health,
        }


# --------------------------------------------------------------------------
# Chạy không đồ hoạ: so sánh trục xoay BẬT và TẮT
# --------------------------------------------------------------------------
def run_headless(scenario: str, auto_rotate: bool, days: int = 7, wind_dir: float = 0.0,
                 seed: int = 7) -> Simulation:
    sim = Simulation(seed=seed, auto_rotate=auto_rotate, scenario=scenario, user_wind_dir=wind_dir)
    for _ in range(days * 1440):
        sim.step(1.0)
    return sim


if __name__ == "__main__":
    DAYS = 7
    cases = [("normal", 0.0, "gió đúng hướng lưới"), ("normal", 60.0, "gió lệch 60 độ"),
             ("drought", 0.0, "gió đúng hướng lưới"), ("storm", 0.0, "gió đúng hướng lưới")]
    for sc, wdir, note in cases:
        print(f"\n=== {SCENARIOS[sc]['label']} - {note} - {DAYS} ngày ===")
        print(f"{'':30s}{'Lưới CỐ ĐỊNH':>16s}{'Trục xoay TỰ ĐỘNG':>20s}")
        a = run_headless(sc, False, DAYS, wdir)
        b = run_headless(sc, True, DAYS, wdir)
        rows = [("Nước thu được (L)", "harvested", "{:.0f}"),
                ("Nước tưới được (L)", "irrigated", "{:.0f}"),
                ("Tràn bồn (L)", "overflow", "{:.0f}"),
                ("Thiếu nước tưới (L)", "unmet", "{:.0f}"),
                ("Sức khoẻ cây (%)", "health", "{:.0f}"),
                ("Lưới hứng gió quá tải (giờ)", "overload_hours", "{:.1f}")]
        sa, sb = a.stats(), b.stats()
        for label, key, fmt in rows:
            print(f"{label:30s}{fmt.format(sa[key]):>16s}{fmt.format(sb[key]):>20s}")
        n, mae, pct = b.fc.accuracy()
        true_k = NET_EFFICIENCY * LWC_MAX * 3.6 * NET_AREA
        print(f"  Dự báo 6h (trục xoay bật): {n} lần chấm, sai số TB {mae:.0f} L ({pct:.0f}%)"
              f"   |  hệ số lưới học được {b.fc.k():.1f} (thật {true_k:.1f})")
