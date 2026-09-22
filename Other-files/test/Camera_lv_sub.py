import time

import cv2
import numpy as np
import pco
from PIL import Image, ImageDraw, ImageFont


EXPOSURE_S = 0.20
RING_BUFFER_FRAMES = 10

VIDEO_WINDOW = "pco.edge live"
PLOT_WINDOW = "ROI intensity vs time"

DRIFT_DOWNSAMPLE = 4
MAX_DRIFT_PIXELS = 80

ROI_EDIT_ANGLE_STEP = 1.0
ROI_EDIT_LENGTH_STEP = 1.0
ROI_EDIT_HEIGHT_STEP = 1.0
ROI_EDIT_MOVE_STEP = 5.0

MAX_PLOT_POINTS = 2000

zoom = 1.0
pan_x = 0.0
pan_y = 0.0
is_panning = False
pan_start_mouse_x = 0
pan_start_mouse_y = 0
pan_start_x = 0.0
pan_start_y = 0.0

roi_start = None
roi_end = None
is_selecting_roi = False

roi = None
roi_edit_mode = False
recording_enabled = False
plot_buttons = {}

latest_display_shape = None
latest_image_shape = None
latest_offset = (0, 0)
latest_scale = 1.0
latest_base_scale = 1.0
latest_window_size = (1000, 800)

roi_times = []
roi_means = []
start_time = None


def get_arial_font(size):
    font_paths = [
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/Arial.ttf",
    ]

    for path in font_paths:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass

    return ImageFont.load_default()


ARIAL_10 = get_arial_font(10)
ARIAL_11 = get_arial_font(11)
ARIAL_12 = get_arial_font(12)
ARIAL_14 = get_arial_font(14)


def put_arial_text(img_bgr, text, xy, font, color=(0, 0, 0)):
    img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
    pil_img = Image.fromarray(img_rgb)
    draw = ImageDraw.Draw(pil_img)
    draw.text(xy, text, font=font, fill=color)
    img_bgr[:] = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)


def diff_to_gray_preview(diff: np.ndarray) -> np.ndarray:
    max_abs = np.percentile(np.abs(diff), 99.8)
    max_abs = max(max_abs, 1)

    gray = 128.0 + diff.astype(np.float32) * 127.0 / max_abs
    gray = np.clip(gray, 0, 255)

    return gray.astype(np.uint8)


def estimate_drift_shift(background_u16: np.ndarray, image_u16: np.ndarray):
    bg = background_u16.astype(np.float32)
    img = image_u16.astype(np.float32)

    if DRIFT_DOWNSAMPLE > 1:
        bg_small = cv2.resize(
            bg,
            None,
            fx=1.0 / DRIFT_DOWNSAMPLE,
            fy=1.0 / DRIFT_DOWNSAMPLE,
            interpolation=cv2.INTER_AREA,
        )
        img_small = cv2.resize(
            img,
            None,
            fx=1.0 / DRIFT_DOWNSAMPLE,
            fy=1.0 / DRIFT_DOWNSAMPLE,
            interpolation=cv2.INTER_AREA,
        )
    else:
        bg_small = bg
        img_small = img

    bg_small -= np.mean(bg_small)
    img_small -= np.mean(img_small)

    shift_small, response = cv2.phaseCorrelate(bg_small, img_small)

    dx = shift_small[0] * DRIFT_DOWNSAMPLE
    dy = shift_small[1] * DRIFT_DOWNSAMPLE

    if abs(dx) > MAX_DRIFT_PIXELS or abs(dy) > MAX_DRIFT_PIXELS:
        dx, dy = 0.0, 0.0

    return dx, dy, response


def drift_correct_image(image_u16: np.ndarray, dx: float, dy: float) -> np.ndarray:
    image_f32 = image_u16.astype(np.float32)
    matrix = np.array([[1, 0, -dx], [0, 1, -dy]], dtype=np.float32)

    aligned = cv2.warpAffine(
        image_f32,
        matrix,
        (image_u16.shape[1], image_u16.shape[0]),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REFLECT,
    )

    return aligned


def subtract_background_after_drift(image_u16: np.ndarray, background_u16: np.ndarray):
    dx, dy, response = estimate_drift_shift(background_u16, image_u16)
    image_aligned = drift_correct_image(image_u16, dx, dy)
    diff = image_aligned - background_u16.astype(np.float32)
    return diff, dx, dy, response


def image_to_window_xy(x_img, y_img):
    ox, oy = latest_offset
    return int(x_img * latest_scale + ox), int(y_img * latest_scale + oy)


def window_to_image_xy(x_win, y_win):
    ox, oy = latest_offset
    x_img = int((x_win - ox) / latest_scale)
    y_img = int((y_win - oy) / latest_scale)

    if latest_image_shape is None:
        return 0, 0

    h, w = latest_image_shape[:2]
    x_img = np.clip(x_img, 0, w - 1)
    y_img = np.clip(y_img, 0, h - 1)
    return x_img, y_img


def make_roi_from_drag(p1, p2):
    x1, y1 = p1
    x2, y2 = p2

    x_min, x_max = sorted((x1, x2))
    y_min, y_max = sorted((y1, y2))

    if x_max <= x_min or y_max <= y_min:
        return None

    return {
        "cx": (x_min + x_max) / 2.0,
        "cy": (y_min + y_max) / 2.0,
        "w": float(x_max - x_min),
        "h": float(y_max - y_min),
        "angle": 0.0,
    }


def roi_box_points(roi_obj):
    rect = (
        (float(roi_obj["cx"]), float(roi_obj["cy"])),
        (float(roi_obj["w"]), float(roi_obj["h"])),
        float(roi_obj["angle"]),
    )
    return cv2.boxPoints(rect)


def get_active_roi():
    if is_selecting_roi and roi_start is not None and roi_end is not None:
        return make_roi_from_drag(roi_start, roi_end)

    return roi


def rotate_roi(delta_angle):
    global roi

    if roi is None:
        return

    roi["angle"] = (roi["angle"] + delta_angle) % 180.0


def stretch_roi(delta_width):
    global roi

    if roi is None:
        return

    roi["w"] = max(2.0, roi["w"] + delta_width)


def stretch_roi_height(delta_height):
    global roi

    if roi is None:
        return

    roi["h"] = max(2.0, roi["h"] + delta_height)


def move_roi(dx, dy):
    global roi

    if roi is None:
        return

    roi["cx"] += dx
    roi["cy"] += dy

    if latest_image_shape is not None:
        h, w = latest_image_shape[:2]
        roi["cx"] = float(np.clip(roi["cx"], 0, w - 1))
        roi["cy"] = float(np.clip(roi["cy"], 0, h - 1))


def start_recording():
    global recording_enabled, roi_times, roi_means, start_time

    recording_enabled = True
    roi_times = []
    roi_means = []
    start_time = time.time()


def stop_recording():
    global recording_enabled
    recording_enabled = False


def any_window_closed(window_names):
    """Treat closing any displayed OpenCV window as a request to stop."""
    for window_name in window_names:
        try:
            if cv2.getWindowProperty(
                window_name, cv2.WND_PROP_VISIBLE
            ) < 1:
                return True
        except cv2.error:
            return True
    return False


def clamp_pan():
    global pan_x, pan_y

    if latest_image_shape is None:
        return

    image_height, image_width = latest_image_shape[:2]
    window_width, window_height = latest_window_size
    scale = latest_base_scale * zoom

    display_width = image_width * scale
    display_height = image_height * scale

    pan_limit_x = max(0.0, (display_width - window_width) / 2.0)
    pan_limit_y = max(0.0, (display_height - window_height) / 2.0)

    pan_x = float(np.clip(pan_x, -pan_limit_x, pan_limit_x))
    pan_y = float(np.clip(pan_y, -pan_limit_y, pan_limit_y))


def point_on_image(x, y):
    if latest_image_shape is None:
        return False

    image_height, image_width = latest_image_shape[:2]
    offset_x, offset_y = latest_offset
    display_width = image_width * latest_scale
    display_height = image_height * latest_scale

    return (
        offset_x <= x < offset_x + display_width
        and offset_y <= y < offset_y + display_height
    )


def mouse_callback(event, x, y, flags, param):
    global zoom, pan_x, pan_y
    global is_panning
    global pan_start_mouse_x, pan_start_mouse_y
    global pan_start_x, pan_start_y
    global roi_start, roi_end, is_selecting_roi, roi
    global roi_times, roi_means, start_time, recording_enabled

    if latest_image_shape is None:
        return

    if event == cv2.EVENT_MOUSEWHEEL:
        img_x, img_y = window_to_image_xy(x, y)

        if flags > 0:
            zoom = min(zoom * 1.2, 10.0)
        else:
            zoom = max(zoom / 1.2, 0.2)

        h, w = latest_image_shape[:2]
        window_w, window_h = latest_window_size

        new_scale = latest_base_scale * zoom
        disp_w = w * new_scale
        disp_h = h * new_scale

        center_ox = (window_w - disp_w) / 2.0
        center_oy = (window_h - disp_h) / 2.0

        pan_x = x - img_x * new_scale - center_ox
        pan_y = y - img_y * new_scale - center_oy
        clamp_pan()

    elif event == cv2.EVENT_LBUTTONDOWN:
        if not point_on_image(x, y):
            return

        if flags & cv2.EVENT_FLAG_SHIFTKEY:
            roi_start = window_to_image_xy(x, y)
            roi_end = roi_start
            is_selecting_roi = True
            is_panning = False
        else:
            is_panning = True
            is_selecting_roi = False
            pan_start_mouse_x = x
            pan_start_mouse_y = y
            pan_start_x = pan_x
            pan_start_y = pan_y

    elif event == cv2.EVENT_MOUSEMOVE and is_selecting_roi:
        if flags & cv2.EVENT_FLAG_LBUTTON:
            roi_end = window_to_image_xy(x, y)
        else:
            is_selecting_roi = False

    elif event == cv2.EVENT_MOUSEMOVE and is_panning:
        if flags & cv2.EVENT_FLAG_LBUTTON:
            pan_x = pan_start_x + x - pan_start_mouse_x
            pan_y = pan_start_y + y - pan_start_mouse_y
            clamp_pan()
        else:
            is_panning = False

    elif event == cv2.EVENT_LBUTTONUP and is_selecting_roi:
        roi_end = window_to_image_xy(x, y)
        is_selecting_roi = False

        new_roi = make_roi_from_drag(roi_start, roi_end)

        if new_roi is not None:
            roi = new_roi
            roi_times = []
            roi_means = []
            start_time = None
            recording_enabled = False
        else:
            roi = None
            roi_times = []
            roi_means = []
            start_time = None
            recording_enabled = False

    elif event == cv2.EVENT_LBUTTONUP and is_panning:
        pan_x = pan_start_x + x - pan_start_mouse_x
        pan_y = pan_start_y + y - pan_start_mouse_y
        clamp_pan()
        is_panning = False


def point_in_rect(x, y, rect):
    x1, y1, x2, y2 = rect
    return x1 <= x <= x2 and y1 <= y <= y2


def plot_mouse_callback(event, x, y, flags, param):
    global roi_edit_mode

    if event != cv2.EVENT_LBUTTONDOWN:
        return

    if point_in_rect(x, y, plot_buttons.get("record", (-1, -1, -1, -1))):
        if recording_enabled:
            stop_recording()
        else:
            start_recording()

    elif point_in_rect(x, y, plot_buttons.get("edit", (-1, -1, -1, -1))):
        roi_edit_mode = not roi_edit_mode

    elif roi_edit_mode and point_in_rect(x, y, plot_buttons.get("rotate_left", (-1, -1, -1, -1))):
        rotate_roi(-ROI_EDIT_ANGLE_STEP)

    elif roi_edit_mode and point_in_rect(x, y, plot_buttons.get("rotate_right", (-1, -1, -1, -1))):
        rotate_roi(ROI_EDIT_ANGLE_STEP)

    elif roi_edit_mode and point_in_rect(x, y, plot_buttons.get("width_minus", (-1, -1, -1, -1))):
        stretch_roi(-ROI_EDIT_LENGTH_STEP)

    elif roi_edit_mode and point_in_rect(x, y, plot_buttons.get("width_plus", (-1, -1, -1, -1))):
        stretch_roi(ROI_EDIT_LENGTH_STEP)

    elif roi_edit_mode and point_in_rect(x, y, plot_buttons.get("height_minus", (-1, -1, -1, -1))):
        stretch_roi_height(-ROI_EDIT_HEIGHT_STEP)

    elif roi_edit_mode and point_in_rect(x, y, plot_buttons.get("height_plus", (-1, -1, -1, -1))):
        stretch_roi_height(ROI_EDIT_HEIGHT_STEP)

    elif roi_edit_mode and point_in_rect(x, y, plot_buttons.get("move_left", (-1, -1, -1, -1))):
        move_roi(-ROI_EDIT_MOVE_STEP, 0)

    elif roi_edit_mode and point_in_rect(x, y, plot_buttons.get("move_right", (-1, -1, -1, -1))):
        move_roi(ROI_EDIT_MOVE_STEP, 0)

    elif roi_edit_mode and point_in_rect(x, y, plot_buttons.get("move_up", (-1, -1, -1, -1))):
        move_roi(0, -ROI_EDIT_MOVE_STEP)

    elif roi_edit_mode and point_in_rect(x, y, plot_buttons.get("move_down", (-1, -1, -1, -1))):
        move_roi(0, ROI_EDIT_MOVE_STEP)


def make_aspect_display(preview_u8: np.ndarray, window_w: int, window_h: int) -> np.ndarray:
    global latest_offset, latest_scale, latest_display_shape, latest_image_shape
    global latest_base_scale, latest_window_size

    img_h, img_w = preview_u8.shape[:2]
    latest_image_shape = preview_u8.shape
    latest_window_size = (window_w, window_h)

    latest_base_scale = min(window_w / img_w, window_h / img_h)
    latest_scale = latest_base_scale * zoom
    clamp_pan()

    disp_w = max(1, int(img_w * latest_scale))
    disp_h = max(1, int(img_h * latest_scale))

    resized = cv2.resize(preview_u8, (disp_w, disp_h), interpolation=cv2.INTER_AREA)

    canvas = np.zeros((window_h, window_w), dtype=np.uint8)

    center_ox = (window_w - disp_w) / 2.0
    center_oy = (window_h - disp_h) / 2.0

    ox = int(round(center_ox + pan_x))
    oy = int(round(center_oy + pan_y))

    dst_x0 = max(0, ox)
    dst_y0 = max(0, oy)

    src_x0 = max(0, -ox)
    src_y0 = max(0, -oy)

    copy_w = min(window_w - dst_x0, disp_w - src_x0)
    copy_h = min(window_h - dst_y0, disp_h - src_y0)

    if copy_w > 0 and copy_h > 0:
        canvas[dst_y0:dst_y0 + copy_h, dst_x0:dst_x0 + copy_w] = resized[
            src_y0:src_y0 + copy_h,
            src_x0:src_x0 + copy_w,
        ]

    latest_offset = (ox, oy)
    latest_display_shape = canvas.shape

    return cv2.cvtColor(canvas, cv2.COLOR_GRAY2BGR)


def draw_roi(display_bgr: np.ndarray, diff: np.ndarray, dx: float, dy: float):
    active_roi = get_active_roi()

    if active_roi is None:
        cv2.putText(
            display_bgr,
            f"Shift+drag: select ROI | drag: pan | drift dx={dx:.2f}, dy={dy:.2f}",
            (20, 30),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            (0, 255, 255),
            2,
            cv2.LINE_AA,
        )
        return None

    points = roi_box_points(active_roi)

    window_points = np.array(
        [image_to_window_xy(px, py) for px, py in points],
        dtype=np.int32,
    )

    color = (0, 255, 255) if roi_edit_mode else (0, 255, 0)
    cv2.polylines(display_bgr, [window_points], True, color, 2, cv2.LINE_AA)

    mask = np.zeros(diff.shape[:2], dtype=np.uint8)
    image_points = np.round(points).astype(np.int32)
    cv2.fillPoly(mask, [image_points], 255)

    roi_img = diff[mask > 0]

    if roi_img.size == 0:
        return None

    mean_val = float(np.mean(roi_img))

    text = (
        f"ROI cx={active_roi['cx']:.0f}, cy={active_roi['cy']:.0f}, "
        f"w={active_roi['w']:.0f}, h={active_roi['h']:.0f}, "
        f"angle={active_roi['angle']:.1f} | "
        f"mean={mean_val:.2f} | "
        f"drift dx={dx:.2f}, dy={dy:.2f}"
    )

    print(text, end="\r")

    cv2.putText(
        display_bgr,
        text,
        (20, 30),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.65,
        (0, 255, 255),
        2,
        cv2.LINE_AA,
    )

    return mean_val


def draw_button(plot, name, rect, text, active=False):
    global plot_buttons

    plot_buttons[name] = rect
    x1, y1, x2, y2 = rect

    fill = (0, 255, 255) if active else (240, 240, 240)
    border = (0, 120, 120) if active else (100, 100, 100)

    cv2.rectangle(plot, (x1, y1), (x2, y2), fill, -1)
    cv2.rectangle(plot, (x1, y1), (x2, y2), border, 1)

    bbox = ARIAL_11.getbbox(text)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]

    tx = x1 + max(3, (x2 - x1 - text_w) // 2)
    ty = y1 + max(2, (y2 - y1 - text_h) // 2 - 1)

    put_arial_text(plot, text, (tx, ty), ARIAL_11, color=(0, 0, 0))


def draw_roi_edit_buttons(plot, width):
    y1 = 34
    y2 = 62

    draw_button(
        plot,
        "record",
        (20, y1, 125, y2),
        "STOP" if recording_enabled else "START",
        active=recording_enabled,
    )

    draw_button(
        plot,
        "edit",
        (width - 125, y1, width - 20, y2),
        "EDIT ROI",
        active=roi_edit_mode,
    )

    if not roi_edit_mode:
        return

    draw_button(plot, "rotate_left", (width - 650, y1, width - 610, y2), "A-")
    draw_button(plot, "rotate_right", (width - 605, y1, width - 565, y2), "A+")
    draw_button(plot, "width_minus", (width - 560, y1, width - 520, y2), "w-")
    draw_button(plot, "width_plus", (width - 515, y1, width - 475, y2), "w+")
    draw_button(plot, "height_minus", (width - 470, y1, width - 430, y2), "h-")
    draw_button(plot, "height_plus", (width - 425, y1, width - 385, y2), "h+")
    draw_button(plot, "move_left", (width - 380, y1, width - 340, y2), "X-")
    draw_button(plot, "move_right", (width - 335, y1, width - 295, y2), "X+")
    draw_button(plot, "move_up", (width - 290, y1, width - 250, y2), "Y-")
    draw_button(plot, "move_down", (width - 245, y1, width - 205, y2), "Y+")


def draw_y_axis_ticks(plot, x_axis, y_top, y_bottom, y_min, y_max):
    tick_count = 5

    put_arial_text(
        plot,
        "Intensity",
        (8, y_top - 24),
        ARIAL_12,
        color=(0, 0, 0),
    )

    for i in range(tick_count):
        frac = i / (tick_count - 1)
        value = y_max - frac * (y_max - y_min)
        y_pix = int(round(y_top + frac * (y_bottom - y_top)))

        cv2.line(plot, (x_axis - 4, y_pix), (x_axis, y_pix), (0, 0, 0), 1)
        cv2.line(plot, (x_axis + 1, y_pix), (plot.shape[1] - 25, y_pix), (235, 235, 235), 1)

        label = f"{value:.1f}"
        bbox = ARIAL_10.getbbox(label)
        text_w = bbox[2] - bbox[0]

        put_arial_text(
            plot,
            label,
            (x_axis - 8 - text_w, y_pix - 7),
            ARIAL_10,
            color=(0, 0, 0),
        )


def make_intensity_plot(width=900, height=430):
    global plot_buttons

    plot_buttons = {}
    plot = np.full((height, width, 3), 255, dtype=np.uint8)

    margin_l = 95
    margin_r = 25
    margin_t = 92
    margin_b = 62

    x0 = margin_l
    x1 = width - margin_r
    y0 = height - margin_b
    y1 = margin_t

    cv2.rectangle(plot, (x0, y1), (x1, y0), (0, 0, 0), 1)

    put_arial_text(
        plot,
        "ROI mean intensity vs time",
        (margin_l, 12),
        ARIAL_14,
        color=(0, 0, 0),
    )

    draw_roi_edit_buttons(plot, width)

    if len(roi_times) < 2:
        draw_y_axis_ticks(plot, x0, y1, y0, 0.0, 1.0)

        message = "Select ROI, then click START" if not recording_enabled else "Recording..."
        put_arial_text(
            plot,
            message,
            (margin_l + 20, height // 2 - 10),
            ARIAL_14,
            color=(80, 80, 80),
        )
        return plot

    t = np.array(roi_times, dtype=np.float64)
    y = np.array(roi_means, dtype=np.float64)

    t_min, t_max = float(t[0]), float(t[-1])
    y_min, y_max = float(np.min(y)), float(np.max(y))

    if t_max <= t_min:
        t_max = t_min + 1.0

    if y_max <= y_min:
        y_pad = max(abs(y_max) * 0.05, 1.0)
    else:
        y_pad = (y_max - y_min) * 0.08

    y_min -= y_pad
    y_max += y_pad

    draw_y_axis_ticks(plot, x0, y1, y0, y_min, y_max)

    put_arial_text(
        plot,
        "Time (s)",
        (width // 2 - 28, height - 24),
        ARIAL_12,
        color=(0, 0, 0),
    )

    put_arial_text(
        plot,
        f"{t_min:.1f}",
        (x0, height - 43),
        ARIAL_10,
        color=(0, 0, 0),
    )

    t_max_label = f"{t_max:.1f}"
    bbox = ARIAL_10.getbbox(t_max_label)
    text_w = bbox[2] - bbox[0]

    put_arial_text(
        plot,
        t_max_label,
        (x1 - text_w, height - 43),
        ARIAL_10,
        color=(0, 0, 0),
    )

    xs = x0 + (t - t_min) / (t_max - t_min) * (x1 - x0)
    ys = y0 - (y - y_min) / (y_max - y_min) * (y0 - y1)

    points = np.column_stack((xs, ys)).astype(np.int32)
    cv2.polylines(plot, [points], False, (0, 120, 255), 1, cv2.LINE_AA)

    return plot


def main():
    global zoom, pan_x, pan_y, roi, roi_times, roi_means, start_time, recording_enabled

    with pco.Camera() as cam:
        cam.default_configuration()

        cam.exposure_time = EXPOSURE_S

        cam.configuration = {
            "timestamp": "off",
            "metadata": "off",
            # "roi": (1, 1, 1024, 1024),
        }

        cv2.namedWindow(VIDEO_WINDOW, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(VIDEO_WINDOW, 1500, 1200)
        cv2.setMouseCallback(VIDEO_WINDOW, mouse_callback)

        cv2.namedWindow(PLOT_WINDOW, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(PLOT_WINDOW, 900, 430)
        cv2.setMouseCallback(PLOT_WINDOW, plot_mouse_callback)

        cam.record(RING_BUFFER_FRAMES, "ring buffer")

        print("Capturing background frame...")
        cam.wait_for_new_image()
        background, _ = cam.image(0xFFFFFFFF)
        background = background.copy()
        print("Background captured. Live image = drift-corrected current frame - background.")
        print("Left mouse drag: pan image")
        print("Shift + left mouse drag: select ROI")

        try:
            while True:
                if any_window_closed((VIDEO_WINDOW, PLOT_WINDOW)):
                    break

                cam.wait_for_new_image()
                image_raw, _ = cam.image(0xFFFFFFFF)

                diff, dx, dy, response = subtract_background_after_drift(image_raw, background)
                preview = diff_to_gray_preview(diff)

                _, _, window_w, window_h = cv2.getWindowImageRect(VIDEO_WINDOW)
                window_w = max(window_w, 320)
                window_h = max(window_h, 240)

                display = make_aspect_display(preview, window_w, window_h)
                mean_val = draw_roi(display, diff, dx, dy)

                if recording_enabled and roi is not None and mean_val is not None:
                    if start_time is None:
                        start_time = time.time()

                    roi_times.append(time.time() - start_time)
                    roi_means.append(mean_val)

                    if len(roi_times) > MAX_PLOT_POINTS:
                        roi_times = roi_times[-MAX_PLOT_POINTS:]
                        roi_means = roi_means[-MAX_PLOT_POINTS:]

                cv2.imshow(VIDEO_WINDOW, display)
                cv2.imshow(PLOT_WINDOW, make_intensity_plot())

                key = cv2.waitKey(1) & 0xFF

                if key in (ord("+"), ord("=")):
                    zoom = min(zoom * 1.2, 10.0)
                elif key in (ord("-"), ord("_")):
                    zoom = max(zoom / 1.2, 0.2)
                elif key == ord("r"):
                    roi = None
                    zoom = 1.0
                    pan_x = 0.0
                    pan_y = 0.0
                    roi_times = []
                    roi_means = []
                    start_time = None
                    recording_enabled = False
                elif key == ord(" "):
                    if recording_enabled:
                        stop_recording()
                    else:
                        start_recording()
                elif roi_edit_mode and key == ord("a"):
                    rotate_roi(-ROI_EDIT_ANGLE_STEP)
                elif roi_edit_mode and key == ord("d"):
                    rotate_roi(ROI_EDIT_ANGLE_STEP)
                elif roi_edit_mode and key == ord("s"):
                    stretch_roi(-ROI_EDIT_LENGTH_STEP)
                elif roi_edit_mode and key == ord("w"):
                    stretch_roi(ROI_EDIT_LENGTH_STEP)
                elif roi_edit_mode and key == ord("z"):
                    stretch_roi_height(-ROI_EDIT_HEIGHT_STEP)
                elif roi_edit_mode and key == ord("x"):
                    stretch_roi_height(ROI_EDIT_HEIGHT_STEP)
                elif roi_edit_mode and key == ord("j"):
                    move_roi(-ROI_EDIT_MOVE_STEP, 0)
                elif roi_edit_mode and key == ord("l"):
                    move_roi(ROI_EDIT_MOVE_STEP, 0)
                elif roi_edit_mode and key == ord("i"):
                    move_roi(0, -ROI_EDIT_MOVE_STEP)
                elif roi_edit_mode and key == ord("k"):
                    move_roi(0, ROI_EDIT_MOVE_STEP)

        finally:
            try:
                cam.stop()
            finally:
                cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
