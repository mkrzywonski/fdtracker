from reportlab.lib import colors
from reportlab.lib.pagesizes import letter, inch
from reportlab.lib.utils import simpleSplit


def align_text(
    doc,
    text,
    alignment="left",
    margin=0,
    y=0,
    page_width=8.5,
    font_name="Helvetica",
    font_size=12,
    color=colors.black,
):
    """Draws aligned text in PDF with specified formatting"""
    page_width_pts = page_width * inch

    doc.setFont(font_name, font_size)
    doc.setFillColor(color)

    text_width = doc.stringWidth(text, font_name, font_size)

    if alignment == "left":
        x = margin  # Use margin directly as points
    elif alignment == "right":
        x = page_width_pts - margin - text_width
    else:  # center
        x = (page_width_pts - text_width) / 2

    doc.drawString(x, y, text)
    return y - font_size - 2



def draw_page_border(doc, margin=0.5, padding=0.1):
    """Draws standard border with rounded corners"""
    page_width, page_height = letter
    doc.setStrokeColor(colors.black)
    doc.setLineWidth(0.02 * inch)
    doc.roundRect(
        margin * inch - (padding * inch),
        margin * inch - (padding * inch),
        page_width - (2 * margin * inch) + (2 * padding * inch),
        page_height - (2 * margin * inch) + (2 * padding * inch),
        0.25 * inch,
    )
    doc.line(
        margin * inch - (padding * inch),
        page_height - (2 * margin * inch) + (2 * padding * inch) - 0.2 * inch,
        page_width - (margin * inch) + (padding * inch),
        page_height - (2 * margin * inch) + (2 * padding * inch) - 0.2 * inch
    )



def start_new_page(doc, margin=0.5, title=''):
    """Starts new page with border and returns starting y position"""
    draw_page_border(doc, margin)
    _, page_height = letter
    y = page_height - (margin * inch + 20)
    y = align_text(doc, title, "center", y=y, font_name="Helvetica-Bold", font_size=18)
    y -= 20
    return y


def draw_wrapped_text(doc, text, x, y, font_size=12, margin=0.5, new_page_title=''):
    """Draws text with word wrapping, creating new pages as needed"""
    page_width, _ = letter
    doc.setFont("Helvetica", font_size)
    text_width = page_width - (x + margin * inch + 20)
    wrapped_lines = simpleSplit(text, "Helvetica", font_size, text_width)

    for line in wrapped_lines:
        if y < (margin * inch + 20):
            doc.showPage()
            y = start_new_page(doc, margin, new_page_title)
        doc.drawString(x, y, line)
        y -= 15
    return y


def draw_image(doc, img_path, y, width=300, margin=0.5, caption=None):
    """Draws image with optional caption, returns next y position"""
    from PIL import Image

    img = Image.open(img_path)
    aspect = img.width / img.height
    height = width / aspect

    doc.drawImage(img_path, margin * inch + 20, y - height, width=width, height=height)

    if caption:
        doc.setFont("Helvetica", 10)
        doc.drawString(margin * inch + 20, y - height - 15, caption)
        return y - (height + 30)
    return y - (height + 15)
