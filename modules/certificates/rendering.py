from dataclasses import dataclass
from io import BytesIO

import pymupdf as fitz
import segno
from django.core.files.base import ContentFile
from PIL import Image, ImageDraw, ImageFont

from base.enums import StorageProvider
from base.validators import validate_with_schema
from modules.certificates.choices import MAPPABLE_BOX_TYPES, QR_BOX_TYPES, BoxTypeChoices, TextAlignChoices
from modules.certificates.fonts import BUNDLED_FONTS
from modules.certificates.models import GeneratedCertificate
from modules.certificates.naming import resolve_filename
from modules.certificates.schemas import RowDataSchema
from modules.certificates.verification import format_code, new_code, verify_url
from modules.core.models import UploadFile

MIN_FONT_SIZE = 8
PDF_RENDER_DPI = 200


@dataclass
class RenderBox:
    "Box geometry/style in pixel space, ready to hand to `draw_boxes` -- see `_scale_box`."

    key: str
    x: float
    y: float
    width: float
    height: float
    font: str
    font_size: int
    font_color: str
    alignment: str
    type: str


@dataclass
class PreparedImageBox:
    "An image box with its asset already decoded to RGBA -- see `load_prepared_image_box`."

    key: str
    x: float
    y: float
    width: float
    height: float
    image: Image.Image


def load_prepared_image_box(image_box) -> PreparedImageBox:
    "Decode a TemplateImageBox's asset once; call outside the per-row loop -- the asset is invariant across a batch."
    with image_box.image.open() as fh:
        image = Image.open(fh).convert("RGBA")
        image.load()
    return PreparedImageBox(
        key=image_box.key,
        x=image_box.x,
        y=image_box.y,
        width=image_box.width,
        height=image_box.height,
        image=image,
    )


def _scale_box(box, scale: float) -> RenderBox:
    "Scale a PDF-point box (72/inch) to pixel space for a page rasterized at PDF_RENDER_DPI."
    return RenderBox(
        key=box.key,
        x=box.x * scale,
        y=box.y * scale,
        width=box.width * scale,
        height=box.height * scale,
        font=box.font,
        font_size=max(round(box.font_size * scale), MIN_FONT_SIZE),
        font_color=box.font_color,
        alignment=box.alignment,
        type=box.type,
    )


def _scale_image_box(prepared: PreparedImageBox, scale: float) -> PreparedImageBox:
    "Scale a PDF-point image box (72/inch) to pixel space for a page rasterized at PDF_RENDER_DPI. Mirrors `_scale_box`'s geometry math."
    return PreparedImageBox(
        key=prepared.key,
        x=prepared.x * scale,
        y=prepared.y * scale,
        width=prepared.width * scale,
        height=prepared.height * scale,
        image=prepared.image,
    )


def draw_image_boxes(image: Image.Image, image_boxes: list[PreparedImageBox]) -> Image.Image:
    "Paste each image box into its box, preserving aspect ratio (contain-fit, centered)."
    for box in image_boxes:
        src_w, src_h = box.image.size
        scale = min(box.width / src_w, box.height / src_h)
        new_w, new_h = max(1, round(src_w * scale)), max(1, round(src_h * scale))
        resized = box.image.resize((new_w, new_h), Image.Resampling.LANCZOS)

        paste_x = round(box.x + (box.width - new_w) / 2)
        paste_y = round(box.y + (box.height - new_h) / 2)
        image.paste(resized, (paste_x, paste_y), resized)
    return image


def fit_font_size(draw, text, font_path, box_width, box_height, max_size, min_size=MIN_FONT_SIZE):
    "Binary-search the largest font size <= max_size whose rendered bbox fits within the box."

    def fits(size):
        left, top, right, bottom = draw.textbbox((0, 0), text, font=ImageFont.truetype(str(font_path), size))
        return (right - left) <= box_width and (bottom - top) <= box_height

    if max_size <= min_size:
        return min_size
    if fits(max_size):
        return max_size

    lo, hi, best = min_size, max_size, min_size
    while lo <= hi:
        mid = (lo + hi) // 2
        if fits(mid):
            best, lo = mid, mid + 1
        else:
            hi = mid - 1
    return best


def draw_qr(image: Image.Image, box, data: str) -> str | None:
    "Paste a QR code of `data` centred in the box, in `font_color` on a transparent background. Returns a warning, if any."
    try:
        qr = segno.make(data, error="m")
    except segno.DataOverflowError:
        raise ValueError(f"The text for QR box '{box.key}' is too long for a QR code.")
    modules = qr.symbol_size(scale=1, border=1)[0]
    side = min(box.width, box.height)
    buffer = BytesIO()
    # Whole-pixel modules keep the edges sharp enough to scan.
    qr.save(buffer, kind="png", scale=max(1, int(side // modules)), border=1, dark=box.font_color, light=None)
    buffer.seek(0)
    code = Image.open(buffer).convert("RGBA")
    image.paste(code, (round(box.x + (box.width - code.width) / 2), round(box.y + (box.height - code.height) / 2)), code)
    if side < modules:
        return f"QR box '{box.key}' is too small for its content; make it at least {modules} px square."
    return None


def draw_boxes(image: Image.Image, boxes, values: dict[str, str]) -> tuple[Image.Image, list[str]]:
    "Stamp `values[box.key]` into each box, shrinking font size to fit that box only. Returns (image, overflow_warnings)."
    draw = ImageDraw.Draw(image)
    warnings = []
    for box in boxes:
        text = values.get(box.key, "")
        if box.type in QR_BOX_TYPES:
            warning = draw_qr(image, box, text) if text else None
            if warning:
                warnings.append(warning)
            continue
        font_path = BUNDLED_FONTS[box.font]
        size = fit_font_size(draw, text, font_path, box.width, box.height, box.font_size)
        font = ImageFont.truetype(str(font_path), size)

        left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
        text_w, text_h = right - left, bottom - top
        if text_w > box.width or text_h > box.height:
            warnings.append(f"Text for '{box.key}' still overflows its box at the minimum font size ({MIN_FONT_SIZE}).")

        tx = {
            TextAlignChoices.LEFT: box.x,
            TextAlignChoices.RIGHT: box.x + box.width - text_w,
        }.get(box.alignment, box.x + (box.width - text_w) / 2)
        ty = box.y + (box.height - text_h) / 2
        draw.text((tx, ty), text, font=font, fill=box.font_color)
    return image, warnings


def render_image_template(fh, boxes, values: dict[str, str], image_boxes: list[PreparedImageBox]) -> tuple[Image.Image, list[str]]:
    image = draw_image_boxes(Image.open(fh).convert("RGB"), image_boxes)
    return draw_boxes(image, boxes, values)


def render_pdf_template(
    fh, boxes, values: dict[str, str], image_boxes: list[PreparedImageBox], dpi=PDF_RENDER_DPI
) -> tuple[Image.Image, list[str]]:
    "Rasterizes page 1 of the PDF template, then stamps boxes on it as an image."
    doc = fitz.open(stream=fh.read(), filetype="pdf")
    pix = doc.load_page(0).get_pixmap(dpi=dpi)
    image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    scale = dpi / 72
    image = draw_image_boxes(image, [_scale_image_box(box, scale) for box in image_boxes])
    return draw_boxes(image, [_scale_box(box, scale) for box in boxes], values)


def box_values(batch, row: dict, boxes, code: str) -> dict[str, str]:
    "What each box prints: its mapped CSV value, or for verify_* boxes the certificate's code or verify link."
    values = {}
    for box in boxes:
        if box.type in MAPPABLE_BOX_TYPES:
            values[box.key] = row.get(batch.mapping.get(box.key, ""), "")
        elif box.type == BoxTypeChoices.VERIFY_CODE:
            values[box.key] = format_code(code)
        else:
            values[box.key] = verify_url(code)
    return values


def render_file(batch, index: int, row: dict, boxes, image_boxes, code: str) -> tuple[UploadFile, list[str]]:
    "Renders and uploads one certificate for `row` (values already str); raises on failure. Returns the file and font warnings."
    validate_with_schema(RowDataSchema, row)
    values = box_values(batch, row, boxes, code)

    is_pdf = batch.template.file.original_filename.lower().endswith(".pdf")
    with batch.template.file.open() as fh:
        image, warnings = (
            render_pdf_template(fh, boxes, values, image_boxes)
            if is_pdf
            else render_image_template(fh, boxes, values, image_boxes)
        )

    base_name = resolve_filename(batch, index, row)
    buffer = BytesIO()
    if is_pdf:
        image.save(buffer, format="PDF")
        filename = f"{base_name}.pdf"
    else:
        image.save(buffer, format="PNG")
        filename = f"{base_name}.png"

    upload = UploadFile.upload(ContentFile(buffer.getvalue(), name=filename), provider=StorageProvider.CLOUDINARY)
    return upload, warnings


def generate_certificate_for_row(batch, index: int, row: dict, boxes, image_boxes) -> "GeneratedCertificate":
    "Renders one certificate for `row` and records the outcome; never raises -- failures land on the returned row."
    row = {key: (value or "") for key, value in row.items()}
    code = new_code()
    try:
        upload, warnings = render_file(batch, index, row, boxes, image_boxes, code)
        return GeneratedCertificate.objects.create(
            batch=batch,
            row_index=index,
            row_data=row,
            file=upload,
            is_successful=True,
            error_message="; ".join(warnings),
            verification_code=code,
        )
    except Exception as exc:
        return GeneratedCertificate.objects.create(
            batch=batch,
            row_index=index,
            row_data=row,
            is_successful=False,
            error_message=str(exc),
            verification_code=code,
        )
