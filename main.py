"""
AeroAqua - MÔ PHỎNG 3D, BẢN ĐƠN GIẢN 1 LƯỚI
===========================================
1 lưới thu sương có trục xoay + 1 bồn chứa + 1 vườn.

Chạy:   python main.py
Tuỳ chọn:
    --scenario normal|drought|storm   kịch bản lúc mở
    --warp 600                        tua trước N phút mô phỏng rồi mới hiển thị
    --shot out.png                    chụp 1 ảnh sau vài giây rồi thoát (để kiểm thử)

Điều khiển:
    Chuột phải kéo: xoay camera   |  Lăn chuột: zoom  |  Chuột giữa kéo: di chuyển
    Space: tạm dừng   |  1/2/3: kịch bản   |  P: bảng thông số thiết kế
"""
import argparse
import math
import random as pyrandom

from ursina import (Ursina, Entity, Text, Button, EditorCamera, ThinSlider, Mesh, Vec3,
                    DirectionalLight, AmbientLight, application, camera, color, scene,
                    window, time, lerp)
from ursina.models.procedural.cylinder import Cylinder
from ursina.models.procedural.grid import Grid
from ursina.shaders import lit_with_shadows_shader, unlit_shader

import simulation as S

# ------------------------------------------------------------------ tham số dòng lệnh
ap = argparse.ArgumentParser()
ap.add_argument("--scenario", default="normal", choices=list(S.SCENARIOS))
ap.add_argument("--warp", type=int, default=0)
ap.add_argument("--shot", default="")
args = ap.parse_args()

app = Ursina(title="AeroAqua - Mô phỏng 3D (1 lưới)", borderless=False,
             development_mode=False, size=(1440, 810))
window.fps_counter.enabled = False
window.exit_button.visible = False
if hasattr(window, "cog_button"):
    window.cog_button.enabled = False

SPEEDS = [2, 10, 30, 120]            # phút mô phỏng / giây thật


def C(r, g, b, a=255):
    return color.rgba32(r, g, b, a)


class G:                              # trạng thái chung của chương trình
    sim = S.Simulation(scenario=args.scenario)
    paused = False
    speed_i = 1
    acc = 0.0
    ui_timer = 0.0
    elapsed = 0.0


for _ in range(args.warp):
    G.sim.step(1.0)

# ------------------------------------------------------------------ địa hình
RIDGE_Z = 34.0
NET_X = -4.0
NET_Z = 28.0
TANK_POS = (9.0, 17.0)               # x, z - bồn đặt cạnh lưới, thấp hơn một chút
FIELD_X = (-14.0, 14.0)
FIELD_Z = (-12.0, 9.0)


def ground(x, z):
    ridge = 16.0 * math.exp(-((z - RIDGE_Z) / 30.0) ** 2)
    bump = 1.2 * math.sin(0.09 * x + 1.0) * math.cos(0.08 * z)
    return ridge + bump * (ridge / 16.0 + 0.1)


def gp(x, z, dy=0.0):
    return Vec3(x, ground(x, z) + dy, z)


def build_terrain():
    step = 3.0
    xs = [-78 + i * step for i in range(53)]
    zs = [-60 + j * step for j in range(45)]
    verts, cols, tris = [], [], []
    sun = Vec3(-0.4, 0.8, -0.45).normalized()
    for z in zs:
        for x in xs:
            y = ground(x, z)
            verts.append((x, y, z))
            nx = ground(x - 1, z) - ground(x + 1, z)
            nz = ground(x, z - 1) - ground(x, z + 1)
            n = Vec3(nx, 2.0, nz).normalized()
            shade = 0.55 + 0.45 * max(0.0, n.dot(sun))
            t = min(1.0, y / 16.0)
            r, g, b = 70 + 50 * t, 120 + 30 * t, 62 + 20 * t      # cỏ: thấp đậm, cao nhạt
            if FIELD_X[0] - 1 <= x <= FIELD_X[1] + 1 and FIELD_Z[0] - 1 <= z <= FIELD_Z[1] + 1:
                r, g, b = 118, 92, 66                              # đất vườn
            if z < -46:
                r, g, b = 150, 140, 110                            # bờ sông
            cols.append(C(int(r * shade), int(g * shade), int(b * shade)))
    w = len(xs)
    for j in range(len(zs) - 1):
        for i in range(w - 1):
            a = j * w + i
            tris += [a, a + 1, a + w, a + 1, a + w + 1, a + w]
    return Entity(model=Mesh(vertices=verts, triangles=tris, colors=cols, mode="triangle"),
                  shader=unlit_shader)


terrain = build_terrain()
river = Entity(model="cube", position=(0, 0.25, -53), scale=(160, 0.1, 9), color=C(60, 120, 190),
               shader=unlit_shader)
sun_light = DirectionalLight(shadows=False)
sun_light.look_at(Vec3(0.4, -0.8, 0.45))
amb_light = AmbientLight(color=C(120, 125, 135))


def E3(**kw):
    """Entity 3D có chiếu sáng."""
    kw.setdefault("shader", lit_with_shadows_shader)
    return Entity(**kw)


def seg(p1, p2, th, col, **kw):
    """Một đoạn ống/thanh nối 2 điểm."""
    p1, p2 = Vec3(*p1), Vec3(*p2)
    e = E3(model="cube", position=(p1 + p2) / 2, scale=(th, th, (p2 - p1).length()), color=col, **kw)
    e.look_at(p2)
    return e


def label3d(text, pos, scale=44, col=color.white):
    return Text(parent=scene, text=text, position=pos, scale=scale, origin=(0, 0),
                billboard=True, color=col)


# ------------------------------------------------------------------ vật thể 3D
PIPE_DRY, PIPE_WET = C(120, 130, 140), C(60, 170, 255)


class UnitView:
    """Cụm duy nhất: lưới có trục xoay + cảm biến trên đỉnh + bộ điều khiển + bồn + vườn."""
    CROP = (70, 185, 80)

    def __init__(self):
        STEEL, DARK = C(200, 205, 210), C(70, 75, 82)
        # ---- lưới (theo bản CAD): đế bê tông -> hộp số -> trụ xoay -> 2 tấm lưới 3x3
        base = gp(NET_X, NET_Z)
        E3(model="cube", position=base + Vec3(0, 0.4, 0), scale=(4.8, 0.8, 3.8), color=C(150, 150, 150))
        E3(model="cube", position=base + Vec3(-0.6, 1.15, -1.15), scale=(2.2, 0.08, 1.3),
           rotation=(-25, 0, 0), color=C(25, 45, 110))                 # pin mặt trời trên đế
        E3(model="cube", position=base + Vec3(1.6, 1.4, -1.0), scale=(0.9, 1.2, 0.6),
           color=C(235, 235, 225))                                     # hộp bộ điều khiển ESP32
        self.led = Entity(model="sphere", position=base + Vec3(1.6, 1.7, -1.35), scale=0.28,
                          color=C(90, 90, 90), shader=unlit_shader)
        E3(model=Cylinder(12, radius=0.38, height=1.8), position=base + Vec3(0, 0.8, 0), color=DARK)
        E3(model=Cylinder(16, radius=0.75, height=0.5), position=base + Vec3(0, 1.9, 0), color=DARK)
        E3(model=Cylinder(18, radius=1.05, height=0.3), position=base + Vec3(0, 2.4, 0),
           color=C(105, 110, 118))                                     # bánh răng trục xoay
        E3(model="cube", position=base + Vec3(1.0, 1.75, 0.3), scale=(0.9, 0.55, 0.55),
           color=C(40, 90, 170))                                       # motor
        self.pivot = Entity(position=base + Vec3(0, 6.6, 0))
        E3(parent=self.pivot, model=Cylinder(12, radius=0.26, height=7.7), position=(0, -3.9, 0),
           color=STEEL)                                                # trụ xoay
        self.nets = []
        for side in (-1, 1):
            cx = side * 2.95
            self.nets.append(Entity(parent=self.pivot, model="quad", position=(cx, 0, 0),
                                    scale=(5.4, 6), double_sided=True, color=C(30, 95, 125, 235),
                                    shader=unlit_shader))
            Entity(parent=self.pivot, model=Grid(14, 15), position=(cx, 0, 0), scale=(5.4, 6),
                   color=C(15, 35, 45, 150), shader=unlit_shader)      # mắt lưới
            for k in range(4):                                         # khung 3x3
                E3(parent=self.pivot, model="cube", position=(cx - 2.7 + k * 1.8, 0, 0),
                   scale=(0.13, 6.1, 0.16), color=STEEL)
                E3(parent=self.pivot, model="cube", position=(cx, 3 - k * 2, 0),
                   scale=(5.5, 0.13, 0.16), color=STEEL)
        E3(parent=self.pivot, model="cube", scale=(12.3, 0.4, 0.85), position=(0, -3.35, 0),
           color=C(170, 175, 180))                                     # máng hứng
        # ---- cảm biến trên đỉnh trụ: hướng gió, tốc độ gió, nhiệt độ/độ ẩm (SHT4x)
        top = base + Vec3(0, 10.4, 0)
        black = C(40, 40, 40)
        E3(model="cube", position=top, scale=(0.08, 0.5, 0.08), color=DARK)
        E3(model="cube", position=top + Vec3(0, 0.25, 0), scale=(1.9, 0.08, 0.08), color=DARK)
        E3(model="cube", position=top + Vec3(0, 0.5, 0), scale=(0.3, 0.4, 0.3), color=C(240, 240, 240))
        self.vane = Entity(position=top + Vec3(-0.9, 0.55, 0))
        E3(parent=self.vane, model="cube", scale=(0.08, 0.08, 1.5), color=black)
        E3(parent=self.vane, model="cube", scale=(0.06, 0.5, 0.5), position=(0, 0, -0.75), color=C(220, 60, 50))
        self.cups = Entity(position=top + Vec3(0.9, 0.55, 0))
        for a in (0, 120, 240):
            arm = Entity(parent=self.cups, rotation_y=a)
            E3(parent=arm, model="cube", scale=(0.05, 0.05, 0.6), position=(0, 0, 0.3), color=black)
            E3(parent=arm, model="sphere", scale=0.28, position=(0, 0, 0.65), color=black)
        # ---- bồn nhựa xanh có gân trên bệ bê tông
        tp = gp(*TANK_POS)
        E3(model="cube", position=tp + Vec3(0, 0.15, -0.3), scale=(6.6, 0.5, 7.2), color=C(150, 150, 150))
        self.water = E3(model=Cylinder(20, radius=2.4, height=1), position=tp + Vec3(0, 0.4, 0),
                        color=C(30, 120, 235))
        E3(model=Cylinder(20, radius=2.6, height=5), position=tp + Vec3(0, 0.4, 0),
           color=C(150, 195, 255, 95))                                 # vỏ trong mờ để thấy mực nước
        for ry in (1.5, 2.9, 4.3):
            E3(model=Cylinder(20, radius=2.68, height=0.16), position=tp + Vec3(0, ry, 0),
               color=C(35, 105, 205))
        E3(model=Cylinder(20, radius=2.66, height=0.25), position=tp + Vec3(0, 5.4, 0), color=C(35, 105, 205))
        E3(model=Cylinder(16, radius=1.0, height=0.15), position=tp + Vec3(0, 5.65, 0), color=DARK)
        E3(model="cube", position=tp + Vec3(-1.3, 1.1, -3.1), scale=(0.3, 1.6, 0.3), color=C(140, 140, 140))
        E3(model="sphere", position=tp + Vec3(-1.3, 1.7, -3.35), scale=0.3, color=C(200, 50, 40))
        # ---- ống trọng lực lưới -> bồn, chạy sát đất trên trụ đỡ
        self.in_pipes = [seg(base + Vec3(0.5, 3.1, -0.3), base + Vec3(0.5, 1.0, -0.3), 0.2, PIPE_DRY),
                         seg(base + Vec3(0.5, 1.0, -0.3), base + Vec3(0.5, 1.0, -1.9), 0.2, PIPE_DRY)]
        self.in_pipes += self._ground_pipe(base + Vec3(0.5, 1.0, -1.9), tp + Vec3(0, 1.3, 2.6), 4)
        # ---- ống tưới bồn -> vườn + ống nhánh dọc đầu vườn
        head_l, head_r = gp(FIELD_X[0] + 1, FIELD_Z[1] + 0.5, 0.5), gp(FIELD_X[1] - 1, FIELD_Z[1] + 0.5, 0.5)
        mid = gp(TANK_POS[0], FIELD_Z[1] + 0.5, 0.5)
        self.out_pipes = self._ground_pipe(tp + Vec3(0, 0.9, -2.7), mid, 2, support=False)
        self.out_pipes += [seg(head_l, mid, 0.2, PIPE_DRY), seg(mid, head_r, 0.2, PIPE_DRY)]
        # ---- vườn
        self.plants = []
        rng = pyrandom.Random(1)
        for a in range(11):
            for b in range(9):
                px = FIELD_X[0] + 1.5 + a * (FIELD_X[1] - FIELD_X[0] - 3) / 10
                pz = FIELD_Z[0] + 1.5 + b * (FIELD_Z[1] - FIELD_Z[0] - 4) / 8
                h = 0.8 + rng.random() * 0.5
                self.plants.append(E3(model="cube", position=gp(px, pz, h / 2), scale=(1.5, h, 1.5)))
        self.tank_label = label3d("", tp + Vec3(0, 7.6, 0), 46)
        self.net_label = label3d("", base + Vec3(-11.5, 7.5, 0), 46, C(220, 240, 255))
        label3d("BỘ ĐIỀU KHIỂN + CẢM BIẾN", base + Vec3(10.5, 9.5, 0), 30, C(255, 235, 150))
        self.field_label = label3d("", gp(0, (FIELD_Z[0] + FIELD_Z[1]) / 2, 4.5), 44)
        self._health_shown = -1

    @staticmethod
    def _ground_pipe(p0, p1, n, support=True):
        pts = [p0]
        for k in range(1, n + 1):
            q = lerp(p0, p1, k / (n + 1))
            pts.append(gp(q.x, q.z, 0.8))
            if support:
                E3(model="cube", position=gp(q.x, q.z, 0.35), scale=(0.2, 0.9, 0.2), color=C(120, 120, 120))
        pts.append(p1)
        return [seg(a, b, 0.2, PIPE_DRY) for a, b in zip(pts, pts[1:])]

    def update(self, s, dt):
        self.pivot.rotation_y = s.net_yaw
        self.vane.rotation_y = s.wind_dir
        self.cups.rotation_y += s.wind_speed * 60 * dt
        wet = min(1.0, s.harvest_lph / 25.0)
        net_col = C(int(30 + 40 * wet), int(95 + 95 * wet), int(125 + 125 * wet), 235)
        for n in self.nets:
            n.color = net_col
        col_in = lerp(PIPE_DRY, PIPE_WET, wet)
        for p in self.in_pipes:
            p.color = col_in
        col_out = PIPE_WET if s.irrigation_lph > 0 else PIPE_DRY
        for p in self.out_pipes:
            p.color = col_out
        self.water.scale_y = max(0.02, s.frac * 4.9)
        self.led.color = C(255, 150, 30) if s.protecting else C(80, 230, 110) if s.harvest_lph > 0.5 \
            else C(90, 90, 90)
        hp = int(s.health)
        if hp != self._health_shown:
            self._health_shown = hp
            t = hp / 100.0
            col = C(int(lerp(120, self.CROP[0], t)), int(lerp(85, self.CROP[1], t)),
                    int(lerp(45, self.CROP[2], t)))
            for p in self.plants:
                p.color = col

    def update_labels(self, s):
        self.tank_label.text = f"BỒN  {s.level:.0f} L  ({s.frac * 100:.0f}%)"
        self.net_label.text = "BẢO VỆ GIÓ" if s.protecting else f"Đang thu {s.harvest_lph:.1f} L/h"
        self.net_label.color = C(255, 170, 60) if s.protecting else C(220, 240, 255)
        self.field_label.text = f"VƯỜN {S.CROP_NAME.upper()}  -  cây {s.health:.0f}%"


def soft_texture(size=64):
    """Đốm tròn mờ dần ra rìa - dùng cho hạt sương."""
    from PIL import Image
    from ursina import Texture
    img = Image.new("RGBA", (size, size))
    c = (size - 1) / 2
    px = img.load()
    for yy in range(size):
        for xx in range(size):
            d = min(1.0, math.hypot(xx - c, yy - c) / c)
            px[xx, yy] = (255, 255, 255, int(255 * (1 - d) ** 2))
    return Texture(img)


class FogView:
    def __init__(self, n=130):
        self.rng = pyrandom.Random(3)
        self.parts = []
        tex = soft_texture()
        for _ in range(n):
            e = Entity(model="quad", texture=tex, billboard=True, shader=unlit_shader,
                       scale=self.rng.uniform(10, 20), color=C(235, 240, 245, 0))
            e.fx = self.rng.uniform(-70, 70)
            e.fz = self.rng.uniform(-45, 55)
            e.fh = self.rng.uniform(2.5, 11)
            e.k = self.rng.uniform(0.6, 1.3)
            self.parts.append(e)

    def update(self, sim, dt):
        dens = sim.lwc / S.LWC_MAX
        a = math.radians(sim.wind_dir)
        v = sim.wind_speed * 0.9 * dt
        dx, dz = math.sin(a) * v, math.cos(a) * v
        alpha = int(95 * dens)
        for e in self.parts:
            e.fx += dx * e.k
            e.fz += dz * e.k
            if e.fx > 70: e.fx -= 140
            if e.fx < -70: e.fx += 140
            if e.fz > 55: e.fz -= 100
            if e.fz < -45: e.fz += 100
            e.position = (e.fx, ground(e.fx, e.fz) + e.fh, e.fz)
            e.color = C(235, 240, 245, alpha)


unit_view = UnitView()
fog_view = FogView()

# ------------------------------------------------------------------ camera
cam = EditorCamera(rotation=(37, 0, 0))
cam.position = (0, 2, 15.5)
cam.target_z = -158
camera.z = -158

# ------------------------------------------------------------------ GIAO DIỆN (HUD)
UI = camera.ui
PANEL = C(12, 22, 34, 215)
TXT = C(230, 238, 245)
DIM = C(150, 165, 180)
ACCENT = C(80, 220, 235)


def panel(x0, y0, x1, y1):
    return Entity(parent=UI, model="quad", color=PANEL, z=1,
                  position=((x0 + x1) / 2, (y0 + y1) / 2), scale=(x1 - x0, y0 - y1))


def T(text, x, y, scale=0.8, col=TXT, origin=(-0.5, 0.5)):
    t = Text(parent=UI, text="", position=(x, y), scale=scale, origin=origin, color=col, z=-1)
    t.use_tags = False
    t.text = text
    return t


def bar(x, y, w, h, col):
    Entity(parent=UI, model="quad", origin=(-0.5, 0), position=(x, y), scale=(w, h),
           color=C(50, 62, 76), z=0.5)
    return Entity(parent=UI, model="quad", origin=(-0.5, 0), position=(x, y), scale=(w, h), color=col, z=0)


def btn(text, x, y, w, h, fn, col=C(38, 70, 100)):
    b = Button(parent=UI, text=text, position=(x + w / 2, y), scale=(w, h), color=col,
               highlight_color=C(60, 110, 150), pressed_color=C(30, 50, 70), text_size=0.68)
    b.on_click = fn
    return b


def set_text(t, s):
    if t.text != s:
        t.text = s


# ---- thanh trên cùng
panel(-0.16, 0.5, 0.16, 0.446)
clock_text = T("", 0, 0.496, 0.95, color.white, origin=(0, 0.5))
mode_text = T("", 0, 0.468, 0.6, ACCENT, origin=(0, 0.5))
T("Chuột phải: xoay  ·  Lăn chuột: zoom  ·  Space: dừng",
  -0.62, -0.319, 0.55, DIM)

# ---- bảng trái: cảm biến + điều khiển
LX = -0.875
panel(-0.889, 0.5, -0.555, -0.3)
T("BỘ ĐIỀU KHIỂN  ·  Cảm biến", LX, 0.488, 0.95, ACCENT)
env_text = T("", LX, 0.452, 0.8)
T("ĐIỀU KHIỂN MÔ PHỎNG", LX, 0.222, 0.8, ACCENT)
BW, BH = 0.15, 0.034


def toggle_pause():
    G.paused = not G.paused
    refresh_buttons()


def cycle_speed():
    G.speed_i = (G.speed_i + 1) % len(SPEEDS)
    refresh_buttons()


def toggle_rotate():
    s = G.sim
    s.auto_rotate = not s.auto_rotate
    s.log("Người dùng " + ("BẬT trục xoay tự động" if s.auto_rotate else "TẮT trục xoay (lưới cố định)"), "user")
    refresh_buttons()


def set_scenario(name):
    G.sim.scenario = name
    G.sim.log(f"Chuyển kịch bản: {S.SCENARIOS[name]['label']}", "user")
    refresh_buttons()


def reset_sim():
    old = G.sim
    G.sim = S.Simulation(scenario=old.scenario, auto_rotate=old.auto_rotate, user_d_rh=old.user_d_rh,
                         user_wind_mul=old.user_wind_mul, user_wind_dir=old.user_wind_dir)
    G.acc = 0.0
    refresh_buttons()


b_pause = btn("", LX, 0.172, BW, BH, toggle_pause)
b_speed = btn("", LX + BW + 0.012, 0.172, BW, BH, cycle_speed)
b_rot = btn("", LX, 0.130, BW, BH, toggle_rotate)
b_reset = btn("Làm lại từ đầu", LX + BW + 0.012, 0.130, BW, BH, reset_sim, C(110, 55, 55))
T("Kịch bản thời tiết:", LX, 0.1, 0.75, DIM)
SW = 0.1
b_scen = {
    "normal": btn("Bình thường", LX, 0.058, SW, BH, lambda: set_scenario("normal")),
    "drought": btn("Hạn El Niño", LX + SW + 0.006, 0.058, SW, BH, lambda: set_scenario("drought")),
    "storm": btn("Gió bão", LX + 2 * (SW + 0.006), 0.058, SW, BH, lambda: set_scenario("storm")),
}


def make_slider(label, y, lo, hi, default, step, setter, x=LX, group=None):
    lab = T(label, x, y, 0.72, DIM if group is None else TXT)
    s = ThinSlider(min=lo, max=hi, default=default, step=step, dynamic=True,
                   position=(x + 0.012, y - 0.042), scale=0.56)
    if group is not None:
        lab.z = -3
        s.z = -3
        group += [lab, s]
    s.on_value_changed = lambda: setter(s.value)
    return s


def _set_rh(v): G.sim.user_d_rh = v
def _set_wind(v): G.sim.user_wind_mul = v
def _set_dir(v): G.sim.user_wind_dir = v


make_slider("Chỉnh độ ẩm (cộng thêm % RH):", 0.02, -10, 5, 0, 0.5, _set_rh)
make_slider("Hệ số tốc độ gió (x):", -0.065, 0.2, 4, 1, 0.1, _set_wind)
make_slider("Hướng gió (độ, 0 = thổi lên sống núi):", -0.15, -180, 180, 0, 5, _set_dir)

# ---- bảng THÔNG SỐ THIẾT KẾ (bật/tắt bằng nút hoặc phím P)
# Mỗi dòng: (nhãn, tên hằng số trong simulation.py, min, max, bước, hệ số hiển thị)
PARAMS = [
    ("Ngưỡng gió xoay bảo vệ lưới (m/s)", "WIND_PROTECT_ON", 6, 25, 0.5, 1),
    ("Tốc độ trục xoay (độ/phút)", "NET_TURN_RATE", 0.5, 10, 0.5, 1),
    ("Diện tích lưới (m2)", "NET_AREA", 10, 80, 2, 1),
    ("Hiệu suất thu của lưới (%)", "NET_EFFICIENCY", 5, 40, 1, 100),
    ("Dung tích bồn (L)", "TANK_CAPACITY", 200, 2000, 50, 1),
    ("Nhu cầu tưới của vườn (L/ngày)", "DEMAND_PER_DAY", 50, 400, 10, 1),
]
PARAM_DEFAULTS = {p[1]: getattr(S, p[1]) for p in PARAMS}
param_group = []
param_sliders = {}


def set_param(name, shown, k):
    v = shown / k
    setattr(S, name, v)
    if name == "WIND_PROTECT_ON":
        S.WIND_PROTECT_OFF = v - 2.0            # ngưỡng quay lại luôn thấp hơn 2 m/s
    if name == "TANK_CAPACITY":
        G.sim.level = min(G.sim.level, v)


def reset_params():
    for p in PARAMS:
        name, k = p[1], p[5]
        param_sliders[name].value = PARAM_DEFAULTS[name] * k
        set_param(name, PARAM_DEFAULTS[name] * k, k)
    G.sim.log("Người dùng đưa thông số thiết kế về mặc định", "user")


def toggle_params():
    show = not param_group[0].enabled
    for e in param_group:
        e.enabled = show


def build_param_panel():
    bg = panel(-0.5, 0.39, 0.48, -0.03)
    bg.z = -2
    bg.color = C(10, 18, 28, 240)
    title = T("THÔNG SỐ THIẾT KẾ  ·  thay đổi có hiệu lực ngay", -0.48, 0.375, 0.9, ACCENT)
    note = T("Ngưỡng quay lại đón sương = ngưỡng bảo vệ - 2 m/s.  Phím P: đóng/mở bảng này.",
             -0.48, 0.0, 0.62, DIM)
    title.z = note.z = -3
    param_group.extend([bg, title, note])
    for n, (label, name, lo, hi, step, k) in enumerate(PARAMS):
        x = -0.48 if n % 2 == 0 else 0.02
        y = 0.32 - (n // 2) * 0.095
        param_sliders[name] = make_slider(label + ":", y, lo, hi, PARAM_DEFAULTS[name] * k, step,
                                          lambda v, name=name, k=k: set_param(name, v, k),
                                          x=x, group=param_group)
    b1 = btn("Về mặc định", 0.14, 0.012, 0.15, BH, reset_params, C(110, 55, 55))
    b2 = btn("Đóng", 0.31, 0.012, 0.15, BH, toggle_params)
    b1.z = b2.z = -3
    param_group.extend([b1, b2])
    for e in param_group:
        e.enabled = False


build_param_panel()
btn("Chỉnh thông số thiết kế (P)", LX, -0.265, 2 * BW + 0.012, BH, toggle_params, C(120, 90, 30))


def refresh_buttons():
    s = G.sim
    b_pause.text = "Tiếp tục" if G.paused else "Tạm dừng"
    b_speed.text = f"Tốc độ: x{SPEEDS[G.speed_i]}"
    b_rot.text = "Trục xoay: " + ("BẬT" if s.auto_rotate else "TẮT")
    b_rot.color = C(30, 120, 90) if s.auto_rotate else C(120, 60, 50)
    for name, b in b_scen.items():
        b.color = C(30, 120, 150) if name == s.scenario else C(38, 70, 100)


# ---- bảng phải: bồn + vườn + kết quả
RX = 0.555
BARW = 0.324
panel(0.54, 0.5, 0.889, -0.5)
tank_title = T("", RX, 0.488, 0.95, color.white)
wbar = bar(RX, 0.432, BARW, 0.03, C(40, 140, 235))
low_tick = Entity(parent=UI, model="quad", color=C(255, 255, 255, 200), z=-0.5,
                  position=(RX, 0.432), scale=(0.002, 0.036))
wlabel = T("", RX + BARW / 2, 0.432, 0.78, color.white, origin=(0, 0))
T("Vạch trắng: mức cảnh báo bồn thấp", RX, 0.408, 0.56, DIM)
flow_text = T("", RX, 0.376, 0.8)
status_text = T("", RX, 0.288, 0.85)
Entity(parent=UI, model="quad", color=C(70, 90, 110), z=0.5, position=(RX + BARW / 2, 0.245),
       scale=(BARW, 0.0015))
field_title = T("", RX, 0.232, 0.95, color.white)
hbar = bar(RX, 0.182, BARW, 0.018, C(80, 200, 90))
hlabel = T("", RX + BARW / 2, 0.182, 0.7, color.white, origin=(0, 0))
field_text = T("", RX, 0.162, 0.8)
Entity(parent=UI, model="quad", color=C(70, 90, 110), z=0.5, position=(RX + BARW / 2, 0.07),
       scale=(BARW, 0.0015))
T("KẾT QUẢ TÍCH LUỸ", RX, 0.055, 0.85, ACCENT)
stats_text = T("", RX, 0.02, 0.8)

# ---- nhật ký sự kiện
panel(-0.889, -0.31, 0.53, -0.5)
T("NHẬT KÝ BỘ ĐIỀU KHIỂN", LX, -0.318, 0.75, ACCENT)
LOG_N = 6
log_lines = [T("", LX, -0.345 - k * 0.0245, 0.72) for k in range(LOG_N)]
LOG_COL = {"info": TXT, "warn": C(255, 190, 80), "bad": C(255, 100, 90), "good": C(110, 225, 140),
           "sum": C(90, 225, 255), "user": C(190, 160, 255)}

# ---- biểu đồ DỰ BÁO THU SƯƠNG 24 giờ tới (nửa phải của bảng dưới)
FC_X0, FC_PITCH, FC_BASE, FC_MAXH = -0.125, 0.0155, -0.462, 0.092
Entity(parent=UI, model="quad", color=C(70, 90, 110), z=0.5, position=(-0.148, -0.405), scale=(0.0015, 0.17))
T("DỰ BÁO THU SƯƠNG 24 GIỜ TỚI  ·  L mỗi giờ", -0.135, -0.318, 0.68, ACCENT)
Entity(parent=UI, model="quad", color=C(110, 125, 140), z=0.2, origin=(-0.5, 0),
       position=(FC_X0 - 0.006, FC_BASE), scale=(S.FORECAST_HOURS * FC_PITCH, 0.0012))
fc_bars = [Entity(parent=UI, model="quad", origin=(0, -0.5), color=C(70, 160, 240), z=0,
                  position=(FC_X0 + j * FC_PITCH, FC_BASE), scale=(0.0125, 0.0005))
           for j in range(S.FORECAST_HOURS)]
fc_ticks = [T("", FC_X0 + j * FC_PITCH, FC_BASE - 0.005, 0.5, DIM, origin=(0, 0.5))
            for j in range(0, S.FORECAST_HOURS, 3)]
fc_peak = T("", 0, 0, 0.55, TXT, origin=(0, -0.5))
fc_text = T("", 0.262, -0.345, 0.62)


def update_forecast_ui(s):
    fc = s.fc
    top = max(max(fc.hourly), 2.0)
    for j, b in enumerate(fc_bars):
        b.scale_y = max(0.0005, FC_MAXH * fc.hourly[j] / top)
    jmax = max(range(S.FORECAST_HOURS), key=lambda j: fc.hourly[j])
    if fc.hourly[jmax] >= 0.5:                  # chỉ ghi số trên cột cao nhất
        set_text(fc_peak, f"{fc.hourly[jmax]:.0f} L")
        fc_peak.position = (FC_X0 + jmax * FC_PITCH, FC_BASE + FC_MAXH * fc.hourly[jmax] / top + 0.003)
    else:
        set_text(fc_peak, "")
    h0 = int(s.hour)
    for k, t in enumerate(fc_ticks):
        set_text(t, "nay" if k == 0 else f"{(h0 + 3 * k) % 24:02d}h")
    cnt, mae, pct = fc.accuracy()
    acc = f"Sai số 6h: {pct:.0f}% (TB {mae:.0f} L, {cnt} lần)" if cnt else "Sai số 6h: chưa có (cần 6 giờ)"
    bal = s.balance_24h()
    set_text(fc_text,
             f"Tổng 6 giờ tới:  {fc.total_6h:.0f} L\n"
             f"Tổng 24 giờ tới:  {fc.total_24h:.0f} L\n"
             f"Bồn sau 24h (ước):  {'dư' if bal >= 0 else 'THIẾU'} {abs(bal):.0f} L\n"
             f"Hệ số lưới đã học:  {fc.k():.1f}\n"
             f"{acc}\n"
             f"Dữ liệu đã học:  {fc.minutes_learned / 1440:.1f} ngày")


def wind_name(deg):
    names = ["lên sống núi", "chéo phải lên", "sang phải", "chéo phải xuống",
             "xuống thung lũng", "chéo trái xuống", "sang trái", "chéo trái lên"]
    return names[int(((deg % 360) + 22.5) // 45) % 8]


def update_ui():
    s = G.sim
    set_text(clock_text, s.clock())
    update_forecast_ui(s)
    state = "TẠM DỪNG" if G.paused else f"x{SPEEDS[G.speed_i]} phút/giây"
    set_text(mode_text, f"{S.SCENARIOS[s.scenario]['label']}  ·  {state}")
    fog = "không sương" if s.lwc <= 0 else ("sương mỏng" if s.lwc < 0.15 else "sương dày")
    if s.protecting:
        net = "XOAY NGHIÊNG BẢO VỆ"
    elif not s.auto_rotate:
        net = "cố định (thụ động)"
    else:
        net = "bám theo hướng gió"
    facing = abs(math.cos(math.radians(S.ang_diff(s.wind_dir, s.net_yaw))))
    set_text(env_text,
             f"Nhiệt độ (SHT4x):  {s.temp:.1f} °C\n"
             f"Độ ẩm (SHT4x):  {s.rh:.1f} %\n"
             f"Tốc độ gió:  {s.wind_speed:.1f} m/s\n"
             f"Hướng gió:  {s.wind_dir:.0f}°  ({wind_name(s.wind_dir)})\n"
             f"Sương (LWC):  {s.lwc:.2f} g/m³  -  {fog}\n"
             f"Lưới:  {net}\n"
             f"Góc đón gió của lưới:  {facing * 100:.0f}%\n"
             f"Ngưỡng gió bảo vệ:  {S.WIND_PROTECT_ON:.1f} m/s")
    # bồn
    set_text(tank_title, f"BỒN CHỨA · {S.TANK_CAPACITY:.0f} L")
    wbar.scale_x = max(0.0005, BARW * s.frac)
    wbar.color = C(230, 80, 60) if s.frac < S.TANK_LOW else C(40, 140, 235)
    low_tick.x = RX + BARW * S.TANK_LOW
    set_text(wlabel, f"{s.level:.0f} / {S.TANK_CAPACITY:.0f} L  ({s.frac * 100:.0f}%)")
    set_text(flow_text,
             f"Nước vào (thu sương):  {s.harvest_lph:.1f} L/h\n"
             f"Nước ra (tưới vườn):  {s.irrigation_lph:.1f} L/h\n"
             f"Dự báo thu 6h tới:  {s.fc.total_6h:.0f} L")
    if s.protecting:
        st, col = "Lưới đang xoay nghiêng tránh gió", C(255, 165, 60)
    elif s.harvest_lph > 0.5:
        st, col = "Đang thu sương vào bồn", C(110, 225, 140)
    elif s.irrigation_lph > 0:
        st, col = "Đang tưới vườn", C(90, 225, 255)
    else:
        st, col = "Chờ sương", DIM
    set_text(status_text, st)
    status_text.color = col
    # vườn
    set_text(field_title, f"VƯỜN · {S.CROP_NAME}")
    hbar.scale_x = max(0.0005, BARW * s.health / 100)
    hbar.color = C(80, 200, 90) if s.health > 60 else C(230, 190, 60) if s.health > 25 else C(230, 80, 60)
    set_text(hlabel, f"Sức khoẻ cây: {s.health:.0f}%" if s.health > 0 else "CÂY ĐÃ CHẾT")
    set_text(field_text,
             f"Nhu cầu tưới:  {S.DEMAND_PER_DAY:.0f} L/ngày\n"
             f"Giờ tưới:  {S.IRRIGATION_HOURS[0]}h - {S.IRRIGATION_HOURS[1]}h")
    unit_view.update_labels(s)
    st = s.stats()
    set_text(stats_text,
             f"Nước thu từ sương:  {st['harvested']:.0f} L\n"
             f"Nước đã tưới:  {st['irrigated']:.0f} L\n"
             f"Tràn bồn (lãng phí):  {st['overflow']:.0f} L\n"
             f"Thiếu nước tưới:  {st['unmet']:.0f} L\n"
             f"Lưới hứng gió quá tải:  {st['overload_hours']:.1f} giờ\n"
             f"Điện tiết kiệm so với\nbơm từ sông:  {st['river_wh']:.0f} Wh")
    ev = s.events[-LOG_N:]
    for k, line in enumerate(log_lines):
        if k < len(ev):
            t, text, kind = ev[k]
            hh = (t / 60) % 24
            set_text(line, f"[N{int(t // 1440) + 1} {int(hh):02d}:{int(hh * 60) % 60:02d}]  {text}")
            line.color = LOG_COL.get(kind, TXT)
        else:
            set_text(line, "")


def update_sky():
    s = G.sim
    day = max(0.0, min(1.0, 0.5 + 0.75 * math.sin(2 * math.pi * (s.hour - 6) / 24)))
    fog = s.lwc / S.LWC_MAX
    sky = lerp(C(24, 32, 56), C(140, 190, 232), day)
    sky = lerp(sky, lerp(C(70, 78, 92), C(205, 212, 218), day), fog * 0.85)
    window.color = sky
    b = 0.5 + 0.5 * day
    terrain.color = color.rgba(b, b, b * 1.02, 1)
    river.color = lerp(C(30, 60, 110), C(60, 120, 190), day)
    sun_light.color = color.rgba(0.35 + 0.55 * day, 0.35 + 0.55 * day, 0.4 + 0.45 * day, 1)
    amb_light.color = color.rgba(0.3 + 0.2 * day, 0.31 + 0.2 * day, 0.36 + 0.17 * day, 1)


# ------------------------------------------------------------------ vòng lặp chính
def update():
    dt = min(time.dt, 0.1)
    s = G.sim
    G.elapsed += dt
    if not G.paused:
        G.acc += dt * SPEEDS[G.speed_i]
        steps = min(int(G.acc), 240)
        G.acc -= int(G.acc)
        for _ in range(steps):
            s.step(1.0)
    anim_dt = 0.0 if G.paused else dt
    unit_view.update(s, anim_dt)
    fog_view.update(s, anim_dt)
    update_sky()
    G.ui_timer += dt
    if G.ui_timer >= 0.15:
        G.ui_timer = 0.0
        update_ui()
    if args.shot and G.elapsed > 4.0:
        from panda3d.core import Filename
        application.base.graphicsEngine.renderFrame()
        application.base.win.saveScreenshot(Filename.fromOsSpecific(args.shot))
        application.quit()


def input(key):
    if key == "space":
        toggle_pause()
    elif key == "1":
        set_scenario("normal")
    elif key == "2":
        set_scenario("drought")
    elif key == "3":
        set_scenario("storm")
    elif key == "p":
        toggle_params()


refresh_buttons()
update_ui()
if args.shot and args.shot.endswith("_params.png"):      # kiểm thử: chụp kèm bảng thông số
    toggle_params()
app.run()
