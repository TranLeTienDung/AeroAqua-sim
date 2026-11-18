"""
AeroAqua Smart Mesh - LÕI MÔ PHỎNG (không phụ thuộc đồ hoạ)
===========================================================
Mô phỏng theo proposal:
  * Lưới thu sương  -> nước chảy trọng lực về bồn của từng vùng trồng
  * Slave Node (bồn) -> đo mực nước, gửi telemetry qua LoRa, có bơm + van DC
  * Master Node      -> đo vi khí hậu, dự báo sản lượng, chạy "Sacrifice Routing"
  * Người dùng       -> gán trọng số ưu tiên cho từng bồn

Chạy riêng file này để so sánh CÓ / KHÔNG có định tuyến thông minh:
    python simulation.py

Mọi hằng số bên dưới là GIẢ ĐỊNH mô phỏng, chỉnh tự do để thử thiết kế.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

# --------------------------------------------------------------------------
# THAM SỐ MÔ PHỎNG (giả định - chỉnh ở đây)
# --------------------------------------------------------------------------
NET_EFFICIENCY = 0.20        # hiệu suất thu của lưới (20% lượng nước bay qua)
RH_FOG_START = 92.0          # % độ ẩm bắt đầu có sương
LWC_MAX = 0.45               # g/m3 - hàm lượng nước lỏng tối đa trong sương
PREVAILING_WIND_DIR = 0.0    # độ - hướng gió thịnh hành (gió thổi VỀ hướng này)
WIND_PROTECT_ON = 12.0       # m/s - gió mạnh hơn mức này: lưới xoay nghiêng bảo vệ
WIND_PROTECT_OFF = 10.0      # m/s - gió dịu xuống dưới mức này: quay lại thu sương
NET_TURN_RATE = 3.0          # độ / phút mô phỏng - tốc độ trục xoay

PUMP_RATE_LPH = 120.0        # L/giờ - lưu lượng bơm chuyển giữa các bồn
PUMP_WH_PER_L = 0.055        # Wh/L - bơm ngang giữa các bồn (cột áp ~10 m)
RIVER_WH_PER_L = 0.82        # Wh/L - bơm ngược dốc từ sông (cột áp ~150 m)

TELEMETRY_PERIOD = 5         # phút - chu kỳ slave gửi mực nước
CONTROL_PERIOD = 10          # phút - chu kỳ Master ra quyết định
PUMP_LEASE = 25              # phút - lệnh bơm tự hết hạn nếu không được gia hạn

LOW_LEVEL = 0.30             # bồn ưu tiên cao tụt dưới mức này -> xin cứu trợ
TARGET_LEVEL = 0.55          # bơm cứu trợ tới mức này thì dừng
OVERFLOW_START = 0.95        # bồn đầy trên mức này -> chia nước dư
OVERFLOW_STOP = 0.80
DRY_PROTECT = 0.02           # slave tự ngắt bơm khi gần cạn (chống chạy khô)

IRRIGATION_HOURS = (6, 18)   # tưới trong khung giờ này
STRESS_PER_HOUR = 3.0        # % sức khoẻ cây mất mỗi giờ thiếu nước hoàn toàn
RECOVER_PER_HOUR = 0.4       # % hồi phục mỗi giờ được tưới đủ

# --- Dự báo (Master Node) ---
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


def sacrifice_floor(weight: float) -> float:
    """Mức sàn mà bồn 'hiến' được giữ lại. Ưu tiên càng thấp, sàn càng thấp."""
    return 0.05 + 0.02 * weight


# --------------------------------------------------------------------------
@dataclass
class Zone:
    """Một vùng trồng = 1 cụm lưới + 1 bồn thông minh (Slave Node) + 1 ruộng."""
    name: str
    crop: str
    weight: int               # trọng số ưu tiên (1..10) do người dùng gán
    net_area: float           # m2 lưới
    exposure: float           # hệ số đón sương của vị trí (sương phân bố cục bộ)
    capacity: float           # L
    level: float              # L
    demand_per_day: float     # L/ngày nhu cầu tưới
    value: float              # giá trị vụ mùa (triệu VND)

    health: float = 100.0     # % sức khoẻ cây
    net_yaw: float = PREVAILING_WIND_DIR
    protecting: bool = False
    harvest_lph: float = 0.0  # L/giờ đang thu được
    pump_to: int | None = None   # trạng thái THẬT của bơm tại slave
    pump_lease: float = 0.0
    receiving_from: int | None = None
    # thống kê
    harvested: float = 0.0
    overflow: float = 0.0
    sent: float = 0.0
    received: float = 0.0
    unmet: float = 0.0

    @property
    def frac(self) -> float:
        return self.level / self.capacity


@dataclass
class Packet:
    """Một gói LoRa (để phần 3D vẽ hoạt ảnh)."""
    src: int          # chỉ số zone, hoặc -1 = Master
    dst: int
    kind: str         # 'telemetry' | 'command'
    lost: bool


class Forecaster:
    """
    BỘ DỰ BÁO LƯỢNG NƯỚC THU ĐƯỢC - chạy trên Master Node.
    Chỉ dùng thông tin hệ thống thật sự có:
      * số đo cảm biến của Master: độ ẩm, tốc độ gió, hướng gió
      * mực nước các bồn nhận qua telemetry LoRa
      * các lệnh bơm chính Master đã phát, lịch tưới, cấu hình trục xoay
    KHÔNG đọc mô hình thời tiết hay sản lượng thật của mô phỏng.

    Cách làm, 3 bước:
      1. Học chu kỳ ngày-đêm: trung bình độ ẩm và gió theo từng giờ trong ngày,
         cập nhật dần qua mỗi ngày (trung bình trượt mũ).
      2. Dự báo thời tiết từng giờ = chu kỳ đã học + sai lệch hiện tại (tắt dần).
      3. Đổi sang lít: lít/giờ = k_i x chỉ số sương, với
         chỉ số sương = mức sương ước từ độ ẩm x tốc độ gió x góc đón.
         Hệ số k_i của từng lưới được HIỆU CHUẨN từ mực nước bồn tăng lên trong
         những khoảng "sạch" (ban đêm không tưới, bồn không bơm/nhận, chưa đầy).
    """

    def __init__(self, sim):
        n = len(sim.zones)
        self.rh_prof = [None] * 24
        self.wind_prof = [None] * 24
        self._slot = int(sim.hour)
        self._sum_rh = self._sum_wind = 0.0
        self._cnt = 0
        self.k_num = [0.0] * n
        self.k_den = [0.0] * n
        self._idx_acc = [0.0] * n        # tích phân chỉ số sương từ lần telemetry trước
        self._clean = [True] * n
        self._prev_level = [None] * n
        self.hourly = [[0.0] * FORECAST_HOURS for _ in range(n)]   # L cho từng giờ tới
        self.total_6h = [0.0] * n
        self.total_24h = [0.0] * n
        self.minutes_learned = 0.0
        # chấm điểm dự báo 6 giờ
        self._pending = []               # (thời điểm đến hạn, dự báo, tổng thu lúc phát)
        self.scored = []                 # (dự báo, thực tế)
        self._last_issue_hour = -1

    # -- mô hình sương mà Master giả định --------------------------------
    @staticmethod
    def fog_index(rh, wind, wind_dir, auto_rotate):
        level = clamp((rh - RH_FOG_START) / 6.0, 0.0, 1.0)
        if level <= 0.0:
            return 0.0
        if auto_rotate:
            if wind > WIND_PROTECT_ON:
                return 0.0               # lưới sẽ xoay nghiêng bảo vệ
            facing = 1.0
        else:
            facing = abs(math.cos(math.radians(ang_diff(wind_dir, PREVAILING_WIND_DIR))))
        return level * wind * facing

    def k(self, i, sim):
        """Hệ số lưới i (L/giờ cho mỗi đơn vị chỉ số sương): pha giữa giá trị
        thiết kế và giá trị đo được, càng nhiều dữ liệu càng tin số đo."""
        prior = NET_EFFICIENCY * LWC_MAX * 3.6 * sim.zones[i].net_area
        return (self.k_num[i] + prior * K_PRIOR_WEIGHT) / (self.k_den[i] + K_PRIOR_WEIGHT)

    # -- gọi mỗi phút: lấy mẫu cảm biến -----------------------------------
    def sample(self, sim, dt):
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
        # tích luỹ chỉ số sương cho việc hiệu chuẩn k_i
        f = self.fog_index(sim.rh, sim.wind_speed, sim.wind_dir, sim.auto_rotate)
        irrigating = IRRIGATION_HOURS[0] <= sim.hour < IRRIGATION_HOURS[1]
        busy = set(sim.master_plan) | set(sim.master_plan.values())
        for src, dst in enumerate(sim.view_pump):
            if dst is not None:
                busy.update((src, dst))
        for i in range(len(sim.zones)):
            self._idx_acc[i] += f * dt / 60.0
            if irrigating or i in busy:
                self._clean[i] = False

    # -- gọi khi nhận được telemetry của bồn i ----------------------------
    def on_telemetry(self, i, level_frac, sim):
        prev = self._prev_level[i]
        if (prev is not None and self._clean[i] and self._idx_acc[i] > 0.02
                and prev < 0.97 and level_frac < 0.97):
            self.k_num[i] += max(0.0, level_frac - prev) * sim.zones[i].capacity
            self.k_den[i] += self._idx_acc[i]
        self._prev_level[i] = level_frac
        self._idx_acc[i] = 0.0
        self._clean[i] = True

    # -- gọi mỗi chu kỳ điều khiển: lập dự báo ----------------------------
    def _profile(self, prof, hour, fallback):
        """Nội suy tuyến tính chu kỳ đã học tại 'hour' (tâm ô giờ s là s+0.5)."""
        x = (hour - 0.5) % 24.0
        a, b = int(x) % 24, (int(x) + 1) % 24
        va = prof[a] if prof[a] is not None else fallback
        vb = prof[b] if prof[b] is not None else fallback
        return va + (vb - va) * (x - int(x))

    def predict(self, sim, actual_total):
        now_h = sim.hour
        d_rh = sim.rh - self._profile(self.rh_prof, now_h, sim.rh)
        d_wind = sim.wind_speed - self._profile(self.wind_prof, now_h, sim.wind_speed)
        index = []
        for j in range(FORECAST_HOURS):
            acc = 0.0
            for q in range(4):                       # 4 điểm mỗi giờ
                lead = j + (q + 0.5) / 4.0
                decay = math.exp(-lead / ANOMALY_TAU_H)
                h = (now_h + lead) % 24.0
                rh = self._profile(self.rh_prof, h, sim.rh) + d_rh * decay
                wind = max(0.0, self._profile(self.wind_prof, h, sim.wind_speed) + d_wind * decay)
                acc += self.fog_index(rh, wind, sim.wind_dir, sim.auto_rotate)
            index.append(acc / 4.0)
        for i in range(len(sim.zones)):
            k = self.k(i, sim)
            self.hourly[i] = [k * v for v in index]
            self.total_6h[i] = sum(self.hourly[i][:6])
            self.total_24h[i] = sum(self.hourly[i])
        # chấm điểm: mỗi giờ phát 1 dự báo 6h, 6 giờ sau so với lượng thu thật
        while self._pending and self._pending[0][0] <= sim.t:
            _, pred, start = self._pending.pop(0)
            self.scored.append((pred, actual_total - start))
            del self.scored[:-48]
        hour_id = int(sim.t // 60)
        if hour_id != self._last_issue_hour:
            self._last_issue_hour = hour_id
            self._pending.append((sim.t + 360.0, sum(self.total_6h), actual_total))

    def accuracy(self):
        """(số dự báo đã chấm, sai số tuyệt đối TB (L), sai số % theo tổng lượng thật)."""
        if not self.scored:
            return 0, 0.0, 0.0
        err = sum(abs(p - a) for p, a in self.scored)
        act = sum(a for _, a in self.scored)
        return len(self.scored), err / len(self.scored), (100.0 * err / act if act > 1.0 else 0.0)


@dataclass
class Simulation:
    seed: int = 7
    routing_enabled: bool = True
    auto_rotate: bool = True
    lora_loss: float = 0.02
    scenario: str = "normal"
    # điều chỉnh tay của người dùng (cộng thêm vào kịch bản)
    user_d_rh: float = 0.0
    user_wind_mul: float = 1.0
    user_wind_dir: float = PREVAILING_WIND_DIR

    t: float = 0.0                       # phút mô phỏng
    zones: list[Zone] = field(default_factory=list)
    events: list[tuple[float, str, str]] = field(default_factory=list)
    packets: list[Packet] = field(default_factory=list)

    def __post_init__(self):
        self.rng = random.Random(self.seed)            # thời tiết
        self.radio_rng = random.Random(self.seed + 1)  # nhiễu LoRa (tách riêng để so sánh công bằng)
        if not self.zones:
            self.zones = default_zones()
        n = len(self.zones)
        # Môi trường
        self.temp = 18.0
        self.rh = 90.0
        self.wind_speed = 4.0
        self.wind_dir = PREVAILING_WIND_DIR
        self.lwc = 0.0
        self._n_rh = 0.0
        self._n_wind = 0.0
        self._n_dir = 0.0
        # Hiểu biết của Master (chỉ qua telemetry LoRa - có thể cũ!)
        self.view_level = [z.frac for z in self.zones]
        self.view_pump = [None] * n
        self.view_time = [0.0] * n
        self.reason: dict[int, str] = {}       # src -> 'sacrifice' | 'overflow'
        self.master_plan: dict[int, int] = {}  # src -> dst mà Master mong muốn
        self.forecast_6h = [0.0] * n
        self.forecast_24h = [0.0] * n
        # thống kê
        self.pump_wh = 0.0
        self.pkt_sent = 0
        self.pkt_lost = 0
        self._update_env(0.0)
        self.fc = Forecaster(self)
        self.log("Hệ thống khởi động. Master Node bắt đầu thu thập dữ liệu.", "info")

    # ---------------------------------------------------------------- tiện ích
    @property
    def hour(self) -> float:
        return (self.t / 60.0) % 24.0

    @property
    def day(self) -> int:
        return int(self.t // 1440) + 1

    def clock(self) -> str:
        h = self.hour
        return f"Ngày {self.day}  {int(h):02d}:{int(h * 60) % 60:02d}"

    def log(self, text: str, kind: str = "info"):
        self.events.append((self.t, text, kind))
        if len(self.events) > 200:
            del self.events[:100]

    # ---------------------------------------------------------------- môi trường
    def _base_env(self, hour: float):
        """Chu kỳ ngày-đêm tất định (không nhiễu) - Master dùng để dự báo."""
        sc = SCENARIOS[self.scenario]
        s = math.sin(2 * math.pi * (hour - 9.0) / 24.0)   # đỉnh lúc 15h, đáy lúc 3h
        temp = 18.0 + 6.0 * s + sc["d_temp"]
        rh = 84.0 - 14.0 * s + sc["d_rh"] + self.user_d_rh
        wind = (4.0 + 1.5 * math.sin(2 * math.pi * (hour - 2.0) / 24.0)) \
            * sc["wind_mul"] * self.user_wind_mul
        return temp, rh, max(0.0, wind)

    @staticmethod
    def _lwc_from_rh(rh: float) -> float:
        return clamp((rh - RH_FOG_START) / 6.0, 0.0, 1.0) * LWC_MAX

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
        self.lwc = self._lwc_from_rh(self.rh)

    def yield_lph(self, zone: Zone, lwc: float, wind: float, facing: float = 1.0) -> float:
        """Sản lượng lưới (L/giờ) = hiệu suất x LWC x tốc độ gió x diện tích x góc đón."""
        g_per_s = NET_EFFICIENCY * lwc * wind * zone.net_area * zone.exposure * facing
        return g_per_s * 3.6

    def _forecast(self):
        """Master lập dự báo từ số đo của chính nó (xem lớp Forecaster)."""
        self.fc.predict(self, sum(z.harvested for z in self.zones))
        self.forecast_6h = list(self.fc.total_6h)
        self.forecast_24h = list(self.fc.total_24h)

    # ---------------------------------------------------------------- LoRa
    def _radio(self, src: int, dst: int, kind: str) -> bool:
        lost = self.radio_rng.random() < self.lora_loss
        self.pkt_sent += 1
        self.pkt_lost += lost
        self.packets.append(Packet(src, dst, kind, lost))
        return not lost

    # ---------------------------------------------------------------- bước mô phỏng
    def step(self, dt: float = 1.0):
        """Tiến mô phỏng dt phút (nên dùng dt = 1)."""
        prev_t = self.t
        self.t += dt
        self._update_env(dt)
        self.fc.sample(self, dt)
        hrs = dt / 60.0
        zs = self.zones

        # 1) Lưới thu sương
        for z in zs:
            was = z.protecting
            if self.auto_rotate:
                if self.wind_speed > WIND_PROTECT_ON:
                    z.protecting = True
                elif self.wind_speed < WIND_PROTECT_OFF:
                    z.protecting = False
                target = self.wind_dir + (90.0 if z.protecting else 0.0)
                d = ang_diff(target, z.net_yaw)
                z.net_yaw = (z.net_yaw + clamp(d, -NET_TURN_RATE * dt, NET_TURN_RATE * dt)) % 360.0
            else:
                z.protecting = False
                d = ang_diff(PREVAILING_WIND_DIR, z.net_yaw)
                z.net_yaw = (z.net_yaw + clamp(d, -NET_TURN_RATE * dt, NET_TURN_RATE * dt)) % 360.0
            if z.protecting != was:
                self.log(f"Lưới {z.name}: " + ("gió mạnh -> xoay nghiêng bảo vệ" if z.protecting
                                               else "gió dịu -> quay lại đón sương"),
                         "warn" if z.protecting else "info")
            facing = abs(math.cos(math.radians(ang_diff(self.wind_dir, z.net_yaw))))
            z.harvest_lph = self.yield_lph(z, self.lwc, self.wind_speed, facing)
            got = z.harvest_lph * hrs
            z.harvested += got
            z.level += got

        # 2) Bơm chuyển nước (trạng thái thật tại slave)
        for i, z in enumerate(zs):
            if z.pump_to is None:
                continue
            dst = zs[z.pump_to]
            z.pump_lease -= dt
            vol = min(PUMP_RATE_LPH * hrs, z.level, max(0.0, dst.capacity - dst.level))
            z.level -= vol
            dst.level += vol
            z.sent += vol
            dst.received += vol
            self.pump_wh += vol * PUMP_WH_PER_L
            if z.pump_lease <= 0:
                self.log(f"Slave {z.name}: lệnh bơm hết hạn (mất liên lạc) -> tự ngắt an toàn", "warn")
                self._stop_pump(i)
            elif z.frac <= DRY_PROTECT:
                self.log(f"Slave {z.name}: bồn gần cạn -> tự ngắt bơm chống chạy khô", "warn")
                self._stop_pump(i)

        # 3) Tưới + sức khoẻ cây
        h = self.hour
        irrigating = IRRIGATION_HOURS[0] <= h < IRRIGATION_HOURS[1]
        for z in zs:
            if z.health <= 0:
                continue
            if irrigating:
                span = IRRIGATION_HOURS[1] - IRRIGATION_HOURS[0]
                need = z.demand_per_day / span * hrs * (1.0 + 0.03 * (self.temp - 20.0))
                give = min(need, z.level)
                z.level -= give
                miss = need - give
                z.unmet += miss
                if miss > 1e-9:
                    z.health -= STRESS_PER_HOUR * hrs * (miss / need)
                    if z.health <= 0:
                        z.health = 0.0
                        self.log(f"Vùng {z.name} ({z.crop}): cây đã chết vì thiếu nước!", "bad")
                else:
                    z.health = min(100.0, z.health + RECOVER_PER_HOUR * hrs)

        # 4) Tràn bồn
        for z in zs:
            if z.level > z.capacity:
                z.overflow += z.level - z.capacity
                z.level = z.capacity

        # 5) Telemetry LoRa: slave -> Master (lệch pha giữa các node)
        for i, z in enumerate(zs):
            phase = i * TELEMETRY_PERIOD / len(zs)
            if int((self.t - phase) // TELEMETRY_PERIOD) != int((prev_t - phase) // TELEMETRY_PERIOD):
                if self._radio(i, -1, "telemetry"):
                    self.fc.on_telemetry(i, z.frac, self)
                    self.view_level[i] = z.frac
                    self.view_pump[i] = z.pump_to
                    self.view_time[i] = self.t

        # 6) Master ra quyết định
        if int(self.t // CONTROL_PERIOD) != int(prev_t // CONTROL_PERIOD):
            self._forecast()
            self._master_control()

    def _stop_pump(self, i: int):
        z = self.zones[i]
        if z.pump_to is not None:
            self.zones[z.pump_to].receiving_from = None
        z.pump_to = None
        z.pump_lease = 0.0

    # ---------------------------------------------------------------- THUẬT TOÁN LÕI
    def plan_routes(self) -> dict[int, int]:
        """
        SACRIFICE ROUTING - chạy trên Master, CHỈ dùng dữ liệu telemetry.
        Trả về {bồn nguồn: bồn đích}.
          Luật 1 (hy sinh):  bồn ưu tiên cao < LOW_LEVEL -> rút nước từ bồn có
                             trọng số THẤP HƠN, cho tới khi đạt TARGET_LEVEL hoặc
                             bồn hiến chạm sàn sacrifice_floor().
          Luật 2 (chia dư):  bồn sắp tràn -> đẩy phần dư sang bồn có điểm khẩn
                             cấp  w x (1 - mực nước)  lớn nhất.
        """
        if not self.routing_enabled:
            return {}
        zs = self.zones
        lv = self.view_level
        n = len(zs)
        plan: dict[int, int] = {}
        reason: dict[int, str] = {}
        used: set[int] = set()

        # Giữ các tuyến đang chạy nếu còn hợp lệ (có trễ - tránh bật/tắt liên tục)
        for src in range(n):
            dst = self.view_pump[src]
            if dst is None:
                dst = self.master_plan.get(src)      # lệnh đã gửi nhưng chưa xác nhận
            if dst is None or src in used or dst in used:
                continue
            why = self.reason.get(src, "sacrifice")
            if why == "sacrifice":
                keep = (zs[src].weight < zs[dst].weight and lv[dst] < TARGET_LEVEL
                        and lv[src] > sacrifice_floor(zs[src].weight))
            else:
                keep = lv[src] > OVERFLOW_STOP and lv[dst] < 0.90
            if keep:
                plan[src] = dst
                reason[src] = why
                used.update((src, dst))

        # Luật 1: hy sinh bồn ưu tiên thấp cứu bồn ưu tiên cao
        needy = [i for i in range(n) if i not in used and lv[i] < LOW_LEVEL and zs[i].health > 0]
        needy.sort(key=lambda i: (-zs[i].weight, lv[i]))
        for r in needy:
            if r in used:
                continue
            donors = [d for d in range(n) if d not in used and d != r
                      and zs[d].weight < zs[r].weight
                      and lv[d] > sacrifice_floor(zs[d].weight) + 0.05]
            if not donors:
                continue
            d = min(donors, key=lambda j: (zs[j].weight, -lv[j]))
            plan[d] = r
            reason[d] = "sacrifice"
            used.update((d, r))

        # Luật 2: chia nước dư khi bồn sắp tràn
        for s in range(n):
            if s in used or lv[s] < OVERFLOW_START:
                continue
            cands = [j for j in range(n) if j not in used and j != s and lv[j] < 0.75]
            if not cands:
                continue
            j = max(cands, key=lambda q: zs[q].weight * (1.0 - lv[q]))
            plan[s] = j
            reason[s] = "overflow"
            used.update((s, j))

        self.reason = reason
        return plan

    def _master_control(self):
        zs = self.zones
        new_plan = self.plan_routes()
        for src in range(len(zs)):
            want = new_plan.get(src)
            had = self.master_plan.get(src)
            if want is not None:
                if want != had:
                    why = self.reason[src]
                    if why == "sacrifice":
                        self.log(f"SACRIFICE ROUTING: bồn {zs[want].name} (w={zs[want].weight}) còn "
                                 f"{self.view_level[want]*100:.0f}% -> rút nước từ bồn {zs[src].name} "
                                 f"(w={zs[src].weight})", "route")
                    else:
                        self.log(f"CHIA NƯỚC DƯ: bồn {zs[src].name} sắp tràn "
                                 f"({self.view_level[src]*100:.0f}%) -> chuyển sang bồn {zs[want].name}",
                                 "route")
                # gửi / gia hạn lệnh bơm tới slave nguồn
                if self._radio(-1, src, "command"):
                    z = zs[src]
                    if z.pump_to is not None and z.pump_to != want:
                        self._stop_pump(src)
                    if z.frac > DRY_PROTECT:
                        z.pump_to = want
                        z.pump_lease = PUMP_LEASE
                        zs[want].receiving_from = src
            else:
                if had is not None:
                    self.log(f"Master: dừng tuyến {zs[src].name} -> {zs[had].name}", "info")
                if zs[src].pump_to is not None or self.view_pump[src] is not None or had is not None:
                    if self._radio(-1, src, "command"):
                        self._stop_pump(src)
                        self.view_pump[src] = None
        self.master_plan = new_plan

    # ---------------------------------------------------------------- thống kê
    def stats(self) -> dict:
        zs = self.zones
        moved = sum(z.sent for z in zs)
        total_value = sum(z.value for z in zs)
        kept = sum(z.value * z.health / 100.0 for z in zs)
        used = sum(z.harvested - z.overflow for z in zs)
        return {
            "harvested": sum(z.harvested for z in zs),
            "overflow": sum(z.overflow for z in zs),
            "moved": moved,
            "unmet": sum(z.unmet for z in zs),
            "pump_wh": self.pump_wh,
            "river_wh": used * RIVER_WH_PER_L,     # nếu bơm cùng lượng nước từ sông
            "value_kept": kept,
            "value_total": total_value,
            "value_pct": 100.0 * kept / total_value,
            "pkt_sent": self.pkt_sent,
            "pkt_lost": self.pkt_lost,
        }


def default_zones() -> list[Zone]:
    """Mô hình 3 bồn như MVP trong proposal. Sương phân bố lệch: vùng giá trị
    cao (A) lại đón ít sương nhất, vùng giá trị thấp (C) đón nhiều nhất."""
    return [
        Zone("A", "Dâu tây",  weight=9, net_area=30, exposure=0.6, capacity=600, level=360,
             demand_per_day=300, value=300),
        Zone("B", "Cà phê",   weight=5, net_area=30, exposure=1.0, capacity=600, level=360,
             demand_per_day=200, value=150),
        Zone("C", "Rau màu",  weight=2, net_area=30, exposure=1.5, capacity=600, level=360,
             demand_per_day=120, value=50),
    ]


# --------------------------------------------------------------------------
# Chạy không đồ hoạ: so sánh CÓ và KHÔNG có Sacrifice Routing
# --------------------------------------------------------------------------
def run_headless(scenario: str, routing: bool, days: int = 7, seed: int = 7) -> Simulation:
    sim = Simulation(seed=seed, routing_enabled=routing, scenario=scenario)
    for _ in range(days * 1440):
        sim.step(1.0)
    return sim


if __name__ == "__main__":
    DAYS = 7
    for sc in ("normal", "drought"):
        print(f"\n=== Kịch bản: {SCENARIOS[sc]['label']} - {DAYS} ngày ===")
        print(f"{'':28s}{'KHÔNG định tuyến':>20s}{'CÓ Sacrifice Routing':>24s}")
        a = run_headless(sc, False, DAYS)
        b = run_headless(sc, True, DAYS)
        sa, sb = a.stats(), b.stats()
        rows = [
            ("Nước thu được (L)", "harvested", "{:.0f}"),
            ("Nước tràn lãng phí (L)", "overflow", "{:.0f}"),
            ("Nước chuyển giữa bồn (L)", "moved", "{:.0f}"),
            ("Thiếu nước tưới (L)", "unmet", "{:.0f}"),
            ("Điện bơm (Wh)", "pump_wh", "{:.1f}"),
            ("Giá trị vụ mùa giữ được (%)", "value_pct", "{:.1f}"),
        ]
        for label, key, fmt in rows:
            print(f"{label:28s}{fmt.format(sa[key]):>20s}{fmt.format(sb[key]):>24s}")
        for i in range(3):
            za, zb = a.zones[i], b.zones[i]
            print(f"  Sức khoẻ {za.name} - {za.crop:8s} (w={za.weight}){za.health:>13.0f}%{zb.health:>23.0f}%")

        n, mae, pct = b.fc.accuracy()
        ks = "  ".join(f"{z.name}={b.fc.k(i, b):.1f}" for i, z in enumerate(b.zones))
        true_ks = "  ".join(f"{z.name}={NET_EFFICIENCY * LWC_MAX * 3.6 * z.net_area * z.exposure:.1f}"
                            for z in b.zones)
        print(f"  Dự báo 6h: {n} lần chấm, sai số TB {mae:.0f} L ({pct:.0f}% tổng lượng thật)")
        print(f"  Hệ số lưới Master học được: {ks}   (giá trị thật: {true_ks})")
