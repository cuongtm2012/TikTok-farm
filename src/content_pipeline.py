# TikTok Farm - Content Pipeline Module
# Generates slideshow images using Pillow with template-based layouts

import os
import yaml
import json
import logging
import random
from pathlib import Path
from typing import Optional, List, Dict, Tuple
from datetime import datetime

logger = logging.getLogger(__name__)

try:
    from PIL import Image, ImageDraw, ImageFont, ImageFilter
    PILLOW_AVAILABLE = True
except ImportError:
    PILLOW_AVAILABLE = False
    logger.warning("Pillow not installed. Content pipeline disabled.")


class ContentPipeline:
    """Creates TikTok slideshow images from product photos + brand logos + overlay elements.

    Uses template-based layouts defined in config/templates.yaml.
    Each post produces 3-5 composited images stored in output directory.
    """

    def __init__(
        self,
        templates_path: str = "config/templates.yaml",
        output_dir: str = "data/posts/",
        products_dir: str = "content/products/",
        brands_dir: str = "content/brands/",
        images_per_post: int = 5,
        fonts_dir: str = "",
    ):
        self.output_dir = Path(output_dir)
        self.products_dir = Path(products_dir)
        self.brands_dir = Path(brands_dir)
        self.images_per_post = images_per_post
        self.fonts_dir = fonts_dir or os.path.join(os.path.dirname(__file__), "..", "content", "templates")

        # Create dirs
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.products_dir.mkdir(parents=True, exist_ok=True)
        self.brands_dir.mkdir(parents=True, exist_ok=True)

        # Load templates
        self.templates = self._load_templates(templates_path)

        # Try to load fonts
        self._fonts = self._load_fonts()

    def _load_templates(self, templates_path: str) -> Dict:
        """Load layout templates from YAML file."""
        tp = Path(templates_path)
        if not tp.exists():
            logger.warning(f"Templates file not found at {templates_path}, using default template")
            return {
                "default": {
                    "width": 1080,
                    "height": 1920,
                    "background_color": "#FFFFFF",
                    "elements": [],
                }
            }

        try:
            with open(tp, "r") as f:
                data = yaml.safe_load(f)
            templates = data.get("templates", {})
            logger.info(f"Loaded {len(templates)} content templates")
            return templates
        except Exception as e:
            logger.error(f"Failed to load templates: {e}")
            return {
                "default": {
                    "width": 1080,
                    "height": 1920,
                    "background_color": "#FFFFFF",
                    "elements": [],
                }
            }

    def _load_fonts(self) -> Dict:
        """Load available fonts."""
        fonts = {}
        font_paths = [
            "/System/Library/Fonts/Helvetica.ttc",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            os.path.join(self.fonts_dir, "Roboto-Regular.ttf"),
            os.path.join(self.fonts_dir, "Roboto-Bold.ttf"),
        ]

        # Try common default fonts
        for fp in font_paths:
            if os.path.exists(fp):
                name = os.path.splitext(os.path.basename(fp))[0]
                fonts[name] = fp

        if not fonts:
            # Pillow's default will be used
            logger.warning("No custom fonts found, using PIL default")

        return fonts

    def _get_font(self, size: int = 36, bold: bool = False) -> ImageFont.FreeTypeFont:
        """Get a font object at the specified size."""
        try:
            if bold:
                font_path = self._fonts.get("Roboto-Bold") or self._fonts.get("DejaVuSans")
            else:
                font_path = self._fonts.get("Roboto-Regular") or self._fonts.get("DejaVuSans") or \
                            self._fonts.get("Helvetica") or self._fonts.get("LiberationSans-Regular")

            if font_path:
                return ImageFont.truetype(font_path, size)
        except Exception as e:
            logger.warning(f"Failed to load font: {e}")

        return ImageFont.load_default()

    def _hex_to_rgb(self, hex_color: str) -> Tuple[int, int, int]:
        """Convert hex color string to RGB tuple."""
        hex_color = hex_color.lstrip("#")
        if len(hex_color) == 6:
            return tuple(int(hex_color[i : i + 2], 16) for i in (0, 2, 4))
        return (255, 255, 255)

    def _get_template(self, template_name: str = "default") -> Dict:
        """Get a template by name, falling back to default."""
        template = self.templates.get(template_name) or self.templates.get("default")
        if not template:
            template = {"width": 1080, "height": 1920, "background_color": "#FFFFFF", "elements": []}
        return template

    def _draw_rounded_rect(self, draw: ImageDraw, x: int, y: int, w: int, h: int, r: int, color: Tuple[int, int, int]):
        """Draw a rounded rectangle."""
        draw.rounded_rectangle([(x, y), (x + w, y + h)], radius=r, fill=color)

    def _composite_image(
        self,
        template: Dict,
        product_image: Image.Image,
        brand_logo: Optional[Image.Image],
        rating: float = 4.5,
        review: str = "Amazing product! Highly recommended for everyone.",
        price: str = "$29.99",
        index: int = 0,
    ) -> Image.Image:
        """Composite a single slide image from template and assets."""
        width = template.get("width", 1080)
        height = template.get("height", 1920)
        bg_color = self._hex_to_rgb(template.get("background_color", "#FFFFFF"))

        # Create canvas
        canvas = Image.new("RGB", (width, height), bg_color)
        draw = ImageDraw.Draw(canvas)

        elements = template.get("elements", [])

        for elem in elements:
            elem_type = elem.get("type", "")

            try:
                if elem_type == "product_image":
                    # Place product image
                    x, y = elem.get("x", 0), elem.get("y", 0)
                    ew, eh = elem.get("width", width), elem.get("height", height // 2)
                    fit = elem.get("fit", "cover")

                    resized = self._resize_image(product_image, ew, eh, fit)
                    canvas.paste(resized, (x, y))

                elif elem_type == "brand_logo" and brand_logo:
                    x, y = elem.get("x", 40), elem.get("y", 40)
                    lw, lh = elem.get("width", 120), elem.get("height", 120)
                    logo_resized = self._resize_image(brand_logo, lw, lh, "contain")
                    canvas.paste(logo_resized, (x, y), logo_resized if logo_resized.mode == "RGBA" else None)

                elif elem_type == "rating_stars":
                    x = elem.get("x", 40)
                    y = elem.get("y", 1140)
                    star_size = elem.get("star_size", 32)
                    max_stars = elem.get("max_stars", 5)

                    filled = int(rating)
                    partial = rating - filled
                    star_color = "#FFD700"
                    empty_color = "#E0E0E0"

                    for i in range(max_stars):
                        sx = x + i * (star_size + 8)
                        if i < filled:
                            self._draw_star(draw, sx, y, star_size, self._hex_to_rgb(star_color))
                        elif i == filled and partial > 0:
                            # Partial fill
                            self._draw_star(draw, sx, y, star_size, self._hex_to_rgb(star_color), partial)
                        else:
                            self._draw_star(draw, sx, y, star_size, self._hex_to_rgb(empty_color))

                elif elem_type == "rating_text":
                    x = elem.get("x", 240)
                    y = elem.get("y", 1145)
                    font_size = elem.get("font_size", 28)
                    color = self._hex_to_rgb(elem.get("color", "#333333"))
                    font = self._get_font(font_size)
                    draw.text((x, y), f"{rating:.1f} / 5.0", fill=color, font=font)

                elif elem_type == "review_text":
                    x = elem.get("x", 40)
                    y = elem.get("y", 1200)
                    max_w = elem.get("width", 1000)
                    font_size = elem.get("font_size", 36)
                    color = self._hex_to_rgb(elem.get("color", "#1A1A1A"))
                    max_lines = elem.get("max_lines", 4)
                    line_spacing = elem.get("line_spacing", 1.5)
                    font = self._get_font(font_size)

                    self._draw_wrapped_text(draw, x, y, max_w, review, font, color, max_lines, line_spacing)

                elif elem_type == "price_tag":
                    x = elem.get("x", 40)
                    y = elem.get("y", 1400)
                    font_size = elem.get("font_size", 42)
                    color = self._hex_to_rgb(elem.get("color", "#FF0000"))
                    font = self._get_font(font_size, bold=True)
                    draw.text((x, y), price, fill=color, font=font)

                elif elem_type == "cta_button":
                    x = elem.get("x", 340)
                    y = elem.get("y", 1480)
                    bw = elem.get("width", 400)
                    bh = elem.get("height", 80)
                    radius = elem.get("border_radius", 40)
                    bg = self._hex_to_rgb(elem.get("background_color", "#FF004F"))
                    text_color = self._hex_to_rgb(elem.get("text_color", "#FFFFFF"))
                    text = elem.get("text", "Shop Now")
                    font_size = elem.get("font_size", 32)

                    self._draw_rounded_rect(draw, x, y, bw, bh, radius, bg)
                    font = self._get_font(font_size, bold=True)
                    bbox = draw.textbbox((0, 0), text, font=font)
                    tw = bbox[2] - bbox[0]
                    th = bbox[3] - bbox[1]
                    tx = x + (bw - tw) // 2
                    ty = y + (bh - th) // 2
                    draw.text((tx, ty), text, fill=text_color, font=font)

                elif elem_type == "affiliate_disclaimer":
                    x = elem.get("x", 40)
                    y = elem.get("y", 1680)
                    font_size = elem.get("font_size", 18)
                    color = self._hex_to_rgb(elem.get("color", "#999999"))
                    text = elem.get("text", "Affiliate link")
                    font = self._get_font(font_size)
                    draw.text((x, y), text, fill=color, font=font)

            except Exception as e:
                logger.warning(f"Failed to render element '{elem_type}': {e}")

        return canvas

    def _resize_image(self, img: Image.Image, target_w: int, target_h: int, fit: str = "cover") -> Image.Image:
        """Resize image to fit dimensions while maintaining aspect ratio."""
        if fit == "cover":
            # Resize to fill the area (cropping if needed)
            img_ratio = img.width / img.height
            target_ratio = target_w / target_h

            if img_ratio > target_ratio:
                # Image is wider, match height
                new_h = target_h
                new_w = int(new_h * img_ratio)
            else:
                # Image is taller, match width
                new_w = target_w
                new_h = int(new_w / img_ratio)

            resized = img.resize((new_w, new_h), Image.LANCZOS)

            # Center crop
            left = (new_w - target_w) // 2
            top = (new_h - target_h) // 2
            return resized.crop((left, top, left + target_w, top + target_h))

        elif fit == "contain":
            # Resize to fit within the area (no cropping)
            img.thumbnail((target_w, target_h), Image.LANCZOS)
            # Create transparent background
            result = Image.new("RGBA", (target_w, target_h), (0, 0, 0, 0))
            paste_x = (target_w - img.width) // 2
            paste_y = (target_h - img.height) // 2
            result.paste(img, (paste_x, paste_y), img if img.mode == "RGBA" else None)
            return result

        else:
            return img.resize((target_w, target_h), Image.LANCZOS)

    def _draw_star(self, draw: ImageDraw, x: int, y: int, size: int, color: Tuple[int, int, int], fill_ratio: float = 1.0):
        """Draw a 5-pointed star."""
        import math

        points = []
        for i in range(10):
            angle = math.pi / 2 + i * 2 * math.pi / 10
            radius = size / 2 if i % 2 == 0 else size / 4
            px = x + size / 2 + radius * math.cos(angle)
            py = y + size / 2 + radius * math.sin(angle)
            points.append((px, py))

        if fill_ratio >= 1.0:
            draw.polygon(points, fill=color)
        elif fill_ratio > 0:
            # Partial fill approximation: draw a polygon clipped to fill_ratio
            # Simplified: just draw the left portion
            clip_x = x + size * fill_ratio
            clipped_points = [(px, py) for px, py in points if px <= clip_x]
            if clipped_points:
                draw.polygon(clipped_points, fill=color)

    def _draw_wrapped_text(
        self,
        draw: ImageDraw,
        x: int,
        y: int,
        max_width: int,
        text: str,
        font: ImageFont.FreeTypeFont,
        color: Tuple[int, int, int],
        max_lines: int = 4,
        line_spacing: float = 1.5,
    ):
        """Draw text with word wrapping."""
        words = text.split()
        lines = []
        current_line = ""

        for word in words:
            test_line = f"{current_line} {word}".strip()
            bbox = draw.textbbox((0, 0), test_line, font=font)
            line_width = bbox[2] - bbox[0]

            if line_width <= max_width:
                current_line = test_line
            else:
                lines.append(current_line)
                current_line = word

            if len(lines) >= max_lines:
                # Add ellipsis to last line
                last = lines[-1]
                if len(last) > 3:
                    lines[-1] = last[:-3] + "..."
                break

        if current_line and len(lines) < max_lines:
            lines.append(current_line)

        # Draw each line
        line_height = bbox[3] - bbox[1] if lines else 20
        for i, line in enumerate(lines):
            ly = y + i * int(line_height * line_spacing)
            draw.text((x, ly), line, fill=color, font=font)

    def _load_asset(self, path: Path, description: str) -> Optional[Image.Image]:
        """Load an image asset with error handling."""
        if not path.exists():
            logger.warning(f"{description} not found: {path}")
            return None
        try:
            return Image.open(path).convert("RGBA")
        except Exception as e:
            logger.error(f"Failed to load {description} {path}: {e}")
            return None

    def _generate_product_variations(self, product_img: Image.Image, count: int) -> List[Image.Image]:
        """Create variations of a product image for multiple slides."""
        variations = [product_img]

        # Create simple variations
        for i in range(1, count):
            if i % 2 == 0:
                # Zoom slightly
                w, h = product_img.size
                zoom = 1.0 + (i * 0.05)
                new_w = int(w / zoom)
                new_h = int(h / zoom)
                if new_w > 0 and new_h > 0:
                    cropped = product_img.crop((
                        (w - new_w) // 2,
                        (h - new_h) // 2,
                        (w + new_w) // 2,
                        (h + new_h) // 2,
                    ))
                    variations.append(cropped.resize((w, h), Image.LANCZOS))
            else:
                # Apply slight blur
                blurred = product_img.filter(ImageFilter.GaussianBlur(radius=1))
                variations.append(blurred)

        # Pad to count
        while len(variations) < count:
            variations.append(product_img)

        return variations[:count]

    async def generate_post(
        self,
        account_id: int,
        product_name: str = "Product",
        template_name: str = "default",
        rating: float = 4.5,
        review: str = "Great product! I love it.",
        price: str = "$29.99",
        product_image_path: Optional[str] = None,
        brand_logo_path: Optional[str] = None,
    ) -> Optional[str]:
        """Generate a full slideshow post.

        Returns path to the output directory containing generated images,
        or None on failure.
        """
        if not PILLOW_AVAILABLE:
            logger.error("Pillow not available. Cannot generate content.")
            return None

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        post_dir = self.output_dir / str(account_id) / timestamp
        post_dir.mkdir(parents=True, exist_ok=True)

        template = self._get_template(template_name)

        # Load assets
        if product_image_path:
            product_img = self._load_asset(Path(product_image_path), "product image")
        else:
            # Use first available product image
            product_files = list(self.products_dir.glob("*"))
            if product_files:
                product_img = self._load_asset(product_files[0], "product image")
            else:
                # Create a placeholder
                product_img = Image.new("RGB", (800, 800), (200, 200, 200))
                draw = ImageDraw.Draw(product_img)
                font = self._get_font(40)
                draw.text((300, 380), "Product", fill=(100, 100, 100), font=font)
                logger.warning("No product images found, using placeholder")

        if brand_logo_path:
            brand_logo = self._load_asset(Path(brand_logo_path), "brand logo")
        else:
            # Try to find brand logo
            brand_files = list(self.brands_dir.glob("*"))
            brand_logo = self._load_asset(brand_files[0], "brand logo") if brand_files else None

        # Generate variations
        product_variations = self._generate_product_variations(product_img, self.images_per_post)

        # Review snippets for each slide
        reviews = self._generate_review_snippets(review, self.images_per_post)

        # Compose each slide
        generated_count = 0
        for i in range(self.images_per_post):
            try:
                slide = self._composite_image(
                    template=template,
                    product_image=product_variations[i],
                    brand_logo=brand_logo,
                    rating=rating,
                    review=reviews[i],
                    price=price,
                    index=i,
                )

                slide_path = post_dir / f"slide_{i + 1:02d}.png"
                slide.save(str(slide_path), "PNG", optimize=True)
                generated_count += 1
                logger.debug(f"Generated slide {i + 1}/{self.images_per_post}: {slide_path}")

            except Exception as e:
                logger.error(f"Failed to generate slide {i + 1}: {e}")

        if generated_count >= 3:
            # Save metadata
            metadata = {
                "account_id": account_id,
                "template": template_name,
                "product": product_name,
                "rating": rating,
                "review": review,
                "price": price,
                "slides_count": generated_count,
                "created_at": timestamp,
            }
            meta_path = post_dir / "metadata.json"
            with open(meta_path, "w") as f:
                json.dump(metadata, f, indent=2)

            logger.info(f"Generated {generated_count} slides at {post_dir}")
            return str(post_dir)

        logger.error(f"Failed to generate minimum slides (got {generated_count})")
        return None

    def _generate_review_snippets(self, base_review: str, count: int) -> List[str]:
        """Generate review variations for multi-slide posts."""
        if count <= 1:
            return [base_review]

        snippets = [base_review]

        # Simple variations
        variations_pool = [
            f"{base_review} 🔥",
            f"I've been using this for weeks! {base_review}",
            f"Best purchase ever! {base_review}",
            f"5/5 stars! {base_review}",
            f"Can't recommend enough! {base_review}",
            f"Game changer! {base_review}",
            f"Absolutely love it! {base_review}",
            f"Worth every penny! {base_review}",
        ]

        random.shuffle(variations_pool)
        snippets.extend(variations_pool[: count - 1])

        return snippets[:count]

    @classmethod
    def from_settings(cls, settings: dict) -> "ContentPipeline":
        """Create instance from settings dict."""
        content_config = settings.get("content", {})
        return cls(
            templates_path="config/templates.yaml",
            output_dir=content_config.get("output_dir", "data/posts/"),
            products_dir="content/products/",
            brands_dir="content/brands/",
            images_per_post=content_config.get("images_per_post", 5),
        )
