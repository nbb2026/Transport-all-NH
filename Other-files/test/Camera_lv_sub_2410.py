import csv
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import simpledialog, messagebox

import cv2
import numpy as np
import pco
import pyvisa
from PIL import Image, ImageDraw, ImageFont


EXPOSURE_S = 0.20
RING_BUFFER_FRAMES = 10

VIDEO_WINDOW = "pco.edge live"
TIME_PLOT_WINDOW = "ROI intensity vs time"
VI_PLOT_WINDOW = "Voltage vs ROI intensity"

KEITHLEY_RESOURCE = "GPIB0::24::INSTR"  # Change to your 2410 VISA address
CURRENT_COMPLIANCE_A = 1e-3
MAX_ABS_SCAN_VOLTAGE_V = 10.0

DEFAULT_START_V = 1.0
DEFAULT_END_V = -1.0
DEFAULT_STEP_V = 0.1

SETTLE_DELAY_S = 2.0
COLLECT_TIME_S = 10.0

DRIFT_DOWNSAMPLE = 4
MAX_DRIFT_PIXELS = 80

ROI_EDIT_ANGLE_STEP = 1.0
ROI_EDIT_LENGTH_STEP = 1.0
ROI_EDIT_HEIGHT_STEP = 1.0
ROI_EDIT_MOVE_STEP = 5.0

MAX_PLOT_POINTS = 30000
RESULTS_DIRECTORY = (
    Path(__file__).resolve().parent / f"{Path(__file__).stem}_Results"
)

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
plot_buttons = {}

latest_display_shape = None
latest_image_shape = None
latest_offset = (0, 0)
latest_scale = 1.0
latest_base_scale = 1.0
latest_window_size = (1000, 800)

roi_times = []
roi_means = []
roi_voltages = []
roi_phases = []

step_voltages = []
step_mean_intensities = []
step_std_intensities = []
step_sample_counts = []

current_step_intensities = []

scan_running = False
scan_finished = False
scan_phase = "idle"
scan_start_time = None
phase_start_time = None
collect_start_time = None
voltage_index = 0
voltage_sequence = []

current_set_voltage = 0.0
current_output_voltage = 0.0
last_save_message = ""


class Keithley2410:
    def __init__(self, resource_name):
        self.resource_name = resource_name
        self.rm = None
        self.inst = None
        self.last_set_voltage = 0.0
        self.output_enabled = False

    def write(self, command, check=True):
        self.inst.write(command)
        if check:
            error = self.inst.query("SYST:ERR?").strip()
            error_code = int(error.split(",", 1)[0].strip())
            if error_code != 0:
                raise RuntimeError(f"2410 SCPI error after '{command}': {error}")

    def __enter__(self):
        try:
            self.rm = pyvisa.ResourceManager()
            self.inst = self.rm.open_resource(self.resource_name)
            self.inst.timeout = 5000
            self.inst.write_termination = "\n"
            self.inst.read_termination = "\n"

            identity = self.inst.query("*IDN?").strip()
            if "2410" not in identity.upper():
                raise RuntimeError(
                    f"Refusing to control unexpected instrument at "
                    f"{self.resource_name}: {identity}"
                )

            self.inst.write("*CLS")
            self.write(":SOUR:FUNC VOLT")
            self.write(":SOUR:VOLT:MODE FIXED")
            self.write(":SENS:FUNC 'CURR'")
            self.write(f":SENS:CURR:PROT {CURRENT_COMPLIANCE_A}")
            self.write(":SOUR:VOLT 0")
            self.write(":OUTP OFF")
            return self
        except Exception:
            try:
                self.output_off()
            except Exception:
                pass
            if self.inst is not None:
                self.inst.close()
            if self.rm is not None:
                self.rm.close()
            raise

    def enable_output(self):
        self.write(":SOUR:VOLT 0")
        self.write(":OUTP ON")
        self.output_enabled = True

    def set_voltage(self, voltage):
        if abs(float(voltage)) > MAX_ABS_SCAN_VOLTAGE_V:
            raise ValueError(
                f"Requested voltage {voltage} V exceeds the software limit "
                f"of ±{MAX_ABS_SCAN_VOLTAGE_V} V."
            )
        self.last_set_voltage = float(voltage)
        self.write(f":SOUR:VOLT {voltage:.6f}")

    def read_source_setpoint(self):
        return float(self.inst.query(":SOUR:VOLT?").strip())

    def output_off(self):
        if self.inst is not None:
            errors = []
            for command in (":SOUR:VOLT 0", ":OUTP OFF"):
                try:
                    self.inst.write(command)
                except Exception as exc:
                    errors.append(f"{command}: {exc}")
            self.output_enabled = False
            self.last_set_voltage = 0.0
            if errors:
                raise RuntimeError("2410 shutdown failed: " + "; ".join(errors))

    def __exit__(self, exc_type, exc, tb):
        try:
            self.output_off()
        except Exception as shutdown_error:
            print(shutdown_error)
        if self.inst is not None:
            for command in ("*RST", "*CLS", ":SYST:LOC"):
                try:
                    self.inst.write(command)
                except Exception:
                    pass
        if self.inst is not None:
            self.inst.close()
        if self.rm is not None:
            self.rm.close()


def ask_scan_params():
    root = tk.Tk()
    root.withdraw()

    start_v = simpledialog.askfloat("Start voltage", "Start voltage V:", initialvalue=DEFAULT_START_V, parent=root)
    if start_v is None:
        root.destroy()
        return None

    end_v = simpledialog.askfloat("End voltage", "End voltage V:", initialvalue=DEFAULT_END_V, parent=root)
    if end_v is None:
        root.destroy()
        return None

    step_v = simpledialog.askfloat("Voltage step", "Step V:", initialvalue=DEFAULT_STEP_V, parent=root)
    if step_v is None:
        root.destroy()
        return None

    step_v = abs(float(step_v))

    if step_v <= 0:
        messagebox.showerror("Invalid step", "Step must be greater than 0.", parent=root)
        root.destroy()
        return None

    if abs(start_v) > MAX_ABS_SCAN_VOLTAGE_V or abs(end_v) > MAX_ABS_SCAN_VOLTAGE_V:
        messagebox.showerror(
            "Voltage limit",
            f"Start and end voltages must be within ±{MAX_ABS_SCAN_VOLTAGE_V:g} V.",
            parent=root,
        )
        root.destroy()
        return None

    root.destroy()
    return float(start_v), float(end_v), step_v


def build_voltage_sequence(start_v, end_v, step_v):
    direction = 1.0 if end_v >= start_v else -1.0
    signed_step = direction * abs(step_v)

    values = []
    v = start_v

    if direction > 0:
        while v < end_v:
            values.append(round(v, 10))
            v += signed_step
    else:
        while v > end_v:
            values.append(round(v, 10))
            v += signed_step

    if not values or values[-1] != round(end_v, 10):
        values.append(round(end_v, 10))

    return_values = values[-2::-1] if len(values) > 1 else []
    return [float(x) for x in values + return_values]


def save_csv_files():
    global last_save_message

    if len(roi_times) == 0 and len(step_voltages) == 0:
        last_save_message = "No data to save"
        print("\nNo data to save.")
        return

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    RESULTS_DIRECTORY.mkdir(parents=True, exist_ok=True)
    save_path = RESULTS_DIRECTORY
    time_csv = save_path / f"ROI intensity vs time {timestamp}.csv"
    vi_csv = save_path / f"Voltage vs ROI mean intensity {timestamp}.csv"

    with time_csv.open("x", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["time_s", "set_voltage_V", "roi_mean_intensity", "phase"])
        for t, v, m, p in zip(roi_times, roi_voltages, roi_means, roi_phases):
            writer.writerow([f"{t:.6f}", f"{v:.6f}", f"{m:.6f}", p])

    with vi_csv.open("x", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["set_voltage_V", "mean_roi_intensity", "std_roi_intensity", "sample_count"])
        for v, mean_i, std_i, n in zip(
            step_voltages,
            step_mean_intensities,
            step_std_intensities,
            step_sample_counts,
        ):
            writer.writerow([f"{v:.6f}", f"{mean_i:.6f}", f"{std_i:.6f}", int(n)])

    last_save_message = f"Saved CSV at {timestamp}"
    print(f"\nSaved:\n{time_csv}\n{vi_csv}")


def get_arial_font(size):
    for path in ["C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/Arial.ttf"]:
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


def diff_to_gray_preview(diff):
    max_abs = np.percentile(np.abs(diff), 99.8)
    max_abs = max(max_abs, 1)
    gray = 128.0 + diff.astype(np.float32) * 127.0 / max_abs
    return np.clip(gray, 0, 255).astype(np.uint8)


def estimate_drift_shift(background_u16, image_u16):
    bg = background_u16.astype(np.float32)
    img = image_u16.astype(np.float32)

    bg_small = cv2.resize(bg, None, fx=1.0 / DRIFT_DOWNSAMPLE, fy=1.0 / DRIFT_DOWNSAMPLE, interpolation=cv2.INTER_AREA)
    img_small = cv2.resize(img, None, fx=1.0 / DRIFT_DOWNSAMPLE, fy=1.0 / DRIFT_DOWNSAMPLE, interpolation=cv2.INTER_AREA)

    bg_small -= np.mean(bg_small)
    img_small -= np.mean(img_small)

    shift_small, response = cv2.phaseCorrelate(bg_small, img_small)

    dx = shift_small[0] * DRIFT_DOWNSAMPLE
    dy = shift_small[1] * DRIFT_DOWNSAMPLE

    if abs(dx) > MAX_DRIFT_PIXELS or abs(dy) > MAX_DRIFT_PIXELS:
        dx, dy = 0.0, 0.0

    return dx, dy, response


def drift_correct_image(image_u16, dx, dy):
    image_f32 = image_u16.astype(np.float32)
    matrix = np.array([[1, 0, -dx], [0, 1, -dy]], dtype=np.float32)

    return cv2.warpAffine(
        image_f32,
        matrix,
        (image_u16.shape[1], image_u16.shape[0]),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REFLECT,
    )


def subtract_background_after_drift(image_u16, background_u16):
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
    return int(np.clip(x_img, 0, w - 1)), int(np.clip(y_img, 0, h - 1))


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
    if roi is not None:
        roi["angle"] = (roi["angle"] + delta_angle) % 180.0


def stretch_roi(delta_width):
    if roi is not None:
        roi["w"] = max(2.0, roi["w"] + delta_width)


def stretch_roi_height(delta_height):
    if roi is not None:
        roi["h"] = max(2.0, roi["h"] + delta_height)


def move_roi(dx, dy):
    if roi is None:
        return

    roi["cx"] += dx
    roi["cy"] += dy

    if latest_image_shape is not None:
        h, w = latest_image_shape[:2]
        roi["cx"] = float(np.clip(roi["cx"], 0, w - 1))
        roi["cy"] = float(np.clip(roi["cy"], 0, h - 1))


def start_voltage_scan(k2410):
    global scan_running, scan_finished, scan_phase
    global roi_times, roi_means, roi_voltages, roi_phases
    global step_voltages, step_mean_intensities, step_std_intensities, step_sample_counts
    global current_step_intensities
    global scan_start_time, phase_start_time, collect_start_time
    global voltage_index, voltage_sequence
    global current_set_voltage, current_output_voltage

    if roi is None:
        print("Please select ROI first.")
        return

    params = ask_scan_params()
    if params is None:
        return

    start_v, end_v, step_v = params
    voltage_sequence = build_voltage_sequence(start_v, end_v, step_v)

    if not voltage_sequence:
        print("Voltage sequence is empty.")
        return

    roi_times = []
    roi_means = []
    roi_voltages = []
    roi_phases = []

    step_voltages = []
    step_mean_intensities = []
    step_std_intensities = []
    step_sample_counts = []
    current_step_intensities = []

    voltage_index = 0
    scan_start_time = time.time()
    phase_start_time = scan_start_time
    collect_start_time = None

    scan_running = True
    scan_finished = False
    scan_phase = "delay"

    current_set_voltage = voltage_sequence[voltage_index]
    try:
        k2410.enable_output()
        k2410.set_voltage(current_set_voltage)
        current_output_voltage = k2410.read_source_setpoint()
    except Exception:
        try:
            k2410.output_off()
        finally:
            scan_running = False
            scan_phase = "idle"
            current_set_voltage = 0.0
            current_output_voltage = 0.0
        raise

    print(f"\nSet 2410 voltage: {current_set_voltage:.4f} V")
    print(f"Delay {SETTLE_DELAY_S:.1f} s before collecting intensity.")


def stop_voltage_scan(k2410):
    global scan_running, scan_phase, current_set_voltage, current_output_voltage

    try:
        k2410.output_off()
    finally:
        scan_running = False
        scan_phase = "idle"
        current_set_voltage = 0.0
        current_output_voltage = 0.0

    print("\nScan stopped. Keithley 2410 voltage set to 0 V.")


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


def finish_current_voltage_step():
    global step_voltages, step_mean_intensities, step_std_intensities, step_sample_counts

    if len(current_step_intensities) == 0:
        mean_i = np.nan
        std_i = np.nan
        sample_count = 0
    else:
        arr = np.array(current_step_intensities, dtype=np.float64)
        mean_i = float(np.mean(arr))
        std_i = float(np.std(arr))
        sample_count = int(len(arr))

    step_voltages.append(float(current_set_voltage))
    step_mean_intensities.append(mean_i)
    step_std_intensities.append(std_i)
    step_sample_counts.append(sample_count)

    print(
        f"\nVoltage {current_set_voltage:.4f} V finished: "
        f"mean intensity = {mean_i:.4f}, std = {std_i:.4f}, n = {sample_count}"
    )


def update_voltage_scan(k2410):
    global scan_running, scan_finished, scan_phase
    global phase_start_time, collect_start_time
    global voltage_index, current_step_intensities
    global current_set_voltage, current_output_voltage

    current_output_voltage = k2410.read_source_setpoint()

    if not scan_running:
        return

    now = time.time()

    if scan_phase == "delay":
        if now - phase_start_time >= SETTLE_DELAY_S:
            scan_phase = "collect"
            collect_start_time = now
            phase_start_time = now
            current_step_intensities = []
            print(f"\nCollecting ROI intensity for {COLLECT_TIME_S:.1f} s at {current_set_voltage:.4f} V.")

    elif scan_phase == "collect":
        if now - collect_start_time >= COLLECT_TIME_S:
            finish_current_voltage_step()

            voltage_index += 1

            if voltage_index >= len(voltage_sequence):
                scan_running = False
                scan_finished = True
                scan_phase = "finished"

                k2410.output_off()
                current_set_voltage = 0.0
                current_output_voltage = 0.0

                print("\nScan finished. Keithley 2410 voltage set to 0 V.")
                return

            current_set_voltage = voltage_sequence[voltage_index]
            k2410.set_voltage(current_set_voltage)
            current_output_voltage = k2410.read_source_setpoint()

            scan_phase = "delay"
            phase_start_time = now
            collect_start_time = None
            current_step_intensities = []

            print(f"\nSet 2410 voltage: {current_set_voltage:.4f} V")
            print(f"Delay {SETTLE_DELAY_S:.1f} s before collecting intensity.")


def get_phase_remaining_time():
    if not scan_running:
        return 0.0

    now = time.time()

    if scan_phase == "delay":
        return max(0.0, SETTLE_DELAY_S - (now - phase_start_time))

    if scan_phase == "collect":
        return max(0.0, COLLECT_TIME_S - (now - collect_start_time))

    return 0.0


def mouse_callback(event, x, y, flags, param):
    global zoom, pan_x, pan_y
    global is_panning
    global pan_start_mouse_x, pan_start_mouse_y
    global pan_start_x, pan_start_y
    global roi_start, roi_end, is_selecting_roi, roi
    global roi_times, roi_means, roi_voltages, roi_phases
    global step_voltages, step_mean_intensities, step_std_intensities, step_sample_counts
    global current_step_intensities, scan_running, scan_finished, scan_phase

    if latest_image_shape is None:
        return

    if event == cv2.EVENT_MOUSEWHEEL:
        img_x, img_y = window_to_image_xy(x, y)
        zoom = min(zoom * 1.2, 10.0) if flags > 0 else max(zoom / 1.2, 0.2)

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
            if scan_running:
                return

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

        roi = make_roi_from_drag(roi_start, roi_end)

        roi_times = []
        roi_means = []
        roi_voltages = []
        roi_phases = []

        step_voltages = []
        step_mean_intensities = []
        step_std_intensities = []
        step_sample_counts = []
        current_step_intensities = []

        scan_running = False
        scan_finished = False
        scan_phase = "idle"

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

    k2410 = param

    if point_in_rect(x, y, plot_buttons.get("record", (-1, -1, -1, -1))):
        if scan_running:
            stop_voltage_scan(k2410)
        else:
            start_voltage_scan(k2410)

    elif point_in_rect(x, y, plot_buttons.get("save", (-1, -1, -1, -1))):
        save_csv_files()

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


def make_aspect_display(preview_u8, window_w, window_h):
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


def draw_roi(display_bgr, diff, dx, dy):
    active_roi = get_active_roi()

    if active_roi is None:
        cv2.putText(
            display_bgr,
            f"Shift+drag: select ROI | drag: pan | 2410 source setpoint={current_output_voltage:.4f} V | drift dx={dx:.2f}, dy={dy:.2f}",
            (20, 30),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            (0, 255, 255),
            2,
            cv2.LINE_AA,
        )
        return None

    points = roi_box_points(active_roi)
    window_points = np.array([image_to_window_xy(px, py) for px, py in points], dtype=np.int32)

    color = (0, 255, 255) if roi_edit_mode else (0, 255, 0)
    cv2.polylines(display_bgr, [window_points], True, color, 2, cv2.LINE_AA)

    mask = np.zeros(diff.shape[:2], dtype=np.uint8)
    cv2.fillPoly(mask, [np.round(points).astype(np.int32)], 255)

    roi_img = diff[mask > 0]

    if roi_img.size == 0:
        return None

    mean_val = float(np.mean(roi_img))

    text = (
        f"2410 source setpoint={current_output_voltage:.4f} V | "
        f"set={current_set_voltage:.4f} V | "
        f"phase={scan_phase} | remain={get_phase_remaining_time():.1f}s | "
        f"mean={mean_val:.2f} | drift dx={dx:.2f}, dy={dy:.2f}"
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
        (20, y1, 145, y2),
        "STOP SCAN" if scan_running else "START SCAN",
        active=scan_running,
    )

    draw_button(plot, "save", (155, y1, 255, y2), "SAVE CSV")

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


def draw_y_axis_ticks(plot, x_axis, y_top, y_bottom, y_min, y_max, label):
    put_arial_text(plot, label, (8, y_top - 24), ARIAL_12, color=(0, 0, 0))

    for i in range(5):
        frac = i / 4
        value = y_max - frac * (y_max - y_min)
        y_pix = int(round(y_top + frac * (y_bottom - y_top)))

        cv2.line(plot, (x_axis - 4, y_pix), (x_axis, y_pix), (0, 0, 0), 1)
        cv2.line(plot, (x_axis + 1, y_pix), (plot.shape[1] - 25, y_pix), (235, 235, 235), 1)

        label_text = f"{value:.1f}"
        bbox = ARIAL_10.getbbox(label_text)
        text_w = bbox[2] - bbox[0]

        put_arial_text(plot, label_text, (x_axis - 8 - text_w, y_pix - 7), ARIAL_10, color=(0, 0, 0))


def make_time_plot(width=1000, height=460):
    global plot_buttons

    plot_buttons = {}
    plot = np.full((height, width, 3), 255, dtype=np.uint8)

    margin_l = 95
    margin_r = 25
    margin_t = 110
    margin_b = 62

    x0 = margin_l
    x1 = width - margin_r
    y0 = height - margin_b
    y1 = margin_t

    cv2.rectangle(plot, (x0, y1), (x1, y0), (0, 0, 0), 1)

    status = "RUNNING" if scan_running else ("FINISHED" if scan_finished else "READY")

    put_arial_text(
        plot,
        f"ROI intensity vs time | 2410 source setpoint={current_output_voltage:.4f} V | set={current_set_voltage:.4f} V | {status}",
        (margin_l, 12),
        ARIAL_14,
        color=(0, 0, 0),
    )

    put_arial_text(
        plot,
        f"phase={scan_phase} | remaining={get_phase_remaining_time():.1f} s | delay={SETTLE_DELAY_S:.1f} s | collect={COLLECT_TIME_S:.1f} s",
        (margin_l, 76),
        ARIAL_12,
        color=(0, 0, 0),
    )

    if last_save_message:
        put_arial_text(plot, last_save_message, (275, 40), ARIAL_11, color=(0, 100, 0))

    draw_roi_edit_buttons(plot, width)

    if len(roi_times) < 2:
        draw_y_axis_ticks(plot, x0, y1, y0, 0.0, 1.0, "Intensity")
        message = "Select ROI, then click START SCAN or press SPACE"
        put_arial_text(plot, message, (margin_l + 20, height // 2 - 10), ARIAL_14, color=(80, 80, 80))
        return plot

    t = np.array(roi_times[-MAX_PLOT_POINTS:], dtype=np.float64)
    y = np.array(roi_means[-MAX_PLOT_POINTS:], dtype=np.float64)
    v = np.array(roi_voltages, dtype=np.float64)

    t_min, t_max = float(t[0]), float(t[-1])
    y_min, y_max = float(np.min(y)), float(np.max(y))

    if t_max <= t_min:
        t_max = t_min + 1.0

    y_pad = max((y_max - y_min) * 0.08, 1.0)
    y_min -= y_pad
    y_max += y_pad

    draw_y_axis_ticks(plot, x0, y1, y0, y_min, y_max, "Intensity")

    put_arial_text(plot, "Time (s)", (width // 2 - 28, height - 24), ARIAL_12, color=(0, 0, 0))
    put_arial_text(plot, f"{t_min:.1f}", (x0, height - 43), ARIAL_10, color=(0, 0, 0))

    t_max_label = f"{t_max:.1f}"
    bbox = ARIAL_10.getbbox(t_max_label)
    text_w = bbox[2] - bbox[0]
    put_arial_text(plot, t_max_label, (x1 - text_w, height - 43), ARIAL_10, color=(0, 0, 0))

    xs = x0 + (t - t_min) / (t_max - t_min) * (x1 - x0)
    ys = y0 - (y - y_min) / (y_max - y_min) * (y0 - y1)

    points = np.column_stack((xs, ys)).astype(np.int32)
    cv2.polylines(plot, [points], False, (0, 120, 255), 1, cv2.LINE_AA)

    voltage_change_idx = np.where(np.diff(v) != 0)[0] + 1
    for idx in voltage_change_idx:
        x = int(xs[idx])
        cv2.line(plot, (x, y1), (x, y0), (180, 180, 180), 1)
        put_arial_text(plot, f"{v[idx]:.2f}V", (x + 3, y1 + 5), ARIAL_10, color=(80, 80, 80))

    return plot


def make_voltage_intensity_plot(width=700, height=460):
    plot = np.full((height, width, 3), 255, dtype=np.uint8)

    margin_l = 95
    margin_r = 35
    margin_t = 70
    margin_b = 70

    x0 = margin_l
    x1 = width - margin_r
    y0 = height - margin_b
    y1 = margin_t

    cv2.rectangle(plot, (x0, y1), (x1, y0), (0, 0, 0), 1)

    put_arial_text(plot, "Voltage vs ROI mean intensity", (margin_l, 18), ARIAL_14, color=(0, 0, 0))

    completed_v = list(step_voltages)
    completed_i = list(step_mean_intensities)

    if scan_phase == "collect" and len(current_step_intensities) > 0:
        completed_v.append(current_set_voltage)
        completed_i.append(float(np.mean(current_step_intensities)))

    if len(completed_v) < 1:
        draw_y_axis_ticks(plot, x0, y1, y0, 0.0, 1.0, "Intensity")
        put_arial_text(plot, "No completed voltage point yet", (margin_l + 20, height // 2), ARIAL_14, color=(80, 80, 80))
        put_arial_text(plot, "Voltage (V)", (width // 2 - 32, height - 28), ARIAL_12, color=(0, 0, 0))
        return plot

    x = np.array(completed_v, dtype=np.float64)
    y = np.array(completed_i, dtype=np.float64)

    valid = np.isfinite(x) & np.isfinite(y)
    x = x[valid]
    y = y[valid]

    if len(x) == 0:
        return plot

    x_min, x_max = float(np.min(x)), float(np.max(x))
    y_min, y_max = float(np.min(y)), float(np.max(y))

    x_pad = max((x_max - x_min) * 0.08, 0.1) if x_max > x_min else max(abs(x_max) * 0.05, 0.1)
    y_pad = max((y_max - y_min) * 0.08, 1.0) if y_max > y_min else max(abs(y_max) * 0.05, 1.0)

    x_min -= x_pad
    x_max += x_pad
    y_min -= y_pad
    y_max += y_pad

    draw_y_axis_ticks(plot, x0, y1, y0, y_min, y_max, "Intensity")

    for i in range(5):
        frac = i / 4
        value = x_min + frac * (x_max - x_min)
        x_pix = int(round(x0 + frac * (x1 - x0)))
        cv2.line(plot, (x_pix, y0), (x_pix, y0 + 4), (0, 0, 0), 1)

        label = f"{value:.2f}"
        bbox = ARIAL_10.getbbox(label)
        text_w = bbox[2] - bbox[0]
        put_arial_text(plot, label, (x_pix - text_w // 2, y0 + 10), ARIAL_10, color=(0, 0, 0))

    put_arial_text(plot, "Voltage (V)", (width // 2 - 32, height - 28), ARIAL_12, color=(0, 0, 0))

    xs = x0 + (x - x_min) / (x_max - x_min) * (x1 - x0)
    ys = y0 - (y - y_min) / (y_max - y_min) * (y0 - y1)

    points = np.column_stack((xs, ys)).astype(np.int32)

    if len(points) >= 2:
        cv2.polylines(plot, [points], False, (255, 80, 0), 1, cv2.LINE_AA)

    for px, py in points:
        cv2.circle(plot, (int(px), int(py)), 4, (255, 80, 0), -1, cv2.LINE_AA)

    latest_label = f"latest avg = {y[-1]:.2f} @ {x[-1]:.4f} V"
    put_arial_text(plot, latest_label, (margin_l, 44), ARIAL_12, color=(0, 0, 0))

    return plot


def main():
    global zoom, pan_x, pan_y, roi
    global roi_times, roi_means, roi_voltages, roi_phases
    global step_voltages, step_mean_intensities, step_std_intensities, step_sample_counts
    global current_step_intensities
    global scan_running, scan_finished, scan_phase
    global current_set_voltage, current_output_voltage

    with Keithley2410(KEITHLEY_RESOURCE) as k2410, pco.Camera() as cam:
        cam.default_configuration()
        cam.exposure_time = EXPOSURE_S

        cam.configuration = {
            "timestamp": "off",
            "metadata": "off",
        }

        cv2.namedWindow(VIDEO_WINDOW, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(VIDEO_WINDOW, 1500, 1200)
        cv2.setMouseCallback(VIDEO_WINDOW, mouse_callback)

        cv2.namedWindow(TIME_PLOT_WINDOW, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(TIME_PLOT_WINDOW, 1000, 460)
        cv2.setMouseCallback(TIME_PLOT_WINDOW, plot_mouse_callback, k2410)

        cv2.namedWindow(VI_PLOT_WINDOW, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(VI_PLOT_WINDOW, 700, 460)

        cam.record(RING_BUFFER_FRAMES, "ring buffer")

        print("Capturing background frame...")
        cam.wait_for_new_image()
        background, _ = cam.image(0xFFFFFFFF)
        background = background.copy()
        print("Background captured.")
        print("Left mouse drag: pan image")
        print("Shift + left mouse drag: select ROI")
        print("Select ROI, then press SPACE or click START SCAN.")

        try:
            while True:
                if any_window_closed(
                    (VIDEO_WINDOW, TIME_PLOT_WINDOW, VI_PLOT_WINDOW)
                ):
                    break

                update_voltage_scan(k2410)

                cam.wait_for_new_image()
                image_raw, _ = cam.image(0xFFFFFFFF)

                diff, dx, dy, response = subtract_background_after_drift(image_raw, background)
                preview = diff_to_gray_preview(diff)

                _, _, window_w, window_h = cv2.getWindowImageRect(VIDEO_WINDOW)
                window_w = max(window_w, 320)
                window_h = max(window_h, 240)

                display = make_aspect_display(preview, window_w, window_h)
                mean_val = draw_roi(display, diff, dx, dy)

                if (
                    scan_running
                    and scan_phase in ("delay", "collect")
                    and roi is not None
                    and mean_val is not None
                ):
                    now = time.time()

                    roi_times.append(now - scan_start_time)
                    roi_means.append(mean_val)
                    roi_voltages.append(current_set_voltage)
                    roi_phases.append(scan_phase)

                    if scan_phase == "collect":
                        current_step_intensities.append(mean_val)

                cv2.imshow(VIDEO_WINDOW, display)
                cv2.imshow(TIME_PLOT_WINDOW, make_time_plot())
                cv2.imshow(VI_PLOT_WINDOW, make_voltage_intensity_plot())

                key = cv2.waitKey(1) & 0xFF

                if key in (ord("+"), ord("=")):
                    zoom = min(zoom * 1.2, 10.0)

                elif key in (ord("-"), ord("_")):
                    zoom = max(zoom / 1.2, 0.2)

                elif key == ord("r"):
                    if scan_running:
                        stop_voltage_scan(k2410)

                    roi = None
                    zoom = 1.0
                    pan_x = 0.0
                    pan_y = 0.0

                    roi_times = []
                    roi_means = []
                    roi_voltages = []
                    roi_phases = []

                    step_voltages = []
                    step_mean_intensities = []
                    step_std_intensities = []
                    step_sample_counts = []
                    current_step_intensities = []

                    scan_running = False
                    scan_finished = False
                    scan_phase = "idle"

                elif key == ord(" "):
                    if scan_running:
                        stop_voltage_scan(k2410)
                    else:
                        start_voltage_scan(k2410)

                elif key == ord("q") or key == 27:
                    break

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
                stop_voltage_scan(k2410)
            except Exception:
                pass

            try:
                cam.stop()
            finally:
                cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
