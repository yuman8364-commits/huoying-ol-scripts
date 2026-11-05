"""Pure observations in canonical game coordinates; no input or state writes."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import statistics

import numpy as np
from PIL import Image

from hyol_scheduler import TemplateStore
from .paths import ROOT, SAMPLES


ASSETS = ROOT / 'assets'
GAME_PAGE = (920, 220, 1300, 325)
WEEKEND_PAGE = (880, 245, 1190, 360)
CLOSE = (1330, 288, 1385, 345)
START = (890, 480, 1200, 600)
OVER = (925, 415, 1170, 510)
CHANCE = (1258, 328, 1288, 358)
REWARD = (1289, 635, 1317, 666)
BAR = (765, 717, 1324, 762)
BOTTOM = (880, 610, 1230, 690)
TRACE = (740, 390, 1340, 785)
SLOTS = tuple((754, 402 + 45*i, 798, 444 + 45*i) for i in range(6))
IDENTITY = (105, 84, 265, 104)


class Pending(RuntimeError):
    """An observation is insufficient to authorize another input."""


class Stopped(Pending):
    pass


def groups(mask: np.ndarray, max_gap: int = 0) -> list[tuple[int, int]]:
    indices = np.flatnonzero(mask)
    if not len(indices):
        return []
    cuts = np.flatnonzero(np.diff(indices) > max_gap + 1) + 1
    return [(int(g[0]), int(g[-1]) + 1) for g in np.split(indices, cuts)]


def identity_mask(crop: Image.Image) -> np.ndarray:
    pixels = np.asarray(crop.convert('RGB'), dtype=np.int16)
    # White HUD glyphs survive the known modal dimming; colored map pixels do not.
    # The final eight HUD rows contain an animated stamina-bar sparkle.
    # Preserve only the enrolled name text rows; never weaken the match floor.
    text = pixels[:12]
    maximum, minimum = text.max(2), text.min(2)
    result = np.zeros(pixels.shape[:2], bool)
    peak = float(np.quantile(maximum, .99))
    if peak < 30:
        return result
    result[:12] = (minimum >= peak*.75) & (maximum - minimum <= 38)
    return result


def f1(a: np.ndarray, b: np.ndarray) -> float:
    if a.shape != b.shape or min(int(a.sum()), int(b.sum())) < 12:
        return 0.0
    return float(2 * np.logical_and(a, b).sum() / (a.sum() + b.sum()))


def binding_matches(expected, current):
    return (expected['key'] == current['key'] and expected['session'] == current['session']
            and f1(np.asarray(expected['identity'], bool), np.asarray(current['identity'], bool)) >= .94)


def layer_count(frame: Image.Image, origin=(0, 0)) -> int:
    """Count bordered, dark-backed reward tiles, independent of a pre-game frame."""
    filled = []
    for roi in SLOTS:
        box = (roi[0]-origin[0], roi[1]-origin[1], roi[2]-origin[0], roi[3]-origin[1])
        tile = np.asarray(frame.crop(box).convert('RGB'), dtype=np.int16)
        high, low = tile.max(2), tile.min(2)
        neutral_dark = float(((high < 70) & (high-low < 20)).mean())
        item = float(((high > 95) & (high-low > 35)).mean())
        red_background = float(((tile[:, :, 0] >= 24)
                                & (tile[:, :, 0] > tile[:, :, 1]*1.8)
                                & (tile[:, :, 0] > tile[:, :, 2]*2.0)).mean())
        if neutral_dark >= .20 and item >= .07:
            filled.append(True)
        elif neutral_dark < .12 and red_background >= .80:
            filled.append(False)
        else:
            raise Pending('奖励图标被动画遮挡或格子状态不明确')
    count = sum(filled)
    if filled != [True]*count + [False]*(6-count):
        raise Pending('奖励图标不连续，不能确认已击飞层数')
    return count


def moving_column(columns, judgment=None):
    """A verified old judgment is a stationary marker, never a new pointer."""
    if len(columns) == 1:
        return columns[0]
    if judgment is not None and any(abs(x-judgment) <= 4 for x in columns):
        moving = [x for x in columns if abs(x-judgment) > 12]
        if len(moving) == 1:
            return moving[0]
    return None


def bottom_bounds(pixels: np.ndarray) -> tuple[int, int] | None:
    if pixels.shape[:2] != (BOTTOM[3]-BOTTOM[1], BOTTOM[2]-BOTTOM[0]):
        return None
    white = (pixels[:, :, :3] >= 210).all(2)
    candidates = [(BOTTOM[0]+a, BOTTOM[0]+b-1)
                  for a, b in groups(white.sum(0) >= 2, 5) if 70 <= b-a <= 158]
    return candidates[0] if len(candidates) == 1 else None


def flame_columns(pixels: np.ndarray) -> list[int]:
    p = pixels.astype(np.int16)
    red, green, blue = p[:, :, 0], p[:, :, 1], p[:, :, 2]
    score = ((red >= 220) & (green >= 155) & (blue < 115)).sum(0)
    return [BAR[0] + (a+b-1)//2 for a, b in groups(score >= 5)
            if 5 <= b-a <= 30 and score[a:b].max() >= 8]


def perfect_zone(pixels: np.ndarray) -> tuple[int, int]:
    p = pixels.astype(np.int16)[7:40]
    r, g, b = p[:, :, 0], p[:, :, 1], p[:, :, 2]
    yellow = (r >= 120) & (r < 220) & (g >= 90) & (g >= r*.60) & (g <= r*.93) & (b < g*.60)
    centre = (BAR[0]+BAR[2]-1)/2
    bands = [(BAR[0]+a, BAR[0]+b-1) for a, b in groups(yellow.mean(0) >= .65, 2)
             if 4 <= b-a <= 90 and abs(BAR[0]+(a+b-1)/2-centre) <= 4]
    if len(bands) != 1:
        raise Pending('Perfect 色带无法唯一定位')
    return bands[0]


@dataclass(frozen=True)
class Motion:
    x: float
    velocity: float
    captured_at: float
    capture_seconds: float
    residual: float
    noise: float | None = None
    sample_count: int = 0
    mean_age: float = 0
    sxx: float = 0


def estimate_motion(samples: list[tuple[float, int, float]]) -> Motion | None:
    unique = [row for i, row in enumerate(samples) if i == 0 or row[1] != samples[i-1][1]]
    recent = unique[-40:]
    for i in range(len(recent)-1, 0, -1):
        if recent[i][1] < recent[i-1][1]-30 or abs(recent[i][1]-recent[i-1][1]) > 100:
            recent = recent[i:]
            break
    if recent:
        recent = [row for row in recent if row[0] >= recent[-1][0]-.65]
    if len(recent) < 6 or recent[-1][0]-recent[0][0] < .18:
        return None
    ts = np.asarray([x[0]-recent[0][0] for x in recent])
    xs = np.asarray([x[1] for x in recent])
    velocity, intercept = np.polyfit(ts, xs, 1)
    residual = float(np.max(np.abs(xs-(velocity*ts+intercept))))
    if not 80 <= velocity <= 2500 or residual > 7:
        return None
    errors = xs-(velocity*ts+intercept)
    noise = float(np.sqrt(np.sum(errors**2)/max(1, len(ts)-2)))
    return Motion(float(velocity*ts[-1]+intercept), float(velocity), recent[-1][0],
                  statistics.median(x[2] for x in recent[-8:]), residual,
                  noise, len(ts), float(ts[-1]-ts.mean()), float(np.sum((ts-ts.mean())**2)))


def dispatch_at(motion: Motion, zone: tuple[int, int], latency: float, now: float) -> float | None:
    crossing = ((zone[0]+zone[1])/2 - motion.x) / motion.velocity
    at = motion.captured_at + crossing - latency
    # This uncertainty budget includes sampling jitter; slow captures skip a sweep.
    fit_error = motion.residual
    if motion.noise is not None and motion.sxx > 0 and motion.sample_count >= 6:
        fit_error = 2*motion.noise*np.sqrt(1/motion.sample_count+(motion.mean_age+crossing)**2/motion.sxx)
    uncertainty = fit_error + motion.velocity*motion.capture_seconds/2
    if (not 0 <= crossing <= .65 or at < now+.035
            or uncertainty > (zone[1]-zone[0])/2-.75):
        return None
    return at


def judgment_from_frames(frames: list[np.ndarray], zone: tuple[int, int], roi=TRACE) -> int | None:
    target = sum(zone)/2
    candidates = []
    for frame in frames:
        bar = frame[BAR[1]-roi[1]:BAR[3]-roi[1], BAR[0]-roi[0]:BAR[2]-roi[0]]
        columns = flame_columns(bar)
        near = [x for x in columns if abs(x-target) < 110]
        if len(near) == 1 and any(abs(x-near[0]) >= 75 for x in columns):
            candidates.append(near[0])
    if len(candidates) < 3:
        return None
    median = statistics.median(candidates)
    inliers = [x for x in candidates if abs(x-median) <= 3]
    if len(inliers) < 3 or len(inliers) < len(candidates)*.75:
        return None
    return round(statistics.median(inliers))


def _glyph(pixels: Image.Image, red: bool) -> np.ndarray | None:
    p = np.asarray(pixels.convert('RGB'), dtype=np.int16)
    r, g, b = p[:, :, 0], p[:, :, 1], p[:, :, 2]
    mask = ((r > 115) & (r > g+35) & (r > b+35)) if red else (
        (p.min(2) > 135) & (p.max(2)-p.min(2) < 65))
    ys, xs = np.where(mask)
    if len(xs) < 7:
        return None
    cropped = mask[ys.min():ys.max()+1, xs.min():xs.max()+1]
    if cropped.shape[1] > cropped.shape[0]*1.2 or cropped.shape[0] < 6:
        return None
    return np.asarray(Image.fromarray(cropped).resize((16, 24), Image.Resampling.NEAREST), bool)


class Vision:
    def __init__(self, asset_dir=ASSETS, sample_dir=None):
        self.assets = Path(asset_dir)
        self.samples = Path(sample_dir) if sample_dir is not None else SAMPLES
        self.templates = TemplateStore(.93)
        self.glyphs: dict[int, list[np.ndarray]] = {i: [] for i in range(4)}
        # Read real digit samples. Cross-style masks are a strict fallback, never
        # a missing-template-is-zero rule. Unknown/ambiguous glyphs remain unknown.
        sources = {0: ['daruma_chance_digit_0.bmp', 'ranking_digit_0.bmp'],
                   1: ['daruma_reward_digit_1.bmp', 'daruma_chance_digit_1.bmp'],
                   2: ['daruma_chance_digit_2.bmp', 'daruma_reward_digit_2.bmp',
                       'ranking_digit_2.bmp', 'group_instance_reward_digit_2.bmp'],
                   3: ['daruma_reward_digit_3.bmp', 'daruma_chance_digit_3.bmp']}
        for digit, names in sources.items():
            for name in names:
                path = self.asset(name)
                if path.exists():
                    with Image.open(path) as image:
                        for red in (True, False):
                            mask = _glyph(image, red)
                            if mask is not None:
                                self.glyphs[digit].append(mask)

    def asset(self, name):
        path = self.samples/name
        return path if path.exists() else self.assets/name

    def match(self, frame, name, roi, threshold=.93):
        return self.templates.match(frame, self.asset(name), roi, threshold)

    def visible(self, frame, name, roi, threshold=.93):
        return self.match(frame, name, roi, threshold)[0]

    def page(self, frame: Image.Image) -> str:
        if self.offline(frame):
            return 'offline'
        game = self.visible(frame, 'daruma_game_page.bmp', GAME_PAGE)
        if game:
            if self.visible(frame, 'daruma_game_over.bmp', OVER, .88):
                return 'over'  # The start button can be visible behind game-over.
            return 'ready' if self.visible(frame, 'daruma_start.bmp', START) else 'playing'
        if self.visible(frame, 'daruma_weekend_page.bmp', WEEKEND_PAGE):
            return 'weekend'
        if self.visible(frame, 'daruma_weekend_menu.bmp', (475, 240, 705, 755)):
            return 'menu'
        if self.visible(frame, 'daruma_hotspot.bmp', (450, 69, 550, 145)):
            return 'home' if self.visible(frame, 'home_leave.bmp', (1800, 930, 1920, 1032)) else 'field'
        return 'unknown'

    def offline(self, frame):
        return any(self.visible(frame, name, (650, 350, 1300, 780), .98) for name in (
            'offline_login_prompt.bmp', 'offline_login_prompt_current.bmp', 'offline_login_text_current.bmp'))

    def reward_completed(self, frame):
        return (self.page(frame) == 'weekend' and self.visible(frame, 'daruma_reward_completed.bmp',
                                                              (1190, 635, 1330, 710), .95))

    def digit(self, frame, kind: str) -> int:
        roi, prefix = (CHANCE, 'daruma_chance_digit_') if kind == 'chance' else (REWARD, 'daruma_reward_digit_')
        exact = [i for i in range(4) if self.visible(frame, f'{prefix}{i}.bmp', roi, .95)]
        if len(exact) == 1:
            return exact[0]
        if exact:
            raise Pending(f'{kind} 数字匹配不唯一：{exact}')
        # Locate individual red/light connected columns inside the bounded counter.
        crop = frame.crop(roi)
        p = np.asarray(crop, dtype=np.int16)
        red = kind == 'reward'
        mask = ((p[:, :, 0] > 115) & (p[:, :, 0] > p[:, :, 1]+35)
                & (p[:, :, 0] > p[:, :, 2]+35)) if red else (
                    (p.min(2) > 135) & (p.max(2)-p.min(2) < 65))
        scores = []
        for left, right in groups(mask.sum(0) >= 1):
            glyph = _glyph(crop.crop((left, 0, right, crop.height)), red)
            if glyph is None:
                continue
            ranking = sorted(((max((f1(glyph, ref) for ref in refs), default=0), number)
                              for number, refs in self.glyphs.items()), reverse=True)
            if ranking[0][0] >= .90 and ranking[0][0]-ranking[1][0] >= .10:
                scores.append(ranking[0][1])
        if len(scores) != 1:
            raise Pending(f'{kind} 数字未可靠识别，不能推断为0')
        return scores[0]
