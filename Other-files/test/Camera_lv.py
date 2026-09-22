import cv2
import numpy as np
import pco


EXPOSURE_S = 0.02
RING_BUFFER_FRAMES = 10

VIDEO_WINDOW = "pco.edge live - raw image"
WINDOW_WIDTH = 1500
WINDOW_HEIGHT = 1200

MIN_ZOOM = 1.0
MAX_ZOOM = 20.0
ZOOM_FACTOR = 1.2

zoom = 1.0
center_x = None
center_y = None
latest_image_shape = None

is_panning = False
pan_start_mouse_x = 0
pan_start_mouse_y = 0
pan_start_center_x = 0.0
pan_start_center_y = 0.0
pan_image_per_window_x = 1.0
pan_image_per_window_y = 1.0


def mono16_to_preview(image_u16):
    p1, p99 = np.percentile(image_u16, (1, 99.8))

    preview = np.clip(
        (image_u16.astype(np.float32) - p1)
        * 255.0
        / max(p99 - p1, 1.0),
        0,
        255,
    )

    return preview.astype(np.uint8)


def get_crop_bounds(image_width, image_height):
    global center_x, center_y

    if center_x is None:
        center_x = image_width / 2.0

    if center_y is None:
        center_y = image_height / 2.0

    crop_width = max(1, int(image_width / zoom))
    crop_height = max(1, int(image_height / zoom))

    center_x = float(np.clip(
        center_x,
        crop_width / 2.0,
        image_width - crop_width / 2.0,
    ))

    center_y = float(np.clip(
        center_y,
        crop_height / 2.0,
        image_height - crop_height / 2.0,
    ))

    x1 = int(round(center_x - crop_width / 2.0))
    y1 = int(round(center_y - crop_height / 2.0))

    x1 = int(np.clip(x1, 0, image_width - crop_width))
    y1 = int(np.clip(y1, 0, image_height - crop_height))

    return x1, y1, x1 + crop_width, y1 + crop_height


def make_zoomed_display(image, window_width, window_height):
    image_height, image_width = image.shape[:2]

    x1, y1, x2, y2 = get_crop_bounds(
        image_width,
        image_height,
    )

    cropped = image[y1:y2, x1:x2]

    scale = min(
        window_width / cropped.shape[1],
        window_height / cropped.shape[0],
    )

    display_width = max(1, int(cropped.shape[1] * scale))
    display_height = max(1, int(cropped.shape[0] * scale))

    resized = cv2.resize(
        cropped,
        (display_width, display_height),
        interpolation=cv2.INTER_LINEAR,
    )

    canvas = np.zeros(
        (window_height, window_width),
        dtype=np.uint8,
    )

    offset_x = (window_width - display_width) // 2
    offset_y = (window_height - display_height) // 2

    canvas[
        offset_y:offset_y + display_height,
        offset_x:offset_x + display_width,
    ] = resized

    mapping = {
        "crop_x1": x1,
        "crop_y1": y1,
        "crop_width": x2 - x1,
        "crop_height": y2 - y1,
        "offset_x": offset_x,
        "offset_y": offset_y,
        "display_width": display_width,
        "display_height": display_height,
    }

    return canvas, mapping


latest_mapping = None


def update_pan_position(mouse_x, mouse_y):
    global center_x, center_y

    center_x = pan_start_center_x - (
        mouse_x - pan_start_mouse_x
    ) * pan_image_per_window_x
    center_y = pan_start_center_y - (
        mouse_y - pan_start_mouse_y
    ) * pan_image_per_window_y

    image_height, image_width = latest_image_shape[:2]
    get_crop_bounds(image_width, image_height)


def mouse_callback(event, x, y, flags, param):
    global zoom, center_x, center_y
    global is_panning
    global pan_start_mouse_x, pan_start_mouse_y
    global pan_start_center_x, pan_start_center_y
    global pan_image_per_window_x, pan_image_per_window_y

    if latest_image_shape is None or latest_mapping is None:
        return

    mapping = latest_mapping

    offset_x = mapping["offset_x"]
    offset_y = mapping["offset_y"]
    display_width = mapping["display_width"]
    display_height = mapping["display_height"]

    cursor_on_image = (
        offset_x <= x < offset_x + display_width
        and offset_y <= y < offset_y + display_height
    )

    if event == cv2.EVENT_LBUTTONDOWN:
        if not cursor_on_image:
            return

        is_panning = True
        pan_start_mouse_x = x
        pan_start_mouse_y = y
        pan_start_center_x = (
            mapping["crop_x1"] + mapping["crop_width"] / 2.0
        )
        pan_start_center_y = (
            mapping["crop_y1"] + mapping["crop_height"] / 2.0
        )
        pan_image_per_window_x = (
            mapping["crop_width"] / display_width
        )
        pan_image_per_window_y = (
            mapping["crop_height"] / display_height
        )
        return

    if event == cv2.EVENT_MOUSEMOVE and is_panning:
        if flags & cv2.EVENT_FLAG_LBUTTON:
            update_pan_position(x, y)
        else:
            is_panning = False
        return

    if event == cv2.EVENT_LBUTTONUP:
        if is_panning:
            update_pan_position(x, y)
            is_panning = False
        return

    if event != cv2.EVENT_MOUSEWHEEL or not cursor_on_image:
        return

    mouse_image_x = mapping["crop_x1"] + (
        (x - offset_x)
        / display_width
        * mapping["crop_width"]
    )

    mouse_image_y = mapping["crop_y1"] + (
        (y - offset_y)
        / display_height
        * mapping["crop_height"]
    )

    old_zoom = zoom

    if flags > 0:
        zoom = min(zoom * ZOOM_FACTOR, MAX_ZOOM)
    else:
        zoom = max(zoom / ZOOM_FACTOR, MIN_ZOOM)

    if zoom == old_zoom:
        return

    image_height, image_width = latest_image_shape[:2]

    old_crop_width = image_width / old_zoom
    old_crop_height = image_height / old_zoom

    new_crop_width = image_width / zoom
    new_crop_height = image_height / zoom

    relative_x = (
        mouse_image_x - mapping["crop_x1"]
    ) / max(old_crop_width, 1.0)

    relative_y = (
        mouse_image_y - mapping["crop_y1"]
    ) / max(old_crop_height, 1.0)

    new_x1 = mouse_image_x - relative_x * new_crop_width
    new_y1 = mouse_image_y - relative_y * new_crop_height

    center_x = new_x1 + new_crop_width / 2.0
    center_y = new_y1 + new_crop_height / 2.0

    center_x = float(np.clip(
        center_x,
        new_crop_width / 2.0,
        image_width - new_crop_width / 2.0,
    ))

    center_y = float(np.clip(
        center_y,
        new_crop_height / 2.0,
        image_height - new_crop_height / 2.0,
    ))


def draw_corner_labels(display):
    h, w = display.shape[:2]

    font = cv2.FONT_HERSHEY_SIMPLEX
    scale = 2.8
    thick = 6
    color = (0, 255, 255)
    margin = 35

    cv2.putText(display, "1", (margin, margin + 70), font, scale, color, thick, cv2.LINE_AA)

    cv2.putText(display, "2", (margin, h - margin), font, scale, color, thick, cv2.LINE_AA)

    text3_size, _ = cv2.getTextSize("3", font, scale, thick)
    cv2.putText(
        display,
        "3",
        (w - text3_size[0] - margin, h - margin),
        font,
        scale,
        color,
        thick,
        cv2.LINE_AA,
    )

    text4_size, _ = cv2.getTextSize("4", font, scale, thick)
    cv2.putText(
        display,
        "4",
        (w - text4_size[0] - margin, margin + 70),
        font,
        scale,
        color,
        thick,
        cv2.LINE_AA,
    )


def main():
    global latest_image_shape, latest_mapping
    global zoom, center_x, center_y

    with pco.Camera() as camera:
        camera.default_configuration()
        camera.exposure_time = EXPOSURE_S

        camera.configuration = {
            "timestamp": "off",
            "metadata": "off",
        }

        cv2.namedWindow(VIDEO_WINDOW, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(
            VIDEO_WINDOW,
            WINDOW_WIDTH,
            WINDOW_HEIGHT,
        )
        cv2.setMouseCallback(VIDEO_WINDOW, mouse_callback)

        camera.record(
            RING_BUFFER_FRAMES,
            "ring buffer",
        )

        print("Camera started.")
        print("Mouse wheel: zoom at cursor")
        print("Left mouse drag: pan image")
        print("r: reset zoom")
        print("q or Esc: quit")

        try:
            while True:
                if cv2.getWindowProperty(
                    VIDEO_WINDOW,
                    cv2.WND_PROP_VISIBLE,
                ) < 1:
                    break

                camera.wait_for_new_image()
                image_raw, _ = camera.image(0xFFFFFFFF)

                preview = mono16_to_preview(image_raw)
                latest_image_shape = preview.shape

                if center_x is None or center_y is None:
                    image_height, image_width = preview.shape[:2]
                    center_x = image_width / 2.0
                    center_y = image_height / 2.0

                _, _, window_width, window_height = (
                    cv2.getWindowImageRect(VIDEO_WINDOW)
                )

                window_width = max(window_width, 320)
                window_height = max(window_height, 240)

                display_gray, latest_mapping = make_zoomed_display(
                    preview,
                    window_width,
                    window_height,
                )

                display = cv2.cvtColor(display_gray, cv2.COLOR_GRAY2BGR)

                yellow = (0, 255, 255)

                cv2.putText(
                    display,
                    f"Zoom: {zoom:.2f}x",
                    (20, 30),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.7,
                    yellow,
                    2,
                    cv2.LINE_AA,
                )

                draw_corner_labels(display)

                cv2.imshow(VIDEO_WINDOW, display)

                key = cv2.waitKey(1) & 0xFF

                if key == ord("r"):
                    zoom = 1.0
                    image_height, image_width = preview.shape[:2]
                    center_x = image_width / 2.0
                    center_y = image_height / 2.0

                elif key in (ord("q"), 27):
                    break

        finally:
            try:
                camera.stop()
            except Exception:
                pass

            cv2.destroyAllWindows()
            print("Camera stopped.")


if __name__ == "__main__":
    main()
