import fitz  # PyMuPDF
import requests

def calendar_image(year: int):
    local_pdf = f"kalender-{year}-querformat-in-farbe.pdf"
    pdf_url = f"https://www.kalenderpedia.de/download/{local_pdf}"
    output_image = f"tmp/calendar_{year}.png"

    download_pdf(pdf_url, local_pdf)
    convert_pdf_to_image(local_pdf, output_image)

    return output_image

def download_pdf(url, local_filename):
    response = requests.get(url)
    response.raise_for_status()  # Raise an error for bad status codes
    with open(local_filename, 'wb') as f:
        f.write(response.content)

def convert_pdf_to_image(pdf_path, output_image_path, dpi=300):
    doc = fitz.open(pdf_path)
    page:fitz.Page = doc.load_page(0)  # Page numbers start at 0
    pix = fitz.utils.get_pixmap(page, dpi=600)
    pix.save(output_image_path)

if __name__ == "__main__":
    calendar_image(2025)