"""
AeroAqua Smart Mesh - MÔ PHỎNG 3D (bản demo)
============================================
Chạy:   python main.py
Tuỳ chọn:
    --scenario normal|drought|storm   kịch bản lúc mở
    --warp 600                        tua trước N phút mô phỏng rồi mới hiển thị
    --shot out.png                    chụp 1 ảnh sau vài giây rồi thoát (để kiểm thử)

Điều khiển:
    Chuột phải kéo: xoay camera   |  Lăn chuột: zoom  |  Chuột giữa kéo: di chuyển
    Space: tạm dừng   |  1/2/3: kịch bản   |  Các nút và thanh trượt ở bảng bên trái
"""
import argparse
import math
import random as pyrandom

from ursina import (Ursina, Entity, Text, Button, EditorCamera, ThinSlider, Mesh, Vec3,
                    DirectionalLight, AmbientLight, application, camera, color, scene,
                    window, time, destroy, lerp)
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

app = Ursina(title="AeroAqua Smart Mesh - Mô phỏng 3D", borderless=False,
             development_mode=False, size=(1440, 810))
window.fps_counter.enabled = False
window.exit_button.visible = False
if hasattr(window, "cog_button"):
    window.cog_button.enabled = False

USE_LIGHTING = True
SPEEDS = [2, 10, 30, 120]            # phút mô phỏng / giây thật
LOSS_STEPS = [0.0, 0.02, 0.2, 0.5]


def C(r, g, b, a=255):
    return color.rgba32(r, g, b, a)


class G:                              # trạng thái chung của chương trình
    sim = S.Simulation(scenario=args.scenario)
    paused = False
    speed_i = 1
    acc = 0.0
    ui_timer = 0.0
    frame = 0
    elapsed = 0.0


for _ in range(args.warp):
    G.sim.step(1.0)
G.sim.packets.clear()

# ------------------------------------------------------------------ địa hình
ZONE_X = [-25.0, 0.0, 25.0]
RIDGE_Z = 34.0
TANK_Z = [12.0, 6.0, 12.0]
FIELD_Z = (-24.0, 0.0)
FIELD_HALF_W = 9.5
MASTER_POS = (0.0, 22.0)             # x, z


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
            for zx in ZONE_X:                                      # đất ruộng
                if abs(x - zx) <= FIELD_HALF_W + 1 and FIELD_Z[0] - 1 <= z <= FIELD_Z[1] + 1:
                    r, g, b = 118, 92, 66
            if z < -46:                                            # bờ sông
                r, g, b = 150, 140, 110
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

if USE_LIGHTING:
    sun_light = DirectionalLight(shadows=False)
    sun_light.look_at(Vec3(0.4, -0.8, 0.45))
    amb_light = AmbientLight(color=C(120, 125, 135))


def E3(**kw):
    """Entity 3D có chiếu sáng."""
    kw.setdefault("shader", lit_with_shadows_shader if USE_LIGHTING else unlit_shader)
    return Entity(**kw)


def seg(p1, p2, th, col, **kw):
    """Một đoạn ống/thanh nối 2 điểm."""
    p1, p2 = Vec3(*p1), Vec3(*p2)
    e = E3(model="cube", position=(p1 + p2) / 2, scale=(th, th, (p2 - p1).length()), color=col, **kw)
    e.look_at(p2)
    return e


def label3d(text, pos, scale=50, col=color.white):
    return Text(parent=scene, text=text, position=pos, scale=scale, origin=(0, 0),
                billboard=True, color=col)


# ------------------------------------------------------------------ vật thể 3D
def wind_sensors(vane_pos, cups_pos):
    """Chong chóng hướng gió + gáo đo tốc độ gió. Trả về 2 entity để xoay."""
    black = C(40, 40, 40)
    vane = Entity(position=vane_pos)
    E3(parent=vane, model="cube", scale=(0.08, 0.08, 1.5), color=black)
    E3(parent=vane, model="cube", scale=(0.06, 0.5, 0.5), position=(0, 0, -0.75), color=C(220, 60, 50))
    cups = Entity(position=cups_pos)
    for a in (0, 120, 240):
        arm = Entity(parent=cups, rotation_y=a)
        E3(parent=arm, model="cube", scale=(0.05, 0.05, 0.6), position=(0, 0, 0.3), color=black)
        E3(parent=arm, model="sphere", scale=0.28, position=(0, 0, 0.65), color=black)
    return vane, cups


class ZoneView:
    """Lưới thu sương + bồn (slave node) + ruộng của 1 vùng."""
    CROP_COL = [(70, 185, 80), (35, 125, 65), (140, 205, 75)]

    def __init__(self, i):
        self.i = i
        x = ZONE_X[i]
        # ---- lưới thu sương trên sống núi
        # Kết cấu theo bản CAD: đế bê tông (pin mặt trời đặt trên đế) -> hộp số bánh
        # răng -> trụ xoay -> 2 tấm lưới 3x3 hai bên trụ -> máng hứng -> cảm biến gió
        # trên đỉnh trụ.
        base = gp(x, RIDGE_Z)
        STEEL, DARK = C(200, 205, 210), C(70, 75, 82)
        E3(model="cube", position=base + Vec3(0, 0.4, 0), scale=(4.8, 0.8, 3.8), color=C(150, 150, 150))
        E3(model="cube", position=base + Vec3(-0.2, 1.15, -1.15), scale=(2.2, 0.08, 1.3),
           rotation=(-25, 0, 0), color=C(25, 45, 110))                 # pin mặt trời trên đế
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
        # cảm biến gió trên đỉnh trụ (đứng yên theo hướng thế giới, không xoay theo lưới)
        top = base + Vec3(0, 10.4, 0)
        E3(model="cube", position=top, scale=(0.08, 0.5, 0.08), color=DARK)
        E3(model="cube", position=top + Vec3(0, 0.25, 0), scale=(1.9, 0.08, 0.08), color=DARK)
        self.vane, self.cups = wind_sensors(top + Vec3(-0.9, 0.55, 0), top + Vec3(0.9, 0.55, 0))
        # ---- bồn chứa (Slave Node): bồn nhựa xanh có gân, đặt trên bệ bê tông
        self.tank_pos = gp(x, TANK_Z[i])
        tp = self.tank_pos
        E3(model="cube", position=tp + Vec3(0, 0.15, -0.3), scale=(6.6, 0.5, 7.2), color=C(150, 150, 150))
        self.water = E3(model=Cylinder(20, radius=2.4, height=1), position=tp + Vec3(0, 0.4, 0),
                        color=C(30, 120, 235))
        E3(model=Cylinder(20, radius=2.6, height=5), position=tp + Vec3(0, 0.4, 0),
           color=C(150, 195, 255, 95))                                 # vỏ bồn trong mờ để thấy mực nước
        for ry in (1.5, 2.9, 4.3):                                     # gân bồn
            E3(model=Cylinder(20, radius=2.68, height=0.16), position=tp + Vec3(0, ry, 0),
               color=C(35, 105, 205))
        E3(model=Cylinder(20, radius=2.66, height=0.25), position=tp + Vec3(0, 5.4, 0), color=C(35, 105, 205))
        E3(model=Cylinder(16, radius=1.0, height=0.15), position=tp + Vec3(0, 5.65, 0), color=DARK)  # nắp
        E3(model="cube", position=tp + Vec3(-1.3, 1.1, -3.1), scale=(0.3, 1.6, 0.3), color=C(140, 140, 140))
        E3(model="sphere", position=tp + Vec3(-1.3, 1.7, -3.35), scale=0.3, color=C(200, 50, 40))  # vòi
        # hộp điều khiển + pin mặt trời + anten LoRa
        self.node_pos = tp + Vec3(3.4, 3.0, 0)
        E3(model="cube", position=self.node_pos, scale=(0.9, 1.2, 0.6), color=C(225, 225, 215))
        E3(model="cube", position=tp + Vec3(3.4, 1.3, 0), scale=(0.15, 2.6, 0.15), color=C(120, 120, 120))
        E3(model="cube", position=tp + Vec3(3.4, 4.3, 0), scale=(1.8, 0.08, 1.2), rotation=(-30, 0, 0),
           color=C(25, 45, 110))
        E3(model="cube", position=tp + Vec3(3.9, 4.2, 0), scale=(0.05, 1.4, 0.05), color=C(30, 30, 30))
        self.pump_led = Entity(model="sphere", position=self.node_pos + Vec3(0, 0, -0.4), scale=0.35,
                               color=C(90, 90, 90), shader=unlit_shader)
        # ống trọng lực: lưới -> bồn
        # ống chạy sát đất trên các trụ đỡ, như bản CAD
        self.gravity_pipes = [seg(base + Vec3(0.5, 3.1, -0.3), base + Vec3(0.5, 1.0, -0.3), 0.2,
                                  C(120, 130, 140))]
        p0, p1 = base + Vec3(0.5, 1.0, -1.9), tp + Vec3(0, 1.3, 2.6)
        pts = [p0]
        for k in range(1, 5):
            q = lerp(p0, p1, k / 5)
            pts.append(gp(q.x, q.z, 0.8))
            E3(model="cube", position=gp(q.x, q.z, 0.35), scale=(0.2, 0.9, 0.2), color=C(120, 120, 120))
        pts.append(p1)
        self.gravity_pipes.append(seg(base + Vec3(0.5, 1.0, -0.3), p0, 0.2, C(120, 130, 140)))
        for a, b in zip(pts, pts[1:]):
            self.gravity_pipes.append(seg(a, b, 0.2, C(120, 130, 140)))
        # ---- ruộng
        self.plants = []
        rng = pyrandom.Random(i)
        for a in range(8):
            for b in range(7):
                px = x - FIELD_HALF_W + 1.5 + a * (2 * FIELD_HALF_W - 3) / 7
                pz = FIELD_Z[0] + 1.5 + b * (FIELD_Z[1] - FIELD_Z[0] - 3) / 6
                h = 0.8 + rng.random() * 0.5
                self.plants.append(E3(model="cube", position=gp(px, pz, h / 2), scale=(1.5, h, 1.5)))
        self.label = label3d("", tp + Vec3(0, 7.8, 0), 52)
        self.net_label = label3d("", base + Vec3(0, 12.4, 0), 62, C(220, 240, 255))
        self._health_shown = -1

    def update(self, z: S.Zone, sim, dt):
        self.pivot.rotation_y = z.net_yaw
        self.vane.rotation_y = sim.wind_dir
        self.cups.rotation_y += sim.wind_speed * 60 * dt
        wet = min(1.0, z.harvest_lph / 25.0)
        net_col = C(int(30 + 40 * wet), int(95 + 95 * wet), int(135 + 115 * wet), 235)
        for n in self.nets:
            n.color = net_col
        pipe_col = lerp(C(120, 130, 140), C(60, 170, 255), wet)
        for p in self.gravity_pipes:
            p.color = pipe_col
        self.water.scale_y = max(0.02, z.frac * 4.9)
        if z.pump_to is not None:
            self.pump_led.color = C(255, 150, 30)
        elif z.receiving_from is not None:
            self.pump_led.color = C(60, 220, 255)
        else:
            self.pump_led.color = C(90, 90, 90)
        hp = int(z.health)
        if hp != self._health_shown:
            self._health_shown = hp
            g = self.CROP_COL[self.i]
            t = hp / 100.0
            col = C(int(lerp(120, g[0], t)), int(lerp(85, g[1], t)), int(lerp(45, g[2], t)))
            for p in self.plants:
                p.color = col

    def update_labels(self, z: S.Zone):
        self.label.text = f"BỒN {z.name}  w={z.weight}  {z.frac * 100:.0f}%"
        self.net_label.text = "BẢO VỆ GIÓ" if z.protecting else f"{z.harvest_lph:.1f} L/h"
        self.net_label.color = C(255, 170, 60) if z.protecting else C(220, 240, 255)


class MasterView:
    def __init__(self):
        p = gp(*MASTER_POS)
        E3(model="cube", position=p + Vec3(0, 0.3, 0), scale=(2, 0.6, 2), color=C(150, 150, 150))
        E3(model=Cylinder(10, radius=0.15, height=8), position=p, color=C(190, 190, 195))
        self.box_pos = p + Vec3(0, 4.2, 0)
        E3(model="cube", position=self.box_pos, scale=(1.6, 2.0, 1.0), color=C(235, 235, 225))
        E3(model="cube", position=p + Vec3(0, 6.2, -0.8), scale=(2.6, 0.1, 1.7), rotation=(-35, 0, 0),
           color=C(25, 45, 110))
        # Cảm biến gió đã chuyển lên đỉnh trụ lưới; Master chỉ còn hộp ESP32 + anten LoRa
        E3(model="cube", position=p + Vec3(0, 8.6, 0), scale=(0.07, 1.4, 0.07), color=C(30, 30, 30))
        E3(model="sphere", position=p + Vec3(0, 9.3, 0), scale=0.28, color=C(230, 60, 50))
        self.top = p + Vec3(0, 9.4, 0)
        label3d("MASTER NODE", p + Vec3(0, 10.6, 0), 46, C(255, 235, 150))

    def update(self, sim, dt):
        pass


class PipeView:
    """Ống nối 2 bồn + giọt nước chạy khi đang bơm."""
    def __init__(self, a, b, zv):
        self.a, self.b = a, b
        self.pa = zv[a].tank_pos + Vec3(0, 1.0, -2.8)
        self.pb = zv[b].tank_pos + Vec3(0, 1.0, -2.8)
        self.pipe = seg(self.pa, self.pb, 0.3, C(110, 110, 115))
        self.drops = [Entity(model="sphere", scale=0.7, color=C(80, 220, 255), enabled=False,
                             shader=unlit_shader) for _ in range(5)]
        self.phase = 0.0

    def update(self, zones, dt):
        direction = 0
        if zones[self.a].pump_to == self.b:
            direction = 1
        elif zones[self.b].pump_to == self.a:
            direction = -1
        self.pipe.color = C(60, 200, 255) if direction else C(110, 110, 115)
        self.phase = (self.phase + dt * 0.5) % 1.0
        for k, d in enumerate(self.drops):
            d.enabled = direction != 0
            if direction:
                t = (self.phase + k / len(self.drops)) % 1.0
                if direction < 0:
                    t = 1 - t
                d.position = lerp(self.pa, self.pb, t) + Vec3(0, 0.3, 0)


class PacketView:
    """Hoạt ảnh gói tin LoRa."""
    active = []

    def __init__(self, p1, p2, kind, lost):
        self.p1, self.p2, self.lost = Vec3(*p1), Vec3(*p2), lost
        self.t = 0.0
        self.base_col = C(255, 230, 60) if kind == "telemetry" else C(255, 90, 230)
        self.e = Entity(model="sphere", scale=0.8, color=self.base_col, shader=unlit_shader)
        PacketView.active.append(self)

    def update(self, dt):
        self.t += dt / 0.8
        if self.lost and self.t > 0.45:
            self.e.color = C(255, 40, 40)
            self.e.scale = 0.8 + (self.t - 0.45) * 4
        if self.t >= (0.7 if self.lost else 1.0):
            destroy(self.e)
            PacketView.active.remove(self)
            return
        pos = lerp(self.p1, self.p2, self.t)
        pos.y += math.sin(self.t * math.pi) * 5.0
        self.e.position = pos


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


zone_views = [ZoneView(i) for i in range(3)]
master_view = MasterView()
pipe_views = [PipeView(0, 1, zone_views), PipeView(1, 2, zone_views), PipeView(0, 2, zone_views)]
fog_view = FogView()

# ------------------------------------------------------------------ camera
cam = EditorCamera(rotation=(36, 0, 0))
cam.position = (0, 3, 9)
cam.target_z = -185
camera.z = -185

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
panel(-0.25, 0.5, 0.25, 0.404)
clock_text = T("", 0, 0.493, 1.35, color.white, origin=(0, 0.5))
mode_text = T("", 0, 0.455, 0.75, ACCENT, origin=(0, 0.5))
T("Chuột phải: xoay  ·  Lăn chuột: zoom  ·  Chuột giữa: di chuyển  ·  Space: tạm dừng",
  0, 0.427, 0.55, DIM, origin=(0, 0.5))

# ---- bảng trái: Master Node + điều khiển
LX = -0.875
panel(-0.889, 0.5, -0.555, -0.3)
T("MASTER NODE  ·  Vi khí hậu", LX, 0.488, 0.95, ACCENT)
env_text = T("", LX, 0.452, 0.8)
T("ĐIỀU KHIỂN MÔ PHỎNG", LX, 0.222, 0.8, ACCENT)
BW, BH = 0.15, 0.034


def toggle_pause():
    G.paused = not G.paused
    refresh_buttons()


def cycle_speed():
    G.speed_i = (G.speed_i + 1) % len(SPEEDS)
    refresh_buttons()


def toggle_routing():
    s = G.sim
    s.routing_enabled = not s.routing_enabled
    s.log("Người dùng " + ("BẬT" if s.routing_enabled else "TẮT") + " định tuyến thông minh", "user")
    refresh_buttons()


def toggle_rotate():
    s = G.sim
    s.auto_rotate = not s.auto_rotate
    s.log("Người dùng " + ("BẬT trục xoay tự động" if s.auto_rotate else "TẮT trục xoay (lưới cố định)"), "user")
    refresh_buttons()


def cycle_loss():
    s = G.sim
    k = min(range(len(LOSS_STEPS)), key=lambda j: abs(LOSS_STEPS[j] - s.lora_loss))
    s.lora_loss = LOSS_STEPS[(k + 1) % len(LOSS_STEPS)]
    s.log(f"Người dùng đặt tỉ lệ mất gói LoRa = {s.lora_loss * 100:.0f}%", "user")
    refresh_buttons()


def set_scenario(name):
    G.sim.scenario = name
    G.sim.log(f"Chuyển kịch bản: {S.SCENARIOS[name]['label']}", "user")
    refresh_buttons()


def reset_sim():
    old = G.sim
    G.sim = S.Simulation(scenario=old.scenario, routing_enabled=old.routing_enabled,
                         auto_rotate=old.auto_rotate, lora_loss=old.lora_loss,
                         user_d_rh=old.user_d_rh, user_wind_mul=old.user_wind_mul,
                         user_wind_dir=old.user_wind_dir)
    for z_new, z_old in zip(G.sim.zones, old.zones):
        z_new.weight = z_old.weight
    G.acc = 0.0
    refresh_buttons()


def change_weight(i, d):
    z = G.sim.zones[i]
    new = max(1, min(10, z.weight + d))
    if new != z.weight:
        G.sim.log(f"App: đổi trọng số ưu tiên bồn {z.name}: {z.weight} -> {new}", "user")
        z.weight = new
    G.ui_timer = 1.0


b_pause = btn("", LX, 0.172, BW, BH, toggle_pause)
b_speed = btn("", LX + BW + 0.012, 0.172, BW, BH, cycle_speed)
b_route = btn("", LX, 0.130, BW, BH, toggle_routing)
b_rot = btn("", LX + BW + 0.012, 0.130, BW, BH, toggle_rotate)
b_loss = btn("", LX, 0.088, BW, BH, cycle_loss)
b_reset = btn("Làm lại từ đầu", LX + BW + 0.012, 0.088, BW, BH, reset_sim, C(110, 55, 55))
T("Kịch bản thời tiết:", LX, 0.058, 0.75, DIM)
SW = 0.1
b_scen = {
    "normal": btn("Bình thường", LX, 0.016, SW, BH, lambda: set_scenario("normal")),
    "drought": btn("Hạn El Niño", LX + SW + 0.006, 0.016, SW, BH, lambda: set_scenario("drought")),
    "storm": btn("Gió bão", LX + 2 * (SW + 0.006), 0.016, SW, BH, lambda: set_scenario("storm")),
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


make_slider("Chỉnh độ ẩm (cộng thêm % RH):", -0.02, -10, 5, 0, 0.5, _set_rh)
make_slider("Hệ số tốc độ gió (x):", -0.105, 0.2, 4, 1, 0.1, _set_wind)
make_slider("Hướng gió (độ, 0 = thổi lên sống núi):", -0.19, -180, 180, 0, 5, _set_dir)

# ---- bảng THÔNG SỐ THIẾT KẾ (bật/tắt bằng nút hoặc phím P)
# Mỗi dòng: (nhãn, tên hằng số trong simulation.py, min, max, bước, hệ số hiển thị)
PARAMS = [
    ("Ngưỡng gió xoay bảo vệ lưới (m/s)", "WIND_PROTECT_ON", 6, 25, 0.5, 1),
    ("Tốc độ trục xoay (độ/phút)", "NET_TURN_RATE", 0.5, 10, 0.5, 1),
    ("Hiệu suất thu của lưới (%)", "NET_EFFICIENCY", 5, 40, 1, 100),
    ("Ngưỡng xin cứu trợ (% bồn)", "LOW_LEVEL", 10, 50, 1, 100),
    ("Mức dừng cứu trợ (% bồn)", "TARGET_LEVEL", 30, 90, 1, 100),
    ("Lưu lượng bơm (L/giờ)", "PUMP_RATE_LPH", 30, 300, 10, 1),
]
PARAM_DEFAULTS = {name: getattr(S, name) for _, name, *_ in PARAMS}
param_group = []
param_sliders = {}


def set_param(name, shown, k):
    v = shown / k
    if name == "TARGET_LEVEL":
        v = max(v, S.LOW_LEVEL + 0.05)          # mức dừng phải cao hơn ngưỡng xin
    setattr(S, name, v)
    if name == "WIND_PROTECT_ON":
        S.WIND_PROTECT_OFF = v - 2.0            # ngưỡng quay lại luôn thấp hơn 2 m/s
    if name == "LOW_LEVEL" and S.TARGET_LEVEL < v + 0.05:
        S.TARGET_LEVEL = v + 0.05


def reset_params():
    for _, name, _lo, _hi, _st, k in PARAMS:
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
        s = make_slider(label + ":", y, lo, hi, PARAM_DEFAULTS[name] * k, step,
                        lambda v, name=name, k=k: set_param(name, v, k), x=x, group=param_group)
        param_sliders[name] = s
    b1 = btn("Về mặc định", 0.14, 0.012, 0.15, BH, reset_params, C(110, 55, 55))
    b2 = btn("Đóng", 0.31, 0.012, 0.15, BH, toggle_params)
    b1.z = b2.z = -3
    param_group.extend([b1, b2])
    for e in param_group:
        e.enabled = False


build_param_panel()
btn("Chỉnh thông số thiết kế (P)", LX, -0.277, 2 * BW + 0.012, BH, toggle_params, C(120, 90, 30))


def refresh_buttons():
    s = G.sim
    b_pause.text = "Tiếp tục" if G.paused else "Tạm dừng"
    b_speed.text = f"Tốc độ: x{SPEEDS[G.speed_i]}"
    b_route.text = "Định tuyến: " + ("BẬT" if s.routing_enabled else "TẮT")
    b_route.color = C(30, 120, 90) if s.routing_enabled else C(120, 60, 50)
    b_rot.text = "Trục xoay: " + ("BẬT" if s.auto_rotate else "TẮT")
    b_rot.color = C(30, 120, 90) if s.auto_rotate else C(120, 60, 50)
    b_loss.text = f"LoRa mất gói: {s.lora_loss * 100:.0f}%"
    for name, b in b_scen.items():
        b.color = C(30, 120, 150) if name == s.scenario else C(38, 70, 100)


# ---- bảng phải: 3 bồn
RX = 0.555
panel(0.54, 0.5, 0.889, -0.5)
CARD_H = 0.208


class TankCard:
    def __init__(self, i):
        self.i = i
        y = 0.488 - i * CARD_H
        self.title = T("", RX, y, 0.95, color.white)
        self.wtext = T("", RX + 0.205, y, 0.9, C(255, 225, 120))
        btn("-", RX + 0.262, y - 0.012, 0.028, 0.028, lambda: change_weight(i, -1))
        btn("+", RX + 0.296, y - 0.012, 0.028, 0.028, lambda: change_weight(i, +1))
        self.wbar = bar(RX, y - 0.05, 0.324, 0.024, C(40, 140, 235))
        self.low_tick = Entity(parent=UI, model="quad", color=C(255, 255, 255, 200), z=-0.5,
                               position=(RX + 0.324 * S.LOW_LEVEL, y - 0.05), scale=(0.002, 0.03))
        self.floor_tick = Entity(parent=UI, model="quad", color=C(255, 150, 40), z=-0.5,
                                 position=(RX, y - 0.05), scale=(0.002, 0.03))
        self.wlabel = T("", RX + 0.162, y - 0.05, 0.72, color.white, origin=(0, 0))
        self.harvest = T("", RX, y - 0.07, 0.72)
        self.hbar = bar(RX, y - 0.108, 0.2, 0.012, C(80, 200, 90))
        self.hlabel = T("", RX + 0.21, y - 0.098, 0.72)
        self.status = T("", RX, y - 0.122, 0.78)
        self.view = T("", RX, y - 0.148, 0.62, DIM)
        Entity(parent=UI, model="quad", color=C(70, 90, 110), z=0.5,
               position=(RX + 0.162, y - 0.178), scale=(0.324, 0.0015))

    def update(self, sim):
        z = sim.zones[self.i]
        i = self.i
        set_text(self.title, f"BỒN {z.name} · {z.crop}")
        set_text(self.wtext, f"w = {z.weight}")
        self.wbar.scale_x = max(0.0005, 0.324 * z.frac)
        self.wbar.color = C(230, 80, 60) if z.frac < 0.12 else C(40, 140, 235)
        self.floor_tick.x = RX + 0.324 * S.sacrifice_floor(z.weight)
        self.low_tick.x = RX + 0.324 * S.LOW_LEVEL
        set_text(self.wlabel, f"{z.level:.0f} / {z.capacity:.0f} L  ({z.frac * 100:.0f}%)")
        set_text(self.harvest, f"Thu: {z.harvest_lph:.1f} L/h  ·  Dự báo 6h: {sim.forecast_6h[i]:.0f} L")
        self.hbar.scale_x = max(0.0005, 0.2 * z.health / 100)
        self.hbar.color = C(80, 200, 90) if z.health > 60 else C(230, 190, 60) if z.health > 25 else C(230, 80, 60)
        set_text(self.hlabel, f"Cây: {z.health:.0f}%" if z.health > 0 else "Cây: ĐÃ CHẾT")
        if z.pump_to is not None:
            why = "hy sinh" if sim.reason.get(i) == "sacrifice" else "chia nước dư"
            st, col = f"BƠM -> bồn {sim.zones[z.pump_to].name}  ({why})", C(255, 165, 60)
        elif z.receiving_from is not None:
            st, col = f"NHẬN <- bồn {sim.zones[z.receiving_from].name}", C(90, 225, 255)
        else:
            st, col = "Bơm tắt · van đóng", DIM
        set_text(self.status, st)
        self.status.color = col
        age = sim.t - sim.view_time[i]
        set_text(self.view, f"Master thấy: {sim.view_level[i] * 100:.0f}%  (telemetry {age:.0f} ph trước)")


cards = [TankCard(i) for i in range(3)]
NOTE_TICKS = "Vạch trắng: ngưỡng cứu trợ · Vạch cam: sàn hy sinh"
T(NOTE_TICKS, RX, -0.132, 0.56, DIM)
T("KẾT QUẢ TÍCH LUỸ", RX, -0.16, 0.85, ACCENT)
stats_text = T("", RX, -0.192, 0.76)
vbar = bar(RX, -0.385, 0.324, 0.024, C(80, 200, 90))
vlabel = T("", RX + 0.162, -0.385, 0.7, color.white, origin=(0, 0))

# ---- nhật ký sự kiện
panel(-0.889, -0.31, 0.53, -0.5)
T("NHẬT KÝ MASTER NODE / LoRa", LX, -0.318, 0.75, ACCENT)
T("Gói tin:  vàng = telemetry   hồng = lệnh bơm   đỏ = mất gói",
  -0.6, -0.318, 0.62, DIM)
LOG_N = 6
log_lines = [T("", LX, -0.345 - k * 0.0245, 0.72) for k in range(LOG_N)]
LOG_COL = {"info": TXT, "warn": C(255, 190, 80), "bad": C(255, 100, 90),
           "route": C(90, 225, 255), "user": C(190, 160, 255)}

# ---- biểu đồ DỰ BÁO THU SƯƠNG 24 giờ tới (nửa phải của bảng dưới)
FC_X0, FC_PITCH, FC_BASE, FC_MAXH = -0.125, 0.0155, -0.462, 0.092
Entity(parent=UI, model="quad", color=C(70, 90, 110), z=0.5, position=(-0.148, -0.405), scale=(0.0015, 0.17))
T("DỰ BÁO THU SƯƠNG 24 GIỜ TỚI  ·  tổng 3 lưới, L mỗi giờ", -0.135, -0.318, 0.68, ACCENT)
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
    n = len(s.zones)
    total = [sum(fc.hourly[i][j] for i in range(n)) for j in range(S.FORECAST_HOURS)]
    top = max(max(total), 5.0)
    for j, b in enumerate(fc_bars):
        b.scale_y = max(0.0005, FC_MAXH * total[j] / top)
    jmax = max(range(S.FORECAST_HOURS), key=lambda j: total[j])
    if total[jmax] >= 0.5:                      # chỉ ghi số trên cột cao nhất
        set_text(fc_peak, f"{total[jmax]:.0f} L")
        fc_peak.position = (FC_X0 + jmax * FC_PITCH, FC_BASE + FC_MAXH * total[jmax] / top + 0.003)
    else:
        set_text(fc_peak, "")
    h0 = int(s.hour)
    for k, t in enumerate(fc_ticks):
        set_text(t, "nay" if k == 0 else f"{(h0 + 3 * k) % 24:02d}h")
    cnt, mae, pct = fc.accuracy()
    if cnt:
        acc = f"Sai số 6h: {pct:.0f}% (TB {mae:.0f} L, {cnt} lần)"
    else:
        acc = "Sai số 6h: chưa có (cần 6 giờ)"
    ks = " / ".join(f"{fc.k(i, s):.1f}" for i in range(n))
    per = " · ".join(f"{z.name} {s.forecast_24h[i]:.0f}" for i, z in enumerate(s.zones))
    set_text(fc_text,
             f"Tổng 6 giờ tới:  {sum(s.forecast_6h):.0f} L\n"
             f"Tổng 24 giờ tới:  {sum(s.forecast_24h):.0f} L\n"
             f"24h theo lưới:  {per} L\n"
             f"Hệ số lưới đã học:  {ks}\n"
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
    fog = "không sương" if s.lwc <= 0 else ("Sương mỏng" if s.lwc < 0.15 else "Sương dày")
    if any(z.protecting for z in s.zones):
        net = "XOAY NGHIÊNG BẢO VỆ"
    elif not s.auto_rotate:
        net = "cố định (thụ động)"
    else:
        net = "bám theo hướng gió"
    loss = 100.0 * s.pkt_lost / s.pkt_sent if s.pkt_sent else 0.0
    set_text(env_text,
             f"Nhiệt độ (SHT4x):  {s.temp:.1f} °C\n"
             f"Độ ẩm (SHT4x):  {s.rh:.1f} %\n"
             f"Tốc độ gió:  {s.wind_speed:.1f} m/s\n"
             f"Hướng gió:  {s.wind_dir:.0f}°  ({wind_name(s.wind_dir)})\n"
             f"Sương (LWC):  {s.lwc:.2f} g/m³  -  {fog}\n"
             f"Lưới:  {net}\n"
             f"Ngưỡng gió bảo vệ:  {S.WIND_PROTECT_ON:.1f} m/s\n"
             f"Dự báo thu 6h tới:  {sum(s.forecast_6h):.0f} L\n"
             f"LoRa: {s.pkt_sent} gói, mất {s.pkt_lost} ({loss:.1f}%)")
    for c in cards:
        c.update(s)
    for zv, z in zip(zone_views, s.zones):
        zv.update_labels(z)
    st = s.stats()
    set_text(stats_text,
             f"Nước thu từ sương:  {st['harvested']:.0f} L\n"
             f"Tràn bồn (lãng phí):  {st['overflow']:.0f} L\n"
             f"Chuyển giữa các bồn:  {st['moved']:.0f} L\n"
             f"Thiếu nước tưới:  {st['unmet']:.0f} L\n"
             f"Điện bơm nội bộ:  {st['pump_wh']:.1f} Wh\n"
             f"Nếu bơm từ sông lên:  {st['river_wh']:.0f} Wh\n"
             f"Giá trị vụ mùa giữ được:")
    vbar.scale_x = max(0.0005, 0.324 * st["value_pct"] / 100)
    vbar.color = C(80, 200, 90) if st["value_pct"] > 70 else C(230, 190, 60) if st["value_pct"] > 40 else C(230, 80, 60)
    set_text(vlabel, f"{st['value_pct']:.0f}%  ({st['value_kept']:.0f}/{st['value_total']:.0f} triệu)")
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
    if USE_LIGHTING:
        sun_light.color = color.rgba(0.35 + 0.55 * day, 0.35 + 0.55 * day, 0.4 + 0.45 * day, 1)
        amb_light.color = color.rgba(0.3 + 0.2 * day, 0.31 + 0.2 * day, 0.36 + 0.17 * day, 1)


def node_point(idx):
    return master_view.top if idx < 0 else zone_views[idx].node_pos + Vec3(0.5, 2.0, 0)


# ------------------------------------------------------------------ vòng lặp chính
def update():
    dt = min(time.dt, 0.1)
    s = G.sim
    G.frame += 1
    G.elapsed += dt
    if not G.paused:
        G.acc += dt * SPEEDS[G.speed_i]
        steps = min(int(G.acc), 240)
        G.acc -= int(G.acc)
        for _ in range(steps):
            s.step(1.0)
    # gói LoRa -> hoạt ảnh
    for p in s.packets:
        if len(PacketView.active) < 14:
            PacketView(node_point(p.src), node_point(p.dst), p.kind, p.lost)
    s.packets.clear()
    for pv in list(PacketView.active):
        pv.update(dt)
    anim_dt = 0.0 if G.paused else dt
    for zv, z in zip(zone_views, s.zones):
        zv.update(z, s, anim_dt)
    master_view.update(s, anim_dt)
    for pv in pipe_views:
        pv.update(s.zones, anim_dt)
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
