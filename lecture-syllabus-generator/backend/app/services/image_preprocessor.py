"""Build a bounded, metadata-free model copy without modifying the upload."""
import base64
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError

from app.config import settings
from app.errors import AppError, FileFormatError, FileTooLargeError


@dataclass(frozen=True)
class PreparedImage:
    data_url: str
    metadata: dict


def diagram_detail_images(image: PreparedImage) -> list[tuple[str, str]]:
    """Overlapping quadrants and a center view from the sanitized model copy.

    Keep the global image too: a connector crossing a crop boundary must never
    be inferred from an isolated tile. No file or original image is modified.
    """
    raw = base64.b64decode(image.data_url.split(',', 1)[1])
    with Image.open(BytesIO(raw)) as source:
        width, height = source.size
        boxes = [('左上', (0, 0, (width * 3 + 4) // 5, (height * 3 + 4) // 5)),
                 ('右上', (width * 2 // 5, 0, width, (height * 3 + 4) // 5)),
                 ('左下', (0, height * 2 // 5, (width * 3 + 4) // 5, height)),
                 ('右下', (width * 2 // 5, height * 2 // 5, width, height)),
                 ('中心', (width // 4, height // 4, (width * 3 + 3) // 4, (height * 3 + 3) // 4))]
        result = []
        for label, box in boxes:
            tile = source.crop(box).convert('RGB')
            tile.thumbnail((1536, 1536), Image.Resampling.LANCZOS)
            output = BytesIO()
            tile.save(output, format='JPEG', quality=92, subsampling=0)
            result.append((label, 'data:image/jpeg;base64,' + base64.b64encode(output.getvalue()).decode('ascii')))
        return result


def prepare_image(file_path: str) -> PreparedImage:
    if str(file_path).startswith('cos://'):
        from app.services import object_storage
        with object_storage.get_storage().materialize(file_path, limit=min(settings.max_file_mb * 1024 * 1024,
                                                                         object_storage.MAX_READ)) as local:
            return prepare_image(str(local))
    path = Path(file_path)
    expected_format = {'.jpg': 'JPEG', '.jpeg': 'JPEG', '.png': 'PNG'}.get(path.suffix.lower())
    if expected_format is None:
        raise FileFormatError('真实图片识别仅支持 JPG、JPEG、PNG；PDF 请先逐页转换为图片。')
    limit = settings.max_file_mb * 1024 * 1024
    try:
        with path.open('rb') as source:
            original = source.read(limit + 1)
    except OSError:
        raise FileFormatError('无法读取上传图片，请重新选择文件。') from None
    if len(original) > limit:
        raise FileTooLargeError()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(BytesIO(original), formats=['JPEG', 'PNG']) as header:
                if header.format != expected_format or getattr(header, 'n_frames', 1) != 1:
                    raise FileFormatError('图片实际格式与扩展名不一致，或包含多帧；请转为单张 JPG/PNG。')
                original_size = header.size
                if header.width * header.height > settings.image_max_pixels:
                    raise AppError('IMAGE_DIMENSIONS_EXCEEDED', '图片像素过大，请先缩小后重新上传。')
                header.verify()
            with Image.open(BytesIO(original), formats=['JPEG', 'PNG']) as source:
                orientation = source.getexif().get(274)
                corrected = ImageOps.exif_transpose(source)
                corrected.load()
                oriented_size = corrected.size
                # PNG's 16-bit grayscale samples must be scaled, not clipped
                # by the generic RGB conversion (which treats >255 as white).
                if corrected.mode.startswith('I;16') or (corrected.mode == 'I' and source.format == 'PNG'):
                    transparent = corrected.info.get('transparency')
                    alpha = None
                    if isinstance(transparent, int):
                        # Compare in the original 16-bit space: distinct gray
                        # values can quantize to the same 8-bit value.
                        alpha = corrected.convert('I').point(
                            [0 if value == transparent else 255 for value in range(65536)], 'L',
                        )
                    gray = corrected.convert('F').point(lambda value: value / 257).convert('L')
                    gray.info.pop('transparency', None)
                    corrected = gray.convert('RGBA')
                    if alpha is not None:
                        corrected.putalpha(alpha)
                corrected.thumbnail((settings.image_max_edge, settings.image_max_edge), Image.Resampling.LANCZOS)
                rgba = corrected.convert('RGBA')
                clean = Image.new('RGB', rgba.size, 'white')
                clean.paste(rgba, mask=rgba.getchannel('A'))
                output_format = expected_format
                output = BytesIO()
                clean.save(output, format=output_format, **({'quality': 92, 'subsampling': 0} if output_format == 'JPEG' else {}))
                if output.tell() > limit and output_format == 'PNG':
                    output_format = 'JPEG'
                    output = BytesIO()
                    clean.save(output, format='JPEG', quality=92, subsampling=0)
                prepared = output.getvalue()
                output_size = clean.size
    except (Image.DecompressionBombWarning, Image.DecompressionBombError):
        raise AppError('IMAGE_DIMENSIONS_EXCEEDED', '图片像素过大，请先缩小后重新上传。') from None
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, TypeError):
        raise FileFormatError('图片损坏或无法完整解码，请重新导出 JPG/PNG 后上传。') from None
    if len(prepared) > limit:
        raise FileTooLargeError()
    mime = 'image/png' if output_format == 'PNG' else 'image/jpeg'
    return PreparedImage(
        data_url=f'data:{mime};base64,' + base64.b64encode(prepared).decode('ascii'),
        metadata={
            'version': '1.0.1', 'sha256': sha256(original).hexdigest(),
            'original_width': original_size[0], 'original_height': original_size[1],
            'width': output_size[0], 'height': output_size[1],
            'orientation_corrected': orientation in {2, 3, 4, 5, 6, 7, 8},
            'resized': output_size != oriented_size,
            'original_bytes': len(original), 'prepared_bytes': len(prepared), 'mime_type': mime,
        },
    )
